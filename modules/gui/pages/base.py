#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""共享的字段绑定、刷新与问题焦点行为。"""

import tkinter as tk
from tkinter import ttk

from modules.config import DEFAULT_VALUES
from modules.gui.widgets import (CollapsibleSection, ScrollForm,
                                 activate_and_focus)


class Page(ScrollForm):
    """四页共享同一个 EditorModel。"""

    def __init__(self, parent, model):
        """创建表单并登记字段路径。"""
        super().__init__(parent)
        self.model = model
        self.fields = {}
        self.sections = {}
        self._refreshing = False
        self.section_for_issue = CollapsibleSection(self.body, '高级设置')
        self.widget_for_issue = None

    def field(self, parent, label, path, *, secret=False, kind='text'):
        """创建同模型字段绑定的标签和输入框。"""
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=3)
        if kind == 'multiline':
            ttk.Label(row, text=label).pack(anchor='w')
        else:
            ttk.Label(row, text=label, width=12).pack(side='left')
        value = self.model.get(path)
        if kind == 'multiline':
            variable = None
            widget = tk.Text(row, height=5, wrap='word', undo=True)
            widget.insert('1.0', '' if value is None else str(value))
            widget.edit_modified(False)
            widget.bind('<<Modified>>', lambda _: self._text_changed(path, widget))
        elif kind == 'bool':
            variable = tk.BooleanVar(master=row, value=bool(value))
            widget = ttk.Checkbutton(row, variable=variable)
        else:
            variable = tk.StringVar(master=row, value='' if value is None else str(value))
            widget = ttk.Entry(row, textvariable=variable, show='●' if secret else '')
        widget.pack(side='left', fill='x', expand=True)
        if variable is not None:
            variable.trace_add('write', lambda *_: self._changed(path, variable, kind))
        self.fields[path] = (variable, widget, kind)
        if parent is not self.body:
            self.sections[path] = self.section_for_issue
        return widget

    def _text_changed(self, path, widget):
        """把多行输入的实际内容写回草稿。"""
        if not widget.edit_modified():
            return
        widget.edit_modified(False)
        if not self._refreshing:
            self.model.set(path, widget.get('1.0', 'end-1c'))

    def _changed(self, path, variable, kind):
        """仅把用户输入写回草稿，不在 refresh 时产生编辑。"""
        if self._refreshing:
            return
        value = variable.get()
        if kind == 'bool':
            value = bool(value)
        elif kind == 'int':
            self.model.set_input(path, value, kind='int')
            return
        elif kind == 'optional' and not value:
            value = None
        self.model.set(path, value)
        if path in self.sections:
            self._section_state()

    def set_field(self, path, value):
        """通过控件执行一次用户编辑。"""
        variable, widget, kind = self.fields[path]
        if kind == 'multiline':
            widget.delete('1.0', 'end')
            widget.insert('1.0', value)
            self._text_changed(path, widget)
        else:
            variable.set(value)

    def refresh(self) -> None:
        """从当前共享模型同步所有输入框。"""
        self._refreshing = True
        try:
            for path, (variable, widget, kind) in self.fields.items():
                try:
                    value = self.model.get_input(path) if kind == 'int' else self.model.get(path)
                except (KeyError, IndexError, TypeError):
                    continue
                if kind == 'multiline':
                    text = '' if value is None else str(value)
                    if widget.get('1.0', 'end-1c') != text:
                        widget.delete('1.0', 'end')
                        widget.insert('1.0', text)
                    widget.edit_modified(False)
                else:
                    variable.set(bool(value) if kind == 'bool' else '' if value is None else str(value))
        finally:
            self._refreshing = False
        self._section_state()

    def _section_state(self):
        """仅列出脱敏的非默认高级字段名称，不显示字段值。"""
        configured = []
        for path in self.sections:
            default = DEFAULT_VALUES
            for key in path:
                if not isinstance(default, dict) or key not in default:
                    break
                default = default[key]
            try:
                if self.model.get(path) != default:
                    configured.append(path[-1])
            except (KeyError, IndexError, TypeError):
                continue
        section = self.section_for_issue
        section.title = ('高级设置（已配置：' + '、'.join(configured) + '）'
                         if configured else '高级设置')
        section.set_expanded(section.expanded)

    def focus_issue(self, path) -> None:
        """展开并聚焦对应字段，同时滚动至问题。"""
        key = next((field for field in self.fields if field == path), None)
        if key is None:
            key = next((field for field in self.fields if path[:len(field)] == field), None)
        if key is None:
            return
        section = self.sections.get(key)
        if section is not None:
            section.set_expanded(True)
            self.section_for_issue = section
        widget = self.fields[key][1]
        self.focus_widget(widget)

    def focus_widget(self, widget):
        """切换页面后在主线程展开并滚动到目标控件。"""
        self.winfo_toplevel().update_idletasks()
        self.widget_for_issue = widget
        activate_and_focus(widget)
        self.update_idletasks()
        if self.body.winfo_height():
            offset = 0
            current = widget
            while current is not self.body:
                offset += current.winfo_y()
                current = current.master
            self.canvas.yview_moveto(offset / self.body.winfo_height())
