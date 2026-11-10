#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一监视入口的配置加载与分发组合测试。

真实调用 load_config 的解析、拒绝与写回边界（配置只写入 tmp_path），
进入监视分发时全部下游函数替身化，不启动进程、不查询事件日志、不推送。
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from ruamel.yaml import YAML

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import monitor
from modules.config import load_config
from modules.monitor_target import MonitorTarget

# 合法的新监视目标样例
PROCESS_TARGET = 'notepad.exe'
EXE_PATH = r'C:\Apps\tool.exe'
TASK_PATH = r'\Custom\MyTask'


def _write_config(tmp_path, value):
    """把映射写入临时 YAML 配置并返回路径。

    Args:
        tmp_path (pathlib.Path): pytest 临时目录
        value: 待写入的 YAML 数据

    Returns:
        pathlib.Path: 配置文件路径
    """
    path = tmp_path / 'config.yaml'
    with path.open('w', encoding='utf-8') as stream:
        YAML().dump(value, stream)
    return path


def _write_raw(tmp_path, text):
    """把原始文本写入临时配置并返回路径。

    Args:
        tmp_path (pathlib.Path): pytest 临时目录
        text (str): 原始 YAML 文本

    Returns:
        pathlib.Path: 配置文件路径
    """
    path = tmp_path / 'config.yaml'
    path.write_text(text, encoding='utf-8')
    return path


def _assert_rejected_without_write(tmp_path, value, mkstemp_mock):
    """写入给定配置，断言拒绝加载且原文件字节不变。

    Args:
        tmp_path (pathlib.Path): pytest 临时目录
        value: 待写入的 YAML 数据
        mkstemp_mock (MagicMock): tempfile.mkstemp 替身
    """
    path = _write_config(tmp_path, value)
    original = path.read_bytes()
    with pytest.raises(SystemExit) as error:
        load_config(str(path))
    assert error.value.code == 1
    assert path.read_bytes() == original
    mkstemp_mock.assert_not_called()


LEGACY_CASES = [
    ('top_task', {'task': {}}),
    ('top_launch', {'launch': None}),
    ('top_wait', {'wait': {'max_wait': '1s'}}),
    ('monitor_mode', {'monitor': {'target': PROCESS_TARGET,
                                  'monitor_mode': 'psutil'}}),
    ('monitor_process_name', {'monitor': {'target': PROCESS_TARGET,
                                          'process_name': ''}}),
    ('monitor_timeout_interval', {'monitor': {'target': PROCESS_TARGET,
                                              'timeout_interval': None}}),
    ('monitor_loop_interval', {'monitor': {'target': PROCESS_TARGET,
                                           'loop_interval': '1s'}}),
    ('monitor_psutil', {'monitor': {'target': PROCESS_TARGET,
                                    'psutil': {'process_name': PROCESS_TARGET}}}),
    ('monitor_task_scheduler', {'monitor': {
        'target': PROCESS_TARGET,
        'task_scheduler': {'task_name': TASK_PATH}}}),
    ('launch_type', {'monitor': {'target': EXE_PATH, 'mode': 'launch',
                                 'launch': {'type': None}}}),
    ('launch_path', {'monitor': {'target': EXE_PATH, 'mode': 'launch',
                                 'launch': {'path': EXE_PATH}}}),
    ('launch_task_name', {'monitor': {'target': TASK_PATH, 'mode': 'launch',
                                      'launch': {'task_name': TASK_PATH}}}),
    ('external_timeout_threshold', {'monitor': {'target': PROCESS_TARGET},
                                    'external': {'timeout_threshold': 3}}),
]


@pytest.mark.parametrize(
    'value', [case[1] for case in LEGACY_CASES],
    ids=[case[0] for case in LEGACY_CASES])
def test_legacy_layout_is_rejected_without_write(tmp_path, value):
    """任一旧监视字段（含空值）存在时拒绝加载且不写回。"""
    with patch('modules.config.tempfile.mkstemp') as mkstemp_mock:
        _assert_rejected_without_write(tmp_path, value, mkstemp_mock)


def test_mixed_new_and_legacy_layout_is_rejected_without_write(tmp_path):
    """新旧字段混用时同样拒绝加载且不写回。"""
    value = {
        'monitor': {
            'mode': 'launch',
            'target': EXE_PATH,
            'launch': {'args': '--flag', 'cwd': None, 'type': 'program'},
            'watch': {'lookback_minutes': 5},
        },
    }
    with patch('modules.config.tempfile.mkstemp') as mkstemp_mock:
        _assert_rejected_without_write(tmp_path, value, mkstemp_mock)


@pytest.mark.parametrize(
    'target', [None, '', '   ', 123, {'path': EXE_PATH}],
    ids=['none', 'empty_str', 'blank_str', 'int', 'map'])
def test_missing_or_invalid_target_is_rejected_without_write(
        tmp_path, target):
    """target 缺失、空值或非字符串时拒绝加载且不写回。"""
    value = {'monitor': {'target': target}}
    with patch('modules.config.tempfile.mkstemp') as mkstemp_mock:
        _assert_rejected_without_write(tmp_path, value, mkstemp_mock)


def test_missing_target_key_is_rejected(tmp_path):
    """完全缺少 target 键时拒绝加载。"""
    with patch('modules.config.tempfile.mkstemp') as mkstemp_mock:
        _assert_rejected_without_write(tmp_path, {'monitor': {}}, mkstemp_mock)


@pytest.mark.parametrize(
    'text', ['', '   \n', '- item\n', 'plain text\n'],
    ids=['empty', 'comment_only', 'list_root', 'scalar_root'])
def test_empty_or_non_mapping_root_is_rejected(tmp_path, text):
    """空文件、仅注释与非映射根节点一律拒绝加载。"""
    path = _write_raw(tmp_path, text)
    original = path.read_bytes()
    with pytest.raises(SystemExit) as error:
        load_config(str(path))
    assert error.value.code == 1
    assert path.read_bytes() == original


def test_valid_layout_loads_and_keeps_target(tmp_path):
    """合法新结构加载成功并保留 target 原值。"""
    path = _write_config(tmp_path, {
        'monitor': {'mode': 'watch', 'target': 'MyApp.EXE'},
    })
    config = load_config(str(path))
    assert config['monitor']['target'] == 'MyApp.EXE'
    assert config['monitor']['mode'] == 'watch'


def _base_config(mode, target):
    """构造分发表单所需的最小配置。

    Args:
        mode (str): watch 或 launch
        target (str): 监视目标

    Returns:
        dict: 监视配置
    """
    return {
        'monitor': {
            'mode': mode,
            'target': target,
            'common': {
                'timeout_interval': '1s',
                'loop_interval': '1s',
                'timeout_threshold': 1,
                'max_wait': '0s',
                'check_interval': '1s',
            },
            'launch': {'args': None, 'cwd': None},
            'watch': {'lookback_minutes': 5},
        },
        'external': {
            'on_end': '', 'on_timeout': '', 'on_wait_timeout': '',
        },
    }


def _pid_info():
    """构造单 PID 监视所需的身份信息。"""
    return {
        'pid': 4321,
        'name': 'demo.exe',
        'create_time': 100.0,
        'start_time': 100.0,
        'last_warning_time': 100.0,
        'timeout_count': 0,
    }


def _spinner():
    """构造 spinner 上下文替身。"""
    handle = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = handle
    context.__exit__.return_value = False
    return context


def test_watch_bare_process_dispatches_to_target_collector(tmp_path):
    """watch + 裸进程名进入目标收集流程。"""
    config = _base_config('watch', PROCESS_TARGET)
    with patch('modules.monitor._collect_matching_processes_for_target',
               return_value={}) as collect_mock, \
            patch('modules.monitor.spinner_phase', return_value=_spinner()), \
            patch('modules.monitor.notify_fail'):
        with pytest.raises(SystemExit):
            monitor.monitor_processes(config, config_dir=str(tmp_path))

    target = collect_mock.call_args.args[0]
    assert isinstance(target, MonitorTarget)
    assert target.kind == 'process'
    assert target.value == PROCESS_TARGET


def test_watch_exe_path_dispatches_to_target_collector(tmp_path):
    """watch + EXE 路径进入目标收集流程。"""
    config = _base_config('watch', EXE_PATH)
    with patch('modules.monitor._collect_matching_processes_for_target',
               return_value={}) as collect_mock, \
            patch('modules.monitor.spinner_phase', return_value=_spinner()), \
            patch('modules.monitor.notify_fail'):
        with pytest.raises(SystemExit):
            monitor.monitor_processes(config, config_dir=str(tmp_path))

    target = collect_mock.call_args.args[0]
    assert target.kind == 'file'
    assert target.value == EXE_PATH


def test_watch_task_dispatches_to_watch_task_target(tmp_path):
    """watch + 任务路径进入计划任务等待绑定流程。"""
    config = _base_config('watch', TASK_PATH)
    with patch('modules.monitor._watch_task_target',
               return_value=_pid_info()) as watch_mock, \
            patch('modules.monitor._monitor_single_pid') as pid_mock, \
            patch('modules.monitor.spinner_phase', return_value=_spinner()):
        monitor.monitor_processes(config, config_dir=str(tmp_path))

    target = watch_mock.call_args.args[1]
    assert target.kind == 'task'
    assert target.value == TASK_PATH
    pid_mock.assert_called_once()


def test_launch_file_dispatches_to_launch_file(tmp_path):
    """launch + 文件目标调用文件启动并进入单 PID 监视。"""
    config = _base_config('launch', EXE_PATH)
    config['monitor']['launch']['args'] = '--flag'
    with patch('modules.monitor.launch_file',
               return_value=_pid_info()) as launch_mock, \
            patch('modules.monitor._monitor_single_pid') as pid_mock, \
            patch('modules.monitor.spinner_phase', return_value=_spinner()):
        monitor.monitor_processes(config, config_dir=str(tmp_path))

    target, args = launch_mock.call_args.args
    assert target.kind == 'file'
    assert target.value == EXE_PATH
    assert args == '--flag'
    pid_mock.assert_called_once()


def test_launch_task_dispatches_to_launch_task_target(tmp_path):
    """launch + 任务目标进入触发与边界绑定流程。"""
    config = _base_config('launch', TASK_PATH)
    with patch('modules.monitor._launch_task_target',
               return_value=_pid_info()) as launch_mock, \
            patch('modules.monitor._monitor_single_pid') as pid_mock, \
            patch('modules.monitor.spinner_phase', return_value=_spinner()):
        monitor.monitor_processes(config, config_dir=str(tmp_path))

    target = launch_mock.call_args.args[1]
    assert target.kind == 'task'
    assert target.value == TASK_PATH
    pid_mock.assert_called_once()


@pytest.mark.parametrize(
    'mode,target',
    [('psutil', PROCESS_TARGET), ('watch', ''), ('launch', 'worker.py'),
     ('launch', r'C:\Apps\tool.txt'), ('watch', r'C:a.exe')],
    ids=['invalid_mode', 'empty_target', 'launch_bare_name',
         'launch_bad_extension', 'drive_relative'])
def test_invalid_target_notifies_and_skips_downstream(tmp_path, mode, target):
    """非法 mode 或 target 只提示并退出，不执行任何下游。"""
    config = _base_config(mode, target)
    with patch('modules.monitor.notify_fail') as fail_mock, \
            patch('modules.monitor._collect_matching_processes_for_target') \
            as collect_mock, \
            patch('modules.monitor._watch_task_target') as watch_mock, \
            patch('modules.monitor._launch_task_target') as launch_mock, \
            patch('modules.monitor.launch_file') as file_mock, \
            patch('modules.monitor._monitor_single_pid') as pid_mock:
        with pytest.raises(SystemExit) as error:
            monitor.monitor_processes(config, config_dir=str(tmp_path))

    assert error.value.code == 1
    fail_mock.assert_called_once()
    collect_mock.assert_not_called()
    watch_mock.assert_not_called()
    launch_mock.assert_not_called()
    file_mock.assert_not_called()
    pid_mock.assert_not_called()
