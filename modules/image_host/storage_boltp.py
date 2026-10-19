#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""Boltp 站点专用的组、资料与期限查询、解析与选择规则。

本模块只导入共享模型与 HTTP 基础设施，保留原有 group→profile 顺序、
403 资料容忍与默认优先算法；与另一站点互不共享解析器或选择状态。
"""

from collections.abc import Mapping

from . import v2_http
from .core import ImageHostError
from .storage import _fetch_time
from .storage import _positive_integer
from .storage import StorageMetadata
from .storage import StorageSelection


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


def parse_profile(payload: Mapping) -> int | None:
    """校验账号默认存储，不把合法但已不可用的编号当作查询失败。"""
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


def parse_retention(payload: Mapping) -> int | None:
    """只校验组内期限字段，不要求 storages 字段出现或类型正确。"""
    try:
        data = payload.get('data')
        if not isinstance(data, Mapping):
            raise ValueError
        group = data.get('group')
        if not isinstance(group, Mapping):
            raise ValueError
        options = group.get('options')
        if not isinstance(options, Mapping):
            raise ValueError
        expire = options.get('file_expire_seconds')
        if expire is not None and (
                not isinstance(expire, int) or isinstance(expire, bool)
                or expire < 0):
            raise ValueError
        return expire
    except ImageHostError:
        raise
    except Exception:
        pass
    raise ImageHostError('storage_lookup_failed', '', stage='group')


def fetch_retention(token: str) -> int | None:
    """单次查询组信息并返回可选期限，不请求资料或写入任何缓存。"""
    try:
        return parse_retention(v2_http.request_json('boltp', 'group', token))
    except ImageHostError:
        raise
    except Exception:
        pass
    raise ImageHostError('storage_lookup_failed', '', stage='group')


def fetch_metadata(token: str, *, now: float) -> StorageMetadata:
    """顺序查询组和可选账号资料，资料无权限时保留组内存储。

    凭证由调用方解析，本入口不展开或修剪。零期限不代表永久保存，
    此处不计算过期时间；普通边界异常不保留敏感底层异常链。
    """
    fetched_at = _fetch_time(now)
    stage = 'group'
    try:
        storage_ids, expire = parse_group(
            v2_http.request_json('boltp', 'group', token))
        default = None
        profile_state = 'not_requested'
        if token:
            stage = 'profile'
            try:
                default = parse_profile(
                    v2_http.request_json('boltp', 'profile', token))
                profile_state = 'available'
            except ImageHostError as error:
                # 上传令牌可无资料读取权限，默认项缺失不阻断上传。
                tolerant = (error.code == 'http_failed'
                            and error.http_status == 403)
                if not tolerant:
                    raise
        return StorageMetadata(storage_ids, default, expire, fetched_at,
                               profile_state)
    except ImageHostError:
        raise
    except Exception:
        pass
    raise ImageHostError('storage_lookup_failed', '', stage=stage)


def select_storage(metadata: StorageMetadata, manual_present: bool,
                   manual_value: object) -> StorageSelection:
    """使用已校验快照按手填、默认、首项顺序选择，不修改输入。"""
    storage_ids = metadata.storage_ids
    if not storage_ids:
        raise ImageHostError('storage_unavailable', '', stage='storage')
    if (manual_present and _positive_integer(manual_value)
            and manual_value in storage_ids):
        return StorageSelection(manual_value, False)
    if metadata.profile_state == 'forbidden':
        code = ('profile_forbidden_invalid_storage' if manual_present
                else 'profile_forbidden')
        raise ImageHostError(code, '', stage='profile', http_status=403)
    default = metadata.default_storage_id
    selected = default if default in storage_ids else storage_ids[0]
    return StorageSelection(selected, manual_present)
