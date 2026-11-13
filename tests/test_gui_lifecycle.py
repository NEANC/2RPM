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
from unittest.mock import patch

import pytest

from modules.gui.protocol import TestRequest as GuiRequest
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
