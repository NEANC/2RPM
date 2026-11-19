#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""隔离测试进程的线程、结果及回收契约。"""

from dataclasses import replace
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from unittest.mock import patch

import psutil
import pytest
import win32api
import win32con
import win32job
import win32process

from modules.gui.protocol import TestRequest as GuiRequest
from modules.gui.protocol import TestResult as GuiResult
from modules.gui.protocol import encode_result


ROOT = Path(__file__).resolve().parents[1]


def run_large_stdin_child(command, *, timeout, env=None):
    """在 Job 中启动子测试，并在所有退出路径回收其全部后代。"""
    job = win32job.CreateJobObject(None, '')
    child = None
    try:
        limits = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation,
                                         limits)
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True,
                                 creationflags=win32process.CREATE_SUSPENDED)
        win32job.AssignProcessToJobObject(job, child._handle)
        thread_id = psutil.Process(child.pid).threads()[0].id
        thread = win32api.OpenThread(win32con.THREAD_SUSPEND_RESUME, False, thread_id)
        try:
            win32process.ResumeThread(thread)
        finally:
            thread.Close()
        stdout, stderr = child.communicate(timeout=timeout)
        assert child.returncode == 0, stdout + stderr
    finally:
        job.Close()
        if child is not None:
            child.communicate(timeout=5)


def request(tmp_path, *, request_id='first', config=None):
    """建立可追踪代次和目录的请求。"""
    return GuiRequest(request_id, 4, 7, 'notify', config or {},
                      str(tmp_path), str(tmp_path / 'session'), None, None,
                      None, 'on_end')


def runner_for(results, *, command=None):
    """构造使用独立真实进程的 runner。"""
    from modules.gui.test_runner import TestRunner
    return TestRunner(command or (sys.executable, '-m', 'modules.gui.worker'),
                      results, worker_cwd=ROOT)


def test_all_three_streams_are_pipes(tmp_path):
    """进程仅在测试管道传递内容，工作目录与启动者无关。"""
    results = queue.Queue()
    runner = runner_for(results)
    original = subprocess.Popen
    seen = []

    def spawn(*args, **kwargs):
        """记录进程参数并执行真实启动。"""
        seen.append(kwargs)
        return original(*args, **kwargs)

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn):
        runner.start(request(tmp_path))
        assert results.get(timeout=10).status == 'failure'
    assert seen[0]['stdin'] == seen[0]['stdout'] == seen[0]['stderr'] == subprocess.PIPE
    assert seen[0]['creationflags'] == subprocess.CREATE_NO_WINDOW
    assert seen[0]['shell'] is False
    assert seen[0]['cwd'] == str(ROOT)


def test_snapshot_is_detached(tmp_path):
    """启动后修改原配置不改变 worker 接收到的快照。"""
    from modules.gui.protocol import decode_request
    data = {'value': ['original']}
    results = queue.Queue()
    runner = runner_for(results)
    original = subprocess.Popen
    seen = []

    def spawn(*args, **kwargs):
        """在创建进程时修改原始草稿。"""
        data['value'].append('later')
        process = original(*args, **kwargs)
        communicate = process.communicate

        def capture(*args, **kwargs):
            """观察一次真实管道写入。"""
            seen.append(decode_request(kwargs['input']))
            return communicate(*args, **kwargs)

        process.communicate = capture
        return process

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn):
        runner.start(request(tmp_path, config=data))
        results.get(timeout=10)
    assert seen[0].config == {'value': ['original']}
    assert len(seen) == 1


def test_busy_rejects_second_test(tmp_path):
    """首个 worker 回收前不能复用槽位。"""
    from modules.gui.test_runner import TestBusyError
    results = queue.Queue()
    runner = runner_for(results)
    runner.start(request(tmp_path))
    with pytest.raises(TestBusyError):
        runner.start(request(tmp_path, request_id='second'))
    results.get(timeout=10)
    assert not runner.active


def test_result_belongs_to_original_revision(tmp_path):
    """真实 worker 的失败结果仍保留请求三元标识。"""
    results = queue.Queue()
    runner = runner_for(results)
    runner.start(request(tmp_path))
    result = results.get(timeout=10)
    assert (result.request_id, result.document_generation, result.revision) == ('first', 4, 7)
    assert result.status == 'failure'


def test_bad_exit_is_failure(tmp_path):
    """非零退出与无协议输出不能作为成功。"""
    results = queue.Queue()
    runner = runner_for(results, command=(sys.executable, '-c', 'raise SystemExit(2)'))
    runner.start(request(tmp_path))
    assert results.get(timeout=10).status == 'failure'
    assert results.empty()


def test_cancel_never_touches_cli(tmp_path):
    """取消只改变当前 runner，不访问独立 CLI 启动器。"""
    results = queue.Queue()
    runner = runner_for(results)
    with patch('modules.gui.launcher.launch_cli') as cli:
        runner.start(request(tmp_path))
        runner.cancel()
        assert results.get(timeout=10).status == 'failure'
        cli.assert_not_called()


def test_eof_does_not_hang(tmp_path):
    """先读到 EOF 的子进程仍应最终退出并释放槽位。"""
    results = queue.Queue()
    runner = runner_for(results, command=(sys.executable, '-c',
                             'import os; os.close(1); os.close(2)'))
    runner.start(request(tmp_path))
    assert results.get(timeout=10).status == 'failure'
    assert not runner.active


def test_cancel_before_spawn(tmp_path):
    """创建线程前取消可阻止 Popen。"""
    results = queue.Queue()
    runner = runner_for(results)
    gate = threading.Event()
    original = runner._supervise

    def delayed(*args):
        """用事件固定检查启动前取消的顺序。"""
        assert gate.wait(5)
        original(*args)

    with patch.object(runner, '_supervise', side_effect=delayed), \
            patch('modules.gui.test_runner.subprocess.Popen') as popen:
        runner.start(request(tmp_path))
        runner.cancel()
        gate.set()
        assert results.get(timeout=5).status == 'failure'
        popen.assert_not_called()


def test_close_reaps_worker(tmp_path):
    """关闭即时返回且拒绝继续启动。"""
    from modules.gui.test_runner import TestRunnerClosedError
    results = queue.Queue()
    runner = runner_for(results)
    runner.start(request(tmp_path))
    runner.close()
    with pytest.raises(TestRunnerClosedError):
        runner.start(request(tmp_path))
    assert results.get(timeout=10).status == 'failure'
    assert not runner.active


def test_cancel_reaps_worker(tmp_path):
    """同一请求取消后清理期间保持忙态，终态完成后回收并释放槽位。"""
    from modules.gui.test_runner import TestBusyError
    results = queue.Queue()
    runner = runner_for(results, command=(sys.executable, '-c',
                             'import time;time.sleep(30)'))
    session = tmp_path / 'session'
    session.mkdir()
    artifact = session / 'first.jpg'
    artifact.write_bytes(b'image')
    original = subprocess.Popen
    processes = []
    spawned = threading.Event()
    cleaning = threading.Event()
    release = threading.Event()
    clean_request = runner._clean_request

    def spawn(*args, **kwargs):
        """保存本次唯一 worker 供回收检查。"""
        process = original(*args, **kwargs)
        processes.append(process)
        spawned.set()
        return process

    def blocked_clean(current_request):
        """在同一请求的产物清理中固定忙态观察窗口。"""
        cleaning.set()
        assert release.wait(5)
        clean_request(current_request)

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn), \
            patch.object(runner, '_clean_request', side_effect=blocked_clean):
        runner.start(request(tmp_path))
        try:
            assert spawned.wait(5)
            runner.cancel()
            assert runner._cancel.is_set()
            assert cleaning.wait(5)
            assert runner.active
            assert results.empty()
            assert artifact.exists()
            assert processes[0].poll() is not None
            assert all(stream.closed for stream in
                       (processes[0].stdin, processes[0].stdout, processes[0].stderr))
            with pytest.raises(TestBusyError):
                runner.start(request(tmp_path, request_id='second'))
            assert len(processes) == 1
        finally:
            release.set()
        result = results.get(timeout=5)
    assert (result.request_id, result.status, result.reason) == (
        'first', 'failure', '测试已取消；已发出的请求仍可能完成')
    assert not artifact.exists()
    assert processes[0].poll() is not None
    assert all(stream.closed for stream in
               (processes[0].stdin, processes[0].stdout, processes[0].stderr))
    assert not runner.active
    assert runner._cancel is None
    assert results.empty()


@pytest.mark.parametrize('action', ('cancel', 'close'))
def test_blocked_large_stdin_reaps_real_worker(tmp_path, action):
    """Windows 大请求写入不读 stdin 的真实 worker 后仍可取消或关闭回收。"""
    if os.environ.get('GUI_TEST_LARGE_STDIN_CHILD') != '1':
        pid_path = tmp_path / 'worker.pid'
        run_large_stdin_child(
            (sys.executable, '-m', 'pytest',
             f'{__file__}::test_blocked_large_stdin_reaps_real_worker[{action}]',
             '-q'), timeout=20,
            env={**os.environ, 'GUI_TEST_LARGE_STDIN_CHILD': '1',
                 'GUI_TEST_LARGE_STDIN_PID_PATH': str(pid_path)})
        return

    from modules.gui.test_runner import CANCEL_REASON
    from modules.gui.test_runner import TestRunnerClosedError
    results = queue.Queue()
    runner = runner_for(results, command=(sys.executable, '-c',
                             'import time;time.sleep(30)'))
    original = subprocess.Popen
    original_io = runner._io
    processes = []
    io_threads = []
    writing = threading.Event()
    write_returned = threading.Event()
    payload = request(tmp_path, config={'blob': 'x' * (8 * 1024 * 1024)})

    def spawn(*args, **kwargs):
        """保留真实进程和管道，观察首次真实 stdin 写入入口。"""
        process = original(*args, **kwargs)
        processes.append(process)
        Path(os.environ['GUI_TEST_LARGE_STDIN_PID_PATH']).write_text(str(process.pid))
        write = process._stdin_write

        def observed(data):
            """记录写入量并继续执行原生阻塞写入。"""
            assert len(data) >= 8 * 1024 * 1024
            writing.set()
            write(data)
            write_returned.set()

        process._stdin_write = observed
        return process

    def tracked_io(*args):
        """记录实际执行管道通信的线程对象。"""
        io_threads.append(threading.current_thread())
        original_io(*args)

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn), \
            patch.object(runner, '_io', side_effect=tracked_io):
        try:
            runner.start(payload)
            assert writing.wait(10)
            assert not write_returned.wait(0.1)
            assert processes[0].poll() is None
            assert not processes[0].stdin.closed
            assert runner.active
            assert results.empty()
            getattr(runner, action)()
            if action == 'close':
                with pytest.raises(TestRunnerClosedError):
                    runner.start(request(tmp_path, request_id='second'))
            result = results.get(timeout=10)
            assert (result.request_id, result.status, result.reason) == (
                'first', 'failure', CANCEL_REASON)
            assert processes[0].poll() is not None
            assert all(stream.closed for stream in
                       (processes[0].stdin, processes[0].stdout, processes[0].stderr))
            assert not runner.active
            runner._supervisor.join(timeout=5)
            assert not runner._supervisor.is_alive()
            assert len(io_threads) == 1
            io_threads[0].join(timeout=5)
            assert io_threads[0].name == 'gui-test-io'
            assert not io_threads[0].is_alive()
            assert results.empty()
        finally:
            runner.cancel()
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            if runner._supervisor is not None:
                runner._supervisor.join(timeout=5)


@pytest.mark.parametrize('exit_code', (None, 2))
def test_large_stdin_child_reaps_reported_worker_on_timeout_or_failure(tmp_path, exit_code):
    """子 pytest 超时或失败时父进程回收已上报的真实 worker。"""
    pid_path = tmp_path / 'worker.pid'
    script = ('import subprocess, sys, time; '
              'worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]); '
              'open(sys.argv[1], "w").write(str(worker.pid)); '
              f'{"time.sleep(30)" if exit_code is None else "sys.exit(2)"}')
    command = (sys.executable, '-c', script, str(pid_path))
    try:
        with pytest.raises((subprocess.TimeoutExpired, AssertionError)):
            run_large_stdin_child(command, timeout=2)
        assert pid_path.exists()
        worker_pid = int(pid_path.read_text())
        assert not psutil.pid_exists(worker_pid)
    finally:
        if pid_path.exists():
            worker_pid = int(pid_path.read_text())
            if psutil.pid_exists(worker_pid):
                worker = psutil.Process(worker_pid)
                worker.kill()
                worker.wait(timeout=5)


@pytest.mark.parametrize('exit_code', (None, 2))
def test_large_stdin_child_reaps_detached_grandchild_without_killing_pid_file_target(
        tmp_path, exit_code):
    """超时和失败都清理真实孙进程，但不能信任 PID 文件中的无关进程。"""
    pid_path = tmp_path / 'worker.pid'
    grandchild_path = tmp_path / 'grandchild.pid'
    unrelated = subprocess.Popen((sys.executable, '-c', 'import time; time.sleep(30)'))
    script = ('import subprocess, sys, time; '
              'grandchild = subprocess.Popen([sys.executable, "-c", '
              '"import time; time.sleep(30)"]); '
              'open(sys.argv[1], "w").write(str(grandchild.pid)); '
              f'{"time.sleep(30)" if exit_code is None else "sys.exit(2)"}')
    try:
        pid_path.write_text(str(unrelated.pid))
        with pytest.raises((subprocess.TimeoutExpired, AssertionError)):
            run_large_stdin_child((sys.executable, '-c', script, str(grandchild_path)),
                                  timeout=2)
        assert grandchild_path.exists()
        assert unrelated.poll() is None
        assert not psutil.pid_exists(int(grandchild_path.read_text()))
    finally:
        unrelated.kill()
        unrelated.wait(timeout=5)
        if grandchild_path.exists():
            grandchild_pid = int(grandchild_path.read_text())
            if psutil.pid_exists(grandchild_pid):
                grandchild = psutil.Process(grandchild_pid)
                grandchild.kill()
                grandchild.wait(timeout=5)


def test_cancel_after_terminal_commit_is_noop(tmp_path):
    """已提交结果不可被迟来的取消改写。"""
    results = queue.Queue()
    runner = runner_for(results)
    runner.start(request(tmp_path))
    result = results.get(timeout=10)
    runner.cancel()
    runner.close()
    assert results.empty()
    assert result.request_id == 'first'


def test_reap_blocks_next_start(tmp_path):
    """释放槽位后新请求持有独立取消状态。"""
    from modules.gui.test_runner import TestBusyError
    results = queue.Queue()
    runner = runner_for(results)
    runner.start(request(tmp_path))
    with pytest.raises(TestBusyError):
        runner.start(request(tmp_path, request_id='second'))
    results.get(timeout=10)
    runner.start(replace(request(tmp_path), request_id='second'))
    assert results.get(timeout=10).request_id == 'second'


def test_communicate_once_on_independent_io_thread(tmp_path):
    """唯一 communicate 与入队分别在 I/O、监督线程执行。"""
    results = queue.Queue()
    runner = runner_for(results)
    original = subprocess.Popen
    observed = []

    def spawn(*args, **kwargs):
        """包装真实子进程的通信边界。"""
        process = original(*args, **kwargs)
        communicate = process.communicate

        def recorded(*args, **kwargs):
            """记录运行线程和输入参数。"""
            observed.append((threading.current_thread().name, args, kwargs))
            return communicate(*args, **kwargs)

        process.communicate = recorded
        return process

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn):
        runner.start(request(tmp_path))
        assert results.get(timeout=10).status == 'failure'
    assert len(observed) == 1
    assert observed[0][0] == 'gui-test-io'
    assert observed[0][1] == ()
    assert 'timeout' not in observed[0][2]
    assert observed[0][2]['input'].count(b'protocol_version') == 1


def test_cancel_during_spawn(tmp_path):
    """Popen 进行中取消仍回收返回的真实 worker。"""
    entered = threading.Event()
    resume = threading.Event()
    results = queue.Queue()
    runner = runner_for(results)
    original = subprocess.Popen
    processes = []

    def spawn(*args, **kwargs):
        """在创建真实进程前冻结 Popen 边界。"""
        entered.set()
        assert resume.wait(5)
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn):
        runner.start(request(tmp_path))
        assert entered.wait(5)
        runner.cancel()
        resume.set()
        assert results.get(timeout=10).reason == '测试已取消；已发出的请求仍可能完成'
    assert processes[0].poll() is not None
    assert not runner.active


def test_exit_before_io_done_keeps_active(tmp_path):
    """进程退出不意味着管道 I/O 已回收。"""
    results = queue.Queue()
    runner = runner_for(results)
    entered = threading.Event()
    resume = threading.Event()
    original_io = runner._io

    def delayed(*args):
        """在 I/O 线程真正退出前保持屏障。"""
        original_io(*args)
        entered.set()
        assert resume.wait(5)

    with patch.object(runner, '_io', side_effect=delayed):
        runner.start(request(tmp_path))
        assert entered.wait(10)
        assert runner.active
        assert results.empty()
        resume.set()
        assert results.get(timeout=10).status == 'failure'
    assert not runner.active


def test_close_does_not_delete_session_on_calling_thread(tmp_path):
    """会话目录清理由后台完成，关闭调用线程不执行磁盘 I/O。"""
    session = tmp_path / 'session'
    session.mkdir()
    (session / 'preview.jpg').write_bytes(b'image')
    results = queue.Queue()
    runner = runner_for(results)
    runner.start(request(tmp_path))
    results.get(timeout=10)
    session.mkdir(exist_ok=True)
    (session / 'preview.jpg').write_bytes(b'image')
    called = []
    original = __import__('shutil').rmtree

    def tracked(*args, **kwargs):
        """记录删除目录的线程。"""
        called.append(threading.current_thread().name)
        return original(*args, **kwargs)

    with patch('modules.gui.test_runner.shutil.rmtree', side_effect=tracked):
        runner.close()
        for thread in threading.enumerate():
            if thread.name == 'gui-test-cleanup':
                thread.join(timeout=5)
    assert not session.exists()
    assert called and 'MainThread' not in called


def test_valid_success_retains_preview_until_close(tmp_path):
    """合法成功保留预览至会话关闭。"""
    results = queue.Queue()
    runner = runner_for(results)
    session = tmp_path / 'session'
    session.mkdir()
    preview = session / 'preview.jpg'
    preview.write_bytes(b'image')
    original = subprocess.Popen

    def spawn(*args, **kwargs):
        """真实进程退出后将其输出替换为身份正确的协议结果。"""
        process = original(*args, **kwargs)
        communicate = process.communicate

        def valid(*a, **kw):
            """仍执行真实通信，再给监督线程合法消息。"""
            communicate(*a, **kw)
            return encode_result(GuiResult('first', 4, 7, 'success', '完成',
                                           (str(preview),))), b''

        process.communicate = valid
        return process

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn):
        runner.start(request(tmp_path))
        assert results.get(timeout=10).status == 'success'
    assert preview.exists()
    runner.close()
    runner._cleanup.join(timeout=5)
    assert not session.exists()


def test_cancel_before_terminal_commit_wins(tmp_path):
    """合法结果读取后但锁提交前的取消优先。"""
    results = queue.Queue()
    runner = runner_for(results)
    entered = threading.Event()
    resume = threading.Event()
    original = subprocess.Popen
    decode = __import__('modules.gui.test_runner', fromlist=['decode_result']).decode_result

    def spawn(*args, **kwargs):
        """使用真实子进程并提供合法结果。"""
        process = original(*args, **kwargs)
        communicate = process.communicate

        def valid(*a, **kw):
            """完成真实通信之后提供成功协议。"""
            communicate(*a, **kw)
            return encode_result(GuiResult('first', 4, 7, 'success', '完成', ())), b''

        process.communicate = valid
        return process

    def blocked_decode(*args, **kwargs):
        """合法结果完成后固定取消窗口。"""
        result = decode(*args, **kwargs)
        entered.set()
        assert resume.wait(5)
        return result

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn), \
            patch('modules.gui.test_runner.decode_result', side_effect=blocked_decode):
        runner.start(request(tmp_path))
        assert entered.wait(10)
        runner.cancel()
        resume.set()
        assert results.get(timeout=10).reason == '测试已取消；已发出的请求仍可能完成'
    assert results.empty()


def test_eof_before_exit_remains_cancellable(tmp_path):
    """stdout/stderr 已 EOF 但 worker 仍存活时取消生效。"""
    results = queue.Queue()
    command = (sys.executable, '-c',
               'import time;time.sleep(30)')
    runner = runner_for(results, command=command)
    original = subprocess.Popen
    reached = threading.Event()
    processes = []

    def spawn(*args, **kwargs):
        """真实进程继续存活，替身仅模拟先返回的 EOF。"""
        process = original(*args, **kwargs)
        processes.append(process)

        def observed(*a, **kw):
            """令 I/O 先完成，同时不改变真实进程退出时序。"""
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
            reached.set()
            return b'', b''

        process.communicate = observed
        return process

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn):
        runner.start(request(tmp_path))
        assert reached.wait(5)
        assert runner.active
        runner.cancel()
        assert results.get(timeout=5).status == 'failure'
    assert processes[0].poll() is not None


def test_io_failure_reaps_before_terminal(tmp_path):
    """通信抛错仍回收真实进程与三管道。"""
    results = queue.Queue()
    runner = runner_for(results)
    original = subprocess.Popen
    processes = []

    def spawn(*args, **kwargs):
        """仅替换通信失败，不替换真实进程生命周期。"""
        process = original(*args, **kwargs)
        processes.append(process)

        def broken(*a, **kw):
            """模拟通信阶段异常。"""
            raise OSError('pipe failure')

        process.communicate = broken
        return process

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn):
        runner.start(request(tmp_path))
        assert results.get(timeout=10).status == 'failure'
    assert processes[0].poll() is not None
    assert all(stream.closed for stream in
               (processes[0].stdin, processes[0].stdout, processes[0].stderr))


def test_spawn_failure_cleans_session(tmp_path):
    """启动失败仅清理本请求产物，不删除会话目录。"""
    session = tmp_path / 'session'
    session.mkdir()
    artifact = session / 'first.jpg'
    artifact.write_bytes(b'image')
    results = queue.Queue()
    runner = runner_for(results, command=(str(tmp_path / 'missing.exe'),))
    runner.start(request(tmp_path))
    assert results.get(timeout=5).status == 'failure'
    assert not artifact.exists()
    assert session.exists()
    assert not runner.active


def test_io_thread_start_failure_reaps_before_single_terminal(tmp_path):
    """I/O 线程无法启动时仍先回收真实 worker 和管道再提交一次失败。"""
    results = queue.Queue()
    runner = runner_for(results, command=(sys.executable, '-c',
                             'import time;time.sleep(0.5)'))
    original = subprocess.Popen
    processes = []
    failed = threading.Event()
    start = threading.Thread.start

    def spawn(*args, **kwargs):
        """保留真实子进程的三个管道供终态检查。"""
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    def reject_io(thread):
        """只在 I/O 线程启动边界注入异常。"""
        if thread.name == 'gui-test-io':
            failed.set()
            raise RuntimeError('cannot start I/O thread')
        return start(thread)

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn), \
            patch.object(threading.Thread, 'start', reject_io):
        runner.start(request(tmp_path))
        assert failed.wait(5)
        result = results.get(timeout=5)
    assert (result.request_id, result.document_generation, result.revision,
            result.status) == ('first', 4, 7, 'failure')
    assert processes[0].poll() is not None
    assert all(stream.closed for stream in
               (processes[0].stdin, processes[0].stdout, processes[0].stderr))
    assert not runner.active
    assert results.empty()


def test_failed_request_preserves_previous_preview_in_real_session(tmp_path):
    """同一真实目录中后续请求失败不得删掉前次预览。"""
    session = tmp_path / 'session'
    session.mkdir()
    previous = session / 'first.jpg'
    current = session / 'second.jpg'
    previous.write_bytes(b'previous')
    current.write_bytes(b'partial')
    results = queue.Queue()
    runner = runner_for(results, command=(str(tmp_path / 'missing.exe'),))
    runner.start(request(tmp_path, request_id='second'))
    result = results.get(timeout=5)
    assert result.status == 'failure'
    assert previous.read_bytes() == b'previous'
    assert not current.exists()
    assert not runner.active
    assert results.empty()


def test_close_never_joins_on_tk(tmp_path):
    """窗口关闭调用线程不等待线程和 worker。"""
    results = queue.Queue()
    runner = runner_for(results)
    runner.start(request(tmp_path))
    with patch.object(threading.Thread, 'join', side_effect=AssertionError('Tk join')):
        runner.close()
    assert results.get(timeout=10).status == 'failure'


def test_terminate_timeout_escalates_to_kill(tmp_path):
    """不读取 stdin 且忽略 terminate 的进程在宽限期后升级 kill。"""
    results = queue.Queue()
    runner = runner_for(results, command=(sys.executable, '-c',
                                          'import time; time.sleep(30)'))
    original = subprocess.Popen
    process_list = []
    terminated = []

    def spawn(*args, **kwargs):
        """替身进程不读 stdin、不响应 terminate，只由 kill 结束。"""
        process = original(*args, **kwargs)
        process_list.append(process)

        def ignored():
            """记录一次不生效的终止请求。"""
            terminated.append(process)

        process.terminate = ignored
        kill = process.kill
        process.kill = lambda: (process_list.append('kill'), kill())
        return process

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn):
        runner.start(request(tmp_path))
        while not process_list:
            threading.Event().wait(0.01)
        runner.cancel()
        assert results.get(timeout=10).status == 'failure'
    assert len(terminated) == 1
    assert process_list.count('kill') == 1
    assert process_list[0].poll() is not None


def test_cancel_exit_race_is_single_terminal(tmp_path):
    """worker 自然退出与取消交叠仍只提交一条终态。"""
    results = queue.Queue()
    runner = runner_for(results, command=(sys.executable, '-c', 'pass'))
    entered = threading.Event()
    resume = threading.Event()
    original = subprocess.Popen

    def spawn(*args, **kwargs):
        """等待真实短命进程自然结束后才释放 Popen。"""
        process = original(*args, **kwargs)
        process.wait(timeout=10)
        entered.set()
        assert resume.wait(5)
        return process

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn):
        runner.start(request(tmp_path))
        assert entered.wait(10)
        runner.cancel()
        resume.set()
        assert results.get(timeout=10).status == 'failure'
    assert results.empty()


def test_close_during_spawn_reaps_worker(tmp_path):
    """关闭发生在 Popen 内时仍等待该进程退出。"""
    from modules.gui.test_runner import TestRunnerClosedError
    results = queue.Queue()
    runner = runner_for(results)
    entered = threading.Event()
    resume = threading.Event()
    original = subprocess.Popen
    spawned = []

    def spawn(*args, **kwargs):
        """阻塞返回但保留真实 worker 句柄。"""
        process = original(*args, **kwargs)
        spawned.append(process)
        entered.set()
        assert resume.wait(5)
        return process

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn):
        runner.start(request(tmp_path))
        assert entered.wait(5)
        runner.close()
        with pytest.raises(TestRunnerClosedError):
            runner.start(request(tmp_path, request_id='new'))
        resume.set()
        assert results.get(timeout=10).status == 'failure'
    assert spawned[0].poll() is not None


def test_wrong_identity_is_failure(tmp_path):
    """合法 JSON 但来自其他文档的结果不可冒认本次成功。"""
    results = queue.Queue()
    runner = runner_for(results)
    original = subprocess.Popen

    def spawn(*args, **kwargs):
        """使用真实进程后注入错误代次消息。"""
        process = original(*args, **kwargs)
        communicate = process.communicate

        def mismatched(*a, **kw):
            """保留进程回收但改变返回的代次。"""
            communicate(*a, **kw)
            return encode_result(GuiResult('first', 5, 7, 'success', '', ())), b''

        process.communicate = mismatched
        return process

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn):
        runner.start(request(tmp_path))
        result = results.get(timeout=10)
    assert (result.document_generation, result.revision, result.status) == (4, 7, 'failure')


def test_cancel_while_communicate_blocked(tmp_path):
    """首次写入堵塞中取消仍由监督线程终止真实 worker。"""
    results = queue.Queue()
    runner = runner_for(results, command=(sys.executable, '-c',
                             'import time;time.sleep(30)'))
    original = subprocess.Popen
    entered = threading.Event()
    processes = []

    def spawn(*args, **kwargs):
        """保留真实进程及管道，记录实际写入前的边界。"""
        process = original(*args, **kwargs)
        processes.append(process)
        communicate = process.communicate

        def blocked(*a, **kw):
            """让通信结束只受真实进程退出驱动。"""
            entered.set()
            return communicate(*a, **kw)

        process.communicate = blocked
        return process

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn):
        runner.start(request(tmp_path, config={'large': 'x' * (9 * 1024 * 1024)}))
        assert entered.wait(5)
        runner.cancel()
        assert results.get(timeout=5).reason == '测试已取消；已发出的请求仍可能完成'
    assert processes[0].poll() is not None
    assert results.empty()


def test_io_failure_with_live_reader_reaps_worker(tmp_path):
    """读线程仍等待活进程 EOF 时，I/O 异常不阻断监督终止。"""
    results = queue.Queue()
    runner = runner_for(results, command=(sys.executable, '-c',
                             'import time;time.sleep(30)'))
    original = subprocess.Popen
    processes = []
    reader_started = threading.Event()

    def spawn(*args, **kwargs):
        """在真实 stdout 读线程启动后制造 communicate 失败。"""
        process = original(*args, **kwargs)
        processes.append(process)

        def broken(*a, **kw):
            """保持真实管道 reader 阻塞直到 worker 退出。"""
            process.stdout_thread = threading.Thread(
                target=process.stdout.read, name='gui-test-live-reader')
            process.stdout_thread.start()
            reader_started.set()
            raise OSError('pipe failure')

        process.communicate = broken
        return process

    with patch('modules.gui.test_runner.subprocess.Popen', side_effect=spawn):
        runner.start(request(tmp_path))
        assert reader_started.wait(5)
        assert results.get(timeout=5).status == 'failure'
    assert processes[0].poll() is not None
    assert not processes[0].stdout_thread.is_alive()


def test_close_during_success_cleanup_removes_preview(tmp_path):
    """关闭与成功收尾交叠时会话目录不能遗留。"""
    results = queue.Queue()
    runner = runner_for(results)
    session = tmp_path / 'session'
    session.mkdir()
    (session / 'first.jpg').write_bytes(b'image')
    cleaning = threading.Event()
    release = threading.Event()
    original = runner._clean_request

    def blocked(current):
        """将当前请求清理冻结在终态提交之前。"""
        cleaning.set()
        assert release.wait(5)
        original(current)

    try:
        with patch.object(runner, '_clean_request', side_effect=blocked):
            runner.start(request(tmp_path))
            assert cleaning.wait(10)
            runner.close()
            release.set()
            assert results.get(timeout=10).status == 'failure'
        assert not session.exists()
        assert not runner.active
    finally:
        release.set()
