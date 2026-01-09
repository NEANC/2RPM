#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import colorama

from modules.utils import get_program_directory

LOGGER = logging.getLogger(__name__)


def setup_default_logging():
    """设置默认日志配置"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # 控制台处理器
    console_formatter = logging.Formatter(
        fmt='%(levelname)s | %(asctime)s.%(msecs)03d | %(message)s',
        datefmt='%H:%M:%S'
    )
    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # 文件处理器，日志输出到默认的日志文件
    program_dir = get_program_directory()
    default_log_file = os.path.join(program_dir, 'default.log')
    # 确保目录存在
    os.makedirs(os.path.dirname(default_log_file) or '.', exist_ok=True)
    
    file_formatter = logging.Formatter(
        fmt='%(asctime)s | %(levelname)s | %(message)s'
    )
    file_handler = logging.FileHandler(
        default_log_file, encoding='utf-8'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)


def setup_logging(config):
    """设置日志配置。

    根据配置文件中的设置，初始化日志系统，包括控制台输出和文件输出。

    Args:
        config (dict): 配置信息。
    """
    LOGGER.debug("开始执行函数: setup_logging")
    log_config = config.get('log_settings', {})
    enable_log_file = log_config.get('enable_log_file', False)
    log_level_str = log_config.get('log_level', 'INFO')
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)
    log_dir = log_config.get('log_directory', 'logs')

    # 设置日志目录
    program_dir = get_program_directory()
    LOGGER.debug(f"程序所在目录: {program_dir}")
    log_dir = os.path.join(program_dir, log_dir)
    LOGGER.debug(f"日志目录设置: {log_dir}")

    # 日志格式
    file_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(message)s')

    # 更新日志级别
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    LOGGER.info(f"日志级别设置: {log_level_str.upper()}")

    # 清除之前的处理器
    default_log_file = os.path.join(program_dir, 'default.log')
    
    for handler in root_logger.handlers[:]:
        # 检查是否是文件处理器且指向默认日志文件
        if isinstance(handler, logging.FileHandler) and getattr(handler, 'baseFilename', '') == default_log_file:
            try:
                handler.close()
                LOGGER.debug(f"已关闭默认日志文件处理器: {default_log_file}")
            except Exception as e:
                LOGGER.warning(f"关闭默认日志文件处理器时出错: {e}")
        root_logger.removeHandler(handler)
    LOGGER.debug("已清除之前的日志处理器")

    # 控制台日志处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)

    # 初始化 colorama
    colorama.init()

    class ColorLogFormatter(logging.Formatter):
        """带颜色的日志格式化器"""

        LEVEL_COLORS = {
            'DEBUG': colorama.Fore.CYAN,
            'INFO': colorama.Fore.WHITE,
            'WARNING': colorama.Fore.YELLOW,
            'ERROR': colorama.Fore.RED,
            'CRITICAL': colorama.Back.RED + colorama.Fore.BLACK + colorama.Style.BRIGHT,
        }

        def format(self, record):
            """格式化日志记录。

            Args:
                record (LogRecord): 日志记录对象。

            Returns:
                str: 格式化后的日志字符串。
            """
            level_color = self.LEVEL_COLORS.get(record.levelname, '')
            reset = colorama.Style.RESET_ALL
            message = super().format(record)
            return f"{level_color}{message}{reset}"

    color_formatter = ColorLogFormatter(
        '%(levelname)s | %(asctime)s.%(msecs)03d | %(message)s',
        datefmt='%H:%M:%S'
    )
    console_handler.setFormatter(color_formatter)
    root_logger.addHandler(console_handler)
    LOGGER.info("控制台日志处理器已就绪")

    # 日志文件处理器
    if enable_log_file:
        os.makedirs(log_dir, exist_ok=True)  # 仅在启用日志时创建日志目录

        # 日志文件名
        log_filename = log_config.get('log_filename', '2RPM')
        timestamp = time.strftime('%Y-%m-%d_%H-%M-%S')
        log_file = os.path.join(log_dir, f"{log_filename}_{timestamp}.log")
        LOGGER.info(f"日志文件输出: {log_file}")

        # 合并默认日志文件内容
        if os.path.exists(default_log_file):
            try:
                with open(default_log_file, 'r', encoding='utf-8') as f:
                    default_log_content = f.read()
                if default_log_content:
                    # 将默认日志内容写入新的日志文件开头
                    with open(log_file, 'w', encoding='utf-8') as f:
                        f.write(default_log_content)
                    LOGGER.info("已将初始化日志合并到主日志文件")
                # 尝试删除默认日志文件，最多尝试3次
                max_attempts = 3
                for attempt in range(max_attempts):
                    try:
                        os.remove(default_log_file)
                        LOGGER.info("已删除临时默认日志文件")
                        break
                    except Exception as e:
                        if attempt < max_attempts - 1:
                            LOGGER.warning(f"删除默认日志文件失败，{max_attempts - attempt - 1} 次尝试后重试: {e}")
                            time.sleep(0.5)
                        else:
                            LOGGER.warning(f"处理默认日志文件时出错: {e}")
            except Exception as e:
                LOGGER.warning(f"处理默认日志文件时出错: {e}")

        # 创建文件处理器
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=log_config.get('max_log_files', 15),
            encoding='utf-8'
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(log_level)
        root_logger.addHandler(file_handler)
        LOGGER.info("日志文件处理器已就绪")

        # 日志自清洁
        clean_logs(log_dir, config)
    else:
        # 即使禁用日志文件，也清理默认日志文件
        if os.path.exists(default_log_file):
            try:
                os.remove(default_log_file)
                LOGGER.info("已删除临时默认日志文件")
            except Exception as e:
                LOGGER.warning(f"删除默认日志文件时出错: {e}")
        LOGGER.info("日志文件输出已禁用")


def clean_logs(log_dir, config):
    """清理过期日志文件。

    Args:
        log_dir (str): 日志目录。
        config (dict): 配置信息。
    """
    LOGGER.debug("开始执行函数: clean_logs")
    log_config = config.get('log_settings', {})
    max_days = log_config.get('log_retention_days', 3)
    max_files = log_config.get('max_log_files', 15)
    files = sorted(Path(log_dir).glob('*.log'), key=os.path.getmtime)
    now = time.time()

    # 按天数清理
    for file_path in files:
        if now - file_path.stat().st_mtime > max_days * 86400:
            try:
                file_path.unlink()
                LOGGER.info(f"删除过期的日志文件: {file_path}")
            except Exception as e:
                LOGGER.warning(f"删除日志文件 {file_path} 失败: {e}")

    # 按数量清理
    if len(files) > max_files:
        for file_path in files[:len(files) - max_files]:
            try:
                file_path.unlink()
                LOGGER.info(f"删除多余的日志文件: {file_path}")
            except Exception as e:
                LOGGER.warning(f"删除日志文件 {file_path} 失败: {e}")