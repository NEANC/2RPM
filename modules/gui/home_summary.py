#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""不依赖页面控件的安全摘要与截图条件。"""

from collections.abc import Mapping

from modules.gui.forms import screenshot_conditions


EVENTS = {'on_end': '目标结束', 'on_timeout': '运行超时',
          'on_wait_timeout': '等待启动超时', 'on_external': '外部程序执行通知'}
CONDITIONS = {'enabled': '事件或事件截图未启用', 'channels': '未配置已知推送通道',
              'references': '消息缺少有效截图变量引用', 'targets': '未配置有效截图目标'}


def summary(config):
    """仅返回安全状态，不包含目标、凭证及未知原文。"""
    monitor = config.get('monitor', {})
    push = config.get('push', {})
    templates = push.get('templates', {})
    mode = {'watch': '等待目标运行', 'launch': '主动启动并监视'}.get(
        monitor.get('mode'), '待校验')
    target = monitor.get('target')
    target_state = ('已配置' if isinstance(target, str) and target.strip()
                    else '未配置' if isinstance(target, str) else '待校验')
    common = monitor.get('common', {})
    wait = common.get('max_wait') if isinstance(common, Mapping) else None
    wait_state = ('已配置' if isinstance(wait, str) and wait.strip()
                  else '未配置' if isinstance(wait, str) else '待校验')
    channels = push.get('push_channel_settings', {}).get('channels', [])
    hosts = push.get('screenshot', {}).get('targets', [])
    lines = [f'运行方式：{mode}', f'目标位置：{target_state}',
             f'最长等待：{wait_state}',
             f'通知通道：{len(channels) if isinstance(channels, list) else "待校验"} 个',
             f'截图目标：{len(hosts) if isinstance(hosts, list) else "待校验"} 个']
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
