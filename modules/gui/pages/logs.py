#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""ctk-kit 日志位置与保留策略。"""

import tkinter as tk
from tkinter import filedialog

import ctk_kit

from modules.gui.appearance import FONT_BODY, FONT_CARD_TITLE, FONT_SMALL, FONT_TITLE
from modules.gui.widgets import scroll_to_widget


class Page(ctk_kit.CTkScrollableFrame):
    """只修改配置，不读取或创建日志文件。"""

    def __init__(self, parent, app):
        """建立目录输入和两项日志保留数值。"""
        super().__init__(parent, corner_radius=0)
        self.model = app.model
        self.fields = {}
        self._refreshing = False
        header = ctk_kit.CTkFrame(self, fg_color='transparent')
        header.pack(fill='x', pady=(0, 12))
        ctk_kit.CTkLabel(header, text='日志设置', font=FONT_TITLE).pack(side='left')
        ctk_kit.CTkButton(header, text='打开日志目录', font=FONT_BODY,
                          command=app.open_logs).pack(side='right')
        card = self._card('保存位置')
        row = ctk_kit.CTkFrame(card, fg_color='transparent')
        row.pack(fill='x', padx=16, pady=8)
        self._entry(row, ('log', 'log_directory'))
        ctk_kit.CTkButton(row, text='⌕', width=32, command=self.choose_directory).pack(side='left')
        ctk_kit.CTkButton(row, text='×', width=32, command=self.clear_directory).pack(side='left')
        ctk_kit.CTkLabel(card, text='相对路径以程序目录为起点。',
                         font=FONT_SMALL).pack(anchor='w', padx=16, pady=(0, 16))
        card = self._card('保留策略')
        for title, key in (('最大日志数', 'max_log_files'), ('保留天数', 'retention_days')):
            row = ctk_kit.CTkFrame(card, fg_color='transparent')
            row.pack(fill='x', padx=16, pady=8)
            ctk_kit.CTkLabel(row, text=title, font=FONT_BODY, width=110,
                             anchor='w').pack(side='left')
            path = ('log', key)
            self._entry(row, path, numeric=True)
            for delta, text in ((1, '▲'), (-1, '▼')):
                ctk_kit.CTkButton(row, text=text, width=28,
                                  command=lambda step=delta, field=path: self._step(field, step)).pack(side='left')
        self.refresh()

    def _card(self, title):
        """绘制无分隔线的卡片。"""
        card = ctk_kit.CTkFrame(self, corner_radius=12)
        card.pack(fill='x', pady=8)
        ctk_kit.CTkLabel(card, text=title, font=FONT_CARD_TITLE).pack(anchor='w', padx=16, pady=(12, 4))
        return card

    def _entry(self, parent, path, *, numeric=False):
        """数值输入保留尚未完成的原始文本。"""
        variable = tk.StringVar(master=self)
        entry = ctk_kit.CTkEntry(parent, textvariable=variable, font=FONT_BODY,
                                 width=80 if numeric else 300)
        entry.pack(side='left', fill='x', expand=not numeric, padx=8)
        variable.trace_add('write', lambda *_: self._changed(path, variable, numeric))
        self.fields[path] = (variable, entry, 'int' if numeric else 'text')

    def _changed(self, path, variable, numeric):
        """只响应主动编辑而不响应刷新。"""
        if not self._refreshing:
            if numeric:
                self.model.set_input(path, variable.get(), kind='int')
            else:
                self.model.set(path, variable.get())

    def _step(self, path, delta):
        """步进已解析的数值，未完成原文不被覆盖。"""
        raw = self.model.get_input(path)
        if raw.lstrip('+-').isdigit():
            self.fields[path][0].set(str(int(raw) + delta))

    def choose_directory(self):
        """选择目录只更新草稿。"""
        selected = filedialog.askdirectory(parent=self)
        if selected:
            self.fields[('log', 'log_directory')][0].set(selected)

    def clear_directory(self):
        """仅清空草稿目录路径。"""
        self.fields[('log', 'log_directory')][0].set('')

    def _safe_value(self, path, kind):
        """读取可能缺失的字段，结构异常时显示空文本。"""
        try:
            return (self.model.get_input(path) if kind == 'int'
                    else str(self.model.get(path) or ''))
        except (KeyError, IndexError, TypeError):
            return ''

    def refresh(self):
        """从当前文档同步所有可见字段。"""
        self._refreshing = True
        try:
            for path, (variable, _entry, kind) in self.fields.items():
                variable.set(self._safe_value(path, kind))
        finally:
            self._refreshing = False

    def focus_issue(self, path):
        """定位日志配置中的对应输入。"""
        field = self.fields.get(tuple(path))
        if field:
            scroll_to_widget(self, field[1])
            field[1].focus_force()
