#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""主窗边框、标题栏交互及关闭确认回归。"""

import ctypes
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import call

import ctk_kit
import customtkinter
import pytest

from modules.gui.appearance import FONT_BODY, FONT_CARD_TITLE, FONT_TITLE
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


def _expose_for_pointer(root):
    """真实屏幕命中前将受测窗口置于其他普通窗口之上。"""
    root.attributes('-topmost', True)
    try:
        root.lift()
        root.update()
    finally:
        root.attributes('-topmost', False)


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


def test_window_controls_match_preview_dimensions_and_hover(window):
    """右上三个透明按钮采用主页预览的等宽和高度。"""
    root, shell, _ = window
    scale = shell.minimize._get_widget_scaling()
    for button in (shell.minimize, shell.maximize, shell.close_button):
        assert round(button.winfo_width() / scale) == 37
        assert button.cget('height') == 34
        assert button.winfo_height() == shell.titlebar.winfo_height()
        assert button.cget('fg_color') == 'transparent'
    assert shell.close_button.cget('hover_color') == ('#C42B1C', '#C42B1C')
    assert root.overrideredirect()


def test_drag_moves_window_by_pointer_delta(window):
    """标题栏拖动按屏幕坐标差平移窗口。"""
    root, shell, _ = window
    start = (root.winfo_x(), root.winfo_y())
    shell.start_drag(SimpleNamespace(x_root=140, y_root=150))
    shell.drag(SimpleNamespace(x_root=165, y_root=185))
    root.update()
    assert (root.winfo_x(), root.winfo_y()) == (start[0] + 25, start[1] + 35)


def test_pointer_hit_survives_another_window_covering_controls(window):
    """其他窗口遮住已映射按钮时，命中测试先暴露受测窗口。"""
    root, shell, _ = window
    button = shell.minimize
    x = button.winfo_rootx() + button.winfo_width() // 2
    y = button.winfo_rooty() + button.winfo_height() // 2
    blocker = tk.Toplevel(root)
    try:
        blocker.geometry(f'150x100+{x - 40}+{y - 30}')
        blocker.lift()
        blocker.update()
        assert root.winfo_ismapped() and button.winfo_ismapped()
        assert root.winfo_containing(x, y) is None
        _expose_for_pointer(root)
        assert not root.attributes('-topmost')
        target = root.winfo_containing(x, y)
        assert target is button or str(target).startswith(str(button) + '.'), (
            button.cget('text'), x, y, target)
    finally:
        blocker.destroy()


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
                assert round(app.sidebar.sidebar.winfo_width() / scale) == 280
                assert app.sidebar._bottom_buttons['设置'].winfo_manager()
                assert app.config_group_button.winfo_manager()
    finally:
        root.destroy()
        tk._default_root = None


def test_home_card_uses_preview_border_type_and_primary_actions(tmp_path):
    """主页卡片保留内圆角，标题与按钮遵循统一字体和强调色。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        home = app.visible_pages['主页']
        root.update()
        cards = [child for child in home.winfo_children()
                 if isinstance(child, ctk_kit.CTkFrame)
                 and child.cget('corner_radius') == 10]
        assert len(cards) == 3
        for card in cards:
            assert card.cget('border_width') == 1
            assert card.cget('border_color') == ('#DADADA', '#333333')
            assert card.cget('fg_color') == ('#FAFAFA', '#232323')
        assert FONT_BODY[0] == FONT_CARD_TITLE[0] == FONT_TITLE[0] == 'Microsoft YaHei UI'
        assert (FONT_BODY[1], FONT_CARD_TITLE[1], FONT_TITLE[1]) == (18, 22, 36)
        assert FONT_CARD_TITLE[2] == FONT_TITLE[2] == 'bold'
        header = home.winfo_children()[0]
        actions = [child for child in header.winfo_children()
                   if isinstance(child, ctk_kit.CTkButton)]
        assert {child.cget('text') for child in actions} == {'新建配置', '打开配置'}
        assert all(child.cget('fg_color') == '#0067C0'
                   for child in actions)
        assert all(child.cget('width') == 124 and child.cget('height') == 56
                   for child in actions)
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


def test_real_app_window_controls_receive_pointer_clicks(tmp_path):
    """侧栏装入后，三个窗口按钮中心都能命中并触达对应回调。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.update()
        _expose_for_pointer(root)
        assert not root.attributes('-topmost')
        for button in (app.window.minimize, app.window.maximize,
                       app.window.close_button):
            callback = Mock()
            button.configure(command=callback)
            x = button.winfo_rootx() + button.winfo_width() // 2
            y = button.winfo_rooty() + button.winfo_height() // 2
            target = root.winfo_containing(x, y)
            assert target is button or str(target).startswith(str(button) + '.'), (
                button.cget('text'), x, y, target)
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


def test_home_header_actions_are_hittable_below_window_controls(tmp_path):
    """主页顶栏按钮在真实窗口里避开操作栏并能接收鼠标命中。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.update()
        _expose_for_pointer(root)
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


def test_real_app_narrow_window_keeps_home_header_actions_visible(tmp_path):
    """主窗缩窄时主页动作按钮仍位于窗口内且可命中。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.geometry('420x680+100+100')
        root.update()
        _expose_for_pointer(root)
        assert root._min_width == 600
        assert root._min_height < 680
        minimum_width, minimum_height = root.tk.call('wm', 'minsize', root._w)
        assert root.winfo_width() >= minimum_width
        assert root.winfo_height() >= minimum_height
        home = app.visible_pages['主页']
        header = home.winfo_children()[0]
        buttons = [widget for widget in header.winfo_children()
                   if isinstance(widget, ctk_kit.CTkButton)]
        assert len(buttons) == 2
        for button in buttons:
            assert button.winfo_rootx() >= root.winfo_rootx()
            assert (button.winfo_rootx() + button.winfo_width() <= app.window.titlebar.winfo_rootx()
                    or button.winfo_rooty() >= app.window.titlebar.winfo_rooty()
                    + app.window.titlebar.winfo_height())
            x = button.winfo_rootx() + button.winfo_width() // 2
            y = button.winfo_rooty() + button.winfo_height() // 2
            target = root.winfo_containing(x, y)
            assert target is button or str(target).startswith(str(button) + '.')
    finally:
        root.destroy()
        tk._default_root = None


def test_dpi_narrow_then_wide_restores_home_header_layout(tmp_path):
    """高 DPI 窄窗转宽后主页顶栏恢复单行及右侧避让。"""
    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        header = app.visible_pages['主页'].winfo_children()[0]
        label = header.winfo_children()[0]
        root.geometry('600x420+100+100')
        root.update()
        assert label.pack_info()['side'] == 'top'
        root.geometry('760x420+100+100')
        root.update()
        assert label.pack_info()['side'] == 'left'
        assert header.pack_info()['padx'] == (0, app.window.titlebar.winfo_width())
        root.event_generate('<Configure>', width=760, height=420)
        root.update()
        assert label.pack_info()['side'] == 'left'
        assert header.pack_info()['padx'] == (0, app.window.titlebar.winfo_width())
    finally:
        root.destroy()
        tk._default_root = None


def test_dpi_wide_header_buttons_do_not_intersect_window_controls(tmp_path, monkeypatch):
    """1.75 倍缩放下由窄转宽时四页按钮不与窗口控制栏相交。"""
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
        bar = app.window.titlebar
        for name in ('主页', '截图设置', '消息推送设置', '日志设置'):
            assert app.show_page(name)
            root.update()
            header = app.visible_pages[name].winfo_children()[0]
            assert header.winfo_children()[0].pack_info()['side'] == 'left'
            buttons = [child for child in header.winfo_children()
                       if isinstance(child, ctk_kit.CTkButton)]
            assert buttons, name
            for button in buttons:
                assert (button.winfo_rootx() + button.winfo_width() <= bar.winfo_rootx()
                        or button.winfo_rootx() >= bar.winfo_rootx() + bar.winfo_width()
                        or button.winfo_rooty() + button.winfo_height() <= bar.winfo_rooty()
                        or button.winfo_rooty() >= bar.winfo_rooty() + bar.winfo_height()), (
                            name, button.cget('text'),
                            (button.winfo_rootx(), button.winfo_rooty(),
                             button.winfo_width(), button.winfo_height()),
                            (bar.winfo_rootx(), bar.winfo_rooty(),
                             bar.winfo_width(), bar.winfo_height()))
    finally:
        if root is not None:
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
        app.window.minimize.invoke()
        root.update()
        root.deiconify()
        root.update()
        assert root.overrideredirect()
        assert int(root.geometry().split('x')[1].split('+')[0]) <= 520
    finally:
        root.destroy()
        tk._default_root = None


def test_narrow_work_area_keeps_headers_clickable_scrollable_and_restores(tmp_path, monkeypatch):
    """760×520 工作区内四页顶栏可命中，缩矮滚动后恢复仍不越界。"""
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
        app.window.minimize.invoke()
        root.update()
        root.deiconify()
        root.update()
        assert root.overrideredirect()
        assert int(root.geometry().split('x')[0]) <= 760
        assert int(root.geometry().split('x')[1].split('+')[0]) <= 520
    finally:
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
    """配置页顶栏贴顶，右侧避让窗口控制栏且按钮真实命中回调。"""
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
        bar = app.window.titlebar
        assert header.pack_info()['pady'][0] == 0
        assert header.pack_info()['padx'] == (0, bar.winfo_width())
        assert header.winfo_rootx() + header.winfo_width() <= bar.winfo_rootx()
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


def test_repeated_minimize_restore_preserves_window_size(window):
    """多轮最小化恢复保持原始窗口尺寸且不叠加恢复回调。"""
    root, shell, _ = window
    root._gui_work_size = (760, 520)
    root.geometry('500x400+100+100')
    root.update()
    expected = root.geometry()
    for _ in range(3):
        shell.iconify()
        root.update()
        root.deiconify()
        root.update()
        assert root.geometry() == expected
        assert root.overrideredirect()
    assert len(root.bind('<Map>').splitlines()) == 1


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
