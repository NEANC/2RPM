#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""监视配置页行为（GUI 测试分片）。"""

import sys
import tkinter as tk
from pathlib import Path

import pytest
import ctk_kit

from modules.config_document import new_blank_document, new_document
from modules.gui.model import EditorModel
from modules.gui.pages.monitor import Page
from tests.gui_support import make_app
from tests.gui_support import arrow_y
from tests.gui_support import monitor_page as _page
from tests.gui_support import descendants as _descendants


def _blank_page():
    """创建空白草稿下的真实监视表单。"""
    root = tk.Tk()
    root.withdraw()
    model = EditorModel(new_blank_document())
    page = Page(root, model)
    page.pack(fill='both', expand=True)
    return root, model, page


def test_target_disabled_until_mode_selected():
    """未选运行方式时目标与类型禁用并提示，选择后才可编辑。"""
    root, model, page = _blank_page()
    try:
        assert page.mode.get() == '未选择'
        assert page.mode_hint.cget('text') == '请先选择运行方式'
        assert not page.file_entry.is_enabled()
        assert page.task_entry.cget('state') == 'disabled'
        assert page.target_type.cget('state') == 'disabled'
        assert page.browse_button.cget('state') == 'disabled'
        page._target_changed(r'C:\Apps\demo.exe')
        assert model.snapshot() == {}
        page._switch('等待目标运行')
        assert model.snapshot() == {'monitor': {'mode': 'watch'}}
        assert page.mode_hint.cget('text') == ''
        assert page.file_entry.is_enabled()
        assert page.task_entry.cget('state') == 'normal'
        assert page.target_type.cget('state') == 'normal'
        page._target_changed(r'C:\Apps\demo.exe')
        assert model.get(('monitor', 'target')) == r'C:\Apps\demo.exe'
    finally:
        root.destroy()


def test_target_text_hidden_without_mode():
    """有目标无运行方式时只隐藏显示，不删除已有配置值。"""
    document = new_blank_document()
    document.tree['monitor'] = {'target': r'C:\Apps\legacy.exe'}
    root = tk.Tk()
    root.withdraw()
    model = EditorModel(document)
    page = Page(root, model)
    page.pack(fill='both', expand=True)
    try:
        assert page.mode.get() == '未选择'
        assert page.file_entry.get() == ''
        assert page.task_entry.get() == ''
        assert not page.file_entry.is_enabled()
        assert model.snapshot() == {'monitor': {'target': r'C:\Apps\legacy.exe'}}
    finally:
        root.destroy()


def test_blank_monitor_spinner_does_not_seed_default():
    """空白任务回溯的箭头只提示输入数字，不写入零或默认值。"""
    root, model, page = _blank_page()
    try:
        page._switch('等待目标运行')
        before = model.revision
        page.lookback._arrows.event_generate(
            '<Button-1>', x=5, y=arrow_y(page.lookback, 'up'))
        root.update()
        assert page.lookback.entry.get() == ''
        assert 'watch' not in model.snapshot()['monitor']
        assert model.revision == before
        assert page.number_hint.cget('text') == '请先输入数字'
    finally:
        root.destroy()


def test_monitor_uses_ctk_business_controls():
    """监视业务字段使用 ctk-kit 卡片、搜索框与步进控件。"""
    root, _, page = _page()
    try:
        assert isinstance(page, ctk_kit.CTkScrollableFrame)
        assert isinstance(page.mode, ctk_kit.CTkSegmentedButton)
        assert isinstance(page.file_entry, ctk_kit.SearchBox)
        assert isinstance(page.task_entry, ctk_kit.CTkEntry)
        assert isinstance(page.lookback, ctk_kit.Spinner)
        assert isinstance(page.fields[('monitor', 'common', 'max_wait')][1],
                          ctk_kit.CTkEntry)
        cards = [widget for widget in page.winfo_children()
                 if isinstance(widget, ctk_kit.Card)]
        assert len(cards) == 3
        assert all(not widget.winfo_class().startswith('T')
                   for widget in _descendants(page))
    finally:
        root.destroy()


def test_target_type_switches_editable_widget():
    """切换目标类型后字段指向对应输入，搜索框清除写回空目标。"""
    root, model, page = _page()
    try:
        page.set_field(('monitor', 'target'), r'C:\Apps\demo.exe')
        page._select_type('task')
        assert page.fields[('monitor', 'target')][1] is page.task_entry
        page._select_type('file')
        assert page.fields[('monitor', 'target')][1] is page.file_entry._entry
        page.file_entry.clear()
        root.update()
        assert model.get(('monitor', 'target')) == ''
    finally:
        root.destroy()


def test_lookback_spinner_writes_int_and_keeps_incomplete_draft():
    """整数步进正常写回整数，无法表示的原文保留为草稿。"""
    root, model, page = _page()
    try:
        path = ('monitor', 'watch', 'lookback_minutes')
        page.set_field(path, '42')
        assert model.get(path) == 42
        page.set_field(path, '-')
        assert model.get(path) == 42
        assert model.get_input(path) == '-'
    finally:
        root.destroy()


@pytest.mark.parametrize('event', ['<Return>', '<FocusOut>'])
@pytest.mark.parametrize('raw', ['', '-', '12x', '007', '1000', '-1'])
def test_lookback_native_event_keeps_raw_input(event, raw):
    """真实内部输入事件不让原生步进器钳制或替换用户原文。"""
    root, model, page = _page()
    path = ('monitor', 'watch', 'lookback_minutes')
    try:
        entry = page.lookback.entry._entry
        entry.delete(0, 'end')
        entry.insert(0, raw)
        root.update()
        before = model.get(path)
        entry.event_generate(event)
        root.update()
        assert page.lookback.entry.get() == raw
        assert model.get_input(path) == raw
        assert model.get(path) == (int(raw) if raw.lstrip('-').isdigit() else before)
        assert (path in model.input_errors) == (raw in ('', '-', '12x'))
    finally:
        root.destroy()


@pytest.mark.parametrize(('raw', 'direction', 'expected'), [
    ('007', 'up', '8'), ('7', 'down', '6'), ('999', 'up', '999'),
    ('0', 'down', '0'), ('', 'up', ''), ('-', 'up', ''), ('12x', 'up', ''),
])
def test_lookback_arrow_steps_only_parseable_input(raw, direction, expected):
    """真实箭头仅对可解析数字按一步移动，非法草稿不被覆盖。"""
    root, model, page = _page()
    path = ('monitor', 'watch', 'lookback_minutes')
    try:
        page.lookback.entry._entry.delete(0, 'end')
        page.lookback.entry._entry.insert(0, raw)
        root.update()
        before = model.get(path)
        page.lookback._arrows.event_generate(
            '<Button-1>', x=5, y=arrow_y(page.lookback, direction))
        root.update()
        assert page.lookback.entry.get() == (raw if raw in ('', '-', '12x') else expected)
        assert model.get(path) == (before if raw in ('', '-', '12x') else int(expected))
        assert model.get_input(path) == (raw if raw in ('', '-', '12x') else expected)
    finally:
        root.destroy()


@pytest.mark.parametrize('direction', ['up', 'down'])
def test_lookback_real_arrow_preserves_integer_over_dynamic_limit(direction):
    """真实箭头遇到超过当前整数位数限制的原文时不抛异常、不覆写。"""
    previous_limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    root = None
    try:
        root, model, page = _page()
        path = ('monitor', 'watch', 'lookback_minutes')
        raw = '9' * (sys.get_int_max_str_digits() + 1)
        errors = []
        root.report_callback_exception = lambda *error: errors.append(error)
        entry = page.lookback.entry._entry
        entry.delete(0, 'end')
        entry.insert(0, raw)
        root.update()
        before = model.get(path)
        page.lookback._arrows.event_generate(
            '<Button-1>', x=5, y=arrow_y(page.lookback, direction))
        root.update()
        assert not errors
        assert entry.get() == raw
        assert model.get_input(path) == raw
        assert model.get(path) == before
        assert path in model.input_errors
    finally:
        if root is not None:
            root.destroy()
        sys.set_int_max_str_digits(previous_limit)


@pytest.mark.parametrize(('raw', 'saved'), [('', False), ('-', False),
                                               ('12x', False), ('007', True),
                                               ('1000', True), ('-1', True)])
def test_lookback_native_input_save_rules(tmp_path, raw, saved):
    """未完成草稿不能落盘，完整越界值不经控件钳制即可落盘。"""
    from modules.config_document import read_document
    from tests.test_gui_controller import make_controller

    root = tk.Tk()
    controller, _, _ = make_controller(tmp_path)
    page = Page(root, controller.model)
    path = ('monitor', 'watch', 'lookback_minutes')
    try:
        entry = page.lookback.entry._entry
        entry.delete(0, 'end')
        entry.insert(0, raw)
        entry.event_generate('<Return>')
        root.update()
        assert controller.save() is saved
        assert (tmp_path / 'a.yaml').exists() is saved
        if saved:
            assert read_document(tmp_path / 'a.yaml').tree['monitor']['watch']['lookback_minutes'] == int(raw)
            assert not controller.model.input_errors
        else:
            assert controller.model.get_input(path) == raw
    finally:
        root.destroy()


@pytest.mark.parametrize('raw', ['', '-', '12x'])
def test_lookback_incomplete_native_input_blocks_cli(tmp_path, monkeypatch, raw):
    """回车后的未完成数字禁止用磁盘旧值启动独立 CLI。"""
    from unittest.mock import Mock
    from tests.test_gui_controller import make_controller

    root = tk.Tk()
    controller, _, _ = make_controller(tmp_path)
    page = Page(root, controller.model)
    launch = Mock()
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
    try:
        entry = page.lookback.entry._entry
        entry.delete(0, 'end')
        entry.insert(0, raw)
        entry.event_generate('<Return>')
        root.update()
        assert controller.start_cli() is None
        launch.assert_not_called()
        assert controller.model.get_input(('monitor', 'watch', 'lookback_minutes')) == raw
    finally:
        root.destroy()


def test_lookback_refresh_preserves_draft_and_active_entry():
    """刷新不会重建输入框或吞掉未完成草稿与插入光标。"""
    root, model, page = _page()
    try:
        entry = page.lookback.entry._entry
        entry.delete(0, 'end')
        entry.insert(0, '12x')
        entry.icursor(1)
        root.update()
        page.refresh()
        assert page.lookback.entry._entry is entry
        assert entry.get() == '12x'
        assert entry.index('insert') == 1
        assert model.get_input(('monitor', 'watch', 'lookback_minutes')) == '12x'
    finally:
        root.destroy()


def test_custom_cwd_survives_open():
    """刷新、模式切换与打开配置均不覆盖已有工作目录。"""
    root, model, page = _page()
    try:
        model.set(('monitor', 'launch', 'cwd'), r'C:\custom')
        model.set(('monitor', 'target'), 'notepad.exe')
        page.refresh()
        page._switch('launch')
        page._switch('watch')
        assert model.get(('monitor', 'launch', 'cwd')) == r'C:\custom'
    finally:
        root.destroy()


def test_user_target_edit_updates_cwd():
    """仅主动编辑启动文件目标时用文件所在目录填入 cwd。"""
    root, model, page = _page()
    try:
        page._switch('launch')
        model.set(('monitor', 'launch', 'cwd'), r'C:\custom')
        page.set_field(('monitor', 'target'), r'C:\Apps\program.exe')
        assert model.get(('monitor', 'launch', 'cwd')) == r'C:\Apps'
    finally:
        root.destroy()


def test_switch_to_task_clears_args_only_on_user_action():
    """主动改为计划任务清空参数，普通刷新不清空。"""
    root, model, page = _page()
    try:
        page._switch('launch')
        model.set(('monitor', 'launch', 'args'), '--existing')
        model.set(('monitor', 'target'), r'\Tasks\demo')
        page.refresh()
        assert model.get(('monitor', 'launch', 'args')) == '--existing'
        page._select_type('task')
        assert model.get(('monitor', 'launch', 'args')) is None
    finally:
        root.destroy()


def test_pasted_target_updates_model_without_key_release():
    """右键菜单粘贴不产生按键事件，也应写回草稿。"""
    root, model, page = _page()
    try:
        root.clipboard_clear()
        root.clipboard_append(r'C:\Apps\paste.exe')
        root.update()
        page.file_entry.set('')
        entry = page.file_entry._entry._entry
        entry.focus_force()
        entry.event_generate('<<Paste>>')
        root.update()
        assert model.get(('monitor', 'target')) == r'C:\Apps\paste.exe'
    finally:
        root.destroy()


def test_target_type_switch_preserves_uncommitted_target():
    """仅切换目标类型不能用隐藏输入覆盖现有目标。"""
    root, model, page = _page()
    try:
        page.set_field(('monitor', 'target'), r'C:\Apps\demo.exe')
        page._select_type('task')
        assert model.get(('monitor', 'target')) == r'C:\Apps\demo.exe'
        page._select_type('file')
        assert model.get(('monitor', 'target')) == r'C:\Apps\demo.exe'
    finally:
        root.destroy()


def test_existing_bare_name_is_preserved():
    """已有裸进程名保留并明确提示 YAML 编辑。"""
    root, model, page = _page()
    try:
        page.refresh()
        assert model.get(('monitor', 'target')) == 'notepad.exe'
        assert 'YAML' in page.preview.cget('text')
    finally:
        root.destroy()


def test_complete_invalid_gui_target_saves_to_disk_but_cannot_start(tmp_path, monkeypatch):
    """页面输入的完整业务无效值经控制器保存后仍阻止启动。"""
    from unittest.mock import Mock
    from modules.config_document import read_document
    from tests.test_gui_controller import make_controller

    root = tk.Tk()
    controller, _, _ = make_controller(tmp_path)
    page = Page(root, controller.model)
    launch = Mock()
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
    try:
        page.set_field(('monitor', 'target'), 'new.exe')
        assert not controller.model.input_errors
        assert controller.save()
        path = tmp_path / 'a.yaml'
        assert read_document(path).tree['monitor']['target'] == 'new.exe'
        assert controller.start_cli() is None
        assert read_document(path).tree['monitor']['target'] == 'new.exe'
        launch.assert_not_called()
    finally:
        root.destroy()


def test_open_old_bare_name_refreshes_page_and_still_starts(tmp_path, monkeypatch):
    """旧裸名从磁盘打开并刷新页面后按原有规则启动。"""
    from unittest.mock import Mock
    from modules.config_document import save_document
    from modules.gui.launcher import LaunchResult
    from tests.test_gui_controller import make_controller

    old = new_document()
    old.tree['monitor']['mode'] = 'watch'
    old.tree['monitor']['target'] = 'notepad.exe'
    saved = save_document(old, tmp_path / 'old.yaml', expected_fingerprint=None)
    root = tk.Tk()
    controller, _, _ = make_controller(tmp_path)
    page = Page(root, controller.model)
    launch = Mock(return_value=LaunchResult(True, '已创建 CLI 进程'))
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
    try:
        assert controller.open(saved.path)
        page.refresh()
        assert page.file_entry.get() == 'notepad.exe'
        assert controller.model.get(('monitor', 'target')) == 'notepad.exe'
        assert not controller.model.dirty
        assert not any(issue.severity == 'error'
                       for issue in controller.model.validate(saved.path.parent).issues)
        result = controller.start_cli()
        assert result is not None and result.created
        assert launch.call_args.args[1] == saved.path.resolve()
    finally:
        root.destroy()


@pytest.mark.parametrize('value', ['new.exe', r'.\new.exe', r'C:\Apps\new.bat'])
def test_watch_gui_file_requires_absolute_exe_but_saves_complete_draft(value):
    """GUI 新输入不接受裸名、相对路径或非 EXE，但原文仍可保存。"""
    root, model, page = _page()
    try:
        page.set_field(('monitor', 'target'), value)
        assert model.get(('monitor', 'target')) == value
        assert not model.input_errors
        assert any(issue.path == ('monitor', 'target') and issue.severity == 'error'
                   for issue in model.validate(Path('C:/config')).issues)
    finally:
        root.destroy()


def test_watch_gui_file_absolute_exe_is_valid():
    """GUI 完整 EXE 路径通过目标校验。"""
    root, model, page = _page()
    try:
        page.set_field(('monitor', 'target'), r'C:\Apps\new.exe')
        assert not any(issue.path == ('monitor', 'target')
                       for issue in model.validate(Path('C:/config')).issues)
    finally:
        root.destroy()


@pytest.mark.parametrize('value', ['NewTask', 'Tasks\\NewTask', r'C:\Apps\new.exe'])
def test_gui_task_requires_full_task_path(value):
    """计划任务输入必须为反斜杠开头的完整任务路径。"""
    root, model, page = _page()
    try:
        page._select_type('task')
        page.set_field(('monitor', 'target'), value)
        assert model.get(('monitor', 'target')) == value
        assert not model.input_errors
        assert any(issue.path == ('monitor', 'target') and issue.severity == 'error'
                   for issue in model.validate(Path('C:/config')).issues)
    finally:
        root.destroy()


def test_existing_bare_name_stays_valid_until_gui_edits_target():
    """打开、切页及保存旧裸名不新增 GUI 输入限制。"""
    root, model, page = _page()
    try:
        page.refresh()
        assert not any(issue.path == ('monitor', 'target')
                       for issue in model.validate(Path('C:/config')).issues)
        page.set_field(('monitor', 'target'), 'new.exe')
        assert any(issue.path == ('monitor', 'target')
                   for issue in model.validate(Path('C:/config')).issues)
    finally:
        root.destroy()


def test_gui_target_rule_survives_save_but_external_reload_resets_it(tmp_path):
    """保存无效业务值不丢诊断；外部重载按已有配置规则处理。"""
    from modules.config_document import read_document, save_document

    root, model, page = _page()
    try:
        page.set_field(('monitor', 'target'), 'new.exe')
        saved = save_document(model.materialize(), tmp_path / 'monitor.yaml',
                              expected_fingerprint=None)
        model.accept_saved(saved)
        assert any(issue.code == 'gui_file_path'
                   for issue in model.validate(Path('C:/config')).issues)
        model.replace_document(read_document(saved.path))
        assert not any(issue.code == 'gui_file_path'
                       for issue in model.validate(Path('C:/config')).issues)
    finally:
        root.destroy()
