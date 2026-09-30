#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""解析经过时长期限并计算供后续上传使用的本地到期显示。"""

from dataclasses import dataclass
from datetime import datetime
import math
import re

from modules.image_host.core import ImageHostError


_DATETIME_SPAN = datetime.max - datetime.min
_MAX_SECONDS = _DATETIME_SPAN.days * 86400 + _DATETIME_SPAN.seconds
_SEGMENT = re.compile(r'([0-9]+)([wdhms])')
_UNIT_SECONDS = {'w': 604800, 'd': 86400, 'h': 3600, 'm': 60, 's': 1}


@dataclass(frozen=True)
class ExpirationDecision:
    """保存秒级绝对期限、本地显示以及策略缩短事实。"""

    deadline: float | None
    expired_at: str | None
    shortened: bool


def parse_expiration(value) -> int:
    """完整扫描降序且不重复的正整数单位组合，返回经过秒数。

    仅去除字符串首尾空白，数字限 ASCII；允许前导零和非归一值。
    总量上限为 datetime.max 与 datetime.min 间的最大整秒跨度，
    即 315537897599 秒，不是业务期限上限。超长数字先去前导零并
    限制有效位数，避免整数转换限制泄漏原文。实际平台及起点相加
    后的可表示性由 compute_expiration 校验；None 不代表未配置。
    """
    if type(value) is not str:
        raise ImageHostError('config_error', '', stage='expiration')
    candidate = value.strip()
    if not candidate:
        raise ImageHostError('config_error', '', stage='expiration')

    position = 0
    previous_unit = _UNIT_SECONDS['w'] + 1
    total = 0
    while position < len(candidate):
        match = _SEGMENT.match(candidate, position)
        if match is None:
            raise ImageHostError('config_error', '', stage='expiration')
        digits = match.group(1).lstrip('0')
        unit = _UNIT_SECONDS[match.group(2)]
        if not digits or len(digits) > len(str(_MAX_SECONDS)):
            raise ImageHostError('config_error', '', stage='expiration')
        if unit >= previous_unit:
            raise ImageHostError('config_error', '', stage='expiration')
        total += int(digits) * unit
        if total > _MAX_SECONDS:
            raise ImageHostError('config_error', '', stage='expiration')
        previous_unit = unit
        position = match.end()
    return total


def compute_expiration(seconds, started_at, retention_seconds,
                       previous_deadline, now,
                       formatter=None) -> ExpirationDecision:
    """以调用方绝对起点计算期限，不读当前时钟或重新启动倒计时。

    seconds 为 None 时直接省略期限，否则须为解析跨度内的正整数。
    retention_seconds 仅接受 None 或非负整数；None、0 不裁剪。
    时间戳接受非布尔、有限且非负的 int 或 float；最终期限还须能被
    本机 datetime.fromtimestamp 表示。策略及历史期限取最早值，
    向下取整后须严格晚于 now；仅丢弃亚秒不计为策略缩短。
    formatter 只影响显示，接收取整后的绝对期限且须返回字符串。
    默认使用本机本地时间，不保证服务端采用该显示值或据此删除。
    """
    if seconds is None:
        return ExpirationDecision(None, None, False)
    if type(seconds) is not int or not 0 < seconds <= _MAX_SECONDS:
        raise ImageHostError('config_error', '', stage='expiration')
    if (retention_seconds is not None
            and (type(retention_seconds) is not int
                 or retention_seconds < 0)):
        raise ImageHostError('config_error', '', stage='expiration')

    timestamps = (started_at, now)
    if previous_deadline is not None:
        timestamps += (previous_deadline,)
    if any(type(value) not in (int, float) for value in timestamps):
        raise ImageHostError('config_error', '', stage='expiration')

    try:
        if all(value >= 0 and math.isfinite(value) for value in timestamps):
            requested_deadline = started_at + seconds
            effective_seconds = seconds
            if retention_seconds:
                effective_seconds = min(seconds, retention_seconds)
            deadline = started_at + effective_seconds
            if previous_deadline is not None:
                deadline = min(deadline, previous_deadline)
            shortened = deadline < requested_deadline
            deadline = float(math.floor(deadline))
            if deadline > now:
                local_deadline = datetime.fromtimestamp(deadline)
                expired_at = (
                    local_deadline.strftime('%Y-%m-%d %H:%M:%S')
                    if formatter is None else formatter(deadline)
                )
                if type(expired_at) is str:
                    return ExpirationDecision(deadline, expired_at, shortened)
    except Exception:
        pass
    # 在处理器之外抛出固定错误，不保留底层敏感异常链。
    raise ImageHostError('config_error', '', stage='expiration')
