#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 页面行为（GUI 测试分片）。"""

import ctypes
from pathlib import Path
import queue
import subprocess
import sys
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
from modules.gui.test_runner import CANCEL_REASON
from modules.gui.test_runner import TestRunner as GuiTestRunner


from tests.gui_support import make_app


def _local_cli(app, monkeypatch):
    """经 GUI 启动仅睡眠的本地 CLI，保留真实进程供测试回收。"""
    original = subprocess.Popen
    processes = []

    def spawn(*args, **kwargs):
        """保留 launcher 的真实创建参数和进程句柄。"""
        process = original(*args, **kwargs)
        processes.append((process, args, kwargs))
        return process

    app.controller.cli_command = (sys.executable, '-c',
                                  'import time; time.sleep(30)')
    with monkeypatch.context() as patches:
        patches.setattr('modules.gui.launcher.subprocess.Popen', spawn)
        patches.setattr('modules.gui.app.messagebox.showinfo', lambda *_args, **_kwargs: None)
        app.start_cli()
    try:
        assert app.controller.launch_count == 1
        assert len(processes) == 1
        process, args, kwargs = processes[0]
        assert args[0][-2:] == ['-c', str(app.model.document.path.resolve())]
        assert kwargs['creationflags'] == subprocess.CREATE_NEW_CONSOLE
        assert process.poll() is None
        return process
    except BaseException:
        for process, _, _ in processes:
            _stop_process(process)
        raise


def _stop_process(process):
    """即使断言失败也回收本地探针进程。"""
    if process is not None:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)


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


def test_system_titlebar_close_cancel_restores_draft_worker_and_autosave(tmp_path):
    """系统关闭先问草稿再问测试，取消退出恢复自动保存。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        runner = app.controller.runner
        runner.active = True
        path = app.model.document.path
        original = path.read_bytes()
        app.model.set(('monitor', 'target'), 'draft.exe')
        assert app.save_coordinator.pending is not None
        prompts = []

        def confirm(kind, detail):
            """在真实模态事件循环内尝试触发延迟写盘。"""
            prompts.append(kind)
            if kind == 'unsaved':
                root.after(0, lambda: app.save_coordinator._save(app.model.document_generation))
                root.update()
                assert path.read_bytes() == original
                return 'discard'
            assert kind == 'exit_test'
            assert path.read_bytes() == original
            runner.close.assert_not_called()
            return 'cancel'

        app.controller.confirm = confirm
        frame = int(root.tk.call('wm', 'frame', root._w), 0)
        assert ctypes.windll.user32.PostMessageW(frame, 0x10, 0, 0)
        root.update()
        assert prompts == ['unsaved', 'exit_test']
        assert root.winfo_exists()
        assert app.model.get(('monitor', 'target')) == 'draft.exe'
        assert app.model.dirty
        assert path.read_bytes() == original
        assert app.save_coordinator.pending is not None
        runner.close.assert_not_called()
        root.after(0, lambda: app.save_coordinator._save(app.model.document_generation))
        root.update()
        assert read_document(path).tree['monitor']['target'] == 'draft.exe'
    finally:
        root.destroy()


def test_system_titlebar_close_does_not_reenter_during_confirmation(tmp_path):
    """模态确认期间再次收到系统关闭消息不提前结束测试。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        runner = app.controller.runner
        runner.active = True
        app.model.set(('monitor', 'target'), 'draft.exe')
        prompts = []
        frame = int(root.tk.call('wm', 'frame', root._w), 0)

        def confirm(kind, detail):
            """在首个草稿确认内再次投递真实窗口关闭消息。"""
            prompts.append(kind)
            if kind == 'unsaved' and len(prompts) == 1:
                assert ctypes.windll.user32.PostMessageW(frame, 0x10, 0, 0)
                root.update()
            return 'discard' if kind == 'unsaved' else 'cancel'

        app.controller.confirm = confirm
        assert ctypes.windll.user32.PostMessageW(frame, 0x10, 0, 0)
        root.update()
        assert prompts == ['unsaved', 'exit_test']
        assert root.winfo_exists()
        assert app.model.dirty
        runner.close.assert_not_called()
    finally:
        root.destroy()


def test_system_titlebar_close_cancel_then_confirm_reaps_worker_not_gui_cli(tmp_path, monkeypatch):
    """GUI 创建的 CLI 两次关闭后存活，真实 worker 仅最终确认后回收。"""
    destroyed = []
    root = tk.Tk()
    cli = None
    worker = None
    runner = None
    try:
        app = make_app(root, tmp_path)
        cli = _local_cli(app, monkeypatch)
        runner = GuiTestRunner((sys.executable, '-c', 'import time; time.sleep(30)'),
                               queue.Queue(), worker_cwd=Path(__file__).resolve().parents[1])
        app.controller.runner = runner
        app.test_session.runner = runner
        original = subprocess.Popen
        spawned = threading.Event()
        processes = []

        def spawn(*args, **kwargs):
            """记录真实 worker 与其隔离管道。"""
            process = original(*args, **kwargs)
            processes.append(process)
            spawned.set()
            return process

        request = GuiTestRequest('close-worker', 1, 1, 'notify', {}, str(tmp_path),
                                 str(tmp_path / 'session'), None, None, None, 'on_end')
        with monkeypatch.context() as patches:
            patches.setattr('modules.gui.test_runner.subprocess.Popen', spawn)
            runner.start(request)
            assert spawned.wait(5)
        worker = processes[0]
        assert runner.active and worker.poll() is None
        app.model.set(('monitor', 'target'), 'draft.exe')
        prompts = []
        approvals = iter(('cancel', 'confirm'))

        def confirm(kind, detail):
            """每次询问期间检查两个真实进程仍在运行。"""
            prompts.append(kind)
            assert runner.active and worker.poll() is None
            assert cli.poll() is None
            if kind == 'unsaved':
                return 'discard'
            if kind == 'exit_test':
                return next(approvals)
            return 'confirm'

        app.controller.confirm = confirm
        root.bind('<Destroy>', lambda event: destroyed.append(event.widget))
        frame = int(root.tk.call('wm', 'frame', root._w), 0)
        assert ctypes.windll.user32.PostMessageW(frame, 0x10, 0, 0)
        root.update()
        assert prompts == ['unsaved', 'exit_test']
        assert root.winfo_exists() and runner.active
        assert worker.poll() is None and cli.poll() is None
        assert not runner._closed

        assert ctypes.windll.user32.PostMessageW(frame, 0x10, 0, 0)
        root.update()
        assert prompts == ['unsaved', 'exit_test', 'unsaved', 'exit_test', 'exit']
        assert runner._closed
        result = runner.results.get(timeout=5)
        assert (result.request_id, result.document_generation, result.revision) == (
            'close-worker', 1, 1)
        assert result.status == 'failure' and result.reason == CANCEL_REASON
        assert runner.results.empty()
        runner._supervisor.join(timeout=5)
        assert not runner._supervisor.is_alive() and not runner.active
        assert worker.poll() is not None
        assert all(stream is not None and stream.closed
                   for stream in (worker.stdin, worker.stdout, worker.stderr))
        assert not any(thread.name in ('gui-test-io', 'gui-test-supervisor')
                       for thread in threading.enumerate())
        assert cli.poll() is None
        assert root in destroyed
    finally:
        if root not in destroyed:
            root.destroy()
        if runner is not None:
            runner.close()
            if runner._supervisor is not None:
                runner._supervisor.join(timeout=5)
        for process in processes if 'processes' in locals() else ():
            _stop_process(process)
        _stop_process(cli)
