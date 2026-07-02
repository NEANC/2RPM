#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 task.lookback_minutes 的解析行为与配置回写。

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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import modules.monitor as monitor
from modules.config import load_config, DEFAULT_VALUES


class TestTaskLookbackConfig(unittest.TestCase):
    """测试 task.lookback_minutes 的配置归一化与回写行为。"""

    def _write_config(self, content):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
            f.write(content)
            return f.name

    def test_negative_lookback_is_normalized_and_written_back(self):
        """负值应被修正为正值并写回配置文件。"""
        config_file = self._write_config(
            "task:\n  task_name: task_a\n  lookback_minutes: -5\n"
        )
        try:
            config = load_config(config_file)
            self.assertEqual(config['task']['lookback_minutes'], 5)

            with open(config_file, 'r', encoding='utf-8') as f:
                persisted = f.read()

            self.assertIn("lookback_minutes", persisted)
            self.assertIn("5", persisted)
            self.assertNotIn("-5", persisted)
        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)

    def test_invalid_lookback_defaults_and_written_back(self):
        """非法值应回退默认值，并回写回配置。"""
        config_file = self._write_config(
            "task:\n  task_name: task_a\n  lookback_minutes: abc\n"
        )
        try:
            config = load_config(config_file)
            self.assertEqual(config['task']['lookback_minutes'], DEFAULT_VALUES['task']['lookback_minutes'])

            with open(config_file, 'r', encoding='utf-8') as f:
                persisted = f.read()

            self.assertIn("lookback_minutes", persisted)
            self.assertIn(str(DEFAULT_VALUES['task']['lookback_minutes']), persisted)
        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)

    def test_lookback_string_minutes_accepted(self):
        """允许类似 '10' 这种字符串分钟值"""
        config_file = self._write_config(
            "task:\n  task_name: task_a\n  lookback_minutes: '10'\n"
        )
        try:
            config = load_config(config_file)
            self.assertEqual(config['task']['lookback_minutes'], '10')
        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)


class TestTaskMonitorLookbackQuery(unittest.TestCase):
    """在可控环境验证 lookback_minutes 被传入事件查询。"""

    def test_query_called_with_lookback_minutes(self):
        """确保 monitor_via_task_scheduler 使用规范化后的 lookback 值。"""
        calls = {}

        def fake_query_task_pid(task_name, lookback_minutes):
            calls['task_name'] = task_name
            calls['lookback_minutes'] = lookback_minutes
            return {'state': 'not_found'}

        cfg = {
            'task': {'task_name': 'task_a', 'lookback_minutes': 5},
            'external': {},
            'monitor': {
                'timeout_interval': '1m',
                'loop_interval': '1s',
            },
            'wait': {
                'check_interval': '1s',
                'max_wait': '1s',
            },
        }

        with patch('modules.monitor.query_task_pid', fake_query_task_pid):
            try:
                monitor.monitor_via_task_scheduler(cfg)
            except SystemExit:
                pass

        self.assertEqual(calls.get('task_name'), 'task_a')
        self.assertEqual(calls.get('lookback_minutes'), 5)


if __name__ == '__main__':
    unittest.main()
