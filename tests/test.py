#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2RPM V3 单元测试
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# 添加项目根目录到模块路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.utils import (
    get_program_directory,
    format_time_ms,
    run_external_program,
    parse_time_string,
    parse_push_channels,
    build_push_channel_node,
    push_channel_signature
)
from modules.config import (
    load_config,
    merge_configs,
    get_default_config,
    correct_push_channel_config
)
from ruamel.yaml.comments import CommentedMap


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


class TestParsePushChannels(unittest.TestCase):
    """测试 push_channel 配置的解析逻辑"""

    def test_standard_dict(self):
        """标准字典写法应正确解析，provider 排在最前"""
        channels = parse_push_channels('{provider: serverchan, sckey: SCTxxxx}')
        self.assertEqual(channels, [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}])

    def test_keys_out_of_order(self):
        """键乱序时仍应正确解析"""
        channels = parse_push_channels('{sckey: SCTxxxx, provider: serverchan}')
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]['provider'], 'serverchan')
        self.assertEqual(channels[0]['sckey'], 'SCTxxxx')
        # provider 必须是首个键
        self.assertEqual(list(channels[0].keys())[0], 'provider')

    def test_alias_key_corrected(self):
        """serverchan 的别名 key 应被纠正为 sckey"""
        channels = parse_push_channels('{provider: serverchan, key: SCTxxxx}')
        self.assertEqual(channels, [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}])

    def test_headless_list_comma(self):
        """无参数头写法 [serverchan, SCTxxxx]"""
        channels = parse_push_channels('[serverchan, SCTxxxx]')
        self.assertEqual(channels, [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}])

    def test_headless_list_colon(self):
        """无参数头写法 [serverchan: SCTxxxx]"""
        channels = parse_push_channels('[serverchan: SCTxxxx]')
        self.assertEqual(channels, [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}])

    def test_headless_dict_comma(self):
        """无参数头写法 {serverchan, SCTxxxx}"""
        channels = parse_push_channels('{serverchan, SCTxxxx}')
        self.assertEqual(channels, [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}])

    def test_headless_dict_colon(self):
        """无参数头写法 {serverchan: SCTxxxx}"""
        channels = parse_push_channels('{serverchan: SCTxxxx}')
        self.assertEqual(channels, [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}])

    def test_dingtalk_multi_params_out_of_order(self):
        """dingtalk 多参数乱序应正确解析"""
        channels = parse_push_channels(
            '{secret: mysecret, provider: dingtalk, token: mytoken}'
        )
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]['provider'], 'dingtalk')
        self.assertEqual(channels[0]['token'], 'mytoken')
        self.assertEqual(channels[0]['secret'], 'mysecret')

    def test_multi_channels(self):
        """以 ; 分割的多通道应解析为多个通道"""
        channels = parse_push_channels(
            '{provider: serverchan, sckey: SCTxxxx}; {provider: dingtalk, token: tk}'
        )
        self.assertEqual(len(channels), 2)
        self.assertEqual(channels[0]['provider'], 'serverchan')
        self.assertEqual(channels[1]['provider'], 'dingtalk')
        self.assertEqual(channels[1]['token'], 'tk')

    def test_empty_value(self):
        """空值与空字符串应返回空列表"""
        self.assertEqual(parse_push_channels(None), [])
        self.assertEqual(parse_push_channels(''), [])
        self.assertEqual(parse_push_channels('   '), [])


class TestBuildPushChannelNode(unittest.TestCase):
    """测试 push_channel 节点的回写构建逻辑"""

    def test_empty_channels(self):
        """空通道列表应返回空 CommentedMap"""
        node = build_push_channel_node([])
        self.assertIsInstance(node, CommentedMap)
        self.assertEqual(len(node), 0)

    def test_single_channel_flow_map(self):
        """单通道应构建为流式 CommentedMap，provider 在最前"""
        node = build_push_channel_node([{'provider': 'serverchan', 'sckey': 'SCTxxxx'}])
        self.assertIsInstance(node, CommentedMap)
        self.assertEqual(list(node.keys())[0], 'provider')
        self.assertEqual(node['sckey'], 'SCTxxxx')

    def test_multi_channels_string(self):
        """多通道应构建为以 ; 分割的字符串"""
        node = build_push_channel_node([
            {'provider': 'serverchan', 'sckey': 'SCTxxxx'},
            {'provider': 'dingtalk', 'token': 'tk'},
        ])
        self.assertIsInstance(node, str)
        self.assertIn(';', node)
        self.assertTrue(node.strip().startswith('{provider: serverchan'))


class TestPushChannelSignature(unittest.TestCase):
    """测试 push_channel 签名比较逻辑"""

    def test_dict_signature_equal_ignoring_order(self):
        """字典签名应忽略空白差异保持可比较"""
        sig1 = push_channel_signature({'provider': 'serverchan', 'sckey': 'SCTxxxx'})
        sig2 = push_channel_signature({'provider': 'serverchan', 'sckey': 'SCTxxxx'})
        self.assertEqual(sig1, sig2)

    def test_str_signature_ignores_spaces(self):
        """字符串签名应忽略空格差异"""
        sig1 = push_channel_signature('{provider: a}; {provider: b}')
        sig2 = push_channel_signature('{provider:a};{provider:b}')
        self.assertEqual(sig1, sig2)


class TestCorrectPushChannelConfig(unittest.TestCase):
    """测试 push_channel 配置规范化的整体流程"""

    def _build_config(self, push_channel_value):
        """构造包含 push_channel 的最小配置字典

        Args:
            push_channel_value: push_channel 的原始值。

        Returns:
            dict: 含 push_settings.push_channel_settings.push_channel 的配置。
        """
        return {
            'push_settings': {
                'push_channel_settings': {
                    'push_channel': push_channel_value
                }
            }
        }

    def test_normalize_alias_and_order(self):
        """乱序+别名写法应被规范化并触发替换"""
        config = self._build_config('{key: SCTxxxx, provider: serverchan}')
        changed = correct_push_channel_config(config)
        self.assertTrue(changed)
        node = config['push_settings']['push_channel_settings']['push_channel']
        self.assertEqual(list(node.keys())[0], 'provider')
        self.assertEqual(node['sckey'], 'SCTxxxx')

    def test_already_normalized_no_change(self):
        """已是标准流式格式时不应重复替换"""
        node = build_push_channel_node([{'provider': 'serverchan', 'sckey': 'SCTxxxx'}])
        config = self._build_config(node)
        changed = correct_push_channel_config(config)
        self.assertFalse(changed)

    def test_empty_value_no_change(self):
        """空配置不应触发替换"""
        self.assertFalse(correct_push_channel_config(self._build_config(None)))
        self.assertFalse(correct_push_channel_config(self._build_config({})))
        self.assertFalse(correct_push_channel_config(self._build_config('')))


if __name__ == '__main__':
    unittest.main()