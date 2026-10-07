#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import datetime
import re
import time
import logging
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from json import JSONDecodeError
from string import Formatter

from onepush import get_notifier
from modules.screenshot.pipeline import (
    ScreenshotBatch,
    append_screenshot_notices,
    prepare_screenshots,
)
from modules.screenshot.targets import allocate_targets
from modules.utils import (
    parse_push_channels,
    parse_time_string,
)

LOGGER = logging.getLogger(__name__)

# 模板中始终由推送配置提供的截图变量名
_AGGREGATE_VARIABLE = 'screenshot'
# 仅按位置编号的截图变量允许进入截图占位处理
_SCREENSHOT_INDEX = re.compile(r'screenshot_[0-9]+')
# 普通模板变量保留名，避免截图 out 覆盖既有变量
_RESERVED_VARIABLES = frozenset({
    'host_name',
    'current_time',
    'short_current_time',
    'process_name',
    'process_pid',
    'process_run_time',
    'process_wait_time',
    'external_program_name',
    'external_program_path',
})
# 引用未配置编号的截图变量时给出的固定中文提示
_UNCONFIGURED_SCREENSHOT = '截图失败：未配置该截图目标'
# 截图编排出现未知异常降级时给出的固定中文提示
_INTERNAL_SCREENSHOT = '截图失败：内部错误'


def _parse_response_body(response, *, safe=False):
    """尝试将响应体解析为 JSON 字典

    Args:
        response: requests.Response 对象
        safe (bool): 安全模式仅将 JSON 解码失败视为非 JSON 响应，
            其他普通异常交由通道边界安全判失败。

    Returns:
        dict | None: 解析成功返回字典；无法解析或非字典返回 None
    """
    try:
        body = response.json()
    except Exception as exc:
        if safe and not isinstance(exc, JSONDecodeError):
            raise
        return None
    # 守卫：仅字典型响应体可参与业务字段判定
    if not isinstance(body, dict):
        return None
    return body


def _is_push_successful(response, *, safe=False):
    """判定 onepush 返回的响应是否代表推送成功

    onepush 的 notify() 即便服务端返回业务错误（如 HTTP 400、错误码、限流），
    通常也不会抛出异常，而是返回 requests.Response（请求异常时返回 None）
    因此需检查 HTTP 状态码与响应体业务字段，才能判定真实成败

    Args:
        response: onepush notify() 的返回值，通常为 requests.Response，
            请求异常时为 None
        safe (bool): 截图通知仅返回固定失败分类及验证后的 HTTP 状态码，
            不将响应正文、业务消息或业务码拼入原因。

    Returns:
        tuple[bool, str]: (是否成功, 失败原因描述)；成功时原因为空字符串
    """
    # 守卫：请求异常时 onepush 内部吞掉异常并返回 None
    if response is None:
        return False, "未收到响应，请求可能已失败"

    # HTTP 状态码非 2xx 直接判失败
    status_code = getattr(response, 'status_code', None)
    if safe and (type(status_code) is not int or not 100 <= status_code <= 599):
        return False, "响应判定失败"
    if status_code is not None and not 200 <= status_code < 300:
        if safe:
            return False, f"HTTP 请求失败（HTTP {status_code}）"
        text = (getattr(response, 'text', '') or '').strip()
        return False, f"HTTP {status_code}: {text}"

    # 无法解析响应体时，仅凭 2xx 状态码判为成功
    body = _parse_response_body(response, safe=safe)
    if body is None:
        return True, ""

    # errcode 字段（钉钉、企业微信等）：非 0 即失败
    errcode = body.get('errcode')
    if errcode is not None and errcode != 0:
        if safe:
            return False, "业务拒绝"
        return False, f"errcode={errcode}: {body.get('errmsg', '')}"

    # code 字段（Server酱、Qmsg 等）：非 0 / 200 即失败
    code = body.get('code')
    if code is not None and code not in (0, 200):
        if safe:
            return False, "业务拒绝"
        reason = body.get('message') or body.get('reason') or body.get('info') or ''
        return False, f"code={code}: {reason}"

    # success 字段（Qmsg 等）：显式 False 即失败
    if body.get('success') is False:
        if safe:
            return False, "业务拒绝"
        reason = body.get('reason') or body.get('message') or ''
        return False, f"success=false: {reason}"

    return True, ""


def _handle_attempt_failure(provider, attempt, max_count, reason, retry_interval):
    """记录单次发送失败并决定是否继续重试

    Args:
        provider (str): 推送渠道名称
        attempt (int): 当前尝试序号（从 1 开始）
        max_count (int): 最大重试次数
        reason (str): 失败原因描述
        retry_interval (int): 重试间隔（秒）

    Returns:
        bool: True 表示应继续重试；False 表示已达最大重试次数
    """
    LOGGER.error(
        f"通道 [{provider}] 通知发送失败 (尝试 {attempt}/{max_count}): {reason}"
    )
    # 守卫：仍有重试机会则等待后继续
    if attempt < max_count:
        time.sleep(retry_interval)
        return True
    LOGGER.warning(
        f"通道 [{provider}] 通知发送失败，已超过最大重试次数"
    )
    return False


def _notify_single_channel(channel, title, content, retry_interval, max_count,
                           screenshot_count=0):
    """向单个推送通道发送通知，失败时按配置重试

    Args:
        channel (dict): 标准通道字典，含 provider 及该渠道所需参数
        title (str): 通知标题
        content (str): 通知内容
        retry_interval (int): 重试间隔（秒）
        max_count (int): 最大重试次数
        screenshot_count (int): 标题或正文实际引用的截图项数量；大于 0 时
            成功与失败日志均使用安全摘要，避免签名直链或凭证进入
            本项目通知日志，不控制第三方库自身的日志。

    Returns:
        bool: 是否发送成功
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
            # 截图路径不格式化客户端异常，也不向日志 helper 传递原文
            reason = "客户端发送失败" if screenshot_count > 0 else str(e)
            if not _handle_attempt_failure(
                    provider, attempt, max_count, reason, retry_interval):
                return False
            continue

        # 截图响应判定异常在通道内安全失败，避免外层重新记录敏感异常
        if screenshot_count > 0:
            try:
                success, reason = _is_push_successful(response, safe=True)
            except Exception:
                success, reason = False, "响应判定失败"
        else:
            success, reason = _is_push_successful(response)
        if success:
            # 标题可能引用含签名直链的截图结果，此时只记录安全摘要
            if screenshot_count:
                LOGGER.info(
                    "通知发送成功 [%s]（含截图结果，截图项 %d）",
                    provider, screenshot_count)
            else:
                LOGGER.info(f"通知发送成功 [{provider}]: {title}")
            return True

        if not _handle_attempt_failure(
                provider, attempt, max_count, reason, retry_interval):
            return False
    return False


def _is_placeholder_root(name, configured_names):
    """判定根字段名是否为截图占位变量（其值恒为字符串）

    聚合 screenshot、按位置编号的 screenshot_N 及本次已配置 out 的输出名
    均由截图编排产出字符串，模板对其做属性或下标访问必为笔误。

    Args:
        name (str): 模板字段的根名
        configured_names (set[str]): 本次实际分配的截图输出变量名

    Returns:
        bool: 是否为截图占位变量根名
    """
    return (
        name in configured_names
        or name == _AGGREGATE_VARIABLE
        or _SCREENSHOT_INDEX.fullmatch(name) is not None
    )


def _collect_template_fields(values, configured_names, *templates):
    """递归收集模板引用的根字段名，并完成可判定的 format 结构校验

    支持转义花括号、字段访问及嵌套 format spec。转换符仅允许 None/s/r/a；
    对本次已提供的普通变量按真实字段访问语义解析属性与下标，任一模板花括号
    不配对、转换符非法或字段访问失败均返回 None。截图占位变量的值恒为字符串，
    其根名带属性或下标访问时同样判定为无效模板并返回 None，供通知层在截图前
    判定模板是否可用。不评估 format spec 的语义，也不把截图占位变量当作缺失
    变量。

    Args:
        values (dict): 本次已可解析的普通模板变量，不含截图占位变量
        configured_names (set[str]): 本次实际分配的截图输出变量名
        *templates: 待解析的模板字符串（如标题与正文）

    Returns:
        set[str] | None: 引用到的根字段名集合；结构不可用时返回 None
    """
    formatter = Formatter()
    fields = set()
    for template in templates:
        pending = [template]
        try:
            while pending:
                parsed = formatter.parse(pending.pop())
                for _, name, specification, conversion in parsed:
                    # 守卫：转换符仅允许字符串化、repr 化与 ascii 化
                    if conversion not in (None, 's', 'r', 'a'):
                        return None
                    # 守卫：转义花括号不产生字段引用
                    if name is None:
                        continue
                    root = name.split('.')[0].split('[')[0]
                    fields.add(root)
                    # 仅对本次已知变量按真实语义解析属性与下标访问
                    if root in values:
                        formatter.get_field(name, (), values)
                    # 守卫：占位变量值恒为字符串，字段访问必为模板笔误
                    elif (name != root
                          and _is_placeholder_root(root, configured_names)):
                        return None
                    if specification:
                        pending.append(specification)
        except (ValueError, AttributeError, IndexError, KeyError, TypeError):
            return None
    return fields


def _resolve_capture_flag(template):
    """解析本次事件的截图开关

    截图开关是模板级配置，仅从模板读取；调用方传入的同名 kwarg 按普通模板
    变量处理，不影响是否截图。模板未提供时按启用处理；仅显式布尔值生效，
    其余类型一律按关闭处理并给出不含配置内容的安全诊断，避免意外上传。

    Args:
        template (dict): 当前模板配置

    Returns:
        bool: 是否执行截图
    """
    value = template.get('capture_screenshot', True)
    if value is True:
        return True
    if value is False:
        return False
    # 守卫：非布尔类型不得视为启用，仅记录类型名
    LOGGER.warning(
        "capture_screenshot 配置类型无效，已按关闭截图处理: %s",
        type(value).__name__)
    return False


def _configured_screenshot_names(section, reserved_names):
    """复用实际分配规则取得本次截图输出名（无截图或上传副作用）

    Args:
        section: screenshot 配置映射
        reserved_names: 调用方保留的普通变量名集合

    Returns:
        set[str]: 分配后的实际输出变量名
    """
    raw_targets = section.get('targets') if isinstance(section, Mapping) else None
    targets, _ = allocate_targets(raw_targets, reserved_names)
    return {item['out'] for item in targets if item['out']}


def _has_valid_screenshot_target(section, reserved_names):
    """判定本次是否配置了通过校验的截图目标（无截图或上传副作用）

    Args:
        section: screenshot 配置映射
        reserved_names: 调用方保留的普通变量名集合

    Returns:
        bool: 配置中存在可用目标时为 True
    """
    raw_targets = section.get('targets') if isinstance(section, Mapping) else None
    targets, _ = allocate_targets(raw_targets, reserved_names)
    return any(item['error'] is None for item in targets)


def _prepare_screenshot_batch(section, enabled, reserved_names, *,
                              runtime=None, event='event'):
    """调用一次截图编排，未知普通异常时安全降级为空批次

    控制信号保持原对象向上传播，不被吞掉或包装。降级时一并返回内部错误
    标记，供上层区分“未配置目标”与“截图内部错误”两种占位提示；日志只
    记录异常类型名用于诊断，不输出异常原文、配置或堆栈。

    Args:
        section: screenshot 配置映射
        enabled: 是否执行截图上传
        reserved_names: 调用方保留的普通变量名集合
        runtime: 运行上下文（程序根与净化的配置 stem）
        event: 本次事件的真实模板键

    Returns:
        tuple[ScreenshotBatch, bool]: 可跨通道复用的截图批次与内部错误标记
    """
    try:
        return prepare_screenshots(
            section, enabled, reserved_names,
            runtime=runtime, event=event), False
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        # 仅记录异常类型名用于诊断，不输出异常原文、配置或堆栈
        LOGGER.error(
            "截图编排出现未知异常（%s），已跳过本次截图结果",
            type(exc).__name__)
        return ScreenshotBatch({}, (), ()), True


def _referenced_screenshot_count(title, content, batch):
    """统计本次通知实际引用的截图批次项数，供日志脱敏判定复用

    仅当标题或正文确实包含非空的批次值时才计数，使未使用截图结果的
    场景保持既有日志不变。

    Args:
        title (str): 渲染后的标题
        content (str): 渲染后的正文
        batch (ScreenshotBatch): 本次截图批次

    Returns:
        int: 标题或正文实际引用的非空截图项数量
    """
    return sum(
        1 for value in batch.values.values()
        if value and (value in title or value in content)
    )


def _log_rendered_notification(template_key, title, content, batch):
    """记录待发送通知

    含截图结果的正文可能携带签名直链，此时只记录安全摘要与计数，
    绝不打印完整正文、批次内容、链接或凭证。

    Args:
        template_key (str): 模板键
        title (str): 渲染后的标题
        content (str): 渲染后的正文
        batch (ScreenshotBatch): 本次截图批次
    """
    if _referenced_screenshot_count(title, content, batch):
        LOGGER.info(
            "通知包含截图结果，已省略完整正文: %s（截图项 %d，提示 %d）",
            template_key,
            sum(1 for value in batch.values.values() if value),
            len(batch.warnings),
        )
        return
    LOGGER.info(
        f"通知标题: {title}\r\n"
        f"通知内容: {content}"
    )


def send_notification(config, template_key, **kwargs):
    """发送通知

    使用 OnePush 库进行推送通知，内部通过 ThreadPoolExecutor 向各通道并发提交
    多通道并发执行（含各自重试），但会等待所有通道完成后才返回（整体同步等待），
    以确保推送线程不会在主进程退出时被强制终止

    截图编排在模板校验之后、发送之前执行一次，同一批次被所有通道及其重试复用。

    Args:
        config (dict): 配置信息
        template_key (str): 模板键
        **kwargs: 模板参数

    Returns:
        list[tuple[str, bool]]: 各通道推送结果，元素为 (provider, 是否成功)
            禁用、模板缺变量、无有效通道等提前返回路径均返回空列表
    """
    LOGGER.info(f"使用模板: {template_key} 推送报告")
    push_section = config.get('push', {})
    templates = push_section.get('templates', {})
    template = templates.get(template_key, {})

    # 检查是否启用了该通知
    if not template.get('enable', True):
        LOGGER.warning(f"通知推送已被禁用: {template_key}")
        return []

    raw_title = template.get('title', '')
    raw_content = template.get('content', '')

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

    # 获取推送通道（与截图无关，无通道时直接返回以避免多余副作用）
    channel_settings = push_section.get('push_channel_settings', {})
    raw_channels = channel_settings.get('channels')

    # 解析为标准通道列表，兼容无参数头、乱序、以 ';' 分割的多通道写法
    channels = parse_push_channels(raw_channels)
    if not channels:
        LOGGER.error("推送通道未配置或格式无效，无法发送通知")
        return []

    screenshot_section = push_section.get('screenshot', {})
    reserved_names = _RESERVED_VARIABLES | set(kwargs)
    configured_names = _configured_screenshot_names(
        screenshot_section, reserved_names)

    # 截图前校验模板语法、普通变量与占位变量字段，避免明知失败仍截图上传
    fields = _collect_template_fields(
        kwargs, configured_names, raw_title, raw_content)
    if fields is None:
        LOGGER.error("通知模板格式无效，已跳过该条通知模板键: %s", template_key)
        return []

    placeholders = {
        name for name in fields
        if name not in kwargs and _is_placeholder_root(name, configured_names)
    }
    missing = [
        name for name in fields
        if name not in kwargs and name not in placeholders
    ]
    if missing:
        LOGGER.error(
            "通知模板缺少变量: %s，已跳过该条通知模板键: %s",
            ', '.join(sorted(missing)), template_key
        )
        return []

    # 每目标每事件仅执行一次，单目标失败不影响其他目标与本次通知
    enabled = _resolve_capture_flag(template)
    # 模板未引用任何截图变量且无有效目标时按纯禁用路径处理，避免未使用
    # 截图的既有用户每次通知都产生目标缺失告警
    if enabled and not placeholders and not _has_valid_screenshot_target(
            screenshot_section, reserved_names):
        enabled = False
    batch, internal_error = _prepare_screenshot_batch(
        screenshot_section, enabled, reserved_names,
        runtime=config.get('_runtime'), event=template_key)

    values = dict(batch.values)
    values.update(kwargs)
    # 未配置目标与截图内部错误使用各自固定提示，避免提示误导
    hint = _INTERNAL_SCREENSHOT if internal_error else _UNCONFIGURED_SCREENSHOT
    fallback = hint if enabled else ''
    for name in placeholders:
        values.setdefault(name, fallback)

    try:
        title = raw_title.format(**values)
        content = raw_content.format(**values)
    except KeyError as e:
        LOGGER.error(
            f"通知模板缺少变量: {e}，已跳过该条通知模板键: {template_key}"
        )
        return []

    # 用原始正文追加一次告警与必要改名结果，不再重复格式化
    content = append_screenshot_notices(content, raw_content, batch)
    # 本次通知是否引用截图结果，同一判定同时决定待发送日志与成功日志
    screenshot_count = _referenced_screenshot_count(title, content, batch)
    _log_rendered_notification(template_key, title, content, batch)

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
                    screenshot_count,
                ),
            )
            for channel in channels
        ]
        # future.result() 会阻塞至各通道完成，从而保证整体同步等待
        results = [(provider, future.result()) for provider, future in futures]
    return results
