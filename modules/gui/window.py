#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""页面顶部动作的窄窗换行。"""


def wrap_header(root, header, titlebar=None, *, reserve=False, gap=10):
    """窄窗把动作按钮移到标题下一行，宽窗恢复单行。

    窗口使用系统标题栏后，页头不再为窗口按钮预留右侧宽度；``reserve``
    仅为兼容旧调用保留，需要时仍可按标题栏宽度留白。
    """
    label, *buttons = header.winfo_children()
    compact = None

    def layout(narrow):
        """按窄/宽两种状态重排标题与动作按钮。"""
        label.pack_forget()
        for button in buttons:
            button.pack_forget()
        if narrow:
            label.pack(side='top', anchor='w', pady=(0, 6))
        else:
            label.pack(side='left')
        for button in buttons:
            button.pack(side='right', padx=(gap, 0))
        if reserve and titlebar is not None:
            header.pack_configure(padx=0 if narrow else (0, titlebar.winfo_width()))

    def resize(event):
        """仅在跨越布局阈值时重排顶栏。"""
        nonlocal compact
        if event.widget is not root:
            return
        narrow = root.winfo_width() / root._get_window_scaling() < 700
        if narrow == compact:
            return
        compact = narrow
        layout(narrow)

    root.bind('<Configure>', resize, add='+')
