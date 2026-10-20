#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证两站都不请求资料接口，权限仅映射为上传表单，所有 HTTP 响应均为合成。"""

import json
from pathlib import Path

import pytest
import requests

from modules.image_host.context import UploadContext
from modules.image_host.registry import upload_with_fallback
from modules.image_host.storage_cache import StorageCache


URL = 'https://example.com/synthetic.png'
STORAGE_ID = 2


@pytest.mark.parametrize('provider', ['boltp', 'beeimg_cn'])
def test_profile_is_never_queried_and_permission_maps_to_form(
        monkeypatch, tmp_path, provider):
    """资料接口零请求；组首项直接上传，权限映射为 is_public 表单值。"""
    calls = []

    def send(adapter, request, **kwargs):
        """合成组信息与上传成功，任何资料请求立即失败。"""
        calls.append(request)
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        if request.url.endswith('/group'):
            payload = {'status': 'success', 'data': {
                'group': {'options': {'file_expire_seconds': 0}},
                'storages': [{'id': STORAGE_ID}],
            }}
        elif request.url.endswith('/upload'):
            assert b'name="storage_id"\r\n\r\n2\r\n' in request.body
            assert b'name="is_public"\r\n\r\n0\r\n' in request.body
            payload = {'status': 'success', 'data': {
                'public_url': URL}}
        else:
            pytest.fail('两站都不得请求资料接口')
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
            [{'provider': provider, 'token': 'SYNTHETIC_TOKEN',
              'options': {'permission': 0}}],
            context=context)
    finally:
        context.close()

    assert result.success is True
    assert result.url == URL
    assert not result.failures
    assert [request.url.rsplit('/api/v2', 1)[1] for request in calls] == [
        '/group', '/upload']
    assert len(calls) == 2
