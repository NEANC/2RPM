#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 草稿模型的事务与模式切换测试。"""

import copy

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
