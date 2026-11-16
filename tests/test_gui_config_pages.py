#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""监视业务表单的模型与可见控件测试。"""

import tkinter as tk

import pytest
import ctk_kit

from modules.config_document import new_document
from modules.gui.model import EditorModel
from modules.gui.pages.monitor import Page


@pytest.fixture(autouse=True)
def _fresh_default_root():
    """每个用例前解除已销毁的 Tk 默认根，使新建窗口接管字体资源。"""
    tk._default_root = None


def test_screenshot_page_flushes_before_reorder_and_refuses_on_conflict():
    """操作前补存失败时目标和模板均保持原样。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'window', 'target': 'A'},
        {'provider': 'window', 'target': 'B'}])
    model.set(('push', 'templates', 'on_end', 'content'),
              '{screenshot_1} {screenshot_2}')
    model.before_list_edit = Mock(return_value=False)
    page = ScreenshotPage(root, Mock(model=model))
    before = model.snapshot()
    try:
        page.move_target(0, 1)
        assert model.snapshot() == before
        assert model.before_list_edit.call_count == 1
    finally:
        root.destroy()


def test_screenshot_page_presave_reload_does_not_remove_new_document_target():
    """补存期间重载文档时旧目标序号不得删除新文档条目。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'window', 'target': 'old'},
        {'provider': 'window', 'target': 'original-other'}])
    page = ScreenshotPage(root, Mock(model=model))
    replacement = new_document()
    replacement.tree['push']['screenshot']['targets'] = [
        {'provider': 'window', 'target': 'new'},
        {'provider': 'window', 'target': 'other'}]
    model.before_list_edit = lambda: (model.replace_document(replacement), True)[1]
    try:
        page.move_target(0, 1)
        assert model.get(('push', 'screenshot', 'targets')) == [
            {'provider': 'window', 'target': 'new'},
            {'provider': 'window', 'target': 'other'}]
    finally:
        root.destroy()


def test_screenshot_page_retention_and_credentials():
    """切换清理、天数及凭证仅改动对应字段。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'image_host'), [
        {'provider': 'catbox', 'token': 'secret',
         'expiration': 20, 'options': {'max': 1}}])
    page = ScreenshotPage(root, Mock(model=model))
    try:
        old_revision = model.revision
        page.refresh()
        assert model.revision == old_revision
        page._change_days(1)
        assert model.get(('push', 'screenshot', 'retention', 'max_days')) == 15
        page.cleanup.select()
        page._change_cleanup()
        assert model.get(('push', 'screenshot', 'retention', 'enabled')) is True
        assert page.host_rows[0]['provider'].get() == 'catbox'
        assert model.get(('push', 'screenshot', 'image_host'))[0]['options'] == {'max': 1}
    finally:
        root.destroy()


def test_retention_missing_or_scalar_is_safe():
    """保留节缺失时默认 14 天，标量时禁用开关与箭头且不写入。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    document = new_document()
    del document.tree['push']['screenshot']['retention']
    model = EditorModel(document)
    page = ScreenshotPage(root, Mock(model=model))
    try:
        assert page.days.number.cget('text') == '14'
        page._change_days(1)
        assert model.get(('push', 'screenshot', 'retention', 'max_days')) == 15
        model.set(('push', 'screenshot', 'retention'), 'broken')
        page.refresh()
        assert page.days.number.cget('text') == '--'
        assert page.cleanup.cget('state') == 'disabled'
        assert page.days.up.cget('state') == 'disabled'
        assert page.days.down.cget('state') == 'disabled'
        page._change_cleanup()
        page._change_days(1)
        assert model.get(('push', 'screenshot', 'retention')) == 'broken'
    finally:
        root.destroy()


def test_remove_host_renumbers_only_host_option_errors(monkeypatch):
    """删除图床时仅重编号图床 options 错误，无关字段错误原样保留。"""
    from unittest.mock import Mock
    from modules.config_rules import Issue
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'image_host'), [
        {'provider': 'beeimg', 'token': 'a'},
        {'provider': 'beeimg', 'token': 'b'},
        {'provider': 'beeimg', 'token': 'c'}])
    unrelated = ('monitor', 'common', 'timeout_threshold')
    model.input_errors[unrelated] = Issue(
        unrelated, 'incomplete_input', 'error', '输入尚未完成')
    host_path = ('push', 'screenshot', 'image_host', 2, 'options')
    model.input_errors[host_path] = Issue(
        host_path, 'invalid_options', 'error', 'options 必须是有效 YAML 映射')
    page = ScreenshotPage(root, Mock(model=model))
    monkeypatch.setattr('modules.gui.pages.screenshot.messagebox.askyesno',
                        lambda *args, **kwargs: True)
    try:
        page.remove_host(0)
        assert model.input_errors[unrelated].code == 'incomplete_input'
        moved = ('push', 'screenshot', 'image_host', 1, 'options')
        assert model.input_errors[moved].path == moved
        assert host_path not in model.input_errors
    finally:
        root.destroy()


def test_non_string_target_fields_are_shown_safely():
    """ADB target 非字符串与 provider 非字符串时页面不崩溃且不改写配置。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'adb', 'target': 123},
        {'provider': [], 'target': 'demo'}])
    try:
        page = ScreenshotPage(root, Mock(model=model))
        assert len(page.target_rows) == 2
        assert model.get(('push', 'screenshot', 'targets')) == [
            {'provider': 'adb', 'target': 123},
            {'provider': [], 'target': 'demo'}]
    finally:
        root.destroy()


def test_window_target_selection_keeps_custom_out(monkeypatch):
    """窗口枚举使用现有 Win32 枚举，空列表和取消不破坏输入。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'window', 'target': 'manual', 'out': 'custom'}])
    page = ScreenshotPage(root, Mock(model=model))
    try:
        monkeypatch.setattr('modules.gui.pages.screenshot.win32gui.EnumWindows',
                            lambda callback, argument: None)
        monkeypatch.setattr('modules.gui.pages.screenshot.messagebox.showinfo',
                            lambda *args, **kwargs: None)
        page._choose_window(0)
        assert model.get(('push', 'screenshot', 'targets'))[0] == {
            'provider': 'window', 'target': 'manual', 'out': 'custom'}
    finally:
        root.destroy()


def test_adb_port_entry_supports_new_address_and_edit():
    """新 ADB 目标能输入端口，现有地址端口也能修改端口。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'adb', 'target': '127.0.0.1:16384'}])
    page = ScreenshotPage(root, Mock(model=model))
    try:
        port = page.target_rows[0]['port']
        port.delete(0, 'end')
        port.insert(0, '16385')
        assert model.get(('push', 'screenshot', 'targets'))[0]['target'] == '127.0.0.1:16385'
        page._change_provider(0, 'window')
        page._change_provider(0, 'adb')
        assert page.target_rows[0]['port'].get() == '16385'
    finally:
        root.destroy()


def test_screenshot_page_presave_failure_keeps_targets():
    """补存失败不会修改列表或模板。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.before_list_edit = Mock(return_value=False)
    app = Mock(model=model, capture=Mock(), upload=Mock())
    page = ScreenshotPage(root, app)
    try:
        page.add_target()
        page.remove_host(0)
        assert model.get(('push', 'screenshot', 'targets')) == []
        assert len(model.get(('push', 'screenshot', 'image_host'))) == 1
        assert model.before_list_edit.call_count == 2
    finally:
        root.destroy()


def test_adb_serial_not_destroyed_on_refresh():
    """不能拆分数字端口的 ADB serial 原文保留。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'adb', 'target': 'emulator-5554'}])
    app = Mock(model=model)
    page = ScreenshotPage(root, app)
    try:
        page.refresh()
        assert model.get(('push', 'screenshot', 'targets'))[0]['target'] == 'emulator-5554'
        page._set_adb(0, 'serial-edit', None)
        assert model.get(('push', 'screenshot', 'targets'))[0]['target'] == 'serial-edit'
    finally:
        root.destroy()


def test_screenshot_page_visible_and_safe_operations(monkeypatch):
    """截图页为 ctk 控件，排序删除同步模板且图床保留高级字段。"""
    from modules.gui.pages.screenshot import Page as ScreenshotPage
    from unittest.mock import Mock

    root = tk.Tk()
    root.withdraw()
    model = EditorModel(new_document())
    app = Mock(model=model, capture=Mock(), upload=Mock())
    model.before_list_edit = Mock(return_value=True)
    model.on_list_edit = Mock()
    page = ScreenshotPage(root, app)
    try:
        assert isinstance(page, ctk_kit.CTkScrollableFrame)
        assert all(not widget.winfo_class().startswith('T')
                   for widget in _descendants(page))
        model.set(('push', 'screenshot', 'targets'), [
            {'provider': 'window', 'target': 'A'},
            {'provider': 'window', 'target': 'B'}])
        model.set(('push', 'templates', 'on_end', 'content'),
                  '{screenshot_1} {screenshot_2}')
        page.refresh()
        page.move_target(0, 1)
        assert model.get(('push', 'templates', 'on_end', 'content')) == (
            '{screenshot_2} {screenshot_1}')
        assert model.before_list_edit.called
        assert model.on_list_edit.called
        assert page.target_rows[0]['number'].cget('text') == '01'
        monkeypatch.setattr('modules.gui.pages.screenshot.messagebox.askyesno',
                            lambda *a, **k: True)
        page.remove_target(0)
        assert model.get(('push', 'templates', 'on_end', 'content')) == (
            '{screenshot_1} ')
        model.set(('push', 'screenshot', 'image_host'), [
            {'provider': 'catbox', 'token': 'secret', 'options': {'x': 1},
             'expiration': 7}])
        page.refresh()
        page.add_host()
        assert model.get(('push', 'screenshot', 'image_host'))[1]['provider'] == 'beeimg'
        page.move_host(0, 1)
        assert model.get(('push', 'screenshot', 'image_host'))[1] == {
            'provider': 'catbox', 'token': 'secret',
            'options': {'x': 1}, 'expiration': 7}
    finally:
        root.destroy()


def test_screenshot_page_navigation_uses_new_page(tmp_path):
    """可见导航加载截图页而非旧附件桥接。"""
    from tests.test_gui_pages import make_app
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        assert isinstance(app.visible_pages['截图设置'], ScreenshotPage)
        assert app.show_page('截图设置')
    finally:
        root.destroy()


def _page():
    """创建真实窗口中的监视表单。"""
    root = tk.Tk()
    root.withdraw()
    model = EditorModel(new_document())
    page = Page(root, model)
    page.pack(fill='both', expand=True)
    return root, model, page


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


def _descendants(parent):
    """枚举所有真实子控件。"""
    for child in parent.winfo_children():
        yield child
        yield from _descendants(child)


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
