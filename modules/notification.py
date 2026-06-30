#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import datetime
import time
import logging
from concurrent.futures import ThreadPoolExecutor

from onepush import get_notifier
from modules.utils import (
    parse_push_channels,
    parse_time_string,
)

LOGGER = logging.getLogger(__name__)


def _notify_single_channel(channel, title, content, retry_interval, max_count):
    """向单个推送通道发送通知，失败时按配置重试。

    Args:
        channel (dict): 标准通道字典，含 provider 及该渠道所需参数。
        title (str): 通知标题。
        content (str): 通知内容。
        retry_interval (int): 重试间隔（秒）。
        max_count (int): 最大重试次数。

    Returns:
        bool: 是否发送成功。
    """
    params = dict(channel)
    provider = params.pop('provider', '')

    # 守卫：缺少 provider 无法发送
    if not provider:
        LOGGER.error("推送通道缺少 provider 键，已跳过该通道")
        return False

    LOGGER.info(f"推送通道: {provider}")
    for attempt in range(1, max_count + 1):
        try:
            notifier = get_notifier(provider)
            notifier.notify(title=title, content=content, **params)
            LOGGER.info(f"通知发送成功 [{provider}]: {title}")
            return True
        except Exception as e:
            LOGGER.error(
                f"通知发送失败 [{provider}] (尝试 {attempt}/{max_count}): {e}"
            )
            if attempt < max_count:
                time.sleep(retry_interval)
            else:
                LOGGER.critical(
                    f"通知发送失败 [{provider}]，已达到最大重试次数。"
                )
    return False


def send_notification(config, template_key, **kwargs):
    """发送通知。

    使用 OnePush 库进行推送通知，内部通过 ThreadPoolExecutor 向各通道并发提交。
    多通道并发执行（含各自重试），但会等待所有通道完成后才返回（整体同步等待），
    以确保推送线程不会在主进程退出时被强制终止。

    Args:
        config (dict): 配置信息。
        template_key (str): 模板键。
        **kwargs: 模板参数。
    """
    LOGGER.info(f"使用模板: {template_key} 推送报告")
    push_section = config.get('push', {})
    templates = push_section.get('templates', {})
    template = templates.get(template_key, {})

    # 检查是否启用了该通知
    if not template.get('enable', True):
        LOGGER.warning(f"通知推送已被禁用: {template_key}")
        return

    title = template.get('title', '')
    content = template.get('content', '')

    # 合并为一次 datetime.now() 调用，消除跨秒不一致
    now = datetime.datetime.now()
    current_time = now.strftime('%Y/%m/%d %H:%M:%S')
    short_current_time = now.strftime('%H:%M:%S')
    host_name = socket.gethostname()

    kwargs.update({
        'host_name': host_name,
        'current_time': current_time,
        'short_current_time': short_current_time,
    })

    try:
        title = title.format(**kwargs)
        content = content.format(**kwargs)
        LOGGER.info(
            f"通知标题: {title}\r\n"
            f"通知内容: {content}"
        )
    except KeyError as e:
        LOGGER.error(
            f"通知模板缺少变量: {e}，已跳过该条通知。模板键: {template_key}"
        )
        return

    # 获取推送通道
    channel_settings = push_section.get('push_channel_settings', {})
    raw_channels = channel_settings.get('channels')

    # 解析为标准通道列表，兼容无参数头、乱序、以 ';' 分割的多通道写法
    channels = parse_push_channels(raw_channels)
    if not channels:
        LOGGER.error("推送通道未配置或格式无效，无法发送通知")
        return

    retry_settings = push_section.get('retry', {})
    retry_interval_str = retry_settings.get('interval', '3s')
    retry_interval = parse_time_string(retry_interval_str)
    max_count = retry_settings.get('max_count', 3)

    # 通过 ThreadPoolExecutor 向各通道并发推送，等待全部完成后返回
    LOGGER.info(f"共解析到 {len(channels)} 个推送通道")
    with ThreadPoolExecutor() as executor:
        for channel in channels:
            executor.submit(
                _notify_single_channel,
                channel,
                title,
                content,
                retry_interval,
                max_count,
            )
