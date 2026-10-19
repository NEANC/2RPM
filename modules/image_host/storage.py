#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""保存存储元数据与选择的共享模型，并按站点显式分发查询与选择。

两站业务规则位于 storage_beeimg_cn 与 storage_boltp；本模块只保留
共享模型、共享校验辅助与显式分发入口，不执行缓存或上传。
"""

from dataclasses import dataclass
import math
from typing import Literal

from .core import ImageHostError


@dataclass(frozen=True)
class StorageMetadata:
    """保存一次完整查询的存储编号、默认编号、期限和调用方时刻。"""

    storage_ids: tuple[int, ...]
    default_storage_id: int | None
    file_expire_seconds: int | None
    fetched_at: float
    profile_state: Literal[
        'available', 'not_requested', 'forbidden'] = 'not_requested'


@dataclass(frozen=True)
class StorageSelection:
    """保存选中的存储编号及是否替代了显式手填值。"""

    storage_id: int
    replaced_manual: bool


def _positive_integer(value):
    """只接受非布尔正整数，不转换或展示输入。"""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _fetch_time(now):
    """转换有限非负调用方时刻，在异常处理器外抛出固定配置错误。"""
    fetched_at = None
    try:
        if isinstance(now, (int, float)) and not isinstance(now, bool):
            if now >= 0:
                candidate = float(now)
                if math.isfinite(candidate):
                    fetched_at = candidate
    except Exception:
        pass
    if fetched_at is None:
        raise ImageHostError('config_error', '', stage='storage')
    return fetched_at


def fetch_storage_metadata(provider, token, *, now) -> StorageMetadata:
    """按站点显式分发组、资料查询；未知站点沿用既有固定路由错误。"""
    if provider == 'beeimg_cn':
        from . import storage_beeimg_cn
        return storage_beeimg_cn.fetch_metadata(token, now=now)
    if provider == 'boltp':
        from . import storage_boltp
        return storage_boltp.fetch_metadata(token, now=now)
    raise ImageHostError('invalid_provider', '', stage='group')


def fetch_boltp_retention(token):
    """分发到 Boltp 单次期限查询，保持既有分类与单请求语义。"""
    from . import storage_boltp
    return storage_boltp.fetch_retention(token)


def select_storage(metadata, manual_present, manual_value, *, provider='boltp',
                   excluded_id=None) -> StorageSelection:
    """三位置参数兼容入口默认 Boltp 旧算法，生产调用必须显式传 provider。

    BeeIMG.CN 永不使用默认入口；其选择器按组顺序排除 excluded_id，
    且忽略手填优先级。
    """
    if provider == 'beeimg_cn':
        from . import storage_beeimg_cn
        return storage_beeimg_cn.select_storage(
            metadata, manual_present, manual_value, excluded_id=excluded_id)
    from . import storage_boltp
    return storage_boltp.select_storage(metadata, manual_present, manual_value)
