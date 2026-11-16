#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""不依赖页面控件的安全摘要与截图条件。"""

from collections.abc import Mapping

from modules.gui.forms import screenshot_conditions


EVENTS = {'on_end': '目标结束', 'on_timeout': '运行超时',
          'on_wait_timeout': '等待启动超时', 'on_external': '外部程序执行通知'}
CONDITIONS = {'enabled': '事件或事件截图未启用', 'channels': '未配置已知推送通道',
              'references': '消息缺少有效截图变量引用', 'targets': '未配置有效截图目标'}


def _count(items):
    """统计有效列表项，非列表返回待校验并标注被跳过的无效项。"""
    if not isinstance(items, list):
        return '待校验'
    valid = sum(isinstance(item, Mapping) for item in items)
    return f'{valid} 个' + ('（含无效项）' if valid != len(items) else '')


def summary(config):
    """仅返回安全状态，不包含目标、凭证及未知原文。"""
    config = config if isinstance(config, Mapping) else {}
    monitor = config.get('monitor')
    monitor = monitor if isinstance(monitor, Mapping) else {}
    push = config.get('push')
    push = push if isinstance(push, Mapping) else {}
    templates = push.get('templates')
    mode = {'watch': '等待目标运行', 'launch': '主动启动并监视'}.get(
        monitor.get('mode'), '待校验')
    target = monitor.get('target')
    target_state = ('已配置' if isinstance(target, str) and target.strip()
                    else '未配置' if isinstance(target, str) else '待校验')
    common = monitor.get('common')
    wait = common.get('max_wait') if isinstance(common, Mapping) else None
    wait_state = ('已配置' if isinstance(wait, str) and wait.strip()
                  else '未配置' if isinstance(wait, str) else '待校验')
    channel_settings = push.get('push_channel_settings')
    channels = channel_settings.get('channels') if isinstance(channel_settings, Mapping) else None
    screenshot = push.get('screenshot')
    hosts = screenshot.get('targets') if isinstance(screenshot, Mapping) else None
    lines = [f'运行方式：{mode}', f'目标位置：{target_state}',
             f'最长等待：{wait_state}',
             f'通知通道：{_count(channels)}',
             f'截图目标：{_count(hosts)}']
    details = []
    for key, title in EVENTS.items():
        event = templates.get(key) if isinstance(templates, Mapping) else None
        status = ('启用' if event.get('enable') is True else '关闭') if isinstance(
            event, Mapping) else '待校验'
        lines.append(f'{title}：{status}')
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
    external = config.get('external', {})
    for key in ('on_end', 'on_timeout', 'on_wait_timeout'):
        value = external.get(key) if isinstance(external, Mapping) else None
        lines.append(f'{EVENTS[key]}外部程序：' +
                     ('已配置' if isinstance(value, str) and value.strip()
                      else '未配置' if isinstance(value, str) else '待校验'))
    return '\n'.join(lines), details
