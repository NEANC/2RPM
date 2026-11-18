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


def test_document_reload_refresh_keeps_lists_unselected(tmp_path):
    """文档重载后的通道编辑折叠状态和截图行必须重建。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        channels = app.visible_pages['消息推送设置']
        attachments = app.visible_pages['截图设置']
        attachments.add_target()
        channels.expand_channel(0)
        old_entry = channels.channel_entries['sckey']
        old_target = attachments.target_rows[0]['number']
        path = app.model.document.path
        disk = read_document(path)
        disk.tree['monitor']['target'] = 'external.exe'
        save_document(disk, path, expected_fingerprint=disk.fingerprint)

        assert not app.controller.check_external()
        assert app.model.get(('monitor', 'target')) == 'external.exe'
        app.refresh()
        assert channels._expanded_channel is None
        assert channels.channel_entries == {}
        assert not old_entry.winfo_exists()
        assert not old_target.winfo_exists()
        assert len(attachments.target_rows) == 1
    finally:
        root.destroy()


def test_appearance_controls_apply_and_persist_without_replacing_pages(tmp_path, monkeypatch):
    """外观选择更新 ctk 窗口并持久化，现有 ttk 页面仍可使用。"""
    import ctk_kit

    from modules.gui.appearance import AppearancePreferences, load_preferences

    path = tmp_path / 'gui.ini'
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: path)
    root = ctk_kit.App(title='测试', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        assert app.appearance.dark is None
        assert app.appearance.material == 'Mica'
        assert not hasattr(app, 'notebook')
        assert app.show_page('设置')
        settings_page = app.visible_pages['设置']
        settings_page.theme.select()
        settings_page._change_theme()
        settings_page.material.set('无材质')
        settings_page._change_material('无材质')
        assert app.show_page('监视设置')
        assert app.visible_pages['监视设置'].fields[('monitor', 'target')][1].winfo_exists()
        assert load_preferences(path) == AppearancePreferences(dark=True, material='none')
        assert ctk_kit.get_appearance_mode() == 'Dark'
    finally:
        root.destroy()


def test_after_heartbeat_continues_during_long_worker(tmp_path):
    """真实 Tk 定时器在后台测试未结束时继续执行。"""
    root = tk.Tk()
    release = threading.Event()
    worker = None
    heartbeat_id = None
    try:
        app = make_app(root, tmp_path)
        app.visible_pages['截图设置'].add_target()
        app.test_dialogs.open_capture = Mock()
        app.test_dialogs.show_result = Mock()
        ticks = []

        def heartbeat():
            """以真实 after 记录主线程定时回调。"""
            nonlocal heartbeat_id
            ticks.append(threading.get_ident())
            heartbeat_id = root.after(15, heartbeat)

        def start(_request):
            """模拟长期阻塞的后台 worker，不占用 Tk 线程。"""
            nonlocal worker
            app.controller.runner.active = True
            worker = threading.Thread(target=release.wait, daemon=True)
            worker.start()

        app.controller.runner.start.side_effect = start
        heartbeat_id = root.after(15, heartbeat)
        app.capture()
        assert app.test_dialogs.open_capture.call_args.args[0]('window', '窗口')
        deadline = time.monotonic() + 0.4
        while len(ticks) < 4 and time.monotonic() < deadline:
            root.update()
            time.sleep(0.005)
        assert worker is not None and worker.is_alive()
        assert len(ticks) >= 4
        assert set(ticks) == {threading.get_ident()}
    finally:
        release.set()
        if worker is not None:
            worker.join(timeout=1)
        if heartbeat_id is not None:
            root.after_cancel(heartbeat_id)
        if 'app' in locals():
            root.after_cancel(app._after_id)
        root.destroy()
