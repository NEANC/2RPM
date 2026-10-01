#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""依据 Boltp（闪电图床）官方接口文档构造合成响应，绝不代表线上结果。

契约来源：https://www.boltp.com/api/v2/pages/api-docs（站点自带文档页面
数据，核对日期 2026-09-30）；未做真实上传，也未使用真实令牌。
文档存储 ID 字段表与请求示例互相冲突，因此测试只使用自选合成值。
仅替换 HTTP 边界，通过真实注册表验证上传与顺序故障转移。
"""

from copy import deepcopy
from importlib import import_module
from inspect import signature
import json
import logging
import traceback

import pytest
import requests


IMAGE = b'\x89PNG\r\n\x1a\nlocal-synthetic-image'
FILENAME = '本地截图.png'
SECRET = 'FAKE_BOLTP_SECRET_7319'
ENV_NAME = 'BOLTP_LOCAL_TEST_TOKEN'
URL = 'https://www.boltp.com/20260930/synthetic.png?signature=a%2Fb%3D&x=1+2'
ENDPOINT = 'https://www.boltp.com/api/v2/upload'
WARNING = 'Boltp 存在不支持的其他选项，已忽略'
STORAGE_ID = 5
OPTIONS = {'storage_id': STORAGE_ID}
UNSUPPORTED = {'tags': ['本地标签'], 'expired_at': '2030-01-01 00:00:00',
               'intro': '本地描述', 'is_remove_exif': True}


def registry():
    """读取生产静态注册表，不动态安装待测适配器。"""
    return import_module('modules.image_host.registry')


def payload(url=URL):
    """按官方成功示例构造本地合成正文，未做任何真实上传。"""
    return {'status': 'success', 'message': '上传成功', 'time': 1735689600,
            'data': {'id': 1, 'public_url': url, 'is_public': True}}


def upload(token=SECRET, options=None, extra_hosts=()):
    """通过实际入口处理凭证、参数副本及后续站点。"""
    if options is None:
        options = dict(OPTIONS)
    hosts = [{'provider': 'boltp', 'token': token, 'options': options}]
    return registry().upload_with_fallback(
        IMAGE, FILENAME, hosts + list(extra_hosts))


class Response:
    """提供合成 JSON 并记录响应作用域和释放次数。"""

    def __init__(self):
        """初始化文档结构的成功响应，不访问外网。"""
        self.status_code = 200
        self.body = json.dumps(payload())
        self.error = None
        self.active = False
        self.closed = 0
        self.reads = 0

    def __enter__(self):
        """开始响应所有权作用域。"""
        self.active = True
        return self

    def __exit__(self, *args):
        """记录关闭且不吞掉异常或进程控制信号。"""
        self.active = False
        self.closed += 1

    def iter_content(self, chunk_size):
        """在响应作用域内提供有界读取块，保留异常和释放断言。"""
        assert self.active
        assert 0 < chunk_size <= 1024 * 1024
        self.reads += 1
        if self.error is not None:
            raise self.error
        yield self.body.encode('utf-8')

    def json(self):
        """仅供旧站点备用适配器使用实际 JSON 解码器。"""
        assert self.active
        self.reads += 1
        if self.error is not None:
            raise self.error
        response = requests.Response()
        response._content = self.body.encode('utf-8')
        response.encoding = 'utf-8'
        return response.json()


class Session:
    """仅提供一次 POST 的无网络会话替身。"""

    def __init__(self):
        """初始化会话、请求和合成响应队列。"""
        self.response = Response()
        self.responses = []
        self.error = None
        self.calls = []
        self.created = 0
        self.closed = 0
        self.active = False

    def create(self):
        """记录构造次数以验证配置错误发生在请求之前。"""
        self.created += 1
        return self

    def __enter__(self):
        """进入会话所有权作用域。"""
        self.active = True
        return self

    def __exit__(self, *args):
        """验证响应先于会话关闭。"""
        assert not self.response.active
        self.active = False
        self.closed += 1

    def post(self, endpoint, **kwargs):
        """记录参数并返回当前合成响应或抛出指定异常。"""
        assert self.active
        self.calls.append((endpoint, kwargs))
        if self.error is not None:
            raise self.error
        if self.responses:
            self.response = self.responses.pop(0)
        return self.response


@pytest.fixture(autouse=True)
def client(monkeypatch):
    """仅替换 HTTP，并独立阻断任何意外真实网络访问。"""
    def forbidden(*args, **kwargs):
        """拒绝逃逸出替身的真实请求。"""
        raise AssertionError('测试禁止访问真实网络')

    fake = Session()
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    monkeypatch.setattr(requests, 'Session', fake.create)
    monkeypatch.setattr('modules.image_host.v2_http._V2Session', fake.create)
    return fake


def test_registration_and_four_argument_signature():
    """静态注册新增 Boltp 四参数适配器，并保留既有两个 v2 站点。"""
    providers = import_module('modules.image_host.providers')
    adapter = getattr(providers, 'upload_boltp', None)
    assert callable(adapter)
    assert registry().UPLOADERS == {
        'catbox': providers.upload_catbox,
        'wmimg': providers.upload_wmimg,
        'beeimg': providers.upload_beeimg,
        'beeimg_cn': providers.upload_beeimg_cn,
        'boltp': adapter,
        'superbed': providers.upload_superbed,
    }
    assert list(signature(adapter).parameters) == [
        'image_bytes', 'filename', 'token', 'options',
    ]
    assert signature(adapter).return_annotation is str


@pytest.mark.parametrize('token', [None, '', SECRET, '  ' + SECRET + '  '])
def test_exact_multipart_authentication_and_anonymous_contract(client, token):
    """官方文档未要求凭证，空凭证匿名上传且凭证仅进入 Bearer。"""
    result = upload(token)
    assert result.success
    assert result.url == URL
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    assert client.calls == [(ENDPOINT, {
        'data': {'storage_id': STORAGE_ID, 'is_public': '1'},
        'files': {'file': (FILENAME, IMAGE, 'image/png')},
        'headers': headers, 'timeout': (5, 15), 'verify': True,
        'stream': True, 'allow_redirects': False,
    })]
    assert client.created == client.closed == client.response.closed == 1
    assert client.response.reads == 1


@pytest.mark.parametrize('resolved', [SECRET, '${NOT_EXPANDED_AGAIN}'])
def test_environment_reference_is_resolved_once(client, monkeypatch, resolved):
    """只读取测试注入的环境引用，不递归展开环境值。"""
    monkeypatch.setenv(ENV_NAME, resolved)
    assert upload('${' + ENV_NAME + '}').success
    token = client.calls[0][1]['headers']['Authorization']
    assert token == 'Bearer ' + resolved


@pytest.mark.parametrize('value', [None, ''])
def test_missing_environment_fails_before_http(client, monkeypatch, value):
    """环境引用缺失或为空时安全失败，不静默退化为匿名上传。"""
    if value is None:
        monkeypatch.delenv(ENV_NAME, raising=False)
    else:
        monkeypatch.setenv(ENV_NAME, value)
    result = upload('${' + ENV_NAME + '}')
    assert not result.success
    assert result.failures[0].code == 'missing_environment'
    assert client.created == 0


@pytest.mark.parametrize('options', [{}, {'permission': 1}, {'permission': 0}])
def test_default_and_explicit_permission_map_to_is_public(client, options):
    """缺省与整数权限映射为显式公开值，绝不省略该字段。"""
    options = {**options, 'storage_id': STORAGE_ID}
    original = deepcopy(options)
    assert upload(options=options).success
    assert client.calls[0][1]['data'] == {
        'storage_id': STORAGE_ID,
        'is_public': '1' if options.get('permission', 1) else '0',
    }
    assert options == original


@pytest.mark.parametrize('permission', [
    True, False, -1, 2, '0', '1', 'private', 1.0, [], {},
])
def test_invalid_permission_fails_before_request(client, permission):
    """布尔权限与越界或字符串权限在创建会话前安全拒绝。"""
    result = upload(options={'storage_id': STORAGE_ID,
                            'permission': permission})
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert client.created == 0
    assert client.calls == []


@pytest.mark.parametrize('value', [True, False, '1', '0', 0, 1, None])
def test_direct_is_public_cannot_override_permission(client, value):
    """原生公开字段不得绕过项目权限语义被忽略后上传。"""
    result = upload(options={'storage_id': STORAGE_ID, 'is_public': value})
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert client.created == 0
    assert client.calls == []


@pytest.mark.parametrize('storage_id', [None, True, False, 0, -1, '5', 5.0,
                                        [], {}])
def test_invalid_storage_id_fails_before_request(client, storage_id):
    """存储 ID 必填且仅接受非布尔正整数，缺失或非法值不发请求。"""
    options = {} if storage_id is None else {'storage_id': storage_id}
    result = upload(options=options)
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert client.created == 0
    assert client.calls == []


@pytest.mark.parametrize('storage_id', [2, 3, 1, 99])
def test_explicit_positive_storage_id_is_forwarded(client, storage_id):
    """显式正整数存储 ID 原样发送，不套用文档冲突的示例或套餐值。"""
    options = {'storage_id': storage_id}
    original = deepcopy(options)
    assert upload(options=options).success
    assert client.calls[0][1]['data'] == {
        'storage_id': storage_id, 'is_public': '1',
    }
    assert options == original


def test_album_id_is_omitted_unless_explicitly_configured(client):
    """未配置相册时不发送相册字段，也不猜测或创建相册。"""
    assert upload().success
    assert 'album_id' not in client.calls[0][1]['data']


@pytest.mark.parametrize('album_id', [0, 1, 23, 999])
def test_explicit_integer_album_id_is_forwarded(client, album_id):
    """显式整数相册 ID 原样发送，取值范围由服务端判断。"""
    options = {'storage_id': STORAGE_ID, 'album_id': album_id}
    original = deepcopy(options)
    assert upload(options=options).success
    assert client.calls[0][1]['data'] == {
        'storage_id': STORAGE_ID, 'is_public': '1', 'album_id': album_id,
    }
    assert options == original


@pytest.mark.parametrize('album_id', [True, False, '5', 5.0, [], {}, None])
def test_invalid_album_id_type_is_rejected(client, album_id):
    """布尔、字符串或容器相册 ID 在请求前以安全类别拒绝。"""
    result = upload(options={'storage_id': STORAGE_ID, 'album_id': album_id})
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert client.created == 0
    assert client.calls == []


def test_unsupported_nonsafety_options_have_fixed_warning(client, caplog):
    """不透传其他字段，也不记录不可信键、值或完整配置。"""
    options = {'storage_id': STORAGE_ID, **UNSUPPORTED,
               SECRET + '\nforged warning': {'public_url': URL}}
    original = deepcopy(options)
    with caplog.at_level(logging.WARNING):
        result = upload(options=options)
    assert result.success
    assert options == original
    assert client.calls[0][1]['data'] == {
        'storage_id': STORAGE_ID, 'is_public': '1',
    }
    assert [(record.levelno, record.getMessage()) for record in caplog.records
            ] == [(logging.WARNING, WARNING)]
    assert SECRET not in repr(result) + caplog.text
    assert URL not in repr(result) + caplog.text
    assert 'forged warning' not in caplog.text
    assert 'is_remove_exif' not in caplog.text


@pytest.mark.parametrize('status', [
    True, False, 1, 0, None, [], {}, 'Success', 'SUCCESS', 'successful',
    'success ', SECRET,
])
def test_only_exact_official_success_status_is_accepted(client, caplog, status):
    """有效链接与 HTTP 200 不足以成功，只接受精确字符串状态。"""
    body = payload()
    body.update(status=status, message=SECRET)
    client.response.body = json.dumps(body)
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'invalid_response'
    assert result.failures[0].message == '图床响应无效'
    assert client.closed == client.response.closed == 1
    assert SECRET not in repr(result) + caplog.text


@pytest.mark.parametrize('body', [
    None, [], True, 1, 'success', {}, {'data': {'public_url': URL}},
    {'status': 'success'}, {'status': 'success', 'data': None},
    {'status': 'success', 'data': []}, {'status': 'success', 'data': 'bad'},
    {'status': 'success', 'data': {}},
])
def test_invalid_containers_fail_safely(client, body):
    """顶层、数据容器必须为映射，成功后仍必须有直链字段。"""
    client.response.body = json.dumps(body)
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'invalid_response'
    assert client.closed == client.response.closed == 1


def test_missing_direct_url_never_uses_other_link_fields(client):
    """只接受 data.public_url，不使用分享页或其他候选链接。"""
    client.response.body = json.dumps({'status': 'success', 'data': {
        'id': 1, 'url': URL, 'pathname': URL, 'thumbnail_url': URL,
    }})
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'invalid_response'
    assert client.closed == client.response.closed == 1


@pytest.mark.parametrize('url', [
    None, True, 123, [], {}, '', 'ftp://example.com/a', '//example.com/a',
    'https://user:' + SECRET + '@example.com/a',
    'https://example.com:' + SECRET,
])
def test_invalid_urls_use_v2_response_error_code(client, caplog, url):
    """直链仍经过核心校验，v2 分类不暴露无效候选或凭证。"""
    client.response.body = json.dumps(payload(url))
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'invalid_response'
    assert client.closed == client.response.closed == 1
    assert SECRET not in repr(result) + caplog.text


@pytest.mark.parametrize('body', ['', '<html>' + SECRET + '</html>',
                                  '{"status":', 'not-json-' + SECRET])
def test_malformed_json_is_safe_and_resources_close(client, caplog, body):
    """实际 JSON 解码失败不泄露正文并释放所有已取得资源。"""
    client.response.body = body
    result = upload(SECRET)
    assert not result.success
    assert result.failures[0].code == 'invalid_response'
    assert client.closed == client.response.closed == 1
    assert SECRET not in repr(result) + caplog.text
    assert not caplog.records


@pytest.mark.parametrize('status', [199, 300, 301, 302, 303, 307, 308,
                                   401, 403, 422, 429, 500])
def test_non_2xx_never_parses_body_or_follows_redirects(client, caplog, status):
    """HTTP 错误与重定向直接失败且无第二次请求。"""
    client.response.status_code = status
    client.response.body = SECRET
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'http_failed'
    assert client.response.reads == 0
    assert client.closed == client.response.closed == 1
    assert len(client.calls) == 1
    assert SECRET not in repr(result) + caplog.text


@pytest.mark.parametrize('error_type', [
    requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectionError, requests.exceptions.SSLError,
    requests.exceptions.ContentDecodingError, ValueError, RuntimeError,
])
@pytest.mark.parametrize('stage', ['post', 'json'])
def test_transport_and_ordinary_errors_release_owned_resources(
        client, caplog, error_type, stage):
    """超时、解压及普通错误仅返回固定摘要，不重试并按所有权清理。"""
    error = error_type(SECRET + URL)
    if stage == 'post':
        client.error = error
    else:
        client.response.error = error
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'transport_failed'
    assert result.failures[0].message == '图床网络传输失败'
    assert client.closed == 1
    assert client.response.closed == (stage == 'json')
    assert len(client.calls) == 1
    assert SECRET not in repr(result) + caplog.text
    assert URL not in repr(result) + caplog.text
    assert not caplog.records


@pytest.mark.parametrize('stage', ['post', 'json'])
@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_control_signal_identity_cleanup_and_no_fallback(
        client, stage, signal_type):
    """控制信号原对象向上传播，不在后续图床继续上传。"""
    signal = signal_type('local stop')
    if stage == 'post':
        client.error = signal
    else:
        client.response.error = signal
    with pytest.raises(signal_type) as caught:
        upload(extra_hosts=[{'provider': 'boltp'}])
    assert caught.value is signal
    assert client.closed == 1
    assert client.response.closed == (stage == 'json')
    assert len(client.calls) == 1


@pytest.mark.parametrize('kind', ['request', 'decode', 'ordinary'])
def test_adapter_exception_has_no_original_chain(client, caplog, kind):
    """额外验证适配器异常对象本身不保留敏感原因或上下文。"""
    if kind == 'request':
        client.error = requests.exceptions.ConnectTimeout(SECRET + URL)
    elif kind == 'decode':
        client.response.body = SECRET + URL
    else:
        client.response.error = RuntimeError(SECRET + URL)
    result = upload(SECRET)
    assert result.failures[0].code == (
        'invalid_response' if kind == 'decode' else 'transport_failed')
    providers = import_module('modules.image_host.providers')
    error_class = import_module('modules.image_host.core').ImageHostError
    with pytest.raises(error_class) as caught:
        providers.upload_boltp(
            IMAGE, FILENAME, SECRET, {'storage_id': STORAGE_ID})
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    text = ''.join(traceback.format_exception(caught.value))
    assert SECRET not in text + repr(result) + caplog.text
    assert URL not in text + repr(result) + caplog.text
    assert client.closed == 2
    assert client.response.closed == (0 if kind == 'request' else 2)


def test_signed_url_and_input_are_preserved_without_diagnostic_leaks(
        client, caplog):
    """保留完整签名直链且不修改源配置，结果 repr 隐藏 URL。"""
    signed = URL + '&token=' + SECRET
    client.response.body = json.dumps(payload(signed))
    hosts = [{'provider': ' BOLTP ', 'token': SECRET,
              'options': {'storage_id': STORAGE_ID, 'permission': 1,
                          'album_id': 23}}]
    original = deepcopy(hosts)
    result = registry().upload_with_fallback(IMAGE, FILENAME, hosts)
    assert result.success
    assert result.url == signed
    assert hosts == original
    assert SECRET not in repr(result) + str(result) + caplog.text
    assert signed not in repr(result) + str(result) + caplog.text
    assert not caplog.records


@pytest.mark.parametrize('failure', [None, 'http', 'business', 'json',
                                     'structure', 'url', 'options'])
def test_real_registry_fallback_isolated_credentials_and_first_success_stop(
        client, failure):
    """仅替换 HTTP 验证真实站点顺序、凭证隔离与成功即停。"""
    options = {'storage_id': STORAGE_ID}
    first = client.response
    if failure == 'http':
        first.status_code = 500
    elif failure == 'business':
        first.body = json.dumps({'status': 'error', 'message': SECRET})
    elif failure == 'json':
        first.body = 'not-json'
    elif failure == 'structure':
        first.body = json.dumps([])
    elif failure == 'url':
        first.body = json.dumps(payload('invalid'))
    elif failure == 'options':
        options = {'storage_id': 0, 'permission': 0}
    second = Response()
    second.body = json.dumps({'status': True, 'data': {'links': {'url': URL}}})
    client.responses = [second] if failure == 'options' else [first, second]
    result = upload(SECRET, options, [
        {'provider': 'wmimg', 'token': 'FAKE_WMIMG_ONLY'},
        {'provider': 'boltp', 'token': '${UNREAD_TEST_ENV}'},
    ])
    assert result.success
    if failure is None:
        assert result.provider == 'boltp'
        assert result.attempts == ('boltp',)
        assert result.failures == ()
        assert len(client.calls) == 1
        assert second.closed == 0
    else:
        assert result.provider == 'wmimg'
        assert result.attempts == ('boltp', 'wmimg')
        assert len(result.failures) == 1
        endpoint, request = client.calls[-1]
        assert endpoint == 'https://wmimg.com/api/v1/upload'
        assert request['headers']['Authorization'] == 'Bearer FAKE_WMIMG_ONLY'
        assert SECRET not in repr(request)
        assert second.closed == 1
        assert len(client.calls) == (1 if failure == 'options' else 2)
    assert client.closed == client.created == len(client.calls)
    assert first.closed == (failure != 'options')


@pytest.mark.parametrize('expired_at', [None, '2030-01-02 03:04:05'])
def test_prepared_expiration_is_forwarded_only_from_attribute(client, expired_at):
    """真实注册表保留内部期限属性，原生选项仍忽略且源输入不变。"""
    prepared_type = getattr(import_module('modules.image_host.expiration'),
                            'PreparedV2Options', None)
    assert prepared_type is not None
    options = prepared_type({'storage_id': STORAGE_ID}, expired_at=expired_at)
    original = deepcopy(options)
    assert upload(options=options).success
    data = client.calls[0][1]['data']
    assert ('expired_at' in data) is (expired_at is not None)
    if expired_at is not None:
        assert data['expired_at'] == expired_at
    assert options == original
    assert options.expired_at == original.expired_at
    assert len(client.calls) == 1


@pytest.mark.parametrize('expired_at', [
    True, 123, [], {}, '', SECRET, '2030-1-02 03:04:05',
    '2030-01-02T03:04:05', '2030-01-02 03:04:05\n',
    '2030-13-02 03:04:05', '2030-02-30 03:04:05',
])
def test_invalid_prepared_expiration_fails_before_http(client, expired_at):
    """内部期限仍须符合日期契约，无效值不得发送或回显。"""
    prepared_type = getattr(import_module('modules.image_host.expiration'),
                            'PreparedV2Options', None)
    assert prepared_type is not None
    result = upload(options=prepared_type(OPTIONS, expired_at=expired_at))
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert SECRET not in repr(result)
    assert not client.calls
    assert client.created == 0


@pytest.mark.parametrize('body, status, code', [
    ({'status': 'error', 'message': '不存在的储存驱动'}, 200,
     'storage_unavailable'),
    ({'status': 'error', 'message': '普通业务拒绝'}, 200,
     'business_rejected'),
    ({'status': 'error', 'message': '不存在的储存驱动'}, 423, 'http_failed'),
    ({'status': 'success', 'data': {}}, 200, 'invalid_response'),
])
def test_precise_failures_reach_real_registry_fallback(client, body, status, code):
    """合成兼容错误进入真实注册表，本地备用接管且成功即停。"""
    client.response.status_code = status
    client.response.body = json.dumps(body)
    backup = Response()
    backup.body = json.dumps({'status': True, 'data': {'links': {'url': URL}}})
    client.responses = [client.response, backup]
    result = upload(extra_hosts=[{'provider': 'wmimg'},
                                 {'provider': 'boltp', 'options': OPTIONS}])
    assert result.success
    assert result.provider == 'wmimg'
    assert result.attempts == ('boltp', 'wmimg')
    assert result.failures[0].code == code
    assert len(client.calls) == 2
    assert client.calls[0][0] == ENDPOINT
    assert client.closed == client.created == 2
    assert backup.closed == 1
