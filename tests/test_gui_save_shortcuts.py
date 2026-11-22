#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""窗口级 Ctrl+S 与保存前正文同步的真实 Tk 事件回归。"""

import time
import tkinter as tk
from unittest.mock import Mock

import pytest

from modules.config_document import read_document
from modules.gui import controller as controller_module
from tests.gui_support import make_app


def press(widget):
    """在控件上合成一次 Ctrl+S 按下事件。"""
    widget.event_generate('<KeyPress-s>', state=0x4)


def release(widget):
    """在控件上合成一次 Ctrl+S 释放事件。"""
    widget.event_generate('<KeyRelease-s>', state=0x4)


def release_on_root(root):
    """正文控件会被保存后的页面重建销毁时，改由主窗接收释放事件。"""
    root.focus_force()
    root.update()
    root.event_generate('<KeyRelease-s>', state=0x4)


def _focused_input(root, app, case):
    """装配一种输入控件并返回承载焦点的内部控件。"""
    if case == 'entry':
        app.show_page('监视设置')
        root.update()
        page = app.visible_pages['监视设置']
        widget = page.fields[('monitor', 'common', 'max_wait')][1]._entry
        widget.focus_force()
        widget.insert(0, '30s')
        return widget
    if case == 'search':
        app.show_page('监视设置')
        root.update()
        page = app.visible_pages['监视设置']
        page._switch('等待目标运行')
        root.update()
        page.set_field(('monitor', 'target'), 'C:/tools/target.exe')
        widget = page.file_entry._entry
        widget.focus_force()
        return widget
    if case == 'number':
        app.show_page('监视设置')
        root.update()
        page = app.visible_pages['监视设置']
        page.set_field(('monitor', 'watch', 'lookback_minutes'), 37)
        widget = page.lookback.entry._entry
        widget.focus_force()
        return widget
    app.show_page('消息推送设置')
    root.update()
    page = app.visible_pages['消息推送设置']
    page.expand_event('on_end')
    root.update()
    widget = page.fields[('push', 'templates', 'on_end', 'content')][1]._textbox
    widget.focus_force()
    widget.insert('1.0', '事件正文草稿')
    return widget


EXPECTED = {
    'entry': lambda tree: tree['monitor']['common']['max_wait'] == '30s',
    'search': lambda tree: tree['monitor']['target'] == 'C:/tools/target.exe',
    'number': lambda tree: tree['monitor']['watch']['lookback_minutes'] == 37,
    'text': lambda tree: tree['push']['templates']['on_end']['content'] == '事件正文草稿',
}


@pytest.mark.parametrize('case', ['entry', 'search', 'number', 'text'])
def test_ctrl_s_saves_latest_focused_input(tmp_path, case):
    """四类输入控件焦点下按下并释放 Ctrl+S，最新实际输入全部落盘。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path, blank=True)
        widget = _focused_input(root, app, case)
        root.update()
        press(widget)
        root.update()
        if case == 'text':
            release_on_root(root)
        else:
            release(widget)
        root.update()
        assert EXPECTED[case](read_document(tmp_path / 'a.yaml').tree)
    finally:
        root.destroy()


def test_ctrl_s_untouched_text_keeps_empty_mapping(tmp_path):
    """未编辑的空正文经快捷键保存后仍不生成配置键。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path, blank=True)
        app.show_page('消息推送设置')
        root.update()
        page = app.visible_pages['消息推送设置']
        page.expand_event('on_end')
        root.update()
        widget = page.fields[('push', 'templates', 'on_end', 'content')][1]._textbox
        widget.focus_force()
        root.update()
        press(widget)
        root.update()
        release_on_root(root)
        root.update()
        assert read_document(tmp_path / 'a.yaml').tree == {}
    finally:
        root.destroy()


def test_ctrl_s_repeat_and_modal_release(tmp_path, monkeypatch):
    """长按只保存一次，模态内释放与聚焦对账后下一次完整按键仍有效。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path, blank=True)
        app.show_page('监视设置')
        root.update()
        widget = app.visible_pages['监视设置'].fields[
            ('monitor', 'common', 'max_wait')][1]._entry
        widget.focus_force()
        root.update()
        saves = Mock()
        app.save_shortcuts.save = saves
        shortcuts = app.save_shortcuts

        # 长按：自动重复的 KeyPress 只算一次请求。
        press(widget)
        press(widget)
        press(widget)
        root.update()
        assert saves.call_count == 1
        assert shortcuts._held is True

        # 无模态时在主窗上释放即清锁，下一次完整按键有效。
        release_on_root(root)
        root.update()
        assert shortcuts._held is False
        press(widget)
        release(widget)
        root.update()
        assert saves.call_count == 2

        # 模态框内的真实释放不进入主窗绑定，按下锁必须保持。
        press(widget)
        root.update()
        assert saves.call_count == 3
        # 焦点移出主窗时物理键仍按住，焦点对账不得清锁。
        monkeypatch.setattr('modules.gui.save_shortcuts.save_key_down',
                            lambda: True)
        dialog = tk.Toplevel(root)
        marker = tk.Label(dialog, text='模态')
        marker.pack()
        root.update()
        marker.focus_force()
        dialog.grab_set()
        root.update()
        assert root.focus_get() is marker
        assert shortcuts._held is True
        # 随后在模态框内松开物理键，释放事件不进入主窗绑定。
        monkeypatch.setattr('modules.gui.save_shortcuts.save_key_down',
                            lambda: False)
        release(marker)
        root.update()
        assert shortcuts._held is True

        # 物理键已在模态框内松开：焦点回流主窗时按状态对账清锁。
        root.event_generate('<FocusIn>')
        root.update()
        assert shortcuts._held is False
        dialog.grab_release()
        dialog.destroy()
        root.update()
        widget.focus_force()
        root.update()
        press(widget)
        release(widget)
        root.update()
        assert saves.call_count == 4

        # 仍按住时对话框关闭不重触发：焦点对账保留锁。
        press(widget)
        assert saves.call_count == 5
        monkeypatch.setattr('modules.gui.save_shortcuts.save_key_down',
                            lambda: True)
        dialog = tk.Toplevel(root)
        dialog.grab_set()
        root.update()
        dialog.grab_release()
        dialog.destroy()
        root.update()
        widget.focus_force()
        root.update()
        widget.event_generate('<FocusIn>')
        widget.event_generate('<FocusOut>')
        root.update()
        assert shortcuts._held is True
        press(widget)
        root.update()
        assert saves.call_count == 5
        release_on_root(root)
        root.update()
        assert shortcuts._held is False
        press(widget)
        release(widget)
        root.update()
        assert saves.call_count == 6

        # 失焦且物理键已松开时清锁。
        press(widget)
        assert saves.call_count == 7
        monkeypatch.setattr('modules.gui.save_shortcuts.save_key_down',
                            lambda: False)
        widget.event_generate('<FocusOut>')
        root.update()
        assert shortcuts._held is False
        press(widget)
        release(widget)
        root.update()
        assert saves.call_count == 8

        # 选择器取消后下一次完整按键仍能保存。
        app.save_shortcuts.save = app.save
        app.model.set(('log', 'log_directory'), 'logs')
        app.controller.choose_path = Mock(return_value=None)
        press(widget)
        release(widget)
        root.update()
        assert not (tmp_path / 'a.yaml').exists()
        app.controller.choose_path = lambda: tmp_path / 'a.yaml'
        press(widget)
        release(widget)
        root.update()
        assert read_document(tmp_path / 'a.yaml').tree['log']['log_directory'] == 'logs'
    finally:
        root.destroy()


def test_ctrl_s_modal_guard_allows_explicit_save_choice(tmp_path):
    """模态期间额外快捷键被忽略，明确的保存与另存为仍完成事务。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path, blank=True)
        app.show_page('监视设置')
        root.update()
        widget = app.visible_pages['监视设置'].fields[
            ('monitor', 'common', 'max_wait')][1]._entry
        widget.focus_force()
        root.update()
        saves = Mock()
        app.save_shortcuts.save = saves

        dialog = tk.Toplevel(root)
        own = Mock()
        dialog.bind('<Control-KeyPress-s>', lambda _event: own())
        marker = tk.Label(dialog, text='模态')
        marker.pack()
        marker.focus_force()
        dialog.grab_set()
        root.update()
        marker.event_generate('<KeyPress-s>', state=0x4)
        root.update()
        assert own.call_count == 1
        assert saves.call_count == 0

        # 模态 grab 期间的自动重复事件仍会到达主窗处理，但必须被丢弃。
        event = type('Event', (), {'keysym': 's', 'widget': widget})()
        assert app.save_shortcuts._on_press(event) is None
        assert saves.call_count == 0
        dialog.grab_release()
        dialog.destroy()
        root.update()

        app.model.set(('log', 'log_directory'), 'logs')
        app.controller.confirm = lambda kind, _detail: ('save' if kind == 'unsaved'
                                                       else 'confirm')
        assert app.controller.new()
        assert read_document(tmp_path / 'a.yaml').tree['log']['log_directory'] == 'logs'

        app.model.set(('log', 'log_directory'), 'logs-elsewhere')
        app.controller.conflicted = True
        destination = tmp_path / 'conflict.yaml'
        assert app.controller.resolve_external('save_as', destination)
        assert read_document(destination).tree['log']['log_directory'] == 'logs-elsewhere'
    finally:
        root.destroy()


def test_ctrl_s_release_without_control_clears_lock(tmp_path):
    """先松 Ctrl 再松 S 时释放事件仍清锁，下一次完整按键有效。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path, blank=True)
        app.show_page('监视设置')
        root.update()
        widget = app.visible_pages['监视设置'].fields[
            ('monitor', 'common', 'max_wait')][1]._entry
        widget.focus_force()
        root.update()
        saves = Mock()
        app.save_shortcuts.save = saves

        widget.event_generate('<KeyPress-s>', state=0x4)
        root.update()
        assert saves.call_count == 1
        assert app.save_shortcuts._held is True

        widget.event_generate('<KeyRelease-s>', state=0)
        root.update()
        assert app.save_shortcuts._held is False

        widget.event_generate('<KeyPress-s>', state=0x4)
        root.update()
        assert saves.call_count == 2
        widget.event_generate('<KeyRelease-s>', state=0)
        root.update()
        assert app.save_shortcuts._held is False

        event = type('Event', (), {'keysym': 's', 'widget': widget})()
        assert app.save_shortcuts._on_press(event) == 'break'
        assert saves.call_count == 3
    finally:
        root.destroy()


def test_ctrl_s_save_runs_inside_transaction(tmp_path, monkeypatch):
    """快捷键保存包进既有事务：执行中暂停自动保存，结束后不再写第二次。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path, blank=True)
        app.show_page('监视设置')
        root.update()
        widget = app.visible_pages['监视设置'].fields[
            ('monitor', 'common', 'max_wait')][1]._entry
        widget.focus_force()
        widget.insert(0, '30s')
        root.update()
        press(widget)
        release(widget)
        root.update()
        path = tmp_path / 'a.yaml'
        assert read_document(path).tree['monitor']['common']['max_wait'] == '30s'

        real_save = controller_module.save_document
        writes = []

        def counted(document, target, **kwargs):
            """记录每次真实写盘的目标路径。"""
            writes.append(target)
            return real_save(document, target, **kwargs)

        monkeypatch.setattr('modules.gui.controller.save_document', counted)
        depths = []
        original = app.controller._save_result

        def observed(*args, **kwargs):
            """在保存执行期间记录协调器事务深度。"""
            depths.append(app.save_coordinator.depth)
            return original(*args, **kwargs)

        monkeypatch.setattr(app.controller, '_save_result', observed)
        app.model.set(('monitor', 'common', 'max_wait'), '45s')
        assert app.save_coordinator.pending is not None
        assert writes == []

        press(widget)
        release(widget)
        root.update()
        assert depths == [1]
        assert app.save_coordinator.depth == 0
        assert app.save_coordinator.pending is None
        assert writes == [path]
        assert read_document(path).tree['monitor']['common']['max_wait'] == '45s'
        assert not app.model.dirty

        time.sleep(0.6)
        root.update()
        assert writes == [path]
    finally:
        root.destroy()
