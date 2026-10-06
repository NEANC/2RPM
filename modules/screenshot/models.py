#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""定义与截图后端无关的截图结果。"""

from dataclasses import dataclass


class CaptureError(Exception):
    """保存截图失败分类和固定安全消息。"""

    def __init__(self, code, message):
        """记录分类，不附加底层异常或用户敏感数据。"""
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class CaptureResult:
    """保存一次截图的实际载荷与不可变元数据。"""

    image_bytes: bytes
    source: str
    target: str
    width: int
    height: int
    warnings: tuple[str, ...] = ()
    image_format: str = 'jpeg'
