#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""不分配大图内存地验证 PNG 解码流程与像素上限。"""

from io import BytesIO
from unittest.mock import MagicMock
from unittest.mock import Mock

from PIL import Image
from PIL import PngImagePlugin
import pytest

from modules.screenshot import adb


@pytest.mark.parametrize(('size', 'accepted'), [
    ((7680, 4320), True), ((89478485, 1), True),
    ((89478486, 1), False), ((0, 2), False), ((2, 0), False),
])
def test_png_verifies_once_and_loads_only_valid_dimensions(
        monkeypatch, size, accepted):
    """8K 和上限可加载，非法尺寸在显式加载前拒绝且仅校验一次。"""
    verified = MagicMock()
    decoded = MagicMock()
    verified.__enter__.return_value = verified
    decoded.__enter__.return_value = decoded
    decoded.size = size
    opened = Mock(side_effect=[verified, decoded])
    monkeypatch.setattr(adb.Image, 'open', opened)
    data = adb._PNG_SIGNATURE + adb._PNG_END
    if accepted:
        assert adb._decode_png(data) is decoded.copy.return_value
        decoded.load.assert_called_once_with()
        decoded.copy.assert_called_once_with()
    else:
        with pytest.raises(adb.CaptureError) as caught:
            adb._decode_png(data)
        assert caught.value.code == 'adb_image_failed'
        decoded.load.assert_not_called()
        decoded.copy.assert_not_called()
    assert opened.call_count == 2
    for call in opened.call_args_list:
        assert call.kwargs == {'formats': ['PNG']}
    verified.verify.assert_called_once_with()
    verified.load.assert_not_called()
    decoded.verify.assert_not_called()
    verified.__exit__.assert_called_once()
    decoded.__exit__.assert_called_once()


def test_real_png_verifies_and_decodes_pixels_once(monkeypatch):
    """真实 PNG 校验一次且仅创建一次像素解码器，副本不重新解码。"""
    pixels = bytes([17, 31, 49, 0, 90, 8, 7, 128,
                    11, 22, 33, 255, 44, 55, 66, 7])
    with BytesIO() as stream:
        with Image.frombytes('RGBA', (2, 2), pixels) as source:
            source.save(stream, format='PNG')
        data = stream.getvalue()
    verify_calls = []
    original_verify = PngImagePlugin.PngImageFile.verify
    decoder = Mock(wraps=Image._getdecoder)

    def record_verify(image):
        """记录真实结构校验并继续执行 Pillow 的校验逻辑。"""
        verify_calls.append(image)
        return original_verify(image)

    monkeypatch.setattr(PngImagePlugin.PngImageFile, 'verify', record_verify)
    monkeypatch.setattr(Image, '_getdecoder', decoder)
    with adb._decode_png(data) as decoded:
        assert decoded.size == (2, 2)
        assert decoded.mode == 'RGBA'
        assert decoded.tobytes() == pixels
    assert len(verify_calls) == 1
    assert decoder.call_count == 1
    assert decoder.call_args.args[1] == 'zip'
