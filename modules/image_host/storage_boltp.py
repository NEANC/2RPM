#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""Boltp 站点入口：委托共享组查询与选择。

组查询与选择规则统一位于共享 storage 模块，绝不请求账号资料。
"""

from . import storage
from .storage import StorageMetadata


def fetch_metadata(token: str, *, now: float) -> StorageMetadata:
    """只查询组信息，不请求账号资料，也不取账号默认编号。"""
    return storage.fetch_group_metadata('boltp', token, now=now)
