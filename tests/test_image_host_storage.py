#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""仅测试 v2_http 单次传输；全部响应合成，不查询真实存储。

Boltp 的精确存储错误是批准的合成兼容策略，并非官方或实测结论。
"""

from collections import UserDict
from copy import deepcopy
from dataclasses import fields
from dataclasses import FrozenInstanceError
from dataclasses import replace
from importlib import import_module
from importlib.util import find_spec
from inspect import signature
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from typing import Literal

import pytest
import requests

from modules.image_host.core import ImageHostError
from modules.image_host.diagnostics import diagnostic_scope


PROVIDERS = ('beeimg_cn', 'boltp')
BASES = {'beeimg_cn': 'https://www.beeimg.cn/api/v2',
         'boltp': 'https://www.boltp.com/api/v2'}
PATHS = {'group': '/group', 'upload': '/upload'}
SECRET = 'FAKE_HTTP_SECRET_7319'
SIGNED_URL = 'https://example.com/image?signature=FAKE_SIGNATURE_7319'
LIMIT = 1024 * 1024
STORAGE_MESSAGE = '不存在的储存驱动'
GROUP_PAYLOAD = {'status': 'success', 'data': {
    'group': {'options': {'file_expire_seconds': 0}},
    'storages': [{'id': 14}, {'id': 13}]}}
GROUP_BODY = json.dumps(GROUP_PAYLOAD, ensure_ascii=False).encode('utf-8')


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

    def prepare_request(self, request):
        """保留待发送请求字段供传输边界核对。"""
        return request

    def merge_environment_settings(self, url, proxies, stream, verify, cert):
        """返回调用方显式传输设置，不读取真实环境。"""
        return {'proxies': proxies, 'stream': stream, 'verify': verify,
                'cert': cert}

    def send(self, request, **kwargs):
        """在唯一网络替换边界返回合成响应。"""
        assert self.active
        recorded = {'headers': request.headers, 'timeout': kwargs['timeout'],
                    'verify': kwargs['verify'],
                    'allow_redirects': kwargs['allow_redirects'],
                    'stream': kwargs['stream']}
        if request.method == 'POST':
            recorded.update(data=request.data, files=request.files)
        self.factory.calls.append((request.method, request.url, recorded))
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
        queue_real_payload(real_http, stage)
        result = http().request_json(provider, stage, token,
                                     data=data, files=files)
        assert result == SUCCESS_PAYLOADS[stage]
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
    client.payload(SUCCESS_PAYLOADS[stage])
    result = http().request_json(provider, stage, token, data=data, files=files)
    assert result == SUCCESS_PAYLOADS[stage]
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
    client.payload(SUCCESS_PAYLOADS['group'])
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
    ('boltp', 'profile'), ('beeimg_cn', 'profile'),
])
def test_invalid_route_fails_before_session(client, provider, stage):
    """未知站点、资料阶段等无效路由在构造会话之前安全拒绝。"""
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
    [b'', GROUP_BODY[:8], b'', GROUP_BODY[8:], b''],
    [GROUP_BODY + b' ' * (LIMIT - len(GROUP_BODY))],
    [GROUP_BODY, b' ' * (LIMIT - len(GROUP_BODY))],
])
def test_bounded_read_accepts_empty_chunks_and_exact_limit(client, chunks):
    """合法多块 JSON 及精确一 MiB 边界均可读取。"""
    client.chunks = chunks
    assert http().request_json('boltp', 'group', '') == GROUP_PAYLOAD
    assert client.sessions[0].response.reads == len(chunks)
    assert client.events == ['response', 'session']


@pytest.mark.parametrize('chunks', [
    [b' ' * (LIMIT + 1), KeyboardInterrupt('不应继续读取')],
    [b'{}', b' ' * (LIMIT - 2), b' ',
     KeyboardInterrupt('不应继续读取')],
])
def test_oversize_stops_immediately_despite_small_content_length(client, chunks):
    """累计解压字节超限立即拒绝，不相信较小声明长度。"""
    client.chunks = chunks
    with pytest.raises(ImageHostError) as caught:
        http().request_json('boltp', 'group', SECRET)
    assert_safe(caught.value, 'invalid_response', 'group', 200)
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
        http().request_json('beeimg_cn', 'group', SECRET)
    assert_safe(caught.value, 'transport_failed', 'group',
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


def storage_module():
    """将尚未实现的存储入口表现为明确的功能缺失断言。"""
    name = 'modules.image_host.storage'
    assert find_spec(name) is not None, '缺少存储元数据查询与纯选择实现'
    return import_module(name)


def site_module(name):
    """将尚未实现的站点模块表现为明确的功能缺失断言。"""
    full = 'modules.image_host.' + name
    assert find_spec(full) is not None, f'缺少站点模块 {name}'
    return import_module(full)


def storage_payload(stage):
    """构造包含无关账号字段的合成成功响应，不使用真实凭证。"""
    if stage == 'group':
        data = {
            'group': {'options': {'file_expire_seconds': 0},
                      'is_guest': True, 'name': SECRET},
            'storages': [{'id': 14, 'intro': SECRET}, {'id': 13}, {'id': 14}],
        }
    else:
        data = {'options': {'default_storage_id': 13},
                'name': SECRET, 'payments': [SECRET], 'url': SIGNED_URL}
    return {'status': 'success', 'data': data}


@pytest.fixture
def storage_http(real_http, monkeypatch):
    """只替换适配器返回队列，保留真实传输、认证隔离和释放观察。"""
    real_http.update(replies=[], raws=[], responses=[])

    def send(adapter, prepared, **kwargs):
        """按调用顺序创建独立内存响应，额外请求立即失败。"""
        index = len(real_http['calls'])
        real_http['calls'].append((prepared, kwargs, adapter.max_retries.total))
        assert index < len(real_http['replies']), '不允许额外重试或匿名降级'
        reply = real_http['replies'][index]
        if 'request_error' in reply:
            raise reply['request_error']
        response = requests.Response()
        response.status_code = reply.get('status', 200)
        response.url = prepared.url
        response.request = prepared
        body = reply.get('body')
        if body is None:
            body = json.dumps(reply['payload'], ensure_ascii=False).encode('utf-8')
        raw = RecordingRaw(body, real_http['events'], reply.get('read_error'))
        response.raw = raw
        real_http['raws'].append(raw)
        real_http['responses'].append(response)
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    return real_http


def queue_storage_success(state, token=SECRET):
    """安排组信息及账号资料响应，返回可局部修改的合成载荷。

    资料响应仅保留在队列中，用于断言新 group 路径绝不会消费它。
    """
    group = storage_payload('group')
    profile = storage_payload('profile')
    state['replies'].append({'payload': group})
    if token:
        state['replies'].append({'payload': profile})
    return group, profile


def group_calls(state):
    """按实际请求顺序返回 (方法, 阶段) 二元组。"""
    return [(prepared.method, prepared.url.rsplit('/', 1)[-1])
            for prepared, _, _ in state['calls']]


SUCCESS_PAYLOADS = {
    'group': GROUP_PAYLOAD,
    'upload': {'status': 'success',
               'data': {'public_url': 'https://example.com/synthetic.png'}},
}


def queue_real_payload(state, stage):
    """为真实 Response 准备对应阶段的合成成功正文。"""
    body = json.dumps(SUCCESS_PAYLOADS[stage], ensure_ascii=False).encode('utf-8')
    state['raw'] = RecordingRaw(body, state['events'])


def assert_storage_requests(state, provider, token, stages):
    """验证实际请求及释放次数，确认没有隐式凭证读取或重试。"""
    assert len(state['calls']) == len(stages)
    for (prepared, kwargs, retries), stage in zip(state['calls'], stages):
        assert prepared.method == 'GET'
        assert prepared.url == BASES[provider] + PATHS[stage]
        assert prepared.body is None
        assert prepared.headers.get('Authorization') == (
            'Bearer ' + token if token else None)
        assert prepared.headers['Accept'] == 'application/json'
        assert kwargs['stream'] is True
        assert kwargs['verify'] is True
        assert kwargs['timeout'] == (5, 15)
        assert retries == 0
    assert state['netrc'] == []
    assert state['events'].count('session_close') == len(stages)
    assert state['events'].count('response_close') == len(state['responses'])
    assert all(raw.releases == 1 for raw in state['raws'])
    lifecycle = [event for event in state['events']
                 if event in ('response_close', 'session_close')]
    assert lifecycle == ['response_close', 'session_close'] * len(stages)


def test_storage_public_contract(client):
    """锁定冻结数据结构、字段顺序、固定签名及实际返回类型。"""
    module = storage_module()
    metadata = module.StorageMetadata((13, 14), 14, 0, 1000)
    selection = module.StorageSelection(13, False)
    assert [field.name for field in fields(metadata)] == [
        'storage_ids', 'default_storage_id', 'file_expire_seconds', 'fetched_at',
        'profile_state']
    assert [field.name for field in fields(selection)] == [
        'storage_id', 'replaced_manual']
    assert [field.type for field in fields(metadata)] == [
        tuple[int, ...], int | None, int | None, float,
        Literal['available', 'not_requested', 'forbidden']]
    assert [field.type for field in fields(selection)] == [int, bool]
    assert metadata.profile_state == 'not_requested'
    for instance in (metadata, selection):
        for field in fields(instance):
            with pytest.raises(FrozenInstanceError):
                setattr(instance, field.name, None)
    fetch = signature(module.fetch_storage_metadata)
    assert list(fetch.parameters) == ['provider', 'token', 'now']
    assert fetch.parameters['now'].kind is fetch.parameters['now'].KEYWORD_ONLY
    assert fetch.parameters['now'].default is fetch.parameters['now'].empty
    assert fetch.return_annotation is module.StorageMetadata
    choose = signature(module.select_storage)
    assert list(choose.parameters) == [
        'metadata', 'manual_present', 'manual_value', 'provider', 'excluded_id']
    for name in ('provider', 'excluded_id'):
        assert choose.parameters[name].kind is choose.parameters[name].KEYWORD_ONLY
    assert choose.parameters['provider'].default == 'boltp'
    assert choose.parameters['excluded_id'].default is None
    assert choose.return_annotation is module.StorageSelection
    assert module.select_storage(metadata, True, 13) == module.StorageSelection(
        13, True)
    assert not client.sessions


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('ids, default, state', [
    ((13, 14), 14, 'available'), ((13, 14), None, 'not_requested'),
    ((13, 14), 99, 'forbidden'), ((14, 13), None, 'forbidden'),
])
@pytest.mark.parametrize('present', [False, True])
@pytest.mark.parametrize('manual', [13, 14, 99, None, True, False, 0, -1,
                                    '13', '', 13.0, [], [13], {'bad': 'value'}])
def test_storage_v2_group_selection_order(
        client, caplog, provider, ids, default, state, present, manual):
    """两站按组顺序取首个候选，忽略手填优先级、默认编号与资料状态。"""
    module = storage_module()
    metadata = module.StorageMetadata(ids, default, 0, 1000, state)
    original = deepcopy((metadata, manual))
    assert module.select_storage(metadata, present, manual,
                                 provider=provider) == module.StorageSelection(
        ids[0], present)
    assert module.select_storage(metadata, present, manual, provider=provider,
                                 excluded_id=ids[0]).storage_id == ids[1]
    assert (metadata, manual) == original
    assert not client.sessions
    assert not client.calls
    assert not caplog.records


@pytest.mark.parametrize('provider', PROVIDERS)
def test_storage_v2_group_selection_empty(client, provider):
    """空存储无论是否手填都报固定错误，绝不猜测存储编号。"""
    module = storage_module()
    with pytest.raises(ImageHostError) as caught:
        module.select_storage(module.StorageMetadata((), 13, None, 0), True, 13,
                              provider=provider)
    assert_safe(caught.value, 'storage_unavailable', 'storage')
    assert not client.calls


@pytest.mark.parametrize('provider', ['other', 'BOLTP', None, []])
@pytest.mark.parametrize('present, manual', [(False, None), (True, 13)])
def test_storage_v2_selection_unknown_provider(client, provider, present, manual):
    """未知站点在选择阶段明确拒绝，不静默当作 Boltp 处理。"""
    module = storage_module()
    metadata = module.StorageMetadata((13, 14), 14, 0, 1000)
    with pytest.raises(ImageHostError) as caught:
        module.select_storage(metadata, present, manual, provider=provider)
    assert_safe(caught.value, 'invalid_provider', 'group')
    assert not client.sessions
    assert not client.calls


@pytest.mark.parametrize('token', ['', SECRET, '  ' + SECRET + '  ',
                                   '${NOT_EXPANDED}'])
def test_storage_fetch_real_transport_beeimg_cn(storage_http, caplog, token):
    """BeeIMG.CN 真实查询只请求 group，不取默认编号与资料状态。"""
    module = storage_module()
    payloads = queue_storage_success(storage_http, token)
    original = deepcopy(payloads)
    result = module.fetch_storage_metadata('beeimg_cn', token, now=1000)
    assert type(result) is module.StorageMetadata
    assert result == module.StorageMetadata((14, 13), None, 0, 1000,
                                           'not_requested')
    assert type(result.fetched_at) is float
    assert payloads == original
    assert set(vars(result)) == {
        'storage_ids', 'default_storage_id', 'file_expire_seconds', 'fetched_at',
        'profile_state'}
    assert SECRET not in repr(result) + repr(vars(result))
    assert SIGNED_URL not in repr(result) + repr(vars(result))
    assert_storage_requests(storage_http, 'beeimg_cn', token, ['group'])
    assert all(response.next is None for response in storage_http['responses'])
    assert not caplog.records


@pytest.mark.parametrize('token', ['', SECRET, '  ' + SECRET + '  ',
                                   '${NOT_EXPANDED}'])
def test_storage_fetch_real_transport_boltp(storage_http, caplog, token):
    """Boltp 真实查询只请求 group，不请求资料也不取默认编号。"""
    module = storage_module()
    payloads = queue_storage_success(storage_http, token)
    original = deepcopy(payloads)
    result = module.fetch_storage_metadata('boltp', token, now=1000)
    assert type(result) is module.StorageMetadata
    assert result == module.StorageMetadata((14, 13), None, 0, 1000,
                                           'not_requested')
    assert type(result.fetched_at) is float
    assert payloads == original
    assert set(vars(result)) == {
        'storage_ids', 'default_storage_id', 'file_expire_seconds', 'fetched_at',
        'profile_state'}
    assert SECRET not in repr(result) + repr(vars(result))
    assert SIGNED_URL not in repr(result) + repr(vars(result))
    assert_storage_requests(storage_http, 'boltp', token, ['group'])
    assert all(response.next is None for response in storage_http['responses'])
    assert not caplog.records


@pytest.mark.parametrize('provider', PROVIDERS)
def test_v2_group_metadata_contract(storage_http, provider):
    """两站 group 查询只发一次 GET，并返回统一的无默认元数据。"""
    module = storage_module()
    queue_storage_success(storage_http)
    metadata = module.fetch_storage_metadata(provider, SECRET, now=1000)
    calls = group_calls(storage_http)
    assert metadata.storage_ids == (14, 13)
    assert (metadata.default_storage_id, metadata.profile_state) == (
        None, 'not_requested')
    assert (metadata.file_expire_seconds, metadata.fetched_at) == (0, 1000.0)
    assert calls == [('GET', 'group')]
    assert module.select_storage(
        metadata, True, 13, provider=provider).storage_id == 14
    assert module.select_storage(
        metadata, True, 13, provider=provider, excluded_id=14).storage_id == 13
    assert metadata.storage_ids == (14, 13)
    assert_storage_requests(storage_http, provider, SECRET, ['group'])


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('path, value, missing', [
    (('data',), None, True), (('data',), [], False),
    (('data', 'group'), None, True), (('data', 'group'), 'bad', False),
    (('data', 'group', 'options'), None, True),
    (('data', 'group', 'options'), [], False),
    (('data', 'storages'), None, True), (('data', 'storages'), {}, False),
])
def test_v2_group_structure_errors(storage_http, provider, path, value, missing):
    """缺失或错误的必要结构使整次查询失败，不跳过坏项。"""
    group, _ = queue_storage_success(storage_http)
    parent = group
    for key in path[:-1]:
        parent = parent[key]
    if missing:
        del parent[path[-1]]
    else:
        parent[path[-1]] = value
    with pytest.raises(ImageHostError) as caught:
        storage_module().fetch_storage_metadata(provider, SECRET, now=1000)
    assert_safe(caught.value, 'storage_lookup_failed', 'group')
    assert group_calls(storage_http) == [('GET', 'group')]


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('entry', [
    None, True, 13, '13', [], {}, {'id': None}, {'id': True}, {'id': False},
    {'id': 0}, {'id': -1}, {'id': '13'}, {'id': 1.5}, {'id': []}, {'id': {}},
    {'name': 'missing-id'},
])
def test_v2_group_entry_errors(storage_http, provider, entry):
    """有效条目夹杂任何非法条目都使整份查询失败，不跳过坏项。"""
    group, _ = queue_storage_success(storage_http)
    group['data']['storages'] = [{'id': 14}, entry, {'id': 13}]
    with pytest.raises(ImageHostError) as caught:
        storage_module().fetch_storage_metadata(provider, SECRET, now=1000)
    assert_safe(caught.value, 'storage_lookup_failed', 'group')
    assert group_calls(storage_http) == [('GET', 'group')]


@pytest.mark.parametrize('provider', PROVIDERS)
def test_v2_group_empty_storages(storage_http, provider):
    """合法但空的 storages 列表归为无可用存储。"""
    group, _ = queue_storage_success(storage_http)
    group['data']['storages'] = []
    with pytest.raises(ImageHostError) as caught:
        storage_module().fetch_storage_metadata(provider, SECRET, now=1000)
    assert_safe(caught.value, 'storage_unavailable', 'storage')
    assert group_calls(storage_http) == [('GET', 'group')]


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('expire', [None, 0, 600])
def test_v2_group_expiration_valid(storage_http, provider, expire):
    """期限缺失、None 与非负整数均合法且不裁剪。"""
    group, _ = queue_storage_success(storage_http)
    options = group['data']['group']['options']
    if expire is None:
        options.pop('file_expire_seconds', None)
    else:
        options['file_expire_seconds'] = expire
    metadata = storage_module().fetch_storage_metadata(provider, SECRET, now=1000)
    assert metadata.file_expire_seconds == expire
    assert group_calls(storage_http) == [('GET', 'group')]


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('expire', [-1, True, '600', 1.5, [], {}])
def test_v2_group_expiration_errors(storage_http, provider, expire):
    """负数、布尔、字符串、浮点与容器期限均使整次查询失败。"""
    group, _ = queue_storage_success(storage_http)
    group['data']['group']['options']['file_expire_seconds'] = expire
    with pytest.raises(ImageHostError) as caught:
        storage_module().fetch_storage_metadata(provider, SECRET, now=1000)
    assert_safe(caught.value, 'storage_lookup_failed', 'group')
    assert group_calls(storage_http) == [('GET', 'group')]


@pytest.mark.parametrize('provider', PROVIDERS)
def test_v2_group_extra_fields_ignored(storage_http, provider):
    """额外字段被忽略，不影响保序去重结果。"""
    group, _ = queue_storage_success(storage_http)
    group['data']['group']['is_guest'] = True
    group['data']['group']['name'] = SECRET
    group['data']['storages'][0]['intro'] = SECRET
    group['data']['options'] = {'extra': [1]}
    metadata = storage_module().fetch_storage_metadata(provider, SECRET, now=1000)
    assert metadata.storage_ids == (14, 13)
    assert SECRET not in repr(metadata)
    assert group_calls(storage_http) == [('GET', 'group')]


@pytest.mark.parametrize('provider', PROVIDERS)
def test_v2_group_http_503(storage_http, provider):
    """组查询 503 归为 http_failed/group/503，且不读取正文。"""
    storage_http['replies'].append({'status': 503, 'body': b''})
    with pytest.raises(ImageHostError) as caught:
        storage_module().fetch_storage_metadata(provider, SECRET, now=1000)
    assert (caught.value.code, caught.value.stage,
            caught.value.http_status) == ('http_failed', 'group', 503)
    assert group_calls(storage_http) == [('GET', 'group')]
    assert storage_http['raws'][0].reads == 0
    assert storage_http['raws'][0].closed


@pytest.mark.parametrize('provider', ['other', 'BOLTP', None, []])
def test_v2_unknown_provider(storage_http, provider):
    """不支持的站点在请求前固定拒绝且零请求。"""
    with pytest.raises(ImageHostError) as caught:
        storage_module().fetch_storage_metadata(provider, SECRET, now=1000)
    assert_safe(caught.value, 'invalid_provider', 'group')
    assert not storage_http['calls']
    assert not storage_http['netrc']


@pytest.mark.parametrize('now', [
    None, True, False, -1, -0.1, '1000', [], {},
    float('nan'), float('inf'), float('-inf'),
    pytest.param(10 ** 10000, id='overflowing-integer'),
])
def test_storage_invalid_now_precedes_network(storage_http, now):
    """非法时刻及巨大整数转换失败在请求前安全拒绝。"""
    with pytest.raises(ImageHostError) as caught:
        storage_module().fetch_storage_metadata('boltp', SECRET, now=now)
    assert_safe(caught.value, 'config_error', 'storage')
    assert not storage_http['calls']
    assert not storage_http['netrc']


@pytest.mark.parametrize('now', [0, -0.0, 0.5, 1000, 10 ** 100, 1e308])
def test_storage_valid_now_has_no_business_upper_limit(storage_http, now):
    """直接记录调用方提供的有限非负时刻，不读取当前时钟。"""
    queue_storage_success(storage_http, '')
    result = storage_module().fetch_storage_metadata('boltp', '', now=now)
    assert result.fetched_at == float(now)
    assert type(result.fetched_at) is float


@pytest.mark.parametrize('path', [
    ('data',), ('data', 'group'), ('data', 'group', 'options'),
    ('data', 'storages'),
])
@pytest.mark.parametrize('value', [None, False, 7, 'bad', [], {}])
@pytest.mark.parametrize('missing', [False, True])
def test_storage_required_shapes(storage_http, path, value, missing):
    """必要映射或列表缺失与错误类型不得被当成未设置。"""
    module = storage_module()
    group, _ = queue_storage_success(storage_http)
    parent = group
    for key in path[:-1]:
        parent = parent[key]
    if missing:
        del parent[path[-1]]
    else:
        parent[path[-1]] = value
    is_list = path[-1] == 'storages'
    valid_empty_options = not missing and value == {} and path[-1] == 'options'
    if valid_empty_options:
        result = module.fetch_storage_metadata('boltp', SECRET, now=1000)
        assert result.file_expire_seconds is None
    else:
        with pytest.raises(ImageHostError) as caught:
            module.fetch_storage_metadata('boltp', SECRET, now=1000)
        empty_list = not missing and is_list and isinstance(value, list)
        assert_safe(caught.value,
                    'storage_unavailable' if empty_list else 'storage_lookup_failed',
                    'storage' if empty_list else 'group')
    assert_storage_requests(storage_http, 'boltp', SECRET, ['group'])


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('entry', [
    None, True, 13, '13', [], {}, {'id': None}, {'id': True}, {'id': False},
    {'id': 0}, {'id': -1}, {'id': '13'}, {'id': 13.0}, {'id': []}, {'id': {}},
])
def test_storage_rejects_any_bad_entry(storage_http, provider, entry):
    """有效条目夹杂任何非法条目都使整份查询失败，不跳过坏项。"""
    group, _ = queue_storage_success(storage_http)
    group['data']['storages'] = [{'id': 13}, entry, {'id': 14}]
    with pytest.raises(ImageHostError) as caught:
        storage_module().fetch_storage_metadata(provider, SECRET, now=1000)
    assert_safe(caught.value, 'storage_lookup_failed', 'group')
    assert_storage_requests(storage_http, provider, SECRET, ['group'])


@pytest.mark.parametrize('value', [None, 0, 1, 99, 10 ** 100, True, False, -1,
                                   '13', 1.0, [], {}])
def test_storage_optional_expiration(storage_http, value):
    """可选期限严格区分 null、零、正整数及非法类型。"""
    group, _ = queue_storage_success(storage_http)
    group['data']['group']['options']['file_expire_seconds'] = value
    valid = value is None or (type(value) is int and value >= 0)
    if valid:
        result = storage_module().fetch_storage_metadata('boltp', SECRET, now=1000)
        assert result.file_expire_seconds == value
        assert not hasattr(result, 'expired_at')
    else:
        with pytest.raises(ImageHostError) as caught:
            storage_module().fetch_storage_metadata('boltp', SECRET, now=1000)
        assert_safe(caught.value, 'storage_lookup_failed', 'group')
    assert_storage_requests(storage_http, 'boltp', SECRET, ['group'])


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('failure, code, status', [
    *[({'status': status, 'body': b'not read'}, 'http_failed', status)
      for status in (401, 403, 423, 429, 500)],
    ({'payload': {'status': 'error', 'message': STORAGE_MESSAGE}},
     'business_rejected', 200),
    ({'body': b'{bad-json'}, 'invalid_response', 200),
    ({'read_error': requests.exceptions.ReadTimeout(SECRET), 'body': b''},
     'transport_failed', 200),
])
def test_storage_preserves_transport_failures(
        storage_http, caplog, provider, failure, code, status):
    """两站 group 均保留传输层分类、状态和实际阶段。"""
    queue_storage_success(storage_http)
    storage_http['replies'][0] = failure
    with pytest.raises(ImageHostError) as caught:
        storage_module().fetch_storage_metadata(provider, SECRET, now=1000)
    assert_safe(caught.value, code, 'group', status)
    assert_storage_requests(storage_http, provider, SECRET, ['group'])
    if code == 'http_failed':
        assert storage_http['raws'][-1].reads == 0
        assert storage_http['raws'][-1].closed
    assert not caplog.records


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_storage_control_signal_real_path(storage_http, signal_type):
    """真实传输读取中的控制信号原对象传播并释放全部已取得资源。"""
    signal = signal_type(SECRET)
    queue_storage_success(storage_http)
    storage_http['replies'][0] = {'body': b'', 'read_error': signal}
    with pytest.raises(signal_type) as caught:
        storage_module().fetch_storage_metadata('boltp', SECRET, now=1000)
    assert caught.value is signal
    assert storage_http['raws'][-1].closed
    assert_storage_requests(storage_http, 'boltp', SECRET, ['group'])


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('message, expected', [
    ('请先绑定手机号', '请先绑定手机号'),
    (SECRET + ' ' + SIGNED_URL, '[已隐藏] [已隐藏]'),
    ('Authorization: Bearer ' + SECRET, None),
])
def test_storage_diagnostic_passthrough(
        storage_http, caplog, capsys, enabled, message, expected):
    """诊断仍由既有通道控制，错误展示及日志不包含账号或秘密。"""
    queue_storage_success(storage_http)
    payload = storage_payload('group')
    payload.update(status='error', message=message)
    storage_http['replies'][0] = {'payload': payload}
    with diagnostic_scope([SECRET] if enabled else None):
        with pytest.raises(ImageHostError) as caught:
            storage_module().fetch_storage_metadata('boltp', SECRET, now=1000)
    assert_safe(caught.value, 'business_rejected', 'group', 200)
    assert caught.value.diagnostic == (expected if enabled else None)
    assert message not in str(caught.value) + repr(caught.value.args)
    assert not caplog.records
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('error_type', [RuntimeError, ValueError,
                                       KeyboardInterrupt, SystemExit])
def test_storage_unknown_boundary_errors(monkeypatch, client, caplog,
                                         error_type):
    """普通未知异常在处理器外安全映射，控制信号保留对象身份。"""
    module = storage_module()
    error = error_type(SECRET + SIGNED_URL)
    calls = []

    def request(provider, current_stage, token):
        """只接受 group 查询并注入指定异常。"""
        calls.append(current_stage)
        raise error

    monkeypatch.setattr(http(), 'request_json', request)
    expected = ImageHostError if isinstance(error, Exception) else error_type
    with pytest.raises(expected) as caught:
        module.fetch_storage_metadata('boltp', SECRET, now=1000)
    if expected is ImageHostError:
        assert_safe(caught.value, 'storage_lookup_failed', 'group')
    else:
        assert caught.value is error
    assert calls == ['group']
    assert not client.calls
    assert not caplog.records


def test_storage_safe_error_identity(monkeypatch):
    """既有安全错误的对象和诊断原样透传，不重新分类或包装。"""
    module = storage_module()
    error = ImageHostError('http_failed', '', stage='group', http_status=401,
                           diagnostic='请先绑定手机号')

    def request(provider, stage, token):
        """在 group 阶段抛出已经过安全处理的错误对象。"""
        raise error

    monkeypatch.setattr(http(), 'request_json', request)
    with pytest.raises(ImageHostError) as caught:
        module.fetch_storage_metadata('boltp', SECRET, now=1000)
    assert caught.value is error
    assert_safe(error, 'http_failed', 'group', 401)
    assert error.diagnostic == '请先绑定手机号'


def test_storage_accepts_mapping_without_retaining_payload(monkeypatch):
    """结构允许一般映射，返回对象不保留或修改完整响应引用。"""
    module = storage_module()
    group = UserDict({'data': UserDict({
        'group': UserDict({'options': UserDict()}),
        'storages': [UserDict({'id': 14}), UserDict({'id': 13}),
                     UserDict({'id': 14})],
    })})
    original = deepcopy(group)

    def request(provider, stage, token):
        """返回一般映射以验证解析层不局限于普通字典。"""
        assert stage == 'group'
        return group

    monkeypatch.setattr(http(), 'request_json', request)
    result = module.fetch_storage_metadata('boltp', SECRET, now=1000)
    assert result == module.StorageMetadata(
        (14, 13), None, None, 1000, 'not_requested')
    assert group == original
    group['data']['storages'].clear()
    assert result.storage_ids == (14, 13)


def test_beeimg_cn_never_requests_profile(storage_http, caplog):
    """BeeIMG.CN 主链只请求 group：即使队列备有 403 资料应答也不消费。"""
    queue_storage_success(storage_http)
    storage_http['replies'][1] = {'status': 403, 'body': b'never-read-body'}
    result = storage_module().fetch_storage_metadata(
        'beeimg_cn', SECRET, now=1000)
    assert result.storage_ids == (14, 13)
    assert result.default_storage_id is None
    assert result.profile_state == 'not_requested'
    assert_storage_requests(storage_http, 'beeimg_cn', SECRET, ['group'])
    assert not caplog.records


@pytest.mark.parametrize('reply, code', [
    ({'status': 401, 'body': b'x'}, 'http_failed'),
    ({'status': 423, 'body': b'x'}, 'http_failed'),
    ({'status': 429, 'body': b'x'}, 'http_failed'),
    ({'status': 500, 'body': b'x'}, 'http_failed'),
    ({'payload': {'status': 'error', 'message': '普通业务拒绝'}},
     'business_rejected'),
    ({'body': b'{bad-json'}, 'invalid_response'),
    ({'body': b'', 'read_error': requests.exceptions.ReadTimeout(SECRET)},
     'transport_failed'),
])
def test_beeimg_cn_group_errors_keep_classification(storage_http, reply, code):
    """BeeIMG.CN 组查询的 401/429/5xx/网络/畸形仍保留既有分类。"""
    queue_storage_success(storage_http)
    storage_http['replies'][0] = reply
    with pytest.raises(ImageHostError) as caught:
        storage_module().fetch_storage_metadata('beeimg_cn', SECRET, now=1000)
    assert (caught.value.code, caught.value.stage) == (code, 'group')
    assert_safe(caught.value, code, 'group',
                200 if 'status' not in reply else reply['status'])


def test_forbidden_prompt_texts_are_verbatim(client):
    """两条固定权限提示逐字保留，展示合同不随选择路径移除而改变。"""
    texts = {
        'profile_forbidden': (
            'BeeIMG.cn 账号资料接口无访问权限，请为令牌授权资料读取权限，'
            '或填写该账号可用的 options.storage_id。'),
        'profile_forbidden_invalid_storage': (
            '手填储存驱动无效，请授权资料读取权限或填写当前账号可用的 '
            'options.storage_id。'),
    }
    for code, text in texts.items():
        assert str(ImageHostError(code, '')) == text
    assert not client.sessions
    assert not client.calls


ENTRY_MODULES = ('v2_http', 'storage', 'storage_beeimg_cn', 'storage_boltp')


def beeimg_group_payload(ids=(13, 14), expire=0):
    """构造只含组信息的 BeeIMG.CN 合成成功响应。"""
    return {'status': 'success', 'data': {
        'group': {'options': {'file_expire_seconds': expire}},
        'storages': [{'id': value} for value in ids]}}


def test_beeimg_group_only_contract(monkeypatch):
    """BeeIMG.CN 仅请求 group：不取默认编号、不请求 profile、仍取组内首项。"""
    bee = site_module('storage_beeimg_cn')
    module = storage_module()
    calls = []
    group = beeimg_group_payload()

    def request_json(provider, stage, token):
        """记录唯一一次查询的站点、阶段与凭证形态。"""
        calls.append(('GET', stage, token or None))
        return group

    monkeypatch.setattr(http(), 'request_json', request_json)
    metadata = bee.fetch_metadata('', now=1000)
    assert calls == [('GET', 'group', None)]
    assert metadata.storage_ids == (13, 14)
    assert metadata.default_storage_id is None
    assert metadata.profile_state == 'not_requested'
    assert module.select_storage(
        metadata, False, None, provider='beeimg_cn').storage_id == 13
    assert module.select_storage(
        metadata, True, 13, provider='beeimg_cn',
        excluded_id=13).storage_id == 14
    assert metadata.storage_ids == (13, 14)
    for token in (SECRET, '  ' + SECRET + '  '):
        calls.clear()
        present = bee.fetch_metadata(token, now=1000)
        assert calls == [('GET', 'group', token)]
        assert present.default_storage_id is None
        assert present.profile_state == 'not_requested'
    with pytest.raises(ImageHostError) as caught:
        module.select_storage(replace(metadata, storage_ids=(13,)), False, None,
                              provider='beeimg_cn', excluded_id=13)
    assert_safe(caught.value, 'storage_unavailable', 'storage')
    with pytest.raises(ImageHostError) as caught:
        module.select_storage(replace(metadata, storage_ids=()), False, None,
                              provider='beeimg_cn')
    assert_safe(caught.value, 'storage_unavailable', 'storage')
    for payload, code, stage in (
            ({}, 'storage_lookup_failed', 'group'),
            ({'data': {'storages': [{'id': 13}]}},
             'storage_lookup_failed', 'group'),
            ({'data': {'group': {}, 'storages': [{'id': 13}]}},
             'storage_lookup_failed', 'group'),
            ({'data': {'group': {'options': None}, 'storages': [{'id': 13}]}},
             'storage_lookup_failed', 'group'),
            ({'data': {'group': {'options': {}}, 'storages': {}}},
             'storage_lookup_failed', 'group'),
            ({'data': {'group': {'options': {}}, 'storages': [{'id': 13}, 'bad']}},
             'storage_lookup_failed', 'group'),
            ({'data': {'group': {'options': {}}, 'storages': [{'id': 13},
                                                             {'id': True}]}},
             'storage_lookup_failed', 'group'),
            ({'data': {'group': {'options': {'file_expire_seconds': -1}},
                      'storages': [{'id': 13}]}},
             'storage_lookup_failed', 'group'),
            ({'data': {'group': {'options': {'file_expire_seconds': True}},
                      'storages': [{'id': 13}]}},
             'storage_lookup_failed', 'group'),
            ({'data': {'group': {'options': {}}, 'storages': []}},
             'storage_unavailable', 'storage'),
    ):
        with pytest.raises(ImageHostError) as caught:
            module.parse_group(payload)
        assert_safe(caught.value, code, stage)


@pytest.mark.parametrize('present, manual', [
    (False, None), (True, 13), (True, 99), (True, 0), (True, '13'),
])
@pytest.mark.parametrize('default', [None, 13, 14, 99])
@pytest.mark.parametrize('profile_state', ['not_requested', 'forbidden',
                                          'available'])
def test_beeimg_selection_ignores_profile(caplog, present, manual, default,
                                          profile_state):
    """BeeIMG.CN 选择忽略手填优先级、默认编号与资料状态，只按组顺序取首项。"""
    module = storage_module()
    metadata = module.StorageMetadata((13, 14), default, 0, 1000, profile_state)
    original = deepcopy((metadata, manual))
    assert module.select_storage(
        metadata, present, manual, provider='beeimg_cn') == (
        module.StorageSelection(13, present))
    assert module.select_storage(
        metadata, present, manual, provider='beeimg_cn',
        excluded_id=13).storage_id == 14
    assert (metadata, manual) == original
    assert not caplog.records


def test_provider_parser_isolation(monkeypatch):
    """另一站入口被打桩为失败时，本站共享解析、查询、选择与校验不受影响。"""
    bee = site_module('storage_beeimg_cn')
    bolt = site_module('storage_boltp')
    module = storage_module()
    transport = http()
    group = beeimg_group_payload()
    upload = {'status': 'success',
              'data': {'public_url': 'https://example.com/synthetic.png'}}

    def boom(*args, **kwargs):
        """本站代码若命中另一站入口即失败。"""
        pytest.fail('不得命中另一站入口')

    def beeimg_transport(provider, stage, token):
        """只接受本站查询，避免以传输替身掩盖跨站调用。"""
        assert (provider, stage) == ('beeimg_cn', 'group')
        return group

    def boltp_transport(provider, stage, token):
        """只接受本站组查询，避免掩盖跨站调用。"""
        assert (provider, stage) == ('boltp', 'group')
        return group

    with monkeypatch.context() as bolt_patch:
        bolt_patch.setattr(bolt, 'fetch_metadata', boom)
        monkeypatch.setattr(transport, 'request_json', beeimg_transport)
        assert module.parse_group(group)[0] == (13, 14)
        metadata = bee.fetch_metadata('', now=1000)
        assert (metadata.storage_ids, metadata.profile_state) == (
            (13, 14), 'not_requested')
        assert module.select_storage(
            metadata, False, None, provider='beeimg_cn').storage_id == 13
        assert module.fetch_storage_metadata(
            'beeimg_cn', '', now=1000).storage_ids == (13, 14)
        transport._validate_payload('group', group)
        transport._validate_payload('upload', upload)
    with monkeypatch.context() as bee_patch:
        bee_patch.setattr(bee, 'fetch_metadata', boom)
        monkeypatch.setattr(transport, 'request_json', boltp_transport)
        assert module.parse_group(group)[0] == (13, 14)
        metadata = bolt.fetch_metadata(SECRET, now=1000)
        assert (metadata.default_storage_id, metadata.profile_state) == (
            None, 'not_requested')
        assert module.select_storage(
            metadata, False, None, provider='boltp').storage_id == 13
        assert module.fetch_storage_metadata(
            'boltp', SECRET, now=1000).storage_ids == (13, 14)
        transport._validate_payload('group', group)
        transport._validate_payload('upload', upload)


def test_v2_obsolete_interfaces_removed():
    """旧期限、资料解析与固定候选入口已删除，仅保留统一组查询。"""
    module = storage_module()
    context_module = import_module('modules.image_host.context')
    transport = http()
    assert not hasattr(module, 'fetch_boltp_retention')
    for name in ('storage_beeimg_cn', 'storage_boltp'):
        site = site_module(name)
        assert callable(site.fetch_metadata)
        for removed in ('parse_group', 'parse_profile', 'parse_retention',
                        'fetch_retention', 'select_storage'):
            assert not hasattr(site, removed), removed
    for removed in ('get_boltp_retention', '_prepare_legacy_storage'):
        assert not hasattr(context_module.UploadContext, removed), removed
    with context_module.UploadContext() as context:
        for removed in ('_retentions', '_corrected'):
            assert not hasattr(context, removed), removed
    assert 'profile' not in transport._PATHS
    assert set(transport._PATHS) == {'group', 'upload'}
    metadata_fields = {item.name for item in fields(module.StorageMetadata)}
    assert {'default_storage_id', 'profile_state'} <= metadata_fields
    entry_fields = {item.name for item in fields(context_module._MetadataEntry)}
    assert 'corrected' in entry_fields
    state_fields = {item.name for item in fields(context_module.StorageState)}
    assert 'correction_used' in state_fields
    assert 'correction' in signature(
        context_module.UploadContext.get_metadata).parameters


@pytest.mark.parametrize('first', ENTRY_MODULES)
def test_v2_shared_import_orders(first):
    """四种首模块顺序均可冷启动，冷进程不建立任何 socket。"""
    root = Path(__file__).resolve().parents[1]
    interpreter = Path(sys.executable)
    assert interpreter.parent.name == 'Scripts'
    assert (interpreter.parent.parent / 'pyvenv.cfg').is_file()
    rest = [name for name in ENTRY_MODULES if name != first]
    script = '\n'.join([
        'import importlib',
        'import socket',
        '',
        '_BaseSocket = socket.socket',
        '',
        '',
        'class _GuardedSocket(_BaseSocket):',
        '    """禁止连接但保留可被子类化的套接字类型。"""',
        '',
        '    def connect(self, address):',
        "        raise RuntimeError('cold import must not open sockets')",
        '',
        '',
        'def _forbid_create_connection(*args, **kwargs):',
        "    raise RuntimeError('cold import must not open sockets')",
        '',
        '',
        'socket.socket = _GuardedSocket',
        'socket.create_connection = _forbid_create_connection',
        '',
        'import modules.image_host.' + first,
        'for name in ' + repr(rest) + ':',
        "    importlib.import_module('modules.image_host.' + name)",
        '',
    ])
    completed = subprocess.run(
        [sys.executable, '-B', '-c', script],
        cwd=root, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
