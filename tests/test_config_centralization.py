#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""配置管理行为回归测试集合。"""

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch
from ruamel.yaml import YAML

# 将项目根目录加入模块搜索路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.config import load_config, DEFAULT_VALUES


def test_new_config_validation():
    """验证缺少字段时会按默认值补齐。"""
    print("\n=== 测试新配置参数缺失检查功能 ===")

    # 创建一个缺少参数的新版本配置文件
    new_config = {
        'monitor': {
            'process_name': 'notepad.exe'
            # 缺少 timeout_interval 和 loop_interval
        },
        'wait': {
            'max_wait': '30s'
            # 缺少 check_interval
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
        assert 'timeout_interval' in config['monitor'], "未填充 timeout_interval 默认值"
        assert 'loop_interval' in config['monitor'], "未填充 loop_interval 默认值"
        assert 'check_interval' in config['wait'], "未填充 check_interval 默认值"

        print("所有缺失参数已填充默认值")
        print(f"   - timeout_interval 默认值: {config['monitor']['timeout_interval']}")
        print(f"   - loop_interval 默认值: {config['monitor']['loop_interval']}")
        print(f"   - check_interval 默认值: {config['wait']['check_interval']}")

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
            patch('modules.config.sys.exit') as exit_mock:
        load_config('config.yaml')

        create_default_mock.assert_called_once_with('config.yaml')
        critical_mock.assert_any_call(f"配置文件不存在: {config_path}")
        spinner_mock.assert_called_once_with("请按任意键退出...")
        input_mock.assert_called_once_with()
        exit_mock.assert_called_once_with(0)


def test_centralized_defaults():
    """测试集中管理的默认值结构完整性。"""
    print("\n=== 测试集中管理的默认值功能 ===")

    # 验证 DEFAULT_VALUES 结构完整
    assert 'monitor' in DEFAULT_VALUES, "DEFAULT_VALUES 缺少 monitor"
    assert 'wait' in DEFAULT_VALUES, "DEFAULT_VALUES 缺少 wait"
    assert 'push' in DEFAULT_VALUES, "DEFAULT_VALUES 缺少 push"
    assert 'external' in DEFAULT_VALUES, "DEFAULT_VALUES 缺少 external"
    assert 'log' in DEFAULT_VALUES, "DEFAULT_VALUES 缺少 log"

    print("DEFAULT_VALUES 结构完整")
    print(f"   - monitor.process_name 默认值: {DEFAULT_VALUES['monitor']['process_name']}")
    print(f"   - monitor.timeout_interval 默认值: {DEFAULT_VALUES['monitor']['timeout_interval']}")
    print(f"   - wait.max_wait 默认值: {DEFAULT_VALUES['wait']['max_wait']}")
    print(f"   - push.retry.max_count 默认值: {DEFAULT_VALUES['push']['retry']['max_count']}")


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
