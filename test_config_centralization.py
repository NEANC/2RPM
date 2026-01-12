#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import tempfile
from ruamel.yaml import YAML

# Add the project root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.config import load_config, DEFAULT_VALUES

def test_old_config_migration():
    """测试旧配置迁移功能"""
    print("=== 测试旧配置迁移功能 ===")
    
    # 创建一个旧版本配置文件
    old_config = {
        'monitor_settings': {
            'process_name_list': ['notepad.exe', 'explorer.exe'],
            'timeout_warning_interval_ms': 900000,
            'monitor_loop_interval_ms': 1000
        },
        'wait_process_settings': {
            'max_wait_time_ms': 30000,
            'wait_process_check_interval_ms': 1000
        },
        'push_settings': {
            'push_error_retry': {
                'retry_interval_ms': 3000,
                'max_retry_count': 3
            }
        }
    }
    
    # 写入临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
        yaml = YAML()
        yaml.dump(old_config, f)
        temp_config_file = f.name
    
    try:
        # 加载并迁移配置
        config = load_config(temp_config_file)
        print("✅ 旧配置迁移成功")
        
        # 检查迁移结果
        assert 'process_name' in config['monitor_settings'], "❌ 未迁移 process_name_list 到 process_name"
        assert 'timeout_warning_interval' in config['monitor_settings'], "❌ 未迁移 timeout_warning_interval_ms"
        assert 'monitor_loop_interval' in config['monitor_settings'], "❌ 未迁移 monitor_loop_interval_ms"
        assert 'max_wait_time' in config['wait_process_settings'], "❌ 未迁移 max_wait_time_ms"
        assert 'wait_process_check_interval' in config['wait_process_settings'], "❌ 未迁移 wait_process_check_interval_ms"
        assert 'retry_interval' in config['push_settings']['push_error_retry'], "❌ 未迁移 retry_interval_ms"
        
        print("✅ 所有配置项迁移成功")
        print(f"   - 迁移后的 process_name: {config['monitor_settings']['process_name']}")
        print(f"   - 迁移后的 timeout_warning_interval: {config['monitor_settings']['timeout_warning_interval']}")
        print(f"   - 迁移后的 monitor_loop_interval: {config['monitor_settings']['monitor_loop_interval']}")
        
    finally:
        # 清理临时文件
        if os.path.exists(temp_config_file):
            os.unlink(temp_config_file)
        old_config_file = f"{os.path.splitext(temp_config_file)[0]}.old.v2{os.path.splitext(temp_config_file)[1]}"
        if os.path.exists(old_config_file):
            os.unlink(old_config_file)

def test_new_config_validation():
    """测试新配置参数缺失检查功能"""
    print("\n=== 测试新配置参数缺失检查功能 ===")
    
    # 创建一个缺少参数的新版本配置文件
    new_config = {
        'monitor_settings': {
            'process_name': 'notepad.exe'
            # 缺少 timeout_warning_interval 和 monitor_loop_interval
        },
        'wait_process_settings': {
            'max_wait_time': '30s'
            # 缺少 wait_process_check_interval
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
        print("✅ 新配置加载成功")
        
        # 检查是否填充了默认值
        assert 'timeout_warning_interval' in config['monitor_settings'], "❌ 未填充 timeout_warning_interval 默认值"
        assert 'monitor_loop_interval' in config['monitor_settings'], "❌ 未填充 monitor_loop_interval 默认值"
        assert 'wait_process_check_interval' in config['wait_process_settings'], "❌ 未填充 wait_process_check_interval 默认值"
        
        print("✅ 所有缺失参数已填充默认值")
        print(f"   - timeout_warning_interval 默认值: {config['monitor_settings']['timeout_warning_interval']}")
        print(f"   - monitor_loop_interval 默认值: {config['monitor_settings']['monitor_loop_interval']}")
        print(f"   - wait_process_check_interval 默认值: {config['wait_process_settings']['wait_process_check_interval']}")
        
    finally:
        # 清理临时文件
        if os.path.exists(temp_config_file):
            os.unlink(temp_config_file)

def test_centralized_defaults():
    """测试集中管理的默认值功能"""
    print("\n=== 测试集中管理的默认值功能 ===")
    
    # 验证 DEFAULT_VALUES 结构完整
    assert 'monitor_settings' in DEFAULT_VALUES, "❌ DEFAULT_VALUES 缺少 monitor_settings"
    assert 'wait_process_settings' in DEFAULT_VALUES, "❌ DEFAULT_VALUES 缺少 wait_process_settings"
    assert 'push_settings' in DEFAULT_VALUES, "❌ DEFAULT_VALUES 缺少 push_settings"
    assert 'external_program_settings' in DEFAULT_VALUES, "❌ DEFAULT_VALUES 缺少 external_program_settings"
    assert 'log_settings' in DEFAULT_VALUES, "❌ DEFAULT_VALUES 缺少 log_settings"
    
    print("✅ DEFAULT_VALUES 结构完整")
    print(f"   - monitor_settings.process_name 默认值: {DEFAULT_VALUES['monitor_settings']['process_name']}")
    print(f"   - monitor_settings.timeout_warning_interval 默认值: {DEFAULT_VALUES['monitor_settings']['timeout_warning_interval']}")
    print(f"   - wait_process_settings.max_wait_time 默认值: {DEFAULT_VALUES['wait_process_settings']['max_wait_time']}")
    print(f"   - push_settings.push_error_retry.max_retry_count 默认值: {DEFAULT_VALUES['push_settings']['push_error_retry']['max_retry_count']}")

if __name__ == "__main__":
    try:
        test_old_config_migration()
        test_new_config_validation()
        test_centralized_defaults()
        print("\n🎉 所有测试通过！配置管理功能正常工作")
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)