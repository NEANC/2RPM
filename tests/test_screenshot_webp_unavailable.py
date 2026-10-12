#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证实际 Pillow WebP 编码器缺失时两来源的安全错误。"""

import struct
from unittest.mock import Mock

from PIL import Image
import pytest

from modules.screenshot import adb
from modules.screenshot import window
from modules.screenshot.models import CaptureError


@pytest.mark.parametrize(('source', 'code', 'message'), [
    ('window', 'encode_failed', '窗口图像编码失败'),
    ('adb', 'adb_encode_failed', 'ADB 图像编码失败'),
])
def test_missing_webp_encoder_is_classified(monkeypatch, source, code, message):
    """移除真实保存注册项后不得悄悄降级或再次捕获。"""
    Image.init()
    monkeypatch.delitem(Image.SAVE, 'WEBP', raising=False)
    read = Mock(return_value=struct.pack('<III', 1, 1, 1) + bytes([17, 31, 49, 0]))
    monkeypatch.setattr(adb, '_read_capture', read)
    with pytest.raises(CaptureError) as caught:
        if source == 'window':
            window._encode_pixels(bytes([49, 31, 17, 0]), 1, 1, [], 'webp')
        else:
            adb.capture_adb('synthetic', purpose='cli', image_format='webp')
    assert caught.value.code == code
    assert str(caught.value) == message
    if source == 'adb':
        read.assert_called_once_with('synthetic', False)
    else:
        read.assert_not_called()
