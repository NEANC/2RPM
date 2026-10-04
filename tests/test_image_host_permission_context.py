#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""离线验证公开权限类型和上传异常上下文隔离。"""

from unittest.mock import MagicMock
from unittest.mock import PropertyMock

import pytest
import requests

from modules.image_host import providers
from modules.image_host.core import ImageHostError


@pytest.fixture
def session(monkeypatch):
    """替换会话工厂，确保测试不产生真实网络请求。"""
    client = MagicMock()
    client.__enter__.return_value = client
    response = client.post.return_value.__enter__.return_value
    response.status_code = 200
    response.text = 'https://example.com/test.png'
    factory = MagicMock(return_value=client)
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
        client.post.side_effect = error
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
    assert client.post.return_value.__exit__.call_count == (stage == 'response')
