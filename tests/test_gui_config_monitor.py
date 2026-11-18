#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""监视配置页行为（GUI 测试分片）。"""

import tkinter as tk
from pathlib import Path

import pytest
import ctk_kit

from modules.config_document import new_document
from modules.gui.model import EditorModel
from modules.gui.pages.monitor import Page
from tests.gui_support import make_app
from tests.gui_support import monitor_page as _page
from tests.gui_support import descendants as _descendants


def test_monitor_uses_ctk_business_controls():
    """监视业务字段不使用 ttk 可见控件。"""
    root, _, page = _page()
    try:
        assert isinstance(page, ctk_kit.CTkScrollableFrame)
        assert isinstance(page.mode, ctk_kit.CTkSegmentedButton)
        assert isinstance(page.fields[('monitor', 'target')][1], ctk_kit.CTkEntry)
        assert all(not widget.winfo_class().startswith('T')
                   for widget in _descendants(page))
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
    """右键粘贴或程序内输入事件应写回草稿。"""
    root, model, page = _page()
    try:
        page.file_entry.delete(0, 'end')
        page.file_entry.insert(0, r'C:\Apps\paste.exe')
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
