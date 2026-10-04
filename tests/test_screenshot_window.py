#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""使用模拟窗口和合成像素验证独立窗口截图后端。"""

import ast
import builtins
import ctypes
from ctypes import wintypes
from importlib import import_module
from importlib.util import find_spec
import inspect
from io import BytesIO
import socket
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest


HWND = 0x100000123
WINDOW_DC = 0x200000123
MEMORY_DC = 0x300000123
BITMAP = 0x400000123
OLD_BITMAP = 0x500000123
OLD_DPI = 0x600000123
TITLE = '测试窗口'
PIXELS = bytes([
    0, 0, 255, 0, 0, 255, 0, 0,
    255, 0, 0, 0, 255, 255, 255, 0,
])


class WindowEnvironment:
    """模拟真实 Win32 返回值、句柄所有权和顶向下 BGRX 像素。"""

    def __init__(self, backend, monkeypatch):
        """替换所有窗口操作和 GDI 操作，禁止接触真实用户窗口。"""
        self.backend = backend
        self.events = []
        self.windows = {HWND: TITLE}
        self.visible = {}
        self.selected_hwnd = None
        self.alive = True
        self.iconic = False
        self.placement = (0, 1, (-1, -1), (-1, -1), (10, 20, 12, 22))
        self.restored_placement = None
        self.buffer = ctypes.create_string_buffer(PIXELS)
        self.selected = OLD_BITMAP
        self.gui = SimpleNamespace(
            EnumWindows=Mock(side_effect=self.enumerate_windows),
            IsWindow=Mock(side_effect=lambda handle: self.alive),
            GetWindowText=Mock(
                side_effect=lambda handle: self.windows[handle]),
            IsIconic=Mock(side_effect=lambda handle: self.iconic),
            GetWindowPlacement=Mock(side_effect=lambda handle: self.placement),
            SetWindowPlacement=Mock(side_effect=self.set_placement),
            ShowWindow=Mock(side_effect=self.show_window),
            GetWindowRect=Mock(return_value=(10, 20, 12, 22)),
            IsWindowVisible=Mock(side_effect=self.is_window_visible),
            SetForegroundWindow=Mock(side_effect=AssertionError('不得抢前台')),
            BringWindowToTop=Mock(side_effect=AssertionError('不得置顶')),
        )
        self.user32 = SimpleNamespace(
            GetWindowDC=Mock(side_effect=self.get_window_dc),
            ReleaseDC=Mock(side_effect=self.release_dc),
            PrintWindow=Mock(return_value=1),
            SetThreadDpiAwarenessContext=Mock(side_effect=self.set_dpi),
        )
        self.gdi32 = SimpleNamespace(
            CreateCompatibleDC=Mock(side_effect=self.create_dc),
            CreateDIBSection=Mock(side_effect=self.create_bitmap),
            SelectObject=Mock(side_effect=self.select_object),
            DeleteObject=Mock(side_effect=self.delete_object),
            DeleteDC=Mock(side_effect=self.delete_dc),
            GdiFlush=Mock(return_value=1),
        )
        monkeypatch.setattr(backend, 'win32gui', self.gui)
        monkeypatch.setattr(backend, '_user32', self.user32)
        monkeypatch.setattr(backend, '_gdi32', self.gdi32)
        monkeypatch.setattr(backend.time, 'sleep', Mock())

    def enumerate_windows(self, callback, argument):
        """仅枚举模拟顶层窗口，包括最小化目标。"""
        for handle in list(self.windows):
            callback(handle, argument)

    def is_window_visible(self, handle):
        """按模拟句柄返回可见状态，未配置的窗口默认可见。"""
        return self.visible.get(handle, True)

    def show_window(self, handle, command):
        """模拟恢复窗口，返回原可见状态而不是成功标志。"""
        assert handle in self.windows
        self.selected_hwnd = handle
        self.events.append(('show', handle))
        self.iconic = False
        return 0

    def set_placement(self, handle, placement):
        """记录恢复的完整状态，使用 pywin32 的无返回值约定。"""
        assert handle in self.windows
        self.selected_hwnd = handle
        self.events.append(('restore_window', handle))
        self.restored_placement = placement
        self.iconic = True
        return None

    def get_window_dc(self, handle):
        """只允许获取已登记模拟目标的完整窗口 DC。"""
        assert handle in self.windows
        self.selected_hwnd = handle
        assert self.alive
        self.events.append(('window_dc', handle))
        return WINDOW_DC

    def create_dc(self, handle):
        """要求以窗口 DC 创建兼容内存 DC。"""
        assert handle == WINDOW_DC
        self.events.append('memory_dc')
        self.selected = OLD_BITMAP
        return MEMORY_DC

    def create_bitmap(self, dc, info, usage, bits, section, offset):
        """校验原生位图结构并返回本地合成像素地址。"""
        assert dc == WINDOW_DC
        header = info._obj.bmiHeader
        assert header.biSize == 40
        assert (header.biWidth, header.biHeight) == (2, -2)
        assert (header.biPlanes, header.biBitCount) == (1, 32)
        assert header.biCompression == 0
        assert usage == 0 and not section and offset == 0
        ctypes.cast(bits, ctypes.POINTER(ctypes.c_void_p))[0] = (
            ctypes.addressof(self.buffer))
        self.events.append('bitmap')
        return BITMAP

    def select_object(self, dc, bitmap):
        """交换原生整数句柄并记录旧对象恢复次序。"""
        assert dc == MEMORY_DC
        self.events.append(
            'select_old' if bitmap == OLD_BITMAP else 'select_new')
        previous = self.selected
        self.selected = bitmap
        return previous

    def delete_object(self, bitmap):
        """确保仅删除自行创建且已不在 DC 中的位图。"""
        assert bitmap == BITMAP
        assert self.selected != BITMAP
        self.events.append('delete_bitmap')
        return 1

    def delete_dc(self, dc):
        """只删除自有内存 DC，不删除借用的窗口 DC。"""
        assert dc == MEMORY_DC
        self.events.append('delete_dc')
        self.selected = None
        return 1

    def release_dc(self, handle, dc):
        """释放借用窗口 DC，不重复或跨窗口释放。"""
        assert handle in self.windows
        assert handle == self.selected_hwnd
        assert dc == WINDOW_DC
        self.events.append(('release_dc', handle))
        return 1

    def set_dpi(self, context):
        """记录局部线程 DPI 上下文进入和还原。"""
        self.events.append(('dpi', context))
        return OLD_DPI


def load_backend():
    """使后端缺失明确表现为断言失败而不是导入收集错误。"""
    assert find_spec('modules.screenshot.window') is not None, (
        '尚未实现独立窗口截图后端')
    return import_module('modules.screenshot.window')


def environment(monkeypatch):
    """创建隔离的窗口环境，保留真实 Pillow 编解码路径。"""
    backend = load_backend()
    return backend, WindowEnvironment(backend, monkeypatch)


def assert_error(backend, code, title=TITLE):
    """核对固定分类以及业务错误不泄漏底层消息。"""
    with pytest.raises(backend.CaptureError) as caught:
        backend.capture_window(title)
    assert caught.value.code == code
    assert 'secret-token' not in str(caught.value)
    return caught.value


def test_unique_window_encodes_real_png(monkeypatch):
    """完整标题唯一匹配时返回可解码且方向、颜色正确的 PNG。"""
    backend, env = environment(monkeypatch)
    env.windows.update({HWND + 1: TITLE + '副本', HWND + 2: ' ' + TITLE})
    result = backend.capture_window(TITLE)
    assert (result.source, result.target) == ('window', TITLE)
    assert (result.width, result.height) == (2, 2)
    assert result.warnings == ()
    with Image.open(BytesIO(result.png_bytes)) as image:
        image.load()
        assert image.format == 'PNG' and image.size == (2, 2)
        assert [image.getpixel((x, y)) for y in range(2)
                for x in range(2)] == [
            (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
    env.user32.PrintWindow.assert_called_once_with(HWND, MEMORY_DC, 0x2)
    assert env.events[-5:] == [
        'select_old', 'delete_bitmap', 'delete_dc', ('release_dc', HWND),
        ('dpi', OLD_DPI)]
    env.gui.ShowWindow.assert_not_called()
    env.gui.SetWindowPlacement.assert_not_called()


def test_visible_duplicate_wins_over_hidden_duplicate(monkeypatch):
    """同名窗口中只选择可见项。"""
    backend, env = environment(monkeypatch)
    visible = HWND + 1
    hidden = HWND + 2
    env.windows = {visible: TITLE, hidden: TITLE}
    env.visible[hidden] = False
    result = backend.capture_window(TITLE)
    assert result.target == TITLE
    assert env.selected_hwnd == visible
    assert env.events[env.events.index(('window_dc', visible))] == (
        'window_dc', visible)
    env.user32.PrintWindow.assert_called_once_with(visible, MEMORY_DC, 0x2)
    env.user32.ReleaseDC.assert_called_once_with(visible, WINDOW_DC)


def test_hidden_only_window_is_not_found(monkeypatch):
    """仅隐藏的同名窗口不构成可捕获候选。"""
    backend, env = environment(monkeypatch)
    env.visible[HWND] = False
    assert_error(backend, 'window_not_found')
    env.user32.PrintWindow.assert_not_called()


def test_multiple_visible_duplicates_remain_ambiguous(monkeypatch):
    """多个可见同名窗口仍明确报告歧义。"""
    backend, env = environment(monkeypatch)
    env.windows = {HWND: TITLE, HWND + 1: TITLE}
    assert_error(backend, 'window_ambiguous')
    env.user32.PrintWindow.assert_not_called()


@pytest.mark.parametrize('title', [None, False, 123, '', ' \t ', [], {}])
def test_invalid_input_fails_before_window_operations(monkeypatch, title):
    """非法标题不进行枚举、DPI 修改或窗口操作。"""
    backend, env = environment(monkeypatch)
    assert_error(backend, 'invalid_title', title)
    env.gui.EnumWindows.assert_not_called()
    assert env.events == []


@pytest.mark.parametrize(('windows', 'code'), [
    ({HWND: TITLE + '副本'}, 'window_not_found'),
    ({HWND: TITLE, HWND + 1: TITLE}, 'window_ambiguous'),
])
def test_missing_or_duplicate_title_never_captures(monkeypatch, windows, code):
    """缺失或重名标题明确失败，不选择第一项或前台窗口。"""
    backend, env = environment(monkeypatch)
    env.windows = windows
    assert_error(backend, code)
    env.user32.PrintWindow.assert_not_called()
    env.gui.ShowWindow.assert_not_called()


def test_title_whitespace_is_not_silently_normalized(monkeypatch):
    """完整标题中的有效空格必须参与精确匹配。"""
    backend, env = environment(monkeypatch)
    env.windows[HWND] = ' ' + TITLE + ' '
    result = backend.capture_window(' ' + TITLE + ' ')
    assert result.target == ' ' + TITLE + ' '


@pytest.mark.parametrize('fails', [False, True])
def test_minimized_window_restores_original_placement(monkeypatch, fails):
    """可见最小化窗口在成功或失败后都恢复原始 placement。"""
    backend, env = environment(monkeypatch)
    env.iconic = True
    env.placement = (2, 2, (-1, -1), (0, 0), (10, 20, 12, 22))
    if fails:
        env.user32.PrintWindow.return_value = 0
        assert_error(backend, 'print_failed')
    else:
        backend.capture_window(TITLE)
    assert env.restored_placement == env.placement
    assert env.iconic
    env.gui.ShowWindow.assert_called_once()
    env.gui.SetWindowPlacement.assert_called_once_with(HWND, env.placement)
    assert backend.time.sleep.called
    assert all(0 <= call.args[0] <= 1
               for call in backend.time.sleep.call_args_list)
    env.gui.SetForegroundWindow.assert_not_called()
    env.gui.BringWindowToTop.assert_not_called()


@pytest.mark.parametrize('show_command', [1, 3])
def test_normal_and_maximized_windows_are_not_changed(
        monkeypatch, show_command):
    """原本正常或最大化窗口不进行恢复或最小化操作。"""
    backend, env = environment(monkeypatch)
    env.placement = (0, show_command, (-1, -1), (-1, -1), (10, 20, 12, 22))
    backend.capture_window(TITLE)
    env.gui.ShowWindow.assert_not_called()
    env.gui.SetWindowPlacement.assert_not_called()


def test_window_destroyed_during_print_is_reported(monkeypatch):
    """目标在捕获中销毁时不返回图像，也不再次操作失效窗口。"""
    backend, env = environment(monkeypatch)
    env.iconic = True

    def destroy_window(*args):
        """模拟 PrintWindow 期间目标被关闭。"""
        env.alive = False
        return 1

    env.user32.PrintWindow.side_effect = destroy_window
    assert_error(backend, 'window_gone')
    env.gui.SetWindowPlacement.assert_not_called()
    env.user32.ReleaseDC.assert_called_once_with(HWND, WINDOW_DC)


def test_window_destroyed_before_capture_never_uses_dc(monkeypatch):
    """枚举后失效的句柄不能进入捕获路径。"""
    backend, env = environment(monkeypatch)
    original = env.enumerate_windows

    def enumerate_then_destroy(callback, argument):
        """在枚举结束时使选中的窗口失效。"""
        original(callback, argument)
        env.alive = False

    env.gui.EnumWindows.side_effect = enumerate_then_destroy
    assert_error(backend, 'window_gone')
    env.user32.GetWindowDC.assert_not_called()


@pytest.mark.parametrize('rectangle', [
    (0, 0, 0, 2), (2, 0, 1, 2), (0, 1, 2, 1),
])
def test_invalid_dimensions_fail_before_allocation(monkeypatch, rectangle):
    """无效尺寸不分配 GDI 图像。"""
    backend, env = environment(monkeypatch)
    env.gui.GetWindowRect.return_value = rectangle
    assert_error(backend, 'invalid_dimensions')
    env.user32.GetWindowDC.assert_not_called()


@pytest.mark.parametrize(('stage', 'expected'), [
    ('GetWindowDC', []),
    ('CreateCompatibleDC', ['release_dc']),
    ('CreateDIBSection', ['delete_dc', 'release_dc']),
    ('SelectObject', ['delete_bitmap', 'delete_dc', 'release_dc']),
])
@pytest.mark.parametrize('raises', [False, True])
def test_partial_gdi_allocation_releases_owned_resources(
        monkeypatch, stage, expected, raises):
    """各初始化阶段失败时仅按所有权清理已成功分配的资源。"""
    backend, env = environment(monkeypatch)
    owner = env.user32 if stage == 'GetWindowDC' else env.gdi32
    function = getattr(owner, stage)
    function.side_effect = OSError('secret-token') if raises else None
    function.return_value = 0
    assert_error(backend, 'gdi_failed')
    cleanup = [
        event[0] if isinstance(event, tuple) else event
        for event in env.events
        if (event[0] if isinstance(event, tuple) else event) in (
            'select_old', 'delete_bitmap', 'delete_dc', 'release_dc')]
    assert cleanup == expected
    env.user32.PrintWindow.assert_not_called()
    assert env.events[-1] == ('dpi', OLD_DPI)


def test_print_zero_releases_resources_in_order(monkeypatch):
    """PrintWindow 返回零必须分类失败并先恢复旧 bitmap。"""
    backend, env = environment(monkeypatch)
    env.user32.PrintWindow.return_value = 0
    assert_error(backend, 'print_failed')
    assert env.events[-5:] == [
        'select_old', 'delete_bitmap', 'delete_dc', ('release_dc', HWND),
        ('dpi', OLD_DPI)]


def test_pillow_conversion_failure_is_classified(monkeypatch):
    """真实图像转换分配失败被归类且 GDI 仍被释放。"""
    backend, env = environment(monkeypatch)
    monkeypatch.setattr(
        Image, 'frombytes', Mock(side_effect=MemoryError('secret-token')))
    assert_error(backend, 'image_failed')
    env.gdi32.DeleteObject.assert_called_once_with(BITMAP)
    env.user32.ReleaseDC.assert_called_once()


def test_png_encoding_failure_is_classified(monkeypatch):
    """内存 PNG 编码失败不泄露底层异常内容。"""
    backend, env = environment(monkeypatch)
    monkeypatch.setattr(
        Image.Image, 'save', Mock(side_effect=OSError('secret-token')))
    assert_error(backend, 'encode_failed')
    env.user32.ReleaseDC.assert_called_once()


def test_invalid_encoded_png_is_rejected(monkeypatch):
    """编码输出必须实际可解码，不能只检查字节非空。"""
    backend, _ = environment(monkeypatch)

    def save_invalid(image, output, *args, **kwargs):
        """模拟编码器输出损坏数据。"""
        output.write(b'not-a-png')

    monkeypatch.setattr(Image.Image, 'save', save_invalid)
    assert_error(backend, 'encode_failed')


@pytest.mark.parametrize('main_failure', [False, True])
def test_restore_exception_never_masks_primary_error(
        monkeypatch, main_failure):
    """状态还原异常保留截图主因，成功图像则附安全告警。"""
    backend, env = environment(monkeypatch)
    env.iconic = True
    env.gui.SetWindowPlacement.side_effect = OSError('secret-token')
    if main_failure:
        env.user32.PrintWindow.return_value = 0
        assert_error(backend, 'print_failed')
    else:
        result = backend.capture_window(TITLE)
        assert any('还原' in warning for warning in result.warnings)
        assert 'secret-token' not in str(result.warnings)
        with Image.open(BytesIO(result.png_bytes)) as image:
            image.load()
            assert image.size == (2, 2)
    assert env.events[-1] == ('dpi', OLD_DPI)


@pytest.mark.parametrize('signal', [KeyboardInterrupt, SystemExit])
def test_control_signals_propagate_after_cleanup(monkeypatch, signal):
    """控制信号在资源和窗口状态清理后继续传播。"""
    backend, env = environment(monkeypatch)
    env.iconic = True
    env.user32.PrintWindow.side_effect = signal()
    with pytest.raises(signal):
        backend.capture_window(TITLE)
    env.gdi32.DeleteObject.assert_called_once_with(BITMAP)
    env.user32.ReleaseDC.assert_called_once()
    env.gui.SetWindowPlacement.assert_called_once_with(HWND, env.placement)
    assert env.events[-1] == ('dpi', OLD_DPI)


@pytest.mark.parametrize('value', [0, 80])
def test_uniform_valid_image_only_warns(monkeypatch, value):
    """纯黑或低方差图像仍是成功 PNG，不能触发另一后端。"""
    backend, env = environment(monkeypatch)
    env.buffer = ctypes.create_string_buffer(
        bytes([value, value, value, 0]) * 4)
    result = backend.capture_window(TITLE)
    assert result.warnings
    with Image.open(BytesIO(result.png_bytes)) as image:
        image.load()
        assert image.size == (2, 2)
        assert image.getpixel((0, 0)) == (value, value, value)
    env.user32.PrintWindow.assert_called_once()


def test_dpi_failure_does_not_modify_window(monkeypatch):
    """线程 DPI 设置失败时不触碰窗口状态或申请 DC。"""
    backend, env = environment(monkeypatch)
    env.user32.SetThreadDpiAwarenessContext.side_effect = None
    env.user32.SetThreadDpiAwarenessContext.return_value = None
    assert_error(backend, 'dpi_failed')
    env.gui.ShowWindow.assert_not_called()
    env.user32.GetWindowDC.assert_not_called()


def test_native_signatures_preserve_pointer_sized_handles():
    """直接检查真实 ctypes 绑定，不用替身掩盖 64 位句柄截断。"""
    backend = load_backend()
    assert backend._user32.PrintWindow.argtypes == [
        wintypes.HWND, wintypes.HDC, wintypes.UINT]
    assert backend._user32.PrintWindow.restype is wintypes.BOOL
    assert backend._user32.GetWindowDC.argtypes == [wintypes.HWND]
    assert backend._user32.GetWindowDC.restype is wintypes.HDC
    assert backend._gdi32.CreateCompatibleDC.restype is wintypes.HDC
    assert backend._gdi32.CreateDIBSection.restype is wintypes.HBITMAP
    assert backend._gdi32.SelectObject.restype is wintypes.HANDLE
    dpi_function = backend._user32.SetThreadDpiAwarenessContext
    assert dpi_function.argtypes == [ctypes.c_void_p]
    assert dpi_function.restype is ctypes.c_void_p
    assert ctypes.sizeof(wintypes.HWND) == ctypes.sizeof(ctypes.c_void_p)
    assert backend.PW_RENDERFULLCONTENT == 0x2


def test_capture_performs_no_file_network_or_process_io(monkeypatch):
    """初始化 Pillow 插件后禁止截图路径写文件、联网或启动进程。"""
    backend, _ = environment(monkeypatch)
    Image.init()
    forbidden = Mock(side_effect=AssertionError('截图不得执行外部 IO'))
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    result = backend.capture_window(TITLE)
    assert result.png_bytes.startswith(b'\x89PNG\r\n\x1a\n')
    forbidden.assert_not_called()


def test_selected_window_hidden_during_recheck_is_not_reselected(monkeypatch):
    """选定后复核发现隐藏时不重选同名窗口或分配 DC。"""
    backend, env = environment(monkeypatch)
    other = HWND + 1
    env.windows[other] = TITLE
    env.visible[other] = False
    visible_calls = 0

    def hide_after_selection(handle):
        nonlocal visible_calls
        if handle == HWND:
            visible_calls += 1
            return visible_calls < 3
        return False

    env.gui.IsWindowVisible.side_effect = hide_after_selection
    assert_error(backend, 'window_gone')
    assert env.gui.EnumWindows.call_count == 1
    env.user32.GetWindowDC.assert_not_called()
    env.user32.PrintWindow.assert_not_called()
    assert env.events[-1] == ('dpi', OLD_DPI)


def test_hidden_after_print_releases_resources_and_dpi(monkeypatch):
    """PrintWindow 后观察到隐藏时保留 window_gone 并执行资源清理。"""
    backend, env = environment(monkeypatch)

    def hide_after_print(*args):
        env.visible[HWND] = False
        return 1

    env.user32.PrintWindow.side_effect = hide_after_print
    assert_error(backend, 'window_gone')
    env.gdi32.DeleteObject.assert_called_once_with(BITMAP)
    env.gdi32.DeleteDC.assert_called_once_with(MEMORY_DC)
    env.user32.ReleaseDC.assert_called_once_with(HWND, WINDOW_DC)
    assert env.events[-1] == ('dpi', OLD_DPI)


def test_hidden_minimized_window_skips_placement_when_still_hidden(monkeypatch):
    """捕获时隐藏且清理仍隐藏时不恢复 placement，仅告警一次。"""
    backend, env = environment(monkeypatch)
    env.iconic = True
    env.user32.PrintWindow.side_effect = lambda *args: (
        env.visible.__setitem__(HWND, False) or 1)
    assert_error(backend, 'window_gone')
    env.gui.SetWindowPlacement.assert_not_called()
    assert env.gui.ShowWindow.call_count == 1
    assert env.events[-1] == ('dpi', OLD_DPI)


def test_hidden_then_visible_minimized_window_restores_but_keeps_error(
        monkeypatch):
    """清理复核时重显允许恢复 placement，但不挽救捕获错误。"""
    backend, env = environment(monkeypatch)
    env.iconic = True
    visibility_calls = 0

    def hide_then_show(handle):
        nonlocal visibility_calls
        visibility_calls += 1
        if visibility_calls == 8:
            env.visible[handle] = False
        elif visibility_calls >= 9:
            env.visible[handle] = True
        return env.visible.get(handle, True)

    env.gui.IsWindowVisible.side_effect = hide_then_show
    with pytest.raises(backend.CaptureError) as caught:
        backend.capture_window(TITLE)
    assert caught.value.code == 'window_gone'
    env.gui.SetWindowPlacement.assert_called_once_with(HWND, env.placement)
    env.user32.PrintWindow.assert_not_called()


def test_non_minimized_hidden_window_adds_no_state_warning(monkeypatch):
    """非最小化捕获因隐藏失败时不触发状态恢复告警。"""
    backend, env = environment(monkeypatch)
    env.user32.PrintWindow.side_effect = lambda *args: (
        env.visible.__setitem__(HWND, False) or 1)
    assert_error(backend, 'window_gone')
    env.gui.SetWindowPlacement.assert_not_called()
    env.gui.ShowWindow.assert_not_called()


@pytest.mark.parametrize('failure', ['title', 'visibility'])
def test_selected_window_recheck_failures_are_sanitized(monkeypatch, failure):
    """标题变化和可见性 API 异常映射为安全的 window_gone。"""
    backend, env = environment(monkeypatch)
    if failure == 'title':
        env.gui.GetWindowText.side_effect = [TITLE, TITLE, '改名窗口']
    else:
        env.gui.IsWindowVisible.side_effect = [True, True, OSError('secret-token')]
    error = assert_error(backend, 'window_gone')
    assert 'secret-token' not in str(error)
    env.user32.GetWindowDC.assert_not_called()


def test_candidate_visibility_exception_is_sanitized(monkeypatch):
    """候选可见性 API 普通异常映射为安全的查询错误。"""
    backend, env = environment(monkeypatch)
    env.gui.IsWindowVisible.side_effect = OSError('secret-token')
    error = assert_error(backend, 'window_lookup_failed')
    assert 'secret-token' not in str(error)
    env.user32.GetWindowDC.assert_not_called()




@pytest.mark.parametrize(('operation', 'code'), [
    ('GetWindowPlacement', 'window_state_failed'),
    ('ShowWindow', 'window_state_failed'),
    ('GetWindowDC', 'gdi_failed'),
    ('PrintWindow', 'print_failed'),
])
@pytest.mark.parametrize('signal', [KeyboardInterrupt, SystemExit])
def test_control_signals_at_capture_checkpoints_stop_without_result(
        monkeypatch, operation, code, signal):
    """捕获各检查点收到控制信号时不返回图像且不重选句柄。"""
    backend, env = environment(monkeypatch)
    env.iconic = operation == 'ShowWindow'
    owner = env.gui if operation in ('GetWindowPlacement', 'ShowWindow') else env.user32
    getattr(owner, operation).side_effect = signal()
    with pytest.raises(signal):
        backend.capture_window(TITLE)
    assert env.user32.PrintWindow.call_count == (1 if operation == 'PrintWindow' else 0)
    assert env.gui.EnumWindows.call_count == 1
    assert env.selected_hwnd in (None, HWND)
    assert env.events[-1] == ('dpi', OLD_DPI)
    if operation == 'PrintWindow':
        env.gdi32.DeleteObject.assert_called_once_with(BITMAP)
        env.gdi32.DeleteDC.assert_called_once_with(MEMORY_DC)
        env.user32.ReleaseDC.assert_called_once_with(HWND, WINDOW_DC)
    if operation == 'GetWindowDC':
        env.user32.GetWindowDC.assert_called_once_with(HWND)
    elif operation != 'PrintWindow':
        env.user32.GetWindowDC.assert_not_called()


def test_hidden_at_final_return_has_no_image_and_keeps_cleanup_contract(monkeypatch):
    """最终返回前窗口隐藏时报告 window_gone，不重选且完成 GDI/DPI 清理。"""
    backend, env = environment(monkeypatch)

    def hide_after_encoding(*args):
        env.visible[HWND] = False
        return 1

    env.user32.PrintWindow.side_effect = hide_after_encoding
    assert_error(backend, 'window_gone')
    assert env.gui.EnumWindows.call_count == 1
    env.user32.PrintWindow.assert_called_once_with(HWND, MEMORY_DC, 0x2)
    env.gdi32.DeleteObject.assert_called_once_with(BITMAP)
    env.gdi32.DeleteDC.assert_called_once_with(MEMORY_DC)
    env.user32.ReleaseDC.assert_called_once_with(HWND, WINDOW_DC)
    assert env.events[-1] == ('dpi', OLD_DPI)


def test_renamed_minimized_window_skips_restore_and_preserves_error(monkeypatch):
    """清理时标题改名不调用 placement 或额外 ShowWindow，并保留原错误。"""
    backend, env = environment(monkeypatch)
    env.iconic = True

    def rename_during_print(*args):
        env.windows[HWND] = '改名窗口'
        return 0

    env.user32.PrintWindow.side_effect = rename_during_print
    assert_error(backend, 'window_gone')
    env.gui.SetWindowPlacement.assert_not_called()
    assert env.gui.ShowWindow.call_count == 1
    assert env.events[-1] == ('dpi', OLD_DPI)


def test_backend_has_no_fallback_or_global_dpi_calls():
    """静态核对不引入桌面回退、ADB 或全局 DPI 修改。"""
    backend = load_backend()
    tree = ast.parse(inspect.getsource(backend))
    prohibited = {
        'SetProcessDPIAware', 'SetProcessDpiAwareness',
        'SetProcessDpiAwarenessContext', 'SetForegroundWindow',
        'BringWindowToTop', 'BitBlt', 'GetDesktopWindow',
    }
    names = {
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    assert not names.intersection(prohibited)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or '').split('.')[0])
    assert not imports.intersection({'mss', 'subprocess', 'requests', 'adb'})


def test_capture_critical_section_is_serialized(monkeypatch):
    """阻塞首个模拟捕获时，第二次调用不能进入窗口操作。"""
    backend, env = environment(monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    results = []
    errors = []

    def blocking_print(*args):
        """暂时阻塞替身而不调用真实同步 Win32 接口。"""
        entered.set()
        assert release.wait(3)
        return 1

    def capture(second=False):
        """在测试线程中记录结果，避免异常静默丢失。"""
        if second:
            second_started.set()
        try:
            results.append(backend.capture_window(TITLE))
        except BaseException as error:
            errors.append(error)

    env.user32.PrintWindow.side_effect = blocking_print
    first = threading.Thread(target=capture)
    second = threading.Thread(target=capture, args=(True,))
    first.start()
    try:
        assert entered.wait(3)
        calls_before = env.gui.GetWindowPlacement.call_count
        second.start()
        assert second_started.wait(3)
        assert env.gui.GetWindowPlacement.call_count == calls_before
        assert env.user32.PrintWindow.call_count == 1
    finally:
        release.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert not errors and len(results) == 2


def test_window_destroyed_during_cleanup_is_not_success(monkeypatch):
    """释放 DC 期间目标被关闭时不能返回已失效目标的成功结果。"""
    backend, env = environment(monkeypatch)

    def release_then_destroy(handle, dc):
        """模拟清理过程中目标窗口被外部关闭。"""
        result = env.release_dc(handle, dc)
        env.alive = False
        return result

    env.user32.ReleaseDC.side_effect = release_then_destroy
    assert_error(backend, 'window_gone')
    assert env.events[-1] == ('dpi', OLD_DPI)


@pytest.mark.parametrize('main_failure', [False, True])
def test_dpi_restore_failure_preserves_image_or_primary_error(
        monkeypatch, main_failure):
    """DPI 还原失败不覆盖主异常，成功路径返回安全告警。"""
    backend, env = environment(monkeypatch)
    env.user32.SetThreadDpiAwarenessContext.side_effect = [OLD_DPI, 0]
    if main_failure:
        env.user32.PrintWindow.return_value = 0
        assert_error(backend, 'print_failed')
    else:
        result = backend.capture_window(TITLE)
        assert any('DPI' in warning for warning in result.warnings)
    assert env.user32.SetThreadDpiAwarenessContext.call_args.args == (OLD_DPI,)


@pytest.mark.parametrize('main_failure', [False, True])
def test_bitmap_cleanup_failure_releases_remaining_resources(
        monkeypatch, main_failure):
    """位图删除失败不阻断 DC 清理，也不覆盖捕获主因。"""
    backend, env = environment(monkeypatch)
    env.gdi32.DeleteObject.side_effect = OSError('secret-token')
    if main_failure:
        env.user32.PrintWindow.return_value = 0
        assert_error(backend, 'print_failed')
    else:
        result = backend.capture_window(TITLE)
        assert any('GDI' in warning for warning in result.warnings)
        assert 'secret-token' not in str(result.warnings)
    env.gdi32.DeleteDC.assert_called_once_with(MEMORY_DC)
    env.user32.ReleaseDC.assert_called_once_with(HWND, WINDOW_DC)


@pytest.mark.parametrize('signal', [KeyboardInterrupt, SystemExit])
def test_cleanup_signal_propagates_after_remaining_cleanup(
        monkeypatch, signal):
    """清理阶段收到控制信号后仍释放其余资源并还原窗口。"""
    backend, env = environment(monkeypatch)
    env.iconic = True
    env.gdi32.DeleteObject.side_effect = signal()
    with pytest.raises(signal):
        backend.capture_window(TITLE)
    env.gdi32.DeleteDC.assert_called_once_with(MEMORY_DC)
    env.user32.ReleaseDC.assert_called_once_with(HWND, WINDOW_DC)
    env.gui.SetWindowPlacement.assert_called_once_with(HWND, env.placement)
    assert env.events[-1] == ('dpi', OLD_DPI)


def test_old_bitmap_restore_failure_deletes_dc_before_bitmap(monkeypatch):
    """旧对象还原失败时先销毁内存 DC，再删除已脱离的位图。"""
    backend, env = environment(monkeypatch)

    def select_with_restore_failure(dc, bitmap):
        """仅模拟还原旧 bitmap 时返回 Win32 失败值。"""
        if bitmap == OLD_BITMAP:
            return 0
        return env.select_object(dc, bitmap)

    env.gdi32.SelectObject.side_effect = select_with_restore_failure
    result = backend.capture_window(TITLE)
    assert any('GDI' in warning for warning in result.warnings)
    assert env.events.index('delete_dc') < env.events.index('delete_bitmap')
    env.gdi32.DeleteDC.assert_called_once_with(MEMORY_DC)
    env.gdi32.DeleteObject.assert_called_once_with(BITMAP)


def test_window_backend_exports_capture_interface():
    """缺少窗口后端时明确失败，并核对公开接口导出。"""
    assert find_spec('modules.screenshot.window') is not None, (
        '尚未实现独立窗口截图后端')
    backend = import_module('modules.screenshot.window')
    package = import_module('modules.screenshot')
    assert callable(backend.capture_window)
    assert package.capture_window is backend.capture_window
    assert package.CaptureError is backend.CaptureError
