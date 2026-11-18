#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""ctk-kit 外部动作路径和触发次数设置。"""

import tkinter as tk
from tkinter import filedialog

import ctk_kit

from modules.config import DEFAULT_VALUES
from modules.gui.appearance import CARD_STYLE, FONT_BODY, FONT_CARD_TITLE, FONT_SMALL, FONT_TITLE
from modules.gui.widgets import STRUCTURE_HINT, path_writable, scroll_to_widget


EVENTS = (
    ('on_end', '目标结束', '监视中的目标进程结束后触发。'),
    ('on_timeout', '运行超时', '同一进程的运行超时告警次数达到阈值的整数倍时触发。'),
    ('on_wait_timeout', '等待启动超时', '超过最长等待时间仍未找到目标进程时触发。'),
)


class Page(ctk_kit.CTkScrollableFrame):
    """仅编辑已有 external 字段和 monitor 超时阈值。"""

    def __init__(self, parent, app):
        """绘制三个事件卡片。"""
        super().__init__(parent, corner_radius=0)
        self.model = app.model
        self.fields = {}
        self.action_drafts = {}
        self.browse_buttons = {}
        self.clear_buttons = {}
        self.restore_buttons = {}
        self.generation = self.model.document_generation
        self._refreshing = False
        ctk_kit.CTkLabel(self, text='外部程序设置', font=FONT_TITLE).pack(anchor='w', pady=(0, 12))
        self.structure_hint = ctk_kit.CTkLabel(self, text='', font=FONT_BODY, anchor='w')
        self.structure_hint.pack(fill='x', pady=(0, 8))
        for event, title, hint in EVENTS:
            card = ctk_kit.CTkFrame(self, **CARD_STYLE)
            card.pack(fill='x', pady=8)
            ctk_kit.CTkLabel(card, text=title, font=FONT_CARD_TITLE).pack(anchor='w', padx=16, pady=(12, 4))
            ctk_kit.CTkLabel(card, text=hint, font=FONT_SMALL).pack(anchor='w', padx=16)
            row = ctk_kit.CTkFrame(card, fg_color='transparent')
            row.pack(fill='x', padx=16, pady=12)
            ctk_kit.CTkLabel(row, text='外部程序／BAT 路径', font=FONT_BODY).pack(side='left')
            self._entry(row, ('external', event))
            browse = ctk_kit.CTkButton(
                row, text='⌕', width=32, command=lambda key=event: self.choose_file(key))
            browse.pack(side='left')
            clear = ctk_kit.CTkButton(
                row, text='×', width=32, command=lambda key=event: self.clear_file(key))
            clear.pack(side='left')
            self.browse_buttons[event] = browse
            self.clear_buttons[event] = clear
            restore = ctk_kit.CTkButton(row, text='恢复', width=55, state='disabled',
                                        command=lambda key=event: self.restore_file(key))
            restore.pack(side='left')
            self.restore_buttons[event] = restore
            if event == 'on_timeout':
                row = ctk_kit.CTkFrame(card, fg_color='transparent')
                row.pack(fill='x', padx=16, pady=(0, 12))
                ctk_kit.CTkLabel(row, text='触发次数阈值', font=FONT_BODY).pack(side='left')
                path = ('monitor', 'common', 'timeout_threshold')
                self._entry(row, path, numeric=True)
                for delta, text in ((1, '▲'), (-1, '▼')):
                    ctk_kit.CTkButton(row, text=text, width=28,
                                      command=lambda step=delta: self._step(step)).pack(side='left')
        self.refresh()

    def _entry(self, parent, path, *, numeric=False):
        """追踪粘贴与键盘输入，数字原文交由模型保存。"""
        variable = tk.StringVar(master=self)
        entry = ctk_kit.CTkEntry(parent, textvariable=variable, font=FONT_BODY,
                                 width=75 if numeric else 250)
        entry.pack(side='left', fill='x', expand=not numeric, padx=8)
        variable.trace_add('write', lambda *_: self._changed(path, variable, numeric))
        self.fields[path] = (variable, entry, 'int' if numeric else 'text')

    def _changed(self, path, variable, numeric):
        """刷新或结构异常时不写回草稿。"""
        if not self._refreshing and path_writable(self.model, path):
            if numeric:
                self.model.set_input(path, variable.get(), kind='int')
            else:
                self.model.set(path, variable.get())

    def _step(self, delta):
        """仅在现值为整数时步进。"""
        path = ('monitor', 'common', 'timeout_threshold')
        if not path_writable(self.model, path):
            return
        try:
            raw = self.model.get_input(path)
        except (KeyError, IndexError, TypeError):
            raw = self.fields[path][0].get()
        if not raw.strip():
            raw = str(DEFAULT_VALUES['monitor']['common']['timeout_threshold'])
        if raw.lstrip('+-').isdigit():
            self.fields[path][0].set(str(int(raw) + delta))

    def choose_file(self, event):
        """系统选择器取消时保持原输入，结构异常时不修改显示。"""
        path = ('external', event)
        if not path_writable(self.model, path):
            return
        selected = filedialog.askopenfilename(parent=self, filetypes=[('程序或脚本', '*.exe *.bat *.cmd'), ('所有文件', '*.*')])
        if selected:
            self.fields[path][0].set(selected)

    def clear_file(self, event):
        """清空指定动作而不执行它，仅在本模型内存中保留可恢复路径。"""
        path = ('external', event)
        if not path_writable(self.model, path):
            return
        value = self.fields[path][0].get()
        if value:
            self.action_drafts[event] = value
        self.fields[path][0].set('')
        self._update_restore(event)

    def restore_file(self, event):
        """只恢复当前文档曾清空的动作，不写入其它文档。"""
        path = ('external', event)
        if not path_writable(self.model, path):
            return
        value = self.action_drafts.pop(event, None)
        if value is not None and self.generation == self.model.document_generation:
            self.fields[path][0].set(value)
        self._update_restore(event)

    def _update_restore(self, event):
        """按草稿存在且字段可写启用恢复入口。"""
        button = self.restore_buttons.get(event)
        if button is not None:
            writable = path_writable(self.model, ('external', event))
            button.configure(
                state='normal' if event in self.action_drafts and writable else 'disabled')

    def _safe_value(self, path, kind):
        """读取可能缺失的字段，结构异常时显示空文本。"""
        try:
            return (self.model.get_input(path) if kind == 'int'
                    else str(self.model.get(path) or ''))
        except (KeyError, IndexError, TypeError):
            return ''

    def refresh(self):
        """显示已有路径与未完成的数字输入，文档替换后丢弃旧恢复草稿。"""
        if self.generation != self.model.document_generation:
            self.action_drafts.clear()
            self.generation = self.model.document_generation
        self._refreshing = True
        try:
            invalid = False
            for path, (variable, entry, kind) in self.fields.items():
                variable.set(self._safe_value(path, kind))
                writable = path_writable(self.model, path)
                entry.configure(state='normal' if writable else 'disabled')
                invalid = invalid or not writable
            self.structure_hint.configure(text=STRUCTURE_HINT if invalid else '')
            for event in self.browse_buttons:
                state = ('normal' if path_writable(
                    self.model, ('external', event)) else 'disabled')
                self.browse_buttons[event].configure(state=state)
                self.clear_buttons[event].configure(state=state)
        finally:
            self._refreshing = False
        for event in self.restore_buttons:
            self._update_restore(event)

    def focus_issue(self, path):
        """导航到与诊断路径相符的输入。"""
        field = self.fields.get(tuple(path))
        if field:
            scroll_to_widget(self, field[1])
            field[1].focus_force()
