#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
# Running-Runtime Process Monitoring

    - 本项目使用 GPT AI 生成，GPT 模型: o1-preview
    - 本项目使用 Claude AI 生成，Claude 模型: claude-3-5-sonnet

- 版本: v2.16.5

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
import logging
import asyncio
import argparse
import datetime
import colorama
import urllib.parse
import urllib.request
from io import StringIO
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
    'push_settings.push_channel_settings.serverchan_key',
    'push_settings.push_channel_settings.push_channel',
    'push_settings.push_channel_settings.push_channel_key',
    'external_program_settings.enable_external_program_call',
    'external_program_settings.trigger_process_name',
    'external_program_settings.external_program_path',
]


# 注释集中管理
COMMENTS = {
    'monitor_settings': {
        '_comment': (
            "监视设置\n"
            "- 监视程序相关配置\n"
        ),
        'process_name_list': (
            "\n要监视的进程名称列表"
        ),
        'timeout_warning_interval_ms': (
            "\n超时警告间隔，默认值900000毫秒（15分钟），单位为毫秒\n"
            "- 时间换算可通过搜索引擎搜索: 分钟换毫秒工具，也可查看下列公式: \n"
            "- 分钟换算毫秒：分钟×60×1000；例 30分钟换算成毫秒：30×60×1000=1800000"
        ),
        'monitor_loop_interval_ms': (
            "\n监视循环间隔，默认值1000毫秒（1秒），单位为毫秒\n"
            "- 时间换算可通过搜索引擎搜索: 秒换毫秒工具，也可查看下列公式: \n"
            "- 秒换算毫秒：秒×1000；例 15秒换算成毫秒：15×1000=15000"
        ),
    },
    'wait_process_settings': {
        '_comment': (
            "等待进程设置\n"
            "- 配置等待进程启动的相关参数\n"
        ),
        'max_wait_time_ms': (
            "\n最长等待时间，默认值: 30000毫秒（30秒），单位为毫秒"
        ),
        'wait_process_check_interval_ms': (
            "\n等待进程检查间隔，默认值1000毫秒（1秒），单位为毫秒\n"
            "- 时间换算可通过搜索引擎搜索: 秒换毫秒工具，也可查看下列公式: \n"
            "- 秒换算毫秒：秒×1000；例 15秒换算成毫秒：15×1000=15000"
        ),
    },
    'push_settings': {
        '_comment': (
            "推送设置\n"
            "- 包含推送通知的相关配置\n"
        ),
        'push_templates': {
            '_comment': (
                "推送模板配置\n"
                "- 可自定义通知的标题和内容\n\n"
                "支持的变量如下：\n"
                "主机名: {host_name}\n"
                "当前时间(YY-mm-dd HH-MM-SS): {current_time}\n"
                "当前时间(HH-MM-SS): {short_current_time}\n"
                "进程名: {process_name}\n"
                "进程PID: {process_pid}\n"
                "进程运行时间: {process_run_time}\n"
                "进程积累等待时间: {process_wait_time}\n"
                "正在运行的进程状态列表: {other_running_processes}\n"
                "进程列表: {process_list}\n"
                "调用的程序名: {external_program_name}\n"
                "调用的程序路径: {external_program_path}\n"
            ),
            'process_end_notification': (
                "\n进程结束通知模板\n"
                "- enable: 是否启用该通知\n"
                "- title: 通知标题\n"
                "- content: 通知内容\n"
            ),
            'process_timeout_warning': (
                "\n进程超时运行警告模板\n"
                "- enable: 是否启用该通知\n"
                "- title: 通知标题\n"
                "- content: 通知内容\n"
            ),
            'process_wait_timeout_warning': (
                "\n等待超时未运行报告模板\n"
                "- enable: 是否启用该通知\n"
                "- title: 通知标题\n"
                "- content: 通知内容\n"
            ),
            'external_program_execution_notification': (
                "\n外部程序执行通知模板\n"
                "- enable: 是否启用该通知\n"
                "- title: 通知标题\n"
                "- content: 通知内容\n"
            ),
        },
        'push_channel_settings': {
            '_comment': (
                "推送通道设置\n"
            ),
            'choose': (
                "\n请选择 'ServerChan' 或者 'OnePush' 进行推送，默认为 'ServerChan'"
            ),
            'serverchan_key': "\nServerChan密钥",
            'push_channel': (
                "\nOnePush推送通道（请查看 https://pypi.org/project/onepush/ "
                "来获得如何使用帮助）"
            ),
            'push_channel_key': "\nOnePush推送通道密钥",
        },
        'push_error_retry': {
            '_comment': "推送错误重试设置\n",
            'retry_interval_ms': (
                "\n重试间隔，默认值: 3000毫秒（3秒）单位为毫秒"
            ),
            'max_retry_count': "\n最大重试次数，默认值: 3次",
        },
        'enable_push_on_external_program_execution': (
            "\n是否在外部程序执行后推送通知，默认值为 False\n"
        ),
    },
    'external_program_settings': {
        '_comment': (
            "外部程序调用设置\n"
        ),
        'enable_external_program_call': (
            "\n============================================================\n"
            "\n进程结束后是否执行外部程序调用\n"
            "- False 为 不执行，True 为 执行，默认为 False\n"
        ),
        'trigger_process_name': (
            "\n指定进程结束后执行外部程序调用\n"
            "- 当程序检测到 'notepad.exe' 结束运行时，会执行外部程序调用"
        ),
        'external_program_path': (
            "\n需要调用的外部程序/BAT脚本的详细路径，例如: "
            "C:\\path\\end\\script.bat"
        ),
        'enable_another_external_program_on_timeout': (
            "\n============================================================\n"
            "\n进程运行超时后触发外部程序调用\n"
            "- False 为 不执行，True 为 执行，默认为 False\n"
        ),
        'another_trigger_process_name': (
            "\n指定进程运行超时次数达到阈值时触发外部程序调用\n"
            "- 当程序检测到 'notepad.exe' 运行超时次数达到阈值时，会执行外部程序调用\n"
        ),
        'another_external_program_path': (
            "\n需要调用的外部程序/BAT脚本的详细路径，例如: "
            "C:\\path\\timeout\\timeout_script.bat"
        ),
        'timeout_count_threshold': (
            "\n设置进程运行超时次数阈值，达到该次数后执行触发外部程序调用，默认值: 3\n"
        ),
        'exit_after_external_program': (
            "\n设置进程运行超时调用外部程序后是否退出程序\n"
            "- False 为 不退出，True 为 退出，默认值为 False\n"
        ),
        'enable_external_program_on_wait_timeout': (
            "\n============================================================\n"
            "\n等待进程启动超时后触发外部程序调用\n"
            "- False 为 不执行，True 为 执行，默认为 False\n"
        ),
        'external_program_on_wait_timeout_path': (
            "\n需要调用的外部程序/BAT脚本的详细路径，例如: "
            "- 例如: C:\\path\\wait_timeout\\wait_timeout_script.bat"
        ),
    },
    'log_settings': {
        '_comment': (
            "日志设置\n"
            "- 配置日志输出的相关参数\n"
        ),
        'enable_log_file': (
            "\n是否输出日志文件，默认为 False\n"
            "- False 为 不输出，True 为 输出"
        ),
        'log_level': (
            "\n日志输出等级，默认为 INFO\n"
            "- 请根据需要进行设置，否则不建议改动\n"
            "- DEBUG > INFO > WARNING > ERROR > CRITICAL"
        ),
        'log_directory': (
            "\n日志输出的目录，默认为程序目录下的 'logs' 文件夹\n"
            "- 请根据需要进行设置，例如: C:\\Path\\2RPM\\v2\\logs\n"
            "- 若不需要日志文件输出，请将 'enable_log_file' 设置为 False\n"
        ),
        'max_log_files': "\n日志最大保存数量，默认值: 15个",
        'log_retention_days': "\n日志保存天数，单位为天，默认值: 3天",
        'log_filename': "\n日志文件名，时间戳不可修改，默认值: 2RPM",
    },
}


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
                }),
                'external_program_execution_notification': CommentedMap({
                    'enable': False,
                    'title': '外部程序执行通知',
                    'content': (
                        '主机: {host_name}\n\n'
                        '当前时间: {current_time}\n\n'
                        '外部程序已执行:\n\n'
                        '程序名: {external_program_name}\n\n'
                        '程序路径: {external_program_path}\n\n'
                    )
                })
            }),
            'push_channel_settings': CommentedMap({
                'choose': 'ServerChan',
                'serverchan_key': '',
                'push_channel': '',
                'push_channel_key': ''
            }),
            'push_error_retry': CommentedMap({
                'retry_interval_ms': 3000,
                'max_retry_count': 3
            }),
            'enable_push_on_external_program_execution': False
        }),
        'external_program_settings': CommentedMap({
            'enable_external_program_call': False,
            'trigger_process_name': 'notepad.exe',
            'external_program_path': 'C:\\path\\to\\your\\script.bat',
            'enable_another_external_program_on_timeout': False,
            'another_trigger_process_name': 'notepad.exe',
            'another_external_program_path':
                'C:\\path\\to\\another_script.bat',
            'timeout_count_threshold': 3,
            'exit_after_external_program': False,
            'enable_external_program_on_wait_timeout': False,
            'external_program_on_wait_timeout_path':
                'C:\\path\\to\\wait_timeout_script.bat'
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

    LOGGER.debug("正在应用注释到默认配置")
    apply_comments(config, COMMENTS)
    LOGGER.debug("内置配置读取完成")
    return config


def apply_comments(config_section, comments_section):
    """递归地将注释应用到配置字典中。

    Args:
        config_section (CommentedMap): 配置的一个部分。
        comments_section (dict or str): 对应的注释部分。
    """
    if isinstance(comments_section, str):
        # 如果注释是字符串，应用到整个节
        if not config_section.ca.comment:
            config_section.yaml_set_start_comment(comments_section)
        return
    elif isinstance(comments_section, dict):
        if '_comment' in comments_section:
            # 应用节的注释
            if not config_section.ca.comment:
                config_section.yaml_set_start_comment(comments_section['_comment'])
        for key, value in config_section.items():
            if key in comments_section:
                comment = comments_section[key]
                if isinstance(comment, dict):
                    apply_comments(value, comment)
                else:
                    # 检查是否已经有注释，避免重复添加
                    if not config_section.ca.items.get(key):
                        config_section.yaml_set_comment_before_after_key(
                            key, before=comment
                        )
            elif isinstance(value, CommentedMap):
                # 如果没有对应的注释，继续递归
                apply_comments(value, {})
            else:
                continue


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
        LOGGER.warning(f"配置文件不存在: {os.path.abspath(config_file)}")
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
    updated = check_and_update_config(user_config, default_config, COMMENTS)
    if updated:
        LOGGER.debug("配置文件已更新，正在写回配置文件。")
        try:
            yaml.indent(mapping=2, sequence=4, offset=2)
            yaml.preserve_quotes = True
            with open(config_file, 'w', encoding='utf-8') as f:
                yaml.dump(user_config, f)
            LOGGER.info(f"已更新配置文件: {os.path.abspath(config_file)}")
        except Exception as e:
            LOGGER.error(f"无法写回配置文件 {os.path.abspath(config_file)}: {e}")

    LOGGER.debug("配置参数版本差异检查完成")
    return user_config


def check_and_update_config(user_config, default_config, comments_section, parent_key=''):
    """检查并更新用户配置，添加缺失的参数，并更新注释。

    Args:
        user_config (CommentedMap): 用户配置字典。
        default_config (CommentedMap): 默认配置字典。
        comments_section (dict or str): 对应的注释部分。
        parent_key (str): 父级键，用于递归。

    Returns:
        bool: 是否有更新。
    """
    LOGGER.debug(f"正在检查配置信息，parent_key: {parent_key}")
    updated = False

    if isinstance(comments_section, str):
        # 应用节的注释
        user_config.yaml_set_start_comment(comments_section)

    for key, default_value in default_config.items():
        full_key = f"{parent_key}.{key}" if parent_key else key
        LOGGER.debug(f"检查关键配置信息: {full_key}")

        user_value = user_config.get(key, None)
        comment = None
        sub_comments_section = {}

        if isinstance(comments_section, dict):
            if key in comments_section:
                sub_comments_section = comments_section[key]
            else:
                sub_comments_section = {}
            if '_comment' in comments_section:
                comment = comments_section['_comment']

        if user_value is None:
            if full_key in CRITICAL_KEYS:
                LOGGER.critical(f"关键参数缺失: {full_key}")
                sys.exit(1)
            else:
                LOGGER.warning(f"参数缺失: {full_key}")
                user_config[key] = default_value
                updated = True
                LOGGER.info(f"正在对参数 {full_key}，应用默认值: {default_value}")

                # 应用注释
                if isinstance(sub_comments_section, str):
                    user_config.yaml_set_comment_before_after_key(
                        key, before=sub_comments_section)
        else:
            if isinstance(default_value, CommentedMap):
                if not isinstance(user_value, CommentedMap):
                    LOGGER.warning(f"参数类型不匹配: {full_key}")
                    user_config[key] = default_value
                    updated = True
                    LOGGER.info(f"使用默认值更新参数: {full_key}")
                    # 应用注释
                    if isinstance(sub_comments_section, str):
                        user_config.yaml_set_comment_before_after_key(
                            key, before=sub_comments_section)
                else:
                    nested_updated = check_and_update_config(
                        user_value, default_value, sub_comments_section, full_key)
                    if nested_updated:
                        updated = True
                    # 应用节的注释
                    if isinstance(sub_comments_section, dict) and '_comment' in sub_comments_section:
                        user_value.yaml_set_start_comment(sub_comments_section['_comment'])
            else:
                # 应用注释
                if isinstance(sub_comments_section, str):
                    user_config.yaml_set_comment_before_after_key(
                        key, before=sub_comments_section)

    LOGGER.debug(f"配置参数检查完成，是否更新: {updated}")
    return updated


def get_comments_section(full_key):
    """根据完整的键路径获取对应的注释部分。

    Args:
        full_key (str): 完整的键路径，以 '.' 分隔。

    Returns:
        dict or str: 对应的注释部分。
    """
    keys = full_key.split('.')
    comments_section = COMMENTS
    for key in keys:
        if key in comments_section:
            comments_section = comments_section[key]
        else:
            return None
    return comments_section


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
    LOGGER.debug("开始执行函数: setup_logging")
    log_config = CONFIG.get('log_settings', {})
    enable_log_file = log_config.get('enable_log_file', False)
    log_level_str = log_config.get('log_level', 'INFO')
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)
    log_dir = log_config.get('log_directory', 'logs')

    # 设置日志目录为程序所在目录下的 logs 文件夹
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
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    LOGGER.debug("已清除之前的日志处理器")

    # 控制台日志处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)

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
    LOGGER.debug("开始执行函数: clean_logs")
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


def run_external_program(program_path):
    """运行外部程序。

    Args:
        program_path (str): 外部程序的路径。
    """
    LOGGER.info(f"正在调用外部程序: {program_path}")
    if not os.path.exists(program_path):
        LOGGER.error(f"外部程序不存在: {program_path}")
        return
    try:
        os.startfile(program_path)
        LOGGER.info(f"成功调用外部程序: {program_path}")
    except Exception as e:
        LOGGER.error(f"调用外部程序失败: {e}")


def get_other_running_processes(processes, exclude_pid=None):
    """获取其他正在运行的进程信息。

    Args:
        processes (dict): 当前监视的进程信息。
        exclude_pid (int, optional): 要排除的进程 PID。默认为 None。

    Returns:
        str: 其他正在运行的进程信息字符串。
    """
    LOGGER.debug("获取其他正在运行的进程信息")
    other_processes = [
        f"{info['name']} (PID: {pid})"
        for pid, info in processes.items() if pid != exclude_pid
    ]
    result = ', '.join(other_processes) if other_processes else '无'
    LOGGER.info(f"其他正在运行的进程信息: {result}")
    return result


def get_missing_processes(process_name_list, processes, exclude_pid=None):
    """获取未运行的进程列表。

    Args:
        process_name_list (list): 要监视的进程名称列表。
        processes (dict): 当前监视的进程信息。
        exclude_pid (int, optional): 要排除的进程 PID。默认为 None。

    Returns:
        list: 未运行的进程名称列表。
    """
    LOGGER.debug("获取未运行的进程列表")
    running_process_names = [
        info['name']
        for pid, info in processes.items() if pid != exclude_pid
    ]
    missing_processes = set(process_name_list) - set(running_process_names)
    result = list(missing_processes)
    LOGGER.info(f"未运行的进程列表: {result}")
    return result


async def send_notification(template_key, **kwargs):
    """发送通知。

    Args:
        template_key (str): 模板键。
        **kwargs: 模板参数。
    """
    LOGGER.info(f"使用模板: {template_key} 推送报告")
    push_settings = CONFIG.get('push_settings', {})
    push_templates = push_settings.get('push_templates', {})
    template = push_templates.get(template_key, {})

    # 检查是否启用了该通知
    if not template.get('enable', True):
        LOGGER.warning(f"通知推送已被禁用: {template_key}")
        return

    title = template.get('title', '')
    content = template.get('content', '')

    # 填充模板参数
    current_time = datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S')
    short_current_time = datetime.datetime.now().strftime('%H:%M:%S')
    host_name = socket.gethostname()

    kwargs.update({
        'host_name': host_name,
        'current_time': current_time,
        'short_current_time': short_current_time,
    })

    title = title.format(**kwargs)
    content = content.format(**kwargs)
    LOGGER.info(f"通知内容: {content}")

    # 获取推送通道
    push_channel_settings = push_settings.get('push_channel_settings', {})
    push_channel = push_channel_settings.get('choose', 'ServerChan')
    serverchan_key = push_channel_settings.get('serverchan_key', '')
    push_channel_name = push_channel_settings.get('push_channel', '')
    push_channel_key = push_channel_settings.get('push_channel_key', '')

    retry_settings = push_settings.get('push_error_retry', {})
    retry_interval_ms = retry_settings.get('retry_interval_ms', 3000)
    max_retry_count = retry_settings.get('max_retry_count', 3)

    # 推送通知
    LOGGER.info(f"推送通道: {push_channel}")
    for attempt in range(1, max_retry_count + 1):
        try:
            if push_channel == 'ServerChan':
                if not serverchan_key:
                    LOGGER.error("ServerChan 密钥未配置，无法发送通知")
                    return
                url = f"https://sc.ftqq.com/{serverchan_key}.send"
                data = urllib.parse.urlencode({
                    'text': title,
                    'desp': content
                }).encode('utf-8')
                req = urllib.request.Request(url, data=data)
                with urllib.request.urlopen(req) as response:
                    response_data = response.read().decode('utf-8')
                    LOGGER.info(f"推送成功: {response_data}")
            elif push_channel == 'OnePush':
                if not push_channel_key:
                    LOGGER.error("OnePush 密钥未配置，无法发送通知")
                    return
                notifier = get_notifier(push_channel_name)
                notifier.notify(
                    title=title, content=content, key=push_channel_key)
                LOGGER.info(f"通知发送成功: {title}")
            else:
                LOGGER.critical(f"推送通道未配置: {push_channel}")
            break
        except Exception as e:
            LOGGER.error(f"通知发送失败 (尝试 {attempt}/{max_retry_count}): {e}")
            if attempt < max_retry_count:
                await asyncio.sleep(retry_interval_ms / 1000)
            else:
                LOGGER.critical("通知发送失败，已达到最大重试次数。")
                sys.exit(1)


async def monitor_processes():
    """监视进程列表。

    等待指定的进程启动，监视其运行状态，并在进程结束或超时时发送通知。
    """
    LOGGER.debug("开始执行函数: monitor_processes")
    monitor_settings = CONFIG.get('monitor_settings', {})
    wait_settings = CONFIG.get('wait_process_settings', {})
    external_settings = CONFIG.get('external_program_settings', {})
    push_settings = CONFIG.get('push_settings', {})

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

    enable_another_external_program_on_timeout = external_settings.get(
        'enable_another_external_program_on_timeout', False)
    another_trigger_process_name = external_settings.get(
        'another_trigger_process_name', '')
    another_external_program_path = external_settings.get(
        'another_external_program_path', '')
    timeout_count_threshold = external_settings.get(
        'timeout_count_threshold', 3)
    exit_after_external_program = external_settings.get(
        'exit_after_external_program', False)

    enable_external_program_on_wait_timeout = external_settings.get(
        'enable_external_program_on_wait_timeout', False)
    external_program_on_wait_timeout_path = external_settings.get(
        'external_program_on_wait_timeout_path', '')

    enable_push_on_external_program_execution = push_settings.get(
        'enable_push_on_external_program_execution', False)

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
            LOGGER.debug("执行等待进程启动循环")
            waited_time_ns = time.perf_counter_ns() - start_time_ns
            if waited_time_ns // 1_000_000 > max_wait_time_ms:
                LOGGER.debug("已等待超时，正在尝试发送通知")
                break

            current_processes = {
                p.pid: p.info for p in psutil.process_iter(
                    ['pid', 'name', 'create_time'])
            }
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
                        'timeout_count': 0,
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
        LOGGER.debug("执行等待进程启动超时报告与推送")
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

        # 执行外部程序
        if enable_external_program_on_wait_timeout:
            LOGGER.info("等待进程启动超时，正在执行外部程序...")
            try:
                run_external_program(external_program_on_wait_timeout_path)
                LOGGER.info(
                    f"外部程序 {external_program_on_wait_timeout_path} 执行成功")
                # 推送通知
                if enable_push_on_external_program_execution:
                    await send_notification(
                        'external_program_execution_notification',
                        external_program_name=os.path.basename(
                            external_program_on_wait_timeout_path),
                        external_program_path=(
                            external_program_on_wait_timeout_path)
                    )
            except Exception as e:
                LOGGER.error(
                    f"执行外部程序 {external_program_on_wait_timeout_path} "
                    f"时发生错误: {e}",
                    exc_info=True
                )
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
            LOGGER.debug("执行监视循环")
            current_time_ns = time.perf_counter_ns()
            current_processes = {
                p.pid: p.info for p in psutil.process_iter(
                    ['pid', 'name', 'create_time'])
            }
            current_pids = set(current_processes.keys())
            monitored_pids = set(processes.keys())

            # 检查进程结束
            ended_pids = monitored_pids - current_pids
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
                    f"进程结束: {process_name} (PID: {pid}) "
                    f"运行时间: {formatted_run_time}"
                )

                # 如果启用了外部程序调用，并且结束的进程是指定的触发进程
                if (enable_external_program_call and
                        process_name == trigger_process_name):
                    LOGGER.info(
                        f"检测到进程 {process_name} 结束，正在调用外部程序..."
                    )
                    try:
                        run_external_program(external_program_path)
                        LOGGER.info(f"成功调用外部程序 {external_program_path}")
                        # 推送通知
                        if enable_push_on_external_program_execution:
                            await send_notification(
                                'external_program_execution_notification',
                                external_program_name=os.path.basename(
                                    external_program_path),
                                external_program_path=external_program_path
                            )
                    except Exception as e:
                        LOGGER.error(
                            f"调用外部程序 {external_program_path} 时发生错误: {e}",
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
                    process_info['timeout_count'] += 1

                    # 检查是否需要执行外部程序
                    if (enable_another_external_program_on_timeout and
                            process_info['name'] ==
                            another_trigger_process_name and
                            process_info['timeout_count'] %
                            timeout_count_threshold == 0):
                        LOGGER.info(
                            f"进程 {process_info['name']} (PID: {pid}) "
                            f"超时次数达到阈值 {timeout_count_threshold}，"
                            f"正在调用外部程序..."
                        )
                        try:
                            run_external_program(
                                another_external_program_path)
                            LOGGER.info(
                                f"外部程序 {another_external_program_path} "
                                f"执行成功"
                            )
                            # 推送通知
                            if enable_push_on_external_program_execution:
                                await send_notification(
                                    'external_program_execution_notification',
                                    external_program_name=os.path.basename(
                                        another_external_program_path),
                                    external_program_path=(
                                        another_external_program_path)
                                )
                            if exit_after_external_program:
                                LOGGER.critical(
                                    "参数 exit_after_external_program 配置为 True，"
                                    "正在结束运行"
                                )
                                sys.exit(0)
                        except Exception as e:
                            LOGGER.error(
                                f"调用外部程序 {another_external_program_path} "
                                f"时发生错误: {e}",
                                exc_info=True
                            )

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


def parse_args():
    """解析命令行参数。

    Returns:
        argparse.Namespace: 命令行参数命名空间。
    """
    LOGGER.debug("解析命令行参数")
    parser = argparse.ArgumentParser(description='2RPM V2')
    parser.add_argument(
        '-c', '-C', '-config', '-Config', '--config', '--Config',
        default=DEFAULT_CONFIG_FILE,
        help="指定配置文件路径，示例 -c C:\\path\\config.yaml"
    )
    args = parser.parse_args()
    LOGGER.debug(f"命令行参数解析结果: {args}")
    return args


def print_info():
    """打印程序的版本和版权信息。"""
    print("\n")
    print("+ " + " Running-Runtime Process Monitoring ".center(80, "="), "+")
    print("||" + "".center(80, " ") + "||")
    print("||" + "本项目使用 GPT AI 与 Claude AI 生成".center(72, " ") + "||")
    print("||" + "GPT 模型为：o1-preview，"
          "Claude 模型为: claude-3-5-sonnet".center(72, " ") + "||")
    print("|| " + "".center(78, "-") + " ||")
    print("||" + "Version: v2.16.5    License: WTFPL".center(80, " ") + "||")
    print("||" + "".center(80, " ") + "||")
    print("+ " + "".center(80, "=") + " +")
    print("\n")


def print_exit_info():
    """在程序结束时打印信息。"""
    print_info()
    LOGGER.info("程序运行结束")


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
    print_info()
    LOGGER.info("程序正在初始化...")

    # 解析命令行参数
    args = parse_args()
    program_dir = get_program_directory()
    config_file = os.path.join(program_dir, args.config)

    try:
        # 加载配置
        CONFIG = load_config(config_file)
        LOGGER.debug("配置已加载")
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
        LOGGER.info("初始化结束，正在运行主程序")
        asyncio.run(monitor_processes())
        LOGGER.info("主程序已结束运行")
    except KeyboardInterrupt:
        LOGGER.critical("捕捉到 Ctrl+C，程序被手动终止")
        sys.exit(1)
    except Exception as e:
        LOGGER.critical(f"程序出现异常: {e}", exc_info=True)
        sys.exit(1)
    finally:
        print_exit_info()


if __name__ == '__main__':
    main()
