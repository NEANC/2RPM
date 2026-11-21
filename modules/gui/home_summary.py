#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""不依赖页面控件的安全摘要与截图条件。"""

from collections.abc import Mapping

from modules.gui.forms import screenshot_conditions


EVENTS = {'on_end': '目标结束', 'on_timeout': '运行超时',
          'on_wait_timeout': '等待启动超时', 'on_external': '外部程序执行通知'}
CONDITIONS = {'enabled': '事件或事件截图未启用', 'channels': '未配置已知推送通道',
              'references': '消息缺少有效截图变量引用', 'targets': '未配置有效截图目标'}
MISSING = object()
INVALID = object()


def _lookup(config, path):
    """沿路径读取草稿；缺失返回 MISSING，中间节点类型异常返回 INVALID。"""
    node = config
    for key in path:
        if not isinstance(node, Mapping):
            return INVALID
        if key not in node:
            return MISSING
        node = node[key]
    return node


def _count(items):
    """统计有效列表项，非列表返回待校验并标注被跳过的无效项。"""
    if not isinstance(items, list):
        return '待校验'
    valid = sum(isinstance(item, Mapping) for item in items)
    return f'{valid} 个' + ('（含无效项）' if valid != len(items) else '')


def _text_state(value):
    """把缺失、合法文本与损坏结构区分为未配置、已配置和待校验。"""
    if value is MISSING:
        return '未配置'
    if isinstance(value, str):
        return '已配置' if value.strip() else '未配置'
    return '待校验'


def _mode_state(value):
    """运行方式缺失显示未选择，未知值或损坏结构显示待校验。"""
    if value is MISSING:
        return '未选择'
    if isinstance(value, str):
        return {'watch': '等待目标运行', 'launch': '主动启动并监视'}.get(
            value, '待校验')
    return '待校验'


def _list_state(value):
    """缺失列表按空列表计数，损坏结构仍显示待校验。"""
    return _count([]) if value is MISSING else _count(value)


def _event_status(event):
    """按事件映射的启用布尔返回配置状态。"""
    if event is MISSING:
        return '未配置'
    if not isinstance(event, Mapping):
        return '待校验'
    enable = event.get('enable', MISSING)
    if enable is MISSING:
        return '未配置'
    if enable is True:
        return '启用'
    if enable is False:
        return '关闭'
    return '待校验'


def summary(config):
    """仅返回安全状态，不包含目标、凭证及未知原文。"""
    config = config if isinstance(config, Mapping) else {}
    mode_value = _lookup(config, ('monitor', 'mode'))
    target = _lookup(config, ('monitor', 'target'))
    wait = _lookup(config, ('monitor', 'common', 'max_wait'))
    channels = _lookup(config, ('push', 'push_channel_settings', 'channels'))
    hosts = _lookup(config, ('push', 'screenshot', 'targets'))
    templates = _lookup(config, ('push', 'templates'))
    lines = [f'运行方式：{_mode_state(mode_value)}',
             f'目标位置：{_text_state(target)}',
             f'最长等待：{_text_state(wait)}',
             f'通知通道：{_list_state(channels)}',
             f'截图目标：{_list_state(hosts)}']
    details = []
    for key, title in EVENTS.items():
        if templates is MISSING:
            lines.append(f'{title}：未配置')
            details.append((title, '未配置', ()))
            continue
        if not isinstance(templates, Mapping):
            lines.append(f'{title}：待校验')
            details.append((title, '待校验', ()))
            continue
        event = templates.get(key, MISSING)
        status = _event_status(event)
        lines.append(f'{title}：{status}')
        if event is MISSING:
            details.append((title, '未配置', ()))
            continue
        if not isinstance(event, Mapping):
            details.append((title, '待校验', ()))
            continue
        try:
            conditions = screenshot_conditions(config, key)
        except (KeyError, TypeError, ValueError):
            details.append((title, '待校验', ()))
            continue
        missing = tuple(label for name, label in CONDITIONS.items()
                        if not conditions[name])
        details.append((title, '条件未满足' if missing else '条件满足', missing))
    external = _lookup(config, ('external',))
    for key in ('on_end', 'on_timeout', 'on_wait_timeout'):
        if external is MISSING:
            state = '未配置'
        elif not isinstance(external, Mapping):
            state = '待校验'
        else:
            state = _text_state(external.get(key, MISSING))
        lines.append(f'{EVENTS[key]}外部程序：{state}')
    return '\n'.join(lines), details
