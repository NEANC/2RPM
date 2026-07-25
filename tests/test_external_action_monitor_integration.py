#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""monitor.py external action 调用点回归测试。"""

import os
import subprocess
import sys

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
    return {'external': {key: value}}


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
        'monitor': {'process_name': 'missing.exe'},
        'wait': {'max_wait': '0s', 'check_interval': '1s'},
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
        'monitor': {'timeout_interval': '1s', 'loop_interval': '1s'},
        'task': {'task_name': r'\Custom\MainTask', 'lookback_minutes': '10'},
        'wait': {'max_wait': '0s', 'check_interval': '1s'},
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


def test_launch_task_wait_timeout_uses_external_action_metadata():
    """launch task 等待进程启动超时时应调用统一入口并发送 on_external。"""
    config = {
        'wait': {'max_wait': '0s', 'check_interval': '1s'},
        'external': {'on_wait_timeout': r'task:\Custom\MyTask'},
    }
    launch_section = {'task_name': r'\Custom\MainTask'}
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
