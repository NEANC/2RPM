#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

# 全局变量
CONFIG = {}
LOGGER = logging.getLogger(__name__)

# 默认配置文件名
DEFAULT_CONFIG_FILE = 'config.yaml'

# 关键参数列表
CRITICAL_KEYS = [
    'monitor_settings.process_name',
    'push_settings.push_channel_settings.choose',
    'push_settings.push_channel_settings.serverchan_key',
    'push_settings.push_channel_settings.push_channel',
    'push_settings.push_channel_settings.push_channel_key',
    'external_program_settings.external_program_path',
]

# 注释集中管理
COMMENTS = {
    'monitor_settings': {
        '_comment': (
            "监视设置\n"
            "- 监视程序相关配置\n"
        ),
        'process_name': (
            "\n要监视的进程名称"
        ),
        'timeout_warning_interval': (
                    "\n超时警告间隔，默认值15分钟，支持 H/M/S 格式\n"
                ),
                'monitor_loop_interval': (
                    "\n监视循环间隔，默认值1秒，支持 H/M/S 格式\n"
                ),
    },
    'wait_process_settings': {
        '_comment': (
            "等待进程设置\n"
            "- 配置等待进程启动的相关参数\n"
        ),
        'max_wait_time': (
                "\n最长等待时间，默认值: 30秒，支持 H/M/S 格式\n"
            ),
            'wait_process_check_interval': (
                "\n等待进程检查间隔，默认值1秒，支持 H/M/S 格式\n"
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
            'retry_interval': (
                "\n重试间隔，默认值: 3000毫秒（3秒），支持 H/M/S 格式\n"
            ),
            'max_retry_count': "\n最大重试次数，默认值: 3次",
        },
    },
    'external_program_settings': {
            '_comment': (
                "外部程序调用设置\n"
            ),
            'external_program_path': (
                "\n进程结束时触发的外部程序/BAT脚本的详细路径，例如: "
                "C:\\path\\end\\script.bat"
            ),
            'another_external_program_path': (
                "\n进程运行超时后触发的外部程序/BAT脚本的详细路径，例如: "
                "C:\\path\\timeout\\timeout_script.bat"
            ),
            'timeout_count_threshold': (
                "\n设置进程运行超时次数阈值，达到该次数后执行触发外部程序调用，默认值: 3\n"
            ),
            'external_program_on_wait_timeout_path': (
                "\n等待进程启动超时后触发的外部程序/BAT脚本的详细路径，例如: "
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
            'process_name': 'notepad.exe',
            'timeout_warning_interval': '15m',
            'monitor_loop_interval': '1s'
        }),
        'wait_process_settings': CommentedMap({
            'max_wait_time': '30s',
            'wait_process_check_interval': '1s'
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
                'retry_interval': '3s',
                'max_retry_count': 3
            }),
        }),
        'external_program_settings': CommentedMap({
            'external_program_path': 'C:\\path\\to\\your\\script.bat',
            'another_external_program_path':
                'C:\\path\\to\\another_script.bat',
            'timeout_count_threshold': 3,
            'external_program_on_wait_timeout_path':
                'C:\\path\\to\\wait_timeout_script.bat'
        }),
        'log_settings': CommentedMap({
            'enable_log_file': True,
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
    # 彻底清除现有的注释，避免重复
    if hasattr(config_section, 'ca'):
        config_section.ca.comment = None
        if hasattr(config_section.ca, 'items'):
            # 清空所有键的注释
            config_section.ca.items.clear()

    if isinstance(comments_section, str):
        # 如果注释是字符串，应用到整个节
        config_section.yaml_set_start_comment(comments_section)
        return
    elif isinstance(comments_section, dict):
        if '_comment' in comments_section:
            # 应用节的注释
            config_section.yaml_set_start_comment(comments_section['_comment'])
        for key, value in config_section.items():
            if key in comments_section:
                comment = comments_section[key]
                if isinstance(comment, dict):
                    apply_comments(value, comment)
                else:
                    # 直接应用注释
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
        LOGGER.info(f"配置文件创建成功: {os.path.abspath(config_file)}")
    except Exception as e:
        LOGGER.critical(f"创建配置文件失败: {e}")
        raise


def merge_configs(user_config, default_config):
    """将用户配置合并到默认配置中。

    Args:
        user_config (dict): 用户配置字典。
        default_config (CommentedMap): 默认配置字典。

    Returns:
        CommentedMap: 合并后的配置字典。
    """
    if not isinstance(user_config, dict):
        return default_config
    
    for key, value in user_config.items():
        if key in default_config:
            if isinstance(value, dict) and isinstance(default_config[key], CommentedMap):
                merge_configs(value, default_config[key])
            else:
                default_config[key] = value
    
    return default_config

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
        LOGGER.critical(f"配置文件不存在: {os.path.abspath(config_file)}")
        create_default_config(config_file)
        LOGGER.info("配置文件已生成，请根据需要修改配置文件后再次运行程序。")
        input("请按任意键退出...")
        sys.exit(0)

    # 保存原有配置到内存
    try:
        yaml = YAML()
        with open(config_file, 'r', encoding='utf-8') as f:
            user_config = yaml.load(f)
        LOGGER.info(f"成功加载配置文件: {os.path.abspath(config_file)}")
    except Exception as e:
        LOGGER.critical(f"无法加载配置文件: {os.path.abspath(config_file)}: {e}")
        raise

    # 加载默认配置
    default_config = get_default_config()
    
    # 合并用户配置到默认配置中
    merged_config = merge_configs(user_config, default_config)
    
    # 检查是否有更新
    updated = False
    
    # 检查用户配置是否缺少默认配置中的键
    def check_missing_keys(config, default, parent_key=''):
        nonlocal updated
        for key, value in default.items():
            full_key = f"{parent_key}.{key}" if parent_key else key
            if key not in config:
                updated = True
                LOGGER.warning(f"参数缺失: {full_key}，使用默认值: {value}")
            elif isinstance(value, CommentedMap) and isinstance(config.get(key), dict):
                check_missing_keys(config[key], value, full_key)
    
    check_missing_keys(user_config, default_config)
    
    if updated:
        LOGGER.debug("配置文件已更新，正在执行无缝迁移。")
        try:
            # 删除原配置文件
            if os.path.exists(config_file):
                os.remove(config_file)
                LOGGER.debug(f"已删除原配置文件: {os.path.abspath(config_file)}")
            
            # 重新创建并写入更新后的配置（包含原有有效配置和新配置的混合）
            yaml = YAML()
            yaml.indent(mapping=2, sequence=4, offset=2)
            yaml.preserve_quotes = True
            with open(config_file, 'w', encoding='utf-8') as f:
                yaml.dump(merged_config, f)
            LOGGER.info(f"已完成配置文件无缝迁移: {os.path.abspath(config_file)}")
        except Exception as e:
            LOGGER.error(f"无法写回配置文件 {os.path.abspath(config_file)}: {e}")

    LOGGER.debug("配置参数版本差异检查完成")
    return merged_config


