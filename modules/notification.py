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


def _parse_response_body(response):
    """尝试将响应体解析为 JSON 字典。

    Args:
        response: requests.Response 对象。

    Returns:
        dict | None: 解析成功返回字典；无法解析或非字典返回 None。
    """
    try:
        body = response.json()
    except Exception:
        return None
    # 守卫：仅字典型响应体可参与业务字段判定
    if not isinstance(body, dict):
        return None
    return body


def _is_push_successful(response):
    """判定 onepush 返回的响应是否代表推送成功。

    onepush 的 notify() 即便服务端返回业务错误（如 HTTP 400、错误码、限流），
    通常也不会抛出异常，而是返回 requests.Response（请求异常时返回 None）。
    因此需检查 HTTP 状态码与响应体业务字段，才能判定真实成败。

    Args:
        response: onepush notify() 的返回值，通常为 requests.Response，
            请求异常时为 None。

    Returns:
        tuple[bool, str]: (是否成功, 失败原因描述)；成功时原因为空字符串。
    """
    # 守卫：请求异常时 onepush 内部吞掉异常并返回 None
    if response is None:
        return False, "未收到响应，请求可能已失败"

    # HTTP 状态码非 2xx 直接判失败
    status_code = getattr(response, 'status_code', None)
    if status_code is not None and not 200 <= status_code < 300:
        text = (getattr(response, 'text', '') or '').strip()
        return False, f"HTTP {status_code}: {text}"

    # 无法解析响应体时，仅凭 2xx 状态码判为成功
    body = _parse_response_body(response)
    if body is None:
        return True, ""

    # errcode 字段（钉钉、企业微信等）：非 0 即失败
    errcode = body.get('errcode')
    if errcode is not None and errcode != 0:
        return False, f"errcode={errcode}: {body.get('errmsg', '')}"

    # code 字段（Server酱、Qmsg 等）：非 0 / 200 即失败
    code = body.get('code')
    if code is not None and code not in (0, 200):
        reason = body.get('message') or body.get('reason') or body.get('info') or ''
        return False, f"code={code}: {reason}"

    # success 字段（Qmsg 等）：显式 False 即失败
    if body.get('success') is False:
        reason = body.get('reason') or body.get('message') or ''
        return False, f"success=false: {reason}"

    return True, ""


def _handle_attempt_failure(provider, attempt, max_count, reason, retry_interval):
    """记录单次发送失败并决定是否继续重试。

    Args:
        provider (str): 推送渠道名称。
        attempt (int): 当前尝试序号（从 1 开始）。
        max_count (int): 最大重试次数。
        reason (str): 失败原因描述。
        retry_interval (int): 重试间隔（秒）。

    Returns:
        bool: True 表示应继续重试；False 表示已达最大重试次数。
    """
    LOGGER.error(
        f"通道 [{provider}] 通知发送失败 (尝试 {attempt}/{max_count}): {reason}"
    )
    # 守卫：仍有重试机会则等待后继续
    if attempt < max_count:
        time.sleep(retry_interval)
        return True
    LOGGER.warning(
        f"通道 [{provider}] 通知发送失败，已超过最大重试次数。"
    )
    return False


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

    for attempt in range(1, max_count + 1):
        try:
            notifier = get_notifier(provider)
            response = notifier.notify(title=title, content=content, **params)
        except Exception as e:
            # 客户端层面异常（参数缺失、网络错误等）
            if not _handle_attempt_failure(
                    provider, attempt, max_count, str(e), retry_interval):
                return False
            continue

        # 请求未抛异常，仍需依据响应判定真实成败
        success, reason = _is_push_successful(response)
        if success:
            LOGGER.info(f"通知发送成功 [{provider}]: {title}")
            return True

        if not _handle_attempt_failure(
                provider, attempt, max_count, reason, retry_interval):
            return False
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

    Returns:
        list[tuple[str, bool]]: 各通道推送结果，元素为 (provider, 是否成功)。
            禁用、模板缺变量、无有效通道等提前返回路径均返回空列表。
    """
    LOGGER.info(f"使用模板: {template_key} 推送报告")
    push_section = config.get('push', {})
    templates = push_section.get('templates', {})
    template = templates.get(template_key, {})

    # 检查是否启用了该通知
    if not template.get('enable', True):
        LOGGER.warning(f"通知推送已被禁用: {template_key}")
        return []

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
        return []

    # 获取推送通道
    channel_settings = push_section.get('push_channel_settings', {})
    raw_channels = channel_settings.get('channels')

    # 解析为标准通道列表，兼容无参数头、乱序、以 ';' 分割的多通道写法
    channels = parse_push_channels(raw_channels)
    if not channels:
        LOGGER.error("推送通道未配置或格式无效，无法发送通知")
        return []

    retry_settings = push_section.get('retry', {})
    retry_interval_str = retry_settings.get('interval', '3s')
    try:
        retry_interval = parse_time_string(retry_interval_str)
    except (TypeError, ValueError):
        LOGGER.warning(
            "推送重试间隔配置无效，已回退默认值: %s", retry_interval_str
        )
        retry_interval = 3

    try:
        max_count = int(retry_settings.get('max_count', 3))
    except (TypeError, ValueError):
        LOGGER.warning(
            "推送重试次数配置无效，已回退默认值: %s", retry_settings.get('max_count', 3)
        )
        max_count = 3
    if max_count < 1:
        LOGGER.warning(
            "推送重试次数必须 >= 1，已回退为 1: %s", max_count
        )
        max_count = 1

    # 通过 ThreadPoolExecutor 向各通道并发推送，等待全部完成后回收结果
    channel_names = ', '.join(c.get('provider', '?') for c in channels)
    LOGGER.info(f"共解析到 {len(channels)} 个推送通道: {channel_names}")
    with ThreadPoolExecutor() as executor:
        futures = [
            (
                channel.get('provider', '?'),
                executor.submit(
                    _notify_single_channel,
                    channel,
                    title,
                    content,
                    retry_interval,
                    max_count,
                ),
            )
            for channel in channels
        ]
        # future.result() 会阻塞至各通道完成，从而保证整体同步等待
        results = [(provider, future.result()) for provider, future in futures]
    return results
