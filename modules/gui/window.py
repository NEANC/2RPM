#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""ctk-kit 无边框主窗的内嵌窗口操作栏。"""

import ctk_kit

from modules.gui.appearance import COLORS, FONT_SMALL


def wrap_header(root, header, titlebar):
    """窄窗将顶栏按钮移至标题下一行，宽窗保持原有单行布局。"""
    label, *buttons = header.winfo_children()
    compact = None

    def resize(event):
        """仅在跨越布局阈值时重排顶栏。"""
        nonlocal compact
        if event.widget is not root:
            return
        narrow = root.winfo_width() / root._get_window_scaling() < 700
        if (narrow == compact and (narrow or
                                  header.pack_info()['padx'] == (0, titlebar.winfo_width()))):
            return
        compact = narrow
        header.pack_configure(padx=0 if narrow else (0, titlebar.winfo_width()))
        label.pack_forget()
        for button in buttons:
            button.pack_forget()
        if narrow:
            label.pack(side='top', anchor='w', pady=(0, 4))
        else:
            label.pack(side='left')
        for button in buttons:
            button.pack(side='right', padx=4 if len(buttons) > 1 else 0)

    root.bind('<Configure>', resize, add='+')


class MainWindow:
    """仅负责主窗边框、拖动和窗口控制。"""

    def __init__(self, root, close):
        """为现有窗口添加透明拖动区域并复用关闭确认回调。"""
        self.root = root
        self._drag_origin = None
        self._restore_geometry = None
        root.overrideredirect(True)
        root.set_window_corners(1)
        root.bind('<Map>', self._restore_borderless, add='+')
        self.titlebar = ctk_kit.CTkFrame(
            root, corner_radius=0, width=111, height=34, fg_color=COLORS['side'])
        self.titlebar.place(relx=1, x=0, y=0, anchor='ne')
        self.close_button = ctk_kit.CTkButton(
            self.titlebar, text='×', width=37, height=34, corner_radius=0,
            fg_color='transparent', hover_color=('#C42B1C', '#C42B1C'),
            text_color=('#1A1A1A', '#F3F3F3'), command=close, font=FONT_SMALL)
        self.close_button.pack(side='right')
        self.maximize = ctk_kit.CTkButton(
            self.titlebar, text='□', width=37, height=34, corner_radius=0,
            fg_color='transparent', hover_color=('#E5E5E5', '#414141'),
            text_color=('#1A1A1A', '#F3F3F3'),
            command=self.toggle_maximize, font=FONT_SMALL)
        self.maximize.pack(side='right')
        self.minimize = ctk_kit.CTkButton(
            self.titlebar, text='—', width=37, height=34, corner_radius=0,
            fg_color='transparent', hover_color=('#E5E5E5', '#414141'),
            text_color=('#1A1A1A', '#F3F3F3'),
            command=self.iconify, font=FONT_SMALL)
        self.minimize.pack(side='right')

    def bind_brand(self, sidebar):
        """将侧栏品牌背景和文字作为窗口拖动区域。"""
        for widget in (sidebar._head, sidebar._title_box,
                       sidebar.title_label, sidebar.subtitle_label):
            widget.bind('<ButtonPress-1>', self.start_drag)
            widget.bind('<B1-Motion>', self.drag)

    def start_drag(self, event):
        """记录透明区域按下时的指针及窗口屏幕坐标。"""
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
        self._restore_geometry = self.root.geometry()
        self.root.overrideredirect(False)
        self.root.iconify()

    def _restore_borderless(self, event):
        """从任务栏恢复时重新隐藏系统标题栏。"""
        if event.widget is not self.root or self._restore_geometry is None:
            return
        self.root.overrideredirect(True)
        if self.root.state() != 'normal':
            return
        geometry = self._restore_geometry
        self._restore_geometry = None
        if hasattr(self.root, '_gui_work_size'):
            size, position = geometry.split('+', 1)
            width, height = map(int, size.split('x'))
            available_width, available_height = self.root._gui_work_size
            geometry = (f'{min(width, available_width)}x'
                        f'{min(height, available_height)}+{position}')
        self.root.geometry(geometry)
