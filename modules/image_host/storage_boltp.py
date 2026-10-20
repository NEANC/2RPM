#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""Boltp 站点入口：委托共享组查询、解析与选择。

本模块只保留过渡期的资料、期限解析入口；组查询与选择规则统一位于
共享 storage 模块，绝不请求账号资料。
"""

from collections.abc import Mapping

from . import storage
from . import v2_http
from .core import ImageHostError
from .storage import _positive_integer
from .storage import StorageMetadata
from .storage import StorageSelection


def parse_group(payload: Mapping) -> tuple[tuple[int, ...], int | None]:
    """过渡导出共享解析器，保持既有站点入口签名。"""
    return storage.parse_group(payload)


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
    """只查询组信息，不请求账号资料，也不取账号默认编号。"""
    return storage.fetch_group_metadata('boltp', token, now=now)


def select_storage(metadata: StorageMetadata, manual_present: bool,
                   manual_value: object, *,
                   excluded_id: int | None = None) -> StorageSelection:
    """过渡导出共享选择器，按组顺序取首个未被排除的编号。"""
    return storage.select_storage(metadata, manual_present, manual_value,
                                  provider='boltp', excluded_id=excluded_id)
