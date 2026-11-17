#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""消息推送可见页的真实编辑与发送边界。"""

import tkinter as tk
from unittest.mock import Mock

import ctk_kit
import pytest

from modules.config_document import new_document
from modules.gui.forms import provider_fields
from modules.gui.model import EditorModel


def test_notification_page_channels_and_preview_follow_draft(monkeypatch):
    """通道独立编辑、预览截图提示不发送也不丢旧参数。"""
    from modules.gui.pages.notification_ctk import Page

    root = tk.Tk()
    tk._default_root = root
    root.withdraw()
    model = EditorModel(new_document())
    model.before_list_edit = Mock(return_value=True)
    model.on_list_edit = Mock()
    app = Mock(model=model, notify=Mock())
    page = Page(root, app)
    try:
        monkeypatch.setattr('modules.gui.pages.notification_ctk.messagebox.askyesno',
                            lambda *args, **kwargs: True)
        assert isinstance(page, ctk_kit.CTkScrollableFrame)
        assert not any(widget.winfo_class().startswith('T') for widget in descendants(page))
        original = model.get(page.channels_path)
        page.add_channel('telegram')
        assert len(model.get(page.channels_path)) == len(original) + 1
        assert 'token' in page.channel_entries
        page.channel_entries['token'].insert(0, 'new-token')
        assert model.get(page.channels_path)[-1]['token'] == 'new-token'
        page.remove_channel(len(original))
        assert model.get(page.channels_path) == original
        assert model.on_list_edit.call_count == 2
        model.set(('push', 'templates', 'on_timeout', 'capture_screenshot'), False)
        page.preview_event('on_timeout')
        assert '截图被阻止' in page.preview_labels['on_timeout'].cget('text')
        app.notify.assert_not_called()
    finally:
        root.destroy()


def test_notification_page_variable_insertion_and_hidden_fields():
    """在正文光标处插入合法变量且刷新不覆盖隐藏截图开关。"""
    from modules.gui.pages.notification_ctk import Page

    root = tk.Tk()
    tk._default_root = root
    model = EditorModel(new_document())
    app = Mock(model=model, notify=Mock())
    page = Page(root, app)
    try:
        path = ('push', 'templates', 'on_end', 'content')
        model.set(('push', 'templates', 'on_end', 'capture_screenshot'), False)
        page.expand_event('on_end')
        editor = page.fields[path][1]
        editor.delete('1.0', 'end')
        editor.insert('1.0', 'hello world')
        editor.mark_set('insert', '1.6')
        page.insert_variable('on_end', 'process_pid')
        assert model.get(path) == 'hello {process_pid}world'
        page.refresh()
        assert model.get(('push', 'templates', 'on_end', 'capture_screenshot')) is False
        assert ('push', 'templates', 'on_end', 'capture_screenshot') not in page.fields
    finally:
        root.destroy()


def test_channel_add_requires_valid_provider_and_presave():
    """无选项或补存失败不会新增通道。"""
    from modules.gui.pages.notification_ctk import Page

    root = tk.Tk()
    tk._default_root = root
    model = EditorModel(new_document())
    model.before_list_edit = Mock(return_value=False)
    model.on_list_edit = Mock()
    page = Page(root, Mock(model=model, notify=Mock()))
    try:
        old = model.get(page.channels_path)
        page.add_channel('请选择通道')
        page.add_channel('bark')
        assert model.get(page.channels_path) == old
        model.on_list_edit.assert_not_called()
    finally:
        root.destroy()


def test_channel_remove_refuses_stale_index_after_reload(monkeypatch):
    """补存时文档代次改变，旧行号不得删除新文档通道。"""
    from modules.gui.pages.notification_ctk import Page

    root = tk.Tk()
    tk._default_root = root
    model = EditorModel(new_document())
    page = Page(root, Mock(model=model, notify=Mock()))
    try:
        old = model.get(page.channels_path)
        replacement = new_document()
        replacement.tree['push']['push_channel_settings']['channels'] = [
            {'provider': 'bark', 'key': 'external'}, *old]
        model.before_list_edit = lambda: (model.replace_document(replacement), True)[1]
        monkeypatch.setattr('modules.gui.pages.notification_ctk.messagebox.askyesno',
                            lambda *args, **kwargs: True)
        page.remove_channel(0)
        assert model.get(page.channels_path)[0]['provider'] == 'bark'
    finally:
        root.destroy()


def test_non_string_channel_provider_is_unknown():
    """通道 provider 非字符串时按未知通道展示且不崩溃。"""
    from modules.gui.pages.notification_ctk import Page

    root = tk.Tk()
    tk._default_root = root
    model = EditorModel(new_document())
    model.set(('push', 'push_channel_settings', 'channels'),
              [{'provider': [], 'token': 'secret'}])
    page = Page(root, Mock(model=model, notify=Mock()))
    try:
        assert '未知' in page.channel_rows[0]['toggle'].cget('text')
        assert page.channel_rows[0]['status'].cget('text') == '需要填写参数'
        assert model.get(('push', 'push_channel_settings', 'channels')) == [
            {'provider': [], 'token': 'secret'}]
    finally:
        root.destroy()


def test_channel_whole_header_toggles_except_delete(monkeypatch):
    """标题空白与状态徽标均能折叠，删除只影响对应通道。"""
    from modules.gui.pages.notification_ctk import Page

    root = tk.Tk()
    tk._default_root = root
    root.geometry('900x680+100+100')
    model = EditorModel(new_document())
    model.before_list_edit = Mock(return_value=True)
    model.on_list_edit = Mock()
    page = Page(root, Mock(model=model, notify=Mock()))
    try:
        monkeypatch.setattr('modules.gui.pages.notification_ctk.messagebox.askyesno',
                            lambda *args, **kwargs: True)
        page.add_channel('telegram')
        page.expand_channel(0)
        page.pack(fill='both', expand=True)
        root.update()
        assert page._expanded_channel == 0
        page.channel_rows[0]['header']._canvas.event_generate('<Button-1>')
        assert page._expanded_channel is None
        root.update_idletasks()
        page.channel_rows[0]['status']._canvas.event_generate('<Button-1>')
        assert page._expanded_channel == 0
        before = len(model.get(page.channels_path))
        page.channel_rows[0]['delete'].invoke()
        assert len(model.get(page.channels_path)) == before - 1
        assert page._expanded_channel is None
    finally:
        root.destroy()


def test_channel_outer_margin_click_toggles_real_row():
    """点击通道外层边距时折叠，再点击时展开。"""
    from modules.gui.pages.notification_ctk import Page

    root = tk.Tk()
    tk._default_root = root
    root.geometry('900x680+100+100')
    page = Page(root, Mock(model=EditorModel(new_document()), notify=Mock()))
    page.pack(fill='both', expand=True)
    try:
        page.expand_channel(0)
        root.update()
        row = page.channel_rows[0]['header'].master
        assert isinstance(row, ctk_kit.CTkFrame)
        assert row.winfo_width() > row.winfo_children()[0].winfo_width()
        row._canvas.event_generate('<Button-1>', x=2, y=2)
        assert page._expanded_channel is None
        root.update()
        page.channel_rows[0]['header'].master._canvas.event_generate('<Button-1>', x=2, y=2)
        assert page._expanded_channel == 0
    finally:
        root.destroy()


@pytest.mark.parametrize(('removed', 'expected'), [(0, 0), (1, None), (2, 1)])
def test_channel_remove_preserves_other_expanded_row(monkeypatch, removed, expected):
    """删除其他通道保持当前展开，删除前方通道时同步可见索引。"""
    from modules.gui.pages.notification_ctk import Page

    root = tk.Tk()
    tk._default_root = root
    model = EditorModel(new_document())
    model.set(('push', 'push_channel_settings', 'channels'), [
        {'provider': 'serverchan', 'sckey': key} for key in ('first', 'middle', 'last')])
    model.before_list_edit = Mock(return_value=True)
    model.on_list_edit = Mock()
    page = Page(root, Mock(model=model, notify=Mock()))
    monkeypatch.setattr('modules.gui.pages.notification_ctk.messagebox.askyesno',
                        lambda *args, **kwargs: True)
    try:
        page.expand_channel(1)
        page.channel_rows[removed]['delete'].invoke()
        assert page._expanded_channel == expected
        assert [item['sckey'] for item in model.get(page.channels_path)] == [
            key for index, key in enumerate(('first', 'middle', 'last')) if index != removed]
        if expected is not None:
            assert page.channel_rows[expected]['provider'] is not None
            assert model.get(page.channels_path)[expected]['sckey'] == 'middle'
        model.on_list_edit.assert_called_once()
    finally:
        root.destroy()


def test_channel_status_badge_updates_immediately():
    """填入和清除必填字段同步更新文字、底色与边框。"""
    from modules.gui.pages.notification_ctk import Page

    root = tk.Tk()
    tk._default_root = root
    root.withdraw()
    model = EditorModel(new_document())
    model.before_list_edit = Mock(return_value=True)
    model.on_list_edit = Mock()
    page = Page(root, Mock(model=model, notify=Mock()))
    try:
        page.add_channel('telegram')
        index = len(page.channel_rows) - 1
        row = page.channel_rows[index]
        required = [field for field in provider_fields(
            model.get(page.channels_path)[-1]['provider']) if field.required]
        assert required
        badge = row['status']
        entry = page.channel_entries[required[0].name]
        initial = (badge.cget('fg_color'), badge.cget('border_color'))
        for field in required:
            widget = page.channel_entries[field.name]
            widget.delete(0, 'end')
            widget.insert(0, 'filled')
        assert badge.cget('text') == '已配置'
        assert badge.cget('border_width') == 1
        assert (badge.cget('fg_color'), badge.cget('border_color')) != initial
        entry.delete(0, 'end')
        assert badge.cget('text') == '需要填写参数'
        assert (badge.cget('fg_color'), badge.cget('border_color')) == initial
        assert row['delete'].cget('fg_color') != 'transparent'
        assert row['delete'].cget('border_width') == 1
    finally:
        root.destroy()


def test_add_channel_dialog_centers_on_parent():
    """添加通道窗在主窗口的几何中心。"""
    from modules.gui.pages.notification_ctk import Page

    root = tk.Tk()
    tk._default_root = root
    root.geometry('900x680+120+100')
    page = Page(root, Mock(model=EditorModel(new_document()), notify=Mock()))
    page.pack(fill='both', expand=True)
    root.update_idletasks()
    try:
        page.open_add_dialog()
        dialog = next(item for item in page.winfo_children() if isinstance(item, ctk_kit.CTkToplevel))
        dialog.update_idletasks()
        assert abs(dialog.winfo_rootx() + dialog.winfo_width() / 2
                   - (root.winfo_rootx() + root.winfo_width() / 2)) <= 2
        assert abs(dialog.winfo_rooty() + dialog.winfo_height() / 2
                   - (root.winfo_rooty() + root.winfo_height() / 2)) <= 2
    finally:
        root.destroy()


def test_preview_button_tracks_open_and_closed_state():
    """预览按钮与内容开合状态一致且刷新后仍一致。"""
    from modules.gui.pages.notification_ctk import Page

    root = tk.Tk()
    tk._default_root = root
    root.withdraw()
    app = Mock(model=EditorModel(new_document()), notify=Mock())
    page = Page(root, app)
    try:
        page.expand_event('on_end')
        assert page.preview_buttons['on_end'].cget('text') == '预览消息'
        page.preview_buttons['on_end'].invoke()
        assert page.preview_buttons['on_end'].cget('text') == '收起预览'
        assert 'on_end' in page._preview_events
        page.refresh()
        assert page.preview_buttons['on_end'].cget('text') == '收起预览'
        page.preview_buttons['on_end'].invoke()
        assert page.preview_buttons['on_end'].cget('text') == '预览消息'
        assert 'on_end' not in page._preview_events
        app.notify.assert_not_called()
    finally:
        root.destroy()


def descendants(widget):
    """递归检查可见控件类别。"""
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def test_preview_tolerates_non_mapping_screenshot_section():
    """截图节为标量时预览仍可渲染，并提示截图条件待校验。"""
    from modules.gui.pages.notification_ctk import Page

    root = tk.Tk()
    tk._default_root = root
    model = EditorModel(new_document())
    model.set(('push', 'screenshot'), 'broken')
    page = Page(root, Mock(model=model, notify=Mock()))
    try:
        page.expand_event('on_end')
        page.preview_event('on_end')
        text = page.preview_labels['on_end'].cget('text')
        assert text.startswith('示例预览')
        assert '截图条件：待校验' in text
    finally:
        root.destroy()


def test_preview_tolerates_non_mapping_template_and_push():
    """事件模板或 push 为标量时预览给出无效提示而不崩溃。"""
    from modules.gui.pages.notification_ctk import Page

    root = tk.Tk()
    tk._default_root = root
    for path, value in ((('push', 'templates', 'on_end'), 'broken'),
                        (('push',), 'broken')):
        model = EditorModel(new_document())
        model.set(path, value)
        page = Page(root, Mock(model=model, notify=Mock()))
        try:
            page.expand_event('on_end')
            page.preview_event('on_end')
            assert '通知模板变量或格式无效' in page.preview_labels['on_end'].cget('text')
            page.insert_variable('on_end', 'process_pid')
        finally:
            page.destroy()
    root.destroy()
