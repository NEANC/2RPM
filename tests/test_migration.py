#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
from ruamel.yaml import YAML

# 添加项目根目录到系统路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.config import load_config, migrate_old_config, ms_to_hms

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def test_time_conversion():
    """测试时间单位转换功能。"""
    print("测试时间单位转换功能...")
    test_cases = [
        (2700000, '45m'),  # 45分钟
        (1000, '1s'),      # 1秒
        (30000, '30s'),    # 30秒
        (3600000, '1h'),   # 1小时
        (3661000, '1h')    # 1小时61秒，应该转换为1小时
    ]
    
    for ms, expected in test_cases:
        result = ms_to_hms(ms)
        status = "✓" if result == expected else "✗"
        print(f"{status} {ms}ms -> {result} (期望: {expected})")

def test_config_migration():
    """测试配置迁移功能。"""
    print("\n测试配置迁移功能...")
    
    # 读取旧配置文件
    old_config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dist', 'M9A.yaml')
    
    if not os.path.exists(old_config_file):
        print(f"错误: 旧配置文件不存在: {old_config_file}")
        return
    
    # 加载旧配置
    yaml = YAML()
    with open(old_config_file, 'r', encoding='utf-8') as f:
        old_config = yaml.load(f)
    
    print("旧配置加载成功，开始迁移...")
    
    # 执行迁移
    migrated_config = migrate_old_config(old_config)
    
    print("\n迁移结果:")
    print("-" * 60)
    
    # 检查关键迁移点
    if 'monitor_settings' in migrated_config:
        ms = migrated_config['monitor_settings']
        print(f"monitor_settings.process_name: {ms.get('process_name')}")
        print(f"monitor_settings.timeout_warning_interval: {ms.get('timeout_warning_interval')}")
        print(f"monitor_settings.monitor_loop_interval: {ms.get('monitor_loop_interval')}")
    
    if 'wait_process_settings' in migrated_config:
        ws = migrated_config['wait_process_settings']
        print(f"\nwait_process_settings.max_wait_time: {ws.get('max_wait_time')}")
        print(f"wait_process_settings.wait_process_check_interval: {ws.get('wait_process_check_interval')}")
    
    if 'push_settings' in migrated_config and 'push_error_retry' in migrated_config['push_settings']:
        pr = migrated_config['push_settings']['push_error_retry']
        print(f"\npush_settings.push_error_retry.retry_interval: {pr.get('retry_interval')}")
    
    print("-" * 60)
    print("迁移测试完成！")

def test_full_load():
    """测试完整的配置加载流程。"""
    print("\n测试完整的配置加载流程...")
    
    # 创建临时配置文件路径
    test_config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_config.yaml')
    
    # 复制旧配置到测试文件
    old_config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dist', 'M9A.yaml')
    
    if not os.path.exists(old_config_file):
        print(f"错误: 旧配置文件不存在: {old_config_file}")
        return
    
    import shutil
    shutil.copy2(old_config_file, test_config_file)
    
    print(f"已创建测试配置文件: {test_config_file}")
    
    try:
        # 加载配置（会自动迁移）
        config = load_config(test_config_file)
        print("配置加载成功！")
        
        # 验证迁移结果
        print("\n验证迁移结果:")
        print(f"monitor_settings.process_name: {config['monitor_settings']['process_name']}")
        print(f"monitor_settings.timeout_warning_interval: {config['monitor_settings']['timeout_warning_interval']}")
        print(f"monitor_settings.monitor_loop_interval: {config['monitor_settings']['monitor_loop_interval']}")
        print(f"wait_process_settings.max_wait_time: {config['wait_process_settings']['max_wait_time']}")
        print(f"wait_process_settings.wait_process_check_interval: {config['wait_process_settings']['wait_process_check_interval']}")
        print(f"push_settings.push_error_retry.retry_interval: {config['push_settings']['push_error_retry']['retry_interval']}")
        
    except Exception as e:
        print(f"错误: {e}")
    finally:
        # 清理测试文件
        if os.path.exists(test_config_file):
            os.remove(test_config_file)
            print(f"\n已清理测试配置文件: {test_config_file}")

if __name__ == '__main__':
    print("开始测试配置迁移功能...")
    print("=" * 60)
    
    test_time_conversion()
    test_config_migration()
    test_full_load()
    
    print("=" * 60)
    print("测试完成！")
