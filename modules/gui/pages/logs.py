#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""ctk-kit 日志位置与保留策略。"""

from collections.abc import Mapping
import re
import tkinter as tk
from tkinter import filedialog

import ctk_kit

from modules.config import DEFAULT_VALUES
from modules.gui.appearance import (COLORS, FONT_BODY, FONT_CARD_TITLE,
                                    FONT_SMALL, FONT_TITLE)
from modules.gui.widgets import STRUCTURE_HINT, path_writable, scroll_to_widget


DIRECTORY = ('log', 'log_directory')
NUMBER_RANGES = {
    ('log', 'max_log_files'): (1, 9999),
    ('log', 'retention_days'): (1, 3650),
}

# 与主页保持同一套原型比例：圆角 20、2px 边框、28px 页边距与卡片内边距。
PAGE_PAD = 28
CARD_GAP = 32
CARD_PAD = 28
CARD_RADIUS = 20
CARD_BORDER = 2
CARD_SURFACE = ('#FAFAFA', '#232323')
CARD_BORDER_COLOR = ('#E1E1E1', '#3A3A3A')


class Page(ctk_kit.CTkScrollableFrame):
    """只修改配置，不读取或创建日志文件。"""

    def __init__(self, parent, app):
        """建立目录输入和两项日志保留数值。"""
        super().__init__(parent, corner_radius=0)
        self.app = app
        self.model = app.model
        self._dark = int(ctk_kit.get_appearance_mode() == 'Dark')
        self.fields = {}
        self.path_fields = {}
        self.spinners = {}
        self.number_variables = {}
        self._refreshing = False
        header = ctk_kit.CTkFrame(self, fg_color='transparent')
        header.pack(fill='x', padx=PAGE_PAD, pady=(PAGE_PAD, 0))
        ctk_kit.CTkLabel(header, text='日志设置', font=FONT_TITLE).pack(side='left')
        ctk_kit.CTkButton(header, text='打开日志目录', font=FONT_BODY,
                          command=app.open_logs).pack(side='right')
        self.structure_hint = ctk_kit.CTkLabel(self, text='', font=FONT_BODY, anchor='w')
        self.structure_hint.pack(fill='x', padx=PAGE_PAD)
        card = self._card()
        ctk_kit.CTkLabel(card, text='保存位置', font=FONT_CARD_TITLE).pack(
            anchor='w', padx=CARD_PAD, pady=(CARD_PAD, 4))
        # 原生 PathField：输入框 + 自带浏览按钮，不做二次拆装。
        self.path_field = ctk_kit.PathField(
            card, mode='folder', title='选择日志目录',
            placeholder='相对路径以程序目录为起点',
            on_change=lambda value: self._path_changed(DIRECTORY, value))
        self.path_field.pack(fill='x', padx=CARD_PAD, pady=(18, 8))
        ctk_kit.CTkLabel(
            card, text='相对路径以程序目录为起点。', font=FONT_SMALL,
            text_color=COLORS['text_secondary'][self._dark]).pack(
                anchor='w', padx=CARD_PAD, pady=(0, CARD_PAD))
        self.browse_button = self.path_field.button
        self.path_fields[DIRECTORY] = self.path_field
        self.fields[DIRECTORY] = (self.path_field, self.path_field.entry, 'text')
        card = self._card()
        ctk_kit.CTkLabel(card, text='保留策略', font=FONT_CARD_TITLE).pack(
            anchor='w', padx=CARD_PAD, pady=(CARD_PAD, 4))
        for title, key in (('最大日志数', 'max_log_files'), ('保留天数', 'retention_days')):
            row = ctk_kit.CTkFrame(card, fg_color='transparent')
            row.pack(fill='x', padx=CARD_PAD, pady=(18, 6) if key == 'max_log_files'
                     else (6, CARD_PAD))
            ctk_kit.CTkLabel(row, text=title, font=FONT_BODY, width=110,
                             anchor='w').pack(side='left')
            path = ('log', key)
            limits = NUMBER_RANGES[path]
            variable = tk.StringVar(master=self)
            spinner = ctk_kit.Spinner(
                row, from_=limits[0], to=limits[1], as_int=True,
                value=DEFAULT_VALUES['log'][key],
                command=lambda _value, field=path: self._number_changed(field))
            spinner.entry._entry.unbind('<Return>')
            spinner.entry._entry.unbind('<FocusOut>')
            spinner.entry.configure(textvariable=variable)
            variable.trace_add('write', lambda *_, field=path: self._number_changed(field))
            spinner._arrows.bind('<Button-1>',
                                 lambda event, field=path: self._step_arrow(field, event))
            spinner.pack(side='right')
            self.spinners[path] = spinner
            self.number_variables[path] = variable
            self.fields[path] = (spinner, spinner.entry, 'int')
        ctk_kit.CTkFrame(self, fg_color='transparent', height=CARD_GAP).pack(
            fill='x')
        self.refresh()

    def _card(self):
        """按主页比例创建圆角内容卡。"""
        card = ctk_kit.Card(
            self, corner_radius=CARD_RADIUS, border_width=CARD_BORDER,
            border_color=CARD_BORDER_COLOR[self._dark],
            fg_color=CARD_SURFACE[self._dark])
        card.pack(fill='x', padx=PAGE_PAD, pady=(CARD_GAP, 0))
        return card

    def _path_changed(self, path, value):
        """刷新或结构异常时不写回草稿。"""
        if not self._refreshing and path_writable(self.model, path):
            self.model.set(path, value)

    def _number_changed(self, path):
        """将可见输入原文写回模型，不进行范围钳制。"""
        if not self._refreshing and path_writable(self.model, path):
            self.model.set_input(path, self.number_variables[path].get(), kind='int')

    def _step_arrow(self, path, event):
        """根据箭头点击的上下半区选择单步增减。"""
        spinner = self.spinners[path]
        middle = max(1, spinner._arrows.winfo_height()) / 2
        self._step(path, 1 if event.y < middle else -1)

    def _step(self, path, delta):
        """只步进完整整数，并限制在对应字段的箭头范围内。"""
        if not path_writable(self.model, path):
            return
        spinner = self.spinners[path]
        raw = spinner.entry.get()
        if not re.fullmatch(r'[+-]?\d+', raw.strip()):
            return
        try:
            current = int(raw)
        except ValueError:
            return
        limits = NUMBER_RANGES[path]
        value = max(limits[0], min(limits[1], current + delta))
        spinner.set(value)

    def choose_directory(self):
        """选择目录只更新草稿，结构异常时不修改显示。"""
        if not path_writable(self.model, DIRECTORY):
            return
        selected = filedialog.askdirectory(parent=self)
        if selected:
            self.path_field.set(selected)

    def clear_directory(self):
        """仅清空草稿目录路径，结构异常时不修改显示。"""
        if not path_writable(self.model, DIRECTORY):
            return
        self.path_field.clear()

    def _safe_value(self, path, kind):
        """读取可能缺失的字段，结构异常时显示空文本。"""
        try:
            return (self.model.get_input(path) if kind == 'int'
                    else str(self.model.get(path) or ''))
        except (KeyError, IndexError, TypeError):
            return ''

    def refresh(self):
        """从当前文档同步所有可见字段，结构异常时禁用编辑。"""
        self._refreshing = True
        try:
            for path, (control, entry, kind) in self.fields.items():
                if kind == 'int':
                    self.number_variables[path].set(self._safe_value(path, kind))
                else:
                    control.set(self._safe_value(path, kind))
                writable = path_writable(self.model, path)
                control.set_readonly(not writable)
                entry.configure(state='normal' if writable else 'disabled')
            snapshot = self.model.snapshot()
            log = snapshot.get('log')
            invalid = 'log' in snapshot and not isinstance(log, Mapping)
            self.structure_hint.configure(text=STRUCTURE_HINT if invalid else '')
            self.path_field.set_readonly(
                not path_writable(self.model, DIRECTORY))
        finally:
            self._refreshing = False

    def focus_issue(self, path):
        """定位日志配置中的对应输入。"""
        field = self.fields.get(tuple(path))
        if field:
            scroll_to_widget(self, field[1])
            field[1].focus_force()
