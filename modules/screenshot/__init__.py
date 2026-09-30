#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""导出截图模型、独立目标解析和窗口捕获接口。"""

from .models import CaptureError
from .models import CaptureResult
from .targets import allocate_targets
from .window import capture_window


__all__ = [
    'CaptureError', 'CaptureResult', 'allocate_targets', 'capture_window',
]
