#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""混合树及扫描身份变化的真实文件系统验收。"""

from datetime import date
import os

import pytest

from modules.screenshot import retention
from modules.screenshot import winfs
from test_screenshot_winfs import make_junction
from test_winfs_partial_acceptance import require_ntfs


def test_retention_reparse_mixed_tree_preserves_every_entry(tmp_path):
    """含普通文件、硬链接和 junction 的过期树整树保留，其他树照常删除。"""
    require_ntfs(tmp_path)
    tree = tmp_path / 'screenshot' / '2020_01_01'
    nested = tree / 'nested'
    nested.mkdir(parents=True)
    ordinary = tree / 'ordinary.bin'
    ordinary.write_bytes(b'ordinary')
    managed = nested / 'linked.bin'
    managed.write_bytes(b'linked')
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'sentinel.bin'
    sentinel.write_bytes(b'outside')
    hardlink = outside / 'hardlink.bin'
    os.link(managed, hardlink)
    junction = nested / 'junction'
    make_junction(junction, outside)
    safe = tree.parent / '2020_01_02'
    safe.mkdir()
    (safe / 'normal.bin').write_bytes(b'normal')
    try:
        warnings = retention.cleanup_retention(
            str(tmp_path), retention.RetentionPolicy(True, 1), today=date(2024, 1, 1))
        assert warnings == ('截图清理失败：已跳过不安全或无法删除的日期目录',)
        assert ordinary.read_bytes() == b'ordinary'
        assert managed.read_bytes() == b'linked'
        assert hardlink.read_bytes() == b'linked'
        assert sentinel.read_bytes() == b'outside'
        assert junction.is_dir()
        assert not safe.exists()
    finally:
        os.rmdir(junction)
    tree.rename(tree.parent / 'released')


def test_scan_rejects_real_child_replacement_without_deleting_siblings(
        monkeypatch, tmp_path):
    """真实枚举后替换尚未打开的子文件，旧身份与新对象都不得误删。"""
    require_ntfs(tmp_path)
    tree = tmp_path / 'tree'
    tree.mkdir()
    target = tree / 'target.bin'
    target.write_bytes(b'original')
    sibling = tree / 'sibling.bin'
    sibling.write_bytes(b'sibling')
    saved = tmp_path / 'original.bin'
    os.link(target, saved)
    original_entries = winfs._entries
    exchanged = False

    def exchange_after_snapshot(handle):
        """保存旧对象确保文件标识不复用，再创建同名替代对象。"""
        nonlocal exchanged
        entries = tuple(original_entries(handle))
        if not exchanged and any(name == target.name for name, _, _ in entries):
            target.unlink()
            target.write_bytes(b'replacement')
            exchanged = True
        yield from entries

    monkeypatch.setattr(winfs, '_entries', exchange_after_snapshot)
    with pytest.raises(winfs.UnsafeObjectError, match='扫描对象身份改变'):
        winfs.remove_tree(str(tree))
    assert exchanged
    assert target.read_bytes() == b'replacement'
    assert sibling.read_bytes() == b'sibling'
    assert saved.read_bytes() == b'original'
    tree.rename(tmp_path / 'released')
