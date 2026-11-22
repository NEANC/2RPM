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


@pytest.mark.parametrize('operation', ['channel_remove', 'target_remove',
                                       'host_remove', 'host_move'])
def test_list_entry_reselects_after_clean_draft_external_reload(tmp_path, operation, monkeypatch):
    """干净草稿补存自动重载后，不沿用旧列表选择执行删除或重排。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        channels = app.visible_pages['消息推送设置']
        attachments = app.visible_pages['截图设置']
        targets_path = ('push', 'screenshot', 'targets')
        hosts_path = ('push', 'screenshot', 'image_host')
        if operation == 'target_remove':
            attachments.add_target()
        if operation == 'host_move':
            attachments.add_host()
        monkeypatch.setattr('modules.gui.pages.notification_ctk.messagebox.askyesno',
                            lambda *a, **k: True)
        monkeypatch.setattr('modules.gui.pages.screenshot.messagebox.askyesno',
                            lambda *a, **k: True)
        monkeypatch.setattr('modules.gui.app.messagebox.showinfo', Mock())
        list_path, action = {
            'channel_remove': (channels.channels_path, lambda: channels.remove_channel(0)),
            'target_remove': (targets_path, lambda: attachments.remove_target(0)),
            'host_remove': (hosts_path, lambda: attachments.remove_host(0)),
            'host_move': (hosts_path, lambda: attachments.move_host(0, 1)),
        }[operation]
        path = app.model.document.path
        disk = read_document(path)
        items = disk.tree
        for key in list_path:
            items = items[key]
        items.insert(0, {'provider': 'external', 'token': 'external'})
        expected = [dict(item) for item in items]
        save_document(disk, path, expected_fingerprint=disk.fingerprint)
        generation = app.model.document_generation

        action()

        assert '配置已更新，请重新选择列表项' in app.status.cget('text')
        assert app.model.document_generation == generation + 1
        assert app.model.get(list_path) == expected
        persisted = read_document(path).tree
        for key in list_path:
            persisted = persisted[key]
        assert persisted == expected
        assert not app.model.dirty
        if operation == 'host_move':
            attachments.move_host(1, -1)
        elif operation == 'channel_remove':
            channels.remove_channel(1)
        elif operation == 'target_remove':
            attachments.remove_target(1)
        else:
            attachments.remove_host(1)
        assert app.model.get(list_path) != expected
    finally:
        root.destroy()
