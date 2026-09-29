#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""通过本地合成响应验证图床契约，不访问外网或模拟实站证据。"""

from collections import UserDict
from copy import deepcopy
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from importlib import import_module
from inspect import signature
import json
import logging
from threading import Thread
import traceback

import pytest
import requests


IMAGE = b'local-synthetic-png-bytes'
FILENAME = 'screenshot.png'
SECRET = 'FAKE_CATBOX_SECRET_7319'
ENV_NAME = 'CATBOX_LOCAL_TEST_TOKEN'
URL = 'https://cdn.example.com/image?id=1&signature=a%2Fb%3D'
ENDPOINT = 'https://catbox.moe/user/api.php'
WMIMG_ENDPOINT = 'https://wmimg.com/api/v1/upload'


def providers():
    """延迟读取真实适配器，使尚未实现表现为用例失败。"""
    return import_module('modules.image_host.providers')


def registry():
    """读取实际静态注册表和上传入口。"""
    return import_module('modules.image_host.registry')


def upload(token=None, options=None, extra_hosts=()):
    """经真实入口上传本地合成图片，凭证统一由注册表解析。"""
    hosts = [{'provider': 'catbox', 'token': token, 'options': options}]
    return registry().upload_with_fallback(
        IMAGE, FILENAME, hosts + list(extra_hosts))


class FakeResponse:
    """仅在内存中提供状态和文本，并记录资源关闭。"""

    def __init__(self, status=200, text=URL, error=None):
        """保存合成响应，不代表 Catbox 实站返回内容。"""
        self.status_code = status
        self.body = text
        self.error = error
        self.closed = 0

    @property
    def text(self):
        """返回合成文本或模拟读取响应失败。"""
        if self.error is not None:
            raise self.error
        return self.body

    def __enter__(self):
        """进入响应所有权作用域。"""
        return self

    def __exit__(self, *args):
        """记录响应释放且不吞掉异常。"""
        self.closed += 1


class FakeSession:
    """只记录上传参数的本地客户端，不提供任何网络能力。"""

    def __init__(self):
        """初始化响应、异常和所有权记录。"""
        self.response = FakeResponse()
        self.error = None
        self.calls = []
        self.closed = 0
        self.created = 0

    def create(self):
        """记录每次新建客户端，返回当前用例独占替身。"""
        self.created += 1
        return self

    def __enter__(self):
        """进入客户端所有权作用域。"""
        return self

    def __exit__(self, *args):
        """记录客户端释放且保留进程控制信号。"""
        self.closed += 1

    def post(self, url, **kwargs):
        """记录唯一 POST，随后返回合成响应或抛出合成异常。"""
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    """阻止意外联网，保留原方法供严格限定端点的回环测试使用。"""
    original_request = requests.sessions.Session.request

    def forbidden(*args, **kwargs):
        """立即拒绝真实请求。"""
        raise AssertionError('测试禁止访问网络')

    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    return original_request


@pytest.fixture
def client(monkeypatch):
    """替换 HTTP 边界而保留真实适配器和注册表。"""
    fake = FakeSession()
    monkeypatch.setattr(requests, 'Session', fake.create)
    return fake


def test_only_implemented_providers_are_registered():
    """生产注册表仅声明已实现的两个站点，不注册空壳。"""
    assert set(registry().UPLOADERS) == {'catbox', 'wmimg'}
    assert registry().UPLOADERS['catbox'] is providers().upload_catbox
    parameters = signature(providers().upload_catbox).parameters
    assert list(parameters) == ['image_bytes', 'filename', 'token', 'options']
    assert signature(providers().upload_catbox).return_annotation is str


@pytest.mark.parametrize('token', [None, ''])
def test_anonymous_multipart_contract(client, token):
    """匿名 POST 精确发送字段、文件名、PNG MIME 和安全 HTTP 参数。"""
    result = upload(token)
    assert result.success
    assert result.url == URL
    assert client.calls == [(ENDPOINT, {
        'data': {'reqtype': 'fileupload'},
        'files': {'fileToUpload': (FILENAME, IMAGE, 'image/png')},
        'timeout': (5, 15), 'verify': True, 'allow_redirects': False,
        'stream': True,
    })]
    assert client.created == client.closed == client.response.closed == 1


@pytest.mark.parametrize('token', [SECRET, '  ' + SECRET + '  '])
def test_direct_userhash_is_preserved(client, token):
    """非空凭证原样放入 userhash，不作为 Bearer 或修改空白。"""
    assert upload(token).success
    assert client.calls[0][1]['data'] == {
        'reqtype': 'fileupload', 'userhash': token,
    }
    assert 'headers' not in client.calls[0][1]
    assert 'auth' not in client.calls[0][1]


@pytest.mark.parametrize('resolved', [SECRET, '${NOT_EXPANDED_AGAIN}'])
def test_environment_is_resolved_once(client, monkeypatch, resolved):
    """真实入口解析环境引用，适配器不再次展开其值。"""
    monkeypatch.setenv(ENV_NAME, resolved)
    assert upload('${' + ENV_NAME + '}').success
    assert client.calls[0][1]['data']['userhash'] == resolved


@pytest.mark.parametrize('body', [URL, ' \r\n' + URL + '\r\n ',
                                   'http://other.example/image?x=1+2'])
def test_valid_candidate_preserves_full_url(client, caplog, body):
    """核心校验保留合法候选链接及签名，不探测域名或扩展名。"""
    client.response.body = body
    result = upload()
    assert result.success
    assert result.url == body.strip()
    assert len(client.calls) == 1
    assert not caplog.records
    assert result.url not in repr(result)


@pytest.mark.parametrize('status', [199, 301, 302, 307, 308, 401, 429, 500])
def test_non_2xx_is_safe_failure(client, caplog, status):
    """非成功状态包括重定向均失败，不暴露响应或发起第二次请求。"""
    client.response = FakeResponse(status, SECRET + URL)
    result = upload(SECRET)
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert SECRET not in repr(result) + caplog.text
    assert len(client.calls) == 1
    assert client.closed == client.response.closed == 1
    assert not caplog.records


@pytest.mark.parametrize('error_type', [
    requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectionError, requests.exceptions.SSLError,
    requests.exceptions.RequestException,
])
def test_request_exceptions_are_sanitized(client, caplog, error_type):
    """请求异常携带假密钥时，结果和格式化异常均只保留安全摘要。"""
    client.error = error_type(SECRET + URL)
    result = upload(SECRET)
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert len(client.calls) == 1
    assert client.closed == 1
    assert client.response.closed == 0
    error_class = import_module('modules.image_host.core').ImageHostError
    with pytest.raises(error_class) as caught:
        providers().upload_catbox(IMAGE, FILENAME, SECRET, {})
    text = ''.join(traceback.format_exception(caught.value))
    assert SECRET not in text + repr(caught.value) + repr(result) + caplog.text
    assert URL not in text + repr(caught.value) + repr(result) + caplog.text
    assert not caplog.records
    assert client.closed == 2


@pytest.mark.parametrize('body', [
    '', ' \r\n', '<html>' + SECRET + '</html>', 'error: ' + SECRET,
    'https://user:' + SECRET + '@example.com/image',
    'https://example.com:bad/image', 'ftp://example.com/image',
])
def test_200_invalid_body_is_rejected(client, caplog, body):
    """HTTP 200 不等于成功，空体、HTML 和非法链接安全失败。"""
    client.response.body = body
    result = upload(SECRET)
    assert not result.success
    assert result.failures[0].code == 'invalid_url'
    assert SECRET not in repr(result) + caplog.text
    assert client.closed == client.response.closed == 1
    assert len(client.calls) == 1


def test_response_read_failure_closes_both_resources(client, caplog):
    """响应读取失败也释放已取得的响应与会话，不泄露原文。"""
    client.response.error = requests.exceptions.ConnectionError(SECRET)
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert client.closed == client.response.closed == 1
    assert SECRET not in repr(result) + caplog.text


@pytest.mark.parametrize('status, body, encoding', [
    pytest.param(200, URL.encode(), None, id='success'),
    pytest.param(500, SECRET.encode(), None, id='http-error'),
    pytest.param(200, b'not-gzip-' * 8192, 'gzip', id='broken-gzip'),
])
def test_real_response_cleanup(
        monkeypatch, forbid_network, caplog, status, body, encoding):
    """经真实注册表读取回环响应，在兜底清理前验证连接确定性释放。"""
    received = []
    responses = []
    connections = []
    sockets = []
    closed = []
    decoding_errors = []

    class Handler(BaseHTTPRequestHandler):
        """仅接收本地合成上传，返回指定的 HTTP/1.1 响应。"""

        protocol_version = 'HTTP/1.1'

        def setup(self):
            """限制测试连接等待时间，避免服务线程无限阻塞。"""
            super().setup()
            self.connection.settimeout(2)

        def handle(self):
            """容许客户端关闭未读响应导致的预期连接重置。"""
            try:
                super().handle()
            except (ConnectionResetError, BrokenPipeError):
                pass

        def do_POST(self):
            """读取固定合成请求后发送具有正确长度的测试正文。"""
            received.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            if encoding:
                self.send_header('Content-Encoding', encoding)
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def log_message(self, format, *args):
            """不输出本地服务请求日志。"""
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    endpoint = f'http://127.0.0.1:{server.server_port}/user/api.php'
    thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05})
    thread.daemon = True
    original_send = requests.adapters.HTTPAdapter.send

    def local_request(session, method, url, **kwargs):
        """仅允许指定回环 POST，且不读取本机会话代理或认证配置。"""
        assert method.upper() == 'POST'
        assert url == endpoint
        session.trust_env = False
        return forbid_network(session, method, url, **kwargs)

    def observe_send(adapter, request, **kwargs):
        """保留真实传输及解压行为，在预读取之前记录响应所有权。"""
        response = original_send(adapter, request, **kwargs)
        responses.append(response)
        connection = response.raw.connection
        connections.append(connection)
        sockets.append(connection.sock)
        original_close = response.close
        original_iter_content = response.iter_content

        def observe_close():
            """记录关闭调用并执行原始响应清理。"""
            closed.append(response)
            return original_close()

        def observe_content(*args, **kwargs):
            """执行真实解压，仅记录异常类型而不保存敏感原文。"""
            try:
                yield from original_iter_content(*args, **kwargs)
            except requests.exceptions.ContentDecodingError:
                decoding_errors.append(requests.exceptions.ContentDecodingError)
                raise

        monkeypatch.setattr(response, 'close', observe_close)
        monkeypatch.setattr(response, 'iter_content', observe_content)
        return response

    try:
        monkeypatch.setattr(providers(), 'CATBOX_ENDPOINT', endpoint)
        monkeypatch.setattr(requests.sessions.Session, 'request', local_request)
        monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', observe_send)
        thread.start()
        result = upload(SECRET)
        assert result.attempts == ('catbox',)
        if status == 200 and encoding is None:
            assert result.success
            assert result.url == URL
        else:
            assert not result.success
            assert result.url is None
            assert result.failures[0].code == 'upload_failed'
            assert result.failures[0].message == '图床上传失败'
        assert len(received) == len(responses) == 1
        assert IMAGE in received[0]
        assert SECRET.encode() in received[0]
        assert SECRET not in repr(result) + caplog.text
        assert 'not-gzip-' not in repr(result) + caplog.text
        assert decoding_errors == (
            [requests.exceptions.ContentDecodingError] if encoding else [])
        response = responses[0]
        assert {
            'close_calls': closed.count(response),
            'raw_closed': response.raw.closed,
            'connection_released': response.raw.connection is None,
        } == {
            'close_calls': 1,
            'raw_closed': True,
            'connection_released': True,
        }
        if encoding:
            assert sockets[0].fileno() == -1
            assert connections[0].sock is None
    finally:
        for response in responses:
            response.close()
        for connection in connections:
            connection.close()
        if thread.ident is not None:
            server.shutdown()
        server.server_close()
        if thread.ident is not None:
            thread.join(timeout=3)
            assert not thread.is_alive()


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('stage', ['post', 'text'])
def test_control_signals_propagate_and_cleanup(client, signal_type, stage):
    """上传或响应处理中的控制信号原样传播，按已获得所有权释放。"""
    signal = signal_type('local stop')
    if stage == 'post':
        client.error = signal
    else:
        client.response.error = signal
    with pytest.raises(signal_type) as caught:
        upload()
    assert caught.value is signal
    assert client.closed == 1
    assert client.response.closed == (stage == 'text')
    assert len(client.calls) == 1


@pytest.mark.parametrize('permission', [0, '0', False])
def test_private_permission_rejected_before_network(client, permission):
    """私有要求不能被静默改为公开上传，必须在创建会话前拒绝。"""
    result = upload(options={'permission': permission})
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert client.calls == []
    assert client.created == 0


def test_public_permission_is_not_forwarded(client, caplog):
    """公开要求可接受，但不发送服务未确认支持的 permission 字段。"""
    assert upload(options={'permission': 1}).success
    assert client.calls[0][1]['data'] == {'reqtype': 'fileupload'}
    assert not caplog.records


def test_unsupported_options_warn_without_values_or_unknown_keys(
        client, caplog):
    """已知选项只记录固定名称，未知键不成为日志注入渠道。"""
    options = {'album_id': SECRET, 'strategy_id': URL,
               SECRET + '\nforged warning': {'token': SECRET}}
    with caplog.at_level(logging.WARNING):
        result = upload(options=options)
    assert result.success
    assert client.calls[0][1]['data'] == {'reqtype': 'fileupload'}
    assert 'album_id' in caplog.text
    assert 'strategy_id' in caplog.text
    assert SECRET not in caplog.text
    assert URL not in caplog.text
    assert 'forged warning' not in caplog.text
    assert caplog.records
    assert all(record.levelno == logging.WARNING for record in caplog.records)


@pytest.mark.parametrize('status', [200, 500])
def test_real_registry_fallback_and_stop(client, monkeypatch, status):
    """Catbox 失败才调用本地备用，成功后不再尝试下一项。"""
    client.response.status_code = status
    calls = []

    def fallback(image_bytes, filename, token, options):
        """提供仅在内存中成功的备用适配器。"""
        calls.append((image_bytes, filename, token, options))
        return URL

    monkeypatch.setitem(registry().UPLOADERS, 'local', fallback)
    result = upload(extra_hosts=[{'provider': 'local'}])
    assert result.success
    if status == 200:
        assert result.provider == 'catbox'
        assert result.attempts == ('catbox',)
        assert calls == []
    else:
        assert result.provider == 'local'
        assert result.attempts == ('catbox', 'local')
        assert result.failures[0].code == 'upload_failed'
        assert calls == [(IMAGE, FILENAME, '', {})]
    assert len(client.calls) == 1


def test_unimplemented_provider_remains_unknown(client):
    """尚未实现的图床仍按未知提供方返回，不发送网络请求。"""
    result = registry().upload_with_fallback(
        IMAGE, FILENAME, [{'provider': 'smms'}])
    assert not result.success
    assert result.failures[0].code == 'unknown_provider'
    assert client.calls == []


def test_signed_success_diagnostics_hide_secrets(client, caplog):
    """业务结果完整保留合成签名链接，而日志和 repr 不泄露。"""
    signed = URL + '&token=' + SECRET
    client.response.body = signed
    result = upload(SECRET)
    assert result.success
    assert result.url == signed
    assert SECRET not in repr(result) + str(result) + caplog.text
    assert signed not in repr(result) + str(result) + caplog.text


def wmimg_payload(url=URL):
    """依据官方字段表构造合成夹具，不代表实际上传返回结果。"""
    return {'status': True, 'data': {'links': {'url': url}}}


def upload_wmimg(token=None, options=None, extra_hosts=()):
    """经真实注册表调用 WMIMG，不替换凭证解析或故障转移。"""
    hosts = [{'provider': 'wmimg', 'token': token, 'options': options}]
    return registry().upload_with_fallback(
        IMAGE, FILENAME, hosts + list(extra_hosts))


class WmimgResponse(FakeResponse):
    """使用真实 JSON 解码器读取本地合成正文并验证响应所有权。"""

    def __init__(self):
        """初始化依据官方字段表生成的成功正文。"""
        super().__init__(text=json.dumps(wmimg_payload()))
        self.active = False
        self.reads = 0

    def __enter__(self):
        """标记响应正文可以在此作用域内读取。"""
        self.active = True
        return super().__enter__()

    def __exit__(self, *args):
        """清除所有权标记并执行已有关闭记录。"""
        self.active = False
        return super().__exit__(*args)

    def json(self):
        """只允许在响应上下文内读取并解析 JSON。"""
        assert self.active
        self.reads += 1
        response = requests.Response()
        response._content = self.text.encode('utf-8')
        response.encoding = 'utf-8'
        return response.json()


@pytest.fixture
def wmclient(client):
    """沿用无网络会话替身，仅替换 WMIMG 的合成响应。"""
    client.response = WmimgResponse()
    return client


def test_wmimg_registration_and_signature():
    """静态注册指向真实适配器，签名与上传核心一致。"""
    adapter = getattr(providers(), 'upload_wmimg', None)
    assert callable(adapter)
    assert registry().UPLOADERS['wmimg'] is adapter
    assert list(signature(adapter).parameters) == [
        'image_bytes', 'filename', 'token', 'options',
    ]
    assert signature(adapter).return_annotation is str


@pytest.mark.parametrize('token', [None, '', SECRET, '  ' + SECRET + '  '])
def test_wmimg_multipart_and_credentials(wmclient, token):
    """账号凭证只进入 Bearer，匿名不被本地拒绝且不手设边界。"""
    result = upload_wmimg(token)
    assert result.success
    assert result.url == URL
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    assert wmclient.calls == [(WMIMG_ENDPOINT, {
        'data': {'permission': 1},
        'files': {'file': (FILENAME, IMAGE, 'image/png')},
        'headers': headers, 'timeout': (5, 15), 'verify': True,
        'stream': True, 'allow_redirects': False,
    })]
    assert wmclient.created == wmclient.closed == wmclient.response.closed == 1
    assert wmclient.response.reads == 1
    assert 'trust_env' not in vars(wmclient)


@pytest.mark.parametrize('resolved', [SECRET, '${NOT_EXPANDED_AGAIN}'])
def test_wmimg_environment_reference_is_resolved_once(
        wmclient, monkeypatch, resolved):
    """只读取测试设置的环境引用，解析值原样用于账号请求头。"""
    monkeypatch.setenv(ENV_NAME, resolved)
    assert upload_wmimg('${' + ENV_NAME + '}').success
    request = wmclient.calls[0][1]
    assert request['headers']['Authorization'] == 'Bearer ' + resolved
    assert request['data'] == {'permission': 1}


@pytest.mark.parametrize('options, expected', [
    ({}, {'permission': 1}),
    ({'permission': 0}, {'permission': 0}),
    ({'permission': 1}, {'permission': 1}),
    ({'album_id': 1}, {'permission': 1, 'album_id': 1}),
    ({'strategy_id': 2}, {'permission': 1, 'strategy_id': 2}),
    ({'permission': 0, 'album_id': 23, 'strategy_id': 45},
     {'permission': 0, 'album_id': 23, 'strategy_id': 45}),
])
def test_wmimg_supported_options_are_exact_and_unchanged(
        wmclient, options, expected):
    """权限不被真值默认覆盖，可选整数仅在显式配置时发送。"""
    original = deepcopy(options)
    assert upload_wmimg(options=options).success
    assert wmclient.calls[0][1]['data'] == expected
    assert options == original
    providers().upload_wmimg(IMAGE, FILENAME, '', options)
    assert options == original


@pytest.mark.parametrize('name', ['album_id', 'strategy_id'])
@pytest.mark.parametrize('value', [0, -1])
def test_wmimg_explicit_integer_ids_are_forwarded(wmclient, name, value):
    """文档未定义 ID 范围，显式整数原样交由服务端验证有效性。"""
    result = upload_wmimg(options={'permission': 0, name: value})
    assert result.success
    assert wmclient.calls[0][1]['data'] == {'permission': 0, name: value}
    assert wmclient.created == wmclient.closed == wmclient.response.closed == 1


@pytest.mark.parametrize('name', ['permission', 'album_id', 'strategy_id'])
@pytest.mark.parametrize('value', [None, True, False, '1', 1.0, [], {}])
def test_wmimg_invalid_option_types_fail_before_request(
        wmclient, name, value):
    """非法类型在创建会话前拒绝，不改变显式安全要求。"""
    options = {name: value}
    original = deepcopy(options)
    result = upload_wmimg(options=options)
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert wmclient.created == 0
    assert wmclient.calls == []
    assert options == original


@pytest.mark.parametrize('options', [
    {'permission': 2}, {'permission': -1},
])
def test_wmimg_invalid_option_range_fails_before_request(wmclient, options):
    """权限仅允许整数零或一，越界值在创建会话前拒绝。"""
    result = upload_wmimg(options=options)
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert wmclient.created == 0
    assert wmclient.calls == []


def test_wmimg_unknown_options_warn_without_sensitive_data(wmclient, caplog):
    """未支持字段仅产生固定警告，不转发临时凭证或过期选项。"""
    options = {'permission': 0, 'token': SECRET, 'expired_at': SECRET,
               SECRET + '\nforged warning': {'url': URL}}
    original = deepcopy(options)
    with caplog.at_level(logging.WARNING):
        result = upload_wmimg(SECRET, options)
    assert result.success
    assert wmclient.calls[0][1]['data'] == {'permission': 0}
    assert options == original
    assert [record.getMessage() for record in caplog.records] == [
        'WMIMG 存在不支持的其他选项，已忽略',
    ]
    assert all(record.levelno == logging.WARNING for record in caplog.records)
    assert SECRET not in caplog.text + repr(result)
    assert URL not in caplog.text + repr(result)


@pytest.mark.parametrize('status', [False, 'true', 1, 0, None, [], {}])
def test_wmimg_business_status_requires_boolean_true(wmclient, caplog, status):
    """即使错误正文含有效链接，也只允许布尔真代表业务成功。"""
    payload = wmimg_payload()
    payload.update(status=status, message=SECRET + URL)
    wmclient.response.body = json.dumps(payload)
    result = upload_wmimg(SECRET)
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert result.failures[0].message == '图床上传失败'
    assert wmclient.closed == wmclient.response.closed == 1
    assert SECRET not in repr(result) + caplog.text
    assert URL not in repr(result) + caplog.text


@pytest.mark.parametrize('payload', [
    None, [], 'true', True, 1, {}, {'data': {'links': {'url': URL}}},
    {'status': True}, {'status': True, 'data': None},
    {'status': True, 'data': []}, {'status': True, 'data': 'bad'},
    {'status': True, 'data': {}},
    {'status': True, 'data': {'links': None}},
    {'status': True, 'data': {'links': []}},
    {'status': True, 'data': {'links': 'bad'}},
])
def test_wmimg_invalid_json_structure_is_safe(wmclient, payload):
    """JSON 顶层、数据与链接容器必须为映射，必要字段不得缺失。"""
    wmclient.response.body = json.dumps(payload)
    result = upload_wmimg()
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert wmclient.closed == wmclient.response.closed == 1


@pytest.mark.parametrize('body', ['', '<html>' + SECRET + URL + '</html>',
                                  'not-json-' + SECRET, '{"status":'])
def test_wmimg_non_json_is_sanitized_and_closed(wmclient, caplog, body):
    """HTML 或畸形 JSON 不回退猜测链接，格式化异常不泄露正文。"""
    wmclient.response.body = body
    result = upload_wmimg(SECRET)
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert wmclient.closed == wmclient.response.closed == 1
    error_class = import_module('modules.image_host.core').ImageHostError
    with pytest.raises(error_class) as caught:
        providers().upload_wmimg(IMAGE, FILENAME, SECRET, {})
    output = (''.join(traceback.format_exception(caught.value))
              + repr(caught.value) + repr(result) + caplog.text)
    assert SECRET not in output
    assert URL not in output
    assert wmclient.closed == wmclient.response.closed == 2


@pytest.mark.parametrize('url', [None, False, 1, [], {}, '',
                               'ftp://example.com/image',
                               'https://user:' + SECRET + '@example.com/a',
                               'https://example.com:' + SECRET])
def test_wmimg_invalid_url_is_sanitized_and_closed(wmclient, caplog, url):
    """候选直链走核心校验，错误释放响应且不暴露假敏感字段。"""
    wmclient.response.body = json.dumps(wmimg_payload(url))
    result = upload_wmimg(SECRET)
    assert not result.success
    assert result.failures[0].code == 'invalid_url'
    assert wmclient.closed == wmclient.response.closed == 1
    assert SECRET not in repr(result) + caplog.text


def test_wmimg_missing_url_is_rejected(wmclient):
    """只接受明确的直链字段，不从缩略图或删除链接推断成功。"""
    wmclient.response.body = json.dumps({
        'status': True, 'data': {'links': {'thumbnail_url': URL}},
    })
    result = upload_wmimg()
    assert not result.success
    assert result.failures[0].code == 'invalid_url'
    assert wmclient.closed == wmclient.response.closed == 1


def test_wmimg_mapping_and_signed_url_are_preserved(
        wmclient, monkeypatch, caplog):
    """各层允许映射，签名直链完整返回但不进入日志或诊断表示。"""
    signed = URL + '&token=' + SECRET
    payload = UserDict(status=True, data=UserDict(
        links=UserDict(url=signed)))

    def mapping_json():
        """在响应作用域内返回合成映射，而非限制实现为字典。"""
        assert wmclient.response.active
        return payload

    monkeypatch.setattr(wmclient.response, 'json', mapping_json)
    result = upload_wmimg(SECRET)
    assert result.success
    assert result.url == signed
    assert SECRET not in repr(result) + str(result) + caplog.text
    assert signed not in repr(result) + str(result) + caplog.text
    assert wmclient.closed == wmclient.response.closed == 1
    assert len(wmclient.calls) == 1
    assert not caplog.records


@pytest.mark.parametrize('status', [199, 302, 401, 403, 429, 500])
def test_wmimg_http_errors_do_not_read_or_follow(wmclient, caplog, status):
    """非成功 HTTP 状态直接失败，不解析正文或跟随重定向。"""
    wmclient.response.status_code = status
    wmclient.response.body = SECRET + URL
    result = upload_wmimg(SECRET)
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert wmclient.response.reads == 0
    assert wmclient.closed == wmclient.response.closed == 1
    assert len(wmclient.calls) == 1
    assert SECRET not in repr(result) + caplog.text
    assert URL not in repr(result) + caplog.text


@pytest.mark.parametrize('error_type', [
    requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectionError, requests.exceptions.SSLError,
    requests.exceptions.ContentDecodingError,
])
@pytest.mark.parametrize('stage', ['post', 'json'])
def test_wmimg_transport_errors_are_safe_and_closed(
        wmclient, caplog, error_type, stage):
    """连接、读取与解压错误仅保留安全摘要并释放已取得的资源。"""
    if stage == 'post':
        wmclient.error = error_type(SECRET + URL)
    else:
        wmclient.response.error = error_type(SECRET + URL)
    result = upload_wmimg(SECRET)
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert wmclient.closed == 1
    assert wmclient.response.closed == (stage == 'json')
    error_class = import_module('modules.image_host.core').ImageHostError
    with pytest.raises(error_class) as caught:
        providers().upload_wmimg(IMAGE, FILENAME, SECRET, {})
    output = (''.join(traceback.format_exception(caught.value))
              + repr(caught.value) + repr(result) + caplog.text)
    assert SECRET not in output
    assert URL not in output
    assert not caplog.records
    assert wmclient.closed == 2
    assert wmclient.response.closed == 2 * (stage == 'json')


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('stage', ['post', 'json'])
def test_wmimg_control_signals_propagate_and_close(
        wmclient, monkeypatch, signal_type, stage):
    """控制信号原样上抛，资源照常释放且备用站点不会执行。"""
    signal = signal_type('local stop')
    if stage == 'post':
        wmclient.error = signal
    else:
        wmclient.response.error = signal
    calls = []

    def fallback(*args):
        """记录控制信号后不应发生的备用调用。"""
        calls.append(args)
        return URL

    monkeypatch.setitem(registry().UPLOADERS, 'local', fallback)
    with pytest.raises(signal_type) as caught:
        upload_wmimg(extra_hosts=[{'provider': 'local'}])
    assert caught.value is signal
    assert calls == []
    assert wmclient.closed == 1
    assert wmclient.response.closed == (stage == 'json')


@pytest.mark.parametrize('failure', [None, 'http', 'business', 'json',
                                     'structure', 'url', 'options'])
def test_wmimg_registry_fallback_and_stop(wmclient, monkeypatch, failure):
    """各类当前项失败允许本地备用，首次成功则停止逐站尝试。"""
    options = {}
    if failure == 'http':
        wmclient.response.status_code = 500
    elif failure == 'business':
        wmclient.response.body = json.dumps({'status': False, 'message': SECRET})
    elif failure == 'json':
        wmclient.response.body = '<html>' + SECRET
    elif failure == 'structure':
        wmclient.response.body = '[]'
    elif failure == 'url':
        wmclient.response.body = json.dumps(wmimg_payload('invalid'))
    elif failure == 'options':
        options = {'permission': False}
    calls = []

    def fallback(image_bytes, filename, token, options):
        """记录不访问网络的备用上传参数。"""
        calls.append((image_bytes, filename, token, options))
        return URL

    monkeypatch.setitem(registry().UPLOADERS, 'local', fallback)
    result = upload_wmimg(SECRET, options, [{'provider': 'local'}])
    assert result.success
    if failure is None:
        assert result.provider == 'wmimg'
        assert result.attempts == ('wmimg',)
        assert result.failures == ()
        assert calls == []
    else:
        assert result.provider == 'local'
        assert result.attempts == ('wmimg', 'local')
        assert len(result.failures) == 1
        assert result.failures[0].provider == 'wmimg'
        assert calls == [(IMAGE, FILENAME, '', {})]
    assert len(wmclient.calls) == (failure != 'options')
