#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 monitor.watch.lookback_minutes 的解析行为与查询边界。

执行本文件可验证：
- 负值 lookback_minutes 自动转为正值并写回配置
- 非法值回退到默认值并写回配置
- 合法字符串按分钟解析正常生效
- 等待绑定时把配置值传给事件查询，查询端把非法值规范为至少 1 毫秒窗口
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ruamel.yaml import YAML

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import modules.monitor as monitor
from modules.config import load_config, DEFAULT_VALUES
from modules import task_monitor
from modules.monitor_target import MonitorTarget

TASK_TARGET = MonitorTarget(kind='task', value=r'\Task', cwd=None)


class TestTaskLookbackConfig(unittest.TestCase):
    """测试 monitor.watch.lookback_minutes 的归一化与回写。"""

    def _write_config(self, content):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
            f.write(content)
            return f.name

    def test_negative_lookback_is_normalized_and_written_back(self):
        """负值应被修正为正值并写回配置文件。"""
        config_file = self._write_config(
            "monitor:\n"
            "  target: task_a\n"
            "  watch:\n"
            "    lookback_minutes: -5\n"
        )
        try:
            config = load_config(config_file)
            self.assertEqual(config['monitor']['watch']['lookback_minutes'], 5)

            with open(config_file, 'r', encoding='utf-8') as f:
                persisted = YAML().load(f)

            self.assertEqual(
                persisted['monitor']['watch']['lookback_minutes'],
                5)
        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)

    def test_invalid_lookback_defaults_and_written_back(self):
        """非法值应回退默认值，并回写回配置。"""
        config_file = self._write_config(
            "monitor:\n"
            "  target: task_a\n"
            "  watch:\n"
            "    lookback_minutes: abc\n"
        )
        try:
            config = load_config(config_file)
            self.assertEqual(
                config['monitor']['watch']['lookback_minutes'],
                DEFAULT_VALUES['monitor']['watch']['lookback_minutes'])

            with open(config_file, 'r', encoding='utf-8') as f:
                persisted = YAML().load(f)

            self.assertEqual(
                persisted['monitor']['watch']['lookback_minutes'],
                DEFAULT_VALUES['monitor']['watch']['lookback_minutes'])
        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)

    def test_lookback_string_and_zero_values_are_persisted(self):
        """字符串保留类型、浮点按绝对值截断，零值不回退默认值。"""
        for raw, expected in (
                ("'10'", '10'), ("'-5'", '5'), ('0', 0),
                ('10.0', 10), ('-3.7', 3)):
            with self.subTest(value=raw):
                config_file = self._write_config(
                    "monitor:\n"
                    "  mode: watch\n"
                    "  target: task_a\n"
                    "  watch:\n"
                    f"    lookback_minutes: {raw}\n"
                )
                try:
                    config = load_config(config_file)
                    with open(config_file, 'r', encoding='utf-8') as stream:
                        persisted = YAML().load(stream)
                    for result in (config, persisted):
                        value = result['monitor']['watch']['lookback_minutes']
                        self.assertEqual(value, expected)
                        self.assertIsInstance(value, type(expected))
                finally:
                    os.unlink(config_file)

    def test_non_finite_lookback_falls_back_to_default(self):
        """NaN 与无穷大不抛异常，按非法值回退默认 10。"""
        for raw in ('.nan', '.inf'):
            with self.subTest(value=raw):
                config_file = self._write_config(
                    "monitor:\n"
                    "  mode: watch\n"
                    "  target: task_a\n"
                    "  watch:\n"
                    f"    lookback_minutes: {raw}\n"
                )
                try:
                    config = load_config(config_file)
                    with open(config_file, 'r', encoding='utf-8') as stream:
                        persisted = YAML().load(stream)
                    for result in (config, persisted):
                        value = result['monitor']['watch']['lookback_minutes']
                        self.assertEqual(value, 10)
                        self.assertIsInstance(value, int)
                finally:
                    os.unlink(config_file)


class TestTaskMonitorLookbackQuery(unittest.TestCase):
    """在可控环境验证规范化后的 lookback_minutes 被传入事件查询。"""

    def test_query_called_with_normalized_lookback_minutes(self):
        """确保等待绑定使用配置中的 lookback 值查询事件。"""
        calls = {}

        def fake_query_task_events(task_path, lookback_minutes):
            calls['task_path'] = task_path
            calls['lookback_minutes'] = lookback_minutes
            return []

        cfg = {
            "monitor": {
                "common": {"max_wait": "1s", "check_interval": "1s"},
                "watch": {"lookback_minutes": 5},
            },
        }
        spinner = MagicMock()
        spinner.__enter__.return_value = spinner
        spinner.__exit__.return_value = False

        with patch('modules.monitor.query_task_events',
                   fake_query_task_events), \
                patch('modules.monitor.time.time',
                      side_effect=[0.0, 0.5, 2.0]), \
                patch('modules.monitor.time.sleep'), \
                patch('modules.monitor.spinner_phase', return_value=spinner):
            result = monitor._wait_task_process(cfg, TASK_TARGET)

        self.assertIsNone(result)
        self.assertEqual(calls.get('task_path'), r'\Task')
        self.assertEqual(calls.get('lookback_minutes'), 5)

    def test_query_normalizes_string_negative_invalid_and_zero(self):
        """查询入口保持字符串、负数、非法回退和零值的既有语义。"""
        for raw, expected_minutes in (
                ('10', 10), (-5, 5), ('abc', 10), (0, 0)):
            with self.subTest(value=raw):
                with patch.object(
                        task_monitor.win32evtlog, 'EvtQuery') as query_mock, \
                        patch.object(
                            task_monitor.win32evtlog, 'EvtNext',
                            return_value=[]):
                    task_monitor.query_task_events(r'\Task', raw)

                xpath = query_mock.call_args.args[2]
                expected_ms = max(1, expected_minutes * 60 * 1000)
                self.assertIn(
                    f'timediff(@SystemTime) <= {expected_ms}', xpath)

    def test_zero_lookback_uses_one_millisecond_query_window(self):
        """lookback=0 时查询 XPath 仍使用至少 1 毫秒窗口。"""
        with patch.object(task_monitor.win32evtlog, 'EvtQuery') as query_mock, \
                patch.object(task_monitor.win32evtlog, 'EvtNext',
                             return_value=[]):
            task_monitor.query_task_events(r'\Task', 0)

        xpath = query_mock.call_args.args[2]
        self.assertIn('timediff(@SystemTime) <= 1', xpath)


if __name__ == '__main__':
    unittest.main()
