#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 草稿模型的事务与模式切换测试。"""

import copy

import pytest

from modules.config_document import ConfigDocument, new_document
from modules.gui.model import EditorModel


def test_mode_drafts_stay_in_memory():
    """模式切换保留各自目标草稿而不写入文档。"""
    model = EditorModel(new_document())
    model.switch_mode('launch')
    model.set(('monitor', 'target'), '中文 程序.exe')
    model.set(('monitor', 'launch', 'args'), '--name "中文 & !"')
    model.switch_mode('watch')
    model.set(('monitor', 'target'), 'other.exe')
    model.switch_mode('launch')
    assert model.get(('monitor', 'target')) == '中文 程序.exe'
    assert model.get(('monitor', 'launch', 'args')) == '--name "中文 & !"'
    assert '_gui' not in model.materialize().tree
    assert model.document.tree['monitor']['mode'] == 'watch'


def test_mode_branch_settings_stay_separate():
    """切换后非当前模式字段仍保留原树，草稿仅在激活时应用。"""
    model = EditorModel(new_document())
    model.switch_mode('launch')
    model.set(('monitor', 'launch', 'args'), 'first')
    model.switch_mode('watch')
    model.set(('monitor', 'watch', 'lookback_minutes'), 25)
    model.switch_mode('launch')
    assert model.get(('monitor', 'launch', 'args')) == 'first'
    assert model.get(('monitor', 'watch', 'lookback_minutes')) == 25


@pytest.mark.parametrize('invalid_mode', [['watch'], {'mode': 'watch'}])
def test_invalid_mode_can_be_validated_and_switched_without_losing_target(tmp_path, invalid_mode):
    """异常模式可报告问题，并在切换后保留原目标。"""
    document = new_document()
    document.tree['monitor']['mode'] = invalid_mode
    document.tree['monitor']['target'] = 'original.exe'
    model = EditorModel(document)

    issues = model.validate(tmp_path).issues
    assert any(issue.severity == 'error' for issue in issues)
    model.switch_mode('launch')

    assert model.get(('monitor', 'mode')) == 'launch'
    assert model.get(('monitor', 'target')) == 'original.exe'


def test_fold_does_not_reset():
    """读取和页面折叠不改变编辑代次或内容。"""
    model = EditorModel(new_document())
    model.set(('monitor', 'common', 'max_wait'), '42s')
    revision_before_fold = model.revision
    model.get(('monitor', 'common', 'max_wait'))
    snapshot = model.snapshot()
    snapshot['monitor']['common']['max_wait'] = '1s'
    assert model.revision == revision_before_fold
    assert model.get(('monitor', 'common', 'max_wait')) == '42s'


def test_unknown_provider_survives():
    """未知通道必须原样留在草稿及持久化候选中。"""
    document = new_document()
    unknown = {'provider': 'private-channel', 'token': '${TOKEN}', 'custom': '保留'}
    document.tree['push']['push_channel_settings']['channels'].append(unknown)
    model = EditorModel(document)
    original = copy.deepcopy(unknown)
    assert original == model.materialize().tree['push']['push_channel_settings']['channels'][-1]
    assert model.dirty is False


def test_unknown_provider_has_safe_error(tmp_path):
    """未知 provider 既不丢失也不把凭证写进诊断。"""
    document = new_document()
    document.tree['push']['push_channel_settings']['channels'] = [
        {'provider': 'private-channel', 'token': 'secret-98765'}]
    candidate = EditorModel(document).validate(tmp_path)
    assert any(issue.severity == 'error' for issue in candidate.issues)
    assert 'secret-98765' not in repr(candidate.issues)


def test_accept_saved_updates_baseline(tmp_path):
    """仅保存成功后更新树、路径、指纹与脏标记。"""
    model = EditorModel(new_document())
    model.set(('monitor', 'target'), 'demo.exe')
    saved = ConfigDocument(tmp_path / 'a.yaml', model.materialize().tree, 'abc')
    model.accept_saved(saved)
    assert model.document == saved
    assert model.dirty is False
    assert model.document_generation == 1
    assert model.revision == 0
    model.accept_saved(ConfigDocument(saved.path, copy.deepcopy(saved.tree), 'def'))
    assert model.revision == 0
    changed = copy.deepcopy(saved.tree)
    changed['monitor']['target'] = 'normalized.exe'
    model.accept_saved(ConfigDocument(saved.path, changed, 'ghi'))
    assert model.revision == 1


@pytest.mark.parametrize('saved_mode, draft_mode', [
    ('watch', 'launch'),
    ('launch', 'watch'),
])
def test_accept_saved_preserves_other_mode_target_draft(tmp_path, saved_mode, draft_mode):
    """保存当前模式后仍可恢复另一模式尚未写盘的目标草稿。"""
    model = EditorModel(new_document())
    model.switch_mode(draft_mode)
    model.set(('monitor', 'target'), 'draft.exe')
    model.switch_mode(saved_mode)
    model.set(('monitor', 'target'), 'saved.exe')
    saved = ConfigDocument(tmp_path / 'config.yaml', model.materialize().tree, 'saved')

    model.accept_saved(saved)
    model.switch_mode(draft_mode)

    assert model.get(('monitor', 'target')) == 'draft.exe'
    assert saved.tree['monitor']['target'] == 'saved.exe'


def test_replace_document_keeps_model_identity(tmp_path):
    """打开另一份文档抛弃旧草稿但保留模型实例。"""
    model = EditorModel(new_document())
    identity = id(model)
    model.switch_mode('launch')
    model.set(('monitor', 'launch', 'args'), 'private')
    other = new_document()
    other.tree['monitor']['target'] = 'new.exe'
    model.replace_document(other)
    model.switch_mode('launch')
    assert id(model) == identity
    assert model.get(('monitor', 'launch', 'args')) is None
    assert model.document_generation == 1


def test_save_as_advances_generation(tmp_path):
    """另存为改变文档身份并重置修订号。"""
    document = ConfigDocument(tmp_path / 'a.yaml', new_document().tree, 'a')
    model = EditorModel(document)
    model.set(('monitor', 'target'), 'other.exe')
    model.accept_saved(ConfigDocument(tmp_path / 'b.yaml', model.materialize().tree, 'b'))
    assert model.document_generation == 1
    assert model.revision == 0
    assert model.dirty is False


def test_validate_is_pure(tmp_path):
    """候选诊断不会更改模型草稿或磁盘文件。"""
    model = EditorModel(new_document())
    before = model.snapshot()
    candidate = model.validate(tmp_path)
    assert model.snapshot() == before
    assert candidate.tree is not model.document.tree
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('value', ['broken', ['push'], 3])
def test_validate_tolerates_non_mapping_push(tmp_path, value):
    """push 为标量或其他非映射时仍能给出诊断而不抛 AttributeError。"""
    document = new_document()
    document.tree['push'] = value
    candidate = EditorModel(document).validate(tmp_path)
    assert candidate.issues


def test_validate_tolerates_non_mapping_channel_settings(tmp_path):
    """push_channel_settings 为标量时按无通道处理并保留结构诊断。"""
    document = new_document()
    document.tree['push']['push_channel_settings'] = 'broken'
    candidate = EditorModel(document).validate(tmp_path)
    assert candidate.issues


def test_partial_number_keeps_raw_draft():
    """数字中间态保留在模型，补全后才转换为文档字段。"""
    model = EditorModel(new_document())
    path = ('monitor', 'common', 'timeout_threshold')
    original = model.get(path)
    model.set_input(path, '-', kind='int')
    assert model.get_input(path) == '-'
    assert model.get(path) == original
    assert model.dirty and model.input_errors
    model.set_input(path, '12', kind='int')
    assert model.get(path) == 12
    assert not model.input_errors
