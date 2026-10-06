#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""Windows 句柄安全文件系统沙盒测试。"""

import ctypes
import os
import struct
import subprocess
import sys
from ctypes import wintypes

import pytest

from modules.screenshot import winfs


_DeviceIoControl = winfs._bind(
    winfs._K32, 'DeviceIoControl', wintypes.BOOL,
    [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
     ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p])


def make_junction(link, target):
    """通过 FSCTL_SET_REPARSE_POINT 在测试目录建立 junction。"""
    link.mkdir()
    absolute = os.path.abspath(str(target))
    substitute = ('\\??\\UNC\\' + absolute[2:] if absolute.startswith('\\\\')
                  else '\\??\\' + absolute).encode('utf-16-le')
    printable = absolute.encode('utf-16-le')
    paths = substitute + b'\0\0' + printable + b'\0\0'
    payload = struct.pack('<HHHH', 0, len(substitute), len(substitute) + 2,
                          len(printable)) + paths
    data = struct.pack('<IHH', 0xA0000003, len(payload), 0) + payload
    value = winfs._CreateFile(str(link), 0x40000000, 0, None, 3,
                              0x02000000 | 0x00200000, None)
    if value == winfs._INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    owned = winfs._Handle(value)
    try:
        buffer = ctypes.create_string_buffer(data)
        returned = wintypes.DWORD()
        if not _DeviceIoControl(value, 0x000900A4, buffer, len(data), None, 0,
                                ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        owned.close()


def test_exclusive_write_preserves_existing_file(tmp_path):
    """排他写入不覆盖既有文件。"""
    target = tmp_path / 'capture.jpg'
    winfs.write_exclusive(str(target), b'owned')
    with pytest.raises(FileExistsError):
        winfs.write_exclusive(str(target), b'replacement')
    assert target.read_bytes() == b'owned'


def test_remove_tree_unlinks_only_managed_hardlink(tmp_path):
    """句柄删除受管链接时保留树外硬链接数据。"""
    tree = tmp_path / 'tree'
    tree.mkdir()
    managed = tree / 'capture.jpg'
    managed.write_bytes(b'owned')
    outside = tmp_path / 'outside.jpg'
    os.link(managed, outside)
    winfs.remove_tree(str(tree))
    assert not tree.exists()
    assert outside.read_bytes() == b'owned'


def test_list_names_reads_names_from_directory_handle(tmp_path):
    """目录句柄枚举返回直接子项名称。"""
    names = {'普通.txt', 'long-' + '中文' * 40 + '.txt'}
    for name in names:
        (tmp_path / name).write_bytes(b'x')
    assert set(winfs.list_names(str(tmp_path))) == names


def test_rejects_dot_components_without_normalizing(tmp_path):
    """路径含点组件时不得先规范化再进行句柄操作。"""
    path = os.path.join(str(tmp_path), '.', 'file')
    with pytest.raises(winfs.UnsafeObjectError):
        winfs.write_exclusive(path, b'x')


def test_write_failure_removes_partial_file(monkeypatch, tmp_path):
    """部分写入后失败时通过当前文件句柄删除半成品。"""
    target = tmp_path / 'partial.jpg'
    original_write = winfs._write

    def write_then_fail(handle, data):
        """先执行真实写入，再注入写入失败。"""
        original_write(handle, data[:2])
        raise OSError('injected write failure')

    monkeypatch.setattr(winfs, '_write', write_then_fail)
    with pytest.raises(OSError, match='injected write failure'):
        winfs.write_exclusive(str(target), b'payload')
    assert not target.exists()


def test_failed_close_is_reported_without_leaking_handle(monkeypatch, tmp_path):
    """正常路径句柄关闭失败必须向调用方报告。"""
    target = tmp_path / 'closed.jpg'
    original_close = winfs._Close
    close_calls = []

    def fail_one_close(handle):
        """真实关闭句柄后模拟关闭状态报告失败。"""
        close_calls.append(handle)
        original_close(handle)
        ctypes.set_last_error(5)
        return 0

    monkeypatch.setattr(winfs, '_Close', fail_one_close)
    with pytest.raises(OSError):
        winfs.write_exclusive(str(target), b'payload')
    assert close_calls
    assert target.read_bytes() == b'payload'


def test_windows_abi_layout_and_multibuffer_enumeration(tmp_path):
    """核对 64 位 Windows 结构布局并真实枚举跨缓冲目录。"""
    assert ctypes.sizeof(winfs.IO_STATUS_BLOCK) == 2 * ctypes.sizeof(ctypes.c_void_p)
    assert winfs.FILE_ID_BOTH_DIR_INFO.FileId.offset == 96
    assert winfs.FILE_ID_BOTH_DIR_INFO.FileName.offset == 104
    assert ctypes.sizeof(winfs.BY_HANDLE_FILE_INFORMATION) == 52
    names = {f'{index:04d}_' + '中文' * 35 + '.txt' for index in range(600)}
    for name in names:
        (tmp_path / name).write_bytes(b'x')
    assert set(winfs.list_names(str(tmp_path))) == names


def test_rejects_junction_child_without_touching_target(monkeypatch, tmp_path):
    """相对打开遭遇切换成 junction 的同名项时拒绝越界访问。"""
    tree = tmp_path / 'tree'
    tree.mkdir()
    child = tree / 'child'
    child.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    marker = outside / 'marker'
    marker.write_bytes(b'outside')
    original = winfs._open_child
    replaced = False

    def replace_before_open(parent, name, directory, create=False, delete=False):
        """在原生相对打开前将尚未固定的目录替换为 junction。"""
        nonlocal replaced
        if name == 'child' and not replaced:
            replaced = True
            child.rmdir()
            make_junction(child, outside)
        return original(parent, name, directory, create, delete)

    monkeypatch.setattr(winfs, '_open_child', replace_before_open)
    try:
        with pytest.raises(winfs.UnsafeObjectError):
            winfs.remove_tree(str(tree))
        assert replaced
        assert marker.read_bytes() == b'outside'
    finally:
        if replaced and child.exists():
            os.rmdir(child)


def test_held_directory_handle_blocks_rename(tmp_path):
    """持有禁止共享删除的目录句柄期间，其他进程不能移走目录。"""
    tree = tmp_path / 'tree'
    tree.mkdir()
    script = ('import os,sys\n'
              'try: os.rename(sys.argv[1], sys.argv[2])\n'
              'except OSError as error: sys.exit(0 if error.winerror in (5,32) else 3)\n'
              'sys.exit(4)\n')
    with winfs._open_path(str(tree)):
        result = subprocess.run([sys.executable, '-B', '-c', script, str(tree),
                                 str(tmp_path / 'moved')], check=False, timeout=10)
        assert result.returncode == 0
    tree.rename(tmp_path / 'released')
