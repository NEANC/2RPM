#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""隔离测试的单 worker 监督与独立管道 I/O。"""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

from modules.gui.protocol import TestRequest
from modules.gui.protocol import TestResult
from modules.gui.protocol import decode_result
from modules.gui.protocol import encode_request
from modules.screenshot.retention import FORMAT_SUFFIX


CANCEL_REASON = '测试已取消；已发出的请求仍可能完成'
FAILURE_REASON = '测试执行失败'
POLL_INTERVAL = 0.1
TERMINATE_GRACE = 1.0


class TestBusyError(RuntimeError):
    """上一请求尚未完成回收。"""


class TestRunnerClosedError(RuntimeError):
    """runner 已永久关闭。"""


class TestRunner:
    """将 Tk 线程与单次测试 worker 的生命周期隔开。"""

    def __init__(self, command: tuple[str, ...], results: queue.Queue[TestResult],
                 *, worker_cwd: Path):
        """记录绝对命令、结果队列及隔离工作目录。"""
        self.command = (str(Path(command[0]).resolve()), *command[1:])
        self.results = results
        self.worker_cwd = worker_cwd.resolve()
        self._lock = threading.Lock()
        self._closed = False
        self._cancel = None
        self._active = False
        self._sessions = set()
        self._cleanup = None
        self._supervisor = None

    @property
    def active(self) -> bool:
        """只有监督线程确认回收后才释放忙态。"""
        with self._lock:
            return self._active

    def start(self, request: TestRequest) -> None:
        """同步占用槽位并异步启动监督线程。"""
        snapshot = replace(request, config=deepcopy(request.config),
                           config_dir=str(Path(request.config_dir).resolve()),
                           session_dir=str(Path(request.session_dir).resolve()),
                           image_path=(str(Path(request.image_path).resolve())
                                       if request.image_path is not None else None))
        with self._lock:
            if self._closed:
                raise TestRunnerClosedError('测试运行器已关闭')
            if self._active:
                raise TestBusyError('已有测试正在运行')
            self._active = True
            cancelled = threading.Event()
            self._cancel = cancelled
            self._sessions.add(snapshot.session_dir)
            try:
                self._supervisor = threading.Thread(
                    target=self._supervise, args=(snapshot, cancelled),
                    name='gui-test-supervisor')
                self._supervisor.start()
            except RuntimeError:
                self._active = False
                self._cancel = None
                self._supervisor = None
                self._sessions.discard(snapshot.session_dir)
                raise

    def cancel(self) -> None:
        """仅设置当前请求的取消信号，不阻塞 Tk。"""
        with self._lock:
            if self._active:
                self._cancel.set()

    def close(self) -> None:
        """禁止后续测试并请求后台回收当前 worker。"""
        sessions = ()
        with self._lock:
            self._closed = True
            if self._active:
                self._cancel.set()
            else:
                sessions = tuple(self._sessions)
                self._sessions.clear()
                if sessions:
                    self._cleanup = threading.Thread(target=self._clean_sessions,
                                                     args=(sessions,), name='gui-test-cleanup')
                    self._cleanup.start()

    @staticmethod
    def _clean_sessions(sessions):
        """后台删除属于本次 GUI 会话的预览文件。"""
        for session in sessions:
            shutil.rmtree(session, ignore_errors=True)

    @staticmethod
    def _clean_request(request):
        """只删除本请求可能写出的截图产物。"""
        for suffix in FORMAT_SUFFIX.values():
            try:
                (Path(request.session_dir) / (request.request_id + suffix)).unlink(missing_ok=True)
            except OSError:
                pass

    def _io(self, process, request, done, slot):
        """独占三管道并执行唯一一次无超时通信。"""
        try:
            payload = encode_request(request)
            slot['output'], slot['stderr'] = process.communicate(input=payload)
        except Exception:
            slot['error'] = True
            while process.poll() is None:
                threading.Event().wait(POLL_INTERVAL)
            for thread_name in ('stdout_thread', 'stderr_thread'):
                thread = getattr(process, thread_name, None)
                if thread is not None:
                    thread.join()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    try:
                        stream.close()
                    except OSError:
                        pass
        finally:
            done.set()

    def _supervise(self, request, cancelled):
        """独立轮询终止进程，等待管道及线程完成后只提交一个终态。"""
        process = None
        io_thread = None
        io_started = False
        done = threading.Event()
        slot = {}
        killed = False
        try:
            if not cancelled.is_set():
                process = subprocess.Popen(self.command, cwd=str(self.worker_cwd),
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, shell=False,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                io_thread = threading.Thread(target=self._io,
                                             args=(process, request, done, slot),
                                             name='gui-test-io')
                io_thread.start()
                io_started = True
                deadline = None
                while True:
                    exit_code = process.poll()
                    if cancelled.is_set() or slot.get('error'):
                        if exit_code is None and deadline is None:
                            try:
                                process.terminate()
                            except OSError:
                                if process.poll() is None:
                                    raise
                            deadline = time.monotonic() + TERMINATE_GRACE
                        elif (exit_code is None and deadline is not None
                              and not killed and time.monotonic() >= deadline):
                            try:
                                process.kill()
                            except OSError:
                                if process.poll() is None:
                                    raise
                            killed = True
                    if exit_code is not None and done.is_set():
                        io_thread.join(timeout=POLL_INTERVAL)
                        if not io_thread.is_alive():
                            break
                    if done.is_set():
                        threading.Event().wait(POLL_INTERVAL)
                    else:
                        done.wait(POLL_INTERVAL)
            if process is not None and process.returncode == 0 and not slot.get('error'):
                try:
                    result = decode_result(slot['output'], request=request)
                except (KeyError, ValueError):
                    result = self._failure(request)
            else:
                result = self._failure(request)
        except Exception:
            result = self._failure(request)
            if process is not None:
                if process.poll() is None:
                    process.kill()
                if io_started:
                    io_thread.join()
                process.wait()
                if not io_started:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()
        cleaned_failure = False
        cleaned_close = False
        while True:
            with self._lock:
                is_cancelled = cancelled.is_set()
                is_closed = self._closed
                if is_cancelled and not cleaned_failure:
                    clean = 'request'
                    cleaned_failure = True
                elif result.status == 'failure' and not cleaned_failure:
                    clean = 'request'
                    cleaned_failure = True
                elif is_closed and not cleaned_close:
                    clean = tuple(self._sessions)
                    cleaned_close = True
                else:
                    if is_cancelled:
                        result = self._failure(request, CANCEL_REASON)
                    if is_closed:
                        self._sessions.clear()
                    self.results.put(result)
                    self._active = False
                    self._cancel = None
                    break
            if clean == 'request':
                self._clean_request(request)
            else:
                self._clean_sessions(clean)

    @staticmethod
    def _failure(request, reason=FAILURE_REASON):
        """将无效协议或取消转换为保留请求身份的安全结果。"""
        return TestResult(request.request_id, request.document_generation,
                          request.revision, 'failure', reason, ())
