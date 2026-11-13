#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""配置文档的保真读取、补丁及原子保存测试。"""

import hashlib
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.config import get_default_config
from modules.config_rules import Change
from modules.config_document import (
    ConfigConflictError,
    ConfigReadError,
    ConfigSaveError,
    new_document,
    normalize_config_path,
    patch_document,
    read_document,
    save_document,
)


SOURCE = (
    '# 保留说明\nmonitor:\n  target: "程序.exe" # 目标说明\n'
    'custom:\n  keep: 原样\n  token: "${TOKEN}"\n'
)


def _source_file(tmp_path):
    """创建含中文路径、注释和未知字段的真实配置。"""
    path = tmp_path / '中文 配置.yaml'
    path.write_text(SOURCE, encoding='utf-8')
    return path


def test_read_validate_never_write(tmp_path, monkeypatch):
    """读取与候选诊断不修改原文件，也不创建临时文件。"""
    from modules.config_rules import analyze_config

    path = _source_file(tmp_path)
    original = path.read_bytes()
    monkeypatch.setattr('os.replace', lambda *_: pytest.fail('读取发生写入'))
    monkeypatch.setattr('tempfile.mkstemp', lambda **_: pytest.fail('读取创建临时文件'))

    document = read_document(path)
    analyze_config(document.tree, config_dir=tmp_path, defaults=get_default_config())

    assert path.read_bytes() == original
    assert document.fingerprint == hashlib.sha256(original).hexdigest()
    assert list(tmp_path.iterdir()) == [path]


def test_save_preserves_unmanaged_fields(tmp_path):
    """只修改明确的字段，保留注释、引号、未知字段与环境变量。"""
    path = _source_file(tmp_path)
    document = read_document(path)
    patched = patch_document(document, (
        Change(('monitor', 'target'), '程序.exe', '新程序.exe', 'set'),
    ))
    saved = save_document(patched, path, expected_fingerprint=document.fingerprint)
    text = path.read_text(encoding='utf-8')

    assert document.tree['monitor']['target'] == '程序.exe'
    assert saved.tree['custom']['keep'] == '原样'
    assert '# 保留说明' in text
    assert '# 目标说明' in text
    assert 'target: "新程序.exe"' in text
    assert '${TOKEN}' in text
    assert saved.fingerprint == hashlib.sha256(path.read_bytes()).hexdigest()


def test_external_edit_conflicts(tmp_path):
    """外部编辑原目标后不覆盖，保留调用者基线。"""
    path = _source_file(tmp_path)
    document = read_document(path)
    baseline = document.fingerprint
    external_editor_bytes = b'monitor:\n  target: other.exe\n'
    path.write_bytes(external_editor_bytes)

    with pytest.raises(ConfigConflictError):
        save_document(document, path, expected_fingerprint=document.fingerprint)

    assert path.read_bytes() == external_editor_bytes
    assert document.fingerprint == baseline


@pytest.mark.parametrize('stage', ['serialize', 'close', 'replace'])
def test_failed_save_keeps_original(tmp_path, monkeypatch, stage):
    """序列化、关闭和替换失败都不改原文件或留下临时文件。"""
    import modules.config_document as module

    path = _source_file(tmp_path)
    original_bytes = path.read_bytes()
    document = read_document(path)
    if stage == 'serialize':
        monkeypatch.setattr(module.YAML, 'dump', lambda *args: (_ for _ in ()).throw(OSError('private-secret')))
    elif stage == 'close':
        real_fdopen = module.os.fdopen

        class FailingClose:
            """模拟真实文件关闭后报告失败。"""

            def __init__(self, stream):
                """保存被包装的文件流。"""
                self.stream = stream

            def write(self, text):
                """把 YAML 写入传递给真实文件流。"""
                return self.stream.write(text)

            def close(self):
                """关闭真实流后模拟关闭错误。"""
                self.stream.close()
                raise OSError('private-secret')

        monkeypatch.setattr(module.os, 'fdopen', lambda *a, **kw: FailingClose(real_fdopen(*a, **kw)))
    else:
        monkeypatch.setattr(module.os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('private-secret')))

    with pytest.raises(ConfigSaveError) as error:
        save_document(document, path, expected_fingerprint=document.fingerprint)

    assert 'private-secret' not in str(error.value)
    assert path.read_bytes() == original_bytes
    assert document.fingerprint == hashlib.sha256(original_bytes).hexdigest()
    assert list(tmp_path.iterdir()) == [path]


def test_save_as_requires_destination_fingerprint(tmp_path):
    """已有目标不能用 None 覆盖，确认后目标改变也必须冲突。"""
    source = _source_file(tmp_path)
    document = read_document(source)
    destination = tmp_path / '另存.yaml'
    destination.write_text('old: yes\n', encoding='utf-8')
    expected = hashlib.sha256(destination.read_bytes()).hexdigest()

    with pytest.raises(ConfigConflictError):
        save_document(document, destination, expected_fingerprint=None)
    destination.write_text('external: yes\n', encoding='utf-8')
    with pytest.raises(ConfigConflictError):
        save_document(document, destination, expected_fingerprint=expected)

    assert destination.read_text(encoding='utf-8') == 'external: yes\n'
    assert document.path == source


def test_new_destination_created_after_confirmation_conflicts(tmp_path):
    """期望不存在的目标被他人创建时拒绝写入。"""
    destination = tmp_path / 'new.yaml'
    document = new_document()
    destination.write_text('other: true\n', encoding='utf-8')
    with pytest.raises(ConfigConflictError):
        save_document(document, destination, expected_fingerprint=None)
    assert destination.read_text(encoding='utf-8') == 'other: true\n'


def test_save_as_and_subsequent_save_use_new_fingerprint(tmp_path):
    """覆盖确认后的另存为返回新基线，连续保存使用新指纹。"""
    source = _source_file(tmp_path)
    document = read_document(source)
    destination = tmp_path / '另存.yaml'
    destination.write_text('old: true\n', encoding='utf-8')
    expected = hashlib.sha256(destination.read_bytes()).hexdigest()

    saved = save_document(document, destination, expected_fingerprint=expected)
    again = save_document(saved, destination, expected_fingerprint=saved.fingerprint)

    assert again.path == destination
    assert again.fingerprint == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert source.read_text(encoding='utf-8') == SOURCE


def test_new_document_saves_to_absent_destination(tmp_path):
    """新文档只在显式保存时创建目标并返回有效指纹。"""
    destination = tmp_path / '新建.yaml'
    document = new_document()
    assert document.path is None
    assert not destination.exists()

    saved = save_document(document, destination, expected_fingerprint=None)

    assert saved.path == destination
    assert saved.fingerprint == hashlib.sha256(destination.read_bytes()).hexdigest()


def test_runtime_never_persisted(tmp_path):
    """内存运行时信息不进入保存树，也不落盘。"""
    path = _source_file(tmp_path)
    document = read_document(path)
    document.tree['_runtime'] = {'secret': 'hidden'}

    saved = save_document(document, path, expected_fingerprint=document.fingerprint)

    assert '_runtime' not in saved.tree
    assert '_runtime' not in path.read_text(encoding='utf-8')
    assert '_runtime' in document.tree


@pytest.mark.parametrize('name, final', [
    ('a', 'a.yaml'), ('a.txt', 'a.txt.yaml'), ('a.yaml', 'a.yaml'),
    ('a.yml', 'a.yml'), ('a.YAML', 'a.YAML.yaml'),
    ('a.YML', 'a.YML.yaml'), ('a.YmL', 'a.YmL.yaml'),
])
def test_normalize_path_matches_cli_case_sensitive_suffix(tmp_path, name, final):
    """相对路径在程序目录下按 CLI 区分大小写的尾串规则补后缀。"""
    path = normalize_config_path(Path(name), program_dir=tmp_path)
    assert path == tmp_path / final
    assert normalize_config_path(path, program_dir=tmp_path) == path


@pytest.mark.parametrize('name', ['a', 'a.txt', 'a.yaml', 'a.yml',
                                  'a.YAML', 'a.YML', 'a.YmL'])
def test_normalized_path_matches_cli_loader(tmp_path, monkeypatch, name):
    """所有大小写后缀的结果与真实 CLI 入口传给加载器的路径一致。"""
    from tests.test_config_cli_entry import run_main

    monkeypatch.setattr('tests.test_config_cli_entry.PROGRAM_DIR', str(tmp_path))
    loader = run_main(monkeypatch, ['-c', name])
    normalized = normalize_config_path(Path(name), program_dir=tmp_path)
    assert os.path.normcase(loader.call_args.args[0]) == os.path.normcase(str(normalized))


def test_patch_only_explicit_changes(tmp_path):
    """路径补丁支持列表索引与删除，原始文档保持不变。"""
    path = tmp_path / 'list.yaml'
    path.write_text('entries:\n  - name: first\n  - name: second\nkeep: yes\n', encoding='utf-8')
    document = read_document(path)
    patched = patch_document(document, (
        Change(('entries', 1, 'name'), 'second', 'changed', 'set'),
        Change(('keep',), True, None, 'delete'),
    ))
    assert patched.tree['entries'][1]['name'] == 'changed'
    assert 'keep' not in patched.tree
    assert document.tree['entries'][1]['name'] == 'second'
    assert 'keep' in document.tree


def test_read_failure_is_safe(tmp_path):
    """缺失或非法 YAML 返回安全读取错误而不回显文件内容。"""
    path = tmp_path / 'bad.yaml'
    path.write_text('private-secret: [\n', encoding='utf-8')
    with pytest.raises(ConfigReadError) as error:
        read_document(path)
    assert 'private-secret' not in str(error.value)


def test_patch_mapping_keeps_unknown_fields_shared_by_yaml_alias(tmp_path):
    """管理映射替换不得清除别名映射中的未知字段。"""
    path = tmp_path / 'alias.yaml'
    path.write_text('shared: &options\n  known: old\n  custom: retained\n'
                    'monitor:\n  common: *options\n', encoding='utf-8')
    document = read_document(path)
    patched = patch_document(document, (
        Change(('monitor', 'common'), document.tree['monitor']['common'],
               {'known': 'new'}, 'set'),
    ))
    saved = save_document(patched, path, expected_fingerprint=document.fingerprint)

    assert saved.tree['monitor']['common']['custom'] == 'retained'
    assert saved.tree['shared']['custom'] == 'retained'
    assert read_document(path).tree['monitor']['common']['custom'] == 'retained'


@pytest.mark.parametrize('field_path, replacement', [
    (('monitor', 'common', 'known'), 'new'),
    (('monitor', 'common'), {'known': 'new'}),
])
def test_patch_alias_path_does_not_modify_anchor(tmp_path, field_path, replacement):
    """指定路径补丁只修改别名分支，原锚点及注释保持原样。"""
    path = tmp_path / 'alias.yaml'
    path.write_text('# 顶部说明\nshared: &base\n  known: old # 锚点说明\n'
                    '  custom: retained\nmonitor:\n  common: *base\n', encoding='utf-8')
    document = read_document(path)
    patched = patch_document(document, (
        Change(field_path, 'old', replacement, 'set'),
    ))
    saved = save_document(patched, path, expected_fingerprint=document.fingerprint)
    text = path.read_text(encoding='utf-8')

    assert document.tree['shared']['known'] == 'old'
    assert saved.tree['shared']['known'] == 'old'
    assert saved.tree['monitor']['common']['known'] == 'new'
    assert saved.tree['monitor']['common']['custom'] == 'retained'
    assert read_document(path).tree['shared']['known'] == 'old'
    assert read_document(path).tree['monitor']['common']['known'] == 'new'
    assert '# 顶部说明' in text
    assert '# 锚点说明' in text
    assert '&base' in text


def test_patch_nested_anchor_under_alias_preserves_other_branches(tmp_path):
    """外层别名中的内层锚点可修改，定义处及非目标别名不变。"""
    path = tmp_path / 'nested_alias.yaml'
    path.write_text('shared: &base\n  inner: &inside\n    known: old\n'
                    '    custom: retained\nmonitor:\n  common: *base\n'
                    'other: *base\n', encoding='utf-8')
    document = read_document(path)

    patched = patch_document(document, (
        Change(('monitor', 'common', 'inner', 'known'), 'old', 'new', 'set'),
    ))
    saved = save_document(patched, path, expected_fingerprint=document.fingerprint)
    reloaded = read_document(path)

    assert saved.tree['monitor']['common']['inner']['known'] == 'new'
    assert saved.tree['monitor']['common']['inner']['custom'] == 'retained'
    assert reloaded.tree['shared']['inner']['known'] == 'old'
    assert reloaded.tree['other']['inner']['known'] == 'old'
    assert reloaded.tree['other'] is reloaded.tree['shared']
    assert document.tree['monitor']['common']['inner']['known'] == 'old'


def test_save_rejects_symlink_in_destination_path(tmp_path):
    """文件链接或父目录链接均不得成为配置保存路径。"""
    target = tmp_path / 'outside.yaml'
    target.write_text('outside: true\n', encoding='utf-8')
    parent = tmp_path / 'actual'
    parent.mkdir()
    file_link = tmp_path / 'linked.yaml'
    directory_link = tmp_path / 'linked_dir'
    try:
        file_link.symlink_to(target)
        directory_link.symlink_to(parent, target_is_directory=True)
    except OSError as error:
        pytest.skip(f'无法创建符号链接: {error.winerror}')
    for destination in (file_link, directory_link / 'new.yaml'):
        with pytest.raises(ConfigSaveError):
            save_document(new_document(), destination, expected_fingerprint=None)
    assert target.read_text(encoding='utf-8') == 'outside: true\n'
    assert list(parent.iterdir()) == []


def test_save_rejects_junction_parent(tmp_path):
    """经过 junction 的保存即使目标不存在也必须拒绝。"""
    from tests.test_screenshot_winfs import make_junction

    actual = tmp_path / 'actual'
    actual.mkdir()
    link = tmp_path / 'junction'
    try:
        make_junction(link, actual)
    except OSError as error:
        pytest.skip(f'无法创建 junction: {error}')
    if not link.is_dir():
        pytest.skip('沙盒 junction 创建后不可访问')
    assert link.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    with pytest.raises(ConfigSaveError):
        save_document(new_document(), link / 'new.yaml', expected_fingerprint=None)
    assert list(actual.iterdir()) == []


def test_save_rejects_any_reparse_tag_in_parent(tmp_path, monkeypatch):
    """不依赖符号链接标签，任意重解析属性均拒绝保存。"""
    parent = tmp_path / 'redirected'
    parent.mkdir()
    destination = parent / 'new.yaml'
    original_lstat = Path.lstat

    def tagged_lstat(path):
        """给父目录注入 Windows junction 的重解析属性。"""
        if path == parent:
            return SimpleNamespace(st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
                                   st_reparse_tag=stat.IO_REPARSE_TAG_MOUNT_POINT)
        return original_lstat(path)

    monkeypatch.setattr(Path, 'lstat', tagged_lstat)
    with pytest.raises(ConfigSaveError):
        save_document(new_document(), destination, expected_fingerprint=None)
    assert not destination.exists()


def test_cleanup_failure_does_not_hide_save_error(tmp_path, monkeypatch):
    """替换失败后的清理异常不能覆盖安全的保存异常。"""
    import modules.config_document as module

    path = _source_file(tmp_path)
    original = path.read_bytes()
    document = read_document(path)

    def fail_replace(*args):
        """模拟包含敏感信息的替换失败。"""
        raise OSError('private-replace')

    def fail_unlink(*args):
        """模拟临时文件清理失败。"""
        raise OSError('private-cleanup')

    monkeypatch.setattr(module.os, 'replace', fail_replace)
    monkeypatch.setattr(module.os, 'unlink', fail_unlink)
    with pytest.raises(ConfigSaveError, match='无法保存配置文档') as error:
        save_document(document, path, expected_fingerprint=document.fingerprint)
    assert 'private-' not in str(error.value)
    assert path.read_bytes() == original


def test_cleanup_failure_without_active_exception_raises_safe_save_error(tmp_path, monkeypatch):
    """清理阶段没有活动异常时仍转换成安全保存异常。"""
    import modules.config_document as module

    path = _source_file(tmp_path)
    original = path.read_bytes()
    document = read_document(path)

    with monkeypatch.context() as patcher:
        patcher.setattr(module.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('private-replace')))
        patcher.setattr(module.os, 'unlink', lambda *args: (_ for _ in ()).throw(OSError('private-cleanup')))
        patcher.setattr(module.sys, 'exception', lambda: None)
        with pytest.raises(ConfigSaveError, match='无法清理配置临时文件') as error:
            save_document(document, path, expected_fingerprint=document.fingerprint)

    assert 'private-cleanup' not in str(error.value)
    assert path.read_bytes() == original
    for temporary in tmp_path.glob(f'.{path.name}.*.tmp'):
        temporary.unlink()
