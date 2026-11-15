#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""截图／上传选择和结果弹窗行为测试。"""

import tkinter as tk
from unittest.mock import Mock

from PIL import Image

from modules.gui.protocol import TestResult as GuiResult
from modules.gui.test_dialogs import TestDialogs as GuiDialogs
from modules.gui.test_session import TestSession as GuiSession


def test_hide_and_reopen_result_keeps_same_window():
    """关闭仅隐藏；重新打开沿用同一个结果窗口。"""
    root = tk.Tk()
    root.withdraw()
    try:
        dialogs = GuiDialogs(root, GuiSession(Mock(active=False)))
        dialogs.show_result('capture', None)
        window = dialogs.result_window
        dialogs.hide_result()
        assert not window.winfo_viewable()
        dialogs.show_result('capture', GuiResult('one', 0, 0, 'failure', '测试失败', ()))
        assert dialogs.result_window is window
        assert window.winfo_exists()
        assert dialogs.retry.cget('text') == '重新测试'
    finally:
        dialogs.clear()
        root.destroy()


def test_capture_selection_requires_source_and_target():
    """截图手填来源与目标缺一不可，取消不发请求。"""
    root = tk.Tk()
    root.withdraw()
    try:
        callback = Mock()
        dialogs = GuiDialogs(root, GuiSession(Mock(active=False)))
        dialogs.open_capture(callback)
        dialogs.capture_target.insert(0, '窗口')
        dialogs.capture_provider.set('')
        dialogs.submit_capture()
        callback.assert_not_called()
        dialogs.capture_provider.set('窗口')
        dialogs.submit_capture()
        callback.assert_called_once_with('window', '窗口')
    finally:
        dialogs.clear()
        root.destroy()


def test_upload_selection_requires_host_and_image():
    """图床及本地图片都选定后才可提交。"""
    root = tk.Tk()
    root.withdraw()
    try:
        callback = Mock()
        dialogs = GuiDialogs(root, GuiSession(Mock(active=False)))
        dialogs.open_upload(['beeimg', 'catbox'], callback)
        dialogs.submit_upload()
        callback.assert_not_called()
        dialogs.upload_host.set('02 catbox')
        dialogs.upload_image.insert(0, 'C:/image.jpg')
        dialogs.submit_upload()
        callback.assert_called_once_with(1, 'C:/image.jpg')
    finally:
        dialogs.clear()
        root.destroy()


def test_retry_returns_to_selection():
    """重新测试恢复选择窗口，而不自动重新提交旧参数。"""
    root = tk.Tk()
    root.withdraw()
    try:
        callback = Mock()
        dialogs = GuiDialogs(root, GuiSession(Mock(active=False)))
        dialogs.open_capture(callback)
        dialogs.selection_window.destroy()
        dialogs.show_result('capture', GuiResult('one', 0, 0, 'success', '', ()))
        dialogs.retry.invoke()
        assert dialogs.selection_window.winfo_exists()
        callback.assert_not_called()
    finally:
        dialogs.clear()
        root.destroy()


def test_capture_result_displays_real_image(tmp_path):
    """成功截图使用本地图片像素作为结果，不仅显示文件名。"""
    path = tmp_path / 'capture.png'
    Image.new('RGB', (16, 12), 'blue').save(path)
    root = tk.Tk()
    root.withdraw()
    try:
        dialogs = GuiDialogs(root, GuiSession(Mock(active=False)))
        dialogs.show_result('capture', GuiResult('one', 0, 0, 'success', '', (str(path),)))
        assert dialogs.result_image is not None
        assert dialogs.result_image.cget('size') == (16, 12)
    finally:
        dialogs.clear()
        root.destroy()


def test_upload_result_displays_real_callback_address():
    """上传地址来自结果协议，不使用示例占位。"""
    root = tk.Tk()
    root.withdraw()
    try:
        dialogs = GuiDialogs(root, GuiSession(Mock(active=False)))
        dialogs.show_result('upload', GuiResult('one', 0, 0, 'success', '', (),
                                                'https://example.org/real.png'))
        assert 'https://example.org/real.png' in dialogs.result_label.cget('text')
    finally:
        dialogs.clear()
        root.destroy()
