#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""导出截图结果模型及独立目标解析接口。"""

from .models import CaptureResult
from .targets import allocate_targets


__all__ = ['CaptureResult', 'allocate_targets']
