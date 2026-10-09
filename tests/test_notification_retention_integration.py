#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证真实保留落盘在通知多通道重试中只执行一次。"""

from collections import Counter
from copy import deepcopy
from datetime import date
from io import BytesIO
from unittest.mock import Mock

from PIL import Image
import pytest

from modules import notification
from modules.screenshot import pipeline
from modules.screenshot import window
from test_notification_screenshot import _base_config
from test_notification_screenshot import _install_onepush
from test_notification_screenshot import _response
from test_notification_screenshot import TEMPLATE_KEYS
from test_notification_screenshot import THREE_CHANNELS
from test_screenshot_cli_host_integration import http_calls
from test_screenshot_cli_host_integration import URL
from test_screenshot_window import TITLE
from test_screenshot_window import WindowEnvironment


@pytest.mark.parametrize('event', TEMPLATE_KEYS)
def test_retained_notification_retries_share_capture_save_and_upload(
        monkeypatch, tmp_path, http_calls, event):
    """真实事件名落盘一次，首图床成功即停止，三通道重试复用同一正文。"""
    WindowEnvironment(window, monkeypatch)
    capture = Mock(wraps=pipeline.capture)
    save = Mock(wraps=pipeline.save_automatic)
    monkeypatch.setattr(pipeline, 'capture', capture)
    monkeypatch.setattr(pipeline, 'save_automatic', save)
    attempts = Counter()

    def notify(title=None, content=None, **params):
        """每通道首次失败，第二次成功，不访问真实推送服务。"""
        key = params.get('sckey') or params.get('token') or params.get('webhook')
        attempts[key] += 1
        return _response(500 if attempts[key] == 1 else 200)

    sent, notifier, _ = _install_onepush(monkeypatch, notify)
    config = _base_config(
        channels=THREE_CHANNELS,
        targets=[{'provider': 'window', 'target': TITLE}],
        image_host=[{'provider': 'catbox'}, {'provider': 'beeimg'}],
        retry={'interval': '0s', 'max_count': 3})
    config['push']['screenshot']['retention'] = {'enabled': True, 'max_days': 7}
    config['_runtime'] = {
        'program_dir': str(tmp_path), 'config_stem': 'PRIVATE_RUNTIME_STEM'}
    before = deepcopy(config)
    assert notification.send_notification(
        config, event, process_name='demo.exe') == [
            ('serverchan', True), ('dingtalk', True), ('lark', True)]
    capture.assert_called_once_with(
        'window', TITLE, image_format='jpeg', purpose='automatic')
    assert save.call_count == 1
    assert save.call_args.args[1] == config['_runtime']
    assert save.call_args.args[2] == event
    assert save.call_args.args[3].enabled is True
    assert len(http_calls) == 1
    assert http_calls[0][1] == 'https://catbox.moe/user/api.php'
    part = http_calls[0][2]['files']['fileToUpload']
    assert part[0] == 'screenshot_1.jpg'
    assert part[1] is save.call_args.args[0].image_bytes
    assert part[2] == 'image/jpeg'
    files = list(tmp_path.rglob('*.jpg'))
    assert files == [tmp_path / 'screenshot' / date.today().strftime('%Y_%m_%d')
                     / 'PRIVATE_RUNTIME_STEM' / (event + '_01.jpg')]
    assert files[0].read_bytes() == part[1]
    with Image.open(BytesIO(files[0].read_bytes())) as image:
        assert image.format == 'JPEG'
        assert image.size == (2, 2)
    assert notifier.notify.call_count == 6
    assert attempts == {'s1': 2, 't1': 2, 'w1': 2}
    assert len(set(sent)) == 1
    assert URL in sent[0][1]
    for title, content in sent:
        assert str(tmp_path) not in title + content
        assert 'PRIVATE_RUNTIME_STEM' not in title + content
        assert '_runtime' not in title + content
    assert config == before
