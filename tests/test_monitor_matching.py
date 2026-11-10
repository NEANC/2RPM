#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""monitor 按监视目标匹配进程的组件测试。

用伪造的 psutil 进程对象验证按名称、按文件路径匹配以及逐进程降级规则，
不触碰真实进程、真实配置与网络；同时以特征测试锁定既有进程集合生命周期
与权限拒绝时的结束处理行为。
"""

import os
import sys
import time
from unittest.mock import patch

import psutil
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import monitor
from modules.monitor_target import MonitorTarget


class _FakeProcess:
    """伪造 psutil 进程对象，按需返回信息与可执行文件路径。"""

    def __init__(self, pid, name=None, create_time=1000.0,
                 exe_path=None, exe_error=None, info=None, info_error=None):
        """初始化伪造进程。

        Args:
            pid (int): 进程 PID
            name (str | None): 进程名
            create_time (float): 进程创建时间
            exe_path (str | None): exe 路径查询结果
            exe_error (Exception | None): exe 查询时抛出的异常
            info (dict | None): 完整覆盖的进程信息字典
            info_error (Exception | None): 读取 info 时抛出的异常
        """
        self.pid = pid
        self._info = info if info is not None else {
            'pid': pid, 'name': name, 'create_time': create_time
        }
        self._info_error = info_error
        self._exe_path = exe_path
        self._exe_error = exe_error
        self.exe_calls = 0

    @property
    def info(self):
        """返回进程信息，必要时抛出配置的异常。"""
        if self._info_error is not None:
            raise self._info_error
        return self._info

    def exe(self):
        """返回可执行文件路径，必要时抛出配置的异常。"""
        self.exe_calls += 1
        if self._exe_error is not None:
            raise self._exe_error
        return self._exe_path


class _FakeSpinner:
    """记录 spinner 调用的假 spinner。"""

    def __init__(self):
        """初始化调用记录。"""
        self.calls = []

    def write_done(self, text):
        """记录定格写入。"""
        self.calls.append(('write_done', text))

    def text(self, text):
        """记录普通文本。"""
        self.calls.append(('text', text))

    def done(self, text):
        """记录成功定格。"""
        self.calls.append(('done', text))

    def fail(self, text):
        """记录失败定格。"""
        self.calls.append(('fail', text))


def _collect(target, processes):
    """在伪造的进程列表上执行目标匹配收集。

    Args:
        target (MonitorTarget): 监视目标
        processes (list): 伪造进程列表

    Returns:
        dict: 收集结果
    """
    with patch('modules.monitor.psutil.process_iter', return_value=iter(processes)):
        return monitor._collect_matching_processes_for_target(target)


def _process_target(name):
    """构造进程名目标。"""
    return MonitorTarget(kind='process', value=name, cwd=None)


def _file_target(path):
    """构造文件路径目标。"""
    return MonitorTarget(kind='file', value=path, cwd=None)


def test_process_name_matches_exactly():
    """裸进程名只按大小写敏感的名称精确匹配。"""
    first = _FakeProcess(1, name='app.exe')
    second = _FakeProcess(2, name='app.exe')
    third = _FakeProcess(3, name='APP.EXE')
    fourth = _FakeProcess(4, name='other.exe')

    result = _collect(_process_target('app.exe'), [first, second, third, fourth])

    assert set(result.keys()) == {1, 2}
    assert result[1]['name'] == 'app.exe'
    # process 目标不得读取 exe 路径
    assert first.exe_calls == 0
    assert third.exe_calls == 0


def test_file_target_matches_only_that_path():
    """同名但位于不同目录的进程只按完整路径匹配。"""
    same_path = _FakeProcess(10, name='app.exe', exe_path=r'C:\Apps\A\app.exe')
    other_path = _FakeProcess(11, name='app.exe', exe_path=r'D:\Apps\B\app.exe')

    result = _collect(_file_target(r'C:\Apps\A\app.exe'), [same_path, other_path])

    assert set(result.keys()) == {10}


def test_file_path_compare_ignores_case_and_separators():
    """配置路径与进程 exe 路径在大小写与分隔符差异下视为相同。"""
    process = _FakeProcess(20, name='APP.EXE', exe_path=r'C:\Apps\A\app.exe')

    result = _collect(_file_target('C:/Apps/A/APP.EXE'), [process])

    assert set(result.keys()) == {20}
    assert result[20]['name'] == 'APP.EXE'


def test_unreadable_exe_falls_back_only_for_confirmed_name():
    """exe 被拒绝读取时，仅在进程名确认等于目标文件名时降级入选。"""
    same_name = _FakeProcess(
        31, name='app.exe', exe_error=psutil.AccessDenied(pid=31))
    other_name = _FakeProcess(
        32, name='other.exe', exe_error=psutil.AccessDenied(pid=32))
    missing_name = _FakeProcess(
        33, info={'pid': 33, 'create_time': 1000.0},
        exe_error=psutil.AccessDenied(pid=33))
    denied_info = _FakeProcess(
        34, info_error=psutil.AccessDenied(pid=34),
        exe_error=psutil.AccessDenied(pid=34))

    with patch('modules.monitor.LOGGER') as logger_mock:
        result = _collect(
            _file_target(r'C:\Apps\A\app.exe'),
            [same_name, other_name, missing_name, denied_info])

    assert set(result.keys()) == {31}
    warnings = [str(call.args[0]) for call in logger_mock.warning.call_args_list]
    fallback_warnings = [msg for msg in warnings if '降级' in msg]
    assert len(fallback_warnings) == 1
    assert '31' in fallback_warnings[0]


def test_disappeared_process_never_falls_back():
    """进程已退出时直接跳过，即使名称与目标文件名相同也不降级。"""
    disappeared = _FakeProcess(
        40, name='app.exe', exe_error=psutil.NoSuchProcess(pid=40))

    result = _collect(_file_target(r'C:\Apps\A\app.exe'), [disappeared])

    assert result == {}


def test_known_different_path_never_falls_back():
    """exe 路径可读但不同时，即使名称相同也不降级入选。"""
    different = _FakeProcess(50, name='app.exe', exe_path=r'D:\Apps\B\app.exe')

    result = _collect(_file_target(r'C:\Apps\A\app.exe'), [different])

    assert result == {}


def test_task_target_rejected():
    """计划任务目标不支持按进程匹配。"""
    task_target = MonitorTarget(kind='task', value=r'\Custom\MyTask', cwd=None)

    with pytest.raises(ValueError):
        monitor._collect_matching_processes_for_target(task_target)


def test_returned_structure_matches_existing():
    """返回结构必须与既有收集函数一致。"""
    process = _FakeProcess(60, name='app.exe', create_time=1234.5)

    with patch('modules.monitor._collect_matching_processes') as existing_mock:
        existing_mock.return_value = {60: {'name': 'app.exe', 'create_time': 1234.5}}
        expected = monitor._collect_matching_processes('app.exe')

    result = _collect(_process_target('app.exe'), [process])

    assert set(result.keys()) == {60}
    assert set(result[60].keys()) == {'name', 'create_time'}
    assert set(result[60].keys()) == set(expected[60].keys())
    assert result[60] == expected[60]


def test_initial_pid_set_is_frozen():
    """结束检测只使用初始 PID 集合，不把新发现的 PID 纳入本次判断。"""
    monitored_pids = {11, 12}
    current_processes = {
        12: {'name': 'app.exe', 'create_time': 200.0},
        13: {'name': 'app.exe', 'create_time': 300.0},
    }
    processes = {
        12: {
            'name': 'app.exe',
            'create_time': 200.0,
            'start_time': 0.0,
            'last_warning_time': 0.0,
            'timeout_count': 0,
        }
    }

    with patch.object(
            monitor, '_add_new_process',
            wraps=monitor._add_new_process) as add_mock:
        ended = monitor._detect_ended_pids(
            monitored_pids, current_processes, processes)

    assert ended == {11}
    assert 13 not in ended
    add_mock.assert_not_called()
    assert 13 not in processes

    before = time.time()
    monitor._add_new_process(processes, 13, 'app.exe', 300.0)
    after = time.time()

    assert 13 in processes
    assert before <= processes[13]['start_time'] <= after
    assert before <= processes[13]['last_warning_time'] <= after


def test_bound_access_denied_preserves_on_end():
    """PID 查询被拒绝时按结束处理，并沿用配置中的 on_end 路径。"""
    pid_info = {
        'pid': 123,
        'name': 'demo.exe',
        'create_time': 100.0,
        'start_time': time.time(),
        'last_warning_time': time.time(),
        'timeout_count': 0,
    }
    config = {'monitor': {'common': {}}, 'external': {}}
    sp = _FakeSpinner()

    with patch(
            'modules.monitor.psutil.Process',
            side_effect=psutil.AccessDenied(pid=123)), \
            patch('modules.monitor.time.sleep'), \
            patch(
                'modules.monitor._handle_process_end',
                return_value=False) as end_mock:
        monitor._monitor_single_pid(pid_info, config, sp)

    end_mock.assert_called_once()
    call_args = end_mock.call_args.args
    assert call_args[1] == pid_info['name']
    assert call_args[4] == config['external'].get('on_end', '')
    assert ('fail', '任务进程已退出，但推送全部失败') in sp.calls or \
        ('done', '任务进程已退出') in sp.calls
