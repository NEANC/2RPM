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

# 默认配置值集中管理
DEFAULT_VALUES = {
    'monitor_settings': {
        'process_name': 'notepad.exe',
        'timeout_warning_interval': '15m',
        'monitor_loop_interval': '1s',
    },
    'wait_process_settings': {
        'max_wait_time': '30s',
        'wait_process_check_interval': '1s',
    },
    'push_settings': {
        'push_templates': {
            'process_end_notification': {
                'enable': True,
                'title': '进程结束通报',
                'content': (
                    '主机: {host_name}\n\n'
                    '当前时间: {current_time}\n\n'
                    '进程: {process_name} (PID: {process_pid}) '
                    '于 {short_current_time} 时已结束运行\n\n'
                    '运行时间: {process_run_time}\n\n'
                ),
            },
            'process_timeout_warning': {
                'enable': True,
                'title': '进程超时运行警告',
                'content': (
                    '主机: {host_name}\n\n'
                    '当前时间: {current_time}\n\n'
                    '进程: {process_name} (PID: {process_pid}) '
                    '于 {short_current_time} 时已运行超过预期: '
                    '{process_run_time}\n\n'
                ),
            },
            'process_wait_timeout_warning': {
                'enable': True,
                'title': '等待超时未运行报告',
                'content': (
                    '主机: {host_name}\n\n'
                    '当前时间: {current_time}\n\n'
                    '进程: {process_name} 超时未运行\n\n'
                    '已等待时间: {process_wait_time}\n\n'
                ),
            },
            'external_program_execution_notification': {
                'enable': False,
                'title': '外部程序执行通知',
                'content': (
                    '主机: {host_name}\n\n'
                    '当前时间: {current_time}\n\n'
                    '外部程序已执行:\n\n'
                    '程序名: {external_program_name}\n\n'
                    '程序路径: {external_program_path}\n\n'
                ),
            },
        },
        'push_channel_settings': {
            'choose': 'ServerChan',
            'serverchan_key': '',
            'push_channel': '',
            'push_channel_key': '',
        },
        'push_error_retry': {
            'retry_interval': '3s',
            'max_retry_count': 3,
        },
    },
    'external_program_settings': {
        'external_program_path': 'C:\\path\\to\\your\\script.bat',
        'another_external_program_path': 'C:\\path\\to\\another_script.bat',
        'timeout_count_threshold': 3,
        'external_program_on_wait_timeout_path': 'C:\\path\\to\\wait_timeout_script.bat',
    },
    'log_settings': {
        'enable_log_file': True,
        'log_level': 'INFO',
        'log_directory': 'logs',
        'max_log_files': 15,
        'log_retention_days': 3,
        'log_filename': '2RPM',
    },
}

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
    
    # 递归创建 CommentedMap 结构
    def create_commented_map(config_dict):
        if isinstance(config_dict, dict):
            commented_map = CommentedMap()
            for key, value in config_dict.items():
                if isinstance(value, dict):
                    commented_map[key] = create_commented_map(value)
                else:
                    commented_map[key] = value
            return commented_map
        else:
            return config_dict
    
    # 使用集中管理的默认值创建配置
    config = create_commented_map(DEFAULT_VALUES)

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


def ms_to_hms(milliseconds):
    """将毫秒转换为H/M/S格式。

    Args:
        milliseconds (int): 毫秒数。

    Returns:
        str: 转换后的H/M/S格式字符串。
    """
    total_seconds = milliseconds // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if hours > 0:
        return f"{hours}h"
    elif minutes > 0:
        return f"{minutes}m"
    else:
        return f"{seconds}s"


def migrate_old_config(old_config):
    """将旧版本配置迁移到新版本配置。

    Args:
        old_config (dict): 旧版本配置字典。

    Returns:
        dict: 迁移后的配置字典。
    """
    LOGGER.info("正在检查并迁移旧版本配置...")
    
    # 深拷贝所有原始配置，确保完整保留所有配置项
    def deep_copy_config(config):
        if isinstance(config, dict):
            copied = {}
            for key, value in config.items():
                copied[key] = deep_copy_config(value)
            return copied
        elif isinstance(config, list):
            return [deep_copy_config(item) for item in config]
        else:
            return config
    
    migrated_config = deep_copy_config(old_config)
    
    # 检查并报告缺少的参数
    def check_missing_params(config, required_params, section_name):
        """检查配置中是否缺少必要的参数，并报告缺失的参数。
        
        Args:
            config (dict): 配置字典。
            required_params (dict): 必要参数及其默认值。
            section_name (str): 配置节名称。
        """
        for param, default_value in required_params.items():
            if param not in config:
                config[param] = default_value
                LOGGER.warning(f"配置节 '{section_name}' 中缺少参数 '{param}'，使用默认值: {default_value}")
    
    # 处理 monitor_settings
    if 'monitor_settings' in migrated_config:
        monitor_settings = migrated_config['monitor_settings']
        
        # 处理 process_name_list -> process_name
        if 'process_name_list' in monitor_settings:
            process_list = monitor_settings['process_name_list']
            if isinstance(process_list, list) and process_list:
                migrated_config['monitor_settings']['process_name'] = process_list[0]
                LOGGER.info(f"已迁移 process_name_list 到 process_name: {migrated_config['monitor_settings']['process_name']}")
        elif 'process_name' not in monitor_settings:
            default_process_name = DEFAULT_VALUES['monitor_settings']['process_name']
            migrated_config['monitor_settings']['process_name'] = default_process_name
            LOGGER.warning(f"配置节 'monitor_settings' 中缺少参数 'process_name'，使用默认值: {default_process_name}")
        
        # 处理时间参数
        if 'timeout_warning_interval_ms' in monitor_settings:
            migrated_config['monitor_settings']['timeout_warning_interval'] = ms_to_hms(monitor_settings['timeout_warning_interval_ms'])
            LOGGER.info(f"已迁移 timeout_warning_interval_ms 到 timeout_warning_interval: {migrated_config['monitor_settings']['timeout_warning_interval']}")
        elif 'timeout_warning_interval' not in monitor_settings:
            default_timeout = DEFAULT_VALUES['monitor_settings']['timeout_warning_interval']
            migrated_config['monitor_settings']['timeout_warning_interval'] = default_timeout
            LOGGER.warning(f"配置节 'monitor_settings' 中缺少参数 'timeout_warning_interval'，使用默认值: {default_timeout}")
        
        if 'monitor_loop_interval_ms' in monitor_settings:
            migrated_config['monitor_settings']['monitor_loop_interval'] = ms_to_hms(monitor_settings['monitor_loop_interval_ms'])
            LOGGER.info(f"已迁移 monitor_loop_interval_ms 到 monitor_loop_interval: {migrated_config['monitor_settings']['monitor_loop_interval']}")
        elif 'monitor_loop_interval' not in monitor_settings:
            default_interval = DEFAULT_VALUES['monitor_settings']['monitor_loop_interval']
            migrated_config['monitor_settings']['monitor_loop_interval'] = default_interval
            LOGGER.warning(f"配置节 'monitor_settings' 中缺少参数 'monitor_loop_interval'，使用默认值: {default_interval}")
        
        # 清理旧参数
        old_monitor_params = ['process_name_list', 'timeout_warning_interval_ms', 'monitor_loop_interval_ms']
        for old_param in old_monitor_params:
            if old_param in migrated_config['monitor_settings']:
                del migrated_config['monitor_settings'][old_param]
                LOGGER.info(f"已删除旧参数: monitor_settings.{old_param}")
    else:
        # 如果 monitor_settings 不存在，创建并添加默认值
        migrated_config['monitor_settings'] = DEFAULT_VALUES['monitor_settings'].copy()
        LOGGER.warning(f"配置中缺少节 'monitor_settings'，创建并使用默认值")
    
    # 处理 wait_process_settings
    if 'wait_process_settings' in migrated_config:
        wait_settings = migrated_config['wait_process_settings']
        
        # 处理时间参数
        if 'max_wait_time_ms' in wait_settings:
            migrated_config['wait_process_settings']['max_wait_time'] = ms_to_hms(wait_settings['max_wait_time_ms'])
            LOGGER.info(f"已迁移 max_wait_time_ms 到 max_wait_time: {migrated_config['wait_process_settings']['max_wait_time']}")
        elif 'max_wait_time' not in wait_settings:
            default_max_wait = DEFAULT_VALUES['wait_process_settings']['max_wait_time']
            migrated_config['wait_process_settings']['max_wait_time'] = default_max_wait
            LOGGER.warning(f"配置节 'wait_process_settings' 中缺少参数 'max_wait_time'，使用默认值: {default_max_wait}")
        
        if 'wait_process_check_interval_ms' in wait_settings:
            migrated_config['wait_process_settings']['wait_process_check_interval'] = ms_to_hms(wait_settings['wait_process_check_interval_ms'])
            LOGGER.info(f"已迁移 wait_process_check_interval_ms 到 wait_process_check_interval: {migrated_config['wait_process_settings']['wait_process_check_interval']}")
        elif 'wait_process_check_interval' not in wait_settings:
            default_check_interval = DEFAULT_VALUES['wait_process_settings']['wait_process_check_interval']
            migrated_config['wait_process_settings']['wait_process_check_interval'] = default_check_interval
            LOGGER.warning(f"配置节 'wait_process_settings' 中缺少参数 'wait_process_check_interval'，使用默认值: {default_check_interval}")
        
        # 清理旧参数
        old_wait_params = ['max_wait_time_ms', 'wait_process_check_interval_ms']
        for old_param in old_wait_params:
            if old_param in migrated_config['wait_process_settings']:
                del migrated_config['wait_process_settings'][old_param]
                LOGGER.info(f"已删除旧参数: wait_process_settings.{old_param}")
    else:
        # 如果 wait_process_settings 不存在，创建并添加默认值
        migrated_config['wait_process_settings'] = DEFAULT_VALUES['wait_process_settings'].copy()
        LOGGER.warning(f"配置中缺少节 'wait_process_settings'，创建并使用默认值")
    
    # 处理 push_settings
    if 'push_settings' in migrated_config and 'push_error_retry' in migrated_config['push_settings']:
        push_error_retry = migrated_config['push_settings']['push_error_retry']
        
        if 'retry_interval_ms' in push_error_retry:
            migrated_config['push_settings']['push_error_retry']['retry_interval'] = ms_to_hms(push_error_retry['retry_interval_ms'])
            LOGGER.info(f"已迁移 retry_interval_ms 到 retry_interval: {migrated_config['push_settings']['push_error_retry']['retry_interval']}")
        elif 'retry_interval' not in push_error_retry:
            default_retry_interval = DEFAULT_VALUES['push_settings']['push_error_retry']['retry_interval']
            migrated_config['push_settings']['push_error_retry']['retry_interval'] = default_retry_interval
            LOGGER.warning(f"配置节 'push_settings.push_error_retry' 中缺少参数 'retry_interval'，使用默认值: {default_retry_interval}")
        
        # 清理旧参数
        if 'retry_interval_ms' in migrated_config['push_settings']['push_error_retry']:
            del migrated_config['push_settings']['push_error_retry']['retry_interval_ms']
            LOGGER.info(f"已删除旧参数: push_settings.push_error_retry.retry_interval_ms")
    elif 'push_settings' not in migrated_config:
        # 如果 push_settings 不存在，创建并添加默认值
        # 递归深拷贝默认值，避免引用问题
        def deep_copy_defaults(defaults):
            if isinstance(defaults, dict):
                copied = {}
                for key, value in defaults.items():
                    copied[key] = deep_copy_defaults(value)
                return copied
            else:
                return defaults
        
        migrated_config['push_settings'] = deep_copy_defaults(DEFAULT_VALUES['push_settings'])
        LOGGER.warning(f"配置中缺少节 'push_settings'，创建并使用默认值")
    elif 'push_error_retry' not in migrated_config['push_settings']:
        # 如果 push_error_retry 不存在，创建并添加默认值
        migrated_config['push_settings']['push_error_retry'] = DEFAULT_VALUES['push_settings']['push_error_retry'].copy()
        LOGGER.warning(f"配置节 'push_settings' 中缺少子节 'push_error_retry'，创建并使用默认值")
    
    # 处理 external_program_settings
    if 'external_program_settings' not in migrated_config:
        # 如果 external_program_settings 不存在，创建并添加默认值
        migrated_config['external_program_settings'] = DEFAULT_VALUES['external_program_settings'].copy()
        LOGGER.warning(f"配置中缺少节 'external_program_settings'，创建并使用默认值")
    
    # 处理 log_settings
    if 'log_settings' not in migrated_config:
        # 如果 log_settings 不存在，创建并添加默认值
        migrated_config['log_settings'] = DEFAULT_VALUES['log_settings'].copy()
        LOGGER.warning(f"配置中缺少节 'log_settings'，创建并使用默认值")
    
    return migrated_config


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
        else:
            # 保留用户配置中存在但默认配置中不存在的键
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

    # 检查是否为旧版本配置
    is_old_version = False
    if 'monitor_settings' in user_config:
        ms = user_config['monitor_settings']
        if 'process_name_list' in ms or 'timeout_warning_interval_ms' in ms or 'monitor_loop_interval_ms' in ms:
            is_old_version = True
    
    if 'wait_process_settings' in user_config:
        ws = user_config['wait_process_settings']
        if 'max_wait_time_ms' in ws or 'wait_process_check_interval_ms' in ws:
            is_old_version = True
    
    if 'push_settings' in user_config and 'push_error_retry' in user_config['push_settings']:
        pr = user_config['push_settings']['push_error_retry']
        if 'retry_interval_ms' in pr:
            is_old_version = True

    # 加载默认配置
    default_config = get_default_config()
    
    if is_old_version:
        # 旧版本配置，进行迁移
        LOGGER.info("检测到旧版本配置，开始迁移...")
        # 迁移旧版本配置
        migrated_config = migrate_old_config(user_config)
        
        # 合并迁移后的配置到默认配置中
        merged_config = merge_configs(migrated_config, default_config)
        
        # 存档旧配置并写回新配置
        old_config_file = f"{os.path.splitext(config_file)[0]}.old.v2{os.path.splitext(config_file)[1]}"
        try:
            with open(old_config_file, 'w', encoding='utf-8') as f:
                yaml.dump(user_config, f)
            LOGGER.info(f"已将旧版本配置存档为: {os.path.abspath(old_config_file)}")
        except Exception as e:
            LOGGER.error(f"无法存档旧配置文件: {e}")
        
        # 写回迁移后的配置
        try:
            yaml = YAML()
            yaml.indent(mapping=2, sequence=4, offset=2)
            yaml.preserve_quotes = True
            with open(config_file, 'w', encoding='utf-8') as f:
                yaml.dump(merged_config, f)
            LOGGER.info(f"已将迁移后的配置写回: {os.path.abspath(config_file)}")
        except Exception as e:
            LOGGER.error(f"无法写回配置文件: {e}")
    else:
        # 新版本配置，检查参数缺失
        LOGGER.info("检测到新版本配置，检查参数缺失...")
        # 合并用户配置到默认配置中
        merged_config = merge_configs(user_config, default_config)
        
        # 检查用户配置是否缺少默认配置中的键
        updated = False
        
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


