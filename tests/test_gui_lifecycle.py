#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Windows 真实大 stdin 堵塞时测试 worker 生命周期。"""

import ctypes
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from modules.config_document import new_document
from modules.config_document import read_document
from modules.config_document import save_document
from modules.gui.controller import Controller
from modules.gui.model import EditorModel
from modules.gui.protocol import TestRequest as GuiRequest
from modules.gui.protocol import TestResult as GuiResult
from modules.gui.test_runner import TestRunner as GuiRunner


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_SIZE = 9 * 1024 * 1024


def run_blocked_request(tmp_path, action):
    """等待真实写入堵塞，再从调用线程发起取消或关闭。"""
    name = 'Local\\2rpm-test-' + uuid.uuid4().hex
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateEventW.restype = ctypes.c_void_p
    kernel.GetNamedPipeInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong),
                                        ctypes.POINTER(ctypes.c_ulong),
                                        ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
    ready = kernel.CreateEventW(None, 1, 0, name)
    assert ready
    entered = threading.Event()
    returned = threading.Event()
    processes = []
    results = queue.Queue()
    callback_duration = []
    command = (sys.executable, str(ROOT / 'tests/fixtures/gui_worker_probe.py'),
               '--unread-stdin', name)
    runner = GuiRunner(command, results, worker_cwd=ROOT)
    request = GuiRequest('blocked', 3, 5, 'notify',
                         {'large': 'x' * PAYLOAD_SIZE}, str(tmp_path),
                         str(tmp_path / 'session'), None, None, None, 'on_end')
    original_popen = subprocess.Popen
    original_write = subprocess.Popen._stdin_write
    pipe_capacity = []

    def observe_write(process, payload):
        """用原始 Windows 写入，仅标识进入和返回。"""
        entered.set()
        try:
            return original_write(process, payload)
        finally:
            returned.set()

    def spawn(*args, **kwargs):
        """保留真实 Popen 与进程引用用于 finally 清理。"""
        process = original_popen(*args, **kwargs)
        import msvcrt
        available = ctypes.c_ulong()
        handle = msvcrt.get_osfhandle(process.stdin.fileno())
        assert kernel.GetNamedPipeInfo(ctypes.c_void_p(handle), None,
                                       None, ctypes.byref(available), None)
        pipe_capacity.append(available.value)
        processes.append(process)
        return process

    try:
        with patch.object(original_popen, '_stdin_write', observe_write), \
                patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn):
            runner.start(request)
            assert kernel.WaitForSingleObject(ctypes.c_void_p(ready), 5000) == 0
            assert pipe_capacity[0] < PAYLOAD_SIZE
            assert entered.wait(5)
            assert not returned.is_set()
            started = time.monotonic()
            getattr(runner, action)()
            callback_duration.append(time.monotonic() - started)
            assert callback_duration[0] < 0.5
            result = results.get(timeout=5)
            assert result.status == 'failure'
            assert (result.request_id, result.document_generation, result.revision) == ('blocked', 3, 5)
            assert results.empty()
            assert returned.wait(5)
            assert not runner.active
            assert processes[0].poll() is not None
            runner._supervisor.join(timeout=5)
            assert not runner._supervisor.is_alive()
            assert all(stream.closed for stream in
                       (processes[0].stdin, processes[0].stdout, processes[0].stderr))
            assert not any(thread.name in ('gui-test-io', 'gui-test-supervisor')
                           for thread in threading.enumerate())
    finally:
        runner.close()
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        kernel.CloseHandle(ctypes.c_void_p(ready))


@pytest.mark.skipif(sys.platform != 'win32', reason='仅 Windows 管道')
def test_large_request_unread_stdin_cancel(tmp_path):
    """不读 stdin 的真实 worker 不阻止取消。"""
    run_blocked_request(tmp_path, 'cancel')


@pytest.mark.skipif(sys.platform != 'win32', reason='仅 Windows 管道')
def test_large_request_unread_stdin_close(tmp_path):
    """不读 stdin 的真实 worker 不阻止关闭。"""
    run_blocked_request(tmp_path, 'close')


def test_file_switch_blocked_until_worker_finishes(tmp_path):
    """worker 正在运行时拒绝新建、打开及另存为。"""
    runner = Mock(active=True)
    model = EditorModel(new_document())
    controller = Controller(model, confirm=lambda *_: 'confirm',
                            choose_path=lambda: tmp_path / 'new.yaml',
                            cli_command=(), runner=runner, program_dir=tmp_path)
    assert not controller.new(lambda: tmp_path / 'new.yaml')
    assert not controller.open(lambda: tmp_path / 'other.yaml')
    assert not controller.save_as(lambda: tmp_path / 'copy.yaml')
    assert model.document.path is None
    runner.active = False
    assert controller.new(lambda: tmp_path / 'new.yaml')


def test_exit_cancel_keeps_worker_and_draft(tmp_path):
    """退出第二次确认取消时不关闭 worker、不放弃草稿。"""
    runner = Mock(active=True)
    model = EditorModel(new_document())
    model.set(('monitor', 'target'), 'draft.exe')
    answers = iter(('discard', 'cancel'))
    controller = Controller(model, confirm=lambda *_: next(answers),
                            choose_path=lambda: None, cli_command=(),
                            runner=runner, program_dir=tmp_path)
    assert not controller.close()
    assert model.get(('monitor', 'target')) == 'draft.exe'
    runner.close.assert_not_called()


def test_exit_confirms_worker_only_after_final_approval(tmp_path):
    """最终退出确认才结束测试 runner，不接触独立 CLI。"""
    runner = Mock(active=True)
    controller = Controller(EditorModel(new_document()),
                            confirm=lambda *_: 'confirm', choose_path=lambda: None,
                            cli_command=('cli.exe',), runner=runner, program_dir=tmp_path)
    assert controller.close()
    runner.close.assert_called_once()


def test_unsaved_or_conflicted_actions_are_blocked(tmp_path):
    """未完整输入或冲突阻止业务操作，保留草稿。"""
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.model.input_errors[('monitor', 'common', 'max_wait')] = '-'
        app.capture()
        app.upload()
        app.notify()
        app.controller.runner.start.assert_not_called()
        assert app.model.input_errors
    finally:
        root.destroy()


@pytest.mark.parametrize(('action', 'path', 'notice'), [
    ('open_upload_selection', ('push', 'screenshot', 'image_host'), '请先添加图床后再测试'),
    ('notify', ('push', 'push_channel_settings', 'channels'), '请先添加推送通道'),
])
def test_empty_test_destination_is_visible(tmp_path, monkeypatch, action, path, notice):
    """真实窗口在没有外发目标时给出可见提示且不启动 worker。"""
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.model.set(path, [])
        dialogs = Mock()
        monkeypatch.setattr('modules.gui.app.messagebox.showinfo', dialogs)
        getattr(app, action)()
        dialogs.assert_called_once()
        assert notice in dialogs.call_args.args[1]
        app.controller.runner.start.assert_not_called()
    finally:
        root.destroy()


@pytest.mark.parametrize('action', ['notify', '_request'])
def test_busy_test_is_visible_without_resubmitting(tmp_path, monkeypatch, action):
    """worker 忙时通知与新测试请求均反馈用户且不重复提交。"""
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.controller.runner.active = True
        dialogs = Mock()
        monkeypatch.setattr('modules.gui.app.messagebox.showinfo', dialogs)
        if action == '_request':
            assert not app._request('capture')
        else:
            app.notify()
        dialogs.assert_called_once()
        assert '已有测试正在运行' in dialogs.call_args.args[1]
        app.controller.runner.start.assert_not_called()
    finally:
        root.destroy()


@pytest.mark.parametrize('action', ['_require_saved', '_before_list_edit'])
def test_operation_flush_failure_is_visible(tmp_path, monkeypatch, action):
    """操作补存失败时显示可见提示并保留原文草稿。"""
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.model.set_input(('monitor', 'common', 'max_wait'), '-')
        app.save_coordinator.flush = Mock(return_value=False)
        popup = Mock()
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', popup)
        assert not getattr(app, action)()
        popup.assert_called_once()
        assert '未保存' in popup.call_args.args[1]
        assert app.model.get_input(('monitor', 'common', 'max_wait')) == '-'
    finally:
        root.destroy()


def test_capture_selection_rejects_changed_document(tmp_path):
    """选择截图目标期间文档被重载时要求重新选择。"""
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.test_dialogs.open_capture = Mock()
        app.capture()
        submit = app.test_dialogs.open_capture.call_args.args[0]
        app.model.replace_document(new_document())
        app.refresh()
        assert not submit('window', '测试窗口')
        app.controller.runner.start.assert_not_called()
    finally:
        root.destroy()


def test_stale_host_selection_requires_reselect(tmp_path):
    """图床顺序或内容更新后不将旧序号送往 worker。"""
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.test_dialogs.open_upload = Mock()
        app.upload()
        _, submit = app.test_dialogs.open_upload.call_args.args
        path = ('push', 'screenshot', 'image_host')
        hosts = app.model.get(path)
        hosts.append({'provider': 'beeimg', 'token': 'placeholder'})
        app.model.set(path, hosts)
        assert not submit(0, str(tmp_path / 'image.png'))
        app.controller.runner.start.assert_not_called()
        assert app.test_dialogs.open_upload.call_count == 2
    finally:
        root.destroy()


def test_upload_confirmation_rejects_changed_host_selection(tmp_path, monkeypatch):
    """外发确认期间重排图床后，旧序号不得提交给 worker。"""
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.test_dialogs.open_upload = Mock()
        app.upload()
        _, submit = app.test_dialogs.open_upload.call_args.args

        def confirm(*args, **kwargs):
            """模拟确认弹窗期间配置被更新。"""
            path = ('push', 'screenshot', 'image_host')
            hosts = app.model.get(path)
            hosts.insert(0, {'provider': 'beeimg', 'token': 'placeholder'})
            app.model.set(path, hosts)
            return True

        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', confirm)
        assert not submit(0, str(tmp_path / 'image.png'))
        app.controller.runner.start.assert_not_called()
        assert app.test_dialogs.open_upload.call_count == 2
    finally:
        root.destroy()


def test_notification_confirmation_rejects_changed_document(tmp_path, monkeypatch):
    """外发确认期间切换文档后，不发送旧身份通知。"""
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        channels = ('push', 'push_channel_settings', 'channels')
        app.model.set(channels, [{'provider': 'bark', 'key': 'placeholder'}])

        def confirm(*args, **kwargs):
            """模拟确认弹窗期间打开另一份文档。"""
            app.model.replace_document(new_document())
            return True

        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', confirm)
        app.notify()
        app.controller.runner.start.assert_not_called()
    finally:
        root.destroy()


def test_upload_confirmation_reloads_disk_and_requires_reselect(tmp_path, monkeypatch):
    """确认期间磁盘图床变动时刷新列表而不按旧序号上传。"""
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.test_dialogs.open_upload = Mock()
        app.upload()
        _, submit = app.test_dialogs.open_upload.call_args.args
        path = app.model.document.path

        def confirm(*args, **kwargs):
            """在真实外发确认期间写入另一版配置。"""
            document = read_document(path)
            document.tree['push']['screenshot']['image_host'].insert(
                0, {'provider': 'beeimg', 'token': 'replacement'})
            save_document(document, path, expected_fingerprint=document.fingerprint)
            return True

        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', confirm)
        assert not submit(0, str(tmp_path / 'image.png'))
        app.controller.runner.start.assert_not_called()
        assert app.test_dialogs.open_upload.call_count == 2
        assert app.model.document.fingerprint == read_document(path).fingerprint
        assert '重新选择图床' in app.status.cget('text')
    finally:
        root.destroy()


def test_notification_confirmation_rejects_disk_change(tmp_path, monkeypatch):
    """确认期间外部编辑磁盘配置时拒绝发送旧通道快照。"""
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        channels = ('push', 'push_channel_settings', 'channels')
        app.model.set(channels, [{'provider': 'bark', 'key': 'placeholder'}])
        path = app.model.document.path

        def confirm(*args, **kwargs):
            """在确认期间更换磁盘通道而不通知内存模型。"""
            document = read_document(path)
            document.tree['push']['push_channel_settings']['channels'] = []
            save_document(document, path, expected_fingerprint=document.fingerprint)
            return True

        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', confirm)
        app.notify()
        app.controller.runner.start.assert_not_called()
        assert app.model.document.fingerprint == read_document(path).fingerprint
        assert '配置已更新' in app.status.cget('text')
    finally:
        root.destroy()


def test_result_and_conflict_actions_bypass_flush(tmp_path):
    """查看与隐藏结果不强制补存尚不能表示的草稿。"""
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.test_dialogs.show_result = Mock()
        app.test_session.last_upload = GuiResult('done', 0, 0, 'success', '', (),
                                                 'https://example.org/real.png')
        app.model.set_input(('monitor', 'common', 'timeout_threshold'), '-', kind='int')
        app.upload()
        app.test_dialogs.show_result.assert_called_once()
        assert app.model.get_input(('monitor', 'common', 'timeout_threshold')) == '-'
    finally:
        root.destroy()


def test_hidden_running_result_stays_hidden_when_finished(tmp_path):
    """用户主动隐藏运行窗后，终态仅更新结果，不擅自重新弹出。"""
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.test_dialogs.show_result = Mock()
        app.test_session.active_request = GuiRequest(
            'capture', app.model.document_generation, app.model.revision, 'capture',
            {}, str(tmp_path), str(app.session_dir), None, None, None, None,
            'window', '测试窗口')
        app.test_session.hide()
        app.controller.runner.results.put(GuiResult(
            'capture', app.model.document_generation, app.model.revision,
            'failure', '测试失败', ()))
        app.drain_results()
        app.test_dialogs.show_result.assert_not_called()
        assert app.test_session.last_capture is not None
        app.capture()
        app.test_dialogs.show_result.assert_called_once()
    finally:
        root.destroy()


def test_reopened_running_result_updates_on_completion(tmp_path):
    """运行中手动重新打开结果窗后，终态须更新当前可见窗口。"""
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.test_dialogs.show_result = Mock()
        app.test_session.active_request = GuiRequest(
            'capture', app.model.document_generation, app.model.revision, 'capture',
            {}, str(tmp_path), str(app.session_dir), None, None, None, None,
            'window', '测试窗口')
        app.test_session.hide()
        app.capture()
        app.controller.runner.results.put(GuiResult(
            'capture', app.model.document_generation, app.model.revision,
            'success', '', ()))
        app.drain_results()
        assert app.test_dialogs.show_result.call_count == 2
    finally:
        root.destroy()


def test_external_reload_keeps_running_result_with_old_version(tmp_path):
    """同路径自动重载后仍接纳原请求，并将结果标注为旧配置。"""
    from modules.config_document import read_document
    from modules.config_document import save_document
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.test_dialogs.show_result = Mock()
        previous = app.model.document_generation
        app.test_session.active_request = GuiRequest(
            'capture', previous, app.model.revision, 'capture', {},
            str(tmp_path), str(app.session_dir), None, None, None, None,
            'window', '测试窗口')
        disk = read_document(app.model.document.path)
        disk.tree['monitor']['target'] = '外部版本.exe'
        save_document(disk, disk.path, expected_fingerprint=disk.fingerprint)
        assert app.controller.check_external() is False
        assert app.model.document_generation != previous
        app.controller.runner.results.put(GuiResult(
            'capture', previous, 0, 'success', '', ()))
        app.drain_results()
        assert app.test_session.last_capture is not None
        assert '对应先前' in app.result_text
        app.capture()
        assert app.test_dialogs.show_result.call_args.kwargs['previous']
    finally:
        root.destroy()


def test_external_reload_keeps_finished_result(tmp_path):
    """同路径重载不清除已结束测试的可重新打开结果。"""
    from modules.config_document import read_document
    from modules.config_document import save_document
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        result = GuiResult('old', app.model.document_generation, 0,
                           'success', '', ())
        app.test_session.last_capture = result
        disk = read_document(app.model.document.path)
        disk.tree['monitor']['target'] = '重载.exe'
        save_document(disk, disk.path, expected_fingerprint=disk.fingerprint)
        app.controller.check_external()
        assert app.test_session.last_capture is result
        app.test_dialogs.show_result = Mock()
        app.capture()
        assert app.test_dialogs.show_result.call_args.kwargs['previous']
    finally:
        root.destroy()


def test_notify_late_result_does_not_popup_on_new_document(tmp_path, monkeypatch):
    """新配置不能接收已切换文档的迟到通知错误窗。"""
    from tests.gui_support import make_app
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.test_session.active_request = GuiRequest(
            'old', 0, 0, 'notify', {}, str(tmp_path), str(app.session_dir),
            None, None, None, 'on_end')
        app.model.replace_document(new_document())
        app.refresh()
        popup = Mock()
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', popup)
        app.controller.runner.results.put(GuiResult('old', 0, 0, 'failure', '失败', ()))
        app.drain_results()
        popup.assert_not_called()
    finally:
        root.destroy()


def test_notify_has_no_cancel_or_reopen(tmp_path, monkeypatch):
    """无通道通知不发起测试；通知结果不保留持续弹窗。"""
    from tests.gui_support import make_app
    import tkinter as tk

    monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *_, **__: False)
    monkeypatch.setattr('modules.gui.app.messagebox.showinfo', Mock())
    root = tk.Tk()
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('push', 'push_channel_settings', 'channels'), [])
        app.notify()
        app.controller.runner.start.assert_not_called()
        assert '请先添加推送通道' in app.status.cget('text')
    finally:
        root.destroy()
