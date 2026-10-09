#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""真实符号链接边界及不区分标签的重解析属性拒绝验收。"""

import ctypes
from datetime import date
import os
import stat

import pytest

from modules.screenshot import retention
from modules.screenshot import winfs
from modules.screenshot.models import CaptureResult
from test_winfs_partial_acceptance import require_ntfs


TODAY = date(2026, 10, 6)
REAL_SYMLINK_ACCEPTANCE = pytest.mark.skipif(
    os.environ.get('RUN_WINFS_SYMLINK_ACCEPTANCE') != '1',
    reason=(
        '真实 symlink 环境门禁未验收；设置 RUN_WINFS_SYMLINK_ACCEPTANCE=1 '
        '显式运行。当前环境有效绝对路径的原生创建在 Z: 返回 87、E: 返回 2，'
        '并非已确认的权限不足；属性注入不替代真实 symlink 验收。'
    ),
)


def make_symlink(link, target, directory):
    """创建并核实真实 symlink；无权限时明确跳过而不使用 junction 替代。"""
    assert target.is_absolute() and target.exists()
    assert target.is_dir() == directory
    assert link.is_absolute() and link.parent.is_dir()
    assert not os.path.lexists(link)
    try:
        os.symlink(target, link, target_is_directory=directory)
    except OSError as error:
        if error.winerror == 1314:
            pytest.skip('真实 symlink 未执行：缺少符号链接创建权限（WinError 1314），未提权')
        raise
    metadata = link.lstat()
    assert metadata.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    assert metadata.st_reparse_tag == stat.IO_REPARSE_TAG_SYMLINK
    assert link.is_symlink()


@REAL_SYMLINK_ACCEPTANCE
@pytest.mark.parametrize('directory', [False, True], ids=['file', 'directory'])
def test_real_symlink_preserves_entire_tree_and_external_target(tmp_path, directory):
    """文件或目录 symlink 使整树删除被拒绝，普通兄弟和目标均保持。"""
    require_ntfs(tmp_path)
    tree = tmp_path / 'tree'
    tree.mkdir()
    sibling = tree / 'ordinary.bin'
    sibling.write_bytes(b'ordinary')
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'sentinel.bin'
    sentinel.write_bytes(b'outside')
    link = tree / 'link'
    make_symlink(link, outside if directory else sentinel, directory)
    try:
        with pytest.raises(winfs.UnsafeObjectError, match='整树含重解析对象'):
            winfs.remove_tree(str(tree))
        if directory:
            with pytest.raises(winfs.UnsafeObjectError, match='拒绝重解析对象'):
                winfs.write_exclusive(str(link / 'new.bin'), b'forbidden')
        else:
            with pytest.raises(FileExistsError):
                winfs.write_exclusive(str(link), b'forbidden')
        assert sibling.read_bytes() == b'ordinary'
        assert sentinel.read_bytes() == b'outside'
        assert set(outside.iterdir()) == {sentinel}
        assert link.is_symlink()
    finally:
        link.unlink()


@REAL_SYMLINK_ACCEPTANCE
@pytest.mark.parametrize('root_kind', ['program', 'screenshot'])
def test_real_symlink_roots_reject_save_and_cleanup(tmp_path, root_kind):
    """程序根或截图根为真实 symlink 时保存和清理均不得穿越。"""
    require_ntfs(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    program = tmp_path / 'program'
    if root_kind == 'program':
        link = program
        managed = outside / 'screenshot'
    else:
        program.mkdir()
        link = program / 'screenshot'
        managed = outside
    expired = managed / '2020_01_01'
    expired.mkdir(parents=True)
    sentinel = expired / 'sentinel.bin'
    sentinel.write_bytes(b'outside')
    make_symlink(link, outside, True)
    try:
        with pytest.raises(winfs.UnsafeObjectError, match='拒绝重解析对象'):
            winfs.list_names(str(program / 'screenshot'))
        policy = retention.RetentionPolicy(True, 1)
        outcome = retention.save_automatic(
            CaptureResult(b'fixture', 'window', 'fixture', 1, 1),
            retention.runtime_context(str(program), 'config.yaml'),
            'event', policy, today=TODAY)
        assert outcome.path is None
        assert outcome.warnings == ('截图保存失败：无法写入输出路径',)
        assert retention.cleanup_retention(str(program), policy, today=TODAY) == (
            '截图清理失败：无法安全枚举受管目录',)
        assert sentinel.read_bytes() == b'outside'
        assert set(managed.iterdir()) == {expired}
        assert link.is_symlink()
    finally:
        link.unlink()


@pytest.mark.parametrize('directory', [False, True], ids=['file', 'directory'])
def test_injected_other_reparse_attribute_rejects_identity(monkeypatch, tmp_path, directory):
    """注入通用重解析属性覆盖其他标签拒绝分支，并非真实文件系统 tag。"""
    require_ntfs(tmp_path)
    original = winfs._Info
    queried = []
    with winfs._open_path(str(tmp_path)) as handle:
        def other_reparse_info(value, pointer):
            """保留真实身份查询，仅注入不携带具体 tag 的重解析属性。"""
            result = original(value, pointer)
            assert result
            info = ctypes.cast(
                pointer, ctypes.POINTER(winfs.BY_HANDLE_FILE_INFORMATION)).contents
            info.attributes = winfs._REPARSE | (winfs._DIRECTORY if directory else 0)
            queried.append(value)
            return result

        monkeypatch.setattr(winfs, '_Info', other_reparse_info)
        with pytest.raises(winfs.UnsafeObjectError, match='拒绝重解析对象'):
            winfs._identity(handle)
        assert queried == [handle.value]


def test_injected_other_reparse_entry_preserves_tree(monkeypatch, tmp_path):
    """真实枚举记录注入通用重解析属性，非真实 FS tag，整树应保留。"""
    require_ntfs(tmp_path)
    tree = tmp_path / 'tree'
    tree.mkdir()
    target = tree / 'target.bin'
    target.write_bytes(b'original')
    original = winfs._entries

    def other_reparse_entries(handle):
        """仅改变真实目标记录属性，保持名称和文件标识。"""
        for name, attributes, file_id in original(handle):
            yield name, attributes | winfs._REPARSE, file_id

    monkeypatch.setattr(winfs, '_entries', other_reparse_entries)
    with pytest.raises(winfs.UnsafeObjectError, match='整树含重解析对象'):
        winfs.remove_tree(str(tree))
    assert target.read_bytes() == b'original'
