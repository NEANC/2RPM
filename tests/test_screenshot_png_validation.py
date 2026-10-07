#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""不分配大图内存地验证 PNG 解码流程与像素上限。"""

from unittest.mock import MagicMock
from unittest.mock import Mock

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
