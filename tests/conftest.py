#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 测试共享前置：每个用例前解除已销毁的 Tk 默认根。"""

import tkinter as tk

import customtkinter
import pytest

# 同一测试进程会先后创建 ctk App 与普通 tk.Tk。ctk App 创建时会把整个进程
# 切到系统 DPI 感知，之后的普通 Tk 根窗口仍按 1.0 逻辑像素搭建，ctk 控件
# 却被放大到系统缩放，页面行溢出后不再映射，焦点断言会落到根窗口。
# 测试统一按 100% 缩放运行；需要验证缩放的用例自行设置 ScalingTracker。
customtkinter.ScalingTracker.deactivate_automatic_dpi_awareness = True


@pytest.fixture(autouse=True)
def _fresh_default_root():
    """让新窗口接管已销毁解释器留下的字体等 Tk 资源。"""
    tk._default_root = None
