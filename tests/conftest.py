#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 测试共享前置：每个用例前解除已销毁的 Tk 默认根。"""

import tkinter as tk

import pytest


@pytest.fixture(autouse=True)
def _fresh_default_root():
    """让新窗口接管已销毁解释器留下的字体等 Tk 资源。"""
    tk._default_root = None
