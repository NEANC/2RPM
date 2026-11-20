#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""ctk-kit 外部动作路径和触发次数设置。"""

import re
import tkinter as tk
from tkinter import filedialog

import ctk_kit

from modules.config import DEFAULT_VALUES
from modules.gui.appearance import (COLORS, FONT_BODY, FONT_CARD_TITLE,
                                    FONT_SMALL, FONT_TITLE)
from modules.gui.widgets import (STRUCTURE_HINT, activate_and_focus,
                                 path_writable, scroll_to_widget)


EVENTS = (
    ('on_end', '目标结束', '监视中的目标进程结束后触发。'),
    ('on_timeout', '运行超时', '同一进程的运行超时告警次数达到阈值的整数倍时触发。'),
    ('on_wait_timeout', '等待启动超时', '超过最长等待时间仍未找到目标进程时触发。'),
)
THRESHOLD = ('monitor', 'common', 'timeout_threshold')
THRESHOLD_RANGE = (1, 99)

# 与主页保持同一套原型比例：圆角 20、2px 边框、28px 页边距与卡片内边距。
PAGE_PAD = 28
CARD_GAP = 32
CARD_PAD = 28
CARD_RADIUS = 20
CARD_BORDER = 2
CARD_SURFACE = ('#FAFAFA', '#232323')
CARD_BORDER_COLOR = ('#E1E1E1', '#3A3A3A')


class Page(ctk_kit.CTkScrollableFrame):
    """仅编辑已有 external 字段和 monitor 超时阈值。"""

    def __init__(self, parent, app):
        """绘制三个事件卡片。"""
        super().__init__(parent, corner_radius=0)
        self.app = app
        self.model = app.model
        self._dark = int(ctk_kit.get_appearance_mode() == 'Dark')
        self.fields = {}
        self.path_fields = {}
        self.spinners = {}
        self.number_variables = {}
        self.browse_buttons = {}
        self._refreshing = False
        self._focus_request = 0
        ctk_kit.CTkLabel(self, text='外部程序设置', font=FONT_TITLE).pack(
            anchor='w', padx=PAGE_PAD, pady=(PAGE_PAD, 12))
        self.structure_hint = ctk_kit.CTkLabel(
            self, text='', font=FONT_BODY, anchor='w')
        self.structure_hint.pack(fill='x', padx=PAGE_PAD)
        for event, title, hint in EVENTS:
            card = self._card()
            ctk_kit.CTkLabel(card, text=title, font=FONT_CARD_TITLE).pack(
                anchor='w', padx=CARD_PAD, pady=(CARD_PAD, 4))
            ctk_kit.CTkLabel(
                card, text=hint, font=FONT_SMALL,
                text_color=COLORS['text_secondary'][self._dark]).pack(
                    anchor='w', padx=CARD_PAD)
            ctk_kit.CTkLabel(card, text='外部程序／BAT 路径', font=FONT_BODY).pack(
                anchor='w', padx=CARD_PAD, pady=(18, 0))
            field = self._path_field(card, event)
            if event == 'on_timeout':
                row = ctk_kit.CTkFrame(card, fg_color='transparent')
                row.pack(fill='x', padx=CARD_PAD, pady=(18, CARD_PAD))
                ctk_kit.CTkLabel(row, text='触发次数阈值', font=FONT_BODY).pack(
                    side='left')
                spinner = self._spinner(row, THRESHOLD, THRESHOLD_RANGE,
                                        DEFAULT_VALUES['monitor']['common']['timeout_threshold'])
                spinner.pack(side='right')
            else:
                field.pack_configure(pady=(4, CARD_PAD))
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

    def _path_field(self, card, event):
        """原生路径框：输入框 + 自带浏览按钮，不做二次拆装。"""
        path = ('external', event)
        field = ctk_kit.PathField(
            card, mode='file', title='选择外部程序或 BAT',
            placeholder='留空则不执行',
            on_change=lambda value, key=event: self._path_changed(
                ('external', key), value))
        field.pack(fill='x', padx=CARD_PAD, pady=(4, 0))
        self.path_fields[path] = field
        self.browse_buttons[event] = field.button
        self.fields[path] = (field, field.entry, 'text')
        return field

    def _spinner(self, parent, path, limits, default):
        """保留数字草稿，同时接管原生箭头的主动步进。"""
        variable = tk.StringVar(master=self)
        spinner = ctk_kit.Spinner(
            parent, from_=limits[0], to=limits[1], as_int=True,
            value=default,
            command=lambda _value: self._number_changed(path))
        spinner.entry._entry.unbind('<Return>')
        spinner.entry._entry.unbind('<FocusOut>')
        spinner.entry.configure(textvariable=variable)
        variable.trace_add('write', lambda *_: self._number_changed(path))
        spinner._arrows.bind('<Button-1>', self._step_arrow)
        self.spinners[path] = spinner
        self.number_variables[path] = variable
        self.fields[path] = (spinner, spinner.entry, 'int')
        return spinner

    def _path_changed(self, path, value):
        """刷新或结构异常时不写回草稿。"""
        if not self._refreshing and path_writable(self.model, path):
            self.model.set(path, value)

    def _number_changed(self, path):
        """将可见数字原文写入草稿，不进行范围钳制。"""
        if not self._refreshing and path_writable(self.model, path):
            self.model.set_input(path, self.number_variables[path].get(), kind='int')

    def _step_arrow(self, event):
        """根据箭头的上下半区仅步进可转换的整数。"""
        spinner = self.spinners[THRESHOLD]
        middle = max(1, spinner._arrows.winfo_height()) / 2
        self._step(1 if event.y < middle else -1)

    def _step(self, delta):
        """只对完整整数执行限定范围内的一步操作。"""
        path = THRESHOLD
        if not path_writable(self.model, path):
            return
        spinner = self.spinners[path]
        try:
            raw = self.model.get_input(path)
        except KeyError:
            raw = str(DEFAULT_VALUES['monitor']['common']['timeout_threshold'])
        if not re.fullmatch(r'[+-]?\d+', raw.strip()):
            return
        try:
            current = int(raw)
        except ValueError:
            return
        value = max(THRESHOLD_RANGE[0], min(THRESHOLD_RANGE[1], current + delta))
        spinner.set(value)

    def choose_file(self, event):
        """系统选择器取消时保持原输入，结构异常时不修改显示。"""
        path = ('external', event)
        if not path_writable(self.model, path):
            return
        selected = filedialog.askopenfilename(
            parent=self, filetypes=[('程序或脚本', '*.exe *.bat *.cmd'),
                                    ('所有文件', '*.*')])
        if selected:
            self.path_fields[path].set(selected)

    def clear_file(self, event):
        """清空指定动作而不执行它。"""
        path = ('external', event)
        if not path_writable(self.model, path):
            return
        self.path_fields[path].clear()

    def _safe_value(self, path, kind):
        """读取可能缺失的字段，结构异常时显示空文本。"""
        try:
            return (self.model.get_input(path) if kind == 'int'
                    else str(self.model.get(path) or ''))
        except (KeyError, IndexError, TypeError):
            return ''

    def refresh(self):
        """显示已有路径与未完成的数字输入。"""
        self._refreshing = True
        try:
            invalid = False
            for path, (control, entry, kind) in self.fields.items():
                if kind == 'int':
                    self.number_variables[path].set(self._safe_value(path, kind))
                else:
                    control.set(self._safe_value(path, kind))
                writable = path_writable(self.model, path)
                control.set_readonly(not writable)
                entry.configure(state='normal' if writable else 'disabled')
                invalid = invalid or not writable
            self.structure_hint.configure(text=STRUCTURE_HINT if invalid else '')
            for event in self.browse_buttons:
                state = ('normal' if path_writable(
                    self.model, ('external', event)) else 'disabled')
                self.browse_buttons[event].configure(state=state)
        finally:
            self._refreshing = False

    def focus_issue(self, path):
        """导航到与诊断路径相符的输入。"""
        field = self.fields.get(tuple(path))
        if field:
            self._focus_request += 1
            request = self._focus_request
            widget = field[1]
            scroll_to_widget(self, widget)
            if self.winfo_toplevel().winfo_viewable():
                activate_and_focus(widget)
            else:
                def focus_latest():
                    """窗口恢复焦点稳定后只兑现最近一次导航。"""
                    if (request == self._focus_request
                            and self.app.current_page == '外部程序设置'
                            and self.winfo_viewable()
                            and widget.winfo_viewable()):
                        activate_and_focus(widget)

                def focus_on_map(event):
                    """窗口实际映射后再将焦点交给诊断字段。"""
                    if event.widget is self.winfo_toplevel():
                        self.winfo_toplevel().unbind('<Map>', binding)
                        self.after(120, focus_latest)

                binding = self.winfo_toplevel().bind('<Map>', focus_on_map, add='+')
