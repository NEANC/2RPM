#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""监视业务表单的模型与可见控件测试。"""

import tkinter as tk
from pathlib import Path

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


def test_screenshot_order_arrows_respect_visible_boundaries():
    """目标与图床排序箭头仅在有相邻可见行时启用。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'window', 'target': 'A'},
        {'provider': 'window', 'target': 'B'}])
    model.set(('push', 'screenshot', 'image_host'), [
        {'provider': 'beeimg', 'token': 'a'},
        {'provider': 'beeimg', 'token': 'b'}])
    page = ScreenshotPage(root, Mock(model=model))
    try:
        for rows in (page.target_rows, page.host_rows):
            first, last = (row['number'].master for row in rows)
            first_buttons = [child for child in first.winfo_children()
                             if isinstance(child, ctk_kit.CTkFrame)][0].winfo_children()
            last_buttons = [child for child in last.winfo_children()
                            if isinstance(child, ctk_kit.CTkFrame)][0].winfo_children()
            assert [button.cget('state') for button in first_buttons] == ['disabled', 'normal']
            assert [button.cget('state') for button in last_buttons] == ['normal', 'disabled']
            assert all(button.cget('font') == page.target_rows[0]['number'].cget('font')
                       for button in first_buttons)
            assert all(button.cget('text_color') == page.target_rows[0]['number'].cget('text_color')
                       for button in first_buttons)
    finally:
        root.destroy()


def test_screenshot_delete_buttons_have_red_surface_and_border():
    """目标和图床删除入口都具有独立的红色系底色与边框。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'window', 'target': 'A'}])
    model.set(('push', 'screenshot', 'image_host'), [
        {'provider': 'beeimg', 'token': 'a'}])
    page = ScreenshotPage(root, Mock(model=model))
    try:
        for body in (page.targets_body, page.hosts_body):
            row = body.winfo_children()[0]
            delete = next(child for child in row.winfo_children()
                          if isinstance(child, ctk_kit.CTkButton) and child.cget('text') == '删除')
            assert delete.cget('border_width') == 1
            assert delete.cget('fg_color') != 'transparent'
            assert delete.cget('hover_color') != 'transparent'
    finally:
        root.destroy()


def test_host_rows_show_only_provider_and_credentials_and_preserve_legacy_fields(tmp_path):
    """旧 catbox 与高级字段留在模型和磁盘，但不展示高级编辑行。"""
    from unittest.mock import Mock
    from modules.config_document import read_document, save_document
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    document = new_document()
    original = [
        {'provider': 'catbox', 'token': 'legacy', 'expiration': 20,
         'options': {'max': 1}, 'custom': 'keep'},
        {'provider': 'beeimg', 'token': 'next', 'expiration': '1d',
         'options': {'album_id': 12}}]
    document.tree['push']['screenshot']['image_host'] = original
    path = tmp_path / 'hosts.yaml'
    root = tk.Tk()
    try:
        model = EditorModel(save_document(document, path, expected_fingerprint=None))
        page = ScreenshotPage(root, Mock(model=model))
        assert page.host_rows[0]['provider'].get() == 'catbox'
        assert page.host_rows[0]['token'].get() == 'legacy'
        assert all(set(row) == {'number', 'provider', 'token', 'source_index'}
                   for row in page.host_rows)
        assert len(page.hosts_body.winfo_children()) == 2
        page.host_rows[0]['token'].delete(0, 'end')
        page.host_rows[0]['token'].insert(0, 'updated')
        page.refresh()
        saved = save_document(model.materialize(), path,
                              expected_fingerprint=model.document.fingerprint)
        assert read_document(path).tree['push']['screenshot']['image_host'] == [
            {**original[0], 'token': 'updated'}, original[1]]
        assert saved.tree['push']['screenshot']['image_host'][0]['provider'] == 'catbox'
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
    revision = model.revision
    page = ScreenshotPage(root, Mock(model=model))
    try:
        assert page.days.number.get() == '14'
        assert model.revision == revision
        page._change_days(1)
        assert model.get(('push', 'screenshot', 'retention', 'max_days')) == 15
        model.set(('push', 'screenshot', 'retention'), 'broken')
        page.refresh()
        assert page.days.number.get() == '--'
        assert page.cleanup.cget('state') == 'disabled'
        assert page.days.up.cget('state') == 'disabled'
        assert page.days.down.cget('state') == 'disabled'
        page._change_cleanup()
        page._change_days(1)
        assert model.get(('push', 'screenshot', 'retention')) == 'broken'
    finally:
        root.destroy()


def test_retention_days_matches_port_stepper_and_edits_value():
    """保留天数与端口同宽同高，可输入且上箭头递增、下箭头递减。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'adb', 'target': '127.0.0.1:16384'}])
    page = ScreenshotPage(root, Mock(model=model))
    try:
        port = page.target_rows[0]['port']
        days = page.days.number
        assert isinstance(days, ctk_kit.CTkEntry)
        assert days.cget('width') == port.cget('width')
        assert days.cget('height') == port.cget('height')
        assert days.get() == '14'
        days.delete(0, 'end')
        days.insert(0, '7')
        assert model.get(('push', 'screenshot', 'retention', 'max_days')) == 7
        page.days.up._command()
        assert days.get() == '8'
        page.days.down._command()
        assert days.get() == '7'
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
    model.set(('push', 'screenshot', 'image_host'), [
        {'provider': [], 'token': 'secret'}])
    try:
        page = ScreenshotPage(root, Mock(model=model))
        assert len(page.target_rows) == 2
        menus = [child for row in page.targets_body.winfo_children()
                 for child in row.winfo_children()
                 if isinstance(child, ctk_kit.CTkOptionMenu)]
        assert menus[1].get() == '未知'
        assert page.host_rows[0]['provider'].get() == '未知'
        assert model.get(('push', 'screenshot', 'targets')) == [
            {'provider': 'adb', 'target': 123},
            {'provider': [], 'target': 'demo'}]
    finally:
        root.destroy()


def test_scalar_screenshot_section_disables_controls_and_keeps_model(tmp_path):
    """截图节为标量时提示结构异常，目标/图床/保留控件禁用且点击不写模型。"""
    from unittest.mock import Mock
    from modules.config_document import read_document, save_document
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    document = new_document()
    document.tree['push']['screenshot'] = 'bad'
    path = tmp_path / 'scalar-screenshot.yaml'
    model = EditorModel(save_document(document, path, expected_fingerprint=None))
    page = ScreenshotPage(root, Mock(model=model))
    before = model.snapshot()
    try:
        assert '结构无效' in page.targets_hint.cget('text')
        assert '结构无效' in page.hosts_hint.cget('text')
        assert page.add_target_button.cget('state') == 'disabled'
        assert page.add_host_button.cget('state') == 'disabled'
        assert page.cleanup.cget('state') == 'disabled'
        assert page.days.up.cget('state') == 'disabled'
        assert page.days.down.cget('state') == 'disabled'
        page.cleanup.toggle()
        page.days.up._command()
        page.days.down._command()
        assert model.snapshot() == before
        assert read_document(path).tree['push']['screenshot'] == 'bad'
    finally:
        root.destroy()


def test_list_creation_refuses_non_mapping_screenshot_section():
    """截图节为标量时列表创建只提示结构异常，不覆盖原值。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot'), 'broken')
    try:
        page = ScreenshotPage(root, Mock(model=model))
        page.add_target()
        page.add_host()
        assert model.get(('push', 'screenshot')) == 'broken'
        assert '结构无效' in page.targets_hint.cget('text')
        assert '结构无效' in page.hosts_hint.cget('text')
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


def test_window_target_search_and_clear_are_inside_one_field():
    """窗口目标的放大镜、输入与清除入口位于同一个搜索框内。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'window', 'target': '原窗口'}])
    page = ScreenshotPage(root, Mock(model=model))
    try:
        row = page.targets_body.winfo_children()[0]
        entry = page.target_rows[0]['entry']
        field = entry.master
        buttons = [child for child in field.winfo_children()
                   if isinstance(child, ctk_kit.CTkButton)]
        assert field.master is row
        assert [button.cget('text') for button in buttons] == ['⌕', '×']
        assert entry.get() == '原窗口'
        buttons[1]._command()
        assert entry.get() == ''
        assert model.get(('push', 'screenshot', 'targets', 0, 'target')) == ''
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


def test_adb_port_out_of_range_has_business_diagnostic(tmp_path):
    """数值端口越界时显示业务诊断，但不收紧 CLI 的 serial 规则。"""
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
        port.insert(0, '70000')
        path = ('push', 'screenshot', 'targets', 0, 'target')
        assert any(issue.path == path and issue.code == 'port_range'
                   for issue in model.validate(tmp_path).issues)
        assert model.get(path) == '127.0.0.1:70000'
    finally:
        root.destroy()


@pytest.mark.parametrize('operation, expected_index', [('move', 1), ('remove', 0)])
def test_adb_port_draft_follows_target_on_list_edit(monkeypatch, operation, expected_index):
    """重排或删除前一项时，端口原文与错误跟随仍在的目标。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'adb', 'target': 'A:1000'},
        {'provider': 'adb', 'target': 'B:2000'}])
    page = ScreenshotPage(root, Mock(model=model))
    monkeypatch.setattr('modules.gui.pages.screenshot.messagebox.askyesno',
                        lambda *args, **kwargs: True)
    try:
        port = page.target_rows[1 if operation == 'remove' else 0]['port']
        port.delete(0, 'end')
        port.insert(0, '-')
        if operation == 'move':
            page.move_target(0, 1)
        else:
            page.remove_target(0)
        path = ('push', 'screenshot', 'targets', expected_index, 'target')
        assert model.raw_inputs == {path: '-'}
        assert list(model.input_errors) == [path]
        assert model.input_errors[path].path == path
        assert page.target_rows[expected_index]['port'].get() == '-'
    finally:
        root.destroy()


def test_removing_adb_target_discards_its_port_draft(monkeypatch):
    """删除有未完成端口的目标时清理草稿及错误。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'adb', 'target': 'A:1000'},
        {'provider': 'adb', 'target': 'B:2000'}])
    page = ScreenshotPage(root, Mock(model=model))
    monkeypatch.setattr('modules.gui.pages.screenshot.messagebox.askyesno',
                        lambda *args, **kwargs: True)
    try:
        port = page.target_rows[0]['port']
        port.delete(0, 'end')
        port.insert(0, '-')
        page.remove_target(0)
        assert model.raw_inputs == {}
        assert model.input_errors == {}
        assert page.target_rows[0]['port'].get() == '2000'
    finally:
        root.destroy()


def test_adb_six_digit_port_edit_does_not_append_another_colon():
    """六位端口再编辑时只替换末尾端口，不污染地址。"""
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
        port.insert(0, '100000')
        port.delete(0, 'end')
        port.insert(0, '100001')
        assert model.get(('push', 'screenshot', 'targets', 0, 'target')) == '127.0.0.1:100001'
    finally:
        root.destroy()


def test_adb_port_stepper_replaces_raw_input():
    """箭头步进后原文与目标一致，不恢复旧的前导零草稿。"""
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
        port.insert(0, '00081')
        page._change_port(0, 1)
        path = ('push', 'screenshot', 'targets', 0, 'target')
        assert model.get(path) == '127.0.0.1:82'
        assert model.get_input(path) == '82'
        assert page.target_rows[0]['port'].get() == '82'
    finally:
        root.destroy()


def test_adb_port_stepper_after_incomplete_input_clears_error_and_saves(tmp_path):
    """端口输入 '-' 后步进恢复有效值，并允许保存到磁盘。"""
    from unittest.mock import Mock
    from modules.config_document import read_document, save_document
    from modules.gui.controller import Controller
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    document = new_document()
    document.tree['push']['screenshot']['targets'] = [
        {'provider': 'adb', 'target': '127.0.0.1:16384'}]
    saved = save_document(document, tmp_path / 'config.yaml', expected_fingerprint=None)
    controller = Controller(EditorModel(saved), confirm=lambda *_: 'confirm',
                            choose_path=lambda: None, cli_command=(),
                            runner=Mock(), program_dir=tmp_path)
    page = ScreenshotPage(root, Mock(model=controller.model))
    path = (*('push', 'screenshot', 'targets'), 0, 'target')
    try:
        port = page.target_rows[0]['port']
        port.delete(0, 'end')
        port.insert(0, '-')
        assert path in controller.model.input_errors
        page._change_port(0, 1)
        assert controller.model.get(path) == '127.0.0.1:16385'
        assert controller.model.get_input(path) == '16385'
        assert path not in controller.model.input_errors
        assert controller.save()
        assert read_document(saved.path).tree['push']['screenshot']['targets'][0]['target'] == '127.0.0.1:16385'
    finally:
        root.destroy()


@pytest.mark.parametrize(('field', 'raw', 'expected'), [
    ('port', '-', '127.0.0.1:16384'),
    ('port', '70000', '127.0.0.1:70000'),
    ('days', '-', 14),
])
def test_screenshot_numeric_drafts_survive_refresh_and_block_save_and_test(
        tmp_path, field, raw, expected):
    """不完整输入阻断补存与测试，可表示整数则保留供业务校验。"""
    from unittest.mock import Mock
    from modules.config_document import read_document, save_document
    from modules.gui.controller import Controller
    from modules.gui.pages.screenshot import Page as ScreenshotPage
    from modules.gui.save_coordinator import SaveCoordinator

    root = tk.Tk()
    document = new_document()
    document.tree['push']['screenshot']['targets'] = [
        {'provider': 'adb', 'target': '127.0.0.1:16384'}]
    saved = save_document(document, tmp_path / 'config.yaml', expected_fingerprint=None)
    controller = Controller(EditorModel(saved), confirm=lambda *_: 'confirm',
                            choose_path=lambda: None, cli_command=(),
                            runner=Mock(), program_dir=tmp_path)
    coordinator = SaveCoordinator(controller, lambda *_: None, lambda *_: None)
    controller.model.before_list_edit = coordinator.flush
    page = ScreenshotPage(root, Mock(model=controller.model))
    path = (('push', 'screenshot', 'targets', 0, 'target') if field == 'port'
            else ('push', 'screenshot', 'retention', 'max_days'))
    try:
        entry = page.target_rows[0]['port'] if field == 'port' else page.days.number
        entry.delete(0, 'end')
        entry.insert(0, raw)
        page.refresh()
        entry = page.target_rows[0]['port'] if field == 'port' else page.days.number
        assert entry.get() == raw
        assert controller.model.get_input(path) == raw
        assert controller.model.get(path) == expected
        if raw == '-':
            assert path in controller.model.input_errors
            assert not controller.save()
            assert controller.start_cli() is None
            assert not controller.model.before_list_edit()
            assert read_document(saved.path).tree['push']['screenshot'][
                'targets' if field == 'port' else 'retention'] == saved.tree['push']['screenshot'][
                    'targets' if field == 'port' else 'retention']
        else:
            assert path not in controller.model.input_errors
            assert controller.save()
            assert read_document(saved.path).tree['push']['screenshot']['targets'][0]['target'] == expected
            assert controller.model.get_input(path) == raw
            page.refresh()
            assert page.target_rows[0]['port'].get() == raw
    finally:
        root.destroy()


@pytest.mark.parametrize(('field', 'valid', 'expected'), [
    ('port', '00081', '127.0.0.1:81'),
    ('days', '007', 7),
])
def test_screenshot_numeric_drafts_recover_and_save_original_text(
        tmp_path, field, valid, expected):
    """输入恢复可表示值后解除阻断并保持原文，磁盘保存解析后的值。"""
    from unittest.mock import Mock
    from modules.config_document import read_document, save_document
    from modules.gui.controller import Controller
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    document = new_document()
    document.tree['push']['screenshot']['targets'] = [
        {'provider': 'adb', 'target': '127.0.0.1:16384'}]
    saved = save_document(document, tmp_path / 'config.yaml', expected_fingerprint=None)
    controller = Controller(EditorModel(saved), confirm=lambda *_: 'confirm',
                            choose_path=lambda: None, cli_command=(),
                            runner=Mock(), program_dir=tmp_path)
    page = ScreenshotPage(root, Mock(model=controller.model))
    path = (('push', 'screenshot', 'targets', 0, 'target') if field == 'port'
            else ('push', 'screenshot', 'retention', 'max_days'))
    try:
        entry = page.target_rows[0]['port'] if field == 'port' else page.days.number
        entry.delete(0, 'end')
        entry.insert(0, '-')
        assert path in controller.model.input_errors
        entry.delete(0, 'end')
        entry.insert(0, valid)
        assert path not in controller.model.input_errors
        assert controller.model.get(path) == expected
        assert controller.save()
        page.refresh()
        entry = page.target_rows[0]['port'] if field == 'port' else page.days.number
        assert entry.get() == valid
        assert controller.model.get_input(path) == valid
        assert read_document(saved.path).tree['push']['screenshot'][
            'targets' if field == 'port' else 'retention'][
                0 if field == 'port' else 'max_days'] == (
                    {'provider': 'adb', 'target': expected} if field == 'port' else expected)
    finally:
        root.destroy()


def test_adb_port_stepper_stacks_arrows_and_updates_target():
    """端口输入与右侧竖排箭头组成窄步进控件，箭头按方向修改目标。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'adb', 'target': '127.0.0.1:16384'}])
    page = ScreenshotPage(root, Mock(model=model))
    try:
        port = page.target_rows[0]['port']
        frame = port.master
        arrows = [child for child in frame.winfo_children()
                  if isinstance(child, ctk_kit.CTkFrame)
                  and all(isinstance(button, ctk_kit.CTkButton)
                          for button in child.winfo_children())]
        assert port.cget('width') == 74
        assert len(arrows) == 1
        up, down = arrows[0].winfo_children()
        assert up.cget('text') == '▲'
        assert down.cget('text') == '▼'
        assert up.pack_info()['side'] == 'top'
        assert down.pack_info()['side'] == 'top'
        up._command()
        assert model.get(('push', 'screenshot', 'targets'))[0]['target'] == '127.0.0.1:16385'
        down._command()
        assert model.get(('push', 'screenshot', 'targets'))[0]['target'] == '127.0.0.1:16384'
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


def test_adb_oversized_numeric_serial_builds_page_without_changing_target(tmp_path):
    """超过 Python 整数转换限制的数字后缀仍以 CLI 原始 serial 展示和保存。"""
    from unittest.mock import Mock
    from modules.config_document import read_document, save_document
    from modules.gui.controller import Controller
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    serial = 'device:' + '9' * 5000
    root = tk.Tk()
    document = new_document()
    document.tree['push']['screenshot']['targets'] = [
        {'provider': 'adb', 'target': serial}]
    saved = save_document(document, tmp_path / 'config.yaml', expected_fingerprint=None)
    controller = Controller(EditorModel(saved), confirm=lambda *_: 'confirm',
                            choose_path=lambda: None, cli_command=(),
                            runner=Mock(), program_dir=tmp_path)
    try:
        page = ScreenshotPage(root, Mock(model=controller.model))
        assert controller.model.get(('push', 'screenshot', 'targets', 0, 'target')) == serial
        assert page.target_rows[0]['port'].get() == ''
        page.refresh()
        assert controller.save()
        assert read_document(saved.path).tree['push']['screenshot']['targets'][0]['target'] == serial
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
