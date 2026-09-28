#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 monitor.task_scheduler.lookback_minutes 的解析行为与配置回写。

执行本文件可验证：
- 负值 lookback_minutes 自动转为正值并写回配置
- 非法值回退到默认值并写回配置
- 合法字符串按分钟解析正常生效
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from ruamel.yaml import YAML

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import modules.monitor as monitor
from modules.config import load_config, DEFAULT_VALUES
from modules import task_monitor


class TestTaskLookbackConfig(unittest.TestCase):
    """测试 monitor.task_scheduler.lookback_minutes 的归一化与回写。"""

    def _write_config(self, content):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
            f.write(content)
            return f.name

    def test_negative_lookback_is_normalized_and_written_back(self):
        """负值应被修正为正值并写回配置文件。"""
        config_file = self._write_config(
            "monitor:\n"
            "  task_scheduler:\n"
            "    task_name: task_a\n"
            "    lookback_minutes: -5\n"
        )
        try:
            config = load_config(config_file)
            self.assertEqual(config['monitor']['task_scheduler']['lookback_minutes'], 5)

            with open(config_file, 'r', encoding='utf-8') as f:
                persisted = YAML().load(f)

            self.assertEqual(
                persisted['monitor']['task_scheduler']['lookback_minutes'],
                5)
        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)

    def test_invalid_lookback_defaults_and_written_back(self):
        """非法值应回退默认值，并回写回配置。"""
        config_file = self._write_config(
            "monitor:\n"
            "  task_scheduler:\n"
            "    task_name: task_a\n"
            "    lookback_minutes: abc\n"
        )
        try:
            config = load_config(config_file)
            self.assertEqual(
                config['monitor']['task_scheduler']['lookback_minutes'],
                DEFAULT_VALUES['monitor']['task_scheduler']['lookback_minutes'])

            with open(config_file, 'r', encoding='utf-8') as f:
                persisted = YAML().load(f)

            self.assertEqual(
                persisted['monitor']['task_scheduler']['lookback_minutes'],
                DEFAULT_VALUES['monitor']['task_scheduler']['lookback_minutes'])
        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)

    def test_lookback_string_and_zero_values_are_persisted(self):
        """字符串保留类型、负字符串去符号，零值不回退默认值。"""
        for raw, expected in (("'10'", '10'), ("'-5'", '5'), ('0', 0)):
            with self.subTest(value=raw):
                config_file = self._write_config(
                    "monitor:\n"
                    "  mode: task_scheduler\n"
                    "  task_scheduler:\n"
                    "    task_name: task_a\n"
                    f"    lookback_minutes: {raw}\n"
                )
                try:
                    config = load_config(config_file)
                    with open(config_file, 'r', encoding='utf-8') as stream:
                        persisted = YAML().load(stream)
                    for result in (config, persisted):
                        value = result['monitor']['task_scheduler']['lookback_minutes']
                        self.assertEqual(value, expected)
                        self.assertIsInstance(value, type(expected))
                finally:
                    os.unlink(config_file)


class TestTaskMonitorLookbackQuery(unittest.TestCase):
    """在可控环境验证规范化后的 lookback_minutes 被传入事件查询。"""

    def test_query_called_with_normalized_lookback_minutes(self):
        """确保 monitor_via_task_scheduler 使用规范化后的 lookback 值。"""
        calls = {}

        def fake_query_task_pid(task_name, lookback_minutes):
            calls['task_name'] = task_name
            calls['lookback_minutes'] = lookback_minutes
            return {'state': 'not_found'}

        cfg = {
            "monitor": {
                "common": {
                    "timeout_interval": "1m",
                    "loop_interval": "1s",
                    "timeout_threshold": 3,
                    "max_wait": "1s",
                    "check_interval": "1s",
                },
                "task_scheduler": {
                    "task_name": "task_a",
                    "lookback_minutes": 5,
                },
            },
        }

        with patch('modules.monitor.query_task_pid', fake_query_task_pid):
            try:
                monitor.monitor_via_task_scheduler(cfg)
            except SystemExit:
                pass

        self.assertEqual(calls.get('task_name'), 'task_a')
        self.assertEqual(calls.get('lookback_minutes'), 5)

    def test_query_normalizes_string_negative_invalid_and_zero(self):
        """查询入口保持字符串、负数、非法回退和零值的既有语义。"""
        for raw, expected in (('10', 10), (-5, 5), ('abc', 10), (0, 0)):
            with self.subTest(value=raw):
                with patch.object(
                        task_monitor, '_get_latest_matching_event',
                        return_value=None) as event_mock:
                    result = task_monitor.query_task_pid('task_a', raw)
                event_mock.assert_called_once_with('task_a', 129, expected)
                self.assertEqual(result['state'], 'not_found')

    def test_zero_lookback_uses_one_millisecond_query_window(self):
        """lookback=0 时查询 XPath 仍使用至少 1 毫秒窗口。"""
        with patch.object(task_monitor.win32evtlog, 'EvtQuery') as query_mock, \
                patch.object(task_monitor.win32evtlog, 'EvtNext', return_value=[]), \
                patch.object(task_monitor.win32evtlog, 'EvtClose', create=True):
            task_monitor._get_latest_matching_event('task_a', 129, 0)

        xpath = query_mock.call_args.args[2]
        self.assertIn('timediff(@SystemTime) <= 1', xpath)


if __name__ == '__main__':
    unittest.main()
