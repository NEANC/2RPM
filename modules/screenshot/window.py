#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""使用同步 PrintWindow 捕获完整窗口，不提供桌面回退或硬超时。

最小化窗口会临时恢复，可能闪烁或影响焦点。图像告警不能识别全部旧帧，
硬件加速窗口的兼容性需另行实机验证。
"""

import ctypes
from ctypes import wintypes
from io import BytesIO
import threading
import time

from PIL import Image
from PIL import ImageStat
import win32con
import win32gui

from .models import CaptureError
from .models import CaptureResult


PW_RENDERFULLCONTENT = 0x2
_REPAINT_WAIT_SECONDS = 0.2
_DPI_PER_MONITOR_AWARE = -3
_CAPTURE_LOCK = threading.Lock()
_MESSAGES = {
    'invalid_title': '窗口标题必须为非空字符串',
    'window_not_found': '未找到完整标题匹配的窗口',
    'window_ambiguous': '存在多个完整标题匹配的窗口',
    'window_lookup_failed': '无法枚举目标窗口',
    'window_gone': '目标窗口已失效或标题已变化',
    'window_state_failed': '无法读取或恢复目标窗口状态',
    'invalid_dimensions': '目标窗口尺寸无效',
    'dpi_failed': '无法设置局部线程 DPI 上下文',
    'gdi_failed': '无法分配或访问窗口图像资源',
    'print_failed': '窗口图像绘制失败',
    'image_failed': '无法构造有效窗口图像',
    'encode_failed': '窗口图像 PNG 编码或校验失败',
}
_GDI_WARNING = '部分截图 GDI 资源未能正常释放'
_STATE_WARNING = '未能还原目标窗口的原最小化状态'
_DPI_WARNING = '未能还原原线程 DPI 上下文'


class _BitmapInfoHeader(ctypes.Structure):
    """保存 Win32 的 40 字节 BITMAPINFOHEADER。"""

    _fields_ = [
        ('biSize', wintypes.DWORD),
        ('biWidth', wintypes.LONG),
        ('biHeight', wintypes.LONG),
        ('biPlanes', wintypes.WORD),
        ('biBitCount', wintypes.WORD),
        ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD),
        ('biXPelsPerMeter', wintypes.LONG),
        ('biYPelsPerMeter', wintypes.LONG),
        ('biClrUsed', wintypes.DWORD),
        ('biClrImportant', wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    """包含未压缩 32 位 DIB 的头和一个保留 RGBQUAD。"""

    _fields_ = [
        ('bmiHeader', _BitmapInfoHeader),
        ('bmiColors', wintypes.DWORD * 1),
    ]


_user32 = ctypes.WinDLL('user32', use_last_error=True)
_gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_user32.PrintWindow.restype = wintypes.BOOL
_user32.GetWindowDC.argtypes = [wintypes.HWND]
_user32.GetWindowDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = ctypes.c_int
_user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(_BitmapInfo), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
_gdi32.SelectObject.restype = wintypes.HANDLE
_gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.GdiFlush.argtypes = []
_gdi32.GdiFlush.restype = wintypes.BOOL


def _error(code):
    """构造固定安全消息，不将底层异常内容带入业务错误。"""
    return CaptureError(code, _MESSAGES[code])


def _ensure_window(hwnd, title):
    """在操作前校验本次选中的句柄和标题，不重新选择其他窗口。"""
    try:
        valid = bool(hwnd) and win32gui.IsWindow(hwnd)
        if valid:
            valid = win32gui.GetWindowText(hwnd) == title
    except Exception:
        raise _error('window_gone') from None
    if not valid:
        raise _error('window_gone')


def _find_window(title):
    """枚举全部顶层窗口并要求完整标题唯一，不过滤最小化窗口。"""
    matches = []

    def collect(hwnd, argument):
        """收集当前仍有效且完整标题相同的顶层窗口。"""
        if win32gui.IsWindow(hwnd) and win32gui.GetWindowText(hwnd) == title:
            matches.append(hwnd)
        return True

    try:
        win32gui.EnumWindows(collect, None)
    except Exception:
        raise _error('window_lookup_failed') from None
    if not matches:
        raise _error('window_not_found')
    if len(matches) != 1:
        raise _error('window_ambiguous')
    _ensure_window(matches[0], title)
    return matches[0]


def _release_gdi(hwnd, window_dc, memory_dc, bitmap, old_bitmap, warnings):
    """恢复旧对象并释放自有资源，部分失败仍清理其余资源。"""
    signals = []

    def release(function, *args):
        """执行一次清理，记录故障并在全部清理后重新传播控制信号。"""
        try:
            if function(*args):
                return True
        except Exception:
            pass
        except BaseException as signal:
            signals.append(signal)
        if _GDI_WARNING not in warnings:
            warnings.append(_GDI_WARNING)
        return False

    deselected = not old_bitmap
    if old_bitmap:
        deselected = release(_gdi32.SelectObject, memory_dc, old_bitmap)
    if not deselected and memory_dc:
        # 恢复旧对象失败时先销毁 DC，不能删除仍被选中的位图。
        deselected = release(_gdi32.DeleteDC, memory_dc)
        memory_dc = None
    if bitmap and deselected:
        release(_gdi32.DeleteObject, bitmap)
    if memory_dc:
        release(_gdi32.DeleteDC, memory_dc)
    if window_dc:
        release(_user32.ReleaseDC, hwnd, window_dc)
    if signals:
        raise signals[0]


def _encode_png(pixels, width, height, warnings):
    """将顶向下 BGRX 像素编码为内存 PNG，并实际解码校验尺寸。"""
    try:
        image = Image.frombytes(
            'RGB', (width, height), pixels, 'raw', 'BGRX', width * 4, 1)
    except Exception:
        raise _error('image_failed') from None
    with image:
        if image.size != (width, height):
            raise _error('image_failed')
        try:
            if max(ImageStat.Stat(image).var) < 1.0:
                warnings.append('图像为纯色或低方差，可能未正确渲染或未更新')
            with BytesIO() as output:
                image.save(output, format='PNG')
                encoded = output.getvalue()
            with Image.open(BytesIO(encoded), formats=['PNG']) as decoded:
                decoded.load()
                if decoded.size != (width, height):
                    raise _error('encode_failed')
            return encoded
        except CaptureError:
            raise
        except Exception:
            raise _error('encode_failed') from None


def _capture_bitmap(hwnd, title, warnings):
    """使用完整窗口 DC 和固定 32 位 DIB 执行唯一 PrintWindow 路径。"""
    window_dc = memory_dc = bitmap = old_bitmap = None
    stage = 'window_state_failed'
    try:
        _ensure_window(hwnd, title)
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width, height = right - left, bottom - top
        if not (0 < width <= 0x7fffffff and 0 < height <= 0x7fffffff):
            raise _error('invalid_dimensions')
        if width * height > Image.MAX_IMAGE_PIXELS:
            raise _error('invalid_dimensions')
        stage = 'gdi_failed'
        _ensure_window(hwnd, title)
        window_dc = _user32.GetWindowDC(hwnd)
        if not window_dc:
            raise _error(stage)
        memory_dc = _gdi32.CreateCompatibleDC(window_dc)
        if not memory_dc:
            raise _error(stage)
        info = _BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = win32con.BI_RGB
        bits = ctypes.c_void_p()
        bitmap = _gdi32.CreateDIBSection(
            window_dc, ctypes.byref(info), win32con.DIB_RGB_COLORS,
            ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise _error(stage)
        old_bitmap = _gdi32.SelectObject(memory_dc, bitmap)
        if not old_bitmap:
            raise _error(stage)
        stage = 'print_failed'
        _ensure_window(hwnd, title)
        printed = _user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT)
        _ensure_window(hwnd, title)
        if not printed:
            raise _error(stage)
        stage = 'gdi_failed'
        if not _gdi32.GdiFlush():
            raise _error(stage)
        stage = 'image_failed'
        pixels = ctypes.string_at(bits, width * height * 4)
        encoded = _encode_png(pixels, width, height, warnings)
        _ensure_window(hwnd, title)
        return encoded, width, height
    except CaptureError:
        raise
    except Exception:
        raise _error(stage) from None
    finally:
        _release_gdi(
            hwnd, window_dc, memory_dc, bitmap, old_bitmap, warnings)


def capture_window(title: str) -> CaptureResult:
    """按唯一完整标题捕获窗口，返回内存 PNG，与监控进程 PID 无关。

    Args:
        title: 非空完整窗口标题；不剥离有效标题两端的空格。

    Returns:
        包含 PNG、原始标题、尺寸和安全告警的不可变结果。

    Raises:
        CaptureError: 标题、窗口状态、DPI、GDI、绘制或编码失败。

    同步 PrintWindow 无可强制终止的线程超时。等待重绘仅为有限延迟，
    不是新帧保证；目标未响应时不承诺绝对硬截止。临时恢复可能影响焦点。
    """
    if not isinstance(title, str) or not title.strip():
        raise _error('invalid_title')
    with _CAPTURE_LOCK:
        hwnd = _find_window(title)
        warnings = []
        try:
            old_dpi = _user32.SetThreadDpiAwarenessContext(
                _DPI_PER_MONITOR_AWARE)
        except Exception:
            raise _error('dpi_failed') from None
        if not old_dpi:
            raise _error('dpi_failed')
        restore_needed = False
        placement = None
        try:
            _ensure_window(hwnd, title)
            placement = win32gui.GetWindowPlacement(hwnd)
            _ensure_window(hwnd, title)
            if win32gui.IsIconic(hwnd):
                restore_needed = True
                _ensure_window(hwnd, title)
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(_REPAINT_WAIT_SECONDS)
                _ensure_window(hwnd, title)
                if win32gui.IsIconic(hwnd):
                    raise _error('window_state_failed')
            encoded, width, height = _capture_bitmap(hwnd, title, warnings)
        except CaptureError:
            raise
        except Exception:
            raise _error('window_state_failed') from None
        finally:
            try:
                if restore_needed:
                    try:
                        _ensure_window(hwnd, title)
                        win32gui.SetWindowPlacement(hwnd, placement)
                        _ensure_window(hwnd, title)
                        if not win32gui.IsIconic(hwnd):
                            warnings.append(_STATE_WARNING)
                    except Exception:
                        warnings.append(_STATE_WARNING)
            finally:
                try:
                    if not _user32.SetThreadDpiAwarenessContext(old_dpi):
                        warnings.append(_DPI_WARNING)
                except Exception:
                    warnings.append(_DPI_WARNING)
        _ensure_window(hwnd, title)
        return CaptureResult(
            encoded, 'window', title, width, height, tuple(warnings))
