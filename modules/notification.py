#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import datetime
import asyncio
import logging

from onepush import get_notifier
from modules.utils import parse_time_string

LOGGER = logging.getLogger(__name__)


async def send_notification(config, template_key, **kwargs):
    """发送通知。

    使用 OnePush 库进行推送通知，OnePush 已内置支持 ServerChan 等多种推送通道。

    Args:
        config (dict): 配置信息。
        template_key (str): 模板键。
        **kwargs: 模板参数。
    """
    LOGGER.info(f"使用模板: {template_key} 推送报告")
    push_settings = config.get('push_settings', {})
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
    LOGGER.info(
        f"通知标题: {title}\r\n"
        f"通知内容: {content}"
    )

    # 获取推送通道
    push_channel_settings = push_settings.get('push_channel_settings', {})
    push_channel = dict(push_channel_settings.get('push_channel', {}))

    if not push_channel:
        LOGGER.error("推送通道未配置，无法发送通知")
        return

    if not isinstance(push_channel, dict):
        LOGGER.error("推送通道配置格式错误，应为字典")
        return

    push_channel_name = push_channel.pop('provider', '')
    if not push_channel_name:
        LOGGER.error("推送通道缺少 provider 键，无法发送通知")
        return

    retry_settings = push_settings.get('push_error_retry', {})
    retry_interval_str = retry_settings.get('retry_interval', '3s')
    retry_interval_ms = parse_time_string(retry_interval_str)
    max_retry_count = retry_settings.get('max_retry_count', 3)

    # 推送通知
    LOGGER.info(f"推送通道: {push_channel_name}")
    for attempt in range(1, max_retry_count + 1):
        try:
            notifier = get_notifier(push_channel_name)
            notifier.notify(
                title=title,
                content=content,
                **push_channel
            )
            LOGGER.info(f"通知发送成功: {title}")
            break
        except Exception as e:
            LOGGER.error(
                f"通知发送失败 (尝试 {attempt}/{max_retry_count}): {e}"
            )
            if attempt < max_retry_count:
                await asyncio.sleep(retry_interval_ms / 1000)
            else:
                LOGGER.critical(
                    "通知发送失败，已达到最大重试次数。"
                )