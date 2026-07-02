#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
import tempfile
import unittest

from ruamel.yaml import YAML

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.config import load_config, DEFAULT_VALUES


class TestConfigWritableValueNormalization(unittest.TestCase):
    """测试配置中可写负值字段统一处理（去负号并回写）。"""

    def _make_tmp_config(self, content: str) -> str:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
            f.write(content)
            return f.name

    def test_negative_time_like_fields_are_normalized(self):
        """时间字符串参数：负值应去负号并写回正值。"""
        config_file = self._make_tmp_config(
            "monitor:\n"
            "  process_name: test.exe\n"
            "  timeout_interval: -5m\n"
            "  loop_interval: -10s\n"
            "wait:\n"
            "  max_wait: -30s\n"
            "  check_interval: -1s\n"
            "push:\n"
            "  retry:\n"
            "    interval: -3s\n"
        )

        try:
            config = load_config(config_file)

            self.assertEqual(config['monitor']['timeout_interval'], '5m')
            self.assertEqual(config['monitor']['loop_interval'], '10s')
            self.assertEqual(config['wait']['max_wait'], '30s')
            self.assertEqual(config['wait']['check_interval'], '1s')
            self.assertEqual(config['push']['retry']['interval'], '3s')

            with open(config_file, 'r', encoding='utf-8') as f:
                persisted = f.read()

            self.assertIn('timeout_interval: 5m', persisted)
            self.assertIn('loop_interval: 10s', persisted)
            self.assertIn('max_wait: 30s', persisted)
            self.assertIn('check_interval: 1s', persisted)
            self.assertIn('interval: 3s', persisted)
            self.assertNotIn('-5m', persisted)
            self.assertNotIn('-10s', persisted)
        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)

    def test_negative_positive_int_fields_are_normalized(self):
        """整数型参数：负值应去符号为正并写回。"""
        config_file = self._make_tmp_config(
            "external:\n"
            "  on_end: path\\to\\script.bat\n"
            "  on_timeout: path\\to\\timeout.bat\n"
            "  timeout_threshold: -3\n"
            "log:\n"
            "  log_directory: logs\n"
            "  max_log_files: -15\n"
            "  retention_days: -3\n"
            "push:\n"
            "  retry:\n"
            "    max_count: -3\n"
            "task:\n"
            "  task_name: demo_task\n"
            "  lookback_minutes: -10\n"
        )

        try:
            config = load_config(config_file)

            self.assertEqual(config['external']['timeout_threshold'], 3)
            self.assertEqual(config['log']['max_log_files'], 15)
            self.assertEqual(config['log']['retention_days'], 3)
            self.assertEqual(config['push']['retry']['max_count'], 3)
            self.assertEqual(config['task']['lookback_minutes'], 10)

            with open(config_file, 'r', encoding='utf-8') as f:
                persisted = f.read()

            self.assertIn('timeout_threshold: 3', persisted)
            self.assertIn('max_log_files: 15', persisted)
            self.assertIn('retention_days: 3', persisted)
            self.assertIn('max_count: 3', persisted)
            self.assertIn('lookback_minutes: 10', persisted)
            self.assertNotIn('-3', persisted)
            self.assertNotIn('-15', persisted)
            self.assertNotIn('-10', persisted)
        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)


if __name__ == '__main__':
    unittest.main()
