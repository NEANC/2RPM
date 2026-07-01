#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
import subprocess

from onepush import all_providers, get_notifier
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

LOGGER = logging.getLogger(__name__)

# 由程序在发送时自动填充的参数，定位位置参数时需要跳过，避免占用用户参数槽位
PROGRAM_FILLED_PARAMS = {'title', 'content'}

# OnePush 已知推送渠道名单（小写），用于在无参数头写法中定位 provider 名称
KNOWN_PROVIDERS = {str(name).strip().lower() for name in all_providers()}

# 解析推送通道片段时复用的 YAML 实例（仅用于解析单个 {..} / [..] 片段）
_FRAGMENT_YAML = YAML()

# 支持剥离的成对包裹引号（直引号与中文弯引号）
QUOTE_PAIRS = {
    "'": "'",
    '"': '"',
    '\u2018': '\u2019',
    '\u201c': '\u201d',
}

# OnePush 各推送渠道密钥参数的别名映射
# 通用写法常用 key，而部分渠道要求特定参数名，此处将其纠正为 OnePush 要求的参数名
CHANNEL_KEY_ALIASES = {
    'serverchan': {'key': 'sckey'},
    'serverchanturbo': {'key': 'sctkey'},
    'pushdeer': {'key': 'pushkey'},
}


def strip_wrapping_quotes(value):
    """剥离字符串值首尾成对的包裹引号。

    支持英文直引号 ' 与 "，以及中文弯引号 '' 与 ""。
    仅当首尾为同一组成对引号时才剥离，非字符串值原样返回。

    Args:
        value: 待处理的值，可能为任意类型。

    Returns:
        剥离包裹引号后的字符串；若入参非字符串则原样返回。
    """
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if len(stripped) < 2:
        return stripped

    head, tail = stripped[0], stripped[-1]
    if QUOTE_PAIRS.get(head) == tail:
        return stripped[1:-1].strip()
    return stripped


def correct_channel_aliases(provider, params):
    """就地纠正推送渠道参数中的别名键名为 OnePush 要求的参数名。

    根据 provider 名称查找别名映射表，将用户使用的通用键名（如 key）
    纠正为对应渠道要求的参数名（如 serverchan 的 sckey）。直接在传入的
    params 上修改，因此对 dict 与 ruamel 的 CommentedMap 均可保留原有结构。

    Args:
        provider (str): 推送通道名称，大小写不敏感，允许带包裹引号。
        params (dict): 推送通道参数字典（不含 provider 键），将被就地修改。

    Returns:
        dict: 已纠正的键名映射，格式为 {旧键名: 新键名}；无纠正时为空字典。
    """
    provider = strip_wrapping_quotes(provider)
    if not isinstance(provider, str):
        return {}

    aliases = CHANNEL_KEY_ALIASES.get(provider.strip().lower(), {})
    if not aliases:
        return {}

    corrections = {}
    for old_key, new_key in aliases.items():
        # 守卫：别名键不存在，或目标键已存在时跳过，避免覆盖用户已正确填写的值
        if old_key not in params or new_key in params:
            continue
        params[new_key] = params.pop(old_key)
        corrections[old_key] = new_key
    return corrections


def get_provider_param_order(provider):
    """获取指定推送渠道用于位置参数推断的参数名顺序。

    依据 OnePush 各渠道声明的 _params（required 在前、optional 在后），
    并剔除由程序自动填充的参数（title、content），得到用户位置参数可占用的
    参数名顺序，用于将无键名的位置值映射到正确的参数名。

    Args:
        provider (str): 推送通道名称，大小写不敏感。

    Returns:
        list[str]: 位置参数对应的参数名顺序；渠道不存在时返回空列表。
    """
    if not isinstance(provider, str):
        return []

    try:
        notifier = get_notifier(provider.strip().lower())
    except Exception:
        # 未知渠道：无法推断参数名，返回空列表交由调用方处理
        return []

    params = getattr(notifier, '_params', None) or {}
    ordered = list(params.get('required', [])) + list(params.get('optional', []))
    return [name for name in ordered if name not in PROGRAM_FILLED_PARAMS]


def _assemble_channel(provider, named, positional):
    """将拆解出的 provider、命名参数与位置参数组装为标准通道字典。

    位置参数按渠道声明的参数顺序映射到参数名，并跳过已被命名参数占用的槽位；
    随后纠正密钥别名（如 serverchan 的 key -> sckey）。

    Args:
        provider (str): 推送通道名称。
        named (dict): 已带键名的参数（键名可能仍是别名）。
        positional (list): 无键名的位置参数值列表。

    Returns:
        dict: 标准通道字典，provider 键排在最前；解析失败时返回空字典。
    """
    if not provider or not isinstance(provider, str):
        return {}

    provider = provider.strip().lower()
    params = {str(key): value for key, value in named.items()}

    if positional:
        order = get_provider_param_order(provider)
        slot = 0
        for value in positional:
            # 跳过已被命名参数占用的参数名槽位
            while slot < len(order) and order[slot] in params:
                slot += 1
            if slot >= len(order):
                LOGGER.warning(
                    f"推送通道 '{provider}' 的位置参数过多，已忽略多余值: {value}"
                )
                break
            params[order[slot]] = value
            slot += 1

    correct_channel_aliases(provider, params)

    channel = {'provider': provider}
    channel.update(params)
    return channel


def _is_known_provider(name):
    """判断给定名称是否为 OnePush 已知推送渠道。

    剥离包裹引号并转为小写后，与已知渠道名单比对。

    Args:
        name: 待判断的名称，可能为任意类型。

    Returns:
        bool: 命中已知渠道名单时为 True，否则为 False。
    """
    if not isinstance(name, str):
        return False
    return strip_wrapping_quotes(name).strip().lower() in KNOWN_PROVIDERS


def _locate_provider(items):
    """在无参数头的键值项列表中定位 provider，并归类其余参数。

    依次按以下优先级定位 provider：
    1. 某项的键命中已知渠道名单（如 {dingtalk, secret: x} 或 {secret: x, dingtalk}）；
    2. 某项的值命中已知渠道名单（如 {SCTxxxx: serverchan}），该项键转为位置参数；
    3. 均未命中时回退为「首项即 provider」，以兼容自定义/未知渠道。

    Args:
        items (list): (key, value) 二元组列表，已完成引号剥离。

    Returns:
        tuple: (provider, named, positional)，分别为通道名、命名参数字典、
            位置参数列表；items 为空时 provider 为 None。
    """
    if not items:
        return None, {}, []

    provider_index = None
    provider = None
    provider_from_value = False

    # 优先级 1：键命中已知渠道名单
    for index, (key, _) in enumerate(items):
        if _is_known_provider(key):
            provider_index = index
            provider = key
            break

    # 优先级 2：值命中已知渠道名单，对应键降级为位置参数
    if provider_index is None:
        for index, (_, value) in enumerate(items):
            if _is_known_provider(value):
                provider_index = index
                provider = value
                provider_from_value = True
                break

    # 优先级 3：回退为首项即 provider
    if provider_index is None:
        return _locate_provider_fallback(items)

    named = {}
    positional = []
    for index, (key, value) in enumerate(items):
        if index == provider_index:
            # provider 由值命中时，其键作为位置参数（如 {SCTxxxx: serverchan} 的 SCTxxxx）
            if provider_from_value and key is not None:
                positional.append(key)
            # provider 由键命中且带值时，其值作为位置参数（如 {serverchan: SCTxxxx} 的 SCTxxxx）
            elif not provider_from_value and value is not None:
                positional.append(value)
            continue
        if value is None:
            positional.append(key)
        else:
            named[key] = value
    return provider, named, positional


def _locate_provider_fallback(items):
    """无任何项命中已知渠道名单时，按「首项即 provider」归类参数。

    Args:
        items (list): (key, value) 二元组列表，已完成引号剥离。

    Returns:
        tuple: (provider, named, positional)。
    """
    first_key, first_value = items[0]
    provider = first_key
    named = {}
    positional = []
    if first_value is not None:
        positional.append(first_value)
    for key, value in items[1:]:
        if value is None:
            positional.append(key)
        else:
            named[key] = value
    return provider, named, positional


def _dict_fragment_to_channel(fragment):
    """将字典形式的通道片段解析为标准通道字典。

    兼容标准写法（含 provider 键，允许键乱序）与各类无参数头写法：
    通道名可位于任意位置（首/中/末），亦可与密钥参数颠倒书写，
    程序通过 OnePush 已知渠道名单自动定位 provider。

    Args:
        fragment (dict): 字典形式的通道片段。

    Returns:
        dict: 标准通道字典；无法解析时返回空字典。
    """
    items = [
        (strip_wrapping_quotes(key), strip_wrapping_quotes(value))
        for key, value in fragment.items()
    ]
    if not items:
        return {}

    has_provider = any(str(key).lower() == 'provider' for key, _ in items)

    if has_provider:
        provider = None
        named = {}
        positional = []
        for key, value in items:
            if str(key).lower() == 'provider':
                provider = value
            elif value is None:
                positional.append(key)
            else:
                named[key] = value
        return _assemble_channel(provider, named, positional)

    # 无参数头：通过已知渠道名单定位 provider，兼容乱序、颠倒、通道名居中等写法
    provider, named, positional = _locate_provider(items)
    return _assemble_channel(provider, named, positional)


def _list_fragment_to_channel(fragment):
    """将列表形式的通道片段解析为标准通道字典。

    先将各元素归一为 (key, value) 项（裸标量 -> (值, None)，单键字典 ->
    (键, 值)），再通过 OnePush 已知渠道名单定位 provider，从而兼容
    [serverchan, SCTxxxx]、[SCTxxxx, serverchan]、[SCTxxxx: serverchan] 等写法。

    Args:
        fragment (list): 列表形式的通道片段。

    Returns:
        dict: 标准通道字典；无法解析时返回空字典。
    """
    elements = list(fragment)
    if not elements:
        return {}

    items = []
    for element in elements:
        if isinstance(element, dict):
            for key, value in element.items():
                items.append(
                    (strip_wrapping_quotes(key), strip_wrapping_quotes(value))
                )
            continue
        items.append((strip_wrapping_quotes(element), None))

    # 显式 provider 键：保持其作为通道名，其余按键值归类
    if any(str(key).lower() == 'provider' for key, _ in items):
        provider = None
        named = {}
        positional = []
        for key, value in items:
            if str(key).lower() == 'provider':
                provider = value
            elif value is None:
                positional.append(key)
            else:
                named[key] = value
        return _assemble_channel(provider, named, positional)

    provider, named, positional = _locate_provider(items)
    return _assemble_channel(provider, named, positional)


def _fragment_to_channel(parsed):
    """将单个已解析的通道片段（结构化对象）转换为标准通道字典。

    Args:
        parsed: 由 YAML 解析得到的对象，可能为 dict、list 或裸标量。

    Returns:
        dict: 标准通道字典；无法解析时返回空字典。
    """
    if isinstance(parsed, dict):
        return _dict_fragment_to_channel(parsed)
    if isinstance(parsed, (list, tuple)):
        return _list_fragment_to_channel(parsed)
    if isinstance(parsed, str):
        provider = strip_wrapping_quotes(parsed).strip().lower()
        return {'provider': provider} if provider else {}
    return {}


def _load_fragment(fragment):
    """将单个通道片段字符串解析为结构化对象。

    Args:
        fragment (str): 单个通道片段文本，如 "{provider: serverchan, sckey: SCTxxxx}"。

    Returns:
        解析后的对象（dict / list / 标量）；解析失败时返回原始字符串。
    """
    try:
        return _FRAGMENT_YAML.load(fragment)
    except Exception:
        return fragment


def _split_channel_fragments(text):
    """将多通道字符串按 ';' 拆分为单个通道片段。

    Args:
        text (str): 多通道配置字符串。

    Returns:
        list[str]: 去除首尾空白后的非空片段列表。
    """
    text = strip_wrapping_quotes(text)
    return [fragment.strip() for fragment in text.split(';') if fragment.strip()]


def parse_push_channels(raw_value):
    """将用户填写的 push_channel 配置解析为标准通道字典列表。

    兼容以下输入形式：
    - 标准字典 {provider: serverchan, sckey: SCTxxxx}（允许键乱序）；
    - 无参数头写法 [serverchan, SCTxxxx] / [serverchan: SCTxxxx] /
      {serverchan, SCTxxxx} / {serverchan: SCTxxxx}；
    - 多通道 YAML 块序列（block list，每个元素均为映射，无需引号包裹）；
    - 以 ';' 分割的多通道字符串（向后兼容）。

    Args:
        raw_value: 配置文件中 push_channel 的原始值（字符串 / dict / list）。

    Returns:
        list[dict]: 标准通道字典列表，每项 provider 键排在最前。
    """
    if raw_value is None:
        return []

    # 字符串形式：可能为多通道（以 ; 分割）或单个片段
    if isinstance(raw_value, str):
        channels = []
        for fragment in _split_channel_fragments(raw_value):
            channel = _fragment_to_channel(_load_fragment(fragment))
            if channel:
                channels.append(channel)
        return channels

    # 列表且所有元素均为映射：视为多通道 block list（如 - {provider: a}）
    if isinstance(raw_value, (list, tuple)) and _is_multi_channel_list(raw_value):
        channels = []
        for element in raw_value:
            channel = _fragment_to_channel(element)
            if channel:
                channels.append(channel)
        return channels

    # 其余结构化对象：视为单通道（含 [serverchan, SCTxxxx] 等无参数头列表写法）
    channel = _fragment_to_channel(raw_value)
    return [channel] if channel else []


def _is_multi_channel_list(raw_value):
    """判断列表形式的 push_channel 是否为「多通道 block list」。

    仅当列表非空且所有元素均为映射（dict）时，视为多通道列表；
    含裸标量的列表（如 [serverchan, SCTxxxx]）属于单通道无参数头写法。

    Args:
        raw_value (list | tuple): 待判断的列表。

    Returns:
        bool: 为多通道 block list 时返回 True。
    """
    if len(raw_value) == 0:
        return False
    return all(isinstance(element, dict) for element in raw_value)


def _build_flow_map(channel):
    """将单个标准通道字典构建为流式渲染的 CommentedMap。

    Args:
        channel (dict): 标准通道字典。

    Returns:
        CommentedMap: 设置了流式风格、provider 键在最前的映射节点。
    """
    flow_map = CommentedMap()
    flow_map['provider'] = channel.get('provider', '')
    for key, value in channel.items():
        if key == 'provider':
            continue
        flow_map[key] = value
    flow_map.fa.set_flow_style()
    return flow_map


def build_push_channel_node(channels):
    """根据标准通道字典列表构建用于写回配置文件的 push_channel 节点。

    统一使用 YAML 原生块序列（CommentedSeq），其每个元素为单行花括号流式
    CommentedMap，无论单通道还是多通道均无需用引号包裹整行。

    Args:
        channels (list[dict]): 标准通道字典列表。

    Returns:
        构建好的节点：空配置为 CommentedMap，其余为元素均为流式 CommentedMap
        的 CommentedSeq。
    """
    if not channels:
        return CommentedMap()
    block_seq = CommentedSeq()
    for channel in channels:
        block_seq.append(_build_flow_map(channel))
    return block_seq


def push_channel_signature(node):
    """计算 push_channel 节点的规范化签名，用于判断配置是否需要回写。

    Args:
        node: push_channel 的值（dict / list / 字符串 / 其他）。

    Returns:
        tuple: 可用于相等比较的规范化签名。
    """
    if isinstance(node, dict):
        return ('map', tuple(
            (str(key), str(value)) for key, value in node.items()
        ))
    if isinstance(node, (list, tuple)):
        return ('seq', tuple(push_channel_signature(element) for element in node))
    if isinstance(node, str):
        return ('str', strip_wrapping_quotes(node).replace(' ', ''))
    return ('other', node)


def get_program_directory():
    """获取程序所在的目录，兼容打包后的可执行文件。

    Returns:
        str: 程序所在的目录路径。
    """
    if getattr(sys, 'frozen', False):
        # 如果是被打包的可执行文件，使用可执行文件所在目录
        program_dir = os.path.dirname(sys.executable)
    else:
        # 使用主脚本所在目录
        program_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    return program_dir


def run_external_program(program_path):
    """运行外部程序。

    Args:
        program_path (str): 外部程序的路径。

    Raises:
        Exception: 当主方案与备选方案均调用失败时抛出，由调用方处理。
    """
    LOGGER.info(f"正在调用外部程序: {program_path}")

    # 守卫：路径为空或文件不存在时提前失败，避免 subprocess 抛出晦涩的
    # WinError 与备选方案的重复报错
    if not program_path:
        raise FileNotFoundError("外部程序路径为空")
    if not os.path.isfile(program_path):
        raise FileNotFoundError(f"外部程序不存在: {program_path}")

    # 守卫已确保 program_path 为存在的文件，直接取其所在目录；
    # 路径不含目录分量时回退到当前工作目录
    program_dir = os.path.dirname(os.path.abspath(program_path)) or os.getcwd()
    LOGGER.info(f"使用工作目录: {program_dir}")
    
    try:
        # 检查文件扩展名
        ext = os.path.splitext(program_path)[1].lower()
        
        if ext == '.bat' or ext == '.cmd':
            # 对于批处理文件，使用 cmd.exe /c 执行
            subprocess.Popen(['cmd.exe', '/c', program_path], shell=False, cwd=program_dir)
        else:
            # 对于其他文件（包括EXE），使用subprocess.Popen
            # 这样可以更好地控制执行环境，避免os.startfile的潜在问题
            subprocess.Popen([program_path], shell=False, cwd=program_dir)
        LOGGER.info(f"成功调用外部程序: {program_path}")
    except Exception as e:
        LOGGER.error(f"调用外部程序失败: {e}")
        # 尝试使用os.startfile作为备选方案
        try:
            os.startfile(program_path)
            LOGGER.info(f"使用备选方案成功调用外部程序: {program_path}")
        except Exception as e2:
            LOGGER.error(f"备选方案调用外部程序也失败: {e2}")
            # 两种方案均失败，向上抛出由调用方据此判定执行结果
            raise


def parse_time_string(time_str):
    """解析时间字符串为秒。

    Args:
        time_str (str): 时间字符串，格式如 "1h", "15m", "30s"。

    Returns:
        int: 转换后的秒数。

    Raises:
        ValueError: 如果时间字符串格式无效。
    """
    LOGGER.info(f"解析时间字符串: {time_str}")
    time_str = time_str.strip().lower()
    if not time_str:
        LOGGER.error("时间字符串不能为空")
        raise ValueError("时间字符串不能为空")
    
    units = {
        'h': 3600,   # 1小时 = 3600秒
        'm': 60,     # 1分钟 = 60秒
        's': 1       # 1秒 = 1秒
    }
    
    if time_str[-1] in units:
        value = int(time_str[:-1])
        unit = time_str[-1]
        seconds = value * units[unit]
        LOGGER.info(f"解析结果: {seconds} 秒")
        return seconds
    else:
        # 尝试直接解析为整数（秒）
        try:
            seconds = int(time_str)
            LOGGER.info(f"直接解析为秒: {seconds}")
            return seconds
        except ValueError:
            LOGGER.error(f"无效的时间格式: {time_str}，请使用 '1h', '15m', '30s'")
            raise ValueError(f"无效的时间格式: {time_str}，请使用 '1h', '15m', '30s'")