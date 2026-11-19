#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""截图／上传选择和结果弹窗行为测试。"""

import tkinter as tk
from unittest.mock import Mock

import ctk_kit
import customtkinter as ctk
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


def test_capture_start_button_tracks_source_and_target():
    """真实截图选择窗仅在来源和目标齐备时允许开始。"""
    root = tk.Tk()
    root.geometry('500x400')
    root.update()
    dialogs = GuiDialogs(root, GuiSession(Mock(active=False)))
    callback = Mock()
    try:
        dialogs.open_capture(callback)
        root.update()
        assert dialogs.capture_start.cget('state') == 'disabled'
        dialogs.capture_target._entry.focus_force()
        root.update()
        dialogs.capture_target.insert(0, '窗口标题')
        dialogs.capture_target._entry.event_generate('<KeyRelease>')
        assert dialogs.capture_start.cget('state') == 'normal'
        dialogs.capture_provider._dropdown_callback('')
        assert dialogs.capture_start.cget('state') == 'disabled'
        dialogs.capture_provider._dropdown_callback('ADB')
        assert dialogs.capture_start.cget('state') == 'normal'
        dialogs.capture_target.delete(0, 'end')
        dialogs.capture_target._entry.event_generate('<KeyRelease>')
        assert dialogs.capture_start.cget('state') == 'disabled'
        dialogs.selection_window.destroy()
        callback.assert_not_called()
    finally:
        dialogs.clear()
        root.destroy()


def test_capture_start_disables_after_programmatic_target_clear():
    """程序清空截图目标时无需键盘事件也会禁用开始按钮。"""
    root = tk.Tk()
    root.withdraw()
    dialogs = GuiDialogs(root, GuiSession(Mock(active=False)))
    callback = Mock()
    try:
        dialogs.open_capture(callback)
        dialogs.capture_target.insert(0, '窗口标题')
        dialogs.capture_provider._dropdown_callback('窗口')
        assert dialogs.capture_start.cget('state') == 'normal'
        dialogs.capture_target.delete(0, 'end')
        assert dialogs.capture_start.cget('state') == 'disabled'
        dialogs.capture_provider._dropdown_callback('ADB')
        assert dialogs.capture_provider.get() == 'ADB'
        assert dialogs.capture_start.cget('state') == 'disabled'
        dialogs.capture_target.insert(0, '127.0.0.1:16384')
        assert dialogs.capture_start.cget('state') == 'normal'
        dialogs.selection_window.destroy()
        callback.assert_not_called()
    finally:
        dialogs.clear()
        root.destroy()


def test_capture_entry_clear_disables_start_and_cancel_does_not_request():
    """真实输入框 clear 后立即禁用，点击取消不创建请求。"""
    root = tk.Tk()
    root.withdraw()
    dialogs = GuiDialogs(root, GuiSession(Mock(active=False)))
    callback = Mock()
    try:
        window = dialogs.open_capture(callback)
        assert isinstance(dialogs.capture_target, ctk_kit.CTkEntry)
        dialogs.capture_target.insert(0, '窗口标题')
        assert dialogs.capture_start.cget('state') == 'normal'
        dialogs.capture_target.clear()
        assert dialogs.capture_target.get() == ''
        assert dialogs.capture_start.cget('state') == 'disabled'
        cancel = next(child for child in window.winfo_children()
                      if isinstance(child, ctk_kit.CTkButton) and child.cget('text') == '取消')
        cancel.invoke()
        assert not window.winfo_exists()
        callback.assert_not_called()
    finally:
        dialogs.clear()
        root.destroy()


def test_capture_context_menu_clear_disables_start_and_cancel_does_not_request():
    """真实右键菜单点击清空后立即禁用，点击取消不创建请求。"""
    root = tk.Tk()
    root.geometry('500x400')
    root.update()
    dialogs = GuiDialogs(root, GuiSession(Mock(active=False)))
    callback = Mock()
    try:
        window = dialogs.open_capture(callback)
        root.update()
        dialogs.capture_target.insert(0, '窗口标题')
        assert dialogs.capture_start.cget('state') == 'normal'
        dialogs.capture_target._entry.event_generate('<Button-3>', x=5, y=5)
        menu = dialogs.capture_target._ctk_kit_edit_menu
        assert menu._popup is not None
        inner = menu._popup.winfo_children()[0].winfo_children()[0]
        clear = next(child for child in inner.winfo_children()
                     if isinstance(child, ctk.CTkButton) and child.cget('text') == '清空')
        assert clear.cget('state') == 'normal'
        clear.invoke()
        assert dialogs.capture_target.get() == ''
        assert dialogs.capture_start.cget('state') == 'disabled'
        cancel = next(child for child in window.winfo_children()
                      if isinstance(child, ctk_kit.CTkButton) and child.cget('text') == '取消')
        cancel.invoke()
        assert not window.winfo_exists()
        callback.assert_not_called()
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
        dialogs.upload_image.set('C:/image.jpg')
        dialogs.submit_upload()
        callback.assert_called_once_with(1, 'C:/image.jpg')
    finally:
        dialogs.clear()
        root.destroy()


def test_upload_start_button_tracks_host_and_image():
    """真实上传选择窗在图床及图片选择变化时即时更新。"""
    root = tk.Tk()
    root.geometry('500x400')
    root.update()
    dialogs = GuiDialogs(root, GuiSession(Mock(active=False)))
    callback = Mock()
    try:
        dialogs.open_upload(['beeimg'], callback)
        root.update()
        assert dialogs.upload_start.cget('state') == 'disabled'
        dialogs.upload_image.set('C:/image.jpg')
        assert dialogs.upload_start.cget('state') == 'disabled'
        dialogs.upload_host._dropdown_callback('01 beeimg')
        assert dialogs.upload_start.cget('state') == 'normal'
        dialogs.upload_image.clear()
        assert dialogs.upload_start.cget('state') == 'disabled'
        dialogs.upload_image.entry._entry.focus_force()
        root.update()
        dialogs.upload_image.entry.insert(0, 'C:/another.png')
        dialogs.upload_image.entry._entry.event_generate('<KeyRelease>')
        assert dialogs.upload_start.cget('state') == 'normal'
        dialogs.upload_host._dropdown_callback('请选择图床')
        assert dialogs.upload_start.cget('state') == 'disabled'
        dialogs.selection_window.destroy()
        callback.assert_not_called()
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
