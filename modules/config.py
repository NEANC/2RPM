#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from modules.utils import (
    build_push_channel_node,
    parse_push_channels,
    parse_time_string,
    push_channel_signature,
)

LOGGER = logging.getLogger(__name__)

# 默认配置值集中管理（V4 精简键名）
DEFAULT_VALUES = {
    'monitor': {
        'monitor_mode': 'psutil',
        'process_name': 'notepad.exe',
        'timeout_interval': '15m',
        'loop_interval': '1s',
    },
    'task': {
        'task_name': '\\Custom\\MyTask',
        'lookback_minutes': 10,
    },
    'launch': {
        'type': 'program',
        'path': 'C:\\path\\to\\target.exe',
        'task_name': '\\Custom\\MyTask',
    },
    'wait': {
        'max_wait': '30s',
        'check_interval': '1s',
    },
    'push': {
        'templates': {
            'on_end': {
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
            'on_timeout': {
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
            'on_wait_timeout': {
                'enable': True,
                'title': '等待超时未运行报告',
                'content': (
                    '主机: {host_name}\n\n'
                    '当前时间: {current_time}\n\n'
                    '进程: {process_name} 超时未运行\n\n'
                    '已等待时间: {process_wait_time}\n\n'
                ),
            },
            'on_external': {
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
            'channels': [
                {'provider': 'serverchan', 'sckey': 'SCTxxxx'},
                {'provider': 'qmsg', 'key': 'xxx', 'qq': 'xxx'},
                {'provider': 'dingtalk', 'token': 'xxx', 'secret': 'xxx'},
                {'provider': 'lark', 'webhook': 'xxx', 'sign': 'xxx'},
                {'provider': 'smtp', 'host': 'xxx', 'user': 'xxx',
                 'password': 'xxx', 'port': 587, 'ssl': True},
            ],
        },
        'retry': {
            'interval': '3s',
            'max_count': 3,
        },
    },
    'external': {
        'on_end': 'C:\\path\\to\\your\\script.bat',
        'on_timeout': 'C:\\path\\to\\another_script.bat',
        'timeout_threshold': 3,
        'on_wait_timeout': 'C:\\path\\to\\wait_timeout_script.bat',
    },
    'log': {
        'log_directory': 'logs',
        'max_log_files': 15,
        'retention_days': 3,
    },
}

# 注释集中管理（V4 精简键名与节名）
COMMENTS = {
    'monitor': {
        '_comment': (
            "监视设置\n"
            "- 监视程序相关配置\n"
        ),
        'monitor_mode': (
            "\n监视模式，可选值: psutil / task_scheduler\n"
            "- psutil: 通过进程名轮询检测程序是否运行（默认）\n"
            "- task_scheduler: 通过事件日志获取计划任务PID后监视\n"
        ),
        'process_name': (
            "\n要监视的进程名称"
        ),
        'timeout_interval': (
            "\n超时警告间隔，默认值15分钟，支持 H/M/S 格式\n"
        ),
        'loop_interval': (
            "\n监视循环间隔，默认值1秒，支持 H/M/S 格式\n"
        ),
    },
    'task': {
        '_comment': (
            "计划任务监视设置（仅在 monitor_mode 为 task_scheduler 时生效）\n"
            "- 通过 Windows 事件日志获取计划任务创建的进程 PID 进行监视\n"
        ),
        'task_name': (
            "\n要监视的计划任务名称\n"
            "- 完整路径格式: \\\\Folder\\\\TaskName\n"
            "- 部分匹配也可工作，如 MyTask\n"
        ),
        'lookback_minutes': (
            "\n事件回溯时间（分钟），查询最近多少分钟内的事件\n"
            "- 默认值: 10\n"
        ),
    },
    'launch': {
        '_comment': (
            "主动拉起目标设置\n"
            "- 配置程序主动拉起目标程序或计划任务并获取 PID 进行监视\n"
            "- 仅在 monitor.monitor_mode 为 launch 时生效\n"
        ),
        'type': (
            "\n拉起类型，可选值: program / task\n"
            "- program: 直接启动可执行程序\n"
            "- task: 触发 Windows 计划任务后监视其进程\n"
        ),
        'path': (
            "\n要拉起的可执行文件路径\n"
            "- 仅 type=program 时使用\n"
            "- 例如: C:\\app\\target.exe"
        ),
        'task_name': (
            "\n要触发的计划任务名称\n"
            "- 仅 type=task 时使用\n"
            "- 完整路径格式: \\\\Folder\\\\TaskName\n"
        ),
    },
    'wait': {
        '_comment': (
            "等待进程设置\n"
            "- 配置等待进程启动的相关参数\n"
        ),
        'max_wait': (
            "\n最长等待时间，默认值: 30秒，支持 H/M/S 格式\n"
        ),
        'check_interval': (
            "\n等待进程检查间隔，默认值1秒，支持 H/M/S 格式\n"
        ),
    },
    'push': {
        '_comment': (
            "推送设置\n"
            "- 包含推送通知的相关配置\n"
        ),
        'templates': {
            '_comment': (
                "推送模板配置\n"
                "- 可自定义通知的标题和内容\n"
            ),
            '_comment_extra': (
                "\n支持的变量如下：\n"
                "主机名: {host_name}\n"
                "当前时间(YY-mm-dd HH-MM-SS): {current_time}\n"
                "当前时间(HH-MM-SS): {short_current_time}\n"
                "进程名: {process_name}\n"
                "进程PID: {process_pid}\n"
                "进程运行时间: {process_run_time}\n"
                "进程积累等待时间: {process_wait_time}\n"
                "调用的程序名: {external_program_name}\n"
                "调用的程序路径: {external_program_path}\n"
            ),
            'on_end': (
                "\n进程结束通知模板\n"
                "- enable: 是否启用该通知\n"
                "- title: 通知标题\n"
                "- content: 通知内容\n"
            ),
            'on_timeout': (
                "\n进程超时运行警告模板\n"
                "- enable: 是否启用该通知\n"
                "- title: 通知标题\n"
                "- content: 通知内容\n"
            ),
            'on_wait_timeout': (
                "\n等待超时未运行报告模板\n"
                "- enable: 是否启用该通知\n"
                "- title: 通知标题\n"
                "- content: 通知内容\n"
            ),
            'on_external': (
                "\n外部程序执行通知模板\n"
                "- enable: 是否启用该通知\n"
                "- title: 通知标题\n"
                "- content: 通知内容\n"
            ),
        },
        'push_channel_settings': {
            '_comment': (
                "\n推送通道设置\n"
            ),
            'channels': (
                "\nOnePush 推送通道配置\n"
                "- 参考下列示例填入对应参数，已支持多通道，新增通道仅需添加一行参数配置：\n"
                "    channels:\n"
                "      - {provider: bark, key: xxx}\n"
                "      - {provider: discord, webhook: xxx}\n"
                "      - {provider: telegram, token: xxx, userid: xxx, api_url: xxx}\n"
                "      - {provider: serverchan, sckey: SCTxxxx}\n"
                "      - {provider: serverchanturbo, sctkey: sctpxxxx}\n"
                "      - {provider: wechatworkapp, corpid: xxx, corpsecret: xxx, agentid: xxx}\n"
                "      - {provider: wechatworkbot, key: xxx}\n"
                "      - {provider: pushplus, token: xxx}\n"
                "      - {provider: gocqhttp, endpoint: xxx, user_id: xxx}\n"
                "      - {provider: qmsg, key: xxx, qq: xxx}\n"
                "      - {provider: dingtalk, token: xxx, secret: xxx}\n"
                "      - {provider: lark, webhook: xxx, sign: xxx}\n"
                "      - {provider: smtp, host: xxx, user: xxx, password: xxx, port: 587, ssl: true}\n"
            ),
        },
        'retry': {
            '_comment': "\n推送错误重试设置\n",
            'interval': (
                "\n重试间隔，默认值: 3秒，支持 H/M/S 格式\n"
            ),
            'max_count': "\n最大重试次数，默认值: 3次",
        },
    },
    'external': {
        '_comment': (
            "外部程序调用设置\n"
        ),
        'on_end': (
            "\n进程结束时触发的外部程序/BAT脚本的详细路径，例如: "
            "C:\\path\\end\\script.bat"
        ),
        'on_timeout': (
            "\n进程运行超时后触发的外部程序/BAT脚本的详细路径，例如: "
            "C:\\path\\timeout\\timeout_script.bat"
        ),
        'timeout_threshold': (
            "\n设置进程运行超时次数阈值，达到该次数后执行触发外部程序调用，默认值: 3\n"
        ),
        'on_wait_timeout': (
            "\n等待进程启动超时后触发的外部程序/BAT脚本的详细路径\n"
            "- 例如: C:\\path\\wait_timeout\\wait_timeout_script.bat"
        ),
    },
    'log': {
        '_comment': (
            "日志设置\n"
            "- 配置日志输出的相关参数\n"
        ),
        'log_directory': (
            "\n日志输出的目录，默认为程序目录下的 'logs' 文件夹\n"
            "- 请根据需要进行设置，例如: C:\\Path\\2RPM\\v2\\logs"
        ),
        'max_log_files': "\n日志最大保存数量，默认值: 15个",
        'retention_days': "\n日志保存天数，单位为天，默认值: 3天",
    },
}


def _check_missing_params(config, required_params, section_name):
    """检查配置中是否缺少必要的参数，并补充缺失参数的默认值

    Args:
        config (dict): 配置字典
        required_params (dict): 必要参数及其默认值
        section_name (str): 配置节名称

    Returns:
        bool: 是否发生了参数补充操作
    """
    updated = False
    for param, default_value in required_params.items():
        # 只在当前配置节中添加缺少的参数，避免跨节添加
        if param not in config:
            config[param] = default_value
            LOGGER.warning(
                f"配置 '{section_name}' 中缺少参数 '{param}'，"
                f"使用默认值: {default_value}"
            )
            updated = True
        elif isinstance(default_value, CommentedMap) and isinstance(config.get(param), dict):
            # 递归检查嵌套配置
            sub_updated = _check_missing_params(
                config[param], default_value, f"{section_name}.{param}"
            )
            updated = updated or sub_updated
    return updated


def _clean_config(config, default_config, section_name='root'):
    """清理配置文件，移除不属于对应配置块的配置项

    Args:
        config (dict): 配置字典
        default_config (CommentedMap): 默认配置字典
        section_name (str): 配置块名称

    Returns:
        bool: 是否发生了配置项移除操作
    """
    removed = False
    keys_to_remove = []
    for key in config:
        if key not in default_config:
            keys_to_remove.append(key)
        elif isinstance(config[key], dict) and isinstance(default_config.get(key), CommentedMap):
            # 默认值为空字典代表自由格式容器（如 push_channel_settings），
            # 其内容由用户自定义，跳过清理以免误删推送通道参数
            if len(default_config[key]) == 0:
                continue
            sub_removed = _clean_config(
                config[key], default_config[key], f"{section_name}.{key}"
            )
            removed = removed or sub_removed
    for key in keys_to_remove:
        LOGGER.warning(
            f"移除不属于配置块 '{section_name}' 的配置项: {key}"
        )
        del config[key]
        removed = True
    return removed


def _create_commented_map(config_dict):
    """递归创建 CommentedMap 结构，嵌入默认值

    将普通 dict 转换为 ruamel 的 CommentedMap，以支持注释嵌入
    push_channel 列表渲染为流式块序列

    Args:
        config_dict (dict): 默认配置字典

    Returns:
        CommentedMap: 递归构造的 CommentedMap 结构
    """
    if not isinstance(config_dict, dict):
        return config_dict

    commented_map = CommentedMap()
    for key, value in config_dict.items():
        if isinstance(value, dict):
            commented_map[key] = _create_commented_map(value)
        elif key == 'channels' and isinstance(value, list):
            # channels 列表渲染为流式块序列（每元素为单行花括号映射）
            commented_map[key] = build_push_channel_node(value)
        else:
            commented_map[key] = value
    return commented_map


def _make_write_yaml():
    """创建用于写回配置文件的 YAML 实例

    Returns:
        YAML: 已配置缩进与引号保留的 YAML 实例
    """
    yaml = YAML()
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.preserve_quotes = True
    return yaml


def get_default_config(for_file_creation=False):
    """获取默认配置，并嵌入完整的注释

    Args:
        for_file_creation (bool): 是否用于创建新配置文件
            为 True 时会在节前添加空行分隔

    Returns:
        CommentedMap: 包含注释的默认配置字典
    """
    LOGGER.info("正在读取默认配置")

    config = _create_commented_map(DEFAULT_VALUES)

    LOGGER.info("正在应用注释到默认配置")
    apply_comments(config, COMMENTS, blank_before_section=for_file_creation)

    LOGGER.info("内置配置读取完成")
    return config


def _clear_comments(config_section):
    """清除配置节中已有的注释，避免重复写入

    Args:
        config_section (CommentedMap): 配置的一个部分

    Returns:
        None
    """
    if not hasattr(config_section, 'ca'):
        return
    config_section.ca.comment = None
    if hasattr(config_section.ca, 'items'):
        config_section.ca.items.clear()


def apply_comments(config_section, comments_section, depth=0,
                   blank_before_section=False, is_top_level=True):
    """递归地将注释应用到配置字典中

    将每个配置项/子节的注释统一放置在其键的上方，并使注释缩进与所在
    层级的配置缩进保持一致（例外：节的变量列表说明保持无缩进）

    Args:
        config_section (CommentedMap): 配置的一个部分
        comments_section (dict): 对应的注释字典
        depth (int): 当前嵌套层级，用于计算注释缩进（每层 2 空格）
        blank_before_section (bool): 是否在顶层节（首个节除外）前插入空行
        is_top_level (bool): 当前是否为顶层节容器

    Returns:
        None
    """
    # 守卫：非字典注释无需处理
    if not isinstance(comments_section, dict):
        return

    _clear_comments(config_section)

    indent = depth * 2
    first_section_done = False
    for key, value in config_section.items():
        comment = comments_section.get(key)

        if isinstance(comment, dict):
            section_text = comment.get('_comment', '')
            # 顶层非首个节按需在节前插入空行进行分隔
            if is_top_level and blank_before_section and first_section_done:
                section_text = '\n' + section_text
            if section_text:
                config_section.yaml_set_comment_before_after_key(
                    key, before=section_text, indent=indent)
            # 变量列表说明等附加注释保持无缩进（例外）
            extra_text = comment.get('_comment_extra')
            if extra_text:
                config_section.yaml_set_comment_before_after_key(
                    key, before=extra_text, indent=0)
            apply_comments(value, comment, depth=depth + 1,
                           blank_before_section=blank_before_section,
                           is_top_level=False)
        elif isinstance(comment, str):
            # 普通参数：将注释设置在参数键上方
            config_section.yaml_set_comment_before_after_key(
                key, before=comment, indent=indent)

        first_section_done = True


def create_default_config(config_file):
    """创建默认配置文件

    Args:
        config_file (str): 配置文件路径

    Raises:
        Exception: 如果无法创建配置文件
    """
    LOGGER.info(f"正在创建配置文件: {os.path.abspath(config_file)}")
    default_config = get_default_config(for_file_creation=True)
    try:
        yaml = _make_write_yaml()
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(default_config, f)
        LOGGER.info(f"配置文件创建成功: {os.path.abspath(config_file)}")
    except Exception as e:
        LOGGER.critical(f"创建配置文件失败: {e}")
        raise


def correct_push_channel_config(user_config):
    """规范化配置中的推送通道，统一回写为标准流式格式

    定位 push.push_channel_settings.channels 节点，将用户填写的
    各种写法（标准字典、无参数头、乱序、块序列或以 ';' 分割的字符串）解析为标准
    通道，并统一重建写回节点为元素均为流式映射的 YAML 块序列（单通道与多通道写法
    一致，无需引号包裹整行）
    密钥别名（如 serverchan 的 key -> sckey）在解析过程中一并纠正
    仅当规范化后的节点与原值存在实质差异时才替换，避免无谓的写回

    Args:
        user_config (dict): 用户配置字典，将被就地修改

    Returns:
        bool: 是否发生了 channels 节点的规范化替换
    """
    push_section = user_config.get('push')
    if not isinstance(push_section, dict):
        return False

    channel_settings = push_section.get('push_channel_settings')
    if not isinstance(channel_settings, dict):
        return False

    raw_value = channel_settings.get('channels')

    # 守卫：空容器或缺失视为未配置，无需规范化
    if raw_value is None:
        return False
    if isinstance(raw_value, dict) and not raw_value:
        return False
    if isinstance(raw_value, str) and not raw_value.strip():
        return False

    channels = parse_push_channels(raw_value)
    if not channels:
        return False

    new_node = build_push_channel_node(channels)

    # 守卫：规范化前后签名一致时无需替换，避免无谓写回
    if push_channel_signature(raw_value) == push_channel_signature(new_node):
        return False

    channel_settings['channels'] = new_node
    providers = ', '.join(channel.get('provider', '') for channel in channels)
    LOGGER.warning(
        f"推送通道配置已规范化为标准格式（通道: {providers}）"
    )
    return True


def _normalize_task_lookback_minutes(task_section):
    """归一化 task.lookback_minutes 配置。

    规则：
    - 字符串与数值均可接受，保留字符串语义（如 '10'）
    - 负值自动去符号为正
    - 无法解析或非法类型回退默认值
    - 归一化成功后回写配置原文，确保配置持久化一致

    说明：
    - 保留字符串输出（例如 '10'）可以兼容历史与手工编辑行为
    """
    if not isinstance(task_section, dict):
        return False

    raw_value = task_section.get('lookback_minutes')
    if raw_value is None:
        return False

    # 空字符串回退默认值
    if isinstance(raw_value, str) and not raw_value.strip():
        LOGGER.warning('task.lookback_minutes 不能为空，已回退默认值')
        task_section['lookback_minutes'] = DEFAULT_VALUES['task']['lookback_minutes']
        return True

    normalized_for_store = None
    if isinstance(raw_value, str):
        raw_str = raw_value.strip()
        if raw_str.startswith('-'):
            raw_str = raw_str.lstrip('-').strip()
            LOGGER.warning(
                f"检测到 task.lookback_minutes 负值，已去除负号: {raw_value!r}"
            )
        if not raw_str:
            LOGGER.warning('task.lookback_minutes 去符号后为空，已回退默认值')
            task_section['lookback_minutes'] = DEFAULT_VALUES['task']['lookback_minutes']
            return True
        try:
            normalized = abs(int(raw_str))
        except ValueError:
            LOGGER.warning(
                f"task.lookback_minutes 无法解析为整数: {raw_value!r}，已回退默认值"
            )
            task_section['lookback_minutes'] = DEFAULT_VALUES['task']['lookback_minutes']
            return True
        normalized_for_store = str(normalized)
    elif isinstance(raw_value, (int, float)):
        normalized = abs(raw_value)
        normalized_for_store = int(normalized)
    else:
        LOGGER.warning(
            f"task.lookback_minutes 类型不合法 ({type(raw_value).__name__})，已回退默认值"
        )
        task_section['lookback_minutes'] = DEFAULT_VALUES['task']['lookback_minutes']
        return True

    if task_section.get('lookback_minutes') == normalized_for_store:
        return False

    task_section['lookback_minutes'] = normalized_for_store
    return True


def _normalize_time_like_config(section, field_key, default_value, field_label):
    """归一化时间类配置值。

    规则：
    - 字符串与数字均可接受
    - 负值去除前导 '-' 并回写为正值字符串
    - 无效值回退默认值
    - 回写时仅当实际发生变更才落盘
    """
    if not isinstance(section, dict):
        return False

    raw_value = section.get(field_key)
    if raw_value is None:
        return False

    # 空字符串直接回退默认值
    if isinstance(raw_value, str):
        raw_str = raw_value.strip()
        if not raw_str:
            LOGGER.warning(f"{field_label} 不能为空，已回退默认值")
            section[field_key] = default_value
            return True

        # 允许时间格式字符串并做统一清理（去空白、去负号）
        normalized = raw_str
        if raw_str.startswith('-'):
            normalized = raw_str.lstrip('-').strip()
            LOGGER.warning(f"检测到 {field_label} 负值，已去除负号: {raw_value!r}")

        if not normalized:
            LOGGER.warning(f"{field_label} 去符号后为空，已回退默认值")
            section[field_key] = default_value
            return True

        # parse_time_string 负责校验 h/m/s 等合法时间串
        try:
            parse_time_string(normalized)
        except (ValueError, TypeError, AttributeError):
            LOGGER.warning(
                f"{field_label} 配置无效，已回退默认值: {raw_value!r}"
            )
            section[field_key] = default_value
            return True
    elif isinstance(raw_value, (int, float)):
        # 数值会在边界处理时转成字符串，保证时间格式仍是可读文本
        try:
            normalized = str(abs(int(raw_value)))
        except (ValueError, TypeError):
            LOGGER.warning(
                f"{field_label} 配置无效，已回退默认值: {raw_value!r}"
            )
            section[field_key] = default_value
            return True
        if raw_value < 0:
            LOGGER.warning(f"检测到 {field_label} 负值，已去除负号: {raw_value!r}")
        try:
            parse_time_string(normalized)
        except (ValueError, TypeError, AttributeError):
            LOGGER.warning(
                f"{field_label} 配置无效，已回退默认值: {raw_value!r}"
            )
            section[field_key] = default_value
            return True
    else:
        # 其它类型不在支持范围内，直接回退默认值
        LOGGER.warning(
            f"{field_label} 类型不合法 ({type(raw_value).__name__})，已回退默认值"
        )
        section[field_key] = default_value
        return True

    # 仅当解析后的值与原值不一致时才更新，避免无意义的脏写
    if section.get(field_key) == normalized:
        return False

    section[field_key] = normalized
    return True



def _normalize_positive_int_config(section, field_key, default_value, field_label):
    """归一化整数类配置值。

    规则：
    - 字符串、整数、浮点数可接受
    - 负值去绝对值后回写
    - 非法值回退默认值
    """
    if not isinstance(section, dict):
        return False

    raw_value = section.get(field_key)
    if raw_value is None:
        return False

    # 字符串输入：先清理空白，再处理前缀负号，最后尝试转 int
    if isinstance(raw_value, str):
        raw_stripped = raw_value.strip()
        if not raw_stripped:
            LOGGER.warning(f"{field_label} 不能为空，已回退默认值")
            section[field_key] = default_value
            return True

        if raw_stripped.startswith('-'):
            raw_stripped = raw_stripped.lstrip('-').strip()
            LOGGER.warning(
                f"检测到 {field_label} 负值，已去除负号: {raw_value!r}"
            )

        if not raw_stripped:
            LOGGER.warning(f"{field_label} 去符号后为空，已回退默认值")
            section[field_key] = default_value
            return True

        try:
            normalized = int(raw_stripped)
        except ValueError:
            LOGGER.warning(
                f"{field_label} 无法解析为整数，已回退默认值: {raw_value!r}"
            )
            section[field_key] = default_value
            return True
    # 数值输入：仅允许 int/float，统一转为正整数，兼容 3.0 这类浮点数
    elif isinstance(raw_value, (int, float)):
        normalized = int(abs(raw_value))
        if raw_value != normalized:
            LOGGER.warning(
                f"检测到 {field_label} 负值，已去除负号: {raw_value!r}"
            )
    # 其它类型直接回退默认值
    else:
        LOGGER.warning(
            f"{field_label} 类型不合法 ({type(raw_value).__name__})，已回退默认值"
        )
        section[field_key] = default_value
        return True

    # 仅当值确实发生变化时才回写，避免无效改动触发脏写
    if section.get(field_key) == normalized:
        return False

    section[field_key] = normalized
    return True


def _normalize_config_writable_values(user_config):
    """统一归一化配置中的可写负值参数。

    返回：是否发生任一字段变更
    """
    updated = False

    monitor_section = user_config.get('monitor', {})
    wait_section = user_config.get('wait', {})
    push_section = user_config.get('push', {})
    retry_section = push_section.get('retry', {}) if isinstance(push_section, dict) else {}
    external_section = user_config.get('external', {})
    log_section = user_config.get('log', {})

    # monitor.*：监控与轮询行为，支持负值仅用于兼容配置，不保留负号
    updated = _normalize_time_like_config(
        monitor_section,
        'timeout_interval',
        DEFAULT_VALUES['monitor']['timeout_interval'],
        'monitor.timeout_interval'
    ) or updated
    updated = _normalize_time_like_config(
        monitor_section,
        'loop_interval',
        DEFAULT_VALUES['monitor']['loop_interval'],
        'monitor.loop_interval'
    ) or updated

    # wait.*：等待行为配置，保持 H/M/S 字符串语义
    updated = _normalize_time_like_config(
        wait_section,
        'max_wait',
        DEFAULT_VALUES['wait']['max_wait'],
        'wait.max_wait'
    ) or updated
    updated = _normalize_time_like_config(
        wait_section,
        'check_interval',
        DEFAULT_VALUES['wait']['check_interval'],
        'wait.check_interval'
    ) or updated

    # push.retry.interval：重试间隔也按时间串处理
    updated = _normalize_time_like_config(
        retry_section,
        'interval',
        DEFAULT_VALUES['push']['retry']['interval'],
        'push.retry.interval'
    ) or updated

    # push.retry.max_count / external.timeout_threshold / log.*：整数类配置
    updated = _normalize_positive_int_config(
        retry_section,
        'max_count',
        DEFAULT_VALUES['push']['retry']['max_count'],
        'push.retry.max_count'
    ) or updated

    # external.timeout_threshold：超时阈值，必须为正整数
    updated = _normalize_positive_int_config(
        external_section,
        'timeout_threshold',
        DEFAULT_VALUES['external']['timeout_threshold'],
        'external.timeout_threshold'
    ) or updated

    # log.max_log_files、log.retention_days：日志归档参数，统一转为非负整数字符语义
    updated = _normalize_positive_int_config(
        log_section,
        'max_log_files',
        DEFAULT_VALUES['log']['max_log_files'],
        'log.max_log_files'
    ) or updated
    updated = _normalize_positive_int_config(
        log_section,
        'retention_days',
        DEFAULT_VALUES['log']['retention_days'],
        'log.retention_days'
    ) or updated

    return updated


def merge_configs(user_config, default_config):
    """将用户配置合并到默认配置中

    Args:
        user_config (dict): 用户配置字典
        default_config (CommentedMap): 默认配置字典

    Returns:
        CommentedMap: 合并后的配置字典
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
    """加载配置文件

    Args:
        config_file (str): 配置文件路径

    Returns:
        dict: 配置字典

    Raises:
        Exception: 如果配置文件无效
    """
    LOGGER.info(f"正在加载配置文件: {os.path.abspath(config_file)}")
    if not os.path.exists(config_file):
        LOGGER.critical(f"配置文件不存在: {os.path.abspath(config_file)}")
        create_default_config(config_file)
        LOGGER.info("配置文件已生成，请根据需要修改配置文件后再次运行程序")
        input("请按任意键退出...")
        sys.exit(0)

    # 加载用户配置
    try:
        yaml = YAML()
        with open(config_file, 'r', encoding='utf-8') as f:
            user_config = yaml.load(f)
        if user_config is None:
            LOGGER.warning(
                f"配置文件内容为空或仅含注释：{os.path.abspath(config_file)}，"
                f"已按默认配置加载"
            )
            user_config = {}
        elif not isinstance(user_config, dict):
            LOGGER.error(
                f"配置文件内容类型不合法 ({type(user_config).__name__})，"
                f"应为字典结构，已按默认配置加载：{os.path.abspath(config_file)}"
            )
            user_config = {}
        LOGGER.info(f"成功加载配置文件: {os.path.abspath(config_file)}")
    except Exception as e:
        LOGGER.critical(f"无法加载配置文件: {os.path.abspath(config_file)}: {e}")
        sys.exit(1)

    # 加载默认配置
    default_config = get_default_config()

    updated = False

    # 确保所有必要的配置节都存在
    for section in default_config:
        if section not in user_config:
            user_config[section] = {}
            LOGGER.warning(f"配置中缺少节 '{section}'，创建默认配置")
            updated = True

    task_section = user_config.get('task', {})
    if isinstance(task_section, dict):
        updated = _normalize_task_lookback_minutes(task_section) or updated

    # 统一处理可写配置中的负值/非法值参数（负值去除负号并回写，非法值回退默认值）
    updated = _normalize_config_writable_values(user_config) or updated

    # 检查每个配置节中的参数
    for section, section_config in default_config.items():
        if isinstance(section_config, CommentedMap) and isinstance(user_config.get(section), dict):
            section_updated = _check_missing_params(
                user_config[section], section_config, section
            )
            updated = updated or section_updated

    # 清理用户配置
    cleaned = _clean_config(user_config, default_config, 'root')

    # 合并更新后的用户配置到默认配置中
    merged_config = merge_configs(user_config, default_config)

    # 规范化推送通道并统一回写为标准流式格式
    # 在合并后处理，确保流式映射节点不被 merge 递归展开而丢失流式风格
    corrected = correct_push_channel_config(merged_config)

    if updated or cleaned or corrected:
        LOGGER.info("配置文件已更新，正在执行无缝迁移")
        try:
            yaml = _make_write_yaml()
            with open(config_file, 'w', encoding='utf-8') as f:
                yaml.dump(merged_config, f)
            LOGGER.info(f"正在写回配置信息: {os.path.abspath(config_file)}")
        except Exception as e:
            LOGGER.error(f"无法写回配置文件 {os.path.abspath(config_file)}: {e}")

    LOGGER.info("配置参数版本差异检查完成")
    return merged_config
