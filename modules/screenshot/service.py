#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""按明确后端名分派截图，不解析 CLI 前缀或推断目标。"""

from .adb import capture_adb
from .models import CaptureError
from .models import CaptureResult
from .window import capture_window


def capture(source: str, target: str) -> CaptureResult:
    """只调用指定后端一次，保留结果和异常，不提供自动回退。"""
    if isinstance(source, str):
        if source == 'window':
            return capture_window(target)
        if source == 'adb':
            return capture_adb(target)
    raise CaptureError('invalid_source', '截图来源必须为 window 或 adb')
