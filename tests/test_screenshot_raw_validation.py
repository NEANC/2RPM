#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证 ADB raw 解码的非法头部和精确负载边界。"""

import struct
from unittest.mock import Mock

import pytest

from modules.screenshot import adb


@pytest.mark.parametrize('header_size', [12, 16])
@pytest.mark.parametrize('delta', [-1, 1, 5])
def test_raw_rejects_inexact_payload(monkeypatch, header_size, delta):
    """两种头部均拒绝不能解释为合法头部的截断和多余负载。"""
    header = struct.pack('<III', 2, 2, 1) + b'\0' * (header_size - 12)
    decode = Mock(side_effect=AssertionError('非法长度不得分配图像'))
    monkeypatch.setattr(adb.Image, 'frombytes', decode)
    with pytest.raises(adb.CaptureError) as caught:
        adb._parse_raw(header + bytes(16 + delta))
    assert caught.value.code == 'adb_raw_invalid'
    decode.assert_not_called()


@pytest.mark.parametrize('header_size', [12, 16])
@pytest.mark.parametrize(('width', 'height', 'pixel_format'), [
    (2, 2, 0), (2, 2, 2), (2, 2, 0xffffffff),
    (0, 2, 1), (2, 0, 1), (0, 0, 1),
    (89478486, 1, 1), (9460, 9460, 1),
])
def test_raw_rejects_invalid_metadata(
        monkeypatch, header_size, width, height, pixel_format):
    """格式、零尺寸和超像素限制在像素解码之前生效。"""
    header = struct.pack('<III', width, height, pixel_format)
    header += b'\0' * (header_size - 12)
    decode = Mock(side_effect=AssertionError('非法元数据不得分配图像'))
    monkeypatch.setattr(adb.Image, 'frombytes', decode)
    with pytest.raises(adb.CaptureError) as caught:
        adb._parse_raw(header + bytes(16))
    assert caught.value.code == 'adb_raw_invalid'
    decode.assert_not_called()


@pytest.mark.parametrize('length', [0, 1, 11])
def test_raw_rejects_truncated_header(length):
    """基础头部不足十二字节时稳定返回 raw 分类。"""
    with pytest.raises(adb.CaptureError) as caught:
        adb._parse_raw(bytes(length))
    assert caught.value.code == 'adb_raw_invalid'
