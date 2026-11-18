#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""截图配置页行为（GUI 测试分片）。"""

import tkinter as tk
from pathlib import Path

import pytest
import ctk_kit
from ctk_kit import tokens

from modules.config_document import new_document
from modules.gui.appearance import FONT_TITLE
from modules.gui.model import EditorModel
from modules.gui.pages.monitor import Page
from tests.gui_support import make_app
from tests.gui_support import descendants as _descendants


def _type_number(control, text):
    """在原生 Spinner（或其内部输入框）里模拟键入并触发页面写回。"""
    entry = control.entry if isinstance(control, ctk_kit.Spinner) else control
    entry.set(text)


def _number_text(control):
    """读取原生 Spinner（或其内部输入框）里的真实文本。"""
    entry = control.entry if isinstance(control, ctk_kit.Spinner) else control
    return entry.get()


def test_screenshot_header_and_cards_follow_prototype():
    """页头两个主按钮与三张原生卡片使用原型尺寸和间距。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    try:
        page = ScreenshotPage(root, Mock(model=EditorModel(new_document())))
        header = page.winfo_children()[0]
        title = header.winfo_children()[0]
        assert title.cget('text') == '截图设置'
        assert title.cget('font') == FONT_TITLE
        buttons = {child.cget('text'): child
                   for child in header.winfo_children()
                   if isinstance(child, ctk_kit.CTkButton)}
        assert set(buttons) == {'截图测试', '上传测试'}
        for button in buttons.values():
            assert button.cget('fg_color') == tokens.COLOR_ACCENT
            assert button.cget('height') == 52
            assert button.cget('width') == 120
        scale = header._get_widget_scaling()
        assert round(header.pack_info()['padx'] / scale) == 28
        cards = [child for child in page.winfo_children()
                 if isinstance(child, ctk_kit.Card)]
        assert len(cards) == 3
        for card in cards:
            assert card.cget('corner_radius') == 20
            assert card.cget('border_width') == 2
            card_scale = card._get_widget_scaling()
            assert round(card.pack_info()['padx'] / card_scale) == 28
            assert tuple(round(value / card_scale)
                         for value in card.pack_info()['pady']) == (20, 0)
        for button in (page.add_target_button, page.add_host_button):
            assert button.cget('fg_color') == tokens.COLOR_ACCENT
        assert page.add_target_button.cget('text') == '＋ 添加目标'
        assert page.add_host_button.cget('text') == '＋ 添加图床'
    finally:
        root.destroy()


def test_screenshot_rows_use_native_controls():
    """目标与图床行使用 DropField、SearchBox、Spinner 和危险删除按钮。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'window', 'target': '窗口 A'},
        {'provider': 'adb', 'target': '127.0.0.1:16384'}])
    model.set(('push', 'screenshot', 'image_host'), [
        {'provider': 'catbox', 'token': 'secret'}])
    try:
        page = ScreenshotPage(root, Mock(model=model))
        window_row = page.target_rows[0]
        assert isinstance(window_row['entry'], ctk_kit.SearchBox)
        assert window_row['provider'].get() == '窗口'
        assert window_row['provider']._values() == ['窗口', 'ADB']
        adb_row = page.target_rows[1]
        assert isinstance(adb_row['entry'], ctk_kit.CTkEntry)
        assert isinstance(adb_row['port'], ctk_kit.Spinner)
        assert adb_row['provider'].get() == 'ADB'
        assert adb_row['port'].cget('width') == page.days.cget('width')
        assert adb_row['port'].cget('height') == page.days.cget('height')
        host_row = page.host_rows[0]
        assert isinstance(host_row['provider'], ctk_kit.DropField)
        assert host_row['provider'].get() == 'catbox'
        assert 'catbox' in host_row['provider']._values()
        assert host_row['token'].cget('show') == '●'
        deletes = [child for row in page.targets_body.winfo_children() +
                   page.hosts_body.winfo_children()
                   for child in row.winfo_children()
                   if isinstance(child, ctk_kit.CTkButton)
                   and child.cget('text') == '删除']
        assert len(deletes) == 3
        assert all(button.cget('fg_color') != 'transparent' for button in deletes)
    finally:
        root.destroy()


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


def test_screenshot_order_indices_use_native_spinner():
    """目标与图床的两位序号由原生 Spinner 显示。"""
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
            assert all(isinstance(row['number'], ctk_kit.Spinner) for row in rows)
            assert [row['number'].get() for row in rows] == ['01', '02']
    finally:
        root.destroy()


def test_screenshot_delete_buttons_have_red_surface_and_border():
    """目标和图床删除入口都使用原生危险色按钮。"""
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
            assert delete.cget('fg_color') != 'transparent'
            assert delete.cget('text_color') != 'transparent'
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
        assert not page.cleanup.is_enabled()
        assert page.days.is_readonly()
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
        days = page.days
        assert isinstance(days, ctk_kit.Spinner)
        assert days.pack_info()['side'] == 'right'
        assert isinstance(port, ctk_kit.Spinner)
        assert days.cget('width') == port.cget('width')
        assert days.cget('height') == port.cget('height')
        assert days.number.get() == '14'
        days.number.set('7')
        assert model.get(('push', 'screenshot', 'retention', 'max_days')) == 7
        days._step(1)
        assert days.number.get() == '8'
        days._step(-1)
        assert days.number.get() == '7'
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
                 for child in _descendants(row)
                 if isinstance(child, ctk_kit.DropField)]
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
        assert not page.cleanup.is_enabled()
        assert page.days.is_readonly()
        page.cleanup.toggle()
        page._change_days(1)
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
    """窗口目标使用原生 SearchBox，放大镜不可点且清除回写空目标。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'window', 'target': '原窗口'}])
    page = ScreenshotPage(root, Mock(model=model))
    try:
        entry = page.target_rows[0]['entry']
        assert isinstance(entry, ctk_kit.SearchBox)
        assert entry._icon.winfo_exists()
        assert entry._clear.cget('text') == '×'
        assert entry.get() == '原窗口'
        browse = next(child for child in entry.master.winfo_children()
                      if isinstance(child, ctk_kit.CTkButton))
        assert browse.cget('text') == '浏览'
        entry.clear()
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
        port.entry.set('16385')
        assert model.get(('push', 'screenshot', 'targets'))[0]['target'] == '127.0.0.1:16385'
        page._change_provider(0, 'window')
        page._change_provider(0, 'adb')
        assert page.target_rows[0]['port'].entry.get() == '16385'
    finally:
        root.destroy()


def test_switching_adb_to_window_discards_only_its_port_error_and_saves(tmp_path):
    """切换首个目标仅清理其端口错误，第二目标修正前不得保存。"""
    from unittest.mock import Mock
    from modules.config_document import read_document, save_document
    from modules.gui.controller import Controller
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    document = new_document()
    document.tree['push']['screenshot']['targets'] = [
        {'provider': 'adb', 'target': 'A:1000'},
        {'provider': 'adb', 'target': 'B:2000'}]
    saved = save_document(document, tmp_path / 'config.yaml', expected_fingerprint=None)
    controller = Controller(EditorModel(saved), confirm=lambda *_: 'confirm',
                            choose_path=lambda: None, cli_command=(),
                            runner=Mock(), program_dir=tmp_path)
    model = controller.model
    page = ScreenshotPage(root, Mock(model=model))
    first = ('push', 'screenshot', 'targets', 0, 'target')
    second = ('push', 'screenshot', 'targets', 1, 'target')
    try:
        other_port = page.target_rows[1]['port']
        other_port.entry.set('-')
        port = page.target_rows[0]['port']
        port.entry.set('-')
        assert model.raw_inputs[first] == '-'
        assert first in model.input_errors
        assert model.raw_inputs[second] == '-'
        assert second in model.input_errors

        page._change_provider(0, 'window')

        assert page.target_rows[0]['port'] is None
        assert first not in model.raw_inputs
        assert first not in model.input_errors
        assert model.raw_inputs[second] == '-'
        assert model.input_errors[second].path == second
        assert page.target_rows[1]['port'].entry.get() == '-'
        assert not controller.save()
        assert read_document(saved.path).tree['push']['screenshot']['targets'] == [
            {'provider': 'adb', 'target': 'A:1000'},
            {'provider': 'adb', 'target': 'B:2000'}]

        other_port = page.target_rows[1]['port']
        other_port.entry.set('2001')
        assert second not in model.input_errors
        assert controller.save()
        assert read_document(saved.path).tree['push']['screenshot']['targets'] == [
            {'provider': 'window', 'target': 'A:1000'},
            {'provider': 'adb', 'target': 'B:2001'}]
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
        port.entry.set('70000')
        path = ('push', 'screenshot', 'targets', 0, 'target')
        assert any(issue.path == path and issue.code == 'port_range'
                   for issue in model.validate(tmp_path).issues)
        assert model.get(path) == '127.0.0.1:70000'
    finally:
        root.destroy()


def test_adb_oversized_port_keeps_raw_and_validates_safely(tmp_path):
    """超长数字端口保留原文，模型校验返回不泄漏输入的错误。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    path = ('push', 'screenshot', 'targets', 0, 'target')
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'adb', 'target': '127.0.0.1:16384'}])
    page = ScreenshotPage(root, Mock(model=model))
    raw = '9' * 4400
    try:
        port = page.target_rows[0]['port']
        port.entry.set(raw)
        assert model.get_input(path) == raw
        assert model.get(path) == '127.0.0.1:16384'
        candidate = model.validate(tmp_path)
        assert any(issue.path == path and issue.code == 'invalid_port'
                   and issue.severity == 'error' for issue in candidate.issues)
        assert raw not in repr(candidate.issues)
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
        port.entry.set('-')
        if operation == 'move':
            page.move_target(0, 1)
        else:
            page.remove_target(0)
        path = ('push', 'screenshot', 'targets', expected_index, 'target')
        assert model.raw_inputs == {path: '-'}
        assert list(model.input_errors) == [path]
        assert model.input_errors[path].path == path
        assert page.target_rows[expected_index]['port'].entry.get() == '-'
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
        port.entry.set('-')
        page.remove_target(0)
        assert model.raw_inputs == {}
        assert model.input_errors == {}
        assert page.target_rows[0]['port'].entry.get() == '2000'
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
        port.entry.set('100000')
        port.entry.set('100001')
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
        port.entry.set('00081')
        page._change_port(0, 1)
        path = ('push', 'screenshot', 'targets', 0, 'target')
        assert model.get(path) == '127.0.0.1:82'
        assert model.get_input(path) == '82'
        assert page.target_rows[0]['port'].entry.get() == '82'
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
        port.entry.set('-')
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
        _type_number(entry, raw)
        page.refresh()
        entry = page.target_rows[0]['port'] if field == 'port' else page.days.number
        assert _number_text(entry) == raw
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
            assert page.target_rows[0]['port'].entry.get() == raw
    finally:
        root.destroy()


def test_adb_oversized_numeric_port_stays_draft_and_blocks_save(tmp_path):
    """真实端口控件收到 4301 位数字时保留原文并阻断落盘。"""
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
    path = ('push', 'screenshot', 'targets', 0, 'target')
    raw = '9' * 4301
    callback_errors = []
    root.report_callback_exception = lambda *args: callback_errors.append(args)
    try:
        port = page.target_rows[0]['port']
        port.entry.set(raw)
        assert port.entry.get() == raw
        assert controller.model.get_input(path) == raw
        assert controller.model.get(path) == '127.0.0.1:16384'
        issue = controller.model.input_errors[path]
        assert issue.severity == 'error'
        assert issue.code == 'invalid_port'
        assert '端口' in issue.message
        page.refresh()
        assert page.target_rows[0]['port'].entry.get() == raw
        assert not controller.save()
        assert read_document(saved.path).tree['push']['screenshot']['targets'][0]['target'] == '127.0.0.1:16384'
        assert not callback_errors
        page.target_rows[0]['port'].entry.set('16385')
        assert path not in controller.model.input_errors
        assert controller.save()
        assert read_document(saved.path).tree['push']['screenshot']['targets'][0]['target'] == '127.0.0.1:16385'
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
        _type_number(entry, '-')
        assert path in controller.model.input_errors
        _type_number(entry, valid)
        assert path not in controller.model.input_errors
        assert controller.model.get(path) == expected
        assert controller.save()
        page.refresh()
        entry = page.target_rows[0]['port'] if field == 'port' else page.days.number
        assert _number_text(entry) == valid
        assert controller.model.get_input(path) == valid
        assert read_document(saved.path).tree['push']['screenshot'][
            'targets' if field == 'port' else 'retention'][
                0 if field == 'port' else 'max_days'] == (
                    {'provider': 'adb', 'target': expected} if field == 'port' else expected)
    finally:
        root.destroy()


def test_adb_port_stepper_stacks_arrows_and_updates_target():
    """ADB 端口使用原生 Spinner，箭头按方向修改目标。"""
    from unittest.mock import Mock
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    model = EditorModel(new_document())
    model.set(('push', 'screenshot', 'targets'), [
        {'provider': 'adb', 'target': '127.0.0.1:16384'}])
    page = ScreenshotPage(root, Mock(model=model))
    try:
        port = page.target_rows[0]['port']
        assert isinstance(port, ctk_kit.Spinner)
        assert isinstance(port.entry, ctk_kit.CTkEntry)
        assert port._arrows.winfo_exists()
        page._change_port(0, 1)
        assert model.get(('push', 'screenshot', 'targets'))[0]['target'] == '127.0.0.1:16385'
        page._change_port(0, -1)
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
        assert page.target_rows[0]['port'].entry.get() == ''
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
        assert page.target_rows[0]['number'].get() == '01'
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
    from tests.gui_support import make_app
    from modules.gui.pages.screenshot import Page as ScreenshotPage

    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        assert isinstance(app.visible_pages['截图设置'], ScreenshotPage)
        assert app.show_page('截图设置')
    finally:
        root.destroy()
