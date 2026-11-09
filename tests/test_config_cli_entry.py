#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""验证监控入口的命令行配置选择契约。

测试只替换入口模块的 console、日志、spinner 与配置加载边界，
配置加载以 SystemExit 终止 main，不执行真实配置读取、监视或清理。
"""

import os
import sys
from importlib import import_module
from unittest.mock import MagicMock

import pytest

# 将项目根目录加入模块搜索路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 测试用程序目录，避开真实安装路径
PROGRAM_DIR = r'C:\Program Files\2RPM'


def entry_module():
    """导入程序入口模块，用于验证参数与路径选择。"""
    return import_module('2RPM')


def run_main(monkeypatch, argv):
    """隔离外部边界运行 main，返回配置加载的调用记录。

    Args:
        monkeypatch (pytest.MonkeyPatch): pytest 补丁夹具。
        argv (list[str]): 除程序名外的命令行参数。

    Returns:
        MagicMock: 被替换的 load_config，调用记录中含实际配置路径。
    """
    entry = entry_module()
    loader = MagicMock(side_effect=SystemExit)
    monkeypatch.setattr(sys, 'argv', ['2RPM.py'] + list(argv))
    monkeypatch.setattr(entry, 'print_info', MagicMock())
    monkeypatch.setattr(entry, 'setup_default_logging', MagicMock())
    monkeypatch.setattr(entry, 'spinner_phase', MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(entry, 'get_program_directory', lambda: PROGRAM_DIR)
    monkeypatch.setattr(entry, 'load_config', loader)
    with pytest.raises(SystemExit):
        entry.main()
    return loader


def assert_loaded_once(loader, expected_path, is_user_specified):
    """断言只加载一次指定路径的配置，并携带显式指定标记。"""
    loader.assert_called_once()
    args, kwargs = loader.call_args
    assert args[0] == expected_path
    assert kwargs['is_user_specified'] is is_user_specified


def test_short_option_appends_yaml_suffix_under_program_dir(monkeypatch):
    """-c baah 应解析为程序目录下的 baah.yaml。"""
    loader = run_main(monkeypatch, ['-c', 'baah'])

    assert_loaded_once(loader, os.path.join(PROGRAM_DIR, 'baah.yaml'), True)


def test_long_option_keeps_absolute_path_with_spaces(monkeypatch):
    """--config 的含空格绝对路径应原样使用，不被拆词或改后缀。"""
    config_path = r'D:\监控配置\工作配置.yml'
    loader = run_main(monkeypatch, ['--config', config_path])

    assert_loaded_once(loader, config_path, True)


def test_positional_path_wins_over_option_without_merging(monkeypatch):
    """位置参数优先于 -c，且两者同时给出时只加载位置参数。"""
    positional = r'E:\监控配置\优先.yaml'
    loader = run_main(monkeypatch, ['-c', 'ignored', positional])

    assert_loaded_once(loader, positional, True)


def test_positional_path_keeps_absolute_path_with_spaces(monkeypatch):
    """位置参数含空格的绝对路径应原样使用，不被拆词或改后缀。"""
    config_path = r'D:\监控配置\工作配置.yml'
    loader = run_main(monkeypatch, [config_path])

    assert_loaded_once(loader, config_path, True)


def test_no_arguments_uses_default_config_not_user_specified(monkeypatch):
    """无参数时应加载程序目录下的默认 config.yaml。"""
    loader = run_main(monkeypatch, [])

    assert_loaded_once(loader, os.path.join(PROGRAM_DIR, 'config.yaml'), False)


@pytest.mark.parametrize(('raw', 'expected'), [
    ('work', 'work.yaml'),
    ('work.yml', 'work.yml'),
    ('work.json', 'work.json.yaml'),
    ('work.YAML', 'work.YAML.yaml'),
])
def test_suffix_rule_appends_unless_lowercase_yaml_or_yml(monkeypatch, raw, expected):
    """仅小写 .yaml/.yml 保留原后缀，其余直接追加 .yaml。"""
    loader = run_main(monkeypatch, ['-c', raw])

    assert_loaded_once(loader, os.path.join(PROGRAM_DIR, expected), True)


def test_relative_path_ignores_current_working_directory(monkeypatch, tmp_path):
    """相对路径始终基于程序目录，不随终端当前工作目录变化。"""
    monkeypatch.chdir(tmp_path)
    loader = run_main(monkeypatch, ['-c', 'relative'])

    assert_loaded_once(loader, os.path.join(PROGRAM_DIR, 'relative.yaml'), True)


def test_config_path_option_is_unknown_and_exits_with_code_two(monkeypatch):
    """--config-path 不是有效选项，argparse 应以退出码 2 拒绝。"""
    entry = entry_module()
    monkeypatch.setattr(sys, 'argv', ['2RPM.py', '--config-path', 'x'])
    monkeypatch.setattr(entry, 'print_info', MagicMock())
    monkeypatch.setattr(entry, 'setup_default_logging', MagicMock())
    monkeypatch.setattr(entry, 'spinner_phase', MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(entry, 'get_program_directory', lambda: PROGRAM_DIR)
    # 若 argparse 回归为接受该选项，则在此以 SystemExit(None) 终止，断言会暴露退出码不符
    monkeypatch.setattr(entry, 'load_config', MagicMock(side_effect=SystemExit))

    with pytest.raises(SystemExit) as excinfo:
        entry.main()

    assert excinfo.value.code == 2


def test_relative_path_basis_is_script_directory_not_cwd(monkeypatch, tmp_path):
    """使用真实 get_program_directory 断言相对路径基于脚本目录。"""
    entry = entry_module()
    script_dir = tmp_path / 'script-home'
    script_dir.mkdir()
    other_cwd = tmp_path / 'other-cwd'
    other_cwd.mkdir()
    script = script_dir / '2RPM.py'
    loader = MagicMock(side_effect=SystemExit)

    monkeypatch.setattr(sys, 'argv', [str(script), '-c', 'relative'])
    monkeypatch.setattr(entry, 'print_info', MagicMock())
    monkeypatch.setattr(entry, 'setup_default_logging', MagicMock())
    monkeypatch.setattr(entry, 'spinner_phase', MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(entry, 'load_config', loader)
    monkeypatch.chdir(other_cwd)

    with pytest.raises(SystemExit):
        entry.main()

    loader.assert_called_once()
    loaded_path = os.path.normcase(loader.call_args.args[0])
    expected_path = os.path.normcase(str(script_dir / 'relative.yaml'))
    assert loaded_path == expected_path
    assert os.path.normcase(str(other_cwd)) not in loaded_path


def test_repeated_option_uses_last_value(monkeypatch):
    """重复给出 -c 时按 argparse 默认行为取最后一次的值。"""
    loader = run_main(monkeypatch, ['-c', 'first', '-c', 'second'])

    assert_loaded_once(loader, os.path.join(PROGRAM_DIR, 'second.yaml'), True)


@pytest.mark.parametrize('option', ['-C', '-config', '-Config', '--Config'])
def test_option_aliases_are_accepted(monkeypatch, option):
    """-C、-config、-Config、--Config 应与 --config 等价。"""
    loader = run_main(monkeypatch, [option, 'alias'])

    assert_loaded_once(loader, os.path.join(PROGRAM_DIR, 'alias.yaml'), True)


def test_bare_screenshot_argument_is_still_a_monitor_config(monkeypatch):
    """裸 screenshot 位置参数仍按监控配置加载，不进入截图分流。"""
    loader = run_main(monkeypatch, ['screenshot'])

    assert_loaded_once(loader, os.path.join(PROGRAM_DIR, 'screenshot.yaml'), True)
