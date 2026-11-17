#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""主窗边框、标题栏交互及关闭确认回归。"""

import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import ctk_kit
import pytest

from tests.test_gui_pages import make_app


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
    assert shell.titlebar.winfo_manager() == 'place'
    assert shell.titlebar.winfo_width() == sum(
        button.winfo_width() for button in (shell.minimize, shell.maximize,
                                           shell.close_button))
    assert shell.titlebar.winfo_x() + shell.titlebar.winfo_width() == root.winfo_width()
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


def test_real_app_controls_overlay_sidebar_without_displacing_it(tmp_path):
    """右上操作区叠加在布局上，侧栏从主窗顶部开始。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.update()
        assert app.sidebar.winfo_y() == 0
        assert app.window.titlebar.winfo_manager() == 'place'
        assert app.window.titlebar.winfo_x() >= app.sidebar.sidebar.winfo_width()
    finally:
        root.destroy()
        tk._default_root = None


def test_home_header_actions_are_hittable_below_window_controls(tmp_path):
    """主页顶栏按钮在真实窗口里避开操作栏并能接收鼠标命中。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.update()
        home = app.visible_pages['主页']
        buttons = [widget for frame in home.winfo_children()
                   for widget in frame.winfo_children()
                   if isinstance(widget, ctk_kit.CTkButton)
                   and widget.cget('text') in ('打开配置', '新建配置')]
        assert len(buttons) == 2
        bar = app.window.titlebar
        for button in buttons:
            assert (button.winfo_rooty() >= bar.winfo_rooty() + bar.winfo_height()
                    or button.winfo_rootx() + button.winfo_width() <= bar.winfo_rootx())
            for x in (button.winfo_rootx() + 2,
                      button.winfo_rootx() + button.winfo_width() - 2):
                for y in (button.winfo_rooty() + 2,
                          button.winfo_rooty() + button.winfo_height() // 2):
                    target = root.winfo_containing(x, y)
                    assert target is button or str(target).startswith(str(button) + '.'), (
                        button.cget('text'), x, y, target)
    finally:
        root.destroy()
        tk._default_root = None


def test_home_header_starts_at_top_and_reserves_only_right_control_area(tmp_path):
    """主页标题不下移，右侧动作区仅为窗口按钮预留 132 像素。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.update()
        home = app.visible_pages['主页']
        header = home.winfo_children()[0]
        bar = app.window.titlebar
        assert header.pack_info()['pady'][0] == 0
        assert header.pack_info()['padx'] == (0, bar.winfo_width())
        assert header.winfo_rootx() + header.winfo_width() <= bar.winfo_rootx()
    finally:
        root.destroy()
        tk._default_root = None


def test_close_button_has_red_hover_in_both_themes(window):
    """关闭按钮两种主题的悬停态都是红色而非默认强调色。"""
    _root, shell, _close = window
    assert shell.close_button.cget('hover_color') == ('#C42B1C', '#C42B1C')


def test_real_app_brand_area_drags_window(tmp_path):
    """侧栏品牌背景与标题上的真实 Tk 事件都能移动窗口。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.update()
        for widget in (app.sidebar._head, app.sidebar.title_label):
            start = (root.winfo_x(), root.winfo_y())
            target = widget._canvas
            target.event_generate('<ButtonPress-1>', x=5, y=5, warp=True)
            root.update()
            target.event_generate('<B1-Motion>', x=25, y=35, warp=True)
            root.update()
            assert (root.winfo_x(), root.winfo_y()) == (start[0] + 20, start[1] + 30)
    finally:
        root.destroy()
        tk._default_root = None


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


@pytest.mark.parametrize('entry', ('button', 'protocol'))
def test_real_app_close_cancel_preserves_window(tmp_path, entry):
    """真实 App 的按钮和窗口关闭协议取消后仍可继续使用。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        app.controller.runner.active = True
        app.controller.confirm = lambda *_: 'cancel'
        root.update()
        if entry == 'button':
            app.window.close_button.invoke()
        else:
            root.tk.call(root.protocol('WM_DELETE_WINDOW'))
        assert root.winfo_exists()
        assert app.window.maximize.winfo_exists()
        assert root.overrideredirect()
        app.controller.runner.close.assert_not_called()
    finally:
        if root.winfo_exists():
            root.destroy()
        tk._default_root = None


@pytest.mark.parametrize('entry', ('button', 'protocol'))
def test_real_app_close_confirm_destroys_window(tmp_path, entry):
    """真实 App 的按钮和窗口关闭协议确认后结束窗口。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        app.controller.runner.active = True
        app.controller.confirm = lambda *_: 'confirm'
        root.update()
        if entry == 'button':
            app.window.close_button.invoke()
        else:
            root.tk.call(root.protocol('WM_DELETE_WINDOW'))
        assert not root.tk.call('info', 'commands', root._w)
        app.controller.runner.close.assert_called_once()
    finally:
        if root.tk.call('info', 'commands', root._w):
            root.destroy()
        tk._default_root = None


def test_real_app_window_buttons_toggle_state(tmp_path):
    """真实 App 内嵌最大化与最小化按钮操作其主窗口。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.update()
        app.window.maximize.invoke()
        root.update()
        assert root.state() == 'zoomed'
        app.window.maximize.invoke()
        root.update()
        assert root.state() == 'normal'
        app.window.minimize.invoke()
        root.update()
        assert root.state() == 'iconic'
        root.deiconify()
        root.update()
        assert root.overrideredirect()
    finally:
        if root.winfo_exists():
            root.destroy()
        tk._default_root = None
