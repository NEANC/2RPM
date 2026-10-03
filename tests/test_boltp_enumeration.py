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


def test_boltp_storage_zero_reaches_real_http_adapter(upload_adapter, monkeypatch):
    providers, _ = upload_adapter
    monkeypatch.setattr(
        providers, 'request_json',
        import_module('modules.image_host.v2_http').request_json)
    calls = []

    def send(adapter, request, **kwargs):
        calls.append(request)
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        response._content = json.dumps({
            'status': 'success',
            'data': {'public_url': URL},
        }).encode('utf-8')
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)

    result = providers.upload_boltp(b'image', 'image.png', '', {'storage_id': 0})

    assert result == URL
    assert len(calls) == 1
    assert calls[0].url.endswith('/api/v2/upload')
    assert calls[0].method == 'POST'
    content_type = calls[0].headers['Content-Type']
    assert content_type.startswith('multipart/form-data;')
    message = BytesParser(policy=default).parsebytes(
        f'Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n'.encode()
        + calls[0].body)
    storage_id = next(
        part for part in message.iter_parts()
        if part.get_param('name', header='content-disposition') == 'storage_id')
    assert storage_id.get_payload(decode=True) == b'0'


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


@pytest.mark.parametrize(('provider_name', 'adapter_name', 'storage_id'), [
    ('boltp', 'upload_boltp', 0),
    ('beeimg_cn', 'upload_beeimg_cn', 5),
])
def test_v2_adapter_accepts_allowed_storage_id(upload_adapter, provider_name,
                                               adapter_name, storage_id):
    providers, calls = upload_adapter

    result = getattr(providers, adapter_name)(b'image', 'image.png', '',
                                               {'storage_id': storage_id})

    assert result == URL
    assert len(calls) == 1
    assert calls[0][0:3] == (provider_name, 'upload', '')
    assert calls[0][3]['storage_id'] == storage_id


@pytest.mark.parametrize(('provider_name', 'adapter_name', 'storage_id'), [
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
