#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 文件事务与 CLI 隔离测试。"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from modules.config_document import ConfigDocument, new_document, read_document, save_document
from modules.gui.launcher import LaunchResult
from modules.gui.model import EditorModel


def make_controller(tmp_path, document=None, answers=None):
    """创建不依赖 Tk 的控制器和可观察对话框。"""
    from modules.gui.controller import Controller

    confirm = Mock(side_effect=answers or [])
    runner = Mock()
    return Controller(EditorModel(document or new_document()), confirm=confirm,
                      choose_path=lambda: tmp_path / 'a.yaml',
                      cli_command=('python.exe', '2RPM.py'), runner=runner,
                      program_dir=tmp_path), confirm, runner


def test_dirty_start_saves_or_cancels(tmp_path, monkeypatch):
    """业务无效草稿即使保存成功也不得启动 CLI。"""
    controller, _, _ = make_controller(tmp_path, answers=['cancel'])
    controller.model.set(('monitor', 'mode'), 'bad')
    launch = Mock(return_value=LaunchResult(True, '已创建 CLI 进程'))
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
    assert controller.start_cli() is None
    assert controller.model.dirty is False
    launch.assert_not_called()


def test_cancel_keeps_dirty(tmp_path):
    """取消替换文档后保持原有草稿及代次。"""
    controller, _, runner = make_controller(tmp_path, answers=['cancel', 'cancel'])
    controller.model.set(('monitor', 'target'), '中文.exe')
    generation = controller.model.document_generation
    assert controller.new() is False
    assert controller.close() is False
    assert controller.model.document_generation == generation
    assert controller.model.get(('monitor', 'target')) == '中文.exe'
    runner.close.assert_not_called()


def test_normalization_requires_confirmation(tmp_path, monkeypatch):
    """启动清理候选差异必须明确确认而非静默写回。"""
    controller, confirm, _ = make_controller(tmp_path, answers=['cancel'])
    controller.save()
    controller.model.set(('monitor', 'common', 'max_wait'), '-30s')
    monkeypatch.setattr('modules.gui.controller.launch_cli', Mock())
    assert controller.start_cli() is None
    assert not controller.model.dirty


def test_normalized_destination_conflict(tmp_path):
    """覆盖判断及指纹检查针对最终大小写敏感路径。"""
    existing = tmp_path / 'a.YAML.yaml'
    existing.write_text('external', encoding='utf-8')
    controller, confirm, _ = make_controller(tmp_path, answers=['cancel'])
    controller.model.set(('monitor', 'target'), 'changed.exe')
    assert not controller.save(tmp_path / 'a.YAML')
    assert existing.read_text(encoding='utf-8') == 'external'
    assert confirm.call_args.args[0] == 'overwrite'
    assert str(existing) in str(confirm.call_args.args[1])
    assert controller.model.dirty


def test_consecutive_saves_use_new_baseline(tmp_path):
    """连续保存采用更新后的磁盘指纹，不误报冲突。"""
    controller, _, _ = make_controller(tmp_path)
    assert controller.save()
    first = controller.model.document.fingerprint
    controller.model.set(('monitor', 'target'), 'second.exe')
    assert controller.save()
    assert first != controller.model.document.fingerprint
    assert read_document(controller.model.document.path).tree['monitor']['target'] == 'second.exe'


def test_relocation_previews_new_base(tmp_path):
    """另存为跨目录时确认信息必须包含新解析基准。"""
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    document = save_document(new_document(), source / 'a.yaml', expected_fingerprint=None)
    controller, confirm, _ = make_controller(tmp_path, document, ['cancel'])
    assert not controller.save(target / 'b.yaml')
    assert confirm.call_args.args[0] == 'relocate'
    assert confirm.call_args.args[1]['to'] == str(target)
    assert controller.model.document == document


def test_clean_uppercase_path_saves_before_launch(tmp_path, monkeypatch):
    """干净的大写扩展名也须另存到 CLI 真正读取的路径。"""
    original = save_document(new_document(), tmp_path / 'a.yaml', expected_fingerprint=None)
    upper = tmp_path / 'a.YAML'
    upper.write_bytes(original.path.read_bytes())
    controller, confirm, _ = make_controller(tmp_path, read_document(upper))
    launch = Mock(return_value=LaunchResult(True, '已创建 CLI 进程'))
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
    result = controller.start_cli()
    assert result.created
    destination = tmp_path / 'a.YAML.yaml'
    assert controller.model.document.path == destination
    assert launch.call_args.args[1] == destination.resolve()
    assert upper.read_bytes() == original.path.read_bytes()


def test_replace_document_keeps_page_model_identity(tmp_path):
    """打开后仅替换同一模型实例，读取失败不替换。"""
    controller, _, _ = make_controller(tmp_path)
    identity = controller.model
    saved = save_document(new_document(), tmp_path / 'b.yaml', expected_fingerprint=None)
    assert controller.open(saved.path)
    assert controller.model is identity
    assert not controller.open(tmp_path / 'missing.yaml')
    assert controller.model.document.path == saved.path


def test_log_directory_uses_program_dir(tmp_path):
    """日志入口只解析程序目录且不创建目录。"""
    controller, _, _ = make_controller(tmp_path)
    assert controller.log_directory(tmp_path) == tmp_path / 'logs'
    assert not (tmp_path / 'logs').exists()


def test_normalized_destination_created_after_confirmation(tmp_path):
    """确认后新建的最终文件不得被 None 指纹覆盖。"""
    controller, _, _ = make_controller(tmp_path)
    controller.model.set(('monitor', 'target'), 'changed.exe')
    before = controller.model.materialize()
    generation = controller.model.document_generation
    original_save = __import__('modules.gui.controller', fromlist=['save_document']).save_document

    def external_creation(document, destination, *, expected_fingerprint):
        """在指纹读取之后模拟另一进程创建文件。"""
        destination.write_bytes(b'external')
        return original_save(document, destination, expected_fingerprint=expected_fingerprint)

    from unittest.mock import patch
    with patch('modules.gui.controller.save_document', external_creation):
        assert not controller.save(tmp_path / 'a')
    assert (tmp_path / 'a.yaml').read_bytes() == b'external'
    assert controller.model.document == before.__class__(None, controller.model.document.tree, None)
    assert controller.model.dirty
    assert controller.model.document_generation == generation


def test_normalized_destination_conflict_after_overwrite(tmp_path):
    """确认覆盖后外部编辑发生变化须保持原模型基线。"""
    destination = tmp_path / 'a.YAML.yaml'
    destination.write_bytes(b'old')

    def confirm(kind, detail):
        """在确认对话后注入外部编辑。"""
        if kind == 'overwrite':
            destination.write_bytes(b'external')
        return 'confirm'

    controller, _, _ = make_controller(tmp_path)
    controller.confirm = confirm
    controller.model.set(('monitor', 'target'), 'changed.exe')
    assert not controller.save(tmp_path / 'a.YAML')
    assert destination.read_bytes() == b'external'
    assert controller.model.document.path is None
    assert controller.model.dirty


def test_normalization_requires_confirmation_on_saved_file(tmp_path, monkeypatch):
    """保存现有非法数值后，启动候选差异取消时不得清理磁盘。"""
    controller, confirm, _ = make_controller(tmp_path, answers=['cancel'])
    controller.model.set(('monitor', 'common', 'max_wait'), '-30s')
    assert controller.save()
    original = controller.model.document.path.read_bytes()
    launch = Mock()
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
    assert controller.start_cli() is None
    assert controller.model.document.path.read_bytes() == original
    assert confirm.call_args.args[0] == 'normalize'
    launch.assert_not_called()


def test_normalization_confirmation_shows_masked_before_and_after(tmp_path, monkeypatch):
    """确认逐项显示变化前后值，但任何嵌套凭证不出现在对话框参数中。"""
    controller, confirm, _ = make_controller(tmp_path, answers=['cancel'])
    controller.model.set(('monitor', 'common', 'max_wait'), '-30s')
    channels = controller.model.get(('push', 'push_channel_settings', 'channels'))
    channels[0]['unknown'] = {'token': 'demo'}
    controller.model.set(('push', 'push_channel_settings', 'channels'), channels)
    assert controller.save()
    monkeypatch.setattr('modules.gui.controller.launch_cli', Mock())
    assert controller.start_cli() is None
    text = str(confirm.call_args.args[1])
    assert confirm.call_args.args[0] == 'normalize'
    assert '-30s' in text and '30s' in text
    assert 'demo' not in text


@pytest.mark.parametrize(('path', 'value'), [
    (('monitor', 'common', 'max_wait'), 'private-time-98765'),
    (('monitor', 'common', 'max_wait'), 'private-time-98765s'),
    (('monitor', 'common', 'max_wait'), True),
    (('monitor', 'common', 'max_wait'), '12.3'),
    (('monitor', 'common', 'timeout_threshold'), '2s'),
])
def test_numeric_whitelist_never_displays_invalid_values(path, value):
    """数值白名单只展示符合字段语义的值，不回显误填内容。"""
    from modules.gui.controller import Controller

    assert Controller._display_change(path, value) == '（已遮挡）'


def test_invalid_numeric_text_is_masked_in_start_confirmation(tmp_path, monkeypatch):
    """真实规范化确认参数不得含误填在时间字段中的敏感文本。"""
    controller, confirm, _ = make_controller(tmp_path, answers=['cancel'])
    controller.model.set(('monitor', 'common', 'max_wait'), 'private-time-98765')
    assert controller.save()
    monkeypatch.setattr('modules.gui.controller.launch_cli', Mock())
    assert controller.start_cli() is None
    assert confirm.call_args.args[0] == 'normalize'
    assert 'private-time-98765' not in repr(confirm.call_args.args[1])
    assert controller.model.document.path.read_bytes()


def test_numeric_confirmation_keeps_valid_values():
    """有效的数值与时间仍可供用户判断规范化差异。"""
    from modules.gui.controller import Controller

    assert Controller._display_change(('monitor', 'common', 'max_wait'), '-30s') == '-30s'
    assert Controller._display_change(('monitor', 'common', 'timeout_threshold'), 3) == '3'


@pytest.mark.parametrize('answer', ['cancel', 'confirm'])
def test_real_model_invalid_threshold_requires_normalization_confirmation(tmp_path, monkeypatch,
                                                                          answer):
    """非法阈值只有候选差异，启动前仍须确认且取消时磁盘不变。"""
    controller, confirm, _ = make_controller(tmp_path, answers=[answer])
    path = ('monitor', 'common', 'timeout_threshold')
    controller.model.set(('monitor', 'target'), 'demo.exe')
    controller.model.set(path, 'invalid-threshold-secret')
    assert controller.save()
    before = controller.model.document.path.read_bytes()
    candidate = controller.model.validate(tmp_path)
    assert not candidate.issues
    assert any(change.path == path and change.after == 3 for change in candidate.changes)
    launch = Mock(return_value=LaunchResult(True, '已创建 CLI 进程'))
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)

    result = controller.start_cli()

    assert confirm.call_args.args[0] == 'normalize'
    detail = repr(confirm.call_args.args[1])
    assert 'invalid-threshold-secret' not in detail
    assert 'monitor' in detail and 'timeout_threshold' in detail
    if answer == 'cancel':
        assert result is None
        assert controller.model.document.path.read_bytes() == before
        launch.assert_not_called()
    else:
        assert result.created
        assert controller.model.get(path) == 3
        assert read_document(controller.model.document.path).tree['monitor']['common']['timeout_threshold'] == 3
        launch.assert_called_once()


def test_dirty_start_save_launches_saved_path(tmp_path, monkeypatch):
    """用户确认保存并启动后只把已保存绝对路径传给启动器。"""
    controller, _, _ = make_controller(tmp_path, answers=['save'])
    controller.model.set(('monitor', 'target'), 'demo.exe')
    launch = Mock(return_value=LaunchResult(True, '已创建 CLI 进程'))
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
    result = controller.start_cli()
    assert result and result.created
    assert launch.call_args.args[1] == controller.model.document.path.resolve()
    assert not controller.model.dirty


def test_normalization_confirmed_rechecks_disk(tmp_path, monkeypatch):
    """规范化后须重新读取验证，外部并发编辑不得启动。"""
    controller, _, _ = make_controller(tmp_path, answers=['confirm'])
    controller.model.set(('monitor', 'common', 'max_wait'), '-30s')
    assert controller.save()
    before = controller.model.document
    original_read = __import__('modules.gui.controller', fromlist=['read_document']).read_document
    calls = 0

    def external_edit(path):
        """在规范化写回和读取复验之间模拟外部编辑。"""
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(path.read_bytes() + b'\n# external\n')
        return original_read(path)

    launch = Mock()
    monkeypatch.setattr('modules.gui.controller.read_document', external_edit)
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
    assert controller.start_cli() is None
    launch.assert_not_called()
    assert controller.model.document == before


def test_dirty_start_invalid_is_saved_but_not_launched(tmp_path, monkeypatch):
    """完整但业务无效的草稿仍保存，启动由业务诊断阻断。"""
    controller, _, _ = make_controller(tmp_path, answers=['save'])
    controller.model.set(('monitor', 'mode'), 'bad')
    monkeypatch.setattr('modules.gui.controller.launch_cli', Mock())
    assert controller.start_cli() is None
    assert not controller.model.dirty
    assert read_document(tmp_path / 'a.yaml').tree['monitor']['mode'] == 'bad'


def test_normalize_confirmation_never_exposes_secrets(tmp_path):
    """规范化确认只展示脱敏路径，不能把原始凭证交给 UI。"""
    controller, confirm, _ = make_controller(tmp_path, answers=['cancel'])
    controller.model.set(('push', 'push_channel_settings', 'channels'),
                         [{'provider': 'serverchan', 'sckey': 'secret-value'}])
    controller.model.set(('monitor', 'common', 'max_wait'), '-30s')
    assert controller.save()
    assert controller.start_cli() is None
    assert 'secret-value' not in repr(confirm.call_args.args[1])


def test_save_as_relocation_keeps_secret_out_of_confirmation(tmp_path):
    """跨目录提示不能原样回显带凭证的路径输入。"""
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    document = save_document(new_document(), first / 'a.yaml', expected_fingerprint=None)
    controller, confirm, _ = make_controller(tmp_path, document, answers=['cancel'])
    controller.model.set(('monitor', 'target'), 'demo.exe')
    assert not controller.save(second / 'b.yaml')
    assert str(second) in confirm.call_args.args[1]['to']


def test_normalization_diff_does_not_send_credential_values_to_dialog(tmp_path, monkeypatch):
    """候选包含凭证变更时确认回调仅收到字段路径。"""
    from modules.config_rules import Candidate, Change

    controller, confirm, _ = make_controller(tmp_path, answers=['cancel'])
    assert controller.save()
    value = 'private-key-98765'
    change = Change(('push', 'screenshot', 'image_host', 0, 'token'), value, '', 'set')
    original_validate = controller.model.validate

    def changed_candidate(config_dir):
        """模拟候选清理中含有敏感值。"""
        candidate = original_validate(config_dir)
        return Candidate(candidate.tree, candidate.issues, (change,))

    monkeypatch.setattr(controller.model, 'validate', changed_candidate)
    assert controller.start_cli() is None
    assert 'private-key-98765' not in repr(confirm.call_args.args[1])


def test_external_edit_before_start_blocks_launch(tmp_path, monkeypatch):
    """即使草稿未修改，外部编辑后的内容也不能被旧基线启动。"""
    controller, _, _ = make_controller(tmp_path)
    assert controller.save()
    path = controller.model.document.path
    path.write_bytes(path.read_bytes() + b'\n# external\n')
    launch = Mock()
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
    assert controller.start_cli() is None
    launch.assert_not_called()


def test_normalization_with_deleted_unknown_section_is_explicit(tmp_path, monkeypatch):
    """候选清理未知节必须可在确认时被识别，取消保持原文件。"""
    controller, confirm, _ = make_controller(tmp_path, answers=['cancel'])
    assert controller.save()
    path = controller.model.document.path
    tree = controller.model.materialize().tree
    tree['unknown'] = {'keep': 'value'}
    controller.model.replace_document(ConfigDocument(path, tree, controller.model.document.fingerprint))
    original = path.read_bytes()
    monkeypatch.setattr('modules.gui.controller.launch_cli', Mock())
    assert controller.start_cli() is None
    assert path.read_bytes() == original


def test_relocation_preview_uses_resolved_target_and_cwd(tmp_path):
    """跨目录前预览来自共享目标解析器，而不只显示原文。"""
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    document = save_document(new_document(), first / 'a.yaml', expected_fingerprint=None)
    controller, confirm, _ = make_controller(tmp_path, document, ['cancel'])
    controller.model.switch_mode('launch')
    controller.model.set(('monitor', 'target'), '.\\demo.exe')
    controller.model.set(('monitor', 'launch', 'cwd'), '.\\work')
    assert not controller.save(second / 'b.yaml')
    detail = confirm.call_args.args[1]
    assert str(second) in detail['resolved_target']
    assert str(second) in detail['resolved_cwd']


def test_missing_cli_pair_returns_safe_failure(tmp_path):
    """配对文件不存在时不得将空命令传给进程创建器。"""
    controller, _, _ = make_controller(tmp_path)
    controller.cli_command = ()
    assert controller.save()
    result = controller.start_cli()
    assert result is not None
    assert result.created is False
    assert '匹配' in result.message


def test_normalization_confirmed_updates_saved_baseline(tmp_path, monkeypatch):
    """确认规范化后更新模型字段和指纹再启动。"""
    controller, _, _ = make_controller(tmp_path, answers=['confirm'])
    controller.model.set(('monitor', 'common', 'max_wait'), '-30s')
    assert controller.save()
    launch = Mock(return_value=LaunchResult(True, '已创建 CLI 进程'))
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
    result = controller.start_cli()
    assert result and result.created
    assert controller.model.get(('monitor', 'common', 'max_wait')) == '30s'
    assert controller.model.document.fingerprint == read_document(controller.model.document.path).fingerprint


def test_start_rejects_unknown_cleanup_after_normalization(tmp_path, monkeypatch):
    """确认清理后复验候选仍有差异时不能启动未完成清理的配置。"""
    from modules.config_rules import Candidate, Change

    controller, _, _ = make_controller(tmp_path, answers=['confirm'])
    assert controller.save()
    change = Change(('monitor', 'common', 'loop_interval'), '1s', '2s', 'set')
    original = controller.model.validate

    def persistent_change(directory):
        """模拟规则在规范化之后仍报告同一项差异。"""
        candidate = original(directory)
        return Candidate(candidate.tree, candidate.issues, (change,))

    monkeypatch.setattr(controller.model, 'validate', persistent_change)
    launch = Mock()
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
    assert controller.start_cli() is None
    launch.assert_not_called()
