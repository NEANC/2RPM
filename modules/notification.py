#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import datetime
import time
import logging
import sys

from serverchan_sdk import sc_send
from onepush import get_notifier
from modules.config import DEFAULT_VALUES

LOGGER = logging.getLogger(__name__)


def send_notification(config, template_key, **kwargs):
    """发送通知。

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
    push_channel = push_channel_settings.get('choose', 'ServerChan')
    serverchan_key = push_channel_settings.get('serverchan_key', '')
    push_channel_name = push_channel_settings.get('push_channel', '')
    push_channel_key = push_channel_settings.get('push_channel_key', '')

    retry_settings = push_settings.get('push_error_retry', {})
    retry_interval_ms = retry_settings.get('retry_interval_ms', 3000)  # 保留毫秒单位的默认值
    max_retry_count = retry_settings.get('max_retry_count', DEFAULT_VALUES['push_settings']['push_error_retry']['max_retry_count'])

    # 推送通知
    LOGGER.info(f"推送通道: {push_channel}")
    for attempt in range(1, max_retry_count + 1):
        try:
            if push_channel == 'ServerChan':
                if not serverchan_key:
                    LOGGER.error("ServerChan 密钥未配置，无法发送通知")
                    return
                options = {}  # 可根据需要添加额外的选项
                response = sc_send(serverchan_key, title, content, options)
                LOGGER.info(f"推送成功: {response}")
            elif push_channel == 'OnePush':
                if not push_channel_key:
                    LOGGER.error("OnePush 密钥未配置，无法发送通知")
                    return
                notifier = get_notifier(push_channel_name)
                notifier.notify(
                    title=title,
                    content=content,
                    key=push_channel_key
                )
                LOGGER.info(f"通知发送成功: {title}")
            else:
                LOGGER.critical(f"推送通道未配置: {push_channel}")
            break
        except Exception as e:
            LOGGER.error(f"通知发送失败 (尝试 {attempt}/{max_retry_count}): {e}")
            if attempt < max_retry_count:
                time.sleep(retry_interval_ms / 1000)
            else:
                LOGGER.critical("通知发送失败，已达到最大重试次数。")
                sys.exit(1)