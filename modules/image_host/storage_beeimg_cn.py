#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""BeeIMG.CN 站点入口：只委托共享组查询。

站点模块保留薄入口，不持有解析器、缓存或上传状态；业务规则统一
位于共享 storage 模块，绝不请求账号资料。
"""

from . import storage
from .storage import StorageMetadata


def fetch_metadata(token: str, *, now: float) -> StorageMetadata:
    """只查询组信息，绝不请求账号资料，也不保留完整响应。"""
    return storage.fetch_group_metadata('beeimg_cn', token, now=now)
