#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 文件事务与 CLI 隔离测试。"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from modules.config_document import (ConfigDocument, ConfigSaveError, new_document,
                                     read_document, save_document)
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


def test_existing_bare_watch_target_still_starts(tmp_path, monkeypatch):
    """旧配置裸进程名不受 GUI 新输入规则影响。"""
    document = save_document(new_document(), tmp_path / 'old.yaml',
                             expected_fingerprint=None)
    controller, _, _ = make_controller(tmp_path, document)
    launch = Mock(return_value=LaunchResult(True, '已创建 CLI 进程'))
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
    result = controller.start_cli()
    assert result is not None and result.created
    launch.assert_called_once()


def test_launch_validates_reread_disk_tree_not_old_draft(tmp_path, monkeypatch):
    """复验实际磁盘候选，不能用旧草稿的检查结果放行。"""
    document = save_document(new_document(), tmp_path / 'config.yaml',
                             expected_fingerprint=None)
    controller, _, _ = make_controller(tmp_path, document)
    disk_tree = controller.model.snapshot()
    disk_tree['monitor']['mode'] = 'invalid'
    monkeypatch.setattr('modules.gui.controller.read_document',
                        lambda _: ConfigDocument(document.path, disk_tree, document.fingerprint))
    launch = Mock()
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)

    assert controller.start_cli() is None
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


def test_business_invalid_target_can_save_as_another_directory(tmp_path):
    """业务无效目标也允许跨目录另存，并保留路径变化确认。"""
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    document = save_document(new_document(), source / 'a.yaml', expected_fingerprint=None)
    controller, confirm, _ = make_controller(tmp_path, document, ['confirm'])
    controller.model.set(('monitor', 'mode'), 'launch')
    controller.model.set(('monitor', 'target'), 'invalid-target')
    before = document.path.read_bytes()
    assert controller.save_as(lambda: target / 'b.yaml')
    assert confirm.call_args.args[0] == 'relocate'
    assert confirm.call_args.args[1]['resolved_target'] == '待校验'
    assert read_document(target / 'b.yaml').tree['monitor']['target'] == 'invalid-target'
    assert document.path.read_bytes() == before
    assert controller.model.document.path == target / 'b.yaml'


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


@pytest.mark.parametrize('outer', ['save', 'save_as'])
def test_overwrite_reentrant_cancel_does_not_hide_outer_write_failure(tmp_path, monkeypatch,
                                                                      outer):
    """覆盖确认中的内层取消不得把外层写失败误报为取消。"""
    source = save_document(new_document(), tmp_path / 'source.yaml', expected_fingerprint=None)
    destination = tmp_path / 'destination.yaml'
    destination.write_bytes(b'existing')
    controller, _, _ = make_controller(tmp_path, source)
    controller.model.set(('monitor', 'target'), 'draft.exe')
    before = controller.model.document
    source_bytes = source.path.read_bytes()
    destination_bytes = destination.read_bytes()
    write = Mock(side_effect=ConfigSaveError('写入失败'))
    monkeypatch.setattr('modules.gui.controller.save_document', write)

    def confirm(kind, detail):
        """在外层覆盖确认中取消内层另存请求。"""
        if kind == 'overwrite':
            assert controller.save_as(lambda: None) is False
            assert controller.save_cancelled is True
        return 'confirm'

    controller.confirm = confirm
    if outer == 'save':
        result = controller.save(destination)
    else:
        result = controller.save_as(lambda: destination)

    assert result is False
    assert controller.save_cancelled is False
    write.assert_called_once()
    assert controller.model.document == before
    assert controller.model.document.fingerprint == before.fingerprint
    assert controller.model.get(('monitor', 'target')) == 'draft.exe'
    assert controller.model.dirty
    assert source.path.read_bytes() == source_bytes
    assert destination.read_bytes() == destination_bytes


@pytest.mark.parametrize('outer', ['save', 'save_as'])
def test_relocate_reentrant_cancel_does_not_hide_target_read_failure(tmp_path, monkeypatch,
                                                                     outer):
    """跨目录确认中的内层取消不得把外层目标读取失败误报为取消。"""
    source_dir = tmp_path / 'source'
    target_dir = tmp_path / 'target'
    source_dir.mkdir()
    target_dir.mkdir()
    source = save_document(new_document(), source_dir / 'a.yaml', expected_fingerprint=None)
    destination = target_dir / 'b.yaml'
    controller, _, _ = make_controller(tmp_path, source)
    controller.model.set(('monitor', 'target'), 'draft.exe')
    before = controller.model.document
    source_bytes = source.path.read_bytes()
    read = Path.read_bytes

    def read_target(path):
        """只对新目标模拟文件系统读取失败。"""
        if path == destination:
            raise OSError('目标不可读取')
        return read(path)

    def confirm(kind, detail):
        """外层确认期间取消内层另存。"""
        assert kind == 'relocate'
        assert controller.save_as(lambda: None) is False
        assert controller.save_cancelled is True
        return 'confirm'

    monkeypatch.setattr(Path, 'read_bytes', read_target)
    controller.confirm = confirm
    result = (controller.save(destination) if outer == 'save'
              else controller.save_as(lambda: destination))

    assert result is False
    assert controller.save_cancelled is False
    assert controller.model.document == before
    assert controller.model.get(('monitor', 'target')) == 'draft.exe'
    assert controller.model.dirty
    assert source.path.read_bytes() == source_bytes
    assert not destination.exists()


def test_relocate_target_read_failure_shows_save_as_error(tmp_path, monkeypatch):
    """内层取消后目标读取失败仍显示另存失败提示。"""
    from modules.gui.app import App

    source_dir = tmp_path / 'source'
    target_dir = tmp_path / 'target'
    source_dir.mkdir()
    target_dir.mkdir()
    source = save_document(new_document(), source_dir / 'a.yaml', expected_fingerprint=None)
    destination = target_dir / 'b.yaml'
    controller, _, _ = make_controller(tmp_path, source)
    controller.model.set(('monitor', 'target'), 'draft.exe')
    source_bytes = source.path.read_bytes()
    read = Path.read_bytes

    def read_target(path):
        """模拟目标文件不可读取。"""
        if path == destination:
            raise OSError('目标不可读取')
        return read(path)

    def confirm(kind, detail):
        """外层路径变化确认期间取消内层另存。"""
        assert kind == 'relocate'
        assert controller.save_as(lambda: None) is False
        return 'confirm'

    controller.confirm = confirm
    controller.runner.active = False
    app = object.__new__(App)
    app.controller = controller
    app.model = controller.model
    app.status = Mock()
    app.root = Mock()
    alert = Mock()
    monkeypatch.setattr(Path, 'read_bytes', read_target)
    monkeypatch.setattr('modules.gui.app.filedialog.asksaveasfilename',
                        lambda **_: str(destination))
    monkeypatch.setattr('modules.gui.app.messagebox.showerror', alert)

    app.save_as()

    app.status.configure.assert_called_once_with(text='配置未另存')
    alert.assert_called_once_with('另存配置', '配置未保存，请检查输入或文件写入权限。草稿已保留。',
                                  parent=app.root)
    assert controller.model.document == source
    assert controller.model.dirty
    assert source.path.read_bytes() == source_bytes
    assert not destination.exists()


def test_app_save_as_conflict_prompt_save_as_does_not_report_failure(
        tmp_path, monkeypatch):
    """冲突提示选择另存后，内层成功不得被外层结果误报为失败。"""
    from modules.gui.app import App
    from modules.gui.controller import SaveResult

    source = save_document(new_document(), tmp_path / 'source.yaml',
                           expected_fingerprint=None)
    destination = tmp_path / 'saved.yaml'
    controller, _, _ = make_controller(tmp_path, source)
    controller.runner.active = False
    controller.model.set(('monitor', 'target'), 'draft.exe')
    source.path.write_bytes(b'external')
    generation = controller.model.document_generation
    original_save_as = controller._save_as_result
    results = []

    def record_save_as(picker):
        """观察冲突提示触发的内层另存与外层另存结果。"""
        result = original_save_as(picker)
        results.append(result)
        return result

    controller._save_as_result = record_save_as
    app = object.__new__(App)
    app.controller = controller
    app.model = controller.model
    app.root = Mock()
    app.status = Mock()
    app._refresh_save_status = Mock()
    controller.on_conflict = app._show_conflict
    picker = Mock(side_effect=[str(source.path), str(destination)])
    choice = Mock(return_value=False)
    alert = Mock()
    monkeypatch.setattr('modules.gui.app.filedialog.asksaveasfilename', picker)
    monkeypatch.setattr('modules.gui.app.messagebox.askyesnocancel', choice)
    monkeypatch.setattr('modules.gui.app.messagebox.showerror', alert)

    app.save_as()

    choice.assert_called_once_with(
        '外部变更', '磁盘配置已变化。是：重新加载；否：将草稿另存为；取消：暂不处理。',
        parent=app.root)
    assert picker.call_count == 2
    assert results == [SaveResult.SAVED, SaveResult.SUPERSEDED]
    assert controller.model.document_generation > generation
    assert controller.model.document.path == destination
    assert not controller.model.dirty
    assert read_document(destination).tree['monitor']['target'] == 'draft.exe'
    assert source.path.read_bytes() == b'external'
    assert not any(call.kwargs.get('text') == '配置未另存'
                   for call in app.status.configure.call_args_list)
    alert.assert_not_called()


def test_app_save_as_conflict_callback_saved_new_document_does_not_report_failure(
        tmp_path, monkeypatch):
    """直接重入 App.save_as 成功后清理旧测试结果且不误报失败。"""
    from modules.gui.app import App

    source = save_document(new_document(), tmp_path / 'source.yaml',
                           expected_fingerprint=None)
    destination = tmp_path / 'saved.yaml'
    controller, _, _ = make_controller(tmp_path, source)
    controller.runner.active = False
    controller.model.set(('monitor', 'target'), 'draft.exe')
    source.path.write_bytes(b'external')
    generation = controller.model.document_generation
    original_save_as = controller._save_as_result
    results = []

    def record_save_as(picker):
        """观察内外两次真实另存的返回值。"""
        result = original_save_as(picker)
        results.append(result)
        return result

    controller._save_as_result = record_save_as
    app = object.__new__(App)
    app.controller = controller
    app.model = controller.model
    app.root = Mock()
    app.status = Mock()
    app._refresh_save_status = Mock()
    app.test_session = Mock()
    app.test_dialogs = Mock()
    app.actions = {'测试通知': Mock()}
    app.preview_label = Mock()
    app._owned_previews = set()
    controller.on_conflict = app.save_as
    picker = Mock(side_effect=[str(source.path), str(destination)])
    alert = Mock()
    monkeypatch.setattr('modules.gui.app.filedialog.asksaveasfilename', picker)
    monkeypatch.setattr('modules.gui.app.messagebox.showerror', alert)

    app.save_as()

    from modules.gui.controller import SaveResult

    assert results == [SaveResult.SAVED, SaveResult.SUPERSEDED]
    assert controller.model.document_generation > generation
    assert controller.model.document.path == destination
    assert not controller.model.dirty
    assert read_document(destination).tree['monitor']['target'] == 'draft.exe'
    assert source.path.read_bytes() == b'external'
    app.test_session.clear.assert_called_once_with()
    app.test_dialogs.clear.assert_called_once_with()
    app.preview_label.configure.assert_called_once_with(image='', text='')
    assert not any(call.kwargs.get('text') == '配置未另存'
                   for call in app.status.configure.call_args_list)
    alert.assert_not_called()


def test_conflict_reload_is_not_an_inner_save(tmp_path):
    """冲突回调重载磁盘虽使草稿干净，也不能冒充另存成功。"""
    from modules.gui.controller import SaveResult

    source = save_document(new_document(), tmp_path / 'source.yaml',
                           expected_fingerprint=None)
    controller, _, _ = make_controller(tmp_path, source)
    controller.model.set(('monitor', 'target'), 'draft.exe')
    external = read_document(source.path)
    external.tree['monitor']['target'] = 'external.exe'
    save_document(external, source.path, expected_fingerprint=source.fingerprint)
    controller.on_conflict = lambda: controller.resolve_external('reload')
    controller.confirm = lambda kind, detail: 'confirm'

    assert controller._save_result() is SaveResult.FAILED
    assert controller.model.get(('monitor', 'target')) == 'external.exe'
    assert not controller.model.dirty


def test_second_external_check_inner_save_as_is_superseded(tmp_path):
    """第二次外部检查期间另存成功应归因为被内层保存取代。"""
    from modules.gui.controller import SaveResult

    source = save_document(new_document(), tmp_path / 'source.yaml',
                           expected_fingerprint=None)
    destination = tmp_path / 'saved.yaml'
    controller, _, _ = make_controller(tmp_path, source)
    controller.model.set(('monitor', 'target'), 'draft.exe')
    check = controller.check_external
    calls = 0

    def check_on_second_call():
        """首次检查通过后，在第二次检查前注入外部修改。"""
        nonlocal calls
        calls += 1
        if calls == 2:
            source.path.write_bytes(b'external')
        return check()

    controller.check_external = check_on_second_call
    controller.on_conflict = lambda: controller.save_as(lambda: destination)

    assert controller._save_result() is SaveResult.SUPERSEDED
    assert calls == 2
    assert controller.model.document.path == destination
    assert not controller.model.dirty
    assert read_document(destination).tree['monitor']['target'] == 'draft.exe'
    assert source.path.read_bytes() == b'external'


def test_conflict_inner_save_as_then_open_clean_file_is_not_superseded(tmp_path):
    """另存后又打开另一干净文件不能冒充本次保存成功。"""
    from modules.gui.controller import SaveResult

    source = save_document(new_document(), tmp_path / 'source.yaml',
                           expected_fingerprint=None)
    destination = tmp_path / 'saved.yaml'
    other = save_document(new_document(), tmp_path / 'other.yaml',
                          expected_fingerprint=None)
    controller, _, _ = make_controller(tmp_path, source)
    controller.model.set(('monitor', 'target'), 'draft.exe')
    source.path.write_bytes(b'external')

    def save_then_open():
        """在冲突回调中真实另存后切换到另一干净文件。"""
        assert controller.save_as(lambda: destination)
        assert controller.open(other.path)

    controller.on_conflict = save_then_open

    assert controller._save_result() is SaveResult.FAILED
    assert controller.model.document.path == other.path
    assert not controller.model.dirty
    assert read_document(destination).tree['monitor']['target'] == 'draft.exe'
    assert source.path.read_bytes() == b'external'


def test_conflict_inner_save_as_then_open_same_file_is_not_superseded(tmp_path):
    """内层另存后显式重开相同路径，文档代次改变不得归因于保存。"""
    from modules.gui.controller import SaveResult

    source = save_document(new_document(), tmp_path / 'source.yaml',
                           expected_fingerprint=None)
    destination = tmp_path / 'saved.yaml'
    controller, _, _ = make_controller(tmp_path, source)
    controller.model.set(('monitor', 'target'), 'draft.exe')
    source.path.write_bytes(b'external')
    generation = controller.model.document_generation
    saved_generation = None
    saved_identity = None

    def save_then_open():
        """确认内层保存身份后重新打开同一路径。"""
        nonlocal saved_generation, saved_identity
        assert controller.save_as(lambda: destination)
        saved_generation = controller.model.document_generation
        saved_identity = (controller.model.document.path,
                          controller.model.document.fingerprint)
        assert saved_generation > generation
        assert (saved_generation, *saved_identity) == controller._saved_document_identity
        assert controller.open(destination)

    controller.on_conflict = save_then_open

    assert controller._save_result() is SaveResult.FAILED
    assert controller.model.document_generation > saved_generation
    assert (controller.model.document.path,
            controller.model.document.fingerprint) == saved_identity
    assert not controller.model.dirty
    assert read_document(destination).tree['monitor']['target'] == 'draft.exe'
    assert source.path.read_bytes() == b'external'


def test_conflict_inner_save_as_keeps_boolean_save_success(tmp_path):
    """冲突回调真实另存成功后旧布尔保存接口不得误报失败。"""
    source = save_document(new_document(), tmp_path / 'source.yaml',
                           expected_fingerprint=None)
    destination = tmp_path / 'saved.yaml'
    controller, _, _ = make_controller(tmp_path, source)
    controller.model.set(('monitor', 'target'), 'demo.exe')
    source.path.write_bytes(b'external')
    controller.on_conflict = lambda: controller.save_as(lambda: destination)

    assert controller.save() is True
    assert controller.model.document.path == destination
    assert not controller.model.dirty
    assert read_document(destination).tree['monitor']['target'] == 'demo.exe'
    assert source.path.read_bytes() == b'external'


def test_conflict_inner_save_as_allows_cli_launch(tmp_path, monkeypatch):
    """启动时冲突回调另存成功，应复验新磁盘路径并启动。"""
    source = save_document(new_document(), tmp_path / 'source.yaml',
                           expected_fingerprint=None)
    destination = tmp_path / 'saved.yaml'
    controller, _, _ = make_controller(tmp_path, source)
    controller.model.set(('monitor', 'target'), 'demo.exe')
    source.path.write_bytes(b'external')
    controller.on_conflict = lambda: controller.save_as(lambda: destination)
    launch = Mock(return_value=LaunchResult(True, '已创建 CLI 进程'))
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)

    result = controller.start_cli()

    assert result is not None and result.created
    launch.assert_called_once_with(controller.cli_command, destination.resolve())
    assert read_document(destination).tree['monitor']['target'] == 'demo.exe'
    assert source.path.read_bytes() == b'external'


def test_app_save_as_inner_success_outer_write_failure_reports_error(tmp_path, monkeypatch):
    """内层另存成功不能掩盖外层目标的写入失败。"""
    from modules.gui.app import App

    source = save_document(new_document(), tmp_path / 'source.yaml',
                           expected_fingerprint=None)
    inner = tmp_path / 'inner.yaml'
    outer = tmp_path / 'outer.yaml'
    outer.write_bytes(b'existing')
    controller, _, _ = make_controller(tmp_path, source)
    controller.runner.active = False
    controller.model.set(('monitor', 'target'), 'draft.exe')
    write = __import__('modules.gui.controller', fromlist=['save_document']).save_document

    def save_with_outer_failure(document, path, *, expected_fingerprint):
        """只拒绝外层目标的写入。"""
        if path == outer:
            raise ConfigSaveError('写入失败')
        return write(document, path, expected_fingerprint=expected_fingerprint)

    def confirm(kind, detail):
        """外层覆盖确认期间另存到内层目标。"""
        if kind == 'overwrite':
            app.save_as()
        return 'confirm'

    controller.confirm = confirm
    app = object.__new__(App)
    app.controller = controller
    app.model = controller.model
    app.root = Mock()
    app.status = Mock()
    app.test_session = Mock()
    app.test_dialogs = Mock()
    app.actions = {'测试通知': Mock()}
    app.preview_label = Mock()
    app._owned_previews = set()
    alert = Mock()
    monkeypatch.setattr('modules.gui.controller.save_document', save_with_outer_failure)
    monkeypatch.setattr('modules.gui.app.filedialog.asksaveasfilename',
                        Mock(side_effect=[str(outer), str(inner)]))
    monkeypatch.setattr('modules.gui.app.messagebox.showerror', alert)

    app.save_as()

    app.status.configure.assert_called_once_with(text='配置未另存')
    alert.assert_called_once_with('另存配置', '配置未保存，请检查输入或文件写入权限。草稿已保留。',
                                  parent=app.root)
    assert controller.model.document.path == inner
    assert inner.exists()
    assert outer.read_bytes() == b'existing'
    app.test_session.clear.assert_called_once_with()
    app.test_dialogs.clear.assert_called_once_with()
    app.preview_label.configure.assert_called_once_with(image='', text='')


@pytest.mark.parametrize('phase', ['conflict', 'overwrite', 'relocate', 'read', 'write'])
def test_reentrant_inner_cancel_does_not_cancel_outer_failure(tmp_path, monkeypatch, phase):
    """各保存阶段的内层取消不应改变外层失败的归因。"""
    source_dir = tmp_path / 'source'
    target_dir = tmp_path / 'target'
    source_dir.mkdir()
    target_dir.mkdir()
    source = save_document(new_document(), source_dir / 'source.yaml',
                           expected_fingerprint=None)
    destination = target_dir / 'target.yaml'
    controller, _, _ = make_controller(tmp_path, source)
    controller.model.set(('monitor', 'target'), 'draft.exe')
    original_read = Path.read_bytes
    original_write = __import__('modules.gui.controller', fromlist=['save_document']).save_document
    calls = []
    entered = False

    def cancel_inner():
        """观察内层调用当下仍报告取消。"""
        calls.append('inner')
        assert controller.save_as(lambda: None) is False
        assert controller.save_cancelled is True

    def confirm(kind, detail):
        """在覆盖或迁移确认里重入。"""
        nonlocal entered
        if kind == phase and not entered:
            entered = True
            cancel_inner()
        return 'confirm'

    def read(path):
        """在外层目标读取期间重入。"""
        if phase == 'read' and path == destination:
            cancel_inner()
            raise OSError('目标不可读取')
        return original_read(path)

    def write(document, path, *, expected_fingerprint):
        """在外层写入期间重入。"""
        if phase in ('overwrite', 'relocate', 'write'):
            if phase == 'write':
                cancel_inner()
            raise ConfigSaveError('写入失败')
        return original_write(document, path, expected_fingerprint=expected_fingerprint)

    controller.confirm = confirm
    if phase == 'conflict':
        source.path.write_bytes(b'external')
        controller.on_conflict = cancel_inner
        target = source.path
    else:
        target = destination
        if phase == 'overwrite':
            destination.write_bytes(b'existing')
        if phase == 'read':
            monkeypatch.setattr(Path, 'read_bytes', read)
        if phase in ('overwrite', 'relocate', 'write'):
            monkeypatch.setattr('modules.gui.controller.save_document', write)

    before = controller.model.document
    assert controller.save(target) is False
    assert calls == ['inner']
    assert controller.save_cancelled is False
    assert controller.model.document == before
    assert controller.model.dirty


@pytest.mark.parametrize('phase', ['overwrite', 'relocate'])
def test_outer_confirmation_cancel_survives_inner_save(tmp_path, phase):
    """内层成功保存不能把外层确认取消改判为失败。"""
    source_dir = tmp_path / 'source'
    target_dir = tmp_path / 'target'
    source_dir.mkdir()
    target_dir.mkdir()
    source = save_document(new_document(), source_dir / 'source.yaml',
                           expected_fingerprint=None)
    destination = target_dir / 'target.yaml'
    if phase == 'overwrite':
        destination.write_bytes(b'existing')
    controller, _, _ = make_controller(tmp_path, source)
    controller.model.set(('monitor', 'target'), 'draft.exe')

    def confirm(kind, detail):
        """外层确认时先保存内层新文件，再取消外层。"""
        if kind == phase:
            assert controller.save(source.path) is True
            assert controller.save_cancelled is False
            return 'cancel'
        return 'confirm'

    controller.confirm = confirm
    assert controller.save(destination) is False
    assert controller.save_cancelled is True


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


def test_repeat_confirmation_external_reload_invalid_config_blocks_launch(tmp_path, monkeypatch):
    """重复启动确认中外部有效 YAML 重载后，旧校验不得放行无效配置。"""
    document = save_document(new_document(), tmp_path / 'config.yaml',
                             expected_fingerprint=None)
    controller, _, _ = make_controller(tmp_path, document)
    controller.launch_count = 1
    generation = controller.model.document_generation
    launch = Mock(return_value=LaunchResult(True, '已创建 CLI 进程'))
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)

    def confirm(kind, detail):
        """在重复启动确认期间由外部写入可读取但业务无效的配置。"""
        assert kind == 'repeat_launch'
        changed = read_document(document.path)
        changed.tree['monitor']['mode'] = 'invalid'
        save_document(changed, document.path, expected_fingerprint=changed.fingerprint)
        return 'confirm'

    controller.confirm = confirm
    assert controller.start_cli() is None
    disk = read_document(document.path)
    assert disk.fingerprint != document.fingerprint
    assert disk.tree['monitor']['mode'] == 'invalid'
    assert controller.model.document_generation > generation
    assert controller.model.revision == 0
    assert controller.model.document.path == document.path
    assert controller.model.document.fingerprint == disk.fingerprint
    assert controller.model.get(('monitor', 'mode')) == 'invalid'
    assert controller.model.snapshot() == disk.tree
    assert not controller.model.dirty
    assert not controller.conflicted
    assert controller.launch_count == 1
    launch.assert_not_called()


@pytest.mark.parametrize('reentry', ['edit', 'save_as'])
def test_normalize_confirmation_reentry_does_not_replace_current_draft(
        tmp_path, monkeypatch, reentry):
    """规范化确认中修改草稿或另存后，旧磁盘候选不得写回并接管草稿。"""
    controller, _, _ = make_controller(tmp_path)
    controller.model.set(('monitor', 'common', 'max_wait'), '-30s')
    assert controller.save()
    source = controller.model.document.path
    original = source.read_bytes()
    destination = tmp_path / 'copy.yaml'
    generation = controller.model.document_generation
    revision = controller.model.revision
    fingerprint = controller.model.document.fingerprint
    launch_count = controller.launch_count
    launch = Mock()
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)

    def confirm(kind, detail):
        """在模态确认中触发 GUI 草稿编辑或另存为。"""
        assert kind == 'normalize'
        if reentry == 'edit':
            controller.model.set(('monitor', 'target'), 'new-draft.exe')
        else:
            assert controller.save_as(lambda: destination)
        current = controller.model.document
        identity = (controller.model.document_generation, controller.model.revision,
                    current.path, current.fingerprint)
        assert identity != (generation, revision, source, fingerprint)
        return_identity.append(identity)
        return 'confirm'

    return_identity = []
    controller.confirm = confirm
    assert controller.start_cli() is None
    assert (controller.model.document_generation, controller.model.revision,
            controller.model.document.path, controller.model.document.fingerprint) == return_identity[0]
    assert source.read_bytes() == original
    assert controller.model.get(('monitor', 'common', 'max_wait')) == '-30s'
    if reentry == 'edit':
        assert controller.model.document_generation == generation
        assert controller.model.revision > revision
        assert controller.model.document.path == source
        assert controller.model.document.fingerprint == fingerprint
        assert controller.model.get(('monitor', 'target')) == 'new-draft.exe'
        assert controller.model.dirty
    else:
        assert controller.model.document_generation > generation
        assert controller.model.revision == 0
        assert controller.model.document.path == destination
        saved = read_document(destination)
        assert controller.model.document.fingerprint == saved.fingerprint
        assert saved.tree['monitor']['common']['max_wait'] == '-30s'
        assert not controller.model.dirty
    assert not controller.conflicted
    assert controller.launch_count == launch_count
    launch.assert_not_called()


@pytest.mark.parametrize('kind', ['normalize', 'repeat_launch'])
def test_start_confirmation_cancel_preserves_draft_identity_and_state(tmp_path, monkeypatch,
                                                                      kind):
    """确认取消不启动、不增加计数，亦不改变草稿基线与冲突状态。"""
    controller, confirm, _ = make_controller(tmp_path, answers=['cancel'])
    if kind == 'normalize':
        controller.model.set(('monitor', 'common', 'max_wait'), '-30s')
    assert controller.save()
    if kind == 'repeat_launch':
        controller.launch_count = 1
    document = controller.model.document
    original = document.path.read_bytes()
    draft = controller.model.snapshot()
    identity = (controller.model.document_generation, controller.model.revision,
                document.path, document.fingerprint)
    count = controller.launch_count
    conflict = controller.conflicted
    launch = Mock(return_value=LaunchResult(True, '已创建 CLI 进程'))
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)

    assert controller.start_cli() is None
    confirm.assert_called_once()
    assert confirm.call_args.args[0] == kind
    assert controller.cli_cancelled is True
    assert controller.launch_count == count
    assert (controller.model.document_generation, controller.model.revision,
            controller.model.document.path, controller.model.document.fingerprint) == identity
    assert controller.model.document == document
    assert controller.model.snapshot() == draft
    assert controller.model.dirty is False
    assert controller.conflicted == conflict
    assert document.path.read_bytes() == original
    launch.assert_not_called()


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
    original_validate = EditorModel.validate

    def changed_candidate(self, config_dir):
        """模拟候选清理中含有敏感值。"""
        candidate = original_validate(self, config_dir)
        return Candidate(candidate.tree, candidate.issues, (change,))

    monkeypatch.setattr(EditorModel, 'validate', changed_candidate)
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
    path.write_bytes(path.read_bytes() + b'\nunknown:\n  keep: value\n')
    controller.model.replace_document(read_document(path))
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
    original = EditorModel.validate

    def persistent_change(self, directory):
        """模拟规则在规范化之后仍报告同一项差异。"""
        candidate = original(self, directory)
        return Candidate(candidate.tree, candidate.issues, (change,))

    monkeypatch.setattr(EditorModel, 'validate', persistent_change)
    launch = Mock()
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
    assert controller.start_cli() is None
    launch.assert_not_called()
