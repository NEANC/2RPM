#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""仅测试 v2_http 单次传输；全部响应合成，不查询真实存储。

Boltp 的精确存储错误是批准的合成兼容策略，并非官方或实测结论。
"""

from copy import deepcopy
from importlib import import_module
from importlib.util import find_spec
from inspect import signature
from io import BytesIO
import json

import pytest
import requests

from modules.image_host.core import ImageHostError
from modules.image_host.diagnostics import diagnostic_scope


PROVIDERS = ('beeimg_cn', 'boltp')
BASES = {'beeimg_cn': 'https://www.beeimg.cn/api/v2',
         'boltp': 'https://www.boltp.com/api/v2'}
PATHS = {'group': '/group', 'profile': '/user/profile', 'upload': '/upload'}
SECRET = 'FAKE_HTTP_SECRET_7319'
SIGNED_URL = 'https://example.com/image?signature=FAKE_SIGNATURE_7319'
LIMIT = 1024 * 1024
STORAGE_MESSAGE = '不存在的储存驱动'


def http():
    """将缺少实现表现为明确断言失败，而不是收集错误。"""
    name = 'modules.image_host.v2_http'
    assert find_spec(name) is not None, '缺少 v2_http 单次传输实现'
    return import_module(name)


class Response:
    """模拟已解压响应块，并阻止所有无界读取入口。"""

    def __init__(self, owner):
        """初始化安全成功正文和资源记录。"""
        self.owner = owner
        self.status_code = 200
        self.headers = {'Content-Length': '1'}
        self.chunks = [b'{"status":"success","data":{}}']
        self.active = False
        self.closed = 0
        self.reads = 0

    def __enter__(self):
        """进入响应作用域，要求会话仍然持有资源。"""
        assert self.owner.active
        self.active = True
        return self

    def __exit__(self, *args):
        """记录先于会话发生的响应释放。"""
        self.active = False
        self.closed += 1
        self.owner.factory.events.append('response')

    def iter_content(self, chunk_size):
        """逐块读取，可在中途抛出指定异常或控制信号。"""
        assert self.active
        assert 0 < chunk_size <= LIMIT
        for chunk in self.chunks:
            self.reads += 1
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    def json(self):
        """禁止通过响应 JSON 方法绕过读取上限。"""
        pytest.fail('禁止无界 response.json')

    @property
    def text(self):
        """禁止通过响应文本属性绕过读取上限。"""
        pytest.fail('禁止无界 response.text')

    @property
    def content(self):
        """禁止通过完整响应属性绕过读取上限。"""
        pytest.fail('禁止无界 response.content')


class Session:
    """每次调用独立创建的无网络会话。"""

    def __init__(self, factory):
        """为本会话单独创建响应并保留计数。"""
        self.factory = factory
        self.active = False
        self.closed = 0
        self.response = Response(self)

    def __enter__(self):
        """标记会话所有权开始。"""
        self.active = True
        return self

    def __exit__(self, *args):
        """核对响应已释放，不吞掉控制信号。"""
        assert not self.response.active
        self.active = False
        self.closed += 1
        self.factory.events.append('session')

    def get(self, url, **kwargs):
        """记录单次 GET 的固定端点和参数。"""
        return self.send('GET', url, kwargs)

    def post(self, url, **kwargs):
        """记录单次 POST 的固定端点和参数。"""
        return self.send('POST', url, kwargs)

    def send(self, method, url, kwargs):
        """在唯一网络替换边界返回合成响应。"""
        assert self.active
        self.factory.calls.append((method, url, kwargs))
        if self.factory.request_error is not None:
            raise self.factory.request_error
        return self.response


class Factory:
    """提供互不复用的会话及可配置的合成结果。"""

    def __init__(self):
        """初始化请求、释放和异常记录。"""
        self.sessions = []
        self.calls = []
        self.events = []
        self.construct_error = None
        self.request_error = None
        self.status = 200
        self.chunks = [b'{"status":"success","data":{}}']

    def create(self):
        """构造独立会话，或者在取得资源前抛出异常。"""
        if self.construct_error is not None:
            raise self.construct_error
        session = Session(self)
        session.response.status_code = self.status
        session.response.chunks = self.chunks
        self.sessions.append(session)
        return session

    def payload(self, value):
        """编码本地合成 JSON，不替换被测解码或分类逻辑。"""
        self.chunks = [json.dumps(value, ensure_ascii=False).encode('utf-8')]


@pytest.fixture(autouse=True)
def client(monkeypatch, request):
    """替换会话构造且独立拒绝任何意外真实请求。"""
    def forbidden(*args, **kwargs):
        """意外访问真实网络时立即使测试失败。"""
        pytest.fail('测试禁止访问真实网络')

    factory = Factory()
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', forbidden)
    if 'real_http' not in request.fixturenames:
        monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
        monkeypatch.setattr(http(), '_V2Session', factory.create)
    return factory


class RecordingRaw(BytesIO):
    """用真实内存流记录读取和资源释放，不访问网络。"""

    def __init__(self, body, events, error=None):
        """保存合成正文、读取异常及共享事件记录。"""
        super().__init__(body)
        self.events = events
        self.error = error
        self.reads = 0
        self.bytes_read = 0
        self.releases = 0

    def read(self, size=-1):
        """记录实际读取量，或原样抛出合成异常。"""
        self.reads += 1
        if self.error is not None:
            raise self.error
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk

    def close(self):
        """执行真实内存流关闭并记录先后顺序。"""
        super().close()
        self.events.append('raw_close')

    def release_conn(self):
        """记录真实 Response.close 发起的连接释放调用。"""
        self.releases += 1
        self.events.append('raw_release')


@pytest.fixture
def real_http(monkeypatch, client):
    """保留真实请求准备流程，仅在适配器边界返回内存响应。"""
    state = {'calls': [], 'netrc': [], 'error': None, 'raw': None,
             'status': 200, 'location': None, 'events': [],
             'response': None, 'reads_at_enter': None}
    original_enter = requests.Response.__enter__
    original_response_close = requests.Response.close
    original_session_close = requests.sessions.Session.close

    def enter(response):
        """观察调用方取得响应时的读取次数，保留原上下文行为。"""
        if response.raw is not None:
            state['reads_at_enter'] = response.raw.reads
        return original_enter(response)

    def response_close(response):
        """执行真实响应关闭后记录，不用空替身伪造释放。"""
        original_response_close(response)
        state['events'].append('response_close')

    def session_close(session):
        """执行真实适配器关闭后记录会话释放。"""
        original_session_close(session)
        state['events'].append('session_close')

    monkeypatch.setattr(requests.Response, '__enter__', enter)
    monkeypatch.setattr(requests.Response, 'close', response_close)
    monkeypatch.setattr(requests.sessions.Session, 'close', session_close)
    monkeypatch.setattr(requests.sessions.os, 'environ', {
        'HTTPS_PROXY': 'http://synthetic-proxy.invalid:8080',
        'HTTP_PROXY': 'http://synthetic-proxy.invalid:8080',
        'NO_PROXY': '',
    })

    def synthetic_netrc(url, *args, **kwargs):
        """记录隐式认证查询，绝不读取真实 netrc 文件。"""
        state['netrc'].append(url)
        return ('SYNTHETIC_USER', 'SYNTHETIC_PASSWORD')

    def send(adapter, prepared, **kwargs):
        """截获最终请求并返回真实响应，不调用任何网络连接。"""
        state['calls'].append((prepared, kwargs, adapter.max_retries.total))
        if state['error'] is not None:
            raise state['error']
        response = requests.Response()
        response.status_code = state['status']
        response.url = prepared.url
        response.request = prepared
        if state['location'] is not None:
            response.headers['Location'] = state['location']
        if state['raw'] is None:
            response._content = b'{"status":"success","data":{}}'
            response._content_consumed = True
        else:
            response.raw = state['raw']
        state['response'] = response
        return response

    monkeypatch.setattr(requests.sessions, 'get_netrc_auth', synthetic_netrc)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    return state


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('stage', PATHS)
@pytest.mark.parametrize('token', ['', '  ' + SECRET + '  '])
@pytest.mark.parametrize('ca_variable', [None, 'REQUESTS_CA_BUNDLE',
                                       'CURL_CA_BUNDLE'])
@pytest.mark.parametrize('fails', [False, True])
def test_real_prepared_auth_and_environment(
        real_http, monkeypatch, caplog, provider, stage, token,
        ca_variable, fails):
    """最终请求只使用显式认证，且保留合成代理及环境证书语义。"""
    if ca_variable:
        monkeypatch.setenv(ca_variable, 'synthetic-ca.pem')
    data = {'storage_id': 7}
    files = {'file': ('synthetic.png', b'PNG', 'image/png')}
    before = deepcopy((data, files))
    if fails:
        real_http['error'] = requests.exceptions.ConnectTimeout(SECRET)
        with pytest.raises(ImageHostError) as caught:
            http().request_json(provider, stage, token, data=data, files=files)
        assert_safe(caught.value, 'transport_failed', stage)
    else:
        result = http().request_json(provider, stage, token,
                                     data=data, files=files)
        assert result == {'status': 'success', 'data': {}}
    assert len(real_http['calls']) == 1
    prepared, kwargs, retries = real_http['calls'][0]
    assert isinstance(prepared, requests.PreparedRequest)
    assert prepared.headers.get('Authorization') == (
        'Bearer ' + token if token else None)
    assert real_http['netrc'] == []
    assert prepared.headers['Accept'] == 'application/json'
    assert prepared.url == BASES[provider] + PATHS[stage]
    assert prepared.method == ('POST' if stage == 'upload' else 'GET')
    assert SECRET not in prepared.url
    assert SECRET not in str(prepared.body)
    assert kwargs['proxies']['https'] == 'http://synthetic-proxy.invalid:8080'
    assert kwargs['verify'] == ('synthetic-ca.pem' if ca_variable else True)
    assert kwargs['timeout'] == (5, 15)
    assert kwargs['stream'] is True
    assert retries == 0
    assert (data, files) == before
    assert not caplog.records


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('stage', PATHS)
@pytest.mark.parametrize('token', ['', SECRET])
@pytest.mark.parametrize('status', [301, 302, 303, 307, 308, 400, 401, 500])
@pytest.mark.parametrize('read_error', [False, True])
def test_real_non_success_never_prereads(
        real_http, caplog, provider, stage, token, status, read_error):
    """非成功响应在任何正文读取前分类，并实际先释放响应资源。"""
    error = requests.exceptions.ReadTimeout(SECRET) if read_error else None
    raw = RecordingRaw(b'x' * (LIMIT + 1), real_http['events'], error)
    real_http.update(status=status, location='/not-followed', raw=raw)
    with pytest.raises(ImageHostError) as caught:
        http().request_json(provider, stage, token)
    assert (caught.value.code, caught.value.http_status,
            raw.reads, raw.bytes_read) == ('http_failed', status, 0, 0)
    assert_safe(caught.value, 'http_failed', stage, status)
    assert real_http['reads_at_enter'] == 0
    assert len(real_http['calls']) == 1
    prepared, kwargs, retries = real_http['calls'][0]
    assert prepared.headers.get('Authorization') == (
        'Bearer ' + token if token else None)
    assert real_http['netrc'] == []
    assert prepared.url == BASES[provider] + PATHS[stage]
    assert prepared.method == ('POST' if stage == 'upload' else 'GET')
    assert SECRET not in prepared.url + str(prepared.body)
    assert kwargs['stream'] is True
    assert kwargs['verify'] is True
    assert kwargs['timeout'] == (5, 15)
    assert retries == 0
    assert real_http['response'].next is None
    assert raw.closed
    assert raw.releases == 1
    assert real_http['events'] == [
        'raw_close', 'raw_release', 'response_close', 'session_close']
    assert not caplog.records


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('stage', PATHS)
@pytest.mark.parametrize('phase', ['request', 'read'])
@pytest.mark.parametrize('error_type', [requests.exceptions.ReadTimeout,
                                       KeyboardInterrupt, SystemExit])
def test_real_transport_and_control_cleanup(
        real_http, caplog, provider, stage, phase, error_type):
    """真实会话保留安全传输分类及控制信号身份和确定性释放。"""
    error = error_type(SECRET)
    raw = RecordingRaw(b'', real_http['events'], error)
    if phase == 'request':
        real_http['error'] = error
    else:
        real_http['raw'] = raw
    expected = ImageHostError if isinstance(error, Exception) else error_type
    with pytest.raises(expected) as caught:
        http().request_json(provider, stage, SECRET)
    if expected is ImageHostError:
        assert_safe(caught.value, 'transport_failed', stage,
                    200 if phase == 'read' else None)
    else:
        assert caught.value is error
    assert len(real_http['calls']) == 1
    assert real_http['netrc'] == []
    if phase == 'read':
        assert raw.reads == 1
        assert raw.closed
        assert raw.releases == 1
        assert real_http['events'] == [
            'raw_close', 'raw_release', 'response_close', 'session_close']
    else:
        assert real_http['response'] is None
        assert real_http['events'] == ['session_close']
        raw.close()
    assert not caplog.records


def assert_safe(error, code, stage, status=None):
    """验证固定错误、安全元数据及无敏感异常链。"""
    assert error.code == code
    assert error.stage == stage
    assert error.http_status == status
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = str(error) + repr(error) + repr(error.args) + repr(vars(error))
    assert SECRET not in rendered
    assert SIGNED_URL not in rendered
    assert 'Authorization' not in rendered
    assert not hasattr(error, 'request')
    assert not hasattr(error, 'response')


def test_request_signature():
    """固定入口只允许两个关键字请求载荷参数。"""
    parameters = signature(http().request_json).parameters
    assert list(parameters) == ['provider', 'stage', 'token', 'data', 'files']
    for name in ('data', 'files'):
        assert parameters[name].kind is parameters[name].KEYWORD_ONLY
        assert parameters[name].default is None
    assert signature(http().request_json).return_annotation is dict


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('stage', PATHS)
@pytest.mark.parametrize('token', ['', SECRET, '  ' + SECRET + '  ',
                                   '${NOT_EXPANDED}'])
def test_fixed_single_request_contract(client, provider, stage, token):
    """精确核对端点、认证、TLS、超时和单次请求，不展开凭证。"""
    data = {'storage_id': 7, 'is_public': '0', 'album_id': 2}
    files = {'file': ('原文件.png', b'\x89PNG\r\n', 'image/png')}
    before = deepcopy((data, files))
    result = http().request_json(provider, stage, token, data=data, files=files)
    assert result == {'status': 'success', 'data': {}}
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    expected = {'headers': headers, 'timeout': (5, 15), 'verify': True,
                'allow_redirects': False, 'stream': True}
    if stage == 'upload':
        expected.update(data=data, files=files)
    assert client.calls == [('POST' if stage == 'upload' else 'GET',
                             BASES[provider] + PATHS[stage], expected)]
    assert (data, files) == before
    assert client.events == ['response', 'session']
    assert client.sessions[0].closed == client.sessions[0].response.closed == 1


def test_independent_sessions_and_credentials(client):
    """不同站点不得复用会话、认证头或请求状态。"""
    for provider, token in zip(PROVIDERS, [SECRET, 'FAKE_SECOND_ONLY']):
        http().request_json(provider, 'group', token)
    assert len(client.sessions) == 2
    assert client.sessions[0] is not client.sessions[1]
    assert client.calls[0][2]['headers']['Authorization'] == 'Bearer ' + SECRET
    assert (client.calls[1][2]['headers']['Authorization']
            == 'Bearer FAKE_SECOND_ONLY')
    assert client.events == ['response', 'session'] * 2


@pytest.mark.parametrize('provider, stage', [
    ('other', 'upload'), ('https://example.com', 'group'),
    ('BOLTP', 'upload'), (None, 'upload'), ([], 'upload'),
    ('boltp', 'other'), ('beeimg_cn', '/group'), ('boltp', []),
])
def test_invalid_route_fails_before_session(client, provider, stage):
    """未知站点或阶段在构造会话之前安全拒绝。"""
    with pytest.raises(ImageHostError) as caught:
        http().request_json(provider, stage, SECRET)
    assert SECRET not in repr(caught.value)
    assert caught.value.__context__ is None
    assert not client.sessions
    assert not client.calls


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('stage', PATHS)
@pytest.mark.parametrize('status', [401, 403, 423, 422, 429, 500, 302])
def test_http_failures_never_read_body(client, provider, stage, status):
    """非成功 HTTP 优先分类，不解析正文也不跟随重定向。"""
    client.status = status
    client.chunks = [AssertionError(SECRET + SIGNED_URL)]
    with pytest.raises(ImageHostError) as caught:
        http().request_json(provider, stage, SECRET)
    assert_safe(caught.value, 'http_failed', stage, status)
    assert client.sessions[0].response.reads == 0
    assert client.events == ['response', 'session']
    assert len(client.calls) == 1


@pytest.mark.parametrize('body', [
    b'', b'<html>FAKE_HTTP_SECRET_7319</html>', b'{"status":', b'\xff',
    b'[]', b'null', b'true', b'1', b'"success"', b'{}',
    b'{"status":true}', b'{"status":1}', b'{"status":[]}',
    b'{"status":"successful"}', b'{"status":"success "}',
    b'{"status":"Success"}', b'{"status":"unknown"}',
    b'{"status":"success","data":"\xed\xa0\x80"}',
    b'{"status":"success","data":' + b'9' * 5000 + b'}',
    b'{"status":"success","data":' + b'[' * 2000 + b'0',
], ids=lambda body: repr(body[:60]))
def test_invalid_json_encoding_shape_and_status(client, body, caplog):
    """解码、编码、根形状和未知状态均归固定响应错误。"""
    client.chunks = [body]
    with pytest.raises(ImageHostError) as caught:
        http().request_json('beeimg_cn', 'upload', SECRET)
    assert_safe(caught.value, 'invalid_response', 'upload', 200)
    assert client.events == ['response', 'session']
    assert not caplog.records


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('stage', PATHS)
@pytest.mark.parametrize('status', [200, 201, 299])
@pytest.mark.parametrize('message', [STORAGE_MESSAGE, '普通业务拒绝',
                                    STORAGE_MESSAGE + ' ',
                                    ' ' + STORAGE_MESSAGE, None, False, []])
def test_business_and_exact_upload_storage_classification(
        client, provider, stage, status, message):
    """仅上传阶段的 HTTP 200 精确存储错误可进入兼容分类。"""
    client.status = status
    client.payload({'status': 'error', 'message': message})
    with pytest.raises(ImageHostError) as caught:
        http().request_json(provider, stage, SECRET)
    code = ('storage_unavailable' if stage == 'upload' and status == 200
            and message == STORAGE_MESSAGE else 'business_rejected')
    assert_safe(caught.value, code, stage, status)
    assert caught.value.diagnostic is None
    assert len(client.calls) == 1
    assert client.events == ['response', 'session']


@pytest.mark.parametrize('provider, status, payload, expected', [
    *[(provider, 200, {'status': 'error', 'message': STORAGE_MESSAGE}, True)
      for provider in PROVIDERS],
    ('other', 200, {'status': 'error', 'message': STORAGE_MESSAGE}, False),
    ([], 200, {'status': 'error', 'message': STORAGE_MESSAGE}, False),
    *[('boltp', status, {'status': 'error', 'message': STORAGE_MESSAGE}, False)
      for status in (201, 400, '200', True)],
    *[('boltp', 200, {'status': status, 'message': STORAGE_MESSAGE}, False)
      for status in (True, False, None, [], 'Error', 'error ', 'success')],
    *[('boltp', 200, {'status': 'error', 'message': message}, False)
      for message in (None, False, [], '不存在的存储驱动',
                      ' ' + STORAGE_MESSAGE, STORAGE_MESSAGE + '\n')],
    *[('beeimg_cn', 200, value, False) for value in (None, [], 'error', {})],
])
def test_exact_storage_predicate(client, provider, status, payload, expected):
    """纯判定不联网，不修剪空白、不模糊匹配且不猜测其他站点。"""
    assert http().is_storage_rejection(provider, status, payload) is expected
    assert not client.sessions


@pytest.mark.parametrize('chunks', [
    [b'', b'{"sta', b'', b'tus":"success"}', b''],
    [b'{"status":"success"}' + b' ' * (LIMIT - 20)],
    [b'{"status":"success"}', b' ' * (LIMIT - 20)],
])
def test_bounded_read_accepts_empty_chunks_and_exact_limit(client, chunks):
    """合法多块 JSON 及精确一 MiB 边界均可读取。"""
    client.chunks = chunks
    assert http().request_json('boltp', 'group', '') == {'status': 'success'}
    assert client.sessions[0].response.reads == len(chunks)
    assert client.events == ['response', 'session']


@pytest.mark.parametrize('chunks', [
    [b' ' * (LIMIT + 1), KeyboardInterrupt('不应继续读取')],
    [b'{"status":"success"}', b' ' * (LIMIT - 20), b' ',
     KeyboardInterrupt('不应继续读取')],
])
def test_oversize_stops_immediately_despite_small_content_length(client, chunks):
    """累计解压字节超限立即拒绝，不相信较小声明长度。"""
    client.chunks = chunks
    with pytest.raises(ImageHostError) as caught:
        http().request_json('boltp', 'upload', SECRET)
    assert_safe(caught.value, 'invalid_response', 'upload', 200)
    assert client.sessions[0].response.reads == len(chunks) - 1
    assert client.events == ['response', 'session']


@pytest.mark.parametrize('phase', ['construct', 'request', 'read'])
@pytest.mark.parametrize('error_type', [
    requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectionError, requests.exceptions.ContentDecodingError,
    requests.exceptions.ChunkedEncodingError, RuntimeError, ValueError,
    UnicodeError,
])
def test_transport_errors_are_safe_and_close_owned_resources(
        client, caplog, phase, error_type):
    """底层普通异常不保留原文，按实际取得资源范围释放。"""
    error = error_type('Authorization: Bearer ' + SECRET + SIGNED_URL)
    if phase == 'construct':
        client.construct_error = error
    elif phase == 'request':
        client.request_error = error
    else:
        client.chunks = [b'{', error]
    with pytest.raises(ImageHostError) as caught:
        http().request_json('beeimg_cn', 'profile', SECRET)
    assert_safe(caught.value, 'transport_failed', 'profile',
                200 if phase == 'read' else None)
    assert client.events == ({'construct': [], 'request': ['session'],
                              'read': ['response', 'session']}[phase])
    assert len(client.calls) == (phase != 'construct')
    assert not caplog.records


def test_requests_json_error_is_not_misclassified_as_transport(client):
    """requests JSON 异常虽继承网络异常，仍须归响应错误。"""
    client.chunks = [requests.exceptions.JSONDecodeError(SECRET, SIGNED_URL, 0)]
    with pytest.raises(ImageHostError) as caught:
        http().request_json('boltp', 'upload', SECRET)
    assert_safe(caught.value, 'invalid_response', 'upload', 200)
    assert client.events == ['response', 'session']


@pytest.mark.parametrize('phase', ['construct', 'request', 'read'])
@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_control_signal_identity_and_cleanup(client, phase, signal_type):
    """控制信号原对象传播，只释放已经取得的资源。"""
    signal = signal_type(SECRET)
    if phase == 'construct':
        client.construct_error = signal
    elif phase == 'request':
        client.request_error = signal
    else:
        client.chunks = [b'{', signal]
    with pytest.raises(signal_type) as caught:
        http().request_json('boltp', 'upload', SECRET)
    assert caught.value is signal
    assert client.events == ({'construct': [], 'request': ['session'],
                              'read': ['response', 'session']}[phase])


@pytest.mark.parametrize('enabled', [False, True])
def test_diagnostics_are_explicit_safe_and_hidden(client, caplog, enabled):
    """诊断默认关闭，显式开启只保留脱敏中文且不进入异常展示。"""
    client.payload({'status': 'error', 'message': '请先绑定手机号'})
    with diagnostic_scope([SECRET] if enabled else None):
        with pytest.raises(ImageHostError) as caught:
            http().request_json('boltp', 'upload', SECRET)
    error = caught.value
    assert_safe(error, 'business_rejected', 'upload', 200)
    assert error.diagnostic == ('请先绑定手机号' if enabled else None)
    assert '请先绑定手机号' not in repr(error) + str(error) + repr(error.args)
    assert not caplog.records


@pytest.mark.parametrize('message', [
    SECRET + ' ' + SIGNED_URL, 'Authorization: Bearer ' + SECRET,
    '<html>' + SECRET + '</html>',
])
def test_diagnostics_never_retain_raw_secrets_or_urls(client, caplog, message):
    """显式诊断也不能保存原凭证、请求头或签名链接。"""
    client.payload({'status': 'error', 'message': message})
    with diagnostic_scope([SECRET]):
        with pytest.raises(ImageHostError) as caught:
            http().request_json('beeimg_cn', 'upload', SECRET)
    assert_safe(caught.value, 'business_rejected', 'upload', 200)
    assert not caplog.records


@pytest.mark.parametrize('storage', [None, True, False, 0, -1, '7', [], {}])
def test_optional_storage_validation_only_relaxes_storage(client, caplog, storage):
    """恢复用途仅放行存储字段，校验函数无网络、日志和输入修改。"""
    validate = getattr(import_module('modules.image_host.providers'),
                       'validate_v2_options', None)
    assert callable(validate)
    options = {'storage_id': storage, 'nested': {'items': [1]}}
    original = deepcopy(options)
    validate(options, require_storage=False)
    validate({}, require_storage=False)
    assert options == original
    with pytest.raises(ImageHostError) as caught:
        validate(options)
    assert caught.value.code == 'invalid_options'
    assert not client.sessions
    assert not caplog.records


@pytest.mark.parametrize('options', [
    {'permission': True}, {'permission': '1'}, {'permission': 2},
    {'is_public': False}, {'album_id': True}, {'album_id': '1'},
])
def test_optional_storage_does_not_relax_other_options(client, caplog, options):
    """不要求存储时仍拒绝非法权限、原生公开字段和相册类型。"""
    validate = getattr(import_module('modules.image_host.providers'),
                       'validate_v2_options', None)
    assert callable(validate)
    with pytest.raises(ImageHostError) as caught:
        validate(options, require_storage=False)
    assert caught.value.code == 'invalid_options'
    assert not client.sessions
    assert not caplog.records
