#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""空白草稿在真实窗口中的装配、浏览与焦点回归。"""

import tkinter as tk
from unittest.mock import Mock

import ctk_kit
import pytest

from modules.gui.app import SAVE_GUIDANCE_MESSAGE
from modules.gui.pages.notification_ctk import EVENTS
from tests.gui_support import descendants, make_app


BUSINESS_PAGES = ('主页', '监视设置', '截图设置', '消息推送设置',
                  '外部程序设置', '日志设置')


def test_all_pages_keep_blank_tree(tmp_path):
    """浏览所有业务页、展开事件并聚焦离焦后，空白草稿仍为空映射。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path, blank=True)
        root.update()
        revision = app.model.revision

        for name in BUSINESS_PAGES:
            assert app.show_page(name)
            root.update()
            page = app.visible_pages[name]
            page.refresh()
            root.update()

        push = app.visible_pages['消息推送设置']
        for event, _label in EVENTS:
            push.expand_event(event)
            root.update()

        for widget in descendants(push):
            if isinstance(widget, ctk_kit.CTkTextbox):
                widget._textbox.focus_force()
                root.update()
                widget._textbox.event_generate('<KeyRelease>')
                widget._textbox.event_generate('<FocusOut>')
                root.update()
            elif isinstance(widget, (ctk_kit.CTkEntry, ctk_kit.CTkOptionMenu,
                                     ctk_kit.SearchBox)):
                widget.focus_force()
                root.update()

        screenshot = app.visible_pages['截图设置']
        for widget in descendants(screenshot):
            if isinstance(widget, (ctk_kit.CTkEntry, ctk_kit.CTkOptionMenu)):
                widget.focus_force()
                root.update()
        app.show_page('主页')
        root.update()

        assert app.model.snapshot() == {}
        assert app.model.revision == revision
        assert push.channel_rows == []
        assert screenshot.target_rows == []
        assert screenshot.host_rows == []
        assert push.channel_hint.cget('text') == ''
        assert push.events_hint.cget('text') == ''
        assert screenshot.targets_hint.cget('text') == ''
        assert screenshot.hosts_hint.cget('text') == ''
        assert screenshot.days.number.get() == ''
        assert screenshot.cleanup.get() == '未设置'
        assert all('示例' not in hint.cget('text') for hint in
                   (push.channel_hint, push.events_hint,
                    screenshot.targets_hint, screenshot.hosts_hint))
    finally:
        root.destroy()


@pytest.mark.parametrize('edited', [False, True], ids=['blank', 'edited'])
@pytest.mark.parametrize('entry', ['capture', 'upload', 'notify', 'cli'])
def test_unbound_actions_show_save_guidance(tmp_path, monkeypatch, entry, edited):
    """无绑定路径时四类业务入口只显示首次保存指导，不产生任何副作用。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path, blank=True)
        info = Mock()
        error = Mock()
        choose = Mock()
        open_picker = Mock()
        save_picker = Mock()
        worker = Mock()
        runner_start = app.controller.runner.start
        launch = Mock()
        monkeypatch.setattr('modules.gui.app.messagebox.showinfo', info)
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', error)
        monkeypatch.setattr('modules.gui.app.filedialog.askopenfilename', open_picker)
        monkeypatch.setattr('modules.gui.app.filedialog.asksaveasfilename', save_picker)
        monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
        monkeypatch.setattr(app.test_session, 'start', worker)
        app.controller.choose_path = choose
        app.test_dialogs.open_capture = Mock()
        app.test_dialogs.open_upload = Mock()
        if edited:
            app.model.set(('log', 'log_directory'), 'logs')
            assert app.model.dirty

        if entry == 'capture':
            app.visible_pages['截图设置'].capture_button.invoke()
        elif entry == 'upload':
            app.visible_pages['截图设置'].upload_button.invoke()
        elif entry == 'notify':
            app.visible_pages['消息推送设置'].notify_button.invoke()
        else:
            app.start_cli()

        info.assert_called_once_with('首次保存', SAVE_GUIDANCE_MESSAGE, parent=root)
        error.assert_not_called()
        choose.assert_not_called()
        open_picker.assert_not_called()
        save_picker.assert_not_called()
        worker.assert_not_called()
        runner_start.assert_not_called()
        launch.assert_not_called()
        app.test_dialogs.open_capture.assert_not_called()
        app.test_dialogs.open_upload.assert_not_called()
        assert app.test_session.active_request is None
        assert app.model.document.path is None
        assert app.status.cget('text') == SAVE_GUIDANCE_MESSAGE
    finally:
        root.destroy()


def test_unsaved_draft_allows_repeated_list_edits(tmp_path, monkeypatch):
    """未绑定路径时先填文本，连续增删排序列表只改内存，不写盘也不弹选择器。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path, blank=True)
        choose = Mock(side_effect=AssertionError('不应选择保存路径'))
        written = Mock(side_effect=AssertionError('不应写入磁盘'))
        app.controller.choose_path = choose
        monkeypatch.setattr('modules.gui.controller.save_document', written)
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', Mock())
        monkeypatch.setattr('modules.gui.app.messagebox.showinfo', Mock())
        monkeypatch.setattr(
            'modules.gui.pages.notification_ctk.messagebox.askyesno',
            lambda *args, **kwargs: True)
        monkeypatch.setattr(
            'modules.gui.pages.screenshot.messagebox.askyesno',
            lambda *args, **kwargs: True)

        app.model.set(('log', 'log_directory'), 'logs')
        assert app.model.dirty

        push = app.visible_pages['消息推送设置']
        shot = app.visible_pages['截图设置']
        assert push.channel_rows == []
        assert shot.target_rows == []
        assert shot.host_rows == []

        push.add_channel('bark')
        push.add_channel('smtp')
        assert len(push.channel_rows) == 2
        assert app.save_coordinator.pending is None
        push.change_channel_provider(0, 'discord')
        push.add_channel('qmsg')
        assert len(push.channel_rows) == 3
        push.remove_channel(1)
        assert len(push.channel_rows) == 2
        assert app.save_coordinator.pending is None

        shot.add_target()
        shot.add_target()
        assert len(shot.target_rows) == 2
        shot.move_target(0, 1)
        shot.remove_target(1)
        assert len(shot.target_rows) == 1
        assert app.save_coordinator.pending is None

        shot.add_host()
        shot.add_host()
        assert len(shot.host_rows) == 2
        shot.move_host(0, 1)
        shot.remove_host(1)
        assert len(shot.host_rows) == 1
        root.update()

        assert app.model.document.path is None
        assert app.model.document.fingerprint is None
        assert app.model.get(('log', 'log_directory')) == 'logs'
        assert len(app.model.get(('push', 'push_channel_settings', 'channels'))) == 2
        assert len(app.model.get(('push', 'screenshot', 'targets'))) == 1
        assert len(app.model.get(('push', 'screenshot', 'image_host'))) == 1
        assert app.model.dirty
        assert app.save_coordinator.pending is None
        choose.assert_not_called()
        written.assert_not_called()
    finally:
        root.destroy()
