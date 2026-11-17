#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""使用 ctk-kit 绘制监视模式、目标及时间设置。"""

import ntpath
from collections.abc import Mapping
import tkinter as tk
from tkinter import filedialog

import ctk_kit

from modules.config import DEFAULT_VALUES
from modules.gui.appearance import FONT_BODY, FONT_CARD_TITLE, FONT_TITLE
from modules.gui.widgets import STRUCTURE_HINT, path_writable, scroll_to_widget
from modules.monitor_target import resolve_monitor_target


class Page(ctk_kit.CTkScrollableFrame):
    """保留高级草稿字段的监视设置页。"""

    def __init__(self, parent, model):
        """构建互斥运行方式、目标类型及时间表单。"""
        super().__init__(parent, corner_radius=0)
        self.model = model
        self.fields = {}
        self._refreshing = False
        self._target_type = 'file'
        ctk_kit.CTkLabel(self, text='监视设置', font=FONT_TITLE).pack(anchor='w', pady=(0, 12))
        self.structure_hint = ctk_kit.CTkLabel(self, text='', font=FONT_BODY, anchor='w')
        self.structure_hint.pack(fill='x', pady=(0, 8))
        card = self._card('运行方式')
        self.mode = ctk_kit.CTkSegmentedButton(
            card, values=['等待目标运行', '主动启动并监视'],
            command=self._switch, font=FONT_BODY)
        self.mode.pack(fill='x', padx=16, pady=8)
        ctk_kit.CTkLabel(card, text='目标类型', font=FONT_BODY).pack(anchor='w', padx=16)
        self.target_type = ctk_kit.CTkOptionMenu(
            card, values=['程序文件', '计划任务'], font=FONT_BODY,
            command=self._select_type)
        self.target_type.pack(anchor='w', padx=16, pady=8)
        self.file_row = ctk_kit.CTkFrame(card, fg_color='transparent')
        self.task_row = ctk_kit.CTkFrame(card, fg_color='transparent')
        self._target_entry(self.file_row, '目标位置', browse=True)
        self._target_entry(self.task_row, '计划任务完整路径', browse=False)
        self.args_row = ctk_kit.CTkFrame(card, fg_color='transparent')
        self._field(self.args_row, '启动参数', ('monitor', 'launch', 'args'), optional=True)
        self.preview = ctk_kit.CTkLabel(card, text='', font=FONT_BODY, anchor='w')
        self.preview.pack(fill='x', padx=16, pady=(4, 16))

        card = self._card('时间设置')
        for label, path in (
                ('等待时限', ('monitor', 'common', 'max_wait')),
                ('超时告警间隔', ('monitor', 'common', 'timeout_interval')),
                ('检查间隔', ('monitor', 'common', 'check_interval')),
                ('轮询间隔', ('monitor', 'common', 'loop_interval')),
                ('任务回溯（分钟）', ('monitor', 'watch', 'lookback_minutes'))):
            self._field(card, label, path, numeric=path[-1] == 'lookback_minutes')
        self.section_for_issue = ctk_kit.CTkFrame(self, corner_radius=12)
        self.section_for_issue.pack(fill='x', pady=8)
        self.section_for_issue.button = ctk_kit.CTkButton(
            self.section_for_issue, text='高级字段请在 YAML 中编辑', font=FONT_BODY)
        self.section_for_issue.button.pack(fill='x', padx=16, pady=10)
        self.section_for_issue.expanded = False
        self.section_for_issue.set_expanded = self._set_advanced
        self.refresh()

    def _card(self, title):
        """建立内容卡片。"""
        card = ctk_kit.CTkFrame(self, corner_radius=12)
        card.pack(fill='x', pady=8)
        ctk_kit.CTkLabel(card, text=title, font=FONT_CARD_TITLE).pack(
            anchor='w', padx=16, pady=(12, 4))
        return card

    def _target_entry(self, parent, label, *, browse):
        """构建文件搜索框或计划任务单行输入。"""
        ctk_kit.CTkLabel(parent, text=label, font=FONT_BODY).pack(anchor='w')
        line = ctk_kit.CTkFrame(parent, fg_color='transparent')
        line.pack(fill='x')
        if browse:
            ctk_kit.CTkButton(line, text='⌕', width=36, command=self._browse).pack(side='left')
        entry = ctk_kit.CTkEntry(line, font=FONT_BODY)
        variable = tk.StringVar(master=self)
        entry.configure(textvariable=variable)
        entry.pack(side='left', fill='x', expand=True, padx=4)
        variable.trace_add('write', lambda *_: self._target_changed(entry))
        if browse:
            ctk_kit.CTkButton(line, text='×', width=36,
                              command=lambda: self.set_field(('monitor', 'target'), '')).pack(side='left')
        if browse:
            self.file_entry = entry
            self.file_variable = variable
        else:
            self.task_entry = entry
            self.task_variable = variable
        self.fields[('monitor', 'target')] = (variable, entry, 'text')

    def _field(self, parent, label, path, *, optional=False, numeric=False):
        """绑定原值输入，仅由用户事件写回模型。"""
        row = ctk_kit.CTkFrame(parent, fg_color='transparent')
        row.pack(fill='x', padx=16, pady=5)
        ctk_kit.CTkLabel(row, text=label, font=FONT_BODY, width=130,
                         anchor='w').pack(side='left')
        entry = ctk_kit.CTkEntry(row, font=FONT_BODY)
        variable = tk.StringVar(master=self)
        entry.configure(textvariable=variable)
        entry.pack(side='left', fill='x', expand=True)
        variable.trace_add('write', lambda *_: self._changed(path, entry, optional, numeric))
        self.fields[path] = (variable, entry, 'int' if numeric else 'text')
        return entry

    def _changed(self, path, entry, optional, numeric):
        """将已修改的用户原文交给模型。"""
        if self._refreshing or not path_writable(self.model, path):
            return
        value = entry.get()
        if numeric:
            self.model.set_input(path, value, kind='int')
        else:
            self.model.set(path, None if optional and not value else value)

    def _monitor(self):
        """返回映射类型的 monitor 节，缺失或结构异常时为空映射。"""
        monitor = self.model.snapshot().get('monitor')
        return monitor if isinstance(monitor, Mapping) else {}

    def _target_changed(self, entry):
        """对启动文件目标的主动编辑同步工作目录。"""
        if self._refreshing or not path_writable(self.model, ('monitor', 'target')):
            return
        value = entry.get()
        self.model.set(('monitor', 'target'), value)
        self.model._gui_target_types[self._monitor().get('mode')] = self._target_type
        if self._monitor().get('mode') == 'launch' and self._target_type == 'file':
            try:
                resolved = resolve_monitor_target('launch', value, self._config_dir(), None)
            except ValueError:
                pass
            else:
                if resolved.kind == 'file':
                    self.model.set(('monitor', 'launch', 'cwd'), ntpath.dirname(resolved.value))
        self._update_preview()

    def set_field(self, path, value):
        """模拟用户在当前可见字段进行输入。"""
        if path == ('monitor', 'target'):
            entry = self.task_entry if self._target_type == 'task' else self.file_entry
        else:
            entry = self.fields[path][1]
        entry.delete(0, 'end')
        entry.insert(0, str(value))

    def _switch(self, value):
        """切换运行方式只变更模式分支，不触碰 cwd。"""
        if not path_writable(self.model, ('monitor', 'mode')):
            return
        mode = {'等待目标运行': 'watch', '主动启动并监视': 'launch'}.get(value, value)
        self.model.switch_mode(mode)
        self.refresh()

    def _select_type(self, value):
        """用户选择任务目标时清空启动参数，不碰工作目录。"""
        if not path_writable(self.model, ('monitor', 'target')):
            return
        self._target_type = 'task' if value in ('task', '计划任务') else 'file'
        self.model._gui_target_types[self._monitor().get('mode')] = self._target_type
        if (self._target_type == 'task' and self._monitor().get('mode') == 'launch'
                and path_writable(self.model, ('monitor', 'launch', 'args'))):
            self.model.set(('monitor', 'launch', 'args'), None)
        self.fields[('monitor', 'target')] = (
            self.task_variable if self._target_type == 'task' else self.file_variable,
            self.task_entry if self._target_type == 'task' else self.file_entry, 'text')
        self._show_target()
        self._update_preview()

    def _show_target(self):
        """仅展示当前类型输入与适用的启动参数。"""
        self.file_row.pack_forget()
        self.task_row.pack_forget()
        self.args_row.pack_forget()
        row = self.task_row if self._target_type == 'task' else self.file_row
        row.pack(fill='x', padx=16, pady=8)
        if self._monitor().get('mode') == 'launch' and self._target_type == 'file':
            self.args_row.pack(fill='x', pady=4)

    def _browse(self):
        """以系统本地选择器选取程序文件。"""
        path = filedialog.askopenfilename(parent=self)
        if path:
            self.set_field(('monitor', 'target'), path)

    def _config_dir(self):
        """取得解析目标所需的配置目录。"""
        path = self.model.document.path
        return str(path.parent if path else getattr(self.winfo_toplevel(),
                                                     '_gui_program_dir', 'C:\\'))

    def _update_preview(self):
        """只显示安全的目标类型提示，不执行目标。"""
        monitor = self._monitor()
        launch = monitor.get('launch')
        cwd = launch.get('cwd') if isinstance(launch, Mapping) else None
        try:
            target = resolve_monitor_target(
                monitor.get('mode'), monitor.get('target'),
                self._config_dir(), cwd)
            if target.kind == 'process':
                text = '此目标需在 YAML 中编辑；已保留原目标'
            else:
                text = f'解析结果：{target.value}；工作目录：{target.cwd or "按 CLI 规则确定"}'
        except (ValueError, TypeError):
            text = '目标路径待校验'
        self.preview.configure(text=text)

    def _set_advanced(self, expanded):
        """保持旧页面问题定位接口且不显示 ttk 控件。"""
        self.section_for_issue.expanded = expanded

    def refresh(self):
        """同步模型值，打开已有任务不触发用户切换逻辑。"""
        self._refreshing = True
        try:
            self.file_entry.configure(state='normal')
            self.task_entry.configure(state='normal')
            for _path, (_variable, entry, _kind) in self.fields.items():
                entry.configure(state='normal')
            monitor = self._monitor()
            mode = monitor.get('mode')
            self.mode.set('主动启动并监视' if mode == 'launch' else '等待目标运行')
            target = monitor.get('target')
            try:
                kind = resolve_monitor_target(mode, target, self._config_dir()).kind
            except (ValueError, TypeError):
                kind = self._target_type
            self._target_type = 'task' if kind == 'task' else 'file'
            self.target_type.set('计划任务' if self._target_type == 'task' else '程序文件')
            for entry in (self.file_entry, self.task_entry):
                entry.delete(0, 'end')
                entry.insert(0, '' if target is None else str(target))
            self.fields[('monitor', 'target')] = (
                self.task_variable if self._target_type == 'task' else self.file_variable,
                self.task_entry if self._target_type == 'task' else self.file_entry, 'text')
            self.fields[('monitor', 'target')][0].set('' if target is None else str(target))
            for path, (_, entry, kind) in self.fields.items():
                if path == ('monitor', 'target'):
                    continue
                try:
                    value = self.model.get_input(path) if kind == 'int' else self.model.get(path)
                except (KeyError, IndexError, TypeError):
                    entry.delete(0, 'end')
                    continue
                entry.delete(0, 'end')
                entry.insert(0, '' if value is None else str(value))
            self._show_target()
            self._update_preview()
            self.section_for_issue.button.configure(
                text='高级字段请在 YAML 中编辑' +
                     ('（已配置）' if self._configured_advanced() else ''))
            writable = {path: path_writable(self.model, path) for path in self.fields}
            for path, (_variable, entry, _kind) in self.fields.items():
                entry.configure(state='normal' if writable[path] else 'disabled')
            target_state = 'normal' if writable.get(('monitor', 'target'), False) else 'disabled'
            self.file_entry.configure(state=target_state)
            self.task_entry.configure(state=target_state)
            self.mode.configure(
                state='normal' if path_writable(self.model, ('monitor', 'mode')) else 'disabled')
            self.target_type.configure(state=target_state)
            self.structure_hint.configure(
                text='' if all(writable.values()) else STRUCTURE_HINT)
        finally:
            self._refreshing = False

    def _configured_advanced(self):
        """仅检查字段是否偏离默认值，不展示敏感原值。"""
        for path in self.fields:
            if len(path) != 3 or path[1] not in ('common', 'watch'):
                continue
            try:
                if self.model.get(path) != DEFAULT_VALUES['monitor'][path[1]][path[2]]:
                    return True
            except (KeyError, IndexError, TypeError):
                continue
        return False

    def focus_issue(self, path):
        """聚焦当前可编辑字段，隐藏字段指向 YAML 提示。"""
        entry = self.fields.get(tuple(path))
        if entry:
            self.section_for_issue.set_expanded(True)
        widget = entry[1]._entry if entry and hasattr(entry[1], '_entry') else (
            entry[1] if entry else self.section_for_issue.button)
        self.widget_for_issue = widget
        scroll_to_widget(self, widget)
        widget.focus_force()
