#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""导出截图模型、目标解析及明确后端的内存截图接口。"""

from .adb import capture_adb
from .models import CaptureError
from .models import CaptureResult
from .service import capture
from .targets import allocate_targets
from .window import capture_window


__all__ = [
    'CaptureError', 'CaptureResult', 'allocate_targets', 'capture',
    'capture_adb', 'capture_window',
]
