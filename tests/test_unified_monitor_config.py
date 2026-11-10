#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证统一监视配置的纯校验与归一化函数。

覆盖范围：
- 旧监视布局字段按存在性逐项拒绝，含空值与新旧混用
- 新监视结构的形状与必填校验
- monitor.watch.lookback_minutes 归一化
- 校验与拒绝函数不修改入参
"""

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.config import (
    _normalize_watch_lookback_minutes,
    _reject_legacy_monitor_config,
    _validate_monitor_target_config,
)
from modules.monitor_target import resolve_monitor_target

# 旧布局中必须拒绝的键路径
LEGACY_PATHS = [
    ('task',),
    ('launch',),
    ('wait',),
    ('monitor', 'monitor_mode'),
    ('monitor', 'process_name'),
    ('monitor', 'timeout_interval'),
    ('monitor', 'loop_interval'),
    ('monitor', 'psutil'),
    ('monitor', 'task_scheduler'),
    ('monitor', 'launch', 'type'),
    ('monitor', 'launch', 'path'),
    ('monitor', 'launch', 'task_name'),
    ('external', 'timeout_threshold'),
]

# 旧布局下同样算“键存在”的空值
LEGACY_VALUES = (None, '', False, {})

# 归一化测试中表示“键缺失”的哨兵
MISSING = object()


def _nest(path, value):
    """把键路径与取值组装为嵌套映射。

    Args:
        path (tuple): 从顶层到叶子的键路径
        value: 叶子取值

    Returns:
        dict: 嵌套映射
    """
    node = value
    for key in reversed(path[1:]):
        node = {key: node}
    return {path[0]: node}


def _assert_exit_code_one(func, config):
    """断言函数以退出码 1 终止。

    Args:
        func: 待调用函数
        config: 传入函数的配置
    """
    with pytest.raises(SystemExit) as excinfo:
        func(config)
    assert excinfo.value.code == 1


@pytest.mark.parametrize(
    'value', LEGACY_VALUES, ids=['none', 'empty_str', 'false', 'empty_map'])
@pytest.mark.parametrize('path', LEGACY_PATHS, ids=lambda path: '.'.join(path))
def test_reject_legacy_key_by_existence(path, value):
    """旧布局字段按键存在判定拒绝，空值同样拒绝。"""
    _assert_exit_code_one(_reject_legacy_monitor_config, _nest(path, value))


def test_reject_mixed_new_and_legacy_config():
    """新旧字段混用时仍然拒绝。"""
    config = {
        'monitor': {
            'mode': 'watch',
            'target': 'notepad.exe',
            'psutil': {'process_name': 'notepad.exe'},
        },
    }
    _assert_exit_code_one(_reject_legacy_monitor_config, config)


@pytest.mark.parametrize(
    'config',
    [
        {'monitor': 'legacy text'},
        {'monitor': {'launch': 'C:\\Apps\\a.exe'}},
        {'external': 'legacy text'},
    ],
    ids=['monitor_str', 'monitor_launch_str', 'external_str'],
)
def test_reject_non_mapping_prefix(config):
    """旧布局前缀不是映射时按非法布局拒绝。"""
    _assert_exit_code_one(_reject_legacy_monitor_config, config)


def test_reject_keeps_input_unchanged():
    """拒绝函数不修改入参。"""
    config = {
        'monitor': {
            'target': 'notepad.exe',
            'psutil': {'process_name': 'notepad.exe'},
        },
        'task': {'task_name': '\\Task'},
    }
    snapshot = copy.deepcopy(config)
    _assert_exit_code_one(_reject_legacy_monitor_config, config)
    assert config == snapshot


def test_reject_accepts_new_layout():
    """仅含新布局字段时不终止。"""
    _reject_legacy_monitor_config({
        'monitor': {
            'mode': 'launch',
            'target': '.\\a.exe',
            'launch': {'args': '', 'cwd': None},
        },
        'external': {'action': 'notify'},
    })


@pytest.mark.parametrize(
    'config', [None, [], {}], ids=['none', 'list', 'empty_map'])
def test_validate_rejects_invalid_root(config):
    """根节点不是映射或缺少 monitor 时拒绝。"""
    _assert_exit_code_one(_validate_monitor_target_config, config)


def test_validate_rejects_non_mapping_monitor():
    """monitor 不是映射时拒绝。"""
    _assert_exit_code_one(
        _validate_monitor_target_config, {'monitor': 'watch'})


def test_validate_rejects_missing_target():
    """缺少 monitor.target 时拒绝。"""
    _assert_exit_code_one(
        _validate_monitor_target_config, {'monitor': {}})


@pytest.mark.parametrize(
    'target',
    [None, '', '   ', 123, {'a': 1}],
    ids=['none', 'empty_str', 'blank_str', 'int', 'map'],
)
def test_validate_rejects_invalid_target(target):
    """target 不是非空字符串时拒绝。"""
    _assert_exit_code_one(
        _validate_monitor_target_config, {'monitor': {'target': target}})


@pytest.mark.parametrize(
    'mode', ['psutil', 'WATCH', ''], ids=['psutil', 'upper', 'empty_str'])
def test_validate_rejects_invalid_mode(mode):
    """mode 不是 watch 或 launch 时拒绝。"""
    _assert_exit_code_one(
        _validate_monitor_target_config,
        {'monitor': {'mode': mode, 'target': 'notepad.exe'}},
    )


@pytest.mark.parametrize(
    'config',
    [
        {'monitor': {'target': 'notepad.exe', 'launch': 'C:\\a.exe'}},
        {'monitor': {'target': 'notepad.exe', 'launch': {'args': 123}}},
        {'monitor': {'target': 'notepad.exe', 'watch': '10'}},
        {'monitor': {'target': 'notepad.exe', 'common': []}},
    ],
    ids=['launch_str', 'args_int', 'watch_str', 'common_list'],
)
def test_validate_rejects_invalid_sections(config):
    """子节或 launch 字段类型不合法时拒绝。"""
    _assert_exit_code_one(_validate_monitor_target_config, config)


@pytest.mark.parametrize(
    'config',
    [
        {'monitor': {'target': 'notepad.exe'}},
        {'monitor': {'mode': 'launch', 'target': '.\\a.exe'}},
        {'monitor': {'mode': 'watch', 'target': 'notepad.exe'}},
        {
            'monitor': {
                'mode': 'launch',
                'target': '.\\a.exe',
                'launch': {'args': '', 'cwd': None},
            },
        },
        {
            'monitor': {
                'mode': 'watch',
                'target': 'notepad.exe',
                'watch': {'lookback_minutes': 10},
            },
        },
        {
            'monitor': {
                'mode': 'watch',
                'target': 'notepad.exe',
                'common': {'timeout_interval': '15m'},
                'launch': {'args': None},
            },
        },
    ],
    ids=['mode_missing', 'launch_mode', 'watch_mode', 'launch_args_empty',
         'watch_lookback', 'mixed_sections'],
)
def test_validate_accepts_valid_layout(config):
    """合法新结构不终止。"""
    _validate_monitor_target_config(config)


def test_validate_keeps_templates_and_external_action():
    """合法结构校验后模板内容与 external 动作字符串保持原样。"""
    config = {
        'monitor': {'mode': 'watch', 'target': 'notepad.exe'},
        'external': {'action': 'cmd /c echo "${host_name}"'},
        'push': {
            'templates': {
                'on_end': {
                    'title': '进程结束通报',
                    'content': '主机: {host_name}\n\n进程: {process_name}\n',
                },
            },
        },
    }
    snapshot = copy.deepcopy(config)
    _validate_monitor_target_config(config)
    assert config == snapshot


@pytest.mark.parametrize(
    'raw,expected_changed,expected_value',
    [
        (MISSING, True, 10),
        (None, True, 10),
        ('', True, 10),
        (' -5 ', True, '5'),
        ('-', True, 10),
        ('abc', True, 10),
        (True, True, 10),
        ([], True, 10),
        (10, False, 10),
        (0, False, 0),
        ('7', False, '7'),
    ],
    ids=['missing', 'none', 'empty_str', 'negative_str', 'dash_only',
         'alpha', 'true', 'list', 'int', 'zero', 'digit_str'],
)
def test_normalize_watch_lookback(raw, expected_changed, expected_value):
    """lookback_minutes 的返回值与写入结果符合约定。"""
    section = {} if raw is MISSING else {'lookback_minutes': raw}
    assert _normalize_watch_lookback_minutes(section) is expected_changed
    assert section['lookback_minutes'] == expected_value


def test_normalize_watch_lookback_leaves_other_keys():
    """归一化只改动 lookback_minutes。"""
    section = {'mode': 'watch', 'lookback_minutes': '-3', 'extra': [1]}
    assert _normalize_watch_lookback_minutes(section) is True
    assert section == {
        'mode': 'watch', 'lookback_minutes': '3', 'extra': [1]}


def test_same_relative_target_resolves_against_different_config_dirs():
    """同一相对目标在不同配置目录下解析为不同绝对路径。"""
    first = resolve_monitor_target('launch', '.\\a.exe', 'C:\\cfg_a')
    second = resolve_monitor_target('launch', '.\\a.exe', 'D:\\cfg_b')

    assert first.value == 'C:\\cfg_a\\a.exe'
    assert second.value == 'D:\\cfg_b\\a.exe'
    assert first.value != second.value
