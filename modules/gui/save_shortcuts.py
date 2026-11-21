#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""主窗口级 Ctrl+S 快捷键：窗口归属、模态守卫与按下锁。"""

import ctypes
import sys
import tkinter as tk
from typing import Callable

VK_CONTROL = 0x11
VK_S = 0x53
SAVE_KEYSYMS = frozenset(('s', 'S'))
PRESS_KEYSYMS = frozenset(('s', 'S'))


def key_is_down(virtual_key: int) -> bool:
    """返回虚拟键此刻是否被物理按下，非 Windows 或读取失败按未按下处理。

    Args:
        virtual_key: Win32 虚拟键码。

    Returns:
        键处于按下状态时为 True。
    """
    if sys.platform != 'win32':
        return False
    try:
        state = ctypes.windll.user32.GetAsyncKeyState(virtual_key)
    except (AttributeError, OSError):
        return False
    return bool(state & 0x8000)


def save_key_down() -> bool:
    """Ctrl 与 S 是否同时被物理按住，用于区分自动重复与真实松开。"""
    return key_is_down(VK_CONTROL) and key_is_down(VK_S)


class SaveShortcuts:
    """在主窗 toplevel 上消费 Ctrl+S，一次按下至释放只保存一次。"""

    PRESS_SEQUENCES = ('<Control-KeyPress-s>', '<Control-KeyPress-S>')
    RELEASE_SEQUENCES = ('<Control-KeyRelease-s>', '<Control-KeyRelease-S>')
    FOCUS_SEQUENCES = ('<FocusIn>', '<FocusOut>')

    def __init__(self, root, save: Callable[[], None]):
        """绑定主窗按键与焦点事件，保存入口由调用方注入。

        Args:
            root: 主窗 toplevel（``tk.Tk`` 或 ``ctk_kit.App``）。
            save: 与保存按钮共用的保存入口。
        """
        self.root = root
        self.save = save
        self._held = False
        self._bindings = []
        for sequence in self.PRESS_SEQUENCES:
            self._bindings.append(
                (sequence, root.bind(sequence, self._on_press, add='+')))
        for sequence in self.RELEASE_SEQUENCES:
            self._bindings.append(
                (sequence, root.bind(sequence, self._on_release, add='+')))
        for sequence in self.FOCUS_SEQUENCES:
            self._bindings.append(
                (sequence, root.bind(sequence, self._on_focus, add='+')))

    def _owns(self, event) -> bool:
        """只接受主窗 toplevel 内的事件，模态 grab 期间忽略新的保存请求。"""
        widget = getattr(event, 'widget', None)
        if widget is None:
            return False
        try:
            if widget.winfo_toplevel() is not self.root:
                return False
            grab = self.root.grab_current()
        except tk.TclError:
            return False
        return grab is None

    def _on_press(self, event):
        """锁住本次按下，自动重复事件被拦截并返回事件终止标志。"""
        if not self._owns(event):
            return None
        if event.keysym not in PRESS_KEYSYMS:
            return None
        if self._held:
            return 'break'
        self._held = True
        self.save()
        return 'break'

    def _on_release(self, event):
        """看到 s/S 的释放即清锁，不消费其他控件的释放行为。"""
        if not self._owns(event):
            return None
        if event.keysym in SAVE_KEYSYMS:
            self._held = False
        return None

    def _on_focus(self, event):
        """焦点变化时按物理键状态清锁，避免锁永久残留或仍按住时重触发。"""
        if not self._owns(event):
            return None
        if not save_key_down():
            self._held = False
        return None

    def close(self) -> None:
        """只解除本对象注册的处理，保留其他绑定。"""
        for sequence, funcid in self._bindings:
            try:
                self.root.unbind(sequence, funcid)
            except tk.TclError:
                continue
        self._bindings = []
        self._held = False
