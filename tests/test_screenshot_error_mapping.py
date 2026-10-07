#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证捕获错误分类与对外安全文案，不访问真实设备或图床。"""

from argparse import Namespace
from importlib import import_module
from io import BytesIO
import struct
from unittest.mock import Mock

from PIL import Image
import pytest

from modules.screenshot.models import CaptureError


SECRET = 'token=PRIVATE_TEST_TOKEN'
MESSAGES = {
    'encode_failed': '窗口图像编码失败',
    'adb_encode_failed': 'ADB 图像编码失败',
    'adb_image_failed': 'ADB 未返回完整有效的图像数据',
    'window_not_found': '未找到完整标题匹配的窗口',
    'adb_timeout': 'ADB 连接或读写等待超时',
}


@pytest.mark.parametrize('image_format', ['jpeg', 'png', 'webp', 'raw'])
def test_window_encoding_failure_is_safe(monkeypatch, image_format):
    """各窗口编码格式失败均保留窗口分类而不泄露底层错误。"""
    window = import_module('modules.screenshot.window')
    encoder = Mock(side_effect=OSError(SECRET))
    monkeypatch.setattr(window, 'encode_image', encoder)
    with pytest.raises(CaptureError) as caught:
        window._encode_pixels(bytes([3, 2, 1, 0]), 1, 1, [], image_format)
    assert caught.value.code == 'encode_failed'
    assert str(caught.value) == MESSAGES['encode_failed']
    assert encoder.call_args.args[1] == image_format


@pytest.mark.parametrize('image_format', ['jpeg', 'webp', 'raw'])
def test_adb_cli_encoding_failure_does_not_retry(monkeypatch, image_format):
    """ADB CLI 编码失败直接终止，不重新采集或回退 PNG。"""
    adb = import_module('modules.screenshot.adb')
    read = Mock(return_value=struct.pack('<III', 1, 1, 1) + bytes(4))
    monkeypatch.setattr(adb, '_read_capture', read)
    monkeypatch.setattr(adb, 'encode_image', Mock(side_effect=OSError(SECRET)))
    with pytest.raises(CaptureError) as caught:
        adb.capture_adb('device', image_format=image_format, purpose='cli')
    assert caught.value.code == 'adb_encode_failed'
    assert str(caught.value) == MESSAGES['adb_encode_failed']
    read.assert_called_once_with('device', False)


def test_adb_automatic_final_encoding_failure(monkeypatch):
    """两次 raw 编码失败后仅回退一次，最终 JPEG 错误采用固定文案。"""
    adb = import_module('modules.screenshot.adb')
    stream = BytesIO()
    with Image.new('RGB', (1, 1)) as image:
        image.save(stream, format='PNG')
    raw = struct.pack('<III', 1, 1, 1) + bytes(4)
    read = Mock(side_effect=[raw, raw, stream.getvalue()])
    monkeypatch.setattr(adb, '_read_capture', read)
    monkeypatch.setattr(adb, 'encode_image', Mock(side_effect=OSError(SECRET)))
    with pytest.raises(CaptureError) as caught:
        adb.capture_adb('device')
    assert caught.value.code == 'adb_encode_failed'
    assert str(caught.value) == MESSAGES['adb_encode_failed']
    assert [call.args for call in read.call_args_list] == [
        ('device', False), ('device', False), ('device', True)]


@pytest.mark.parametrize('purpose', ['cli', 'automatic'])
def test_adb_invalid_image_message(monkeypatch, purpose):
    """直接 PNG 与自动回退的无效数据均输出格式中性文案。"""
    adb = import_module('modules.screenshot.adb')
    monkeypatch.setattr(adb, '_read_capture', Mock(return_value=SECRET.encode()))
    with pytest.raises(CaptureError) as caught:
        adb.capture_adb('device', image_format='png', purpose=purpose)
    assert caught.value.code == 'adb_image_failed'
    assert str(caught.value) == MESSAGES['adb_image_failed']


@pytest.mark.parametrize('entry', ['cli', 'pipeline'])
@pytest.mark.parametrize(('error', 'message'), [
    *((CaptureError(code, SECRET), '截图失败：' + message)
      for code, message in MESSAGES.items()),
    (CaptureError(SECRET, SECRET), '截图失败'),
    (CaptureError([], SECRET), '截图失败'),
    (RuntimeError(SECRET), '截图失败'),
])
def test_public_failure_uses_only_whitelist(
        monkeypatch, tmp_path, capsys, caplog, entry, error, message):
    """已知分类重新构造安全文案，未知代码和异常不可回显。"""
    module = import_module('modules.screenshot.' + entry)
    monkeypatch.setattr(module, 'capture', Mock(side_effect=error))
    save = Mock()
    upload = Mock()
    if entry == 'cli':
        monkeypatch.setattr(module, 'save_cli', save)
        monkeypatch.setattr(module, '_upload_all', upload)
        args = Namespace(source='window', target='target',
                         output=str(tmp_path / 'shot.jpg'), hosts=None)
        assert module.run_screenshot_cli(args, str(tmp_path)) == 1
        assert capsys.readouterr().out == message + '\n'
    else:
        monkeypatch.setattr(module, 'save_automatic', save)
        monkeypatch.setattr(module, 'upload_with_fallback', upload)
        batch = module.prepare_screenshots(
            {'targets': [{'provider': 'window', 'target': 'target'}]}, True, ())
        assert batch.values['screenshot'] == message
        assert message in caplog.text
    assert SECRET not in caplog.text
    save.assert_not_called()
    upload.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('entry', ['cli', 'pipeline', 'window', 'adb'])
@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_control_signals_remain_unchanged(monkeypatch, tmp_path, entry, signal_type):
    """捕获或编码中的控制信号必须按原对象传播。"""
    module = import_module('modules.screenshot.' + entry)
    signal = signal_type(7)
    boundary = 'encode_image' if entry in ('window', 'adb') else 'capture'
    monkeypatch.setattr(module, boundary, Mock(side_effect=signal))
    with pytest.raises(signal_type) as caught:
        if entry == 'window':
            module._encode_pixels(bytes(4), 1, 1, [], 'jpeg')
        elif entry == 'adb':
            monkeypatch.setattr(module, '_read_capture', Mock(
                return_value=struct.pack('<III', 1, 1, 1) + bytes(4)))
            module.capture_adb('device')
        elif entry == 'cli':
            args = Namespace(source='window', target='target',
                             output=str(tmp_path / 'shot.jpg'), hosts=None)
            module.run_screenshot_cli(args, str(tmp_path))
        else:
            module.prepare_screenshots(
                {'targets': [{'provider': 'window', 'target': 'target'}]}, True, ())
    assert caught.value is signal
