#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证受限 Boltp 令牌的自动存储链路，所有 HTTP 响应均为合成。"""

import json
from pathlib import Path

import pytest
import requests

from modules.image_host.context import UploadContext
from modules.image_host.registry import upload_with_fallback
from modules.image_host.storage_cache import StorageCache


@pytest.mark.parametrize('provider,status,expected_success', [
    ('boltp', 403, True),
    ('boltp', 401, False),
    ('boltp', 500, False),
    ('beeimg_cn', 403, False),
])
def test_profile_permission_does_not_block_boltp_upload(
        monkeypatch, tmp_path, provider, status, expected_success):
    """仅 Boltp 资料权限不足时使用组内首个存储，其他错误照常失败。"""
    calls = []

    def send(adapter, request, **kwargs):
        """在适配器边界合成组信息、资料拒绝和上传成功。"""
        calls.append(request)
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        if request.url.endswith('/group'):
            payload = {'status': 'success', 'data': {
                'group': {'options': {'file_expire_seconds': 0}},
                'storages': [{'id': 2}],
            }}
        elif request.url.endswith('/user/profile'):
            response.status_code = status
            payload = {'status': 'error', 'message': '您的令牌没有权限访问此API'}
        else:
            assert request.url.endswith('/upload')
            assert b'name="storage_id"\r\n\r\n2\r\n' in request.body
            payload = {'status': 'success', 'data': {
                'public_url': 'https://example.com/synthetic.png'}}
        response._content = json.dumps(payload).encode('utf-8')
        response._content_consumed = True
        return response

    def forbidden_netrc(*args, **kwargs):
        """禁止测试读取本机隐式凭证。"""
        pytest.fail('禁止读取 netrc')

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    monkeypatch.setattr(requests.sessions, 'get_netrc_auth', forbidden_netrc)
    context = UploadContext(cache=StorageCache(root=Path(tmp_path)))
    try:
        result = upload_with_fallback(
            b'synthetic-png', 'synthetic.png',
            [{'provider': provider, 'token': 'SYNTHETIC_TOKEN'}],
            context=context)
        assert result.success is expected_success
        assert len(calls) == (3 if expected_success else 2)
        if expected_success:
            assert result.url == 'https://example.com/synthetic.png'
            assert not result.failures
            metadata = context.get_metadata(provider, 'SYNTHETIC_TOKEN')
            assert metadata.storage_ids == (2,)
            assert metadata.default_storage_id is None
        else:
            assert result.failures[0].stage == 'profile'
            assert result.failures[0].http_status == status
    finally:
        context.close()
