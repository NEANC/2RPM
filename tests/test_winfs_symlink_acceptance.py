#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""真实符号链接边界及不区分标签的重解析属性拒绝验收。"""

from contextlib import contextmanager
import ctypes
from datetime import date
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import warnings

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


@contextmanager
def make_symlink(link, target, directory):
    """保护创建、校验和使用全过程，仅非递归清理本次符号链接。"""
    assert target.is_absolute() and target.exists()
    assert target.is_dir() == directory
    assert link.is_absolute() and link.parent.is_dir()
    assert not os.path.lexists(link)
    primary_error = None
    try:
        try:
            os.symlink(target, link, target_is_directory=directory)
        except OSError as error:
            if error.winerror == 1314:
                pytest.skip('真实 symlink 未执行：缺少符号链接创建权限（WinError 1314），未提权')
            if error.winerror not in (87, 2):
                raise
            try:
                metadata = link.lstat()
                verified = (
                    metadata.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    and metadata.st_reparse_tag == stat.IO_REPARSE_TAG_SYMLINK
                    and link.is_symlink()
                    and link.samefile(target)
                    and (link.is_dir() if directory else link.is_file())
                )
            except OSError:
                verified = False
            if not verified:
                raise
            warnings.warn(
                f'原生创建报告 WinError {error.winerror}，'
                '但 symlink 标签、目标身份及类型均已核验，继续真实验收',
                RuntimeWarning, stacklevel=2,
            )
        metadata = link.lstat()
        assert metadata.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        assert metadata.st_reparse_tag == stat.IO_REPARSE_TAG_SYMLINK
        assert link.is_symlink()
        yield
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            try:
                metadata = link.lstat()
            except FileNotFoundError:
                pass
            else:
                if (metadata.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                        and metadata.st_reparse_tag == stat.IO_REPARSE_TAG_SYMLINK):
                    link.unlink()
        except OSError:
            if primary_error is None:
                raise
            primary_error.add_note('符号链接清理失败，链接可能残留')


@pytest.mark.parametrize('directory', [False, True], ids=['file', 'directory'])
@pytest.mark.parametrize('code', [87, 2])
@pytest.mark.parametrize('failure', [
    None, 'lstat', 'attributes', 'tag', 'is_symlink', 'samefile',
    'samefile_error', 'type', 'type_error',
])
def test_symlink_creation_error_requires_verified_link(
        monkeypatch, tmp_path, directory, code, failure):
    """仅完整核验通过才告警继续，否则保留原生异常及目标。"""
    target = tmp_path / 'target'
    if directory:
        target.mkdir()
    else:
        target.write_bytes(b'target')
    link = tmp_path / 'link'
    original_lstat = Path.lstat
    original_is_dir = Path.is_dir
    original_is_file = Path.is_file
    error = OSError('injected creation failure')
    error.winerror = code
    queried = []

    def create_then_fail(source, destination, target_is_directory):
        """模拟原生创建留下对象后报告错误。"""
        assert (source, destination, target_is_directory) == (target, link, directory)
        link.write_bytes(b'placeholder')
        raise error

    def query_link(path, *args, **kwargs):
        """区分报错后的首次核验与清理查询。"""
        if path != link:
            return original_lstat(path, *args, **kwargs)
        queried.append('lstat')
        if len(queried) == 1 and failure == 'lstat':
            raise FileNotFoundError('injected missing link')
        return SimpleNamespace(
            st_mode=stat.S_IFLNK,
            st_file_attributes=(0 if failure == 'attributes'
                                else stat.FILE_ATTRIBUTE_REPARSE_POINT),
            st_reparse_tag=(stat.IO_REPARSE_TAG_MOUNT_POINT if failure == 'tag'
                            else stat.IO_REPARSE_TAG_SYMLINK),
        )

    def same_target(path, other):
        """模拟目标身份不符或身份查询报错。"""
        assert (path, other) == (link, target)
        if failure == 'samefile_error':
            raise OSError('injected identity failure')
        return failure != 'samefile'

    def link_type(path, expected_directory):
        """模拟文件和目录类型核验，保留目标的真实类型。"""
        if path != link:
            return original_is_dir(path) if expected_directory else original_is_file(path)
        if failure == 'type_error':
            raise OSError('injected type failure')
        return (directory == expected_directory) != (failure == 'type')

    with monkeypatch.context() as patch:
        patch.setattr(os, 'symlink', create_then_fail)
        patch.setattr(Path, 'lstat', query_link)
        patch.setattr(Path, 'is_symlink', lambda path: failure != 'is_symlink')
        patch.setattr(Path, 'samefile', same_target)
        patch.setattr(Path, 'is_dir', lambda path: link_type(path, True))
        patch.setattr(Path, 'is_file', lambda path: link_type(path, False))
        if failure is None:
            with pytest.warns(RuntimeWarning, match=f'WinError {code}') as caught:
                with make_symlink(link, target, directory):
                    assert link.read_bytes() == b'placeholder'
            assert len(caught) == 1
            assert str(link) not in str(caught[0].message)
            assert str(target) not in str(caught[0].message)
        else:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                with pytest.raises(OSError) as raised:
                    with make_symlink(link, target, directory):
                        pytest.fail('核验失败不得进入主体')
            assert raised.value is error
            assert not caught
        assert len(queried) >= 2
    if failure in ('attributes', 'tag'):
        assert link.read_bytes() == b'placeholder'
    else:
        assert not os.path.lexists(link)
    assert target.is_dir() if directory else target.read_bytes() == b'target'


@pytest.mark.parametrize('directory', [False, True], ids=['file', 'directory'])
@pytest.mark.parametrize('failure', ['lstat', 'attributes', 'tag', 'is_symlink', 87, 2, 5, 1314])
def test_symlink_creation_failure_cleans_only_link(monkeypatch, tmp_path, directory, failure):
    """模拟创建后故障，清理链接占位物而不触碰真实外部目标。"""
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'sentinel.bin'
    sentinel.write_bytes(b'outside')
    target = outside if directory else sentinel
    link = tmp_path / 'link'
    original_lstat = Path.lstat
    original_unlink = Path.unlink
    created = False
    queries = 0
    removed = []

    def create_then_fail(source, destination, target_is_directory):
        """创建普通占位物，按需模拟原生 API 已创建但仍抛错。"""
        nonlocal created
        assert (source, destination, target_is_directory) == (target, link, directory)
        link.write_bytes(b'link placeholder')
        created = True
        if isinstance(failure, int):
            error = OSError('injected creation failure')
            error.winerror = failure
            raise error

    def injected_lstat(path, *args, **kwargs):
        """仅对已创建的占位路径注入链接身份及一次校验故障。"""
        nonlocal queries
        if path != link or not created:
            return original_lstat(path, *args, **kwargs)
        queries += 1
        if queries == 1 and failure == 'lstat':
            raise OSError('injected lstat failure')
        return SimpleNamespace(
            st_mode=stat.S_IFLNK,
            st_file_attributes=(0 if queries == 1 and failure == 'attributes'
                                else stat.FILE_ATTRIBUTE_REPARSE_POINT),
            st_reparse_tag=(0 if queries == 1 and failure == 'tag'
                            else stat.IO_REPARSE_TAG_SYMLINK),
        )

    def remove_link(path, *args, **kwargs):
        """记录非递归删除并拒绝任何目标删除。"""
        assert path == link
        removed.append(path)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, 'symlink', create_then_fail)
    monkeypatch.setattr(Path, 'lstat', injected_lstat)
    monkeypatch.setattr(Path, 'unlink', remove_link)
    if failure == 'is_symlink':
        monkeypatch.setattr(Path, 'is_symlink', lambda path: False)
    expected = (pytest.skip.Exception if failure == 1314 else
                OSError if isinstance(failure, int) or failure == 'lstat' else AssertionError)
    with pytest.raises(expected):
        with make_symlink(link, target, directory):
            pytest.fail('故障必须在进入验收主体前传播')
    assert removed == [link]
    assert not os.path.lexists(link)
    assert sentinel.read_bytes() == b'outside'
    assert set(outside.iterdir()) == {sentinel}


@pytest.mark.parametrize('directory', [False, True], ids=['file', 'directory'])
@pytest.mark.parametrize('cleanup_failure', ['lstat', 'unlink'])
@pytest.mark.parametrize('failure', [87, 2, KeyboardInterrupt, SystemExit, AssertionError, None])
def test_symlink_cleanup_failure_preserves_exception(
        monkeypatch, tmp_path, directory, cleanup_failure, failure):
    """双重故障保留主异常身份并脱敏，无主异常则传播清理失败。"""
    target = tmp_path / 'target'
    if directory:
        target.mkdir()
    else:
        target.write_bytes(b'target')
    link = tmp_path / 'link'
    original_lstat = Path.lstat
    created = False
    cleaning = False
    operations = []
    primary_error = None
    if isinstance(failure, int):
        primary_error = OSError('injected creation failure')
        primary_error.winerror = failure
    elif failure is not None:
        primary_error = failure('injected body failure')
    cleanup_error = OSError(5, 'sensitive cleanup detail', str(link))

    def create_link(source, destination, target_is_directory):
        """用占位物模拟链接创建，并按需抛出既定原生异常。"""
        nonlocal created, cleaning
        assert (source, destination, target_is_directory) == (target, link, directory)
        link.write_bytes(b'placeholder')
        created = True
        if isinstance(failure, int):
            cleaning = True
            raise primary_error

    def query_link(path, *args, **kwargs):
        """模拟链接元数据，仅在清理阶段注入查询失败。"""
        if path != link or not created:
            return original_lstat(path, *args, **kwargs)
        if cleaning:
            operations.append('lstat')
            if cleanup_failure == 'lstat':
                raise cleanup_error
        return SimpleNamespace(
            st_mode=stat.S_IFLNK,
            st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            st_reparse_tag=stat.IO_REPARSE_TAG_SYMLINK,
        )

    def remove_link(path, *args, **kwargs):
        """仅允许删除本次链接，并模拟删除故障。"""
        assert path == link
        operations.append('unlink')
        raise cleanup_error

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, 'symlink', create_link)
            patch.setattr(Path, 'lstat', query_link)
            patch.setattr(Path, 'is_symlink', lambda path: path == link)
            patch.setattr(Path, 'unlink', remove_link)
            with pytest.raises(BaseException) as caught:
                with make_symlink(link, target, directory):
                    cleaning = True
                    if primary_error is not None:
                        raise primary_error
        assert caught.value is (primary_error if primary_error is not None else cleanup_error)
        if primary_error is not None:
            assert caught.value.__notes__ == ['符号链接清理失败，链接可能残留']
            assert caught.value.__context__ is None
        expected_operations = ['lstat'] if isinstance(failure, int) else []
        expected_operations += (['lstat'] if cleanup_failure == 'lstat'
                                else ['lstat', 'unlink'])
        assert operations == expected_operations
        assert link.read_bytes() == b'placeholder'
        assert target.is_dir() if directory else target.read_bytes() == b'target'
    finally:
        if created:
            link.unlink()
    assert not os.path.lexists(link)


@pytest.mark.parametrize('directory', [False, True], ids=['file', 'directory'])
def test_symlink_preexisting_path_is_not_owned(monkeypatch, tmp_path, directory):
    """预存路径不属于本次创建，不得调用创建或删除。"""
    target = tmp_path / 'target'
    target.write_bytes(b'target')
    link = tmp_path / 'link'
    if directory:
        link.mkdir()
    else:
        link.write_bytes(b'existing')

    def unexpected_operation(*args, **kwargs):
        """任何创建或删除均表示越过归属边界。"""
        pytest.fail('不得操作预存路径')

    monkeypatch.setattr(os, 'symlink', unexpected_operation)
    monkeypatch.setattr(Path, 'unlink', unexpected_operation)
    with pytest.raises(AssertionError):
        with make_symlink(link, target, False):
            pytest.fail('不得接受预存路径')
    assert link.is_dir() if directory else link.read_bytes() == b'existing'
    assert target.read_bytes() == b'target'


@pytest.mark.parametrize('directory', [False, True], ids=['file', 'directory'])
def test_symlink_failed_creation_preserves_nonlink(monkeypatch, tmp_path, directory):
    """创建失败后若路径不是链接，不能将普通对象误当链接删除。"""
    target = tmp_path / 'target'
    target.write_bytes(b'target')
    link = tmp_path / 'link'

    def create_nonlink(*args, **kwargs):
        """模拟失败调用留下普通对象而非重解析链接。"""
        if directory:
            link.mkdir()
        else:
            link.write_bytes(b'ordinary')
        error = OSError('injected creation failure')
        error.winerror = 87
        raise error

    monkeypatch.setattr(os, 'symlink', create_nonlink)
    with pytest.raises(OSError):
        with make_symlink(link, target, False):
            pytest.fail('创建失败必须传播')
    assert link.is_dir() if directory else link.read_bytes() == b'ordinary'
    assert target.read_bytes() == b'target'


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
    with make_symlink(link, outside if directory else sentinel, directory):
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
    with make_symlink(link, outside, True):
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
