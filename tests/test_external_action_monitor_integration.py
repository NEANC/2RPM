#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""monitor.py external action 调用点回归测试。"""

import os
import subprocess
import sys
import threading
from concurrent.futures import CancelledError

import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import monitor
from modules.monitor import ProcessTimeoutExitRequested


def _external_result(action_type='task', target=r'\Custom\MyTask'):
    """构造 run_external_action 的返回元数据。"""
    display_name = 'MyTask' if action_type == 'task' else os.path.basename(target)
    return {
        'type': action_type,
        'target': target,
        'display_name': display_name,
        'display_path': target,
    }


def _config_with_external(key, value):
    """构造只包含 external 触发项的测试配置。"""
    return {
        'monitor': {
            'mode': 'psutil',
            'common': {
                'timeout_interval': '1s', 'loop_interval': '1s',
                'timeout_threshold': 1, 'max_wait': '0s',
                'check_interval': '1s',
            },
            'psutil': {'process_name': 'missing.exe'},
            'task_scheduler': {
                'task_name': r'\Custom\MainTask',
                'lookback_minutes': 5,
            },
            'launch': {
                'type': 'task', 'path': None, 'args': None,
                'cwd': None, 'task_name': r'\Custom\MainTask',
            },
        },
        'external': {key: value}
    }


def test_monitor_processes_rejects_invalid_mode_without_fallback():
    """直接调用时非法 mode 应记录错误并停止，不应回退到 psutil。"""
    config = _config_with_external('on_end', '')
    config['monitor']['mode'] = 'invalid'

    with patch('modules.monitor.LOGGER.error') as error_mock, \
            patch('modules.monitor._collect_matching_processes') as collect_mock:
        with pytest.raises(SystemExit):
            monitor.monitor_processes(config)

    error_mock.assert_called_once()
    collect_mock.assert_not_called()


def test_handle_process_end_uses_external_action_metadata():
    """on_end 成功执行后应使用 external action 元数据发送 on_external。"""
    sp = MagicMock()
    config = _config_with_external('on_end', r'task:\Custom\MyTask')

    with patch('modules.monitor.send_notification', return_value=[] ) as notify_mock, \
            patch('modules.monitor.run_external_action', return_value=_external_result()) as action_mock:
        monitor._handle_process_end(
            config,
            'demo.exe',
            123,
            10,
            r'task:\Custom\MyTask',
            sp,
        )

    action_mock.assert_called_once_with(r'task:\Custom\MyTask')
    notify_mock.assert_any_call(
        config,
        'on_external',
        external_program_name='MyTask',
        external_program_path=r'\Custom\MyTask',
        process_name='demo.exe',
        process_pid=123,
    )


def test_check_process_timeout_uses_external_action_metadata():
    """on_timeout 到达阈值时应使用统一入口并请求停止当前 PID 监视。"""
    config = _config_with_external('on_timeout', r'task:\Custom\MyTask')
    process_info = {
        'name': 'demo.exe',
        'start_time': 0,
        'last_warning_time': 0,
        'timeout_count': 0,
    }

    with patch('modules.monitor.send_notification', return_value=[] ) as notify_mock, \
            patch('modules.monitor.run_external_action', return_value=_external_result()) as action_mock:
        with pytest.raises(ProcessTimeoutExitRequested):
            monitor._check_process_timeout(
                config,
                process_info,
                123,
                10,
                1,
                r'task:\Custom\MyTask',
                1,
            )

    action_mock.assert_called_once_with(r'task:\Custom\MyTask')
    notify_mock.assert_any_call(
        config,
        'on_external',
        external_program_name='MyTask',
        external_program_path=r'\Custom\MyTask',
        process_name='demo.exe',
        process_pid=123,
    )


def test_monitor_processes_wait_timeout_uses_external_action():
    """普通进程等待启动超时的 on_wait_timeout 应调用统一入口。"""
    config = {
        'monitor': {
            'mode': 'psutil',
            'common': {
                'timeout_interval': '0s', 'loop_interval': '1s',
                'timeout_threshold': 1, 'max_wait': '0s',
                'check_interval': '1s',
            },
            'psutil': {'process_name': 'missing.exe'},
            'task_scheduler': {
                'task_name': r'\Custom\MainTask',
                'lookback_minutes': 5,
            },
            'launch': {
                'type': 'task', 'path': None, 'args': None,
                'cwd': None, 'task_name': r'\Custom\MainTask',
            },
        },
        'external': {'on_wait_timeout': r'task:\Custom\MyTask'},
    }
    spinner = MagicMock()
    spinner.__enter__.return_value = spinner
    spinner.__exit__.return_value = False

    with patch('modules.monitor._collect_matching_processes', return_value={}), \
            patch('modules.monitor.send_notification', return_value=[]) as notify_mock, \
            patch('modules.monitor.spinner_phase', return_value=spinner), \
            patch('modules.monitor.run_external_action', return_value=_external_result()) as action_mock, \
            patch('modules.monitor.notify_fail'):
        with pytest.raises(SystemExit):
            monitor.monitor_processes(config)

    action_mock.assert_called_once_with(r'task:\Custom\MyTask')
    notify_mock.assert_any_call(
        config,
        'on_external',
        external_program_name='MyTask',
        external_program_path=r'\Custom\MyTask',
        process_name='missing.exe',
        process_pid=None,
    )


def test_monitor_via_task_scheduler_wait_timeout_uses_external_action():
    """task_scheduler 模式等待触发超时时 on_wait_timeout 应调用统一入口。"""
    config = {
        'monitor': {
            'mode': 'task_scheduler',
            'common': {
                'timeout_interval': '1s', 'loop_interval': '1s',
                'timeout_threshold': 1, 'max_wait': '0s',
                'check_interval': '1s',
            },
            'psutil': {'process_name': 'missing.exe'},
            'task_scheduler': {
                'task_name': r'\Custom\MainTask',
                'lookback_minutes': 10,
            },
            'launch': {
                'type': 'task', 'path': None, 'args': None,
                'cwd': None, 'task_name': r'\Custom\MainTask',
            },
        },
        'external': {'on_wait_timeout': r'task:\Custom\MyTask'},
    }
    spinner = MagicMock()
    spinner.__enter__.return_value = spinner
    spinner.__exit__.return_value = False

    with patch('modules.monitor.query_task_pid', return_value={'state': 'not_found'}), \
            patch('modules.monitor.send_notification', return_value=[]) as notify_mock, \
            patch('modules.monitor.spinner_phase', return_value=spinner), \
            patch('modules.monitor.run_external_action', return_value=_external_result()) as action_mock, \
            patch('modules.monitor.notify_fail'):
        with pytest.raises(SystemExit):
            monitor.monitor_via_task_scheduler(config)

    action_mock.assert_called_once_with(r'task:\Custom\MyTask')
    notify_mock.assert_any_call(
        config,
        'on_external',
        external_program_name='MyTask',
        external_program_path=r'\Custom\MyTask',
        process_name=r'\Custom\MainTask',
        process_pid=None,
    )


def test_launch_task_uses_fixed_one_minute_lookback():
    """launch task 查询 PID 时固定使用 1 分钟回溯窗口。"""
    config = {
        'monitor': {
            'mode': 'launch',
            'common': {'max_wait': '1s', 'check_interval': '1s'},
            'task_scheduler': {'lookback_minutes': 99},
            'launch': {
                'type': 'task', 'path': None, 'args': None,
                'cwd': None, 'task_name': r'\Custom\MainTask',
            },
        },
        'external': {},
    }
    spinner = MagicMock()
    spinner.__enter__.return_value = spinner
    spinner.__exit__.return_value = False

    with patch('modules.monitor.subprocess.run') as run_mock, \
            patch('modules.monitor.query_task_pid', return_value={'state': 'not_found'}) as query_mock, \
            patch('modules.monitor.spinner_phase', return_value=spinner), \
            patch('modules.monitor.notify_fail'):
        run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        with pytest.raises(SystemExit):
            monitor._launch_task(config['monitor']['launch'], config)

    query_mock.assert_called_with(r'\Custom\MainTask', lookback_minutes=1)


def test_launch_task_wait_timeout_uses_external_action_metadata():
    """launch task 等待进程启动超时时应调用统一入口并发送 on_external。"""
    config = {
        'monitor': {
            'mode': 'launch',
            'common': {
                'timeout_interval': '1s', 'loop_interval': '1s',
                'timeout_threshold': 1, 'max_wait': '0s',
                'check_interval': '1s',
            },
            'psutil': {'process_name': 'missing.exe'},
            'task_scheduler': {
                'task_name': r'\Custom\MainTask',
                'lookback_minutes': 5,
            },
            'launch': {
                'type': 'task', 'path': None, 'args': None,
                'cwd': None, 'task_name': r'\Custom\MainTask',
            },
        },
        'external': {'on_wait_timeout': r'task:\Custom\MyTask'},
    }
    launch_section = config['monitor']['launch']
    spinner = MagicMock()
    spinner.__enter__.return_value = spinner
    spinner.__exit__.return_value = False

    with patch('modules.monitor.subprocess.run') as run_mock, \
            patch('modules.monitor.query_task_pid', return_value={'state': 'not_found'}), \
            patch('modules.monitor.send_notification', return_value=[]) as notify_mock, \
            patch('modules.monitor.spinner_phase', return_value=spinner), \
            patch('modules.monitor.run_external_action', return_value=_external_result()) as action_mock, \
            patch('modules.monitor.notify_fail'):
        run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        with pytest.raises(SystemExit):
            monitor._launch_task(launch_section, config)

    action_mock.assert_called_once_with(r'task:\Custom\MyTask')
    notify_mock.assert_any_call(
        config,
        'on_external',
        external_program_name='MyTask',
        external_program_path=r'\Custom\MyTask',
        process_name=r'\Custom\MainTask',
        process_pid=None,
    )


class _FakeTty:
    """伪造 TTY 输出流，仅提供 isatty 判定。"""

    def isatty(self):
        """始终返回 True，模拟 TTY 环境。"""
        return True


class _RecordingHandle:
    """记录 write_done/write_fail 的 TTY 替身句柄。"""

    def __init__(self):
        """初始化终态记录、线程身份与 TTY 流。"""
        self.terminal = []
        self.owner_thread_id = threading.get_ident()
        self.closed = False
        self.stream = _FakeTty()

    def text(self, message):
        """忽略文案更新。"""

    def done(self, message):
        """忽略定格收尾。"""

    def fail(self, message):
        """忽略定格收尾。"""

    def write(self, message):
        """忽略插入打印。"""

    def write_done(self, message):
        """记录一条成功终态。"""
        self.terminal.append(('done', message))

    def write_fail(self, message):
        """记录一条失败终态。"""
        self.terminal.append(('fail', message))


class _PhaseStub:
    """spinner_phase 替身上下文，产出给定句柄。"""

    def __init__(self, handle):
        """保存产出句柄。"""
        self.handle = handle

    def __enter__(self):
        """返回产出句柄。"""
        return self.handle

    def __exit__(self, *exc_info):
        """不抑制异常。"""
        return False


def _make_cancelled_send(exc):
    """构造在 on_external 上抛出给定异常的 send_notification 替身。

    Args:
        exc (BaseException): on_external 通知需要抛出的原始异常对象。

    Returns:
        MagicMock: 带 call_count 的替身。
    """

    def side_effect(config, template_key, **kwargs):
        """on_external 抛异常，其余模板返回空结果。"""
        if template_key == 'on_external':
            raise exc
        return []

    return MagicMock(side_effect=side_effect)


def _cancelled_handle_process_end(handle):
    """经 _handle_process_end 的 on_external 入口触发取消。"""
    config = _config_with_external('on_end', r'task:\Custom\MyTask')
    with patch('modules.monitor.run_external_action',
               return_value=_external_result()):
        monitor._handle_process_end(
            config, 'demo.exe', 123, 10, r'task:\Custom\MyTask', handle)


def _cancelled_check_process_timeout(handle):
    """经 _check_process_timeout 的 on_external 入口触发取消。"""
    config = _config_with_external('on_timeout', r'task:\Custom\MyTask')
    process_info = {
        'name': 'demo.exe', 'start_time': 0,
        'last_warning_time': 0, 'timeout_count': 0,
    }
    with patch('modules.monitor.run_external_action',
               return_value=_external_result()):
        monitor._check_process_timeout(
            config, process_info, 123, 10, 1,
            r'task:\Custom\MyTask', 1, spinner=handle)


def _cancelled_launch_task(handle):
    """经 _launch_task 的 on_wait_timeout/on_external 入口触发取消。"""
    config = _config_with_external('on_wait_timeout', r'task:\Custom\MyTask')
    with patch('modules.monitor.subprocess.run',
               return_value=subprocess.CompletedProcess(args=[], returncode=0)), \
            patch('modules.monitor.query_task_pid',
                  return_value={'state': 'not_found'}), \
            patch('modules.monitor.run_external_action',
                  return_value=_external_result()), \
            patch('modules.monitor.spinner_phase',
                  side_effect=lambda *a, **k: _PhaseStub(handle)), \
            patch('modules.monitor.notify_fail'), \
            patch('modules.monitor.time.sleep'):
        monitor._launch_task(config['monitor']['launch'], config)


def _cancelled_monitor_processes(handle):
    """经 monitor_processes 的 on_wait_timeout/on_external 入口触发取消。"""
    config = _config_with_external('on_wait_timeout', r'task:\Custom\MyTask')
    with patch('modules.monitor._collect_matching_processes', return_value={}), \
            patch('modules.monitor.run_external_action',
                  return_value=_external_result()), \
            patch('modules.monitor.spinner_phase',
                  side_effect=lambda *a, **k: _PhaseStub(handle)), \
            patch('modules.monitor.notify_fail'), \
            patch('modules.monitor.time.sleep'):
        monitor.monitor_processes(config)


def _cancelled_task_scheduler(handle):
    """经 monitor_via_task_scheduler 的 on_external 入口触发取消。"""
    config = _config_with_external('on_wait_timeout', r'task:\Custom\MyTask')
    with patch('modules.monitor.query_task_pid',
               return_value={'state': 'not_found'}), \
            patch('modules.monitor.run_external_action',
                  return_value=_external_result()), \
            patch('modules.monitor.spinner_phase',
                  side_effect=lambda *a, **k: _PhaseStub(handle)), \
            patch('modules.monitor.notify_fail'), \
            patch('modules.monitor.time.sleep'):
        monitor.monitor_via_task_scheduler(config)


_CANCELLED_SITES = [
    ('handle_process_end', _cancelled_handle_process_end),
    ('check_process_timeout', _cancelled_check_process_timeout),
    ('launch_task', _cancelled_launch_task),
    ('monitor_processes', _cancelled_monitor_processes),
    ('task_scheduler', _cancelled_task_scheduler),
]


@pytest.mark.parametrize(
    'site_id,driver', _CANCELLED_SITES,
    ids=[site[0] for site in _CANCELLED_SITES])
def test_external_notification_cancelled_error_propagates(site_id, driver):
    """on_external 通知取消时原异常优先重抛，不记为外部动作失败后继续。"""
    handle = _RecordingHandle()
    exc = CancelledError('外部通知取消')
    send = _make_cancelled_send(exc)

    with patch.object(monitor, 'send_notification', send):
        with pytest.raises(CancelledError) as excinfo:
            driver(handle)

    assert excinfo.value is exc
    assert send.call_count == 2
    assert ('fail', '外部动作执行失败') not in handle.terminal
