#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
# Running-Runtime Process Monitoring

    - 本项目使用 GPT AI 生成，GPT 模型: o1-preview
    - 本项目使用 Claude AI 生成，Claude 模型: claude-3-5-sonnet

- 版本: v2.15.1

## License

            DO WHAT THE FUCK YOU WANT TO PUBLIC LICENSE
                    Version 2, December 2004

 Copyright (C) 2004 Sam Hocevar <sam@hocevar.net>

 Everyone is permitted to copy and distribute verbatim or modified
 copies of this license document, and changing it is allowed as long
 as the name is changed.

            DO WHAT THE FUCK YOU WANT TO PUBLIC LICENSE
   TERMS AND CONDITIONS FOR COPYING, DISTRIBUTION AND MODIFICATION

  0. You just DO WHAT THE FUCK YOU WANT TO.

"""

import os
import sys
import time
import psutil
import socket
import ctypes
import logging
import asyncio
import argparse
import datetime
import platform
import colorama
import urllib.parse
import urllib.request
from pathlib import Path
from onepush import get_notifier
from colorama import Back, Fore, Style
from logging.handlers import RotatingFileHandler

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

# 全局变量
CONFIG = {}
LOGGER = logging.getLogger(__name__)

# 默认配置文件名
DEFAULT_CONFIG_FILE = 'config.yaml'

# 关键参数列表
CRITICAL_KEYS = [
    'monitor_settings.process_name_list',
    'push_settings.push_channel_settings.choose',
    'external_program_settings.enable_external_program_call',
    'external_program_settings.trigger_process_name',
    'external_program_settings.external_program_path',
]


def get_default_config():
    """获取默认配置，并嵌入完整的注释。

    Returns:
        CommentedMap: 包含注释的默认配置字典。
    """
    LOGGER.debug("正在读取默认配置")
    config = CommentedMap({
        'monitor_settings': CommentedMap({
            'process_name_list': CommentedSeq([
                'notepad.exe',
                'calc.exe'
            ]),
            'timeout_warning_interval_ms': 900000,
            'monitor_loop_interval_ms': 1000
        }),
        'wait_process_settings': CommentedMap({
            'max_wait_time_ms': 30000,
            'wait_process_check_interval_ms': 1000
        }),
        'push_settings': CommentedMap({
            'push_templates': CommentedMap({
                'process_end_notification': CommentedMap({
                    'enable': True,
                    'title': '进程结束通报',
                    'content': (
                        '主机: {host_name}\n\n'
                        '当前时间: {current_time}\n\n'
                        '进程: {process_name} (PID: {process_pid}) '
                        '于 {short_current_time} 时已结束运行\n\n'
                        '运行时间: {process_run_time}\n\n'
                        '正在运行的进程状态: {other_running_processes}\n\n'
                        '其他未运行的进程: {process_list}\n\n'
                    )
                }),
                'process_timeout_warning': CommentedMap({
                    'enable': True,
                    'title': '进程超时运行警告',
                    'content': (
                        '主机: {host_name}\n\n'
                        '当前时间: {current_time}\n\n'
                        '进程: {process_name} (PID: {process_pid}) '
                        '于 {short_current_time} 时已运行超过预期: '
                        '{process_run_time}\n\n'
                        '正在运行的进程状态: {other_running_processes}\n\n'
                        '其他未运行的进程: {process_list}\n\n'
                    )
                }),
                'process_wait_timeout_warning': CommentedMap({
                    'enable': True,
                    'title': '等待超时未运行报告',
                    'content': (
                        '主机: {host_name}\n\n'
                        '当前时间: {current_time}\n\n'
                        '进程: {process_name} 超时未运行\n\n'
                        '已等待时间: {process_wait_time}\n\n'
                        '正在运行的进程状态: {other_running_processes}\n\n'
                        '其他未运行的进程: {process_list}\n\n'
                    )
                })
            }),
            'push_channel_settings': CommentedMap({
                'choose': 'ServerChan',
                'serverchan_key': '',
                'push_channel': None,
                'push_channel_key': ''
            }),
            'push_error_retry': CommentedMap({
                'retry_interval_ms': 3000,
                'max_retry_count': 3
            })
        }),
        'external_program_settings': CommentedMap({
            'enable_external_program_call': False,
            'trigger_process_name': 'notepad.exe',
            'external_program_path': 'C:\\path\\to\\your\\script.bat'
        }),
        'log_settings': CommentedMap({
            'enable_log_file': False,
            'log_level': 'INFO',
            'log_directory': 'logs',
            'max_log_files': 15,
            'log_retention_days': 3,
            'log_filename': '2RPM'
        })
    })

    LOGGER.debug("正在为配置文件添加注释")
    # 添加注释到 'monitor_settings'
    config['monitor_settings'].yaml_set_comment_before_after_key(
        'process_name_list',
        before=(
            "监视设置\n\n"
            "要监视的进程名称列表"
        )
    )
    config['monitor_settings'].yaml_set_comment_before_after_key(
        'timeout_warning_interval_ms',
        before=(
            "\n超时警告间隔，默认值900000毫秒（15分钟），单位为毫秒\n"
            "时间换算可通过搜索引擎搜索: 分钟换毫秒工具，也可查看下列公式: \n"
            "分钟换算毫秒：分钟×60×1000；例 30分钟换算成毫秒：30×60×1000=1800000"
        )
    )
    config['monitor_settings'].yaml_set_comment_before_after_key(
        'monitor_loop_interval_ms',
        before=(
            "\n监视循环间隔，默认值1000毫秒（1秒），单位为毫秒\n"
            "时间换算可通过搜索引擎搜索: 秒换毫秒工具，也可查看下列公式: \n"
            "秒换算毫秒：秒×1000；例 15秒换算成毫秒：15×1000=15000"
        )
    )

    # 添加注释到 'wait_process_settings'
    config['wait_process_settings'].yaml_set_comment_before_after_key(
        'max_wait_time_ms',
        before=(
            "等待进程设置\n\n"
            "最长等待时间，默认值: 30000毫秒（30秒），单位为毫秒\n"
        )
    )
    config['wait_process_settings'].yaml_set_comment_before_after_key(
        'wait_process_check_interval_ms',
        before=(
            "\n等待进程检查间隔，默认值1000毫秒（1秒），单位为毫秒。\n"
            "时间换算可通过搜索引擎搜索: 秒换毫秒工具，也可查看下列公式: \n"
            "秒换算毫秒：秒×1000；例 15秒换算成毫秒：15×1000=15000"
        )
    )

    # 添加注释到 'push_settings'
    config['push_settings'].yaml_set_comment_before_after_key(
        'push_templates',
        before=(
            "推送设置\n\n"
            "支持的变量如下：\n"
            "主机名: {host_name}\n"
            "当前时间(YY-mm-dd HH-MM-SS): {current_time}\n"
            "当前时间(HH-MM-SS): {short_current_time}\n"
            "进程名: {process_name}\n"
            "进程PID: {process_pid}\n"
            "进程运行时间: {process_run_time}\n"
            "进程积累等待时间: {process_wait_time}\n"
            "正在运行的进程状态列表: {other_running_processes}\n"
            "进程列表: {process_list}\n\n"
            "推送模板配置，可自定义通知的标题和内容\n"
        )
    )

    # 添加注释到 'push_templates'
    push_templates = config['push_settings']['push_templates']
    push_templates.yaml_set_comment_before_after_key(
        'process_end_notification',
        before=(
            "\n进程结束通知模板\n"
            "- enable: 是否启用通知，True 为启用，False 为禁用\n"
            "- title: 通知标题\n"
            "- content: 通知内容\n"
        )
    )
    push_templates.yaml_set_comment_before_after_key(
        'process_timeout_warning',
        before=(
            "\n进程超时运行警告模板\n"
            "- enable: 是否启用通知，True 为启用，False 为禁用\n"
            "- title: 通知标题\n"
            "- content: 通知内容\n"
        )
    )
    push_templates.yaml_set_comment_before_after_key(
        'process_wait_timeout_warning',
        before=(
            "\n等待超时未运行报告模板\n"
            "- enable: 是否启用通知，True 为启用，False 为禁用\n"
            "- title: 通知标题\n"
            "- content: 通知内容\n"
        )
    )

    # 添加注释到 'push_channel_settings'
    push_channel_settings = config['push_settings']['push_channel_settings']
    push_channel_settings.yaml_set_comment_before_after_key(
        'choose',
        before=(
            "推送通道设置\n\n"
            "请选择 'ServerChan' 或者 'OnePush' 进行推送，默认为 'ServerChan'。"
        )
    )
    push_channel_settings.yaml_set_comment_before_after_key(
        'serverchan_key',
        before="\nServerChan密钥"
    )
    push_channel_settings.yaml_set_comment_before_after_key(
        'push_channel',
        before=(
            "\nOnePush推送通道（请查看 https://pypi.org/project/onepush/ "
            "来获得如何使用帮助）"
        )
    )
    push_channel_settings.yaml_set_comment_before_after_key(
        'push_channel_key',
        before="\nOnePush推送通道密钥"
    )

    # 添加注释到 'push_error_retry'
    push_error_retry = config['push_settings']['push_error_retry']
    push_error_retry.yaml_set_comment_before_after_key(
        'retry_interval_ms',
        before=(
            "推送错误重试设置\n\n"
            "重试间隔，默认值: 3000毫秒（3秒）单位为毫秒"
        )
    )
    push_error_retry.yaml_set_comment_before_after_key(
        'max_retry_count',
        before=(
            "\n最大重试次数，默认值: 3次"
        )
    )

    # 添加注释到 'external_program_settings'
    config['external_program_settings'].yaml_set_comment_before_after_key(
        'enable_external_program_call',
        before=(
            "外部程序调用设置\n\n"
            "是否执行外部程序调用，False 为 不执行，True 为 执行，默认为 False\n"
        )
    )
    config['external_program_settings'].yaml_set_comment_before_after_key(
        'trigger_process_name',
        before=(
            "\n指定当哪一个进程结束后触发外部程序调用，例如: notepad.exe\n"
            "当程序检测到 'notepad.exe' 结束运行时，会执行外部程序调用"
        )
    )
    config['external_program_settings'].yaml_set_comment_before_after_key(
        'external_program_path',
        before=(
            "\n需要调用的外部程序/BAT脚本的详细路径，例如: "
            "C:\\path\\to\\your\\script.bat"
        )
    )

    # 添加注释到 'log_settings'
    config['log_settings'].yaml_set_comment_before_after_key(
        'enable_log_file',
        before=(
            "日志设置\n\n"
            "是否输出日志文件，默认为 False\n"
            "False 为 不输出，True 为 输出"
        )
    )
    config['log_settings'].yaml_set_comment_before_after_key(
        'log_level',
        before=(
            "\n日志输出等级，默认为 INFO\n"
            "请根据需要进行设置，否则不建议改动\n"
            "DEBUG > INFO > WARNING > ERROR > CRITICAL"
        )
    )
    config['log_settings'].yaml_set_comment_before_after_key(
        'log_directory',
        before=(
            "\n日志输出的目录，默认为程序目录下的 'logs' 文件夹\n"
            "请根据需要进行设置，例如: C:\\Path\\2RPM\\v2\\logs\n"
            "若不需要日志文件输出，请在 'enable_log_file' 中设置为 False\n"
        )
    )
    config['log_settings'].yaml_set_comment_before_after_key(
        'max_log_files',
        before=(
            "\n日志最大保存数量，默认值: 15个"
        )
    )
    config['log_settings'].yaml_set_comment_before_after_key(
        'log_retention_days',
        before=(
            "\n日志保存天数，单位为天，默认值: 3天"
        )
    )
    config['log_settings'].yaml_set_comment_before_after_key(
        'log_filename',
        before=(
            "\n日志文件名，时间戳不可修改，默认值: 2RPM"
        )
    )

    LOGGER.debug("内置配置读取完成")
    return config


def create_default_config(config_file):
    """创建默认配置文件。

    Args:
        config_file (str): 配置文件路径。

    Raises:
        Exception: 如果无法创建配置文件。
    """
    LOGGER.info(f"正在创建配置文件: {os.path.abspath(config_file)}")
    default_config = get_default_config()
    try:
        yaml = YAML()
        yaml.indent(mapping=2, sequence=4, offset=2)
        yaml.preserve_quotes = True
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(default_config, f)
        LOGGER.info("配置文件创建成功")
    except Exception as e:
        LOGGER.critical(f"创建配置文件失败: {e}")
        raise


def load_config(config_file):
    """加载配置文件。

    Args:
        config_file (str): 配置文件路径。

    Returns:
        dict: 配置字典。

    Raises:
        Exception: 如果配置文件无效。
    """
    LOGGER.info(f"正在加载配置文件: {os.path.abspath(config_file)}")
    if not os.path.exists(config_file):
        LOGGER.warning(
            f"配置文件不存在: {os.path.abspath(config_file)}")
        create_default_config(config_file)

    try:
        yaml = YAML()
        with open(config_file, 'r', encoding='utf-8') as f:
            user_config = yaml.load(f)
            LOGGER.info(f"成功加载配置文件: {os.path.abspath(config_file)}")
    except Exception as e:
        LOGGER.critical(f"无法加载配置文件: {os.path.abspath(config_file)}: {e}")
        raise

    # 检查并更新配置
    default_config = get_default_config()
    updated = check_and_update_config(user_config, default_config)
    if updated:
        LOGGER.debug("配置文件已更新，正在写回配置文件。")
        try:
            yaml = YAML()
            yaml.indent(mapping=2, sequence=4, offset=2)
            yaml.preserve_quotes = True
            with open(config_file, 'w', encoding='utf-8') as f:
                yaml.dump(user_config, f)
            LOGGER.info(f"已更新配置文件: {os.path.abspath(config_file)}")
        except Exception as e:
            LOGGER.error(
                f"无法写回配置文件 {os.path.abspath(config_file)}: {e}"
            )

    LOGGER.debug("配置参数版本差异检查完成")
    return user_config


def update_comments(user_config, default_config, key):
    """更新配置参数的注释。

    Args:
        user_config (CommentedMap): 用户配置字典。
        default_config (CommentedMap): 默认配置字典。
        key (str): 当前键名。
    """
    if default_config.ca.items.get(key):
        before_comment_token = default_config.ca.items[key][0]
        if before_comment_token:
            before_comment = before_comment_token.value
            user_config.yaml_set_comment_before_after_key(
                key, before=before_comment
            )
        eol_comment_token = default_config.ca.items[key][2]
        if eol_comment_token:
            eol_comment = eol_comment_token.value.strip()
            user_config.yaml_add_eol_comment(eol_comment, key)


def check_and_update_config(user_config, default_config, parent_key=''):
    """检查并更新用户配置，添加缺失的参数，并更新注释。

    Args:
        user_config (CommentedMap): 用户配置字典。
        default_config (CommentedMap): 默认配置字典。
        parent_key (str): 父级键，用于递归。

    Returns:
        bool: 是否有更新。
    """
    LOGGER.debug(f"正在检查配置信息，parent_key: {parent_key}")
    updated = False
    for key, default_value in default_config.items():
        full_key = f"{parent_key}.{key}" if parent_key else key
        LOGGER.debug(f"检查关键配置信息: {full_key}")
        if key not in user_config:
            if full_key in CRITICAL_KEYS:
                LOGGER.critical(f"关键参数缺失: {full_key}")
                sys.exit(1)
            else:
                LOGGER.warning(f"参数缺失: {full_key}")
                user_config[key] = default_value
                updated = True
                LOGGER.info(
                    f"正在对参数 {full_key}，应用默认值: {default_value}"
                )
        else:
            if isinstance(default_value, CommentedMap):
                if not isinstance(user_config[key], CommentedMap):
                    LOGGER.warning(f"参数类型不匹配: {full_key}")
                    user_config[key] = default_value
                    updated = True
                    LOGGER.info(f"使用默认值更新参数: {full_key}")
                else:
                    nested_updated = check_and_update_config(
                        user_config[key], default_value, full_key)
                    if nested_updated:
                        updated = True
        # 更新注释
        update_comments(user_config, default_config, key)
    LOGGER.debug(f"配置参数检查完成，正在更新: {updated}")
    return updated


def setup_default_logging():
    """在加载配置文件前设置默认的日志配置。"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        format='%(levelname)s | %(asctime)s.%(msecs)03d | %(message)s',
        datefmt='%H:%M:%S'
    )
    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件处理器，日志输出到默认的日志文件
    program_dir = get_program_directory()
    default_log_file = os.path.join(program_dir, 'default.log')
    file_handler = logging.FileHandler(
        default_log_file, encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def setup_logging():
    """设置日志配置。

    根据配置文件中的设置，初始化日志系统，包括控制台输出和文件输出。
    """
    global LOGGER
    LOGGER.debug("开始执行函数: 日志配置")
    log_config = CONFIG.get('log_settings', {})
    enable_log_file = log_config.get('enable_log_file', False)
    log_level_str = log_config.get('log_level', 'INFO')
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)
    log_dir = log_config.get('log_directory', 'logs')

    # 设置日志目录为程序所在目录下的 logs 文件夹
    program_dir = get_program_directory()
    LOGGER.debug(f"读取程序所在目录: {program_dir}")
    log_dir = os.path.join(program_dir, log_dir)
    LOGGER.debug(f"日志目录设置: {log_dir}")

    # 日志格式
    file_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(message)s')
    console_formatter = logging.Formatter(
        '%(levelname)s | %(asctime)s.%(msecs)03d | %(message)s',
        datefmt='%H:%M:%S')

    # 更新日志级别
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    LOGGER.info(f"日志级别设置: {log_level_str.upper()}")

    # 清除之前的处理器
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    LOGGER.debug("已清除之前的日志处理器")

    # 控制台日志处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)

    if colorama:
        # 初始化 colorama
        colorama.init()

        class ColorLogFormatter(logging.Formatter):
            """带颜色的日志格式化器。"""

            LEVEL_COLORS = {
                'DEBUG': Fore.CYAN,
                'INFO': Fore.WHITE,
                'WARNING': Fore.YELLOW,
                'ERROR': Fore.RED,
                'CRITICAL': Back.RED + Fore.BLACK + Style.BRIGHT,
            }

            def format(self, record):
                """格式化日志记录。

                Args:
                    record: 日志记录对象。

                Returns:
                    str: 格式化后的日志字符串。
                """
                level_color = self.LEVEL_COLORS.get(record.levelname, '')
                reset = Style.RESET_ALL
                message = super().format(record)
                return f"{level_color}{message}{reset}"

        color_formatter = ColorLogFormatter(
            '%(levelname)s | %(asctime)s.%(msecs)03d | %(message)s',
            datefmt='%H:%M:%S'
        )
        console_handler.setFormatter(color_formatter)
        LOGGER.info("控制台输出已渲染颜色")
    else:
        console_handler.setFormatter(console_formatter)
        LOGGER.warning("colorama 未安装，将使用无颜色渲染的控制台输出")

    root_logger.addHandler(console_handler)
    LOGGER.info("控制台日志处理器已就绪")

    # 日志文件处理器
    if enable_log_file:
        os.makedirs(log_dir, exist_ok=True)  # 仅在启用日志时创建日志目录

        # 日志文件名
        log_filename = log_config.get('log_filename', '2RPM')
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        log_file = os.path.join(log_dir, f"{log_filename}_{timestamp}.log")
        LOGGER.info(f"日志文件输出: {log_file}")

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=log_config.get('max_log_files', 15),
            encoding='utf-8'
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(log_level)
        LOGGER.addHandler(file_handler)
        LOGGER.info("日志文件处理器已就绪")

        # 日志自清洁
        clean_logs(log_dir)
    else:
        LOGGER.info("日志文件输出已禁用")


def get_program_directory():
    """获取程序所在的目录，兼容打包后的可执行文件。

    Returns:
        str: 程序所在的目录路径。
    """
    if getattr(sys, 'frozen', False):
        # 如果是被打包的可执行文件
        program_dir = os.path.dirname(sys.executable)
    else:
        # 普通的 Python 脚本
        program_dir = os.path.dirname(os.path.abspath(__file__))
    return program_dir


def clean_logs(log_dir):
    """清理过期日志文件。

    Args:
        log_dir (str): 日志目录。
    """
    LOGGER.debug("开始执行函数: 清理日志文件")
    log_config = CONFIG.get('log_settings', {})
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

    LOGGER.debug("日志清理完成")


def format_time_ns(nanoseconds):
    """将纳秒转换为 HH:MM:SS.SSS 格式的字符串。

    Args:
        nanoseconds (int): 纳秒数。

    Returns:
        str: 格式化后的时间字符串。
    """
    LOGGER.debug(f"格式化时间: {nanoseconds} 纳秒")
    total_seconds = nanoseconds // 1_000_000_000
    millis = (nanoseconds // 1_000_000) % 1000
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    formatted_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    LOGGER.debug(f"格式化后的时间: {formatted_time}")
    return formatted_time


async def monitor_processes():
    """监视进程列表。

    等待指定的进程启动，监视其运行状态，并在进程结束或超时时发送通知。
    """
    LOGGER.debug("开始执行函数: 监视进程列表")
    monitor_settings = CONFIG.get('monitor_settings', {})
    wait_settings = CONFIG.get('wait_process_settings', {})
    external_settings = CONFIG.get('external_program_settings', {})

    process_name_list = monitor_settings.get('process_name_list', [])
    timeout_warning_interval_ms = monitor_settings.get(
        'timeout_warning_interval_ms', 900000)
    monitor_loop_interval_ms = monitor_settings.get(
        'monitor_loop_interval_ms', 1000)

    max_wait_time_ms = wait_settings.get('max_wait_time_ms', 30000)
    wait_process_check_interval_ms = wait_settings.get(
        'wait_process_check_interval_ms', 1000)

    # 外部程序调用设置
    enable_external_program_call = external_settings.get(
        'enable_external_program_call', False)
    trigger_process_name = external_settings.get('trigger_process_name', '')
    external_program_path = external_settings.get('external_program_path', '')

    LOGGER.debug("初始化监视参数")
    processes = {}

    # 检查 process_name_list 是否有效
    if not process_name_list:
        LOGGER.critical("未设置要监视的进程，请检查配置文件！")
        sys.exit(1)

    LOGGER.info(
        f"等待监视进程启动，每 {wait_process_check_interval_ms} ms 检查一次"
    )
    start_time_ns = time.perf_counter_ns()

    try:
        # 等待进程启动
        while True:
            LOGGER.debug("正在执行: 等待进程启动循环")
            waited_time_ns = time.perf_counter_ns() - start_time_ns
            if waited_time_ns // 1_000_000 > max_wait_time_ms:
                LOGGER.debug("已等待超时，正在尝试发送通知")
                break

            current_processes = {
                p.pid: p.info for p in psutil.process_iter(
                    ['pid', 'name', 'create_time'])
            }
            # LOGGER.debug(f"当前进程列表: {current_processes}")
            found_processes = {
                pid: info for pid, info in current_processes.items()
                if info['name'] in process_name_list
            }

            for pid, info in found_processes.items():
                if pid not in processes:
                    start_time_offset_ns = time.perf_counter_ns() - int(
                        (time.time() - info['create_time']) * 1_000_000_000
                    )
                    processes[pid] = {
                        'name': info['name'],
                        'start_time_ns': start_time_offset_ns,
                        'last_warning_time_ns': start_time_offset_ns,
                    }
                    LOGGER.info(f"检测到进程启动: {info['name']} (PID: {pid})")

            # 检查是否所有进程都已启动
            running_process_names = [
                info['name']
                for info in processes.values()
            ]

            missing_processes = (
                set(process_name_list) - set(running_process_names)
            )

            if not missing_processes:
                LOGGER.info("所有目标监视进程已启动")
                break
            else:
                LOGGER.info(
                    f"正在等待目标进程运行，已等待时间: "
                    f"{format_time_ns(waited_time_ns)}"
                )
                await asyncio.sleep(wait_process_check_interval_ms / 1000)
    except asyncio.CancelledError:
        LOGGER.critical("任务被取消，退出等待进程启动循环")
        return

    # 超过等待时间或所有进程已启动
    if missing_processes:
        # 超过等待时间且有进程未启动
        LOGGER.debug("正在执行: 等待进程启动超时报告与推送")
        waited_time_ns = time.perf_counter_ns() - start_time_ns
        formatted_waited_time = format_time_ns(waited_time_ns)
        for process_name in missing_processes:
            await send_notification(
                'process_wait_timeout_warning',
                process_name=process_name,
                process_wait_time=formatted_waited_time,
                other_running_processes=get_other_running_processes(
                    processes),
                process_list=list(missing_processes)
            )
            LOGGER.error(f"等待超时，进程未运行: {process_name}")
    else:
        LOGGER.info("所有监视进程均已启动")

    # 如果没有任何进程需要监视，退出程序
    if not processes:
        LOGGER.critical("未检测到任意目标进程，程序终止运行。")
        sys.exit(1)

    # 监视已启动的进程
    LOGGER.info(
        f"已进入监视循环，每 {monitor_loop_interval_ms} ms 循环一次"
    )
    next_loop_time_ns = time.perf_counter_ns()
    try:
        while processes:
            LOGGER.debug("正在执行: 监视循环")
            current_time_ns = time.perf_counter_ns()
            current_processes = {
                p.pid: p.info for p in psutil.process_iter(
                    ['pid', 'name', 'create_time'])
            }
            current_pids = set(current_processes.keys())
            monitored_pids = set(processes.keys())
            # LOGGER.debug(f"当前 PID 集合：{current_pids}")
            LOGGER.debug(f"当前监视的 PID 集合：{monitored_pids}")

            # 检查进程结束
            ended_pids = monitored_pids - current_pids
            # LOGGER.debug(f"已结束的 PID：{ended_pids}")
            for pid in ended_pids:
                process_info = processes[pid]
                process_name = process_info['name']
                run_time_ns = current_time_ns - process_info['start_time_ns']
                formatted_run_time = format_time_ns(run_time_ns)

                await send_notification(
                    'process_end_notification',
                    process_name=process_name,
                    process_pid=pid,
                    process_run_time=formatted_run_time,
                    other_running_processes=get_other_running_processes(
                        processes, exclude_pid=pid),
                    process_list=get_missing_processes(
                        process_name_list, processes, exclude_pid=pid)
                )
                LOGGER.info(
                    f"进程结束: {process_name} (PID: {pid})"
                    f"运行时间: {formatted_run_time}"
                )

                # 如果启用了外部程序调用，并且结束的进程是指定的触发进程
                if (enable_external_program_call and
                        process_name == trigger_process_name):
                    LOGGER.info(
                        f"检测到进程 {process_name} 结束，正在执行外部程序..."
                    )
                    try:
                        run_external_program(external_program_path)
                        LOGGER.info(f"外部程序 {external_program_path} 执行成功")
                    except Exception as e:
                        LOGGER.error(
                            f"执行外部程序 {external_program_path} 时发生错误: {e}",
                            exc_info=True
                        )

                # 从监视列表中移除
                del processes[pid]
                LOGGER.info(f"已删除进程记录: {pid}")

            # 更新运行时间，检查超时警告
            for pid, process_info in processes.items():
                run_time_ns = current_time_ns - process_info['start_time_ns']
                last_warning_time_ns = process_info.get(
                    'last_warning_time_ns', process_info['start_time_ns'])
                time_since_last_warning_ns = (
                    current_time_ns - last_warning_time_ns
                )

                if time_since_last_warning_ns >= (
                    timeout_warning_interval_ms * 1_000_000
                ):
                    formatted_run_time = format_time_ns(run_time_ns)
                    await send_notification(
                        'process_timeout_warning',
                        process_name=process_info['name'],
                        process_pid=pid,
                        process_run_time=formatted_run_time,
                        other_running_processes=get_other_running_processes(
                            processes, exclude_pid=pid),
                        process_list=get_missing_processes(
                            process_name_list, processes, exclude_pid=pid)
                    )
                    LOGGER.warning(
                        f"进程 {process_info['name']} (PID: {pid}) "
                        f"已运行超时 {formatted_run_time}"
                    )
                    process_info['last_warning_time_ns'] = current_time_ns

            if not processes:
                LOGGER.info("所有进程已结束，程序终止运行。")
                break

            # 计算下一次循环的时间点，确保循环间隔精确
            now_ns = time.perf_counter_ns()
            sleep_time_ns = next_loop_time_ns - now_ns
            if sleep_time_ns < 0:
                sleep_time_ns = 0
                next_loop_time_ns = now_ns
            await asyncio.sleep(sleep_time_ns / 1_000_000_000)
            next_loop_time_ns += monitor_loop_interval_ms * 1_000_000
    except asyncio.CancelledError:
        LOGGER.critical("任务被取消，正在结束监视循环")
        return


def get_other_running_processes(processes, exclude_pid=None):
    """获取其他正在运行的进程状态。

    Args:
        processes (dict): 当前监视的进程。
        exclude_pid (int, optional): 要排除的进程PID。默认为 None。

    Returns:
        str: 其他正在运行的进程状态。
    """
    LOGGER.debug("正在获取: 其他正在运行的进程状态")
    processes_info = []
    current_time_ns = time.perf_counter_ns()
    for pid, info in processes.items():
        if pid != exclude_pid:
            run_time_ns = current_time_ns - info['start_time_ns']
            formatted_run_time = format_time_ns(run_time_ns)
            processes_info.append(
                f"{info['name']} (PID: {pid}, 运行时间: {formatted_run_time})"
            )
    result = '\n'.join(processes_info) if processes_info else '无'
    LOGGER.info(f"其他正在运行的进程状态: {result}")
    return result


def get_missing_processes(process_name_list, processes, exclude_pid=None):
    """获取未运行的进程列表。

    Args:
        process_name_list (list): 配置的进程名称列表。
        processes (dict): 当前监视的进程。
        exclude_pid (int, optional): 要排除的进程PID对应的进程名称。默认为 None。

    Returns:
        list: 未运行的进程列表。
    """
    LOGGER.debug("正在获取: 未运行的进程列表")
    running_process_names = [
        info['name'] for pid, info in processes.items() if pid != exclude_pid
    ]
    missing_processes = set(process_name_list) - set(running_process_names)
    result = list(missing_processes)
    LOGGER.info(f"未运行的进程列表: {result}")
    return result


async def send_notification(template_key, **kwargs):
    """发送通知。

    Args:
        template_key (str): 模板键。
        **kwargs: 模板中使用的变量。
    """
    LOGGER.info(f"使用模板: {template_key}推送报告")
    push_settings = CONFIG.get('push_settings', {})
    templates = push_settings.get('push_templates', {})
    template = templates.get(template_key, {})

    # 检查是否启用了该通知
    if not template.get('enable', True):
        LOGGER.warning(f"通知推送已被禁用: {template_key}")
        return

    title = template.get('title', '')
    content = template.get('content', '')

    # 获取主机名和时间
    host_name = socket.gethostname()
    current_time = datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S')
    short_current_time = datetime.datetime.now().strftime('%H:%M:%S')

    # 格式化内容
    content = content.format(
        host_name=host_name,
        current_time=current_time,
        short_current_time=short_current_time,
        **kwargs
    )
    LOGGER.info(f"通知内容: {content}")

    # 选择推送渠道
    push_channel_settings = push_settings.get('push_channel_settings', {})
    channel = push_channel_settings.get('choose', 'ServerChan')
    max_retry_count = push_settings.get('push_error_retry', {}).get(
        'max_retry_count', 3)
    retry_interval_ms = push_settings.get('push_error_retry', {}).get(
        'retry_interval_ms', 3000)

    LOGGER.info(f"推送通道: {channel}")
    for attempt in range(max_retry_count):
        try:
            if channel == 'ServerChan':
                key = push_channel_settings.get('serverchan_key', '')
                if not key:
                    LOGGER.error("ServerChan密钥未配置，无法发送通知")
                    break
                await asyncio.to_thread(sc_send, title, content, key)
            elif channel == 'OnePush':
                notifier_name = push_channel_settings.get('push_channel', '')
                token = push_channel_settings.get('push_channel_key', '')
                if not notifier_name or not token:
                    LOGGER.error("OnePush推送渠道或密钥未配置，无法发送通知")
                    break
                await asyncio.to_thread(
                    onepush_send, notifier_name, token, title, content)
            LOGGER.info(f"通知发送成功: {title}")
            break
        except Exception as e:
            LOGGER.error(
                f"通知发送失败 (尝试 {attempt + 1}/{max_retry_count}): {e}")
            await asyncio.sleep(retry_interval_ms / 1000)
    else:
        LOGGER.critical("通知发送失败，已达到最大重试次数。")
        sys.exit(1)


def sc_send(text, desp='', key=''):
    """ServerChan消息发送处理函数。

    Args:
        text (str): 消息标题。
        desp (str): 消息内容。
        key (str): ServerChan密钥。

    Returns:
        str: ServerChan 返回的结果。
    """
    LOGGER.info("调用 ServerChan 发送通知")
    postdata = urllib.parse.urlencode({'text': text, 'desp': desp}).encode(
        'utf-8')
    url = f'https://sctapi.ftqq.com/{key}.send'
    req = urllib.request.Request(url, data=postdata, method='POST')
    with urllib.request.urlopen(req) as response:
        result = response.read().decode('utf-8')
    LOGGER.info(f"返回结果: {result}")
    return result


def onepush_send(notifier_name, token, title, content):
    """OnePush消息发送处理函数。

    Args:
        notifier_name (str): 推送渠道名称。
        token (str): 推送渠道密钥。
        title (str): 消息标题。
        content (str): 消息内容。

    Returns:
        str: OnePush 返回的结果。
    """
    LOGGER.info(f"调用 OnePush ({notifier_name}) 发送通知")
    notifier = get_notifier(notifier_name)
    response = notifier.notify(token=token, title=title, content=content)
    LOGGER.info(f"返回结果: {response.text}")
    return response.text


def run_external_program(program_path):
    """执行外部程序，处理可能的异常和平台差异。

    Args:
        program_path (str): 外部程序的路径。

    Raises:
        FileNotFoundError: 当外部程序不存在时抛出。
        Exception: 执行外部程序时发生的其他异常。
    """
    LOGGER.info(f"正在检查外部程序是否存在: {program_path}")
    if not os.path.exists(program_path):
        LOGGER.error(f"外部程序不存在: {program_path}")
        raise FileNotFoundError(f"外部程序不存在: {program_path}")

    if platform.system() == 'Windows':
        LOGGER.info("已确认当前操作系统为 Windows")
        try:
            result = ctypes.windll.shell32.ShellExecuteW(
                None, 'open', program_path, None, None, 1
            )
            if result <= 32:
                error_message = f"ShellExecuteW 执行失败，错误码：{result}"
                LOGGER.error(error_message)
                raise Exception(error_message)
            LOGGER.info("外部程序已启动")
        except Exception as e:
            LOGGER.error(
                f"执行外部程序 {program_path} 时发生错误：{e}",
                exc_info=True
            )
            raise
    else:
        LOGGER.info("当前操作系统非 Windows")
        try:
            if platform.system() == 'Darwin':
                cmd = ['open', program_path]
            else:
                cmd = ['xdg-open', program_path]
            os.spawnvp(os.P_NOWAIT, cmd[0], cmd)
            LOGGER.debug("外部程序已启动")
        except Exception as e:
            LOGGER.error(
                f"执行外部程序 {program_path} 时发生错误：{e}",
                exc_info=True
            )
            raise


def parse_args():
    """解析命令行参数。

    Returns:
        argparse.Namespace: 命令行参数命名空间。
    """
    LOGGER.debug("解析命令行参数")
    parser = argparse.ArgumentParser(description='2RPM V2')
    parser.add_argument(
        '-c', '--config',
        default=DEFAULT_CONFIG_FILE,
        help="指定配置文件路径，示例 -c C:\\path\\config.yaml"
    )
    args = parser.parse_args()
    LOGGER.debug(f"命令行参数解析结果: {args}")
    return args


def main():
    """主函数。

    初始化程序，加载配置，设置日志，并运行主监视器。
    """
    global CONFIG, LOGGER

    # 初始化基本日志配置
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s | %(asctime)s.%(msecs)03d | %(message)s',
        datefmt='%H:%M:%S'
    )
    LOGGER = logging.getLogger(__name__)
    LOGGER.info("程序正在初始化...")

    # 解析命令行参数
    args = parse_args()
    program_dir = get_program_directory()
    config_file = os.path.join(program_dir, args.config)

    try:
        # 加载配置
        CONFIG = load_config(config_file)
        LOGGER.info("配置已加载")
    except SystemExit:
        LOGGER.critical("程序因缺少关键配置终止运行")
        sys.exit(1)
    except Exception as e:
        LOGGER.critical(f"加载配置失败: {e}")
        sys.exit(1)

    # 设置日志
    setup_logging()
    LOGGER.info("已完成日志配置")

    try:
        # 运行主监视器
        LOGGER.info("正在运行主程序")
        asyncio.run(monitor_processes())
        LOGGER.info("主程序已结束运行")
    except KeyboardInterrupt:
        LOGGER.critical("捕捉到 Ctrl+C，程序被手动终止")
        sys.exit(1)
    except Exception as e:
        LOGGER.critical(f"程序出现异常: {e}", exc_info=True)
        sys.exit(1)
    finally:
        LOGGER.info("程序已终止运行")


if __name__ == '__main__':
    main()
