#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""查询最小存储元数据并纯粹选择存储，不维护缓存或执行上传。"""

from collections.abc import Mapping
from dataclasses import dataclass
import math

from . import v2_http
from .core import ImageHostError


@dataclass(frozen=True)
class StorageMetadata:
    """保存一次完整查询的存储编号、默认编号、期限和调用方时刻。"""

    storage_ids: tuple[int, ...]
    default_storage_id: int | None
    file_expire_seconds: int | None
    fetched_at: float


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


def _parse_group(payload):
    """严格校验组信息，保序去重且仅保留存储编号和可选期限。"""
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


def _parse_profile(payload):
    """校验账号默认存储，不将合法但已不可用的编号当作查询失败。"""
    data = payload.get('data')
    if not isinstance(data, Mapping):
        raise ImageHostError('storage_lookup_failed', '', stage='profile')
    options = data.get('options')
    if not isinstance(options, Mapping):
        raise ImageHostError('storage_lookup_failed', '', stage='profile')
    default = options.get('default_storage_id')
    if default is not None and not _positive_integer(default):
        raise ImageHostError('storage_lookup_failed', '', stage='profile')
    return default


def fetch_storage_metadata(provider, token, *, now) -> StorageMetadata:
    """顺序查询组和可选账号资料，Boltp 资料无权限时保留组内存储。

    凭证由调用方解析，本入口不展开或修剪。零期限不代表永久保存，
    此处不计算过期时间；普通边界异常不保留敏感底层异常链。
    """
    fetched_at = _fetch_time(now)
    stage = 'group'
    try:
        storage_ids, expire = _parse_group(
            v2_http.request_json(provider, stage, token))
        default = None
        if token:
            stage = 'profile'
            try:
                default = _parse_profile(
                    v2_http.request_json(provider, stage, token))
            except ImageHostError as error:
                # Boltp 上传令牌可无资料读取权限，默认项缺失不阻断上传。
                if not (provider == 'boltp' and error.code == 'http_failed'
                        and error.http_status == 403):
                    raise
        return StorageMetadata(storage_ids, default, expire, fetched_at)
    except ImageHostError:
        raise
    except Exception:
        pass
    raise ImageHostError('storage_lookup_failed', '', stage=stage)


def select_storage(metadata, manual_present, manual_value) -> StorageSelection:
    """使用已校验快照按手填、默认、首项顺序选择，不修改输入。"""
    storage_ids = metadata.storage_ids
    if not storage_ids:
        raise ImageHostError('storage_unavailable', '', stage='storage')
    if (manual_present and _positive_integer(manual_value)
            and manual_value in storage_ids):
        return StorageSelection(manual_value, False)
    default = metadata.default_storage_id
    selected = default if default in storage_ids else storage_ids[0]
    return StorageSelection(selected, manual_present)
