#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import argparse
import asyncio
import logging

from modules.config import load_config
from modules.logger import setup_default_logging, setup_logging
from modules.monitor import monitor_processes
from modules.utils import get_program_directory
from modules.version import VERSION, print_info

# 默认配置文件名
DEFAULT_CONFIG_FILE = 'config.yaml'

# 全局变量
CONFIG = {}
LOGGER = logging.getLogger(__name__)


def parse_args():
    """解析命令行参数。

    Returns:
        argparse.Namespace: 命令行参数命名空间。
    """
    LOGGER.debug("解析命令行参数")
    parser = argparse.ArgumentParser(description='2RPM V3')
    parser.add_argument(
        '-c', '-C', '-config', '-Config', '--config', '--Config',
        default=DEFAULT_CONFIG_FILE,
        help="指定配置文件路径，示例 -c C:\\path\\config.yaml"
    )
    args = parser.parse_args()
    LOGGER.info(f"命令行参数解析结果: {args}")
    return args


def main():
    """主函数。

    初始化程序，加载配置，设置日志，并运行主监视器。
    """
    global CONFIG, LOGGER

    print_info()
    
    # 初始化基本日志配置
    setup_default_logging()
    LOGGER = logging.getLogger(__name__)
    LOGGER.info("程序正在初始化...")
    LOGGER.debug(f"版本号: {VERSION}")

    # 解析命令行参数
    args = parse_args()
    # 计算程序根目录（使用当前工作目录）
    program_dir = get_program_directory()
    
    # 处理配置文件路径，自动添加 .yaml 扩展名（如果没有提供）
    config_name = args.config
    if not config_name.endswith('.yaml') and not config_name.endswith('.yml'):
        config_name += '.yaml'
    
    config_file = os.path.join(program_dir, config_name)

    # 加载配置（异常由函数内部处理，未处理异常将升至顶层捕获）
    CONFIG = load_config(config_file)
    LOGGER.debug("配置已加载")

    # 设置日志
    setup_logging(CONFIG, config_file)
    LOGGER.info("已完成日志配置")

    try:
        # 运行主监视器
        LOGGER.info("初始化结束，正在运行主程序")
        asyncio.run(monitor_processes(CONFIG))
        LOGGER.info("主程序已结束运行")
    except KeyboardInterrupt:
        LOGGER.critical("捕捉到 Ctrl+C，程序被手动终止")
        sys.exit(1)
    except Exception as e:
        LOGGER.critical(f"程序出现异常: {e}", exc_info=True)
        sys.exit(1)
    finally:
        print_info()
        sys.exit(0)


if __name__ == '__main__':
    main()