#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess

def test_config_file_extension():
    """测试配置文件扩展名自动处理功能。"""
    print("测试配置文件扩展名自动处理功能...")
    print("=" * 80)
    
    # 测试用例
    test_cases = [
        ("M9A", "M9A.yaml"),
        ("config", "config.yaml"),
        ("test.yml", "test.yml"),  # 已经有扩展名，不应修改
        ("existing_config.yaml", "existing_config.yaml")  # 已经有扩展名，不应修改
    ]
    
    for input_name, expected in test_cases:
        print(f"\n测试用例: -c {input_name}")
        print(f"期望结果: {expected}")
        
        # 运行命令并捕获输出
        result = subprocess.run(
            [sys.executable, "2RPM.py", "-c", input_name],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        
        # 检查输出
        output = result.stderr + result.stdout
        if "正在加载配置文件:" in output:
            # 提取加载的配置文件路径
            for line in output.split('\n'):
                if "正在加载配置文件:" in line:
                    loaded_file = line.split(':', 1)[1].strip()
                    loaded_filename = os.path.basename(loaded_file)
                    print(f"实际加载: {loaded_filename}")
                    
                    if loaded_filename == expected:
                        print("✓ 测试通过!")
                    else:
                        print(f"✗ 测试失败! 期望 {expected}, 实际 {loaded_filename}")
                    break
        else:
            print(f"✗ 测试失败! 无法从输出中提取配置文件路径")
            print(f"输出: {output[:200]}...")
    
    print("=" * 80)
    print("测试完成!")

if __name__ == '__main__':
    test_config_file_extension()
