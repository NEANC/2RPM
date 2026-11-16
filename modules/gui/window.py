#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""ctk-kit 无边框主窗的内嵌窗口操作栏。"""

import ctk_kit

from modules.gui.appearance import FONT_BODY


class MainWindow:
    """仅负责主窗边框、拖动和窗口控制。"""

    def __init__(self, root, close):
        """为现有窗口添加方角标题栏并复用关闭确认回调。"""
        self.root = root
        self._drag_origin = None
        root.overrideredirect(True)
        root.set_window_corners(1)
        self.titlebar = ctk_kit.CTkFrame(root, corner_radius=0, height=38)
        self.titlebar.pack(fill='x')
        self.titlebar.pack_propagate(False)
        title = ctk_kit.CTkLabel(self.titlebar, text='2RPM 配置', font=FONT_BODY)
        title.pack(side='left', padx=12)
        self.close_button = ctk_kit.CTkButton(
            self.titlebar, text='×', width=44, height=32, corner_radius=0,
            command=close, font=FONT_BODY)
        self.close_button.pack(side='right')
        self.maximize = ctk_kit.CTkButton(
            self.titlebar, text='□', width=44, height=32, corner_radius=0,
            command=self.toggle_maximize, font=FONT_BODY)
        self.maximize.pack(side='right')
        self.minimize = ctk_kit.CTkButton(
            self.titlebar, text='—', width=44, height=32, corner_radius=0,
            command=self.iconify, font=FONT_BODY)
        self.minimize.pack(side='right')
        for widget in (self.titlebar, title):
            widget.bind('<ButtonPress-1>', self.start_drag)
            widget.bind('<B1-Motion>', self.drag)

    def start_drag(self, event):
        """记录标题栏按下时的指针及窗口屏幕坐标。"""
        self._drag_origin = (event.x_root, event.y_root,
                             self.root.winfo_x(), self.root.winfo_y())

    def drag(self, event):
        """按指针位移平移窗口，最大化时不拖动。"""
        if self._drag_origin is None or self.root.state() == 'zoomed':
            return
        x, y, left, top = self._drag_origin
        self.root.geometry(f'+{left + event.x_root - x}+{top + event.y_root - y}')

    def toggle_maximize(self):
        """在普通窗口与最大化窗口之间切换。"""
        self.root.state('normal' if self.root.state() == 'zoomed' else 'zoomed')

    def iconify(self):
        """暂时撤去无边框标志以允许 Tk 最小化，恢复后重设标志。"""
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.bind('<Map>', self._restore_borderless, add='+')

    def _restore_borderless(self, _event):
        """从任务栏恢复时重新隐藏系统标题栏。"""
        self.root.overrideredirect(True)
