#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""主窗边框、标题栏交互及关闭确认回归。"""

import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import ctk_kit
import pytest


@pytest.fixture
def window():
    """提供真实窗口及可观察的关闭回调。"""
    from modules.gui.window import MainWindow

    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    root.geometry('500x400+100+100')
    close = Mock()
    shell = MainWindow(root, close)
    root.update()
    try:
        yield root, shell, close
    finally:
        root.destroy()
        tk._default_root = None


def test_window_has_square_borderless_shell_and_embedded_controls(window):
    """方角主窗仅保留应用内的三个系统操作按钮。"""
    root, shell, _ = window
    assert root.overrideredirect()
    assert shell.titlebar.winfo_manager() == 'pack'
    assert shell.minimize.master is shell.titlebar
    assert shell.maximize.master is shell.titlebar
    assert shell.close_button.master is shell.titlebar


def test_drag_moves_window_by_pointer_delta(window):
    """标题栏拖动按屏幕坐标差平移窗口。"""
    root, shell, _ = window
    start = (root.winfo_x(), root.winfo_y())
    shell.start_drag(SimpleNamespace(x_root=140, y_root=150))
    shell.drag(SimpleNamespace(x_root=165, y_root=185))
    root.update()
    assert (root.winfo_x(), root.winfo_y()) == (start[0] + 25, start[1] + 35)


def test_maximize_toggles_and_minimize_restores_borderless(window):
    """最大化可恢复，最小化后恢复仍无系统边框。"""
    root, shell, _ = window
    shell.maximize.invoke()
    root.update()
    assert root.state() == 'zoomed'
    shell.maximize.invoke()
    root.update()
    assert root.state() == 'normal'
    shell.minimize.invoke()
    root.update()
    assert root.state() == 'iconic'
    root.deiconify()
    root.update()
    assert root.overrideredirect()


def test_close_button_uses_existing_confirmation_callback(window):
    """关闭按钮只请求既有确认入口，不直接销毁窗口。"""
    root, shell, close = window
    shell.close_button.invoke()
    close.assert_called_once_with()
    assert root.winfo_exists()
