#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2RPM V3 单元测试
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# 添加模块路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.utils import (
    get_program_directory,
    format_time_ms,
    run_external_program,
    get_other_running_processes,
    parse_time_string
)
from modules.config import load_config, merge_configs, get_default_config


class TestUtils(unittest.TestCase):
    """测试 utils.py 模块"""

    def test_get_program_directory(self):
        """测试获取程序目录"""
        with patch('sys.argv', ['2RPM-v3.py']):
            with patch('os.path.abspath', return_value='/path/to/2RPM-v3.py'):
                directory = get_program_directory()
                self.assertEqual(directory, '/path/to')

    def test_format_time_ms(self):
        """测试时间格式化"""
        # 测试 1 小时 30 分钟 45 秒
        result = format_time_ms(5445000)  # 1*3600000 + 30*60000 + 45*1000
        self.assertEqual(result, '01:30:45')
        
        # 测试 0 毫秒
        result = format_time_ms(0)
        self.assertEqual(result, '00:00:00')

    def test_parse_time_string(self):
        """测试时间字符串解析"""
        # 测试小时
        self.assertEqual(parse_time_string('1h'), 3600000)
        
        # 测试分钟
        self.assertEqual(parse_time_string('15m'), 900000)
        
        # 测试秒
        self.assertEqual(parse_time_string('30s'), 30000)
        
        # 测试直接数字
        self.assertEqual(parse_time_string('5000'), 5000)

    def test_get_other_running_processes(self):
        """测试获取其他运行进程"""
        processes = {
            1234: {'name': 'process1'},
            5678: {'name': 'process2'},
            9012: {'name': 'process3'}
        }
        
        # 排除 1234
        result = get_other_running_processes(processes, 1234)
        self.assertIn('process2', result)
        self.assertIn('process3', result)
        self.assertNotIn('process1', result)
        
        # 不排除任何进程
        result = get_other_running_processes(processes)
        self.assertIn('process1', result)
        self.assertIn('process2', result)
        self.assertIn('process3', result)

    @patch('subprocess.Popen')
    def test_run_external_program(self, mock_popen):
        """测试运行外部程序"""
        # 测试批处理文件
        run_external_program('test.bat')
        mock_popen.assert_called()

        # 测试 EXE 文件
        run_external_program('test.exe')
        mock_popen.assert_called()


class TestConfig(unittest.TestCase):
    """测试 config.py 模块"""

    def test_merge_configs(self):
        """测试配置合并"""
        user_config = {
            'monitor_settings': {
                'process_name': 'custom.exe'
            }
        }
        
        default_config = get_default_config()
        
        merged_config = merge_configs(user_config, default_config)
        self.assertEqual(merged_config['monitor_settings']['process_name'], 'custom.exe')


if __name__ == '__main__':
    unittest.main()