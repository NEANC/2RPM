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
    ('boltp', 401, True),
    ('boltp', 500, True),
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
        if provider == 'boltp':
            assert request.url.endswith('/upload')
            assert b'name="storage_id"\r\n\r\n2\r\n' in request.body
            payload = {'status': 'success', 'data': {
                'public_url': 'https://example.com/synthetic.png'}}
        elif request.url.endswith('/group'):
            payload = {'status': 'success', 'data': {
                'group': {'options': {'file_expire_seconds': 0}},
                'storages': [{'id': 2}],
            }}
        elif request.url.endswith('/user/profile'):
            response.status_code = status
            payload = {'status': 'error', 'message': '您的令牌没有权限访问此API'}
        else:
            assert request.url.endswith('/upload')
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
        if provider == 'boltp':
            assert len(calls) == 1
            assert calls[0].url.endswith('/upload')
        else:
            assert len(calls) == 2
            assert calls[0].url.endswith('/group')
            assert calls[1].url.endswith('/user/profile')
            assert result.failures[0].stage == 'profile'
            assert result.failures[0].http_status == status
        if expected_success:
            assert result.url == 'https://example.com/synthetic.png'
            assert not result.failures
    finally:
        context.close()
