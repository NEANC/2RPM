#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 可滚动表单和独立折叠区域。"""

import tkinter as tk
from tkinter import ttk


class ValueBinding:
    """让会触发回调的控件在程序刷新时不写回模型。"""

    def __init__(self, widget, setter, path):
        """绑定控件、模型写入函数和字段路径。"""
        self.widget = widget
        self.setter = setter
        self.path = path
        self.refreshing = False

    def changed(self, value):
        """仅用户编辑时写回草稿。"""
        if not self.refreshing:
            self.setter(self.path, value)

    def refresh(self, value):
        """同步控件显示，抑制同步触发的回调。"""
        self.refreshing = True
        try:
            self.widget.set_value(value)
        finally:
            self.refreshing = False


class CollapsibleSection(ttk.Frame):
    """隐藏内容但保持字段及模型值不变。"""

    def __init__(self, parent, title: str, *, expanded: bool = False):
        """创建可单独控制的高级区域。"""
        super().__init__(parent)
        self.title = title
        self.expanded = not expanded
        self.button = ttk.Button(self, command=self.toggle)
        self.button.pack(fill='x')
        self.body = ttk.Frame(self)
        self.set_expanded(expanded)

    def toggle(self) -> None:
        """只改变控件可见性。"""
        self.set_expanded(not self.expanded)

    def set_expanded(self, expanded: bool) -> None:
        """显式展开或折叠内容。"""
        self.expanded = expanded
        self.button.configure(text=('▼ ' if expanded else '▶ ') + self.title)
        if expanded:
            self.body.pack(fill='x', expand=True)
        else:
            self.body.pack_forget()


class ScrollForm(ttk.Frame):
    """随窗口缩放的滚动页面容器。"""

    def __init__(self, parent):
        """以 Canvas 承载高度不限的 ttk 表单。"""
        super().__init__(parent)
        canvas = tk.Canvas(self, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        self.body = ttk.Frame(canvas, padding=12)
        window = canvas.create_window((0, 0), window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda _: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window, width=event.width))
        self.canvas = canvas
