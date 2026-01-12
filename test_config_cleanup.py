#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import tempfile
from ruamel.yaml import YAML

# Add the project root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.config import load_config

def test_old_config_cleanup():
    """测试旧配置迁移后清理功能"""
    print("=== 测试旧配置迁移后清理功能 ===")
    
    # 创建一个包含旧参数的配置文件
    old_config = {
        'monitor_settings': {
            'process_name_list': ['MaaPiCli.exe'],
            'timeout_warning_interval_ms': 900000,
            'monitor_loop_interval_ms': 1000,
            'process_name': 'MaaPiCli.exe'  # 同时包含新旧参数
        },
        'wait_process_settings': {
            'max_wait_time_ms': 30000,
            'wait_process_check_interval_ms': 1000,
            'max_wait_time': '30s',  # 同时包含新旧参数
            'wait_process_check_interval': '1s'  # 同时包含新旧参数
        },
        'push_settings': {
            'push_error_retry': {
                'retry_interval_ms': 3000,
                'max_retry_count': 3,
                'retry_interval': '3s'  # 同时包含新旧参数
            }
        },
        'external_program_settings': {
            'external_program_path': 'F:\\Path\\ini\\bat\\KillProcesses-1999.bat'
        },
        'log_settings': {
            'log_filename': 'M9A'
        }
    }
    
    # 写入临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
        yaml = YAML()
        yaml.dump(old_config, f)
        temp_config_file = f.name
    
    try:
        print(f"创建测试配置文件: {temp_config_file}")
        print("配置文件内容:")
        with open(temp_config_file, 'r', encoding='utf-8') as f:
            print(f.read())
        
        # 加载并迁移配置
        print("\n开始加载配置...")
        config = load_config(temp_config_file)
        print("\n配置迁移完成！")
        
        # 读取迁移后的配置文件
        print("\n迁移后的配置文件内容:")
        with open(temp_config_file, 'r', encoding='utf-8') as f:
            migrated_content = f.read()
            print(migrated_content)
        
        # 检查旧参数是否已被清理
        old_params = [
            'process_name_list',
            'timeout_warning_interval_ms', 
            'monitor_loop_interval_ms',
            'max_wait_time_ms',
            'wait_process_check_interval_ms',
            'retry_interval_ms'
        ]
        
        print("\n=== 检查旧参数清理情况 ===")
        all_cleaned = True
        for param in old_params:
            if param in migrated_content:
                print(f"❌ 旧参数 '{param}' 未被清理")
                all_cleaned = False
            else:
                print(f"✅ 旧参数 '{param}' 已被清理")
        
        # 检查新参数是否存在
        new_params = [
            'process_name',
            'timeout_warning_interval',
            'monitor_loop_interval',
            'max_wait_time',
            'wait_process_check_interval',
            'retry_interval'
        ]
        
        print("\n=== 检查新参数存在情况 ===")
        all_new_params_exists = True
        for param in new_params:
            if param in migrated_content:
                print(f"✅ 新参数 '{param}' 存在")
            else:
                print(f"❌ 新参数 '{param}' 不存在")
                all_new_params_exists = False
        
        if all_cleaned and all_new_params_exists:
            print("\n🎉 测试通过！旧配置参数已被正确清理，新配置参数已生成")
        else:
            print("\n❌ 测试失败！旧配置参数未被完全清理或新参数缺失")
            sys.exit(1)
            
    finally:
        # 清理临时文件
        if os.path.exists(temp_config_file):
            os.unlink(temp_config_file)
        old_config_file = f"{os.path.splitext(temp_config_file)[0]}.old.v2{os.path.splitext(temp_config_file)[1]}"
        if os.path.exists(old_config_file):
            os.unlink(old_config_file)

if __name__ == "__main__":
    try:
        test_old_config_cleanup()
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)