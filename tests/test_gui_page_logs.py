#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 页面行为（GUI 测试分片）。"""

import queue
import tkinter as tk
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import ctk_kit

from modules.config_document import new_document, read_document, save_document
from modules.gui.model import EditorModel
from modules.gui.forms import screenshot_conditions
from modules.gui.protocol import TestRequest as GuiTestRequest
from modules.gui.protocol import TestResult as GuiTestResult


from tests.gui_support import make_app


def test_missing_log_leaf_is_editable_and_created_on_save(tmp_path):
    """log 映射存在但叶子缺失时可编辑，并在保存时创建键。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        source = tmp_path / 'empty-log.yaml'
        source.write_text('monitor:\n  mode: watch\n  target: demo.exe\n  common: {}\n'
                          'push: {}\nlog: {}\nexternal: {}\n', encoding='utf-8')
        assert app.controller.open(source)
        assert app.show_page('日志设置')
        page = app.visible_pages['日志设置']
        path = ('log', 'max_log_files')
        widget = page.fields[path][1]
        assert widget.cget('state') == 'normal'
        widget.insert(0, '19')
        assert app.model.get(path) == 19
        assert app.controller.save()
        assert read_document(source).tree['log']['max_log_files'] == 19
    finally:
        root.destroy()


def test_open_missing_push_and_log_sections_refreshes_empty_state(tmp_path):
    """缺少 push/log 节时页面按空状态刷新，不抛 KeyError。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = tmp_path / 'missing-sections.yaml'
        path.write_text('monitor:\n  mode: watch\n  target: demo.exe\n  common: {}\n'
                        'external: {}\n', encoding='utf-8')
        assert app.controller.open(path)
        app.show_page('主页')
        assert '通知通道：待校验' in app.summary_text
        assert '截图目标：待校验' in app.summary_text
        assert app.show_page('消息推送设置')
        assert app.visible_pages['消息推送设置'].channel_rows == []
        assert app.show_page('日志设置')
        assert app.visible_pages['日志设置'].fields[('log', 'log_directory')][1].get() == ''
        assert app.visible_pages['日志设置'].fields[('log', 'retention_days')][1].get() == ''
    finally:
        root.destroy()


def test_empty_push_and_log_sections_allow_creation_and_actions(tmp_path, monkeypatch):
    """push 为空且缺少 log 节时，列表创建与测试入口给出提示而不抛 KeyError。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = tmp_path / 'empty-push.yaml'
        path.write_text('monitor:\n  mode: watch\n  target: demo.exe\n  common: {}\n'
                        'push: {}\nlog: {}\nexternal: {}\n', encoding='utf-8')
        assert app.controller.open(path)
        app.test_dialogs.open_upload = Mock()
        app.upload()
        assert '请先添加图床' in app.status.cget('text')
        app.test_dialogs.open_upload.assert_not_called()
        app.notify()
        assert '请先添加推送通道' in app.status.cget('text')
        notices = []
        monkeypatch.setattr('modules.gui.app.messagebox.showinfo',
                            lambda *args, **kwargs: notices.append(args))
        app.open_logs()
        assert notices
        screenshot = app.visible_pages['截图设置']
        screenshot.add_target()
        screenshot.add_host()
        assert app.model.get(('push', 'screenshot', 'targets'))
        assert app.model.get(('push', 'screenshot', 'image_host'))
    finally:
        root.destroy()
