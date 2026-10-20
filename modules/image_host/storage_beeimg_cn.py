#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""BeeIMG.CN 站点入口：只委托共享组查询、解析与选择。

站点模块保留薄入口，不持有解析器、缓存或上传状态；业务规则统一
位于共享 storage 模块，绝不请求账号资料。
"""

from . import storage
from .storage import StorageMetadata
from .storage import StorageSelection


def parse_group(payload) -> tuple[tuple[int, ...], int | None]:
    """过渡导出共享解析器，保持既有站点入口签名。"""
    return storage.parse_group(payload)


def fetch_metadata(token: str, *, now: float) -> StorageMetadata:
    """只查询组信息，绝不请求账号资料，也不保留完整响应。"""
    return storage.fetch_group_metadata('beeimg_cn', token, now=now)


def select_storage(metadata: StorageMetadata, manual_present: bool,
                   manual_value: object, *,
                   excluded_id: int | None = None) -> StorageSelection:
    """过渡导出共享选择器，按组顺序取首个未被排除的编号。"""
    return storage.select_storage(metadata, manual_present, manual_value,
                                  provider='beeimg_cn', excluded_id=excluded_id)
