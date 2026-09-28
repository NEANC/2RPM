#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""配置管理行为回归测试集合。"""

import os
import sys
import tempfile
import pytest
from unittest.mock import MagicMock, patch
from ruamel.yaml import YAML

# 将项目根目录加入模块搜索路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.config import get_default_config, load_config, DEFAULT_VALUES


def test_new_config_validation():
    """验证缺少字段时会按默认值补齐。"""
    print("\n=== 测试新配置参数缺失检查功能 ===")

    # 创建一个缺少参数的新版本配置文件
    new_config = {
        'monitor': {
            'psutil': {
                'process_name': 'notepad.exe'
            }
            # 缺少 common 中的时间参数
        },
        'push': {
            'retry': {}
        }
    }

    # 写入临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
        yaml = YAML()
        yaml.dump(new_config, f)
        temp_config_file = f.name

    try:
        # 加载配置
        config = load_config(temp_config_file)
        print("新配置加载成功")

        # 检查是否填充了默认值
        assert 'timeout_interval' in config['monitor']['common'], "未填充 timeout_interval 默认值"
        assert 'loop_interval' in config['monitor']['common'], "未填充 loop_interval 默认值"
        assert 'max_wait' in config['monitor']['common'], "未填充 max_wait 默认值"
        assert 'check_interval' in config['monitor']['common'], "未填充 check_interval 默认值"

        print("所有缺失参数已填充默认值")
        print(f"   - timeout_interval 默认值: {config['monitor']['common']['timeout_interval']}")
        print(f"   - loop_interval 默认值: {config['monitor']['common']['loop_interval']}")
        print(f"   - check_interval 默认值: {config['monitor']['common']['check_interval']}")

    finally:
        # 清理临时文件
        if os.path.exists(temp_config_file):
            os.unlink(temp_config_file)


def test_missing_config_file_should_create_default_and_prompt():
    """缺失配置文件时应输出完整路径并提示退出。"""
    config_path = os.path.abspath('config.yaml')
    mock_spinner = MagicMock()

    with patch('modules.config.os.path.exists', return_value=False), \
            patch('modules.config.os.path.abspath', return_value=config_path), \
            patch('modules.config.create_default_config') as create_default_mock, \
            patch('modules.config.LOGGER.critical') as critical_mock, \
            patch('modules.config.spinner_phase', return_value=mock_spinner) as spinner_mock, \
            patch('builtins.input', return_value='') as input_mock, \
            patch('modules.config.sys.exit', side_effect=SystemExit(0)) as exit_mock:
        with pytest.raises(SystemExit):
            load_config('config.yaml')

        create_default_mock.assert_called_once_with('config.yaml')
        critical_mock.assert_any_call(f"配置文件不存在: {config_path}")
        spinner_mock.assert_called_once_with("请按任意键退出...")
        input_mock.assert_called_once_with()
        exit_mock.assert_called_once_with(0)


def test_user_specified_config_missing_should_exit_with_error():
    """用户显式指定的配置文件不存在时应报 CRITICAL 并以退出码 1 退出。"""
    config_path = os.path.abspath('nonexistent.yaml')

    with patch('modules.config.os.path.exists', return_value=False), \
            patch('modules.config.os.path.abspath', return_value=config_path), \
            patch('modules.config.create_default_config') as create_default_mock, \
            patch('modules.config.LOGGER.critical') as critical_mock, \
            patch('modules.config.sys.exit', side_effect=SystemExit(1)) as exit_mock:
        with pytest.raises(SystemExit):
            load_config('nonexistent.yaml', is_user_specified=True)

        create_default_mock.assert_not_called()
        critical_mock.assert_any_call(f"配置文件不存在: {config_path}")
        critical_mock.assert_any_call("用户指定的配置文件不存在，请检查路径后重试")
        exit_mock.assert_called_once_with(1)


def test_centralized_defaults():
    """测试集中管理的新配置默认值结构。"""
    monitor = DEFAULT_VALUES['monitor']
    assert set(monitor) == {'mode', 'common', 'psutil', 'task_scheduler', 'launch'}
    assert monitor['mode'] == 'psutil'
    assert monitor['common'] == {
        'timeout_interval': '15m',
        'loop_interval': '1s',
        'timeout_threshold': 3,
        'max_wait': '30s',
        'check_interval': '1s',
    }
    assert monitor['psutil'] == {'process_name': 'notepad.exe'}
    assert monitor['task_scheduler'] == {
        'task_name': '\\Custom\\MyTask',
        'lookback_minutes': 10,
    }
    assert monitor['launch'] == {
        'type': 'program',
        'path': 'C:\\path\\to\\target.exe',
        'task_name': '\\Custom\\MyTask',
        'args': None,
        'cwd': None,
    }
    assert 'timeout_threshold' not in DEFAULT_VALUES['external']
    assert DEFAULT_VALUES['push']['retry'] == {'interval': '3s', 'max_count': 3}
    assert DEFAULT_VALUES['log'] == {
        'log_directory': 'logs',
        'max_log_files': 15,
        'retention_days': 3,
    }


def test_get_default_config_creates_independent_mutable_nodes():
    """验证每次获取默认配置都不会共享可变节点。"""
    first = get_default_config()
    second = get_default_config()

    first['monitor']['common']['timeout_interval'] = '1h'
    first['monitor']['launch']['args'] = ['--changed']
    first['push']['templates']['on_end']['enable'] = False
    first['push']['push_channel_settings']['channels'].append({'provider': 'test'})

    assert second['monitor']['common']['timeout_interval'] == '15m'
    assert second['monitor']['launch']['args'] is None
    assert second['push']['templates']['on_end']['enable'] is True
    assert len(second['push']['push_channel_settings']['channels']) == 5


if __name__ == "__main__":
    try:
        test_new_config_validation()
        test_missing_config_file_should_create_default_and_prompt()
        test_centralized_defaults()
        print("\n所有测试通过！配置管理功能正常工作（V4）")
    except Exception as e:
        print(f"\n测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
