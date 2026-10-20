#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""保存存储元数据与选择的共享实现，并按站点显式分发查询。

共享解析、组查询与选择算法集中在本模块；两站模块只保留薄的站点
入口。本模块不执行缓存或上传。
"""

from collections.abc import Mapping
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


def parse_group(payload: Mapping) -> tuple[tuple[int, ...], int | None]:
    """严格校验组信息，保序去重且仅保留存储编号与可选期限。

    缺项、非列表 storages、非法条目或负/布尔期限均按查询失败处理，
    不静默跳过坏项；合法但空列表归为无可用存储。
    """
    data = payload.get('data')
    if not isinstance(data, Mapping):
        raise ImageHostError('storage_lookup_failed', '', stage='group')
    group = data.get('group')
    if not isinstance(group, Mapping):
        raise ImageHostError('storage_lookup_failed', '', stage='group')
    options = group.get('options')
    storages = data.get('storages')
    if not isinstance(options, Mapping) or not isinstance(storages, list):
        raise ImageHostError('storage_lookup_failed', '', stage='group')
    expire = options.get('file_expire_seconds')
    if expire is not None and (
            not isinstance(expire, int) or isinstance(expire, bool)
            or expire < 0):
        raise ImageHostError('storage_lookup_failed', '', stage='group')
    storage_ids = []
    seen = set()
    for entry in storages:
        if not isinstance(entry, Mapping):
            raise ImageHostError('storage_lookup_failed', '', stage='group')
        storage_id = entry.get('id')
        if not _positive_integer(storage_id):
            raise ImageHostError('storage_lookup_failed', '', stage='group')
        if storage_id not in seen:
            seen.add(storage_id)
            storage_ids.append(storage_id)
    if not storage_ids:
        raise ImageHostError('storage_unavailable', '', stage='storage')
    return tuple(storage_ids), expire


def fetch_group_metadata(provider, token, *, now) -> StorageMetadata:
    """请求一次组信息并解析；不请求资料，也不写入任何缓存。

    凭证由调用方解析，本入口不展开或修剪。零期限不代表永久保存，
    此处不计算过期时间；普通边界异常不保留敏感底层异常链。
    """
    fetched_at = _fetch_time(now)
    from . import v2_http
    try:
        storage_ids, expire = parse_group(
            v2_http.request_json(provider, 'group', token))
        return StorageMetadata(storage_ids, None, expire, fetched_at,
                               'not_requested')
    except ImageHostError:
        raise
    except Exception:
        pass
    raise ImageHostError('storage_lookup_failed', '', stage='group')


def fetch_storage_metadata(provider, token, *, now) -> StorageMetadata:
    """按站点显式分发组查询；未知站点在请求前固定拒绝。"""
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
    """两站统一按组顺序取首个未被本图本槽拒绝的编号。

    手填值、账号默认编号与资料状态均不参与优先级；未知站点明确
    拒绝，不静默当作 Boltp。excluded_id 只用于恢复时排除已拒绝候选。
    """
    if provider not in ('beeimg_cn', 'boltp'):
        raise ImageHostError('invalid_provider', '', stage='group')
    candidates = [storage_id for storage_id in metadata.storage_ids
                  if storage_id != excluded_id]
    if not candidates:
        raise ImageHostError('storage_unavailable', '', stage='storage')
    return StorageSelection(candidates[0], manual_present)
