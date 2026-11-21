#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""空白草稿在真实窗口中的装配、浏览与焦点回归。"""

import tkinter as tk

import ctk_kit

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
