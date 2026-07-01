#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
from ruamel.yaml import YAML

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s | %(asctime)s | %(message)s',
    datefmt='%H:%M:%S'
)
LOGGER = logging.getLogger(__name__)

# 导入配置模块
from modules.config import get_default_config

def test_config_cleaning():
    """测试配置清理逻辑"""
    LOGGER.info("测试配置清理逻辑")
    
    # 加载默认配置
    default_config = get_default_config()
    LOGGER.info("默认配置加载成功")
    
    # 创建一个包含错误配置的测试配置（V4 节名）
    test_config = {
        'monitor': {
            'process_name': 'MaaPiCli.exe',
            'timeout_interval': '45m',
            'loop_interval': '1s',
            # 这些是错误的配置项，应该被清理
            'max_wait': '30s',
            'check_interval': '1s'
        },
        'wait': {
            'max_wait': '30s',
            'check_interval': '1s'
        }
    }
    
    LOGGER.info("原始测试配置:")
    LOGGER.info(f"monitor: {test_config['monitor']}")
    
    # 实现与 config.py 中相同的清理逻辑
    def clean_config(config, default_config):
        """清理配置文件，移除不属于对应配置块的配置项。"""
        keys_to_remove = []
        for key in config:
            if key not in default_config:
                keys_to_remove.append(key)
            elif isinstance(config[key], dict) and isinstance(default_config.get(key), type(default_config)):
                clean_config(config[key], default_config[key])
        for key in keys_to_remove:
            LOGGER.warning(f"移除不属于配置块的配置项: {key}")
            del config[key]
    
    # 清理配置
    clean_config(test_config, default_config)
    
    LOGGER.info("清理后的测试配置:")
    LOGGER.info(f"monitor: {test_config['monitor']}")
    
    # 验证清理是否成功
    monitor_section = test_config['monitor']
    assert 'max_wait' not in monitor_section
    assert 'check_interval' not in monitor_section
    LOGGER.info("配置清理成功!")

if __name__ == '__main__':
    test_config_cleaning()