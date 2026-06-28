#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging

# 添加项目根目录到系统路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.config import load_config

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def test_comment_preservation():
    """测试转换后的配置是否保留了正确的注释和结构。"""
    print("测试配置注释和结构保留...")
    
    # 复制旧配置到测试文件
    old_config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dist', 'M9A.yaml')
    test_config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_config_with_comments.yaml')
    
    if not os.path.exists(old_config_file):
        print(f"错误: 旧配置文件不存在: {old_config_file}")
        return
    
    import shutil
    shutil.copy2(old_config_file, test_config_file)
    
    print(f"已创建测试配置文件: {test_config_file}")
    
    try:
        # 加载配置（会自动迁移并保存）
        config = load_config(test_config_file)
        print("配置加载和迁移成功！")
        
        # 读取生成的配置文件内容
        print("\n生成的配置文件内容（前50行）:")
        print("-" * 80)
        
        with open(test_config_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        for i, line in enumerate(lines[:50], 1):
            print(f"{i:2d}: {line.rstrip()}")
        
        print("-" * 80)
        print(f"配置文件总行数: {len(lines)}")
        
        # 检查注释是否存在
        has_comments = any('#' in line for line in lines)
        print(f"\n配置文件是否包含注释: {'✓' if has_comments else '✗'}")
        
        # 检查关键部分是否存在
        has_monitor_settings = any('monitor_settings' in line for line in lines)
        has_process_name = any('process_name:' in line for line in lines)
        has_time_params = any('timeout_warning_interval:' in line for line in lines)
        
        print(f"monitor_settings 部分是否存在: {'✓' if has_monitor_settings else '✗'}")
        print(f"process_name 参数是否存在: {'✓' if has_process_name else '✗'}")
        print(f"timeout_warning_interval 参数是否存在: {'✓' if has_time_params else '✗'}")
        
    except Exception as e:
        print(f"错误: {e}")
    finally:
        # 清理测试文件
        if os.path.exists(test_config_file):
            os.remove(test_config_file)
            print(f"\n已清理测试配置文件: {test_config_file}")

if __name__ == '__main__':
    print("开始测试配置注释和结构保留...")
    print("=" * 80)
    
    test_comment_preservation()
    
    print("=" * 80)
    print("测试完成！")
