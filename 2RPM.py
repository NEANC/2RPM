#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import argparse
import logging

from modules.config import load_config
from modules.logger import setup_default_logging, setup_logging
from modules.monitor import monitor_processes
from modules.spinner import spinner_phase, notify_fail
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
    LOGGER.info("解析命令行参数")
    parser = argparse.ArgumentParser(description='2RPM V4')
    # 位置参数：支持文件关联或拖拽方式打开配置文件
    parser.add_argument('config_path', nargs='?', default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        '-c', '-C', '-config', '-Config', '--config', '--Config',
        default=None,
        help="指定配置文件路径，示例 -c C:\\path\\config.yaml",
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
    LOGGER.info(f"版本号: {VERSION}")

    # 初始化阶段（参数解析→路径处理→配置加载→日志配置）以 spinner 包裹
    with spinner_phase("程序正在初始化...") as sp:
        # 解析命令行参数
        args = parse_args()
        # 计算程序根目录（使用当前工作目录）
        program_dir = get_program_directory()

        # 配置文件路径优先级：位置参数（文件关联/拖拽） > -c 选项 > 默认值
        if args.config_path is not None:
            config_name = args.config_path
            user_specified = True
        elif args.config is not None:
            config_name = args.config
            user_specified = True
        else:
            config_name = DEFAULT_CONFIG_FILE
            user_specified = False

        # 处理配置文件路径，自动添加 .yaml 扩展名（如果没有提供）
        if not config_name.endswith('.yaml') and not config_name.endswith('.yml'):
            config_name += '.yaml'

        # 绝对路径（拖拽/关联打开）原样使用，相对路径基于程序目录拼接
        if os.path.isabs(config_name):
            config_file = config_name
        else:
            config_file = os.path.join(program_dir, config_name)

        # 加载配置（异常由函数内部处理，未处理异常将升至顶层捕获）
        CONFIG = load_config(config_file, spinner=sp, is_user_specified=user_specified)
        LOGGER.info("配置已加载")

        # 设置日志
        setup_logging(CONFIG, config_file)
        sp.done("程序初始化完成。")

    # 退出码：正常结束为 0，异常或手动终止为 1
    exit_code = 0
    try:
        # 运行主监视器
        LOGGER.info("初始化结束，正在运行主程序")
        monitor_processes(CONFIG)
        LOGGER.info("主程序已结束运行")
    except KeyboardInterrupt:
        notify_fail("捕捉到 Ctrl+C，程序被手动终止")
        exit_code = 1
    except Exception as e:
        notify_fail(f"程序出现异常: {e}", exc_info=True)
        exit_code = 1
    finally:
        sys.exit(exit_code)


if __name__ == '__main__':
    main()