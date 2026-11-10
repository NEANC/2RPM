#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计划任务等待绑定与触发调度的行为测试。

全程使用替身：不启动真实进程、不读写真实事件日志、不发送真实通知、
不执行真实外部动作，也不做真实等待。
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import monitor
from modules.monitor_target import MonitorTarget
from modules.task_monitor import TaskQueryError

TASK_VALUE = '\\Custom\\MyTask'
TASK_TARGET = MonitorTarget(kind='task', value=TASK_VALUE, cwd=None)

BASE_CONFIG = {
    'monitor': {'common': {'max_wait': '3s', 'check_interval': '1s'}},
}

IDENTITY = {
    'pid': 4321,
    'name': 'python.exe',
    'create_time': 555.0,
}


class _FakeClock:
    """每次调用自增的假时钟，记录全部读数。"""

    def __init__(self, start=1000.0, step=1.0):
        """初始化假时钟。

        Args:
            start (float): 起始读数
            step (float): 每次读取的增量
        """
        self.current = start
        self.step = step
        self.values = []

    def __call__(self):
        """返回递增后的读数并记录。

        Returns:
            float: 当前读数
        """
        self.current += self.step
        self.values.append(self.current)
        return self.current


class _FakeSpinner:
    """假 spinner，同时充当 spinner_phase 返回的上下文管理器。"""

    def __init__(self):
        """初始化调用记录。"""
        self.phases = []
        self.texts = []
        self.dones = []
        self.fails = []
        self.write_dones = []
        self.write_fails = []

    def __call__(self, text):
        """记录阶段文案并返回自身的上下文管理器。

        Args:
            text (str): spinner 初始文案

        Returns:
            _FakeSpinner: 自身
        """
        self.phases.append(text)
        return self

    def __enter__(self):
        """进入阶段上下文。

        Returns:
            _FakeSpinner: 自身
        """
        return self

    def __exit__(self, exc_type, exc, traceback):
        """退出阶段上下文，不吞异常。

        Returns:
            bool: 恒为 False
        """
        return False

    def text(self, message):
        """记录普通文案。

        Args:
            message (str): 文案
        """
        self.texts.append(message)

    def done(self, message):
        """记录成功定格。

        Args:
            message (str): 文案
        """
        self.dones.append(message)

    def fail(self, message):
        """记录失败定格。

        Args:
            message (str): 文案
        """
        self.fails.append(message)

    def write_done(self, message):
        """记录内联成功反馈。

        Args:
            message (str): 文案
        """
        self.write_dones.append(message)

    def write_fail(self, message):
        """记录内联失败反馈。

        Args:
            message (str): 文案
        """
        self.write_fails.append(message)


class _Harness:
    """集中安装全部替身，供单个用例定制行为。"""

    def __init__(self):
        """初始化替身默认行为。"""
        self.spinner = _FakeSpinner()
        self.clock = _FakeClock()
        self.query = Mock(return_value=[])
        self.bind = Mock(return_value=None)
        self.boundary = Mock(return_value=0)
        self.run = Mock(return_value=SimpleNamespace(
            returncode=0, stdout=b'', stderr=b''))
        self.notify_fail = Mock()
        self.send = Mock(return_value=[('console', True)])
        self.action = Mock(return_value={
            'display_name': 'alert.exe',
            'display_path': 'C:\\Tools\\alert.exe',
        })
        self.sleep = Mock()

    def install(self, monkeypatch):
        """把替身注入 monitor 命名空间。

        Args:
            monkeypatch: pytest 的 monkeypatch 夹具
        """
        monkeypatch.setattr(monitor, 'query_task_events', self.query)
        monkeypatch.setattr(monitor, 'bind_task_event', self.bind)
        monkeypatch.setattr(monitor, 'get_task_event_boundary', self.boundary)
        monkeypatch.setattr(monitor.subprocess, 'run', self.run)
        monkeypatch.setattr(monitor, 'spinner_phase', self.spinner)
        monkeypatch.setattr(
            monitor, '_send_notification_with_feedback', self.send)
        monkeypatch.setattr(monitor, 'run_external_action', self.action)
        monkeypatch.setattr(monitor, 'notify_fail', self.notify_fail)
        monkeypatch.setattr(monitor.time, 'time', self.clock)
        monkeypatch.setattr(monitor.time, 'sleep', self.sleep)


@pytest.fixture
def env(monkeypatch):
    """安装替身并返回环境对象。

    Args:
        monkeypatch: pytest 的 monkeypatch 夹具

    Returns:
        _Harness: 替身集合
    """
    harness = _Harness()
    harness.install(monkeypatch)
    return harness


def _event(record_id, pid):
    """构造只需 record_id 的候选事件。

    Args:
        record_id (int): 事件记录号
        pid (int): 事件中的进程号

    Returns:
        SimpleNamespace: 候选事件替身
    """
    return SimpleNamespace(record_id=record_id, pid=pid)


def test_watch_skips_newest_invalid_uses_older_valid(env):
    """最新候选绑定失败时改用更早的有效候选。"""
    newest = _event(200, 1111)
    older = _event(199, 2222)
    env.query.return_value = [newest, older]
    env.bind.side_effect = [
        None,
        {'pid': 2222, 'name': 'older.exe', 'create_time': 555.0},
    ]

    pid_info = monitor._watch_task_target(BASE_CONFIG, TASK_TARGET)

    assert pid_info['pid'] == 2222
    assert [call.args[0] for call in env.bind.call_args_list] == [newest, older]


def test_watch_returns_none_after_max_wait(env):
    """始终没有候选时按 max_wait 返回 None，且发生过轮询等待。"""
    assert monitor._wait_task_process(BASE_CONFIG, TASK_TARGET) is None
    assert env.sleep.call_count >= 1


def test_watch_start_time_from_create_time(env):
    """watch 模式的 start_time 取事件绑定的进程创建时间。"""
    env.query.return_value = [_event(1, 4321)]
    env.bind.return_value = dict(IDENTITY)

    pid_info = monitor._wait_task_process(
        BASE_CONFIG, TASK_TARGET, start_from_create_time=True)

    assert pid_info['start_time'] == IDENTITY['create_time']
    assert pid_info['last_warning_time'] != pid_info['start_time']
    assert pid_info['last_warning_time'] == env.clock.values[-1]


def test_launch_start_time_is_binding_moment(env):
    """launch 模式的 start_time 取绑定成功的时刻。"""
    env.query.return_value = [_event(101, 4321)]
    env.bind.return_value = dict(IDENTITY)

    pid_info = monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)

    assert pid_info['start_time'] == env.clock.values[-2]
    assert pid_info['last_warning_time'] == env.clock.values[-1]
    assert pid_info['start_time'] != IDENTITY['create_time']


def test_launch_precheck_failure_does_not_trigger(env):
    """预检读取日志失败时不触发计划任务。"""
    env.query.side_effect = TaskQueryError('通道不可用')

    with pytest.raises(SystemExit) as exc:
        monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)

    assert exc.value.code == 1
    assert env.run.call_count == 0
    assert '无法读取计划任务事件日志，未触发任务' in env.notify_fail.call_args.args[0]


def test_launch_boundary_filters_old_events(env):
    """只采用边界之后的事件。"""
    old = _event(99, 1111)
    new = _event(101, 2222)
    env.query.return_value = [old, new]
    env.boundary.return_value = 100
    env.bind.return_value = {'pid': 2222, 'name': 'new.exe', 'create_time': 555.0}

    pid_info = monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)

    assert pid_info['pid'] == 2222
    assert [call.args[0] for call in env.bind.call_args_list] == [new]


def test_launch_trigger_called_once(env):
    """launch 流程只调用一次 schtasks /run。"""
    env.query.return_value = [_event(101, 4321)]
    env.bind.return_value = dict(IDENTITY)

    monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)

    assert env.run.call_count == 1
    args, kwargs = env.run.call_args
    assert args[0] == ['schtasks', '/run', '/tn', TASK_VALUE]
    assert kwargs == {'capture_output': True, 'check': False}


def test_launch_run_rc_zero_is_not_success(env):
    """返回码 0 但没有新事件时仍视为失败。"""
    env.run.return_value = SimpleNamespace(
        returncode=0, stdout=b'', stderr=b'')

    with pytest.raises(SystemExit) as exc:
        monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)

    assert exc.value.code == 1
    assert '未检测到新的任务进程' in env.notify_fail.call_args.args[0]


def test_launch_post_trigger_query_error_no_retrigger(env):
    """触发后查询失败时退出且不重复触发。"""
    env.query.side_effect = [[], TaskQueryError('日志不可用')]

    with pytest.raises(SystemExit) as exc:
        monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)

    assert exc.value.code == 1
    assert env.run.call_count == 1
    assert '已触发任务，但无法确认监视实例' in env.notify_fail.call_args.args[0]


def test_launch_trigger_rc_nonzero_exits(env):
    """触发返回码非 0 时带解码后的错误文本退出。"""
    env.run.return_value = SimpleNamespace(
        returncode=2, stdout=b'', stderr=b'ERROR: access denied')

    with pytest.raises(SystemExit) as exc:
        monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)

    assert exc.value.code == 1
    assert 'ERROR: access denied' in env.notify_fail.call_args.args[0]
    assert '未检测到新的任务进程' not in env.notify_fail.call_args.args[0]


def test_watch_timeout_sends_notification_and_action(env):
    """watch 超时发送 on_wait_timeout 通知并执行一次外部动作。"""
    config = {
        'monitor': {'common': {'max_wait': '2s', 'check_interval': '1s'}},
        'external': {'on_wait_timeout': 'C:\\Tools\\alert.exe'},
    }

    with pytest.raises(SystemExit) as exc:
        monitor._watch_task_target(config, TASK_TARGET)

    assert exc.value.code == 1
    timeout_calls = [
        call for call in env.send.call_args_list
        if call.args[1] == 'on_wait_timeout'
    ]
    assert len(timeout_calls) == 1
    assert timeout_calls[0].kwargs['process_name'] == TASK_VALUE
    env.action.assert_called_once_with('C:\\Tools\\alert.exe')
    assert env.notify_fail.call_args.args[0] == '未能获取有效的 PID 信息'


def test_watch_query_error_skips_wait_timeout_notification(env):
    """watch 查询事件日志失败时不发送等待超时通知。"""
    env.query.side_effect = TaskQueryError('通道不可用')

    with pytest.raises(SystemExit) as exc:
        monitor._watch_task_target(BASE_CONFIG, TASK_TARGET)

    assert exc.value.code == 1
    timeout_calls = [
        call for call in env.send.call_args_list
        if call.args[1] == 'on_wait_timeout'
    ]
    assert timeout_calls == []
    assert '事件日志' in env.notify_fail.call_args.args[0]


def test_keyboard_interrupt_propagates(env):
    """等待期间收到取消时先提示再原样抛出。"""
    env.sleep.side_effect = KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        monitor._wait_task_process(BASE_CONFIG, TASK_TARGET)

    env.notify_fail.assert_called_once()
    assert env.notify_fail.call_args.args[0] == '任务被取消，退出等待循环'
