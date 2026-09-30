#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""依据 BeeIMG.com 官方文档构造合成响应，绝不代表线上上传结果。

来源：https://beeimg.com/api/ 和 https://beeimg.com/faq。
只替换 HTTP 边界，通过真实注册表验证上传和顺序故障转移。
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
SECRET = 'FAKE_BEEIMG_SECRET_7319'
ENV_NAME = 'BEEIMG_LOCAL_TEST_TOKEN'
URL = 'https://beeimg.com/images/synthetic.png?signature=a%2Fb%3D&x=1+2'
ENDPOINT = 'https://beeimg.com/api/upload/file/json/'
WARNING = 'BeeIMG 存在不支持的其他选项，已忽略'


def registry():
    """读取生产静态注册表，不动态安装待测适配器。"""
    return import_module('modules.image_host.registry')


def payload(url=URL):
    """按官方成功示例的 files 容器构造本地合成正文。"""
    return {'files': {'status': 'Success', 'code': '200', 'url': url}}


def upload(token=None, options=None, extra_hosts=()):
    """通过实际入口处理凭证、参数副本及后续站点。"""
    hosts = [{'provider': 'beeimg', 'token': token, 'options': options}]
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

    def json(self):
        """要求在响应作用域内使用 requests 的实际 JSON 解码器。"""
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
    return fake


def test_registration_and_four_argument_signature():
    """仅注册 BeeIMG.com，不把 BeeIMG.cn 当作同一站点。"""
    providers = import_module('modules.image_host.providers')
    adapter = getattr(providers, 'upload_beeimg', None)
    assert callable(adapter)
    assert registry().UPLOADERS == {
        'catbox': providers.upload_catbox,
        'wmimg': providers.upload_wmimg,
        'beeimg': adapter,
        'beeimg_cn': providers.upload_beeimg_cn,
        'boltp': providers.upload_boltp,
        'superbed': providers.upload_superbed,
    }
    assert list(signature(adapter).parameters) == [
        'image_bytes', 'filename', 'token', 'options',
    ]
    assert signature(adapter).return_annotation is str


@pytest.mark.parametrize('token', [None, '', SECRET, '  ' + SECRET + '  '])
def test_exact_multipart_authentication_and_anonymous_contract(client, token):
    """原文件名和内存 PNG 不变，凭证仅进入当前端点的 apikey。"""
    result = upload(token)
    assert result.success
    assert result.url == URL
    data = {'privacy': 'public'}
    if token:
        data['apikey'] = token
    assert client.calls == [(ENDPOINT, {
        'data': data,
        'files': {'file': (FILENAME, IMAGE, 'image/png')},
        'timeout': (5, 15), 'verify': True, 'stream': True,
        'allow_redirects': False,
    })]
    assert client.created == client.closed == client.response.closed == 1
    assert client.response.reads == 1


@pytest.mark.parametrize('resolved', [SECRET, '${NOT_EXPANDED_AGAIN}'])
def test_environment_reference_is_resolved_once(client, monkeypatch, resolved):
    """只读取测试注入的环境引用，不递归展开环境值。"""
    monkeypatch.setenv(ENV_NAME, resolved)
    assert upload('${' + ENV_NAME + '}').success
    assert client.calls[0][1]['data']['apikey'] == resolved


@pytest.mark.parametrize('value', [None, ''])
def test_missing_environment_fails_before_http(client, monkeypatch, value):
    """缺少环境凭证不能悄悄退化为匿名上传。"""
    if value is None:
        monkeypatch.delenv(ENV_NAME, raising=False)
    else:
        monkeypatch.setenv(ENV_NAME, value)
    result = upload('${' + ENV_NAME + '}')
    assert not result.success
    assert result.failures[0].code == 'missing_environment'
    assert client.created == 0


@pytest.mark.parametrize('options', [{}, {'permission': 1}])
def test_default_and_explicit_public_permission(client, options):
    """公开整数转换为 privacy，不直接发送通用 permission。"""
    assert upload(options=options).success
    assert client.calls[0][1]['data'] == {'privacy': 'public'}


@pytest.mark.parametrize('permission', [
    0, True, False, None, -1, 2, '0', '1', 'private', 1.0, [], {},
])
@pytest.mark.parametrize('token', [None, SECRET])
def test_private_and_invalid_permissions_fail_before_request(
        client, permission, token):
    """严格私有无账户保障时安全拒绝，布尔值不得冒充公开整数。"""
    result = upload(token, {'permission': permission})
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert client.created == 0
    assert client.calls == []


@pytest.mark.parametrize('privacy', ['private', 'truly-private', 'public',
                                     None, False, 0])
def test_native_privacy_cannot_silently_override_permission(client, privacy):
    """未经支持的原生安全参数不能被忽略后执行公开上传。"""
    result = upload(SECRET, {'privacy': privacy})
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert client.created == 0


@pytest.mark.parametrize('albumid', ['abc12', 'abcdefghi', '00001'])
def test_native_album_string_is_preserved(client, albumid):
    """相册和文件夹字符串原样发送，不能转换为 WMIMG 整数 ID。"""
    options = {'albumid': albumid}
    original = deepcopy(options)
    assert upload(SECRET, options).success
    assert client.calls[0][1]['data'] == {
        'privacy': 'public', 'apikey': SECRET, 'albumid': albumid,
    }
    assert options == original


@pytest.mark.parametrize('albumid', [None, True, False, 12345, 1.0, [], {},
                                   '', 'abc', 'abcdef', 'abcdefghij'])
def test_album_contract_rejects_invalid_explicit_values(client, albumid):
    """只接受文档规定的五位或九位字符串，非法值不发请求。"""
    result = upload(SECRET, {'albumid': albumid})
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert client.created == 0


@pytest.mark.parametrize('token', [None, ''])
def test_album_requires_account_credential(client, token):
    """官方相册上传要求同时提供 API key，匿名不忽略相册意图。"""
    result = upload(token, {'albumid': 'abc12'})
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert client.created == 0


def test_unsupported_nonsafety_options_have_fixed_warning(client, caplog):
    """不透传其他站点选项或任意参数，不记录不可信键和值。"""
    options = {'album_id': 123, 'strategy_id': 456, 'apikey': SECRET,
               SECRET + '\nforged warning': {'url': URL}}
    original = deepcopy(options)
    with caplog.at_level(logging.WARNING):
        result = upload(options=options)
    assert result.success
    assert options == original
    assert client.calls[0][1]['data'] == {'privacy': 'public'}
    assert [(record.levelno, record.getMessage()) for record in caplog.records
            ] == [(logging.WARNING, WARNING)]
    assert SECRET not in repr(result) + caplog.text
    assert URL not in repr(result) + caplog.text
    assert 'forged warning' not in caplog.text


@pytest.mark.parametrize('status', [None, False, True, 0, 1, [], {},
                                   'success', 'Duplicate', SECRET])
def test_only_exact_official_success_status_is_accepted(client, caplog, status):
    """有效 URL 和 HTTP 200 不足以成功，不推测重复图片成功语义。"""
    data = payload()
    data['files'].update(status=status, message=SECRET)
    client.response.body = json.dumps(data)
    result = upload(SECRET)
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert result.failures[0].message == '图床上传失败'
    assert client.closed == client.response.closed == 1
    assert SECRET not in repr(result) + caplog.text


@pytest.mark.parametrize('data', [
    None, [], True, 1, 'Success', {}, {'files': None}, {'files': []},
    {'files': 'Success'}, {'files': {}}, {'files': {'url': URL}},
    {'image': {'status': 'Success', 'url': URL}},
    {'status': 'Success', 'url': URL},
    {'files': {'status': 'Please come back with a URL Thank you :)',
               'code': '0'}},
])
def test_invalid_containers_and_official_error_shape_fail(client, data):
    """仅采用官方 files 映射，不接受旧讨论中的 image.url。"""
    client.response.body = json.dumps(data)
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert client.closed == client.response.closed == 1


@pytest.mark.parametrize('url', [None, True, 123, [], {}, '',
                               'ftp://example.com/a', '//example.com/a',
                               'https://user:' + SECRET + '@example.com/a',
                               'https://example.com:' + SECRET])
def test_invalid_urls_use_core_error_code(client, caplog, url):
    """直链经过核心校验，固定错误码不暴露响应或凭证。"""
    client.response.body = json.dumps(payload(url))
    result = upload(SECRET)
    assert not result.success
    assert result.failures[0].code == 'invalid_url'
    assert client.closed == client.response.closed == 1
    assert SECRET not in repr(result) + caplog.text


def test_missing_direct_url_never_uses_thumbnail_or_view_url(client):
    """成功状态仍必须有直链，不从其他链接字段猜测。"""
    client.response.body = json.dumps({'files': {
        'status': 'Success', 'thumbnail_url': URL, 'view_url': URL,
    }})
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'invalid_url'
    assert client.closed == client.response.closed == 1


@pytest.mark.parametrize('body', ['', '<html>' + SECRET + '</html>',
                                  '{"files":', 'not-json-' + SECRET])
def test_malformed_json_is_safe_and_resources_close(client, caplog, body):
    """实际 JSON 解码失败不泄露正文并释放所有已取得资源。"""
    client.response.body = body
    result = upload(SECRET)
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert client.closed == client.response.closed == 1
    assert SECRET not in repr(result) + caplog.text
    assert not caplog.records


@pytest.mark.parametrize('status', [199, 300, 301, 302, 303, 307, 308,
                                   401, 429, 500])
def test_non_2xx_never_parses_body_or_follows_redirects(client, caplog, status):
    """HTTP 错误与重定向直接失败且无第二次请求。"""
    client.response.status_code = status
    client.response.body = SECRET
    result = upload(SECRET)
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
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
    result = upload(SECRET)
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert result.failures[0].message == '图床上传失败'
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
        upload(extra_hosts=[{'provider': 'beeimg'}])
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
    assert result.failures[0].code == 'upload_failed'
    providers = import_module('modules.image_host.providers')
    error_class = import_module('modules.image_host.core').ImageHostError
    with pytest.raises(error_class) as caught:
        providers.upload_beeimg(IMAGE, FILENAME, SECRET, {})
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
    hosts = [{'provider': ' BEEIMG ', 'token': SECRET,
              'options': {'permission': 1, 'albumid': 'abc12'}}]
    original = deepcopy(hosts)
    result = registry().upload_with_fallback(IMAGE, FILENAME, hosts)
    assert result.success
    assert result.url == signed
    assert hosts == original
    assert SECRET not in repr(result) + str(result) + caplog.text
    assert signed not in repr(result) + str(result) + caplog.text
    assert not caplog.records


@pytest.mark.parametrize('failure', [None, 'http', 'business', 'json',
                                     'url', 'options'])
def test_real_registry_fallback_isolated_credentials_and_first_success_stop(
        client, failure):
    """仅替换 HTTP 验证真实站点顺序、凭证隔离与成功即停。"""
    first = client.response
    options = {}
    if failure == 'http':
        first.status_code = 500
    elif failure == 'business':
        first.body = json.dumps({'files': {'status': SECRET}})
    elif failure == 'json':
        first.body = 'not-json'
    elif failure == 'url':
        first.body = json.dumps(payload('invalid'))
    elif failure == 'options':
        options = {'permission': 0}
    second = Response()
    second.body = json.dumps({'status': True, 'data': {'links': {'url': URL}}})
    client.responses = [second] if failure == 'options' else [first, second]
    result = upload(SECRET, options, [
        {'provider': 'wmimg', 'token': 'FAKE_WMIMG_ONLY'},
        {'provider': 'beeimg', 'token': '${UNREAD_TEST_ENV}'},
    ])
    assert result.success
    if failure is None:
        assert result.provider == 'beeimg'
        assert result.attempts == ('beeimg',)
        assert result.failures == ()
        assert len(client.calls) == 1
        assert second.closed == 0
    else:
        assert result.provider == 'wmimg'
        assert result.attempts == ('beeimg', 'wmimg')
        assert len(result.failures) == 1
        endpoint, request = client.calls[-1]
        assert endpoint == 'https://wmimg.com/api/v1/upload'
        assert request['headers']['Authorization'] == 'Bearer FAKE_WMIMG_ONLY'
        assert SECRET not in repr(request)
        assert second.closed == 1
        assert len(client.calls) == (1 if failure == 'options' else 2)
    assert client.closed == client.created == len(client.calls)
    assert first.closed == (failure != 'options')


def test_distinct_unverified_site_remains_unregistered(client):
    """未核验且不实现的站点不注册假支持，也不向其他域名发送请求。"""
    result = registry().upload_with_fallback(
        IMAGE, FILENAME, [{'provider': 'smms', 'token': SECRET}])
    assert not result.success
    assert result.failures[0].code == 'unknown_provider'
    assert client.created == 0
