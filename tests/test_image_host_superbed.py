#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证 Superbed 客户端上传契约，不访问真实上传接口。

官方来源：https://www.superbed.cn/help 的 PicGo 和 ShareX 示例。
仅确认表单 token、可选字符串 categories、file 和根级 url。
所有响应均为本地合成夹具；错误标记是防御性案例，不是官方响应示例。
"""

from copy import deepcopy
from importlib import import_module
from inspect import signature
import json
import logging
import traceback
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests


IMAGE = b'\x89PNG\r\n\x1a\nlocal-synthetic-image'
FILENAME = '本地截图.png'
SECRET = 'FAKE_SUPERBED_SECRET_7319'
ENV_NAME = 'SUPERBED_LOCAL_TEST_TOKEN'
URL = 'https://cdn.example.com/image?signature=a%2Fb%3D&x=1+2'
ENDPOINT = 'https://api.superbed.cn/upload'
WARNING = 'Superbed 存在不支持的其他选项，已忽略'


def registry():
    """读取实际静态注册表，不动态安装待测适配器。"""
    return import_module('modules.image_host.registry')


def upload(token=SECRET, options=None, extra_hosts=()):
    """通过真实入口处理凭证、参数副本和顺序故障转移。"""
    hosts = [{'provider': 'superbed', 'token': token, 'options': options}]
    return registry().upload_with_fallback(
        IMAGE, FILENAME, hosts + list(extra_hosts))


@pytest.fixture(autouse=True)
def client(monkeypatch):
    """以小型上下文替身替换 HTTP，并阻断意外真实请求。"""
    session = MagicMock()
    response = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.status_code = 200
    state = SimpleNamespace(
        session=session, response=response, body=json.dumps({'url': URL}))

    def decode():
        """在响应与会话作用域内执行真实 JSON 解码。"""
        assert response.__enter__.call_count == response.__exit__.call_count + 1
        assert session.__enter__.call_count == session.__exit__.call_count + 1
        actual = requests.Response()
        actual._content = state.body.encode('utf-8')
        actual.encoding = 'utf-8'
        return actual.json()

    def post(*args, **kwargs):
        """验证上次响应已关闭且当前会话有效，不提供其他网络方法。"""
        assert session.__enter__.call_count == session.__exit__.call_count + 1
        assert response.__enter__.call_count == response.__exit__.call_count
        return response

    def close_session(*args):
        """验证响应在会话退出前已经释放。"""
        assert response.__enter__.call_count == response.__exit__.call_count
        return False

    def forbidden(*args, **kwargs):
        """独立阻断任何逃逸出替身的真实网络访问。"""
        raise AssertionError('测试禁止访问真实网络')

    response.json.side_effect = decode
    session.post.side_effect = post
    session.__exit__.side_effect = close_session
    state.create = MagicMock(return_value=session)
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    monkeypatch.setattr(requests, 'Session', state.create)
    return state


def test_registration_and_signature():
    """静态注册仅增加真实 Superbed 四参数适配器。"""
    providers = import_module('modules.image_host.providers')
    adapter = getattr(providers, 'upload_superbed', None)
    assert callable(adapter)
    assert registry().UPLOADERS == {
        'catbox': providers.upload_catbox,
        'wmimg': providers.upload_wmimg,
        'beeimg': providers.upload_beeimg,
        'beeimg_cn': providers.upload_beeimg_cn,
        'superbed': adapter,
    }
    assert list(signature(adapter).parameters) == [
        'image_bytes', 'filename', 'token', 'options',
    ]
    assert signature(adapter).return_annotation is str


@pytest.mark.parametrize('token', [SECRET, '  ' + SECRET + '  '])
@pytest.mark.parametrize('options', [None, {}, {'permission': 1}])
def test_exact_client_upload_contract(client, token, options):
    """只发送客户端表单 token，不混发通用 API key 或权限字段。"""
    result = upload(token, options)
    assert result.success
    assert result.url == URL
    client.session.post.assert_called_once_with(
        ENDPOINT, data={'token': token},
        files={'file': (FILENAME, IMAGE, 'image/png')},
        timeout=(5, 15), verify=True, allow_redirects=False, stream=True)
    assert client.create.call_count == 1
    assert client.session.__exit__.call_count == 1
    assert client.response.__exit__.call_count == 1
    client.response.json.assert_called_once_with()


@pytest.mark.parametrize('token', [None, ''])
def test_empty_credential_rejected_before_session(client, token):
    """官方客户端要求填写 token，不推测匿名支持。"""
    result = upload(token)
    assert not result.success
    assert result.failures[0].code == 'invalid_token'
    client.create.assert_not_called()


@pytest.mark.parametrize('resolved', [SECRET, '${NOT_EXPANDED_AGAIN}'])
def test_environment_resolved_once(client, monkeypatch, resolved):
    """仅解析测试注入的环境引用一次，解析值原样进入表单。"""
    monkeypatch.setenv(ENV_NAME, resolved)
    assert upload('${' + ENV_NAME + '}').success
    assert client.session.post.call_args.kwargs['data'] == {'token': resolved}


@pytest.mark.parametrize('value', [None, ''])
def test_missing_environment_rejected(client, monkeypatch, value):
    """缺失或空环境值在注册表安全失败，不退化为空凭证请求。"""
    if value is None:
        monkeypatch.delenv(ENV_NAME, raising=False)
    else:
        monkeypatch.setenv(ENV_NAME, value)
    result = upload('${' + ENV_NAME + '}')
    assert not result.success
    assert result.failures[0].code == 'missing_environment'
    client.create.assert_not_called()


@pytest.mark.parametrize('categories', ['', '截图相册', '  原样相册  ', '123'])
def test_categories_string_preserved(client, categories):
    """相册名称字符串原样传递，官方允许空字符串，不猜相册 ID。"""
    options = {'categories': categories}
    original = deepcopy(options)
    assert upload(options=options).success
    assert client.session.post.call_args.kwargs['data'] == {
        'token': SECRET, 'categories': categories,
    }
    assert options == original


@pytest.mark.parametrize('categories', [None, False, True, 0, 123, 1.0, [], {}])
def test_invalid_categories_type_rejected(client, categories):
    """不把相册数字、容器或空值自动转成名称。"""
    result = upload(options={'categories': categories})
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    client.create.assert_not_called()


@pytest.mark.parametrize('permission', [
    0, True, False, None, -1, 2, '0', '1', 'private', 1.0, [], {},
])
def test_private_and_invalid_permission_rejected(client, permission):
    """私有能力未经核验，显式配置不得被降级为公开。"""
    result = upload(options={'permission': permission})
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    client.create.assert_not_called()


@pytest.mark.parametrize('privacy', ['private', 'public', None])
def test_unsupported_privacy_cannot_be_ignored(client, privacy):
    """安全相关字段不作为普通未知参数忽略后上传。"""
    result = upload(options={'privacy': privacy})
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    client.create.assert_not_called()


def test_unknown_nonsafety_options_emit_only_fixed_warning(client, caplog):
    """不发送未知字段，也不记录未知键、值或完整配置。"""
    options = {'album_id': 123, 'strategy_id': 456, 'token': SECRET,
               SECRET + '\nforged warning': {'url': URL}}
    original = deepcopy(options)
    with caplog.at_level(logging.WARNING):
        result = upload(options=options)
    assert result.success
    assert options == original
    assert client.session.post.call_args.kwargs['data'] == {'token': SECRET}
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.WARNING, WARNING),
    ]
    assert SECRET not in caplog.text + repr(result)
    assert URL not in caplog.text + repr(result)
    assert 'forged warning' not in caplog.text


@pytest.mark.parametrize('extra', [{}, {'message': 'local information'}])
def test_root_url_needs_no_invented_success_fields(client, extra):
    """官方只指定根级 url，不强求其他产品的成功标记。"""
    client.body = json.dumps({'url': URL, **extra})
    result = upload()
    assert result.success
    assert result.url == URL


@pytest.mark.parametrize('marker', [
    {'success': False}, {'status': False}, {'error': SECRET},
    {'error': True}, {'error': {'message': SECRET}},
])
def test_explicit_failure_marker_rejected(client, caplog, marker):
    """本地防御性夹具：明确失败不能因同时有 url 而变为成功。"""
    client.body = json.dumps({'url': URL, 'message': SECRET, **marker})
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert SECRET not in caplog.text + repr(result)
    assert client.response.__exit__.call_count == 1


@pytest.mark.parametrize('body', [None, [], True, 1, 'url'])
def test_invalid_json_structure_rejected(client, body):
    """JSON 顶层必须是映射，不能猜测其他结构中的链接。"""
    client.body = json.dumps(body)
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert client.response.__exit__.call_count == 1


@pytest.mark.parametrize('body', [
    {}, {'data': {'url': URL}}, {'view_url': URL}, {'thumbnail_url': URL},
    {'files': {'status': 'Success', 'url': URL}},
])
def test_missing_root_url_never_uses_other_links(client, body):
    """不套用其他产品容器，也不用分享页或缩略图代替直链。"""
    client.body = json.dumps(body)
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'invalid_url'


@pytest.mark.parametrize('url', [
    None, True, 123, [], {}, '', 'ftp://example.com/a', '//example.com/a',
    'https://user:' + SECRET + '@example.com/a',
    'https://example.com:' + SECRET,
])
def test_invalid_url_uses_core_safe_error(client, caplog, url):
    """根级候选直链必须经过核心校验，失败不能暴露原文。"""
    client.body = json.dumps({'url': url})
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'invalid_url'
    assert SECRET not in caplog.text + repr(result)
    assert client.response.__exit__.call_count == 1


@pytest.mark.parametrize('body', ['', '<html>' + SECRET + '</html>',
                                  '{"url":', 'not-json-' + SECRET])
def test_bad_json_releases_resources(client, caplog, body):
    """使用真实解码器验证 HTML 和畸形 JSON 的安全失败及清理。"""
    client.body = body
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert client.response.__exit__.call_count == 1
    assert client.session.__exit__.call_count == 1
    assert SECRET not in repr(result) + caplog.text
    assert not caplog.records


@pytest.mark.parametrize('status', [199, 300, 301, 302, 303, 307, 308,
                                   401, 429, 500])
def test_non_2xx_fails_without_read_or_redirect(client, status):
    """非成功 HTTP 不解析正文，不重定向且不重复请求。"""
    client.response.status_code = status
    client.body = SECRET
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    client.response.json.assert_not_called()
    assert client.session.post.call_count == 1
    assert client.response.__exit__.call_count == 1
    assert client.session.__exit__.call_count == 1


@pytest.mark.parametrize('error_type', [
    requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectionError, requests.exceptions.SSLError,
    requests.exceptions.ContentDecodingError, ValueError, RuntimeError,
])
@pytest.mark.parametrize('stage', ['post', 'json'])
def test_exception_sanitization_and_owned_resource_cleanup(
        client, caplog, error_type, stage):
    """请求、读取和解压错误只生成固定错误，不保留敏感异常链。"""
    target = client.session.post if stage == 'post' else client.response.json
    target.side_effect = error_type(SECRET + URL)
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert client.session.post.call_count == 1
    assert client.session.__exit__.call_count == 1
    assert client.response.__exit__.call_count == (stage == 'json')
    providers = import_module('modules.image_host.providers')
    error_class = import_module('modules.image_host.core').ImageHostError
    with pytest.raises(error_class) as caught:
        providers.upload_superbed(IMAGE, FILENAME, SECRET, {})
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    diagnostic = (''.join(traceback.format_exception(caught.value))
                  + repr(caught.value) + repr(result) + caplog.text)
    assert SECRET not in diagnostic
    assert URL not in diagnostic
    assert not caplog.records
    assert client.session.__exit__.call_count == 2
    assert client.response.__exit__.call_count == 2 * (stage == 'json')


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('stage', ['post', 'json'])
def test_control_signal_identity_cleanup_and_no_fallback(
        client, signal_type, stage):
    """控制信号原对象传播，释放资源但不执行后续图床。"""
    signal = signal_type('local stop')
    target = client.session.post if stage == 'post' else client.response.json
    target.side_effect = signal
    with pytest.raises(signal_type) as caught:
        upload(extra_hosts=[{'provider': 'wmimg'}])
    assert caught.value is signal
    assert client.session.post.call_count == 1
    assert client.session.__exit__.call_count == 1
    assert client.response.__exit__.call_count == (stage == 'json')


def test_signed_url_and_inputs_preserved(client, caplog):
    """业务值保留完整签名，原配置不变且诊断不输出链接。"""
    signed = URL + '&token=' + SECRET
    client.body = json.dumps({'url': signed})
    hosts = [{'provider': ' SUPERBED ', 'token': SECRET,
              'options': {'permission': 1, 'categories': '本地相册'}}]
    original = deepcopy(hosts)
    result = registry().upload_with_fallback(IMAGE, FILENAME, hosts)
    assert result.success
    assert result.url == signed
    assert hosts == original
    assert SECRET not in repr(result) + str(result) + caplog.text
    assert signed not in repr(result) + str(result) + caplog.text
    assert not caplog.records


@pytest.mark.parametrize('failure', [None, 'http', 'business', 'json',
                                     'structure', 'url', 'options', 'token'])
def test_serial_fallback_credentials_and_first_success_stop(client, failure):
    """通过真实两个适配器验证顺序、凭证隔离和首成功停止。"""
    options = {'permission': 0} if failure == 'options' else {}
    token = '' if failure == 'token' else SECRET
    first_body = {'url': URL}
    if failure == 'business':
        first_body = {'error': SECRET}
    elif failure == 'structure':
        first_body = []
    elif failure == 'url':
        first_body = {'url': 'invalid'}
    bodies = [first_body, {'status': True, 'data': {'links': {'url': URL}}}]
    skipped = failure in ('options', 'token')
    if skipped:
        bodies.pop(0)
    calls = []

    def post(endpoint, **kwargs):
        """下一站开始前必须释放上一站的响应和会话。"""
        assert client.session.__exit__.call_count == len(calls)
        assert client.response.__exit__.call_count == len(calls)
        calls.append((endpoint, kwargs))
        first = len(calls) == 1 and not skipped
        client.response.status_code = (
            500 if first and failure == 'http' else 200)
        body = bodies.pop(0)
        client.body = (
            'not-json' if first and failure == 'json' else json.dumps(body))
        return client.response

    client.session.post.side_effect = post
    result = upload(token, options, [
        {'provider': 'wmimg', 'token': 'FAKE_WMIMG_ONLY'},
        {'provider': 'superbed', 'token': '${UNREAD_TEST_ENV}'},
    ])
    assert result.success
    if failure is None:
        assert result.provider == 'superbed'
        assert result.attempts == ('superbed',)
        assert result.failures == ()
        assert len(calls) == 1
    else:
        assert result.provider == 'wmimg'
        assert result.attempts == ('superbed', 'wmimg')
        assert len(result.failures) == 1
        endpoint, request = calls[-1]
        assert endpoint == 'https://wmimg.com/api/v1/upload'
        assert request['headers']['Authorization'] == 'Bearer FAKE_WMIMG_ONLY'
        assert SECRET not in repr(request)
        assert len(calls) == (1 if skipped else 2)
    assert client.session.__exit__.call_count == len(calls)
    assert client.response.__exit__.call_count == len(calls)
