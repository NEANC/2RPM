#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""写入期间的目录固定、占用及控制信号验收。"""

from concurrent.futures import ThreadPoolExecutor
import ctypes
from threading import Event

import pytest

from modules.screenshot import winfs
from test_winfs_partial_acceptance import require_ntfs
from winfs_process_guard import run_isolated


def test_parent_cannot_be_replaced_during_real_write(monkeypatch, request, tmp_path):
    """以事件暂停真实写入，父目录替换失败且完成后句柄释放。"""
    if run_isolated(request, tmp_path):
        return
    require_ntfs(tmp_path)
    parent = tmp_path / 'parent'
    parent.mkdir()
    target = parent / 'capture.bin'
    entered = Event()
    release = Event()
    original = winfs._write

    def paused_write(handle, data):
        """写入部分载荷后等待替换尝试结束。"""
        original(handle, data[:2])
        entered.set()
        assert release.wait(10)
        original(handle, data[2:])

    monkeypatch.setattr(winfs, '_write', paused_write)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(winfs.write_exclusive, str(target), b'payload')
        try:
            assert entered.wait(10)
            with pytest.raises(OSError) as caught:
                parent.rename(tmp_path / 'moved')
            assert caught.value.winerror in (5, 32)
        finally:
            release.set()
        future.result(timeout=10)
    assert target.read_bytes() == b'payload'
    parent.rename(tmp_path / 'released')


def test_busy_parent_failure_releases_opened_ancestors(tmp_path):
    """父目录被不共享的句柄占用时失败，释放后同一路径可重试。"""
    require_ntfs(tmp_path)
    parent = tmp_path / 'busy'
    parent.mkdir()
    target = parent / 'capture.bin'
    value = winfs._CreateFile(str(parent), 0x40000000, 0, None, 3, 0x02000000, None)
    assert value != winfs._INVALID, ctypes.get_last_error()
    held = winfs._Handle(value)
    try:
        with pytest.raises(OSError) as caught:
            winfs.write_exclusive(str(target), b'payload')
        assert caught.value.winerror in (5, 32)
        assert not target.exists()
    finally:
        held.close()
    winfs.write_exclusive(str(target), b'payload')
    assert target.read_bytes() == b'payload'
    tmp_path.rename(tmp_path.with_name(tmp_path.name + '-released'))


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_partial_write_signal_cleans_file_and_releases_handles(
        monkeypatch, tmp_path, signal_type):
    """真实部分写入后控制信号原样传播，文件及全部目录句柄释放。"""
    require_ntfs(tmp_path)
    parent = tmp_path / 'nested' / 'parent'
    target = parent / 'capture.bin'
    original = winfs._write
    signal = signal_type('injected')

    def interrupt(handle, data):
        """真实写入半成品后发出控制信号。"""
        original(handle, data[:2])
        raise signal

    monkeypatch.setattr(winfs, '_write', interrupt)
    with pytest.raises(signal_type) as caught:
        winfs.write_exclusive(str(target), b'payload')
    assert caught.value is signal
    assert not target.exists()
    parent.rmdir()
    moved = tmp_path.rename(tmp_path.with_name(tmp_path.name + '-released'))
    moved.rename(tmp_path)
