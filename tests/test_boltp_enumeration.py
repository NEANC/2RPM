#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证 v2 上传适配器的显式存储编号契约。"""

from importlib import import_module

import pytest


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
