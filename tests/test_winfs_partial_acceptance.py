#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""原生短写及半成品归属验收。"""

import ctypes
from ctypes import wintypes

import pytest

from modules.screenshot import winfs


def require_ntfs(path):
    """确认所有真实文件操作所在的临时卷为 NTFS。"""
    filesystem = ctypes.create_unicode_buffer(32)
    get_volume = winfs._bind(
        winfs._K32, 'GetVolumeInformationW', wintypes.BOOL,
        [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
         ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
         ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD])
    assert get_volume(path.anchor, None, 0, None, None, None, filesystem, 32)
    assert filesystem.value == 'NTFS'


def test_native_short_writes_advance_without_duplicating(monkeypatch, tmp_path):
    """真实 WriteFile 每次只写三字节，最终载荷必须逐字节一致。"""
    require_ntfs(tmp_path)
    target = tmp_path / 'short.bin'
    original = winfs._Write
    requested = []

    def short_write(handle, buffer, size, written, overlapped):
        """截短原生调用而不伪造返回的写入字节数。"""
        requested.append(size)
        return original(handle, buffer, min(size, 3), written, overlapped)

    monkeypatch.setattr(winfs, '_Write', short_write)
    winfs.write_exclusive(str(target), b'0123456789')
    assert requested == [10, 7, 4, 1]
    assert target.read_bytes() == b'0123456789'


def test_zero_progress_removes_only_owned_partial(monkeypatch, tmp_path):
    """部分写入后零进展必须失败并保留相邻既有文件。"""
    require_ntfs(tmp_path)
    target = tmp_path / 'partial.bin'
    sentinel = tmp_path / 'sentinel.bin'
    sentinel.write_bytes(b'untouched')
    original = winfs._Write
    calls = []

    def zero_after_partial(handle, buffer, size, written, overlapped):
        """首次真实写入，第二次模拟成功但无进展。"""
        calls.append(size)
        if len(calls) == 1:
            return original(handle, buffer, 2, written, overlapped)
        ctypes.cast(written, ctypes.POINTER(wintypes.DWORD)).contents.value = 0
        return 1

    monkeypatch.setattr(winfs, '_Write', zero_after_partial)
    with pytest.raises(OSError, match='写入无进展'):
        winfs.write_exclusive(str(target), b'payload')
    assert calls == [7, 5]
    assert not target.exists()
    assert sentinel.read_bytes() == b'untouched'
    tmp_path.rename(tmp_path.with_name(tmp_path.name + '-released'))


def test_unproven_partial_identity_is_not_deleted(monkeypatch, tmp_path):
    """失败后无法确认原文件身份时不得标记删除，且保留原异常。"""
    require_ntfs(tmp_path)
    target = tmp_path / 'partial.bin'
    original_identity = winfs._identity
    original_write = winfs._write
    failed = False
    failure = OSError('injected')

    def fail_after_write(handle, data):
        """真实写入后切换身份查询故障窗口。"""
        nonlocal failed
        original_write(handle, data[:2])
        failed = True
        raise failure

    def changed_identity(handle):
        """仅在失败清理时报告无法证明的身份。"""
        identity = original_identity(handle)
        return (identity[0], identity[1] + 1, identity[2]) if failed else identity

    monkeypatch.setattr(winfs, '_write', fail_after_write)
    monkeypatch.setattr(winfs, '_identity', changed_identity)
    with pytest.raises(OSError) as caught:
        winfs.write_exclusive(str(target), b'payload')
    assert caught.value is failure
    assert failure.__notes__ == ['截图半成品清理失败']
    assert target.read_bytes() == b'pa'
    target.unlink()
    tmp_path.rename(tmp_path.with_name(tmp_path.name + '-released'))
