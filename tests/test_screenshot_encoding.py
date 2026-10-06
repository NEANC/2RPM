#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证截图像素转换与固定格式编码。"""

from io import BytesIO
import struct

from PIL import Image
import pytest

from modules.screenshot.encoding import encode_image
from modules.screenshot.encoding import window_image


def test_raw_header_and_window_alpha():
    """窗口 X 无语义，统一 RAW 必须为固定 RGBA8 布局。"""
    with window_image(bytes([3, 2, 1, 0, 6, 5, 4, 7]), 2, 1) as image:
        data = encode_image(image, 'raw')
    assert struct.unpack('<4sIIII', data[:20]) == (b'2RAW', 1, 2, 1, 1)
    assert data[20:] == bytes([1, 2, 3, 255, 4, 5, 6, 255])
    assert len(data) == 28


def test_webp_preserves_transparent_rgb():
    """完全透明和半透明像素的 RGB 不能丢失。"""
    with Image.frombytes(
            'RGBA', (2, 1), bytes([17, 31, 49, 0, 90, 8, 7, 128])) as source:
        encoded = encode_image(source, 'webp')
        with Image.open(BytesIO(encoded)) as decoded:
            assert decoded.size == source.size
            assert decoded.convert('RGBA').tobytes() == source.tobytes()


def test_jpeg_parameters(monkeypatch):
    """固定 q75、不优化、不显式设置采样。"""
    calls = []
    original = Image.Image.save

    def record(image, stream, **kwargs):
        """保留真实编码并记录参数。"""
        calls.append((image.mode, kwargs))
        return original(image, stream, **kwargs)

    monkeypatch.setattr(Image.Image, 'save', record)
    with Image.new('RGBA', (2, 1), (250, 50, 20, 0)) as image:
        data = encode_image(image, 'jpeg')
    assert data.startswith(b'\xff\xd8')
    assert calls == [('RGB', {'format': 'JPEG', 'quality': 75,
                              'optimize': False})]


def test_encode_image_rejects_unknown_format():
    """编码器拒绝未冻结的图像格式。"""
    with Image.new('RGB', (1, 1)) as image:
        with pytest.raises(ValueError, match='不支持的内部图像格式'):
            encode_image(image, 'bmp')
