#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证 v2 上传适配器的显式存储编号契约。"""

from email.parser import BytesParser
from email.policy import default
from importlib import import_module
import json

import pytest
import requests


URL = 'https://images.example/synthetic.png'
REJECTION = '不存在的储存驱动'


def _install(monkeypatch, upload_replies, group_ids=(2, 3)):
    """安装按方法路由的传输替身，返回按发生顺序记录的全部请求。"""
    sent = []
    queue = list(upload_replies)

    def send(adapter, request, **kwargs):
        """GET 返回合成组信息，POST 按队列返回合成正文。"""
        sent.append(request)
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        if request.method == 'GET':
            response._content = json.dumps({'status': 'success', 'data': {
                'group': {'options': {'file_expire_seconds': None}},
                'storages': [{'id': value} for value in group_ids]}}).encode()
        else:
            response._content = queue.pop(0)
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    return sent


def _storage_id(request):
    """从 multipart 请求体读取真实发送的存储编号。"""
    content_type = request.headers['Content-Type']
    message = BytesParser(policy=default).parsebytes(
        f'Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n'.encode()
        + request.body)
    part = next(
        item for item in message.iter_parts()
        if item.get_param('name', header='content-disposition') == 'storage_id')
    return int(part.get_payload(decode=True))


@pytest.fixture(autouse=True)
def isolate_cache(monkeypatch, tmp_path):
    """隔离默认缓存目录，避免读取本机遗留的存储记录。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))


@pytest.fixture
def upload_adapter(monkeypatch):
    """替换 HTTP 边界，记录单次上传参数并返回合成响应。"""
    providers = import_module('modules.image_host.providers')
    calls = []

    def request_json(provider, stage, token, *, data=None, files=None):
        calls.append((provider, stage, token, data, files))
        return {'status': 'success', 'data': {'public_url': URL}}

    monkeypatch.setattr(providers, 'request_json', request_json)
    return providers, calls


def test_boltp_storage_zero_is_rejected_before_http(upload_adapter, monkeypatch):
    """Boltp 直接适配器不再接受零值编号，且零请求。"""
    from modules.image_host.core import ImageHostError

    providers, _ = upload_adapter
    calls = []

    def send(adapter, request, **kwargs):
        calls.append(request)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)

    with pytest.raises(ImageHostError) as caught:
        providers.upload_boltp(b'image', 'image.png', '', {'storage_id': 0})

    assert caught.value.code == 'invalid_options'
    assert calls == []


def test_context_retention_caches_success_and_none_by_hmac_identity(monkeypatch):
    from modules.image_host import context

    calls = []
    monkeypatch.setattr(context.storage, 'fetch_boltp_retention',
                        lambda token: calls.append(token) or None)
    ctx = context.UploadContext()

    assert ctx.get_boltp_retention('secret-a') is None
    assert ctx.get_boltp_retention('secret-a') is None
    assert ctx.get_boltp_retention('secret-b') is None
    assert ctx.get_boltp_retention('') is None
    assert calls == ['secret-a', 'secret-b', '']
    ctx.close()


def test_context_retention_failure_is_safe_cached_snapshot(monkeypatch):
    from modules.image_host import context
    from modules.image_host.core import ImageHostError

    token = 'RETENTION_SECRET_7319'
    calls = []
    original = ValueError(token)

    def fail(value):
        calls.append(value)
        raise original

    monkeypatch.setattr(context.storage, 'fetch_boltp_retention', fail)
    ctx = context.UploadContext()
    errors = []
    for _ in range(2):
        with pytest.raises(ImageHostError) as caught:
            ctx.get_boltp_retention(token)
        errors.append(caught.value)

    assert calls == [token]
    assert errors[0] is not errors[1]
    assert token not in str(errors[0]) + repr(errors[0].args)
    assert errors[0].__cause__ is None
    assert errors[0].__context__ is None
    ctx.close()


def test_context_retention_close_clears_cache_and_closed_rejects(monkeypatch):
    from modules.image_host import context
    from modules.image_host.core import ImageHostError

    calls = []
    monkeypatch.setattr(context.storage, 'fetch_boltp_retention',
                        lambda token: calls.append(token) or 60)
    ctx = context.UploadContext()
    assert ctx.get_boltp_retention('secret') == 60
    ctx.close()
    assert ctx._retentions == {}
    with pytest.raises(ImageHostError):
        ctx.get_boltp_retention('secret')

    fresh = context.UploadContext()
    assert fresh.get_boltp_retention('secret') == 60
    assert calls == ['secret', 'secret']
    fresh.close()


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_context_retention_control_signal_propagates_without_caching(
        monkeypatch, signal_type):
    from modules.image_host import context, storage

    signal = signal_type()
    calls = []

    def fail(token):
        calls.append(token)
        raise signal

    monkeypatch.setattr(context.storage, 'fetch_boltp_retention', fail)
    ctx = context.UploadContext()
    for _ in range(2):
        with pytest.raises(signal_type) as caught:
            ctx.get_boltp_retention('secret')
        assert caught.value is signal
    assert calls == ['secret', 'secret']
    ctx.close()


def test_boltp_fetch_retention_only_requests_group(monkeypatch):
    from modules.image_host import storage, v2_http

    calls = []
    token = 'RETENTION_SECRET_7319'

    def request_json(provider, stage, request_token):
        calls.append((provider, stage, request_token))
        return {'data': {'group': {'options': {'file_expire_seconds': 0}}}}

    monkeypatch.setattr(v2_http, 'request_json', request_json)

    assert storage.fetch_boltp_retention(token) == 0
    assert calls == [('boltp', 'group', token)]


def test_boltp_fetch_retention_maps_malformed_data_without_token(monkeypatch):
    from modules.image_host import storage, v2_http
    from modules.image_host.core import ImageHostError

    token = 'RETENTION_SECRET_7319'
    monkeypatch.setattr(
        v2_http, 'request_json',
        lambda provider, stage, request_token: {
            'data': {'group': {'options': {'file_expire_seconds': True}}}})

    with pytest.raises(ImageHostError) as caught:
        storage.fetch_boltp_retention(token)

    assert caught.value.code == 'storage_lookup_failed'
    assert caught.value.stage == 'group'
    assert token not in str(caught.value) + repr(caught.value.args)


@pytest.mark.parametrize('retention', [None, 0, 86400])
def test_boltp_fetch_retention_accepts_optional_nonnegative_integer(
        monkeypatch, retention):
    from modules.image_host import storage, v2_http

    monkeypatch.setattr(
        v2_http, 'request_json',
        lambda provider, stage, token: {
            'data': {'group': {'options': {'file_expire_seconds': retention}}}})

    assert storage.fetch_boltp_retention('') == retention


@pytest.mark.parametrize(('scenario', 'code', 'status'), [
    ('forbidden', 'http_failed', 403),
    ('server_error', 'http_failed', 500),
    ('business_rejected', 'business_rejected', 200),
    ('invalid_json', 'invalid_response', 200),
    ('timeout', 'transport_failed', None),
])
def test_boltp_fetch_retention_classifies_real_http_transport(
        monkeypatch, scenario, code, status):
    from modules.image_host import storage, v2_http
    from modules.image_host.core import ImageHostError

    sent = []

    def send(adapter, request, **kwargs):
        sent.append(request)
        if scenario == 'timeout':
            raise requests.exceptions.Timeout
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = status
        response._content = {
            'business_rejected': b'{"status":"error","message":"denied"}',
            'invalid_json': b'{',
        }.get(scenario, b'')
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)

    with pytest.raises(ImageHostError) as caught:
        storage.fetch_boltp_retention('')

    assert len(sent) == 1
    assert sent[0].url == 'https://www.boltp.com/api/v2/group'
    assert sent[0].method == 'GET'
    assert caught.value.code == code
    assert caught.value.http_status == status
    assert caught.value.stage == 'group'


@pytest.mark.parametrize('payload', [
    {'data': {'group': {'options': {'file_expire_seconds': 60}}}},
    {'data': {'storages': 'malformed',
              'group': {'options': {'file_expire_seconds': 60}}}},
])
def test_boltp_fetch_retention_ignores_storages_field(monkeypatch, payload):
    from modules.image_host import storage, v2_http

    monkeypatch.setattr(v2_http, 'request_json', lambda *args: payload)

    assert storage.fetch_boltp_retention('') == 60


@pytest.mark.parametrize('payload', [
    {}, {'data': None}, {'data': {'group': []}},
    {'data': {'group': {'options': []}}},
])
def test_boltp_fetch_retention_rejects_malformed_structure(
        monkeypatch, payload):
    from modules.image_host import storage, v2_http
    from modules.image_host.core import ImageHostError

    monkeypatch.setattr(v2_http, 'request_json', lambda *args: payload)

    with pytest.raises(ImageHostError) as caught:
        storage.fetch_boltp_retention('')

    assert (caught.value.code, caught.value.stage) == (
        'storage_lookup_failed', 'group')


@pytest.mark.parametrize('retention', [-1, 1.5, '60', True, [], {}])
def test_boltp_fetch_retention_rejects_invalid_expiration_type(
        monkeypatch, retention):
    from modules.image_host import storage, v2_http
    from modules.image_host.core import ImageHostError

    monkeypatch.setattr(
        v2_http, 'request_json',
        lambda *args: {'data': {'group': {'options': {
            'file_expire_seconds': retention}}}})

    with pytest.raises(ImageHostError) as caught:
        storage.fetch_boltp_retention('')

    assert (caught.value.code, caught.value.stage) == (
        'storage_lookup_failed', 'group')


@pytest.mark.parametrize(('failure_code', 'code', 'status'), [
    ('forbidden', 'http_failed', 403),
    ('server_error', 'http_failed', 500),
    ('invalid_json', 'invalid_response', None),
    ('timeout', 'transport_failed', None),
])
def test_boltp_fetch_retention_preserves_http_classification_and_chain(
        monkeypatch, failure_code, code, status):
    from modules.image_host import storage, v2_http
    from modules.image_host.core import ImageHostError

    def fail(*args):
        if failure_code == 'forbidden':
            raise ImageHostError(
                'http_failed', '', stage='group', http_status=403)
        if failure_code == 'server_error':
            raise ImageHostError(
                'http_failed', '', stage='group', http_status=500)
        if failure_code == 'invalid_json':
            raise ImageHostError('invalid_response', '', stage='group')
        raise ImageHostError('transport_failed', '', stage='group')

    monkeypatch.setattr(v2_http, 'request_json', fail)
    with pytest.raises(ImageHostError) as caught:
        storage.fetch_boltp_retention('')

    assert caught.value.code == code
    assert caught.value.http_status == status
    assert caught.value.__cause__ is None


def test_boltp_fetch_retention_preserves_exact_business_rejection(monkeypatch):
    from modules.image_host import storage, v2_http
    from modules.image_host.core import ImageHostError

    rejection = ImageHostError('business_rejected', '', stage='group')
    monkeypatch.setattr(v2_http, 'request_json', lambda *args: (_ for _ in ()).throw(rejection))

    with pytest.raises(ImageHostError) as caught:
        storage.fetch_boltp_retention('')

    assert caught.value is rejection
    assert caught.value.code == 'business_rejected'
    assert caught.value.__cause__ is None


def test_boltp_fetch_retention_maps_unclassified_error_with_cause(monkeypatch):
    from modules.image_host import storage, v2_http
    from modules.image_host.core import ImageHostError

    monkeypatch.setattr(
        v2_http, 'request_json',
        lambda *args: (_ for _ in ()).throw(ValueError('malformed payload')))

    with pytest.raises(ImageHostError) as caught:
        storage.fetch_boltp_retention('')

    assert caught.value.code == 'storage_lookup_failed'
    assert caught.value.stage == 'group'
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_boltp_fetch_retention_does_not_expose_token(monkeypatch):
    from modules.image_host import storage, v2_http
    from modules.image_host.core import ImageHostError

    token = 'RETENTION_SECRET_7319'
    monkeypatch.setattr(
        v2_http, 'request_json',
        lambda *args: {'data': {'group': {'options': {
            'file_expire_seconds': True}}}})

    with pytest.raises(ImageHostError) as caught:
        storage.fetch_boltp_retention(token)

    assert caught.value.stage == 'group'
    assert token not in str(caught.value) + repr(caught.value.args)


def test_beeimg_cn_storage_zero_is_rejected_before_http(upload_adapter, monkeypatch):
    from modules.image_host.core import ImageHostError

    providers, _ = upload_adapter
    calls = []

    def send(adapter, request, **kwargs):
        calls.append(request)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)

    with pytest.raises(ImageHostError) as caught:
        providers.upload_beeimg_cn(b'image', 'image.png', '', {'storage_id': 0})

    assert caught.value.code == 'invalid_options'
    assert calls == []


def test_boltp_precise_rejection_recovers_from_group(monkeypatch):
    """手填编号被精确拒绝后按组顺序改选下一项，只发两次 POST。"""
    from modules.image_host.registry import upload_with_fallback

    rejection = json.dumps(
        {'status': 'error', 'message': REJECTION}).encode()
    success = json.dumps(
        {'status': 'success', 'data': {'public_url': URL}}).encode()
    sent = _install(monkeypatch, [rejection, success], group_ids=(2, 3))
    result = upload_with_fallback(
        b'image', 'image.png', [{'provider': 'boltp', 'token': '',
                                'options': {'storage_id': 2}}])

    assert result.success
    assert result.url == URL
    assert [request.method for request in sent] == ['POST', 'GET', 'POST']
    assert [_storage_id(request) for request in (sent[0], sent[2])] == [2, 3]
    assert result.attempts == ('boltp',)


def test_boltp_invalid_storage_id_uses_group_selection(monkeypatch):
    """非法手填不再提前失败：先查组再上传组列表首项。"""
    from modules.image_host.registry import upload_with_fallback

    success = json.dumps(
        {'status': 'success', 'data': {'public_url': URL}}).encode()
    sent = _install(monkeypatch, [success], group_ids=(2, 3))
    result = upload_with_fallback(
        b'image', 'image.png', [{'provider': 'boltp', 'token': '',
                                'options': {'storage_id': True}}])

    assert result.success
    assert [request.method for request in sent] == ['GET', 'POST']
    assert _storage_id(sent[1]) == 2


def test_boltp_exhausted_group_falls_back_to_next_host(monkeypatch):
    """恢复后组内无其他候选时结束本站，按原规则进入下一槽位。"""
    from modules.image_host.registry import upload_with_fallback

    rejection = json.dumps(
        {'status': 'error', 'message': REJECTION}).encode()
    success = json.dumps(
        {'status': 'success', 'data': {'public_url': URL}}).encode()
    sent = _install(monkeypatch, [rejection, success], group_ids=(2,))
    result = upload_with_fallback(
        b'image', 'image.png', [
            {'provider': 'boltp', 'token': '', 'options': {'storage_id': 2}},
            {'provider': 'boltp', 'token': '', 'options': {'storage_id': 3}},
        ])

    assert result.success
    assert result.attempts == ('boltp', 'boltp')
    assert [request.method for request in sent] == ['POST', 'GET', 'POST']
    assert _storage_id(sent[2]) == 3


@pytest.mark.parametrize(('provider_name', 'adapter_name', 'storage_id'), [
    ('boltp', 'upload_boltp', 2),
    ('beeimg_cn', 'upload_beeimg_cn', 5),
])
def test_v2_adapter_accepts_positive_storage_id(upload_adapter, provider_name,
                                                adapter_name, storage_id):
    providers, calls = upload_adapter

    result = getattr(providers, adapter_name)(b'image', 'image.png', '',
                                               {'storage_id': storage_id})

    assert result == URL
    assert len(calls) == 1
    assert calls[0][0:3] == (provider_name, 'upload', '')
    assert calls[0][3]['storage_id'] == storage_id


@pytest.mark.parametrize(('provider_name', 'adapter_name', 'storage_id'), [
    ('boltp', 'upload_boltp', 0),
    ('boltp', 'upload_boltp', -1),
    ('boltp', 'upload_boltp', 1.5),
    ('boltp', 'upload_boltp', '0'),
    ('boltp', 'upload_boltp', True),
    ('boltp', 'upload_boltp', None),
    ('boltp', 'upload_boltp', []),
    ('boltp', 'upload_boltp', {}),
    ('boltp', 'upload_boltp', (0,)),
    ('beeimg_cn', 'upload_beeimg_cn', 0),
    ('beeimg_cn', 'upload_beeimg_cn', -1),
])
def test_v2_adapter_rejects_invalid_storage_id(upload_adapter, provider_name,
                                               adapter_name, storage_id):
    from modules.image_host.core import ImageHostError

    providers, calls = upload_adapter

    with pytest.raises(ImageHostError) as caught:
        getattr(providers, adapter_name)(b'image', 'image.png', '',
                                        {'storage_id': storage_id})

    assert caught.value.code == 'invalid_options'
    assert calls == []


@pytest.mark.parametrize(('provider_name', 'adapter_name'), [
    ('boltp', 'upload_boltp'),
    ('beeimg_cn', 'upload_beeimg_cn'),
])
def test_v2_adapter_requires_explicit_storage_id(upload_adapter, provider_name,
                                                 adapter_name):
    from modules.image_host.core import ImageHostError

    providers, calls = upload_adapter

    with pytest.raises(ImageHostError) as caught:
        getattr(providers, adapter_name)(b'image', 'image.png', '', {})

    assert caught.value.code == 'invalid_options'
    assert calls == []


def test_boltp_recovery_counts_real_requests(monkeypatch, tmp_path):
    """恢复只按真实 POST 与组查询计数，不再产生候选切换。"""
    from modules.image_host.context import UploadContext
    from modules.image_host.registry import upload_with_fallback
    from modules.image_host.storage_cache import StorageCache
    from modules.operation_trace import trace_item
    from modules.operation_trace import trace_scope

    rejection = json.dumps(
        {'status': 'error', 'message': REJECTION}).encode()
    success = json.dumps(
        {'status': 'success', 'data': {'public_url': URL}}).encode()
    sent = _install(monkeypatch, [rejection, success], group_ids=(2, 3))
    context = UploadContext(
        cache=StorageCache(tmp_path / 'cache', clock=lambda: 1000.0))
    with trace_scope(mode='cli', images_planned=1,
                     slots_planned=1) as trace:
        with trace_item(image_index=1, slot_index=1):
            result = upload_with_fallback(
                b'image', 'image.png', [{'provider': 'boltp', 'token': '',
                                        'options': {'storage_id': 2}}],
                context=context)
        summary = trace.summary()
    context.close()

    assert result.success
    assert [request.method for request in sent] == ['POST', 'GET', 'POST']
    assert summary['upload_post_attempts'] == 2
    assert (summary['group_get_attempts'],
            summary['profile_get_attempts']) == (1, 0)
    assert summary['postupload_recoveries'] == 1
    assert summary['boltp_candidate_switches'] == 0
    assert summary['slots_succeeded'] == 1
