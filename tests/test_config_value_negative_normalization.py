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
from modules.utils import parse_time_string


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
            "  common:\n"
            "    timeout_interval: -5m\n"
            "    loop_interval: -10s\n"
            "    max_wait: -30s\n"
            "    check_interval: -1s\n"
            "    timeout_threshold: -3\n"
            "  task_scheduler:\n"
            "    lookback_minutes: -10\n"
            "push:\n"
            "  retry:\n"
            "    interval: -3s\n"
        )

        try:
            config = load_config(config_file)

            self.assertEqual(config['monitor']['common']['timeout_interval'], '5m')
            self.assertEqual(config['monitor']['common']['loop_interval'], '10s')
            self.assertEqual(config['monitor']['common']['max_wait'], '30s')
            self.assertEqual(config['monitor']['common']['check_interval'], '1s')
            self.assertEqual(config['monitor']['common']['timeout_threshold'], 3)
            self.assertEqual(config['monitor']['task_scheduler']['lookback_minutes'], 10)
            self.assertEqual(config['push']['retry']['interval'], '3s')

            with open(config_file, 'r', encoding='utf-8') as f:
                persisted = YAML().load(f)

            self.assertEqual(
                persisted['monitor']['common']['timeout_interval'], '5m')
            self.assertEqual(
                persisted['monitor']['common']['loop_interval'], '10s')
            self.assertEqual(
                persisted['monitor']['common']['max_wait'], '30s')
            self.assertEqual(
                persisted['monitor']['common']['check_interval'], '1s')
            self.assertEqual(
                persisted['monitor']['common']['timeout_threshold'], 3)
            self.assertEqual(
                persisted['monitor']['task_scheduler']['lookback_minutes'], 10)
            self.assertEqual(persisted['push']['retry']['interval'], '3s')
        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)

    def test_common_zero_values_are_preserved_and_written_back(self):
        """四个时间字段与阈值的零值应保留并按零秒解析。"""
        time_fields = (
            'timeout_interval', 'loop_interval', 'max_wait', 'check_interval',
        )
        config_file = self._make_tmp_config(
            "monitor:\n"
            "  mode: psutil\n"
            "  psutil:\n"
            "    process_name: test.exe\n"
            "  common:\n"
            + ''.join(f"    {field}: 0s\n" for field in time_fields)
            + "    timeout_threshold: 0\n"
        )
        try:
            config = load_config(config_file)
            with open(config_file, 'r', encoding='utf-8') as stream:
                persisted = YAML().load(stream)
            for result in (config, persisted):
                common = result['monitor']['common']
                for field in time_fields:
                    self.assertEqual(common[field], '0s')
                    self.assertEqual(parse_time_string(common[field]), 0)
                self.assertEqual(common['timeout_threshold'], 0)
                self.assertNotIn('timeout_threshold', result['external'])
        finally:
            os.unlink(config_file)

    def test_invalid_common_values_default_at_new_paths_and_persist(self):
        """非法时间和阈值按新路径告警、回退并持久化。"""
        fields = (
            'timeout_interval', 'loop_interval', 'max_wait',
            'check_interval', 'timeout_threshold',
        )
        for invalid in ('invalid', "''", '[]'):
            with self.subTest(value=invalid):
                config_file = self._make_tmp_config(
                    "monitor:\n"
                    "  mode: psutil\n"
                    "  psutil:\n"
                    "    process_name: test.exe\n"
                    "  common:\n"
                    + ''.join(f"    {field}: {invalid}\n" for field in fields)
                )
                try:
                    with self.assertLogs('modules.config', level='WARNING') as logs:
                        config = load_config(config_file)
                    with open(config_file, 'r', encoding='utf-8') as stream:
                        persisted = YAML().load(stream)
                    for field in fields:
                        expected = DEFAULT_VALUES['monitor']['common'][field]
                        self.assertEqual(config['monitor']['common'][field], expected)
                        self.assertEqual(persisted['monitor']['common'][field], expected)
                        self.assertIn(f'monitor.common.{field}', '\n'.join(logs.output))
                finally:
                    os.unlink(config_file)

    def test_negative_positive_int_fields_are_normalized(self):
        """整数型参数：负值应去符号为正并写回。"""
        config_file = self._make_tmp_config(
            "monitor:\n"
            "  common:\n"
            "    timeout_threshold: -3\n"
            "  task_scheduler:\n"
            "    lookback_minutes: -10\n"
            "log:\n"
            "  log_directory: logs\n"
            "  max_log_files: -15\n"
            "  retention_days: -3\n"
            "push:\n"
            "  retry:\n"
            "    max_count: -3\n"
        )

        try:
            config = load_config(config_file)

            self.assertEqual(config['monitor']['common']['timeout_threshold'], 3)
            self.assertEqual(config['log']['max_log_files'], 15)
            self.assertEqual(config['log']['retention_days'], 3)
            self.assertEqual(config['push']['retry']['max_count'], 3)
            self.assertEqual(config['monitor']['task_scheduler']['lookback_minutes'], 10)

            with open(config_file, 'r', encoding='utf-8') as f:
                persisted = YAML().load(f)

            self.assertEqual(
                persisted['monitor']['common']['timeout_threshold'], 3)
            self.assertEqual(
                persisted['monitor']['task_scheduler']['lookback_minutes'], 10)
            self.assertEqual(persisted['log']['max_log_files'], 15)
            self.assertEqual(persisted['log']['retention_days'], 3)
            self.assertEqual(persisted['push']['retry']['max_count'], 3)
        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)


if __name__ == '__main__':
    unittest.main()
