#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证程序入口的初始化顺序、启动清理与截图分流副作用边界。"""

from argparse import Namespace
from importlib import import_module
import logging
from unittest.mock import Mock

import pytest


class FakeSpinner:
    """记录初始化完成提示的最小 spinner 替身。"""

    def __init__(self, events):
        """保存共享事件表。"""
        self.events = events

    def __enter__(self):
        """进入初始化阶段。"""
        return self

    def __exit__(self, *exc_info):
        """不吞掉任何异常。"""
        return False

    def done(self, message):
        """记录初始化完成事件。"""
        self.events.append('initialized')

    def write_done(self, message):
        """兼容缺失配置文件分支的提示接口。"""


def entry_module():
    """导入程序入口模块。"""
    return import_module('2RPM')


def _install_entry(monkeypatch, tmp_path, config=None):
    """替换入口全部副作用，返回入口模块与按序记录的事件表。"""
    entry = entry_module()
    events = []

    monkeypatch.setattr(entry, 'print_info', Mock())
    monkeypatch.setattr(entry, 'setup_default_logging', Mock())
    monkeypatch.setattr(
        entry, 'setup_logging',
        Mock(side_effect=lambda *args, **kwargs: events.append('setup_logging')))
    monkeypatch.setattr(entry, 'monitor_processes', Mock(
        side_effect=lambda *args, **kwargs: events.append('monitor')))
    monkeypatch.setattr(
        entry, 'spinner_phase', Mock(side_effect=lambda *a, **k: FakeSpinner(events)))
    monkeypatch.setattr(
        entry, 'get_program_directory', Mock(return_value=str(tmp_path)))
    monkeypatch.setattr(
        entry, 'load_config',
        Mock(side_effect=lambda *args, **kwargs: (
            events.append('load')
            or (config if config is not None else {
                'push': {'screenshot': {
                    'retention': {'enabled': True, 'max_days': 14}}},
            }))))
    monkeypatch.setattr(
        entry, 'cleanup_retention',
        Mock(side_effect=lambda *args, **kwargs: (
            events.append('cleanup') or ())))
    return entry, events


def _install_write_guards(monkeypatch):
    """阻止任何配置写回入口，用于验证入口不存在二次写回。"""
    config = import_module('modules.config')

    def forbidden(*args, **kwargs):
        """任何写回尝试都视为违反入口契约。"""
        pytest.fail('启动流程不得再次写回配置')

    monkeypatch.setattr(config.os, 'replace', forbidden)
    monkeypatch.setattr(config.tempfile, 'mkstemp', forbidden)


def test_main_initialization_sequence(monkeypatch, tmp_path):
    """常规启动按 load→setup_logging→cleanup→完成→monitor 顺序执行。"""
    entry, events = _install_entry(monkeypatch, tmp_path)
    monkeypatch.setattr('sys.argv', ['2RPM.py'])

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert exit_info.value.code == 0
    assert events == [
        'load', 'setup_logging', 'cleanup', 'initialized', 'monitor']


def test_runtime_context_is_attached_before_logging(monkeypatch, tmp_path):
    """加载后为 CONFIG 附加只含两个字段的运行上下文。"""
    entry, events = _install_entry(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(
        entry, 'setup_logging',
        Mock(side_effect=lambda config, *args, **kwargs: (
            events.append('setup_logging')
            or seen.update(runtime=config.get('_runtime')))))
    monkeypatch.setattr('sys.argv', ['2RPM.py'])

    with pytest.raises(SystemExit):
        entry.main()

    assert seen['runtime'] == {
        'program_dir': str(tmp_path),
        'config_stem': 'config',
    }


def test_cleanup_failure_is_safe_and_monitor_continues(
        monkeypatch, tmp_path, caplog):
    """清理普通失败被安全记录后仍继续监视并正常退出。"""
    entry, events = _install_entry(monkeypatch, tmp_path)
    monkeypatch.setattr(
        entry, 'cleanup_retention',
        Mock(side_effect=lambda *args, **kwargs: (
            events.append('cleanup') or (_ for _ in ()).throw(OSError('boom')))))
    monkeypatch.setattr('sys.argv', ['2RPM.py'])

    with caplog.at_level(logging.WARNING, logger='2RPM'):
        with pytest.raises(SystemExit) as exit_info:
            entry.main()

    assert exit_info.value.code == 0
    assert events == [
        'load', 'setup_logging', 'cleanup', 'initialized', 'monitor']
    assert '截图清理失败：已跳过本次清理' in caplog.text


def test_main_never_writes_config_back(monkeypatch, tmp_path):
    """启动流程不在加载之外再次整体写回配置。"""
    entry, _ = _install_entry(monkeypatch, tmp_path)
    _install_write_guards(monkeypatch)
    monkeypatch.setattr('sys.argv', ['2RPM.py'])

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert exit_info.value.code == 0


@pytest.mark.parametrize('signal', [KeyboardInterrupt, SystemExit])
def test_cleanup_control_signal_propagates(monkeypatch, tmp_path, signal):
    """清理阶段的控制信号以原对象传播，不被入口吞掉。"""
    entry, events = _install_entry(monkeypatch, tmp_path)
    instance = signal()
    monkeypatch.setattr(
        entry, 'cleanup_retention',
        Mock(side_effect=lambda *args, **kwargs: (
            events.append('cleanup') or (_ for _ in ()).throw(instance))))
    monkeypatch.setattr('sys.argv', ['2RPM.py'])

    with pytest.raises(signal) as caught:
        entry.main()

    assert caught.value is instance
    assert 'initialized' not in events
    assert 'monitor' not in events


def test_screenshot_command_skips_config_logging_and_cleanup(
        monkeypatch, tmp_path):
    """截图分流不加载配置、不初始化日志、不执行启动清理或监视。"""
    entry, events = _install_entry(monkeypatch, tmp_path)
    parse = Mock(return_value=Namespace(source='adb', target='serial'))
    run = Mock(return_value=0)
    monkeypatch.setattr(entry, 'parse_screenshot_args', parse)
    monkeypatch.setattr(entry, 'run_screenshot_cli', run)
    monkeypatch.setattr('sys.argv', ['2RPM.py', 'screenshot', '--source', 'adb:serial'])

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert exit_info.value.code == 0
    parse.assert_called_once_with(['--source', 'adb:serial'])
    run.assert_called_once_with(parse.return_value, str(tmp_path))
    assert events == []
    entry.load_config.assert_not_called()
    entry.setup_logging.assert_not_called()
    entry.cleanup_retention.assert_not_called()
    entry.monitor_processes.assert_not_called()
