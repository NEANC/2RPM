#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""系统标题栏、页面留白及关闭确认回归。"""

import ctypes
import os
import subprocess
import sys
import tkinter as tk
from unittest.mock import Mock
from unittest.mock import call

import ctk_kit
import customtkinter
import pytest

from modules.gui.appearance import FONT_BODY, FONT_CARD_TITLE, FONT_TITLE
from tests.gui_support import make_app


def _expose_for_pointer(root):
    """真实屏幕命中前将受测窗口置于其他普通窗口之上。"""
    root.attributes('-topmost', True)
    try:
        root.lift()
        root.update()
    finally:
        root.attributes('-topmost', False)


def _widget_texts(widget):
    """递归收集控件树中显式设置的文本，用于确认没有应用内窗口按钮。"""
    texts = []
    try:
        text = widget.cget('text')
    except (tk.TclError, ValueError):
        text = None
    if isinstance(text, str):
        texts.append(text)
    for child in widget.winfo_children():
        texts.extend(_widget_texts(child))
    return texts


def test_expose_for_pointer_restores_topmost_after_update_error():
    """窗口更新抛错时不让临时置顶状态遗留。"""
    root = Mock()
    root.update.side_effect = RuntimeError('update failed')
    with pytest.raises(RuntimeError, match='update failed'):
        _expose_for_pointer(root)
    assert root.attributes.call_args_list == [
        call('-topmost', True), call('-topmost', False)]
    root.lift.assert_called_once_with()
    root.update.assert_called_once_with()


def test_real_app_uses_system_titlebar_without_in_app_controls(tmp_path):
    """真实 ctk-kit 主窗使用系统标题栏，不再自绘窗口控制按钮。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.update()
        assert not root.overrideredirect()
        assert root.title() == '2RPM 配置'
        assert not hasattr(app, 'window')
        texts = _widget_texts(root)
        assert '□' not in texts
        assert '—' not in texts
    finally:
        root.destroy()
        tk._default_root = None


def test_real_app_keeps_close_confirmation_protocol(tmp_path):
    """系统标题栏的关闭按钮仍走未保存配置确认流程。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        make_app(root, tmp_path)
        root.update()
        protocol = root.protocol('WM_DELETE_WINDOW')
        assert protocol
        assert root.protocol('WM_DELETE_WINDOW') == protocol
    finally:
        root.destroy()
        tk._default_root = None


def test_home_header_uses_regular_page_padding(tmp_path):
    """主页页头恢复常规留白，不再为右上控制栏预留高度或宽度。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.update()
        header = app.visible_pages['主页'].winfo_children()[0]
        scale = header._get_widget_scaling()
        assert round(header.pack_info()['padx'] / scale) == 28
        assert tuple(round(value / scale)
                     for value in header.pack_info()['pady']) == (28, 0)
    finally:
        root.destroy()
        tk._default_root = None


def test_config_page_headers_have_no_control_reserve(tmp_path):
    """配置页页头左右贴齐内容区，不再为窗口控制栏留出右侧空白。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.update()
        for name in ('截图设置', '消息推送设置', '日志设置'):
            assert app.show_page(name)
            root.update()
            header = app.visible_pages[name].winfo_children()[0]
            scale = header._get_widget_scaling()
            assert round(header.pack_info()['padx'] / scale) == 0, name
            assert tuple(round(value / scale)
                         for value in header.pack_info()['pady']) == (0, 12), name
    finally:
        root.destroy()
        tk._default_root = None


def test_light_sidebar_uses_light_surface_and_readable_navigation(tmp_path, monkeypatch):
    """浅色真实外壳不沿用库中玻璃侧栏的深色键色。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    (tmp_path / 'gui.ini').write_text('[appearance]\ndark = false\nmaterial = none\n', encoding='utf-8')
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.update()
        assert app.sidebar.sidebar.cget('fg_color') == '#F3F3F3'
        assert app.sidebar.title_label.cget('text_color') == '#1A1A1A'
        assert app.sidebar._buttons['监视设置'].cget('text_color') == '#5F5F5F'
        assert app.sidebar._bottom_buttons['设置'].cget('text_color') == '#1A1A1A'
        assert app.config_group_button.cget('text_color') == '#5F5F5F'
        assert app.visible_pages['主页'].save_button.cget('fg_color') == '#0067C0'
        assert app.show_page('监视设置')
        assert app.sidebar._buttons['主页'].cget('text_color') == '#5F5F5F'
        app.choose_theme('深色')
        assert app.sidebar.sidebar.cget('fg_color') == '#1C1C1C'
        assert app.sidebar.title_label.cget('text_color') == '#F3F3F3'
        app.choose_theme('浅色')
        assert app.sidebar.sidebar.cget('fg_color') == '#F3F3F3'
        assert app.sidebar._buttons['主页'].cget('text_color') == '#5F5F5F'
    finally:
        root.destroy()
        tk._default_root = None


def test_sidebar_width_stays_fixed_across_pages_and_resize(tmp_path):
    """配置组和底部设置不随切页或拉宽改变侧栏宽度。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        scale = app.sidebar.sidebar._get_widget_scaling()
        for width in (900, 600, 900):
            root.geometry(f'{width}x680+100+100')
            for name in ('主页', '监视设置', '日志设置', '设置'):
                assert app.show_page(name)
                root.update()
                assert round(app.sidebar.sidebar.winfo_width() / scale) == 246
                assert app.sidebar._bottom_buttons['设置'].winfo_manager()
                assert app.config_group_button.winfo_manager()
    finally:
        root.destroy()
        tk._default_root = None


def test_home_card_uses_preview_border_type_and_primary_actions(tmp_path, monkeypatch):
    """主页卡片保留内圆角，标题与按钮遵循统一字体和强调色。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    (tmp_path / 'gui.ini').write_text('[appearance]\ndark = false\nmaterial = none\n', encoding='utf-8')
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        home = app.visible_pages['主页']
        root.update()
        cards = [child for child in home.winfo_children()
                 if isinstance(child, ctk_kit.Card)]
        assert len(cards) == 3
        for card in cards:
            assert card.cget('corner_radius') == 20
            assert card.cget('border_width') == 2
            assert card.cget('border_color') == '#E1E1E1'
            assert card.cget('fg_color') == '#FAFAFA'
        assert FONT_BODY[0] == FONT_CARD_TITLE[0] == FONT_TITLE[0] == 'Microsoft YaHei UI'
        assert (FONT_BODY[1], FONT_CARD_TITLE[1], FONT_TITLE[1]) == (15, 18, 30)
        assert FONT_CARD_TITLE[2] == FONT_TITLE[2] == 'bold'
        header = home.winfo_children()[0]
        heading = next(child for child in header.winfo_children()
                       if isinstance(child, ctk_kit.CTkLabel))
        assert heading.cget('font')[1] == 30
        actions = [child for child in header.winfo_children()
                   if isinstance(child, ctk_kit.CTkButton)]
        assert {child.cget('text') for child in actions} == {'新建配置', '打开配置'}
        assert all(child.cget('fg_color') == '#0067C0'
                   for child in actions)
        assert all(child.cget('width') == 120 and child.cget('height') == 52
                   for child in actions)
        assert all(child.cget('font')[1] == 15 for child in actions)
        assert home.summary_text
        assert home.save_button.cget('height') == 52
        assert home.save_as_button.cget('height') == 52
        assert home.groups['通知事件'].button.cget('font')[1] == 15
        assert all(round(int(card.pack_info()['pady'][0]) / card._get_widget_scaling()) == 32
                   for card in cards)
    finally:
        root.destroy()
        tk._default_root = None


def test_other_pages_share_home_card_surface(tmp_path):
    """跨页内容卡片与主页使用同一背景、边框和圆角。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.update()
        for name in ('监视设置', '截图设置', '消息推送设置',
                     '外部程序设置', '日志设置', '设置'):
            assert app.show_page(name)
            root.update()
            page = app.visible_pages[name]
            cards = [child for child in page.winfo_children()
                     if isinstance(child, ctk_kit.CTkFrame)
                     and child.cget('corner_radius') == 10]
            assert cards, name
            for card in cards:
                assert card.cget('border_width') == 1, name
                assert card.cget('border_color') == ('#DADADA', '#333333'), name
                assert card.cget('fg_color') == ('#FAFAFA', '#232323'), name
    finally:
        root.destroy()
        tk._default_root = None


def test_initial_height_respects_work_area_and_four_pages_scroll(tmp_path, monkeypatch):
    """低工作区启动不越界，四个主要页面在缩矮后仍可滚动。"""
    monkeypatch.setattr('modules.gui.app.work_area_height', lambda _root: 520)
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.update()
        assert root.geometry().startswith('900x520')
        root.geometry('900x420+100+100')
        root.update()
        assert root.geometry().startswith('900x420')
        for name in ('主页', '监视设置', '截图设置', '消息推送设置'):
            assert app.show_page(name)
            root.update()
            page = app.visible_pages[name]
            assert isinstance(page, ctk_kit.CTkScrollableFrame)
            assert page._parent_canvas.yview()[1] < 1, name
    finally:
        root.destroy()
        tk._default_root = None


def test_narrow_work_area_keeps_headers_clickable_and_scrollable(tmp_path, monkeypatch):
    """760×520 工作区内四页顶栏可命中，缩矮后内容仍可滚动。"""
    from modules.gui import app as gui_app

    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        scaling = root._get_window_scaling()

        def monitor_info(_monitor, pointer):
            """模拟物理像素表示的 760×520 工作区。"""
            info = ctypes.cast(pointer, ctypes.POINTER(gui_app._MonitorInfo)).contents
            info.work.right = round(760 * scaling)
            info.work.bottom = round(520 * scaling)
            return True

        monkeypatch.setattr(gui_app.ctypes.windll.user32, 'GetMonitorInfoW', monitor_info)
        app = make_app(root, tmp_path)
        root.update()
        assert int(root.geometry().split('x')[0]) <= 760
        assert int(root.geometry().split('x')[1].split('+')[0]) <= 520
        assert root._min_width <= 760
        root.geometry('760x420+100+100')
        root.update()
        for width in (760, 600):
            root.geometry(f'{width}x420+100+100')
            root.update()
            for name, labels in (
                ('主页', ('打开配置', '新建配置')),
                ('截图设置', ('截图测试', '上传测试')),
                ('消息推送设置', ('通知测试',)),
                ('日志设置', ('打开日志目录',)),
            ):
                assert app.show_page(name)
                root.update()
                _expose_for_pointer(root)
                page = app.visible_pages[name]
                if name in ('主页', '截图设置', '消息推送设置'):
                    assert page._parent_canvas.yview()[1] < 1, name
                header = page.winfo_children()[0]
                buttons = [child for child in header.winfo_children()
                           if isinstance(child, ctk_kit.CTkButton)]
                assert {button.cget('text') for button in buttons} == set(labels)
                for button in buttons:
                    x = button.winfo_rootx() + button.winfo_width() // 2
                    y = button.winfo_rooty() + button.winfo_height() // 2
                    assert root.winfo_rootx() <= x < root.winfo_rootx() + root.winfo_width()
                    assert root.winfo_rooty() <= y < root.winfo_rooty() + root.winfo_height()
                    target = root.winfo_containing(x, y)
                    assert target is button or str(target).startswith(str(button) + '.'), (
                        width, name, button.cget('text'), target)
        assert int(root.geometry().split('x')[0]) <= 760
        assert int(root.geometry().split('x')[1].split('+')[0]) <= 520
    finally:
        root.destroy()
        tk._default_root = None


def test_dpi_headers_keep_actions_on_one_row_after_wide_resize(tmp_path, monkeypatch):
    """1.75 倍缩放下由窄转宽，四页页头保持标题在左、按钮在右的单行布局。"""
    if os.environ.get('RPM_GUI_DPI_CHILD') != '1':
        result = subprocess.run(
            [sys.executable, '-m', 'pytest', '-q',
             'tests/test_gui_window.py::test_dpi_headers_keep_actions_on_one_row_after_wide_resize'],
            env={**os.environ, 'RPM_GUI_DPI_CHILD': '1'},
            capture_output=True, text=True, timeout=120, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        return
    monkeypatch.setattr(customtkinter.ScalingTracker, 'get_window_dpi_scaling',
                        lambda _window: 1)
    monkeypatch.setattr(customtkinter.ScalingTracker, 'widget_scaling', 1.75)
    monkeypatch.setattr(customtkinter.ScalingTracker, 'window_scaling', 1.75)
    tk._default_root = None
    root = None
    try:
        root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                           remember_theme=False, notifier=False, backdrop='none')
        assert root._get_window_scaling() == 1.75
        app = make_app(root, tmp_path)
        root.geometry('600x420+100+100')
        root.update()
        root.geometry('760x420+100+100')
        root.update()
        for name in ('主页', '截图设置', '消息推送设置', '日志设置'):
            assert app.show_page(name)
            root.update()
            header = app.visible_pages[name].winfo_children()[0]
            assert header.winfo_children()[0].pack_info()['side'] == 'left'
            buttons = [child for child in header.winfo_children()
                       if isinstance(child, ctk_kit.CTkButton)]
            assert buttons, name
            for button in buttons:
                assert button.winfo_rootx() >= root.winfo_rootx()
                assert (button.winfo_rootx() + button.winfo_width()
                        <= root.winfo_rootx() + root.winfo_width())
                assert button.pack_info()['side'] == 'right'
    finally:
        if root is not None:
            root.destroy()
        tk._default_root = None


def test_work_area_height_uses_logical_window_units(monkeypatch):
    """Win32 物理像素转成 ctk-kit geometry 使用的逻辑像素。"""
    from modules.gui import app as gui_app

    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        expected = 520
        physical = round(expected * root._get_window_scaling())

        def monitor_info(_monitor, pointer):
            """模拟低工作区的 Win32 返回值。"""
            info = ctypes.cast(pointer, ctypes.POINTER(gui_app._MonitorInfo)).contents
            info.work.bottom = physical
            return True

        monkeypatch.setattr(gui_app.ctypes.windll.user32, 'GetMonitorInfoW', monitor_info)
        assert gui_app.work_area_height(root) == expected
    finally:
        root.destroy()
        tk._default_root = None


@pytest.mark.parametrize('page_name, labels', (
    ('消息推送设置', ('通知测试',)),
    ('截图设置', ('截图测试', '上传测试')),
    ('日志设置', ('打开日志目录',)),
))
def test_config_header_actions_receive_real_pointer_clicks(tmp_path, page_name, labels):
    """配置页顶栏贴齐内容区且按钮真实命中回调。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        assert app.show_page(page_name)
        root.update()
        _expose_for_pointer(root)
        page = app.visible_pages[page_name]
        header = page.winfo_children()[0]
        assert header.pack_info()['pady'][0] == 0
        assert header.pack_info()['padx'] == 0
        buttons = [widget for widget in header.winfo_children()
                   if isinstance(widget, ctk_kit.CTkButton)]
        assert {button.cget('text') for button in buttons} == set(labels)
        for button in buttons:
            callback = Mock()
            button.configure(command=callback)
            for x in (button.winfo_rootx() + 2,
                      button.winfo_rootx() + button.winfo_width() - 2):
                for y in (button.winfo_rooty() + 2,
                          button.winfo_rooty() + button.winfo_height() // 2):
                    target = root.winfo_containing(x, y)
                    assert target is button or str(target).startswith(str(button) + '.'), (
                        page_name, button.cget('text'), x, y, target)
            x = button.winfo_rootx() + button.winfo_width() // 2
            y = button.winfo_rooty() + button.winfo_height() // 2
            target = root.winfo_containing(x, y)
            target.event_generate('<Enter>', x=x - target.winfo_rootx(),
                                  y=y - target.winfo_rooty())
            target.event_generate('<ButtonPress-1>', x=x - target.winfo_rootx(),
                                  y=y - target.winfo_rooty(), warp=True)
            target.event_generate('<ButtonRelease-1>', x=x - target.winfo_rootx(),
                                  y=y - target.winfo_rooty(), warp=True)
            root.update()
            callback.assert_called_once_with()
    finally:
        root.destroy()
        tk._default_root = None


def test_real_app_close_cancel_preserves_window(tmp_path):
    """真实 App 的窗口关闭协议取消后仍可继续使用。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        app.controller.runner.active = True
        app.controller.confirm = lambda *_: 'cancel'
        root.update()
        root.tk.call(root.protocol('WM_DELETE_WINDOW'))
        assert root.winfo_exists()
        assert not root.overrideredirect()
        app.controller.runner.close.assert_not_called()
    finally:
        if root.winfo_exists():
            root.destroy()
        tk._default_root = None


def test_real_app_close_confirm_destroys_window(tmp_path):
    """真实 App 的窗口关闭协议确认后结束窗口。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        app.controller.runner.active = True
        app.controller.confirm = lambda *_: 'confirm'
        root.update()
        root.tk.call(root.protocol('WM_DELETE_WINDOW'))
        assert not root.tk.call('info', 'commands', root._w)
        app.controller.runner.close.assert_called_once()
    finally:
        if root.tk.call('info', 'commands', root._w):
            root.destroy()
        tk._default_root = None
