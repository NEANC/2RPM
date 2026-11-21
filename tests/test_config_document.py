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
    new_blank_document,
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


@pytest.mark.parametrize('external', [b'external: true\n', None])
def test_external_change_during_serialization_blocks_replace(tmp_path, monkeypatch, external):
    """生成临时文件期间目标被改写或删除时拒绝覆盖并清理临时文件。"""
    import modules.config_document as module

    path = _source_file(tmp_path)
    document = read_document(path)
    real_dump = module.YAML.dump

    def changing_dump(yaml, tree, stream):
        """写完临时文件后模拟不协作的外部写入。"""
        result = real_dump(yaml, tree, stream)
        if external is None:
            path.unlink()
        else:
            path.write_bytes(external)
        return result

    monkeypatch.setattr(module.YAML, 'dump', changing_dump)
    with pytest.raises(ConfigConflictError):
        save_document(document, path, expected_fingerprint=document.fingerprint)
    assert (path.read_bytes() if path.exists() else None) == external
    assert list(tmp_path.iterdir()) == ([path] if external is not None else [])


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


def test_patch_nested_alias_keeps_external_inside_reference_on_reload(tmp_path):
    """复制外层别名时不把外部的内层引用重绑定到新分支。"""
    path = tmp_path / 'nested_reference.yaml'
    path.write_text('shared: &base\n  inner: &inside\n    known: old\n  again: *inside\n  edited: old\n'
                    'monitor:\n  common: *base\nother: *inside\n', encoding='utf-8')
    original = read_document(path)
    patched = patch_document(original, (
        Change(('monitor', 'common', 'edited'), 'old', 'new', 'set'),
    ))

    save_document(patched, path, expected_fingerprint=original.fingerprint)
    reloaded = read_document(path)

    text = path.read_text(encoding='utf-8')
    assert text.count('&inside\n') == 1
    assert '&inside_1' in text
    assert reloaded.tree['monitor']['common']['edited'] == 'new'
    assert reloaded.tree['monitor']['common']['again'] is reloaded.tree['monitor']['common']['inner']
    assert reloaded.tree['shared']['inner']['known'] == 'old'
    assert reloaded.tree['other']['known'] == 'old'
    assert reloaded.tree['other'] is reloaded.tree['shared']['inner']


def test_patch_list_alias_keeps_definition_on_reload(tmp_path):
    """列表别名分支修改后定义处与其他引用保持原值。"""
    path = tmp_path / 'list_reference.yaml'
    path.write_text('shared: &targets\n  - name: old\n    custom: retained\n'
                    'monitor:\n  targets: *targets\nother: *targets\n', encoding='utf-8')
    original = read_document(path)
    patched = patch_document(original, (
        Change(('monitor', 'targets', 0, 'name'), 'old', 'new', 'set'),
    ))

    save_document(patched, path, expected_fingerprint=original.fingerprint)
    reloaded = read_document(path)

    assert reloaded.tree['monitor']['targets'][0]['name'] == 'new'
    assert reloaded.tree['monitor']['targets'][0]['custom'] == 'retained'
    assert reloaded.tree['shared'][0]['name'] == 'old'
    assert reloaded.tree['other'] is reloaded.tree['shared']
    assert original.tree['shared'][0]['name'] == 'old'


def test_patch_nested_scalar_anchor_keeps_external_reference_on_reload(tmp_path):
    """复制分支内标量锚点重命名，外部引用仍指向原定义。"""
    path = tmp_path / 'scalar_reference.yaml'
    path.write_text('shared: &base\n  label: &inside old\n  again: *inside\n  edited: old\n'
                    'monitor:\n  common: *base\nother: *inside\n', encoding='utf-8')
    original = read_document(path)
    patched = patch_document(original, (
        Change(('monitor', 'common', 'edited'), 'old', 'new', 'set'),
    ))

    save_document(patched, path, expected_fingerprint=original.fingerprint)
    reloaded = read_document(path)
    text = path.read_text(encoding='utf-8')

    assert text.count('&inside ') == 1
    assert '&inside_1' in text
    assert reloaded.tree['monitor']['common']['again'] is reloaded.tree['monitor']['common']['label']
    assert reloaded.tree['other'] is reloaded.tree['shared']['label']
    assert reloaded.tree['other'] == 'old'
    assert reloaded.tree['monitor']['common']['edited'] == 'new'


@pytest.mark.parametrize('value', ['42', 'true', '3.5'])
def test_patch_nested_non_string_scalar_anchor_is_unique(tmp_path, value):
    """复制别名分支时数值与布尔标量锚点不与原锚点重名。"""
    path = tmp_path / 'scalar_reference.yaml'
    path.write_text(f'shared: &base\n  number: &inside {value}\n  again: *inside\n'
                    '  edited: old\nmonitor:\n  common: *base\nother: *inside\n',
                    encoding='utf-8')
    original = read_document(path)
    patched = patch_document(original, (
        Change(('monitor', 'common', 'edited'), 'old', 'new', 'set'),
    ))

    save_document(patched, path, expected_fingerprint=original.fingerprint)
    reloaded = read_document(path).tree
    text = path.read_text(encoding='utf-8')

    assert text.count('&inside ') == 1
    assert '&inside_1' in text
    assert reloaded['other'] is reloaded['shared']['number']
    assert reloaded['monitor']['common']['again'] is reloaded['monitor']['common']['number']
    assert reloaded['monitor']['common']['edited'] == 'new'


def test_set_whole_list_preserves_unknown_keys_after_reorder(tmp_path):
    """整段列表更新通过稳定身份匹配项目，保留每项未知字段。"""
    path = tmp_path / 'list_set.yaml'
    path.write_text('monitor:\n  targets:\n    - name: alpha\n      enabled: false\n'
                    '      custom: first\n    - name: beta\n      enabled: false\n'
                    '      custom: second\n', encoding='utf-8')
    original = read_document(path)
    patched = patch_document(original, (
        Change(('monitor', 'targets'), None, [
            {'name': 'beta', 'enabled': True},
            {'name': 'alpha', 'enabled': True},
        ], 'set'),
    ))

    save_document(patched, path, expected_fingerprint=original.fingerprint)
    targets = read_document(path).tree['monitor']['targets']

    assert [(item['name'], item['custom'], item['enabled']) for item in targets] == [
        ('beta', 'second', True), ('alpha', 'first', True),
    ]


def test_image_hosts_normalization_accepts_reordered_saved_slots(tmp_path):
    """已保存的图床交换后仍可应用格式规范化并保留每项未知字段。"""
    path = tmp_path / 'hosts.yaml'
    path.write_text('push:\n  screenshot:\n    image_host:\n'
                    '      - provider: catbox\n        custom: second\n'
                    '      - provider: boltp\n        token: secret\n        custom: first\n', encoding='utf-8')
    document = read_document(path)
    before = document.tree['push']['screenshot']['image_host']
    after = [dict(item) for item in before]
    patched = patch_document(document, (
        Change(('push', 'screenshot', 'image_host'), before, after, 'set'),
    ))
    save_document(patched, path, expected_fingerprint=document.fingerprint)
    hosts = read_document(path).tree['push']['screenshot']['image_host']
    assert [(item['provider'], item['custom']) for item in hosts] == [
        ('catbox', 'second'), ('boltp', 'first')]


def test_same_provider_hosts_reorder_matches_complete_values(tmp_path):
    """相同 provider 的完整条目按值对应，重排不丢未知字段。"""
    path = tmp_path / 'same_provider.yaml'
    path.write_text('push:\n  screenshot:\n    image_host:\n'
                    '      - provider: catbox\n        token: alpha\n        custom: first\n'
                    '      - provider: catbox\n        token: beta\n        custom: second\n',
                    encoding='utf-8')
    document = read_document(path)
    before = document.tree['push']['screenshot']['image_host']
    after = [dict(before[1]), dict(before[0])]
    patched = patch_document(document, (
        Change(('push', 'screenshot', 'image_host'), before, after, 'set'),
    ))
    save_document(patched, path, expected_fingerprint=document.fingerprint)

    hosts = read_document(path).tree['push']['screenshot']['image_host']
    assert [(item['token'], item['custom']) for item in hosts] == [
        ('beta', 'second'), ('alpha', 'first')]


def test_same_provider_hosts_reject_ambiguous_partial_values(tmp_path):
    """同 provider 且没有完整对应关系时不猜测未知字段归属。"""
    path = tmp_path / 'ambiguous_hosts.yaml'
    path.write_text('push:\n  screenshot:\n    image_host:\n'
                    '      - provider: catbox\n        token: same\n        custom: first\n'
                    '      - provider: catbox\n        token: same\n        custom: second\n',
                    encoding='utf-8')
    document = read_document(path)
    original = path.read_bytes()
    with pytest.raises(ConfigSaveError):
        patch_document(document, (
            Change(('push', 'screenshot', 'image_host'), None,
                   [{'provider': 'catbox', 'token': 'same'},
                    {'provider': 'catbox', 'token': 'same'}], 'set'),
        ))
    assert path.read_bytes() == original


def test_set_whole_list_rejects_ambiguous_match_without_touching_file(tmp_path):
    """多项没有可靠对应关系时拒绝保存，原文件及指纹不变。"""
    path = tmp_path / 'ambiguous_list.yaml'
    path.write_text('monitor:\n  targets:\n    - enabled: false\n      custom: first\n'
                    '    - enabled: false\n      custom: second\n', encoding='utf-8')
    original_bytes = path.read_bytes()
    original = read_document(path)

    with pytest.raises(ConfigSaveError) as error:
        patched = patch_document(original, (
            Change(('monitor', 'targets'), None, [
                {'enabled': True}, {'enabled': True},
            ], 'set'),
        ))
        save_document(patched, path, expected_fingerprint=original.fingerprint)

    assert 'first' not in str(error.value)
    assert path.read_bytes() == original_bytes
    assert original.fingerprint == hashlib.sha256(original_bytes).hexdigest()
    assert list(tmp_path.iterdir()) == [path]


def test_set_whole_list_rejects_changed_identity_without_touching_file(tmp_path):
    """原项目身份变化时不能猜测未知字段属于哪个新项目。"""
    path = tmp_path / 'renamed_list.yaml'
    path.write_text('monitor:\n  targets:\n    - name: old\n      custom: retained\n', encoding='utf-8')
    original = read_document(path)
    before = path.read_bytes()

    with pytest.raises(ConfigSaveError):
        patched = patch_document(original, (
            Change(('monitor', 'targets'), None, [{'name': 'new'}], 'set'),
        ))
        save_document(patched, path, expected_fingerprint=original.fingerprint)

    assert path.read_bytes() == before


def test_set_whole_list_alias_preserves_definition_and_unknown_keys(tmp_path):
    """整段替换列表别名时定义与外部引用仍不变。"""
    path = tmp_path / 'list_alias_set.yaml'
    path.write_text('shared: &targets\n  - name: alpha\n    custom: retained\n'
                    'monitor:\n  targets: *targets\nother: *targets\n', encoding='utf-8')
    original = read_document(path)
    patched = patch_document(original, (
        Change(('monitor', 'targets'), None, [{'name': 'alpha', 'enabled': True}], 'set'),
    ))

    save_document(patched, path, expected_fingerprint=original.fingerprint)
    reloaded = read_document(path).tree

    assert reloaded['monitor']['targets'][0]['custom'] == 'retained'
    assert reloaded['monitor']['targets'][0]['enabled'] is True
    assert reloaded['shared'][0]['name'] == 'alpha'
    assert 'enabled' not in reloaded['shared'][0]
    assert reloaded['other'] is reloaded['shared']


def test_set_whole_list_rejects_unmatched_unknown_item(tmp_path):
    """新列表不能凭空丢弃含未知键的旧项目。"""
    path = tmp_path / 'dropped_list.yaml'
    path.write_text('monitor:\n  targets:\n    - name: alpha\n      custom: retained\n', encoding='utf-8')
    original = read_document(path)
    before = path.read_bytes()

    with pytest.raises(ConfigSaveError):
        patched = patch_document(original, (
            Change(('monitor', 'targets'), None, [], 'set'),
        ))
        save_document(patched, path, expected_fingerprint=original.fingerprint)

    assert path.read_bytes() == before


def test_set_whole_list_rejects_unmatched_mixed_items(tmp_path):
    """列表混有标量时也不能静默丢弃映射项目的未知键。"""
    path = tmp_path / 'mixed_list.yaml'
    path.write_text('monitor:\n  targets:\n    - name: alpha\n      custom: retained\n'
                    '    - legacy\n', encoding='utf-8')
    original = read_document(path)
    before = path.read_bytes()

    with pytest.raises(ConfigSaveError):
        patched = patch_document(original, (
            Change(('monitor', 'targets'), None, ['legacy'], 'set'),
        ))
        save_document(patched, path, expected_fingerprint=original.fingerprint)

    assert path.read_bytes() == before


def test_set_whole_mixed_list_rejects_even_when_scalar_is_retained(tmp_path):
    """从纯标量列表变成混合列表时也拒绝歧义整段替换。"""
    path = tmp_path / 'mixed_list.yaml'
    path.write_text('monitor:\n  targets:\n    - legacy\n    - previous\n',
                    encoding='utf-8')
    original = read_document(path)
    before = path.read_bytes()

    with pytest.raises(ConfigSaveError):
        patched = patch_document(original, (
            Change(('monitor', 'targets'), None,
                   ['legacy', {'name': 'alpha', 'enabled': True}], 'set'),
        ))
        save_document(patched, path, expected_fingerprint=original.fingerprint)

    assert path.read_bytes() == before
    assert original.fingerprint == hashlib.sha256(before).hexdigest()
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('field, value', [('name', '[alpha]'), ('id', '{part: alpha}')])
def test_set_whole_list_rejects_unhashable_identity_safely(tmp_path, field, value):
    """非哈希身份返回安全保存错误，绝不泄露底层异常或写盘。"""
    path = tmp_path / 'unhashable_list.yaml'
    path.write_text(f'monitor:\n  targets:\n    - {field}: {value}\n'
                    '      custom: retained\n', encoding='utf-8')
    original = read_document(path)
    before = path.read_bytes()

    with pytest.raises(ConfigSaveError) as error:
        patched = patch_document(original, (
            Change(('monitor', 'targets'), None,
                   [{field: original.tree['monitor']['targets'][0][field],
                     'enabled': True}], 'set'),
        ))
        save_document(patched, path, expected_fingerprint=original.fingerprint)

    assert 'alpha' not in str(error.value)
    assert path.read_bytes() == before
    assert original.fingerprint == hashlib.sha256(before).hexdigest()
    assert list(tmp_path.iterdir()) == [path]


def test_set_whole_list_rejects_duplicate_identity_without_touching_file(tmp_path):
    """重复名称无法确定项目对应关系，即使传入全部键也拒绝保存。"""
    path = tmp_path / 'duplicate_list.yaml'
    path.write_text('monitor:\n  targets:\n    - name: same\n      custom: first\n'
                    '    - name: same\n      custom: second\n', encoding='utf-8')
    original = read_document(path)
    before = path.read_bytes()

    with pytest.raises(ConfigSaveError):
        patched = patch_document(original, (
            Change(('monitor', 'targets'), None, [
                {'name': 'same', 'custom': 'second'},
                {'name': 'same', 'custom': 'first'},
            ], 'set'),
        ))
        save_document(patched, path, expected_fingerprint=original.fingerprint)

    assert path.read_bytes() == before


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


def test_blank_document_has_no_defaults():
    """空白草稿不含项目默认值，路径与指纹均为空。"""
    document = new_blank_document()
    assert document.tree == {}
    assert document.path is None
    assert document.fingerprint is None


def test_blank_document_saves_readable_empty_mapping(tmp_path):
    """空草稿保存为 UTF-8 空映射，重开仍为空而不写入默认配置。"""
    destination = tmp_path / 'blank.yaml'
    document = new_blank_document()

    saved = save_document(document, destination, expected_fingerprint=None)

    raw = destination.read_bytes()
    assert raw
    assert raw.decode('utf-8').strip() == '{}'
    assert saved.tree == {}
    assert saved.path == destination
    assert saved.fingerprint == hashlib.sha256(raw).hexdigest()

    reopened = read_document(destination)
    assert reopened.tree == {}
    assert reopened.fingerprint == saved.fingerprint
