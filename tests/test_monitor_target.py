#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 monitor.target 的纯函数分类与规范化行为。

覆盖范围：
- 盘符绝对路径、UNC 路径、显式相对路径、裸名称、完整任务路径的分类
- watch/launch 的文件扩展名限制
- 相对目标与 cwd 基于配置目录解析
- 非法输入与设备路径的拒绝
"""

import ntpath
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.monitor_target import MonitorTarget, resolve_monitor_target

CONFIG_DIR = r'C:\cfg'


def test_task_root_path_classified_as_task():
    """根目录任务路径应归类为任务目标。"""
    result = resolve_monitor_target('watch', r'\Task', CONFIG_DIR)
    assert result.kind == 'task'
    assert result.value == r'\Task'


def test_launch_relative_file_resolved_against_config_dir():
    """显式相对文件目标应基于配置目录解析为绝对路径。"""
    result = resolve_monitor_target('launch', r'.\a.exe', CONFIG_DIR)
    assert result.kind == 'file'
    assert result.value == r'C:\cfg\a.exe'
    assert result.cwd == r'C:\cfg'


def test_launch_relative_cwd_resolved_against_config_dir():
    """显式相对 cwd 应基于配置目录解析。"""
    result = resolve_monitor_target('launch', r'.\a.exe', CONFIG_DIR, r'.\work')
    assert result.cwd == r'C:\cfg\work'


def test_watch_bare_script_name_is_process():
    """watch 下裸名称表示进程名，而不是脚本路径。"""
    result = resolve_monitor_target('watch', 'worker.py', CONFIG_DIR)
    assert result.kind == 'process'
    assert result.value == 'worker.py'
    assert result.cwd is None


@pytest.mark.parametrize(
    'mode,target,expected_value',
    [
        ('watch', r'C:\Apps\A.EXE', r'C:\Apps\A.EXE'),
        ('watch', 'C:/Apps/A.exe', r'C:\Apps\A.exe'),
        ('watch', r'\\server\share\a.exe', r'\\server\share\a.exe'),
        ('watch', r'.\tool.exe', r'C:\cfg\tool.exe'),
        ('launch', r'../a.exe', r'C:\a.exe'),
        ('launch', r'..\run.cmd', r'C:\run.cmd'),
        ('launch', r'.\b.bat', r'C:\cfg\b.bat'),
    ],
)
def test_file_targets_are_normalized(mode, target, expected_value):
    """文件目标应归一化分隔符并解析为绝对路径。"""
    result = resolve_monitor_target(mode, target, CONFIG_DIR)
    assert result.kind == 'file'
    assert result.value == expected_value


@pytest.mark.parametrize(
    'target',
    [r'\Folder\Task', r'\Folder\Sub\Task'],
)
def test_nested_task_path_is_preserved(target):
    """多级任务路径应保持原样。"""
    result = resolve_monitor_target('watch', target, CONFIG_DIR)
    assert result.kind == 'task'
    assert result.value == target
    assert result.cwd is None


@pytest.mark.parametrize(
    'target,expected_value',
    [
        (r'C:\Apps\a.bat', r'C:\Apps\a.bat'),
        (r'C:\Apps\a.cmd', r'C:\Apps\a.cmd'),
        (r'C:\Apps\a.ps1', r'C:\Apps\a.ps1'),
        (r'C:\Apps\a.py', r'C:\Apps\a.py'),
    ],
)
def test_launch_accepts_supported_script_extensions(target, expected_value):
    """launch 允许 exe/bat/cmd/ps1/py 脚本。"""
    result = resolve_monitor_target('launch', target, CONFIG_DIR)
    assert result.kind == 'file'
    assert result.value == expected_value


def test_watch_rejects_batch_file():
    """watch 的文件目标只允许 exe。"""
    with pytest.raises(ValueError):
        resolve_monitor_target('watch', r'C:\Apps\a.bat', CONFIG_DIR)


def test_launch_cwd_defaults_to_target_directory():
    """cwd 缺省时使用目标文件所在目录。"""
    result = resolve_monitor_target('launch', r'C:\Apps\deep\a.exe', CONFIG_DIR)
    assert result.cwd == r'C:\Apps\deep'


@pytest.mark.parametrize(
    'cwd,expected_value',
    [
        (r'C:\work', r'C:\work'),
        (r'\\server\share\work', r'\\server\share\work'),
        (r'..\work', r'C:\work'),
    ],
)
def test_launch_absolute_cwd_is_normalized(cwd, expected_value):
    """launch 接受盘符绝对目录与 UNC 目录作为 cwd。"""
    result = resolve_monitor_target('launch', r'C:\Apps\a.exe', CONFIG_DIR, cwd)
    assert result.cwd == expected_value


def test_watch_file_target_ignores_cwd():
    """watch 文件目标的 cwd 恒为 None，传入值被忽略。"""
    result = resolve_monitor_target('watch', r'C:\Apps\a.exe', CONFIG_DIR, r'dir\work')
    assert result.cwd is None


def test_launch_task_target_ignores_cwd():
    """任务目标不参与 cwd 生效，即使模式为 launch。"""
    result = resolve_monitor_target('launch', r'\Folder\Task', CONFIG_DIR, r'dir\work')
    assert result.kind == 'task'
    assert result.cwd is None


def test_watch_process_target_ignores_cwd():
    """进程目标不参与 cwd 生效。"""
    result = resolve_monitor_target('watch', 'worker.py', CONFIG_DIR, r'dir\work')
    assert result.kind == 'process'
    assert result.cwd is None


@pytest.mark.parametrize(
    'mode,target',
    [
        ('launch', 'a.exe'),
        ('launch', 'worker.py'),
        ('watch', r'C:a.exe'),
        ('launch', r'C:a.exe'),
        ('watch', r'dir/a.exe'),
        ('launch', r'dir\a.exe'),
        ('watch', '\\'),
        ('watch', r'.\worker.py'),
        ('watch', r'C:\Apps\a.txt'),
        ('launch', r'C:\Apps\a.txt'),
        ('launch', r'\\?\C:\a.exe'),
        ('launch', r'\\.\C:\a.exe'),
        ('watch', None),
        ('watch', ''),
        ('watch', '   '),
        ('psutil', 'a.exe'),
        ('Watch', 'a.exe'),
    ],
)
def test_invalid_targets_raise_value_error(mode, target):
    """非法 mode/target 组合必须抛出 ValueError。"""
    with pytest.raises(ValueError):
        resolve_monitor_target(mode, target, CONFIG_DIR)


@pytest.mark.parametrize(
    'cwd',
    [r'dir\work', r'C:work', '\\', r'\\?\C:\work'],
)
def test_invalid_launch_cwd_raises_value_error(cwd):
    """非法 cwd 必须抛出 ValueError。"""
    with pytest.raises(ValueError):
        resolve_monitor_target('launch', r'C:\Apps\a.exe', CONFIG_DIR, cwd)


@pytest.mark.parametrize(
    'config_dir',
    ['cfg', r'.\cfg', r'C:cfg', ''],
)
def test_relative_config_dir_raises_value_error(config_dir):
    """config_dir 必须是绝对目录。"""
    with pytest.raises(ValueError):
        resolve_monitor_target('watch', 'worker.py', config_dir)


def test_relative_resolution_ignores_current_working_directory(monkeypatch, tmp_path):
    """解析结果不依赖进程当前工作目录。"""
    monkeypatch.chdir(tmp_path)
    result = resolve_monitor_target('launch', r'.\a.exe', CONFIG_DIR, r'.\work')
    assert result.value == r'C:\cfg\a.exe'
    assert result.cwd == r'C:\cfg\work'


def test_missing_target_file_still_classified(tmp_path):
    """目标文件不存在时仍按语法完成分类。"""
    target = str(tmp_path / 'missing' / 'a.exe')
    config_dir = str(tmp_path)
    assert not os.path.exists(target)

    result = resolve_monitor_target('watch', target, config_dir)

    assert result.kind == 'file'
    assert result.value == ntpath.normpath(target)


def test_result_is_frozen_dataclass():
    """解析结果应为不可变数据类。"""
    result = resolve_monitor_target('watch', 'worker.py', CONFIG_DIR)
    assert isinstance(result, MonitorTarget)
    with pytest.raises(Exception):
        result.kind = 'file'
