#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""消息推送可见页的真实编辑与发送边界。"""

import tkinter as tk
from unittest.mock import Mock

import ctk_kit

from modules.config_document import new_document
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
