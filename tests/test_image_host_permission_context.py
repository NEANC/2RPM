#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""离线验证公开权限类型和上传异常上下文隔离。"""

from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import PropertyMock

import pytest
import requests

from modules.image_host import context as context_module
from modules.image_host import providers
from modules.image_host.core import ImageHostError
from modules.image_host.storage import StorageMetadata
from modules.image_host.storage_cache import StorageCache


@pytest.fixture
def session(monkeypatch):
    """替换会话工厂，确保测试不产生真实网络请求。"""
    client = MagicMock()
    client.__enter__.return_value = client
    client.prepare_request.side_effect = lambda request: request.prepare()
    client.merge_environment_settings.return_value = {
        'stream': True, 'verify': True}
    response = client.send.return_value.__enter__.return_value
    response.status_code = 200
    response.text = 'https://example.com/test.png'
    factory = MagicMock(return_value=client)
    def forbidden(*args, **kwargs):
        """拒绝绕过替身的真实传输。"""
        pytest.fail('测试禁止访问真实网络')

    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    monkeypatch.setattr(requests.sessions.Session, 'send', forbidden)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', forbidden)
    monkeypatch.setattr(requests, 'Session', factory)
    return factory, client, response


@pytest.mark.parametrize('permission', [True, False, 1.0, 0, -1, '1', None, [], {}])
def test_catbox_rejects_invalid_permission_before_io(session, permission):
    """仅整数公开权限合法，非法类型在创建会话前拒绝。"""
    factory, _, _ = session
    with pytest.raises(ImageHostError) as caught:
        providers.upload_catbox(b'png', 'test.png', '', {'permission': permission})
    assert caught.value.code == 'invalid_options'
    factory.assert_not_called()


@pytest.mark.parametrize('provider', ['catbox', 'wmimg'])
@pytest.mark.parametrize('stage', ['post', 'response'])
@pytest.mark.parametrize('error_type', [
    requests.exceptions.ConnectionError,
    requests.exceptions.ReadTimeout,
    requests.exceptions.ContentDecodingError,
])
def test_upload_error_discards_raw_context(session, provider, stage, error_type):
    """请求与响应读取错误只留下固定异常，资源仍确定性释放。"""
    _, client, response = session
    error = error_type('SYNTHETIC_SECRET')
    if stage == 'post':
        client.send.side_effect = error
    elif provider == 'wmimg':
        response.json.side_effect = error
    else:
        type(response).text = PropertyMock(side_effect=error)
    with pytest.raises(ImageHostError) as caught:
        getattr(providers, 'upload_' + provider)(b'png', 'test.png', '', {})
    assert caught.value.code == 'upload_failed'
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    client.__exit__.assert_called_once()
    client.send.assert_called_once()
    prepared = client.send.call_args.args[0]
    assert isinstance(prepared, requests.PreparedRequest)
    assert prepared.method == 'POST'
    assert b'png' in prepared.body
    assert client.send.return_value.__exit__.call_count == (stage == 'response')


def test_selection_failure_stays_slot_local(monkeypatch, tmp_path):
    """选择失败只归当前槽位，不写入共享查询失败或阻断后续合法手填。"""
    forbidden = StorageMetadata((13, 14), None, 3600, 1000.0, 'forbidden')
    monkeypatch.setattr(
        context_module, 'fetch_storage_metadata',
        lambda provider, token, *, now: forbidden)
    context = context_module.UploadContext(
        cache=StorageCache(root=Path(tmp_path)))
    try:
        image = context.start_image()
        with pytest.raises(ImageHostError) as caught:
            context.prepare_storage(image, 0, 'beeimg_cn', 'SYNTHETIC_TOKEN')
        assert caught.value.code == 'profile_forbidden'
        assert (caught.value.stage, caught.value.http_status) == ('profile', 403)
        entry, = context._entries.values()
        assert entry.failure is None
        assert entry.metadata is forbidden
        selected = context.prepare_storage(
            image, 1, 'beeimg_cn', 'SYNTHETIC_TOKEN',
            manual_present=True, manual_value=13)
        assert selected.storage_id == 13
    finally:
        context.close()
