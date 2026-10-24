#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""用跨行合成像素验证两种捕获来源的统一 RAW 输出。"""

import struct
from unittest.mock import Mock

from PIL import Image
import pytest

from modules.screenshot import adb
from modules.screenshot import window


PIXELS = [(11, 22, 33, 0), (44, 55, 66, 128),
          (77, 88, 99, 255), (101, 121, 141, 7)]


@pytest.mark.parametrize('source', ['window', 'adb12', 'adb16'])
def test_raw_sources_keep_row_order_without_compression(monkeypatch, source):
    """跨行 RGBA 布局保持精确，窗口忽略 X，ADB 保留 alpha。"""
    save = Mock(side_effect=AssertionError('RAW 不得调用压缩编码器'))
    monkeypatch.setattr(Image.Image, 'save', save)
    if source == 'window':
        pixels = bytes(channel for r, g, b, a in PIXELS
                       for channel in (b, g, r, a))
        result = window._encode_pixels(pixels, 2, 2, [], 'raw')
        expected = bytes(channel for r, g, b, _ in PIXELS
                         for channel in (r, g, b, 255))
    else:
        expected = bytes(channel for pixel in PIXELS for channel in pixel)
        header = struct.pack('<III', 2, 2, 1)
        if source == 'adb16':
            header += struct.pack('<I', 7)
        read = Mock(return_value=header + expected)
        monkeypatch.setattr(adb, '_read_capture', read)
        captured = adb.capture_adb('synthetic', purpose='cli', image_format='raw')
        assert (captured.source, captured.image_format) == ('adb', 'raw')
        assert (captured.width, captured.height) == (2, 2)
        result = captured.image_bytes
        read.assert_called_once_with('synthetic', False)
    assert struct.unpack('<4sIIII', result[:20]) == (b'2RAW', 1, 2, 2, 1)
    assert result[20:] == expected
    assert len(result) == 20 + 2 * 2 * 4
    save.assert_not_called()
