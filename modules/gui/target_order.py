#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""在单次纯变换中维护截图目标身份及事件模板引用。"""

import copy
import re
from collections.abc import Mapping
from string import Formatter

from modules.notification import _RESERVED_VARIABLES
from modules.screenshot.targets import allocate_targets


_FIELD = re.compile(r'\{\{|\}\}|\{([^{}]*)\}')
_SCREENSHOT_NAME = re.compile(r'screenshot_[0-9]+')


def _names(targets):
    """按发送端分配规则取得各目标的唯一无歧义输出名。"""
    allocated, warnings = allocate_targets(targets, _RESERVED_VARIABLES)
    if warnings or len(allocated) != len(targets):
        raise ValueError('截图输出名重复、无效或与保留变量冲突，无法安全更新消息引用')
    return [item['out'] for item in allocated]


def _rewrite(text, replacements):
    """同时替换精确引用，转义花括号和其他文本保持原样。"""
    if not isinstance(text, str):
        raise ValueError('消息模板不是文本，无法安全更新引用')
    try:
        list(Formatter().parse(text))
    except ValueError as exc:
        raise ValueError('消息模板格式无效，无法安全更新引用') from exc

    def replace(match):
        """按一次匹配的原始字段身份生成替换，避免连锁改名。"""
        field = match.group(1)
        if field is None:
            return match.group()
        if '{' in field or '}' in field:
            raise ValueError('消息模板含嵌套字段，无法安全更新引用')
        if field in replacements:
            name = replacements[field]
            return '{' + name + '}' if name is not None else ''
        root = re.split(r'[.\[!:]', field, 1)[0]
        if root in replacements and field != root:
            raise ValueError('截图变量使用复杂格式，无法安全更新引用')
        if _SCREENSHOT_NAME.fullmatch(root) and root not in replacements:
            raise ValueError('消息引用了未配置的截图编号，无法安全更新')
        return match.group()

    result = _FIELD.sub(replace, text)
    if '{' in _FIELD.sub('', text) or '}' in _FIELD.sub('', text):
        raise ValueError('消息模板包含无法解析的引用，无法安全更新')
    return result


def _transform(config, index, destination=None):
    """深复制配置，先验证双向映射再更新目标和模板。"""
    if not isinstance(config, Mapping):
        raise ValueError('消息模板结构无效，无法安全更新引用')
    push = config.get('push')
    if not isinstance(push, Mapping):
        raise ValueError('消息模板结构无效，无法安全更新引用')
    screenshot = push.get('screenshot')
    if not isinstance(screenshot, Mapping):
        raise ValueError('截图目标列表结构无效，无法安全更新')
    templates = push.get('templates', {})
    if not isinstance(templates, Mapping) or not all(
            isinstance(template, Mapping) for template in templates.values()):
        raise ValueError('消息模板结构无效，无法安全更新引用')
    updated = copy.deepcopy(config)
    targets = updated['push']['screenshot']['targets']
    if not isinstance(index, int) or not 0 <= index < len(targets):
        raise ValueError('截图目标序号超出范围')
    if destination is not None and not 0 <= destination < len(targets):
        raise ValueError('截图目标不能越过列表边界')
    before = _names(targets)
    identities = list(range(len(targets)))
    item = targets.pop(index)
    identities.pop(index)
    if destination is not None:
        targets.insert(destination, item)
        identities.insert(destination, index)
    after = _names(targets)
    replacements = {name: None for name in before}
    for identity, new_name in zip(identities, after):
        replacements[before[identity]] = new_name
    templates = updated['push'].get('templates', {})
    for template in templates.values():

        for key in ('title', 'content'):
            if key in template:
                template[key] = _rewrite(template[key], replacements)
    return updated


def move_screenshot_target(config: dict, index: int, offset: int) -> dict:
    """仅按相邻偏移移动整项截图目标并维护模板身份。"""
    if offset not in (-1, 1):
        raise ValueError('只允许相邻目标交换位置')
    return _transform(config, index, index + offset)


def swap_screenshot_targets(config: dict, first: int, second: int) -> dict:
    """交换两个原始位置的目标并维护模板引用身份。"""
    if not isinstance(first, int) or not isinstance(second, int) or first == second:
        raise ValueError('截图目标位置无效')
    return _transform(config, first, second)


def delete_screenshot_target(config: dict, index: int) -> dict:
    """删除目标及其精确引用，重新编号存活目标的引用。"""
    return _transform(config, index)
