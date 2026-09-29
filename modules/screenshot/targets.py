#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""解析独立截图目标，并为每个原始位置分配唯一输出名。"""

from collections.abc import Mapping
import re


_OUTPUT_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
_NUMBERED_NAME = re.compile(r'screenshot_([0-9]+)')
_PROVIDERS = frozenset({'window', 'adb'})
_AGGREGATE_NAME = 'screenshot'


def _normalize_target(raw, index):
    """校验单项配置，保留失败位置且不格式化任意配置对象。"""
    result = {
        'index': index,
        'provider': None,
        'target': None,
        'out': f'screenshot_{index}',
        'error': None,
        'renamed_from': None,
    }
    if not isinstance(raw, Mapping):
        result['error'] = '截图目标必须为映射'
        return result

    provider = raw.get('provider')
    target = raw.get('target')
    if isinstance(provider, str):
        result['provider'] = provider.strip().lower()
    if isinstance(target, str):
        result['target'] = target.strip()

    if result['provider'] not in _PROVIDERS:
        result['error'] = 'provider 必须为 window 或 adb'
    elif not result['target']:
        result['error'] = 'target 必须为非空字符串'

    if 'out' not in raw:
        return result

    out = raw['out']
    if isinstance(out, str) and _OUTPUT_NAME.fullmatch(out):
        result['out'] = out
        return result

    result['out'] = None
    if not isinstance(out, str):
        result['renamed_from'] = '<非字符串输出名>'
    elif not out.strip():
        result['renamed_from'] = '<空输出名>'
    else:
        result['renamed_from'] = '<非法输出名>'
    return result


def _next_output_name(start, unavailable):
    """从指定编号起寻找未声明、未分配且未被保留的输出名。"""
    candidate = f'screenshot_{start}'
    while candidate in unavailable:
        start += 1
        candidate = f'screenshot_{start}'
    return candidate


def allocate_targets(raw_targets=None, reserved_names=()):
    """规范化目标并按原列表顺序分配输出变量，不执行截图。

    Args:
        raw_targets: 截图目标列表；缺省或 None 表示无目标。
        reserved_names: 调用方已有的保留变量名集合或其他可迭代对象。

    Returns:
        二元组，包含规范化目标字典列表及按目标顺序排列的中文告警。
        失败项保留原序号及输出名；所有入参均保持不变。
    """
    if raw_targets is None:
        return [], []
    if not isinstance(raw_targets, list):
        return [], ['截图 targets 配置必须为列表，已忽略该配置']

    normalized = [
        _normalize_target(raw, index)
        for index, raw in enumerate(raw_targets, 1)
    ]
    declared = {
        item['out'] for item in normalized if item['out'] is not None
    }
    reserved = set(reserved_names) | {_AGGREGATE_NAME}
    assigned = set()
    unavailable = declared | reserved
    warnings = []

    for item in normalized:
        name = item['out']
        start = item['index']
        if name is None:
            original = item['renamed_from']
            reason = '无效'
        elif name in reserved:
            original = name
            reason = '为保留名'
        elif name in assigned:
            original = name
            reason = '重复'
            numbered = _NUMBERED_NAME.fullmatch(name)
            if numbered:
                start = int(numbered.group(1)) + 1
        else:
            assigned.add(name)
            continue

        replacement = _next_output_name(start, unavailable)
        item['out'] = replacement
        item['renamed_from'] = original
        assigned.add(replacement)
        unavailable.add(replacement)
        warnings.append(
            f'截图目标 {item["index"]}：输出名“{original}”{reason}，'
            f'已改为“{replacement}”。'
        )

    return normalized, warnings
