#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2RPM V4 单元测试
"""

import os
import sys
import unittest

import psutil
from unittest.mock import patch, MagicMock, call

# 添加项目根目录到模块路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.utils import (
    get_program_directory,
    run_external_program,
    parse_time_string,
    parse_push_channels,
    build_push_channel_node,
    push_channel_signature,
    parse_external_action,
    detect_external_action_type,
    task_action_exists,
    run_task_action,
    run_external_action,
)
from modules.config import (
    load_config,
    merge_configs,
    get_default_config,
    correct_push_channel_config
)
from modules.notification import send_notification
from modules.monitor import monitor_processes, monitor_via_launch
from ruamel.yaml.comments import CommentedMap, CommentedSeq


class TestUtils(unittest.TestCase):
    """测试 utils.py 模块"""

    def test_get_program_directory(self):
        """测试获取程序目录"""
        with patch('sys.argv', ['2RPM-v3.py']):
            with patch('os.path.abspath', return_value='/path/to/2RPM-v3.py'):
                directory = get_program_directory()
                self.assertEqual(directory, '/path/to')

    def test_parse_time_string(self):
        """测试时间字符串解析（V4：返回秒，兼容大小写）"""
        # 测试小时（小写）
        self.assertEqual(parse_time_string('1h'), 3600)

        # 测试分钟（小写）
        self.assertEqual(parse_time_string('15m'), 900)

        # 测试秒（小写）
        self.assertEqual(parse_time_string('30s'), 30)

        # 测试小时（大写）
        self.assertEqual(parse_time_string('1H'), 3600)

        # 测试分钟（大写）
        self.assertEqual(parse_time_string('15M'), 900)

        # 测试秒（大写）
        self.assertEqual(parse_time_string('30S'), 30)

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

    def test_split_args(self):
        """测试命令行参数拆分"""
        from modules.monitor import _split_args
        self.assertEqual(_split_args(''), [])
        self.assertEqual(_split_args('   '), [])
        self.assertEqual(_split_args(None), [])
        self.assertEqual(_split_args('--flag'), ['--flag'])
        self.assertEqual(_split_args('--flag foo --verbose'), ['--flag', 'foo', '--verbose'])
        # 引号参数测试
        self.assertEqual(
            _split_args('--path "C:\\Program Files\\tool.exe"'),
            ['--path', 'C:\\Program Files\\tool.exe']
        )

    def test_resolve_cwd(self):
        """测试工作目录解析与降级"""
        from modules.monitor import _resolve_cwd
        # 用户显式指定
        self.assertEqual(_resolve_cwd('C:\\custom', 'C:\\app\\test.exe'), 'C:\\custom')
        # 用户指定带首尾空格 → 应 strip 后生效
        self.assertEqual(_resolve_cwd(' C:\\custom ', 'C:\\app\\test.exe'), 'C:\\custom')
        # 用户指定但为空/空白 → 从 path 推导
        self.assertEqual(_resolve_cwd('', 'C:\\app\\test.exe'), 'C:\\app')
        self.assertEqual(_resolve_cwd('   ', 'C:\\foo\\bar.exe'), 'C:\\foo')
        self.assertEqual(_resolve_cwd(None, 'C:\\app\\test.exe'), 'C:\\app')
        # path 无目录分量 → 回退 os.getcwd()
        self.assertEqual(_resolve_cwd(None, 'test.exe'), os.getcwd())


class TestExternalActionParsing(unittest.TestCase):
    """测试 external 动作解析和自动判断。"""

    def test_explicit_program_prefix_keeps_drive_colon(self):
        """program 前缀只解析第一个冒号，不破坏 Windows 盘符。"""
        result = parse_external_action(r'program:C:\x\a.bat')
        self.assertEqual(result['type'], 'program')
        self.assertEqual(result['target'], r'C:\x\a.bat')

    def test_explicit_task_prefix_is_case_insensitive(self):
        """task 前缀大小写不敏感。"""
        result = parse_external_action(r'TASK:\Custom\MyTask')
        self.assertEqual(result['type'], 'task')
        self.assertEqual(result['target'], r'\Custom\MyTask')

    def test_explicit_prefix_strips_target_spaces(self):
        """显式前缀后的目标会清理首尾空白。"""
        result = parse_external_action(r'program: C:\x\a.bat ')
        self.assertEqual(result['type'], 'program')
        self.assertEqual(result['target'], r'C:\x\a.bat')

    def test_empty_task_target_raises(self):
        """task 前缀目标为空时报错。"""
        with self.assertRaises(ValueError):
            parse_external_action('task:')

    def test_empty_program_target_raises(self):
        """program 前缀目标为空时报错。"""
        with self.assertRaises(ValueError):
            parse_external_action('program:')

    @patch('modules.utils.task_action_exists')
    def test_drive_path_is_program_without_task_probe(self, exists_mock):
        """盘符路径直接判定为 program，不探测计划任务。"""
        result = parse_external_action(r'C:\x\a.bat')
        self.assertEqual(result['type'], 'program')
        self.assertEqual(result['target'], r'C:\x\a.bat')
        exists_mock.assert_not_called()

    @patch('modules.utils.task_action_exists')
    def test_unc_path_is_program_without_task_probe(self, exists_mock):
        """UNC 路径直接判定为 program，不探测计划任务。"""
        result = parse_external_action(r'\\server\share\a.bat')
        self.assertEqual(result['type'], 'program')
        exists_mock.assert_not_called()

    @patch('modules.utils.task_action_exists')
    def test_executable_suffix_is_program_without_task_probe(self, exists_mock):
        """可执行后缀直接判定为 program，不探测计划任务。"""
        result = parse_external_action('script.ps1')
        self.assertEqual(result['type'], 'program')
        exists_mock.assert_not_called()

    @patch('modules.utils.task_action_exists', return_value=True)
    def test_task_like_path_probe_success_is_task(self, exists_mock):
        """无程序特征且计划任务探测成功时判定为 task。"""
        result = parse_external_action(r'\Custom\MyTask')
        self.assertEqual(result['type'], 'task')
        self.assertEqual(result['target'], r'\Custom\MyTask')
        exists_mock.assert_called_once_with(r'\Custom\MyTask')

    @patch('modules.utils.task_action_exists', return_value=True)
    def test_plain_name_probe_success_is_task(self, exists_mock):
        """普通名称探测成功时判定为 task。"""
        result = parse_external_action('MyTask')
        self.assertEqual(result['type'], 'task')
        self.assertEqual(result['target'], 'MyTask')
        exists_mock.assert_called_once_with('MyTask')

    @patch('modules.utils.task_action_exists', return_value=False)
    def test_plain_name_probe_failure_falls_back_to_program(self, exists_mock):
        """普通名称探测失败时回退 program。"""
        result = parse_external_action('MyTask')
        self.assertEqual(result['type'], 'program')
        self.assertEqual(result['target'], 'MyTask')
        exists_mock.assert_called_once_with('MyTask')


class TestExternalActionExecution(unittest.TestCase):
    """测试 external 动作执行入口。"""

    @patch('modules.utils.run_external_program')
    def test_run_external_action_program_returns_metadata(self, program_mock):
        """program 动作调用 run_external_program 并返回通知元数据。"""
        result = run_external_action(r'program:C:\x\a.bat')
        program_mock.assert_called_once_with(r'C:\x\a.bat')
        self.assertEqual(result['type'], 'program')
        self.assertEqual(result['target'], r'C:\x\a.bat')
        self.assertEqual(result['display_name'], 'a.bat')
        self.assertEqual(result['display_path'], r'C:\x\a.bat')

    @patch('modules.utils.run_task_action')
    def test_run_external_action_task_returns_metadata(self, task_mock):
        """task 动作调用 run_task_action 并返回通知元数据。"""
        result = run_external_action(r'task:\Custom\MyTask')
        task_mock.assert_called_once_with(r'\Custom\MyTask')
        self.assertEqual(result['type'], 'task')
        self.assertEqual(result['target'], r'\Custom\MyTask')
        self.assertEqual(result['display_name'], 'MyTask')
        self.assertEqual(result['display_path'], r'\Custom\MyTask')

    @patch('modules.utils.subprocess.run')
    def test_task_action_exists_uses_schtasks_query(self, run_mock):
        """计划任务探测使用 schtasks query 且只看返回码。"""
        run_mock.return_value.returncode = 0
        self.assertTrue(task_action_exists(r'\Custom\MyTask'))
        run_mock.assert_called_once_with(
            ['schtasks', '/query', '/tn', r'\Custom\MyTask'],
            shell=False,
            capture_output=True,
            text=True,
        )

    @patch('modules.utils.subprocess.run')
    def test_run_task_action_raises_on_failure(self, run_mock):
        """计划任务触发失败时抛出异常。"""
        run_mock.return_value.returncode = 1
        run_mock.return_value.stderr = 'ERROR'
        run_mock.return_value.stdout = ''
        with self.assertRaises(RuntimeError):
            run_task_action(r'\Custom\MyTask')


class TestConfig(unittest.TestCase):
    """测试 config.py 模块"""

    def test_merge_configs(self):
        """测试配置合并（V4 键名）"""
        user_config = {
            'monitor': {
                'process_name': 'custom.exe'
            }
        }
        
        default_config = get_default_config()
        
        merged_config = merge_configs(user_config, default_config)
        self.assertEqual(merged_config['monitor']['process_name'], 'custom.exe')


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

    def test_headless_provider_first_multi_params(self):
        """无参数头：通道名在首位 + 多个命名参数"""
        channels = parse_push_channels('{dingtalk, secret: x, token: y}')
        self.assertEqual(
            channels, [{'provider': 'dingtalk', 'secret': 'x', 'token': 'y'}]
        )

    def test_headless_provider_last(self):
        """无参数头：通道名在末位，命名参数在前"""
        channels = parse_push_channels('{secret: x, token: y, dingtalk}')
        self.assertEqual(
            channels, [{'provider': 'dingtalk', 'secret': 'x', 'token': 'y'}]
        )

    def test_headless_provider_middle(self):
        """无参数头：通道名居中（telegram 多参数）"""
        channels = parse_push_channels(
            '{api_url: x, telegram, token: y, userid: z}'
        )
        self.assertEqual(channels[0]['provider'], 'telegram')
        self.assertEqual(channels[0]['api_url'], 'x')
        self.assertEqual(channels[0]['token'], 'y')
        self.assertEqual(channels[0]['userid'], 'z')

    def test_headless_provider_middle_smtp(self):
        """无参数头：smtp 通道名居中，含类型化参数"""
        channels = parse_push_channels(
            '{user: x, password: y, smtp, host: z, port: 587, ssl: true}'
        )
        self.assertEqual(channels[0]['provider'], 'smtp')
        self.assertEqual(channels[0]['host'], 'z')
        self.assertEqual(channels[0]['port'], 587)
        self.assertEqual(channels[0]['ssl'], True)

    def test_reversed_dict_colon(self):
        """颠倒写法：密钥在前、通道名在后 {SCTxxxx: serverchan}"""
        channels = parse_push_channels('{SCTxxxx: serverchan}')
        self.assertEqual(channels, [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}])

    def test_reversed_dict_comma(self):
        """颠倒写法 {SCTxxxx, serverchan}"""
        channels = parse_push_channels('{SCTxxxx, serverchan}')
        self.assertEqual(channels, [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}])

    def test_reversed_list_colon(self):
        """颠倒写法 [SCTxxxx: serverchan]"""
        channels = parse_push_channels('[SCTxxxx: serverchan]')
        self.assertEqual(channels, [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}])

    def test_reversed_list_comma(self):
        """颠倒写法 [SCTxxxx, serverchan]"""
        channels = parse_push_channels('[SCTxxxx, serverchan]')
        self.assertEqual(channels, [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}])

    def test_unknown_provider_fallback(self):
        """未知渠道（不在白名单）回退为首项即 provider"""
        channels = parse_push_channels('{myprovider, mykey}')
        self.assertEqual(channels[0]['provider'], 'myprovider')

    def test_multi_channels(self):
        """以 ; 分割的多通道应解析为多个通道"""
        channels = parse_push_channels(
            '{provider: serverchan, sckey: SCTxxxx}; {provider: dingtalk, token: tk}'
        )
        self.assertEqual(len(channels), 2)
        self.assertEqual(channels[0]['provider'], 'serverchan')
        self.assertEqual(channels[1]['provider'], 'dingtalk')
        self.assertEqual(channels[1]['token'], 'tk')

    def test_multi_channels_block_list(self):
        """块序列形式（每元素为映射）应解析为多个通道"""
        channels = parse_push_channels([
            {'provider': 'serverchan', 'sckey': 'SCTxxxx'},
            {'provider': 'dingtalk', 'token': 'tk', 'secret': 'sec'},
        ])
        self.assertEqual(len(channels), 2)
        self.assertEqual(channels[0]['provider'], 'serverchan')
        self.assertEqual(channels[0]['sckey'], 'SCTxxxx')
        self.assertEqual(channels[1]['provider'], 'dingtalk')
        self.assertEqual(channels[1]['token'], 'tk')
        self.assertEqual(channels[1]['secret'], 'sec')

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

    def test_single_channel_block_seq(self):
        """单通道也应构建为块序列 CommentedSeq，元素为流式 CommentedMap"""
        node = build_push_channel_node([{'provider': 'serverchan', 'sckey': 'SCTxxxx'}])
        self.assertIsInstance(node, CommentedSeq)
        self.assertEqual(len(node), 1)
        self.assertIsInstance(node[0], CommentedMap)
        self.assertEqual(list(node[0].keys())[0], 'provider')
        self.assertEqual(node[0]['sckey'], 'SCTxxxx')

    def test_multi_channels_block_seq(self):
        """多通道应构建为块序列 CommentedSeq，每元素为流式 CommentedMap"""
        node = build_push_channel_node([
            {'provider': 'serverchan', 'sckey': 'SCTxxxx'},
            {'provider': 'dingtalk', 'token': 'tk'},
        ])
        self.assertIsInstance(node, CommentedSeq)
        self.assertEqual(len(node), 2)
        self.assertIsInstance(node[0], CommentedMap)
        self.assertEqual(list(node[0].keys())[0], 'provider')
        self.assertEqual(node[0]['sckey'], 'SCTxxxx')
        self.assertEqual(node[1]['token'], 'tk')


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

    def test_seq_signature_equal(self):
        """块序列签名应可比较，且与等价的构建节点一致"""
        channels = [
            {'provider': 'serverchan', 'sckey': 'SCTxxxx'},
            {'provider': 'dingtalk', 'token': 'tk'},
        ]
        raw_list = [dict(channel) for channel in channels]
        node = build_push_channel_node(channels)
        self.assertEqual(
            push_channel_signature(raw_list),
            push_channel_signature(node),
        )


class TestCorrectPushChannelConfig(unittest.TestCase):
    """测试 push_channel 配置规范化的整体流程"""

    def _build_config(self, push_channel_value):
        """构造包含 channels 的最小配置字典（V4 键名）

        Args:
            push_channel_value: channels 的原始值。

        Returns:
            dict: 含 push.push_channel_settings.channels 的配置。
        """
        return {
            'push': {
                'push_channel_settings': {
                    'channels': push_channel_value
                }
            }
        }

    def test_normalize_alias_and_order(self):
        """乱序+别名写法应被规范化并触发替换"""
        config = self._build_config('{key: SCTxxxx, provider: serverchan}')
        changed = correct_push_channel_config(config)
        self.assertTrue(changed)
        node = config['push']['push_channel_settings']['channels']
        self.assertIsInstance(node, CommentedSeq)
        self.assertEqual(list(node[0].keys())[0], 'provider')
        self.assertEqual(node[0]['sckey'], 'SCTxxxx')

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


class TestSendNotification(unittest.TestCase):
    """测试推送通知的多通道并发提交逻辑"""

    def _build_config(self, channels_value):
        """构造含模板与多通道的最小推送配置（V4 键名）。

        Args:
            channels_value: push.push_channel_settings.channels 的原始值。

        Returns:
            dict: 含 push.templates / push_channel_settings / retry 的配置。
        """
        return {
            'push': {
                'templates': {
                    'on_end': {
                        'enable': True,
                        'title': '进程结束',
                        'content': '{process_name} 已结束',
                    }
                },
                'push_channel_settings': {
                    'channels': channels_value
                },
                'retry': {
                    'interval': '1s',
                    'max_count': 1,
                }
            }
        }

    @patch('modules.notification._notify_single_channel')
    def test_all_channels_submitted(self, mock_notify):
        """多通道应全部被提交执行（每个通道调用一次）"""
        config = self._build_config(
            '{provider: serverchan, sckey: SCTxxxx}; '
            '{provider: dingtalk, token: tk}'
        )
        send_notification(config, 'on_end', process_name='test.exe')

        # 两个通道均应被提交执行
        self.assertEqual(mock_notify.call_count, 2)
        providers = {
            call.args[0]['provider'] for call in mock_notify.call_args_list
        }
        self.assertEqual(providers, {'serverchan', 'dingtalk'})

    @patch('modules.notification._notify_single_channel')
    def test_disabled_template_skips_push(self, mock_notify):
        """模板 enable=False 时不应提交任何通道"""
        config = self._build_config('{provider: serverchan, sckey: SCTxxxx}')
        config['push']['templates']['on_end']['enable'] = False
        send_notification(config, 'on_end', process_name='test.exe')
        mock_notify.assert_not_called()

    @patch('modules.notification._notify_single_channel')
    def test_no_channels_skips_push(self, mock_notify):
        """未配置通道时不应提交任何通道"""
        config = self._build_config(None)
        send_notification(config, 'on_end', process_name='test.exe')
        mock_notify.assert_not_called()

    @patch('modules.notification._notify_single_channel')
    def test_returns_per_channel_results(self, mock_notify):
        """返回值应为各通道 (provider, 是否成功) 的列表。"""
        # 并发执行下各通道调用顺序不确定，按 provider 绑定返回值避免 flaky
        mock_notify.side_effect = (
            lambda channel, *args, **kwargs: channel['provider'] == 'serverchan'
        )
        config = self._build_config(
            '{provider: serverchan, sckey: SCTxxxx}; '
            '{provider: dingtalk, token: tk}'
        )
        results = send_notification(config, 'on_end', process_name='test.exe')
        self.assertEqual(
            dict(results), {'serverchan': True, 'dingtalk': False}
        )

    @patch('modules.notification._notify_single_channel')
    def test_disabled_template_returns_empty(self, mock_notify):
        """模板禁用时返回空列表。"""
        config = self._build_config('{provider: serverchan, sckey: SCTxxxx}')
        config['push']['templates']['on_end']['enable'] = False
        results = send_notification(config, 'on_end', process_name='test.exe')
        self.assertEqual(results, [])

    @patch('modules.notification._notify_single_channel')
    def test_no_channels_returns_empty(self, mock_notify):
        """无有效通道时返回空列表。"""
        config = self._build_config(None)
        results = send_notification(config, 'on_end', process_name='test.exe')
        self.assertEqual(results, [])


class TestMonitorLoopSmoke(unittest.TestCase):
    """同步主循环冒烟测试：mock psutil 验证结束/超时分支触发通知"""

    def _build_config(self):
        """构造 psutil 模式的最小监视配置（V4 键名）。

        Returns:
            dict: 含 monitor / wait / external 节的配置。
        """
        return {
            'monitor': {
                'monitor_mode': 'psutil',
                'process_name': 'target.exe',
                'timeout_interval': '15m',
                'loop_interval': '1s',
            },
            'wait': {
                'max_wait': '15m',
                'check_interval': '1s',
            },
            'external': {
                'on_end': '',
                'on_timeout': '',
                'on_wait_timeout': '',
                'timeout_threshold': 3,
            },
        }

    @patch('modules.monitor.send_notification')
    @patch('modules.monitor.time.sleep', return_value=None)
    @patch('modules.monitor._collect_matching_processes')
    def test_process_end_triggers_notification(
            self, mock_collect, mock_sleep, mock_notify):
        """进程先启动后结束，应触发 on_end 通知并退出循环"""
        # 第一次：进程已启动；第二次：进程消失（结束）
        mock_collect.side_effect = [
            {1234: {'name': 'target.exe', 'create_time': 100.0}},
            {},
        ]
        monitor_processes(self._build_config())

        # 应调用 on_end 通知
        end_calls = [
            call for call in mock_notify.call_args_list
            if call.args[1] == 'on_end'
        ]
        self.assertEqual(len(end_calls), 1)
        self.assertEqual(end_calls[0].kwargs['process_pid'], 1234)

    @patch('modules.monitor.send_notification')
    @patch('modules.monitor.time.sleep', return_value=None)
    @patch('modules.monitor._collect_matching_processes')
    def test_process_timeout_triggers_notification(
            self, mock_collect, mock_sleep, mock_notify):
        """进程持续运行超过超时间隔，应触发 on_timeout 通知"""
        # 第一轮进程仍在（触发超时），第二轮进程消失以结束循环
        mock_collect.side_effect = [
            {1234: {'name': 'target.exe', 'create_time': 100.0}},
            {1234: {'name': 'target.exe', 'create_time': 100.0}},
            {},
        ]
        config = self._build_config()
        # timeout_interval 设为 0s，任意流逝时间均触发超时，无需 mock time.time
        config['monitor']['timeout_interval'] = '0s'
        monitor_processes(config)

        timeout_calls = [
            call for call in mock_notify.call_args_list
            if call.args[1] == 'on_timeout'
        ]
        self.assertGreaterEqual(len(timeout_calls), 1)
        self.assertEqual(timeout_calls[0].kwargs['process_pid'], 1234)


class TestLaunchSmoke(unittest.TestCase):
    """launch 模式冒烟测试"""

    def _build_config(self, launch_type='program'):
        """构造 launch 模式的最小配置。

        Returns:
            dict: 含 monitor / launch / wait / external 节的配置。
        """
        return {
            'monitor': {
                'monitor_mode': 'launch',
                'timeout_interval': '15m',
                'loop_interval': '1s',
            },
            'launch': {
                'type': launch_type,
                'path': 'C:\\app\\target.exe',
                'task_name': '\\Custom\\MyTask',
            },
            'wait': {
                'max_wait': '30s',
                'check_interval': '1s',
            },
            'external': {
                'on_end': '',
                'on_timeout': '',
                'on_wait_timeout': '',
                'timeout_threshold': 3,
            },
        }

    @patch('modules.monitor.subprocess.Popen')
    @patch('modules.monitor.send_notification')
    @patch('modules.monitor.time.sleep', return_value=None)
    @patch('modules.monitor.psutil.Process')
    @patch('modules.monitor.os.path.isfile', return_value=True)
    def test_launch_program_end_triggers_notification(
            self, mock_isfile, mock_process, mock_sleep, mock_notify, mock_popen):
        """launch type=program: 进程结束后应触发 on_end 通知"""
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_proc.create_time.side_effect = [100.0, 100.0, 100.0]
        mock_popen.return_value = mock_proc
        # 第一次 alive，第二次进程消失
        mock_process.return_value = mock_proc
        mock_process.side_effect = [
            mock_proc, mock_proc, psutil.NoSuchProcess(1234)
        ]

        monitor_via_launch(self._build_config())

        end_calls = [
            call for call in mock_notify.call_args_list
            if call.args[1] == 'on_end'
        ]
        self.assertEqual(len(end_calls), 1)
        self.assertEqual(end_calls[0].kwargs['process_pid'], 1234)

    @patch('modules.monitor.subprocess.Popen')
    @patch('modules.monitor.send_notification')
    @patch('modules.monitor.time.sleep', return_value=None)
    @patch('modules.monitor.psutil.Process')
    @patch('modules.monitor.os.path.isfile', return_value=True)
    def test_launch_program_timeout_triggers_notification(
            self, mock_isfile, mock_process, mock_sleep, mock_notify, mock_popen):
        """launch type=program: 持续运行超时应触发 on_timeout 通知"""
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_proc.create_time.side_effect = [100.0, 100.0, 100.0, 100.0]
        mock_popen.return_value = mock_proc
        # 前三轮 alive（触发一次超时），第四轮进程消失以结束
        mock_process.return_value = mock_proc
        mock_process.side_effect = [
            mock_proc, mock_proc, mock_proc, psutil.NoSuchProcess(1234)
        ]

        config = self._build_config()
        config['monitor']['timeout_interval'] = '0s'
        monitor_via_launch(config)

        timeout_calls = [
            call for call in mock_notify.call_args_list
            if call.args[1] == 'on_timeout'
        ]
        self.assertGreaterEqual(len(timeout_calls), 1)
        self.assertEqual(timeout_calls[0].kwargs['process_pid'], 1234)


if __name__ == '__main__':
    unittest.main()