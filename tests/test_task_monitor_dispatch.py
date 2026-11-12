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
from modules import task_monitor
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
        # 同一父替身下的子替身，便于用 mock_calls 断言跨函数调用顺序
        self.order = Mock()
        self.channel = self.order.ensure_task_channel_enabled
        self.query = self.order.query_task_events
        self.query.return_value = []
        self.bind = self.order.bind_task_event
        self.bind.return_value = None
        self.boundary = self.order.get_task_event_boundary
        self.boundary.return_value = SimpleNamespace(record_id=100, xml='anchor')
        self.run = self.order.subprocess_run
        self.run.return_value = SimpleNamespace(
            returncode=0, stdout=b'', stderr=b'')
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
        monkeypatch.setattr(monitor, 'ensure_task_channel_enabled',
                            self.channel, raising=False)
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


@pytest.mark.parametrize('found', [True, False], ids=['found', 'missing'])
def test_task_zero_wait_queries_once_after_clock_advances(env, found):
    """零等待首轮时钟前进 1 ms 后仍核验边界并查询一次。"""
    config = {'monitor': {'common': {'max_wait': '0s', 'check_interval': '1s'}}}
    anchor = SimpleNamespace(record_id=100, xml='anchor')
    event = _event(101, 4321)
    env.boundary.return_value = anchor
    env.query.return_value = [event] if found else []
    env.bind.return_value = dict(IDENTITY)
    env.clock.step = 0.001

    result = monitor._wait_task_process(
        config, TASK_TARGET, boundary_record_id=anchor,
        launch_trigger_time=1000.0)

    assert (result is not None) is found
    env.query.assert_called_once_with(TASK_VALUE, 2)
    assert env.boundary.call_count == (1 if found else 2)
    assert all(call.args == (100,) for call in env.boundary.call_args_list)
    assert env.bind.call_count == int(found)
    env.sleep.assert_not_called()


def test_task_zero_wait_boundary_loss_is_not_timeout(env):
    """零等待查询后边界身份变化须报错，不可当作普通超时。"""
    config = {'monitor': {'common': {'max_wait': '0s', 'check_interval': '1s'}}}
    env.clock.step = 0.001
    anchor = SimpleNamespace(record_id=100, xml='anchor')
    env.boundary.return_value = SimpleNamespace(record_id=100, xml='changed')

    with pytest.raises(TaskQueryError, match='边界失效'):
        monitor._wait_task_process(
            config, TASK_TARGET, boundary_record_id=anchor,
            launch_trigger_time=1000.0)

    env.query.assert_called_once_with(TASK_VALUE, 2)
    env.sleep.assert_not_called()


def test_task_zero_wait_query_error_is_not_timeout(env):
    """零等待查询故障保持异常语义，不触发普通超时。"""
    config = {'monitor': {'common': {'max_wait': '0s', 'check_interval': '1s'}}}
    env.clock.step = 0.001
    env.query.side_effect = TaskQueryError('通道不可用')

    with pytest.raises(TaskQueryError, match='通道不可用'):
        monitor._wait_task_process(config, TASK_TARGET)

    env.query.assert_called_once()
    env.sleep.assert_not_called()


def test_task_wait_does_not_sleep_past_deadline(env):
    """正等待剩余时间短于检查间隔时只休眠到期限。"""
    config = {'monitor': {'common': {'max_wait': '1s', 'check_interval': '3s'}}}
    env.clock.step = 0.001
    env.sleep.side_effect = lambda seconds: setattr(
        env.clock, 'current', env.clock.current + seconds)

    assert monitor._wait_task_process(config, TASK_TARGET) is None
    env.query.assert_called_once()
    env.sleep.assert_called_once()
    assert 0 < env.sleep.call_args.args[0] <= 1


def test_watch_uses_configured_lookback(env):
    """watch 模式按 monitor.watch.lookback_minutes 查询事件。"""
    config = {
        'monitor': {
            'common': {'max_wait': '3s', 'check_interval': '1s'},
            'watch': {'lookback_minutes': 7},
        },
    }
    env.query.return_value = [_event(1, 4321)]
    env.bind.return_value = dict(IDENTITY)

    pid_info = monitor._wait_task_process(
        config, TASK_TARGET, start_from_create_time=True)

    assert pid_info['pid'] == 4321
    env.query.assert_any_call(TASK_VALUE, 7)


def test_watch_start_time_from_create_time(env):
    """watch 模式的 start_time 取事件绑定的进程创建时间。"""
    env.query.return_value = [_event(1, 4321)]
    env.bind.return_value = dict(IDENTITY)

    pid_info = monitor._wait_task_process(
        BASE_CONFIG, TASK_TARGET, start_from_create_time=True)

    assert pid_info['start_time'] == IDENTITY['create_time']
    assert pid_info['last_warning_time'] != pid_info['start_time']
    assert pid_info['last_warning_time'] == env.clock.values[-1]


def test_watch_first_warning_waits_full_binding_interval(env):
    """接管旧实例后首次警告等待完整间隔，运行时长仍从创建时刻计算。"""
    env.clock.current = 1000.0
    env.clock.step = 0.0
    env.query.return_value = [_event(1, 4321)]
    env.bind.return_value = {
        'pid': 4321, 'name': 'python.exe', 'create_time': 0.0,
    }
    config = {
        'monitor': {'common': {
            'max_wait': '3s', 'check_interval': '1s',
            'timeout_interval': '15m',
        }},
    }

    pid_info = monitor._watch_task_target(config, TASK_TARGET)
    assert pid_info['start_time'] == 0.0
    assert pid_info['last_warning_time'] == 1000.0

    for elapsed, expected_count in [(1, 0), (899, 0), (900, 1), (901, 1)]:
        env.clock.current = 1000.0 + elapsed
        monitor._check_process_timeout(
            config, pid_info, pid_info['pid'], env.clock(), 900, '', 3,
        )
        assert env.send.call_count == expected_count
        assert pid_info['timeout_count'] == expected_count

    env.send.assert_called_once()
    args, kwargs = env.send.call_args
    assert args == (config, 'on_timeout')
    assert kwargs['process_pid'] == 4321
    assert kwargs['process_run_time'] == '0:31:40'
    assert pid_info['last_warning_time'] == 1900.0
    env.action.assert_not_called()


@pytest.mark.parametrize('elapsed,expected_count', [(899, 0), (900, 1)])
def test_watch_single_pid_first_warning_boundary(env, monkeypatch,
                                                 elapsed, expected_count):
    """真实单 PID 监视单轮按显式 15m 配置决定首次警告。"""
    env.clock.current = 1000.0
    env.clock.step = 0.0
    env.query.return_value = [_event(1, 4321)]
    env.bind.return_value = {
        'pid': 4321, 'name': 'python.exe', 'create_time': 0.0,
    }
    config = {
        'monitor': {'common': {
            'max_wait': '3s', 'check_interval': '1s',
            'timeout_interval': '15m', 'timeout_threshold': 3,
        }},
    }
    pid_info = monitor._watch_task_target(config, TASK_TARGET)
    assert pid_info['start_time'] == 0.0
    assert pid_info['last_warning_time'] == 1000.0

    class StopAfterOneLoop(Exception):
        """在第二轮休眠时结束受控监视。"""

    process = Mock()
    process.create_time.return_value = 0.0
    monkeypatch.setattr(monitor.psutil, 'Process', Mock(return_value=process))
    env.sleep.side_effect = [None, StopAfterOneLoop()]
    env.clock.current = 1000.0 + elapsed

    with pytest.raises(StopAfterOneLoop):
        monitor._monitor_single_pid(pid_info, config, env.spinner)

    assert env.sleep.call_count == 2
    process.create_time.assert_called_once_with()
    assert env.send.call_count == expected_count
    assert pid_info['timeout_count'] == expected_count
    assert pid_info['last_warning_time'] == (1900.0 if expected_count else 1000.0)
    if expected_count:
        assert env.send.call_args.args == (config, 'on_timeout')
        assert env.send.call_args.kwargs['process_run_time'] == '0:31:40'
        assert env.send.call_args.kwargs['process_pid'] == 4321
    env.action.assert_not_called()


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


@pytest.mark.parametrize('property_value,events', [
    ((False, 13), [_event(101, 4321)]),
    ((False, 13), []),
    (RuntimeError('get failed'), [_event(101, 4321)]),
    ('open_error', [_event(101, 4321)]),
], ids=['disabled_with_history', 'disabled_empty', 'property_error',
        'open_error'])
def test_launch_channel_precheck_never_triggers(
        env, monkeypatch, property_value, events):
    """真实通道检查拒绝禁用与读取失败，禁止业务推送及外部动作。"""
    handle = Mock()
    env.query.return_value = events
    monkeypatch.setattr(monitor, 'ensure_task_channel_enabled',
                        lambda: task_monitor.ensure_task_channel_enabled(),
                        raising=False)
    open_config = Mock(return_value=handle)
    if property_value == 'open_error':
        open_config.side_effect = RuntimeError('open failed')
    monkeypatch.setattr(task_monitor.win32evtlog, 'EvtOpenChannelConfig',
                        open_config)
    get_property = Mock()
    if isinstance(property_value, Exception):
        get_property.side_effect = property_value
    else:
        get_property.return_value = property_value
    monkeypatch.setattr(task_monitor.win32evtlog,
                        'EvtGetChannelConfigProperty', get_property)

    with pytest.raises(SystemExit) as exc:
        monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)

    assert exc.value.code == 1
    env.query.assert_not_called()
    env.boundary.assert_not_called()
    env.run.assert_not_called()
    env.send.assert_not_called()
    env.action.assert_not_called()
    env.notify_fail.assert_called_once()
    assert '未触发任务' in env.notify_fail.call_args.args[0]
    if property_value == 'open_error':
        handle.Close.assert_not_called()
        get_property.assert_not_called()
    else:
        handle.Close.assert_called_once_with()


@pytest.mark.parametrize('signal', [KeyboardInterrupt(), SystemExit(2)],
                         ids=['keyboard_interrupt', 'system_exit'])
def test_launch_channel_control_signal_propagates(env, monkeypatch, signal):
    """配置读取控制信号原样传播，不触发且释放已打开句柄。"""
    handle = Mock()
    monkeypatch.setattr(monitor, 'ensure_task_channel_enabled',
                        lambda: task_monitor.ensure_task_channel_enabled())
    monkeypatch.setattr(task_monitor.win32evtlog, 'EvtOpenChannelConfig',
                        Mock(return_value=handle))
    monkeypatch.setattr(task_monitor.win32evtlog,
                        'EvtGetChannelConfigProperty', Mock(side_effect=signal))

    with pytest.raises(type(signal)) as caught:
        monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)

    assert caught.value is signal
    handle.Close.assert_called_once_with()
    env.query.assert_not_called()
    env.run.assert_not_called()
    env.send.assert_not_called()
    env.action.assert_not_called()
    env.notify_fail.assert_not_called()


def test_launch_enabled_check_precedes_event_query_and_trigger(env):
    """启用检查先于历史查询、边界建立和触发。"""
    env.query.return_value = [_event(101, 4321)]
    env.bind.return_value = dict(IDENTITY)

    monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)

    names = [call[0] for call in env.order.mock_calls]
    assert names.index('ensure_task_channel_enabled') < names.index(
        'query_task_events') < names.index('get_task_event_boundary') < names.index(
            'subprocess_run')


def test_launch_boundary_filters_old_events(env):
    """只采用边界之后的事件，边界先于触发读取，回溯窗口固定 1 分钟。"""
    config = {
        'monitor': {
            'common': {'max_wait': '3s', 'check_interval': '1s'},
            'watch': {'lookback_minutes': 7},
        },
    }
    old = _event(99, 1111)
    new = _event(101, 2222)
    env.query.return_value = [old, new]
    env.boundary.return_value = SimpleNamespace(record_id=100, xml='anchor')
    env.bind.return_value = {'pid': 2222, 'name': 'new.exe', 'create_time': 555.0}

    pid_info = monitor._launch_task_target(config, TASK_TARGET)

    assert pid_info['pid'] == 2222
    assert [call.args[0] for call in env.bind.call_args_list] == [new]
    called_names = [call[0] for call in env.order.mock_calls]
    assert (
        called_names.index('get_task_event_boundary')
        < called_names.index('subprocess_run')
    )
    env.query.assert_any_call(TASK_VALUE, 1)


def test_launch_ignores_zero_watch_lookback(env):
    """watch.lookback_minutes 为 0 时 launch 仍以 1 分钟窗口完成绑定。"""
    config = {
        'monitor': {
            'common': {'max_wait': '3s', 'check_interval': '1s'},
            'watch': {'lookback_minutes': 0},
        },
    }
    env.query.return_value = [_event(101, 2222)]
    env.boundary.return_value = SimpleNamespace(record_id=100, xml='anchor')
    env.bind.return_value = {'pid': 2222, 'name': 'new.exe', 'create_time': 555.0}

    pid_info = monitor._launch_task_target(config, TASK_TARGET)

    assert pid_info['pid'] == 2222
    env.query.assert_any_call(TASK_VALUE, 1)


def test_launch_zero_boundary_does_not_trigger(env):
    """边界不可用时直接退出，不调用触发命令。"""
    env.boundary.return_value = 0

    with pytest.raises(SystemExit) as exc:
        monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)

    assert exc.value.code == 1
    assert env.run.call_count == 0
    assert '事件边界' in env.notify_fail.call_args.args[0]


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


def test_launch_wait_timeout_sends_wait_timeout_notification_and_action(env):
    """launch 等待新事件超时时发送 on_wait_timeout 并执行外部动作。"""
    config = {
        'monitor': {'common': {'max_wait': '2s', 'check_interval': '1s'}},
        'external': {'on_wait_timeout': 'C:\\Tools\\alert.exe'},
    }

    with pytest.raises(SystemExit) as exc:
        monitor._launch_task_target(config, TASK_TARGET)

    assert exc.value.code == 1
    timeout_calls = [
        call for call in env.send.call_args_list
        if call.args[1] == 'on_wait_timeout'
    ]
    assert len(timeout_calls) == 1
    assert timeout_calls[0].kwargs['process_name'] == TASK_VALUE
    env.action.assert_called_once_with('C:\\Tools\\alert.exe')
    assert env.notify_fail.call_args.args[0] == '未检测到新的任务进程'
    env.run.assert_called_once()


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
