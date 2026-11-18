#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""按已批准原型重绘监视设置：运行方式、目标与时间三张卡片。"""

import ntpath
import re
from collections.abc import Mapping
import tkinter as tk
from tkinter import filedialog

import ctk_kit

from modules.config import DEFAULT_VALUES
from modules.gui.appearance import COLORS, FONT_BODY, FONT_CARD_TITLE, FONT_TITLE
from modules.gui.widgets import (STRUCTURE_HINT, activate_and_focus,
                                 path_writable, scroll_to_widget)
from modules.monitor_target import resolve_monitor_target

# 与主页一致的卡片常量；本页按原型把卡片间距收为 20。
PAGE_PAD = 28
CARD_GAP = 20
CARD_PAD = 28
CARD_RADIUS = 20
CARD_BORDER = 2
CARD_SURFACE = ('#FAFAFA', '#232323')
CARD_BORDER_COLOR = ('#E1E1E1', '#3A3A3A')
LABEL_WIDTH = 150


class Page(ctk_kit.CTkScrollableFrame):
    """保留高级草稿字段的监视设置页。"""

    def __init__(self, parent, model):
        """构建互斥运行方式、目标类型及时间表单。"""
        super().__init__(parent, corner_radius=0)
        self.model = model
        self.fields = {}
        self._refreshing = False
        self._target_type = 'file'
        self._dark = int(ctk_kit.get_appearance_mode() == 'Dark')
        self._build()
        self.refresh()

    # ---------------------------------------------------------------- 装配
    def _build(self):
        """页头之后按原型顺序放三张卡片。"""
        header = ctk_kit.CTkFrame(self, fg_color='transparent')
        header.pack(fill='x', padx=PAGE_PAD, pady=(PAGE_PAD, 0))
        ctk_kit.CTkLabel(header, text='监视设置', font=FONT_TITLE,
                         text_color=COLORS['text_primary'][self._dark]).pack(side='left')
        self.structure_hint = self._text(self, '', anchor='w')
        self.structure_hint.pack(fill='x', padx=PAGE_PAD, pady=(6, 0))
        self._build_mode_card()
        self._build_target_card()
        self._build_time_card()
        self._build_advanced()
        ctk_kit.CTkFrame(self, fg_color='transparent', height=CARD_GAP).pack(fill='x')

    def _card(self, title):
        """原型比例的圆角内容卡，标题放在卡片内部首行。"""
        card = ctk_kit.Card(self, corner_radius=CARD_RADIUS,
                            border_width=CARD_BORDER,
                            border_color=CARD_BORDER_COLOR[self._dark],
                            fg_color=CARD_SURFACE[self._dark])
        card.pack(fill='x', padx=PAGE_PAD, pady=(CARD_GAP, 0))
        ctk_kit.CTkLabel(card, text=title, font=FONT_CARD_TITLE,
                         text_color=COLORS['text_primary'][self._dark]).pack(
            anchor='w', padx=CARD_PAD, pady=(CARD_PAD, 14))
        return card

    def _text(self, parent, text, *, muted=False, **kwargs):
        """正文标签；说明和字段名使用次要色。"""
        return ctk_kit.CTkLabel(
            parent, text=text, font=FONT_BODY,
            text_color=COLORS['text_secondary' if muted else 'text_primary'][self._dark],
            **kwargs)

    def _build_mode_card(self):
        """运行方式：两个互斥选项的分段控件。"""
        card = self._card('运行方式')
        self._mode_card = card
        self.mode = ctk_kit.CTkSegmentedButton(
            card, values=['等待目标运行', '主动启动并监视'],
            command=self._switch, font=FONT_BODY)
        self.mode.pack(fill='x', padx=CARD_PAD, pady=(0, CARD_PAD))

    def _build_target_card(self):
        """监视目标：类型下拉、按类型切换的输入与主动启动参数。"""
        card = self._card('监视目标')
        row = card.row(1, padx=CARD_PAD, pady=(0, 14))
        self._text(row, '目标类型', muted=True).pack(side='left')
        self.target_type = ctk_kit.CTkOptionMenu(
            row, values=['程序文件', '计划任务'], font=FONT_BODY,
            command=self._select_type)
        self.target_type.pack(side='right')
        self.target_body = ctk_kit.CTkFrame(card, fg_color='transparent')
        self.target_body.pack(fill='x', padx=CARD_PAD)
        self._build_file_target()
        self._build_task_target()
        self.args_entry = self._field(
            self.target_body, '启动参数', ('monitor', 'launch', 'args'),
            optional=True, stacked=True)
        self.args_row = self.args_entry.master
        self.preview = self._text(card, '', muted=True, anchor='w', justify='left')
        self.preview.pack(fill='x', padx=CARD_PAD, pady=(14, CARD_PAD))

    def _build_file_target(self):
        """程序文件目标：可输入的搜索框加紧凑「浏览」入口。"""
        self.file_row = ctk_kit.CTkFrame(self.target_body, fg_color='transparent')
        self._text(self.file_row, '目标位置', muted=True, anchor='w').pack(
            fill='x', pady=(0, 6))
        line = ctk_kit.CTkFrame(self.file_row, fg_color='transparent')
        line.pack(fill='x')
        self.file_entry = ctk_kit.SearchBox(
            line, placeholder='输入或选择程序文件绝对路径',
            command=self._target_command, delay=0)
        # SearchBox 内部按组件令牌建输入框；页面正文统一 FONT_BODY。
        self.file_entry._entry.configure(font=FONT_BODY)
        # 放大镜只是图标不响应点击，文件选择走旁边的紧凑按钮。
        self._bind_paste(self.file_entry)
        self.file_entry.pack(side='left', fill='x', expand=True)
        self.browse_button = ctk_kit.SecondaryButton(
            line, '浏览', self._browse, width=76, height=38,
            font=FONT_BODY, border=True)
        self.browse_button.pack(side='left', padx=(8, 0))
        self.file_variable = tk.StringVar(master=self)
        self.fields[('monitor', 'target')] = (
            self.file_variable, self.file_entry._entry, 'text')

    def _build_task_target(self):
        """计划任务目标：完整路径单行输入，不参与本地文件选择。"""
        self.task_row = ctk_kit.CTkFrame(self.target_body, fg_color='transparent')
        self._text(self.task_row, '计划任务完整路径', muted=True, anchor='w').pack(
            fill='x', pady=(0, 6))
        self.task_entry = ctk_kit.CTkEntry(self.task_row, font=FONT_BODY)
        self.task_variable = tk.StringVar(master=self)
        self.task_entry.configure(textvariable=self.task_variable)
        self.task_entry.pack(fill='x')
        self.task_variable.trace_add('write', lambda *_: self._task_changed())

    @staticmethod
    def _bind_paste(box):
        """右键或 Ctrl+V 粘贴不产生 KeyRelease，补一次写回。

        SearchBox 只在按键时触发防抖回调；``set()`` 仍然静默，供刷新使用。
        """
        for event in ('<<Paste>>', '<<Cut>>', '<<Clear>>'):
            box._entry.bind(
                event, lambda _event, current=box: current.after_idle(current.search),
                add='+')

    def _build_time_card(self):
        """时间设置：五个既有字段，任务回溯改用整数步进。"""
        card = self._card('时间设置')
        for label, path in (
                ('等待时限', ('monitor', 'common', 'max_wait')),
                ('超时告警间隔', ('monitor', 'common', 'timeout_interval')),
                ('检查间隔', ('monitor', 'common', 'check_interval')),
                ('轮询间隔', ('monitor', 'common', 'loop_interval'))):
            self._field(card, label, path, padx=CARD_PAD)
        path = ('monitor', 'watch', 'lookback_minutes')
        row = ctk_kit.CTkFrame(card, fg_color='transparent')
        row.pack(fill='x', padx=CARD_PAD, pady=(10, CARD_PAD))
        self._text(row, '任务回溯（分钟）', muted=True, width=LABEL_WIDTH,
                   anchor='w').pack(side='left')
        self.lookback_variable = tk.StringVar(master=self)
        self.lookback = ctk_kit.Spinner(
            row, from_=0, to=999, as_int=True,
            command=lambda _value: self._lookback_changed())
        self.lookback.entry._entry.unbind('<Return>')
        self.lookback.entry._entry.unbind('<FocusOut>')
        self.lookback.entry.configure(font=FONT_BODY,
                                      textvariable=self.lookback_variable)
        self.lookback_variable.trace_add('write', lambda *_: self._lookback_changed())
        self.lookback._arrows.bind('<Button-1>', self._step_lookback)
        self.lookback.pack(side='left')
        self.fields[path] = (self.lookback_variable, self.lookback, 'int')

    def _build_advanced(self):
        """不能定位到可见字段的问题统一落到 YAML 提示。"""
        self.section_for_issue = ctk_kit.CTkFrame(
            self, corner_radius=CARD_RADIUS, border_width=CARD_BORDER,
            border_color=CARD_BORDER_COLOR[self._dark],
            fg_color=CARD_SURFACE[self._dark])
        self.section_for_issue.pack(fill='x', padx=PAGE_PAD, pady=(CARD_GAP, 0))
        self.section_for_issue.button = ctk_kit.CTkButton(
            self.section_for_issue, text='高级字段请在 YAML 中编辑', font=FONT_BODY)
        self.section_for_issue.button.pack(fill='x', padx=CARD_PAD, pady=CARD_PAD)
        self.section_for_issue.expanded = False
        self.section_for_issue.set_expanded = self._set_advanced

    def _field(self, parent, label, path, *, optional=False, stacked=False,
               padx=0):
        """绑定原值输入，仅由用户事件写回模型。"""
        row = ctk_kit.CTkFrame(parent, fg_color='transparent')
        row.pack(fill='x', padx=padx, pady=(10, 0))
        if stacked:
            self._text(row, label, muted=True, anchor='w').pack(
                fill='x', pady=(0, 6))
            entry = ctk_kit.CTkEntry(row, font=FONT_BODY)
            entry.pack(fill='x')
        else:
            self._text(row, label, muted=True, width=LABEL_WIDTH,
                       anchor='w').pack(side='left')
            entry = ctk_kit.CTkEntry(row, font=FONT_BODY)
            entry.pack(side='left', fill='x', expand=True)
        variable = tk.StringVar(master=self)
        entry.configure(textvariable=variable)
        variable.trace_add('write', lambda *_: self._changed(path, entry, optional))
        self.fields[path] = (variable, entry, 'text')
        return entry

    def _changed(self, path, entry, optional):
        """将已修改的用户原文交给模型。"""
        if self._refreshing or not path_writable(self.model, path):
            return
        value = entry.get()
        self.model.set(path, None if optional and not value else value)

    # ---------------------------------------------------------------- 目标
    def _monitor(self):
        """返回映射类型的 monitor 节，缺失或结构异常时为空映射。"""
        monitor = self.model.snapshot().get('monitor')
        return monitor if isinstance(monitor, Mapping) else {}

    def _target_command(self, text):
        """SearchBox 的输入、清除与粘贴都从 command 写回草稿。"""
        if self._refreshing or self._target_type != 'file':
            return
        self.file_variable.set(text)
        self._target_changed(text)

    def _task_changed(self):
        """计划任务输入走同一目标写回路径。"""
        if self._refreshing or self._target_type != 'task':
            return
        self._target_changed(self.task_variable.get())

    def _target_changed(self, value):
        """对启动文件目标的主动编辑同步工作目录。"""
        if self._refreshing or not path_writable(self.model, ('monitor', 'target')):
            return
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
            text = str(value)
            if self._target_type == 'task':
                self.task_entry.delete(0, 'end')
                self.task_entry.insert(0, text)
            else:
                self.file_entry.set(text)
                self.file_variable.set(text)
                self._target_changed(text)
            return
        entry = self.fields[path][1]
        if entry is self.lookback:
            self.lookback.set(str(value))
            self._lookback_changed()
            return
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
        clear_args = (self._target_type == 'task'
                      and self._monitor().get('mode') == 'launch'
                      and path_writable(self.model, ('monitor', 'launch', 'args')))
        if clear_args:
            self.model.set(('monitor', 'launch', 'args'), None)
        target = self.model.get(('monitor', 'target'))
        text = '' if target is None else str(target)
        self._refreshing = True
        try:
            self.file_variable.set(text)
            self.task_variable.set(text)
            if self._target_type == 'task':
                self.file_entry.set(text)
            else:
                self.task_entry.delete(0, 'end')
                self.task_entry.insert(0, text)
            if clear_args:
                self.args_entry.delete(0, 'end')
        finally:
            self._refreshing = False
        self.fields[('monitor', 'target')] = (
            self.task_variable if self._target_type == 'task' else self.file_variable,
            self._target_widget(), 'text')
        self._show_target()
        self._update_preview()

    def _target_widget(self):
        """当前目标的可编辑输入，供字段登记与焦点定位使用。"""
        return self.task_entry if self._target_type == 'task' else self.file_entry._entry

    def _show_target(self):
        """仅展示当前类型输入与适用的启动参数。"""
        for row in (self.file_row, self.task_row, self.args_row):
            row.pack_forget()
        if self._target_type == 'task':
            self.task_row.pack(fill='x')
        else:
            self.file_row.pack(fill='x')
            if self._monitor().get('mode') == 'launch':
                self.args_row.pack(fill='x')

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

    def _lookback_changed(self):
        """任务回溯保留用户原文，非法草稿仍交给模型记录。"""
        path = ('monitor', 'watch', 'lookback_minutes')
        if self._refreshing or not path_writable(self.model, path):
            return
        raw = self.lookback.entry.get()
        self.lookback_variable.set(raw)
        self.model.set_input(path, raw, kind='int')

    def _step_lookback(self, event):
        """只有完整整数才允许箭头在既有范围内前进一步。"""
        if self.lookback.is_readonly():
            return
        raw = self.lookback.entry.get()
        if not re.fullmatch(r'[+-]?\d+', raw.strip()):
            return
        middle = max(1, self.lookback._arrows.winfo_height()) / 2
        delta = 1 if event.y < middle else -1
        value = max(0, min(999, int(raw) + delta))
        self.lookback.set(value)

    def _set_advanced(self, expanded):
        """保持旧页面问题定位接口且不显示 ttk 控件。"""
        self.section_for_issue.expanded = expanded

    def refresh(self):
        """同步模型值，打开已有任务不触发用户切换逻辑。"""
        self._refreshing = True
        try:
            self.file_entry.set_enabled(True)
            self.task_entry.configure(state='normal')
            self.lookback.set_readonly(False)
            monitor = self._monitor()
            mode = monitor.get('mode')
            self.mode.set('主动启动并监视' if mode == 'launch' else '等待目标运行')
            target = monitor.get('target')
            text = '' if target is None else str(target)
            try:
                kind = resolve_monitor_target(mode, target, self._config_dir()).kind
            except (ValueError, TypeError):
                kind = self._target_type
            self._target_type = 'task' if kind == 'task' else 'file'
            self.target_type.set('计划任务' if self._target_type == 'task' else '程序文件')
            self.file_variable.set(text)
            self.file_entry.set(text)
            self.task_variable.set(text)
            self.fields[('monitor', 'target')] = (
                self.task_variable if self._target_type == 'task' else self.file_variable,
                self._target_widget(), 'text')
            for path, (variable, widget, kind) in self.fields.items():
                if path == ('monitor', 'target'):
                    continue
                try:
                    value = self.model.get_input(path) if kind == 'int' else self.model.get(path)
                except (KeyError, IndexError, TypeError):
                    value = ''
                raw = '' if value is None else str(value)
                variable.set(raw)
                if widget is self.lookback and widget.entry.get() != raw:
                    self.lookback.set(raw)
            self._show_target()
            self._update_preview()
            self.section_for_issue.button.configure(
                text='高级字段请在 YAML 中编辑' +
                     ('（已配置）' if self._configured_advanced() else ''))
            writable = {path: path_writable(self.model, path) for path in self.fields}
            for path, (_variable, widget, _kind) in self.fields.items():
                if path == ('monitor', 'target'):
                    continue
                if widget is self.lookback:
                    widget.set_readonly(not writable[path])
                else:
                    widget.configure(state='normal' if writable[path] else 'disabled')
            target_state = writable.get(('monitor', 'target'), False)
            self.file_entry.set_enabled(target_state)
            self.task_entry.configure(state='normal' if target_state else 'disabled')
            self.browse_button.configure(state='normal' if target_state else 'disabled')
            self.mode.configure(
                state='normal' if path_writable(self.model, ('monitor', 'mode')) else 'disabled')
            self.target_type.configure(state='normal' if target_state else 'disabled')
            hint = '' if all(writable.values()) else STRUCTURE_HINT
            self.structure_hint.configure(text=hint)
            if hint:
                self.structure_hint.pack(fill='x', padx=PAGE_PAD, pady=(6, 0),
                                         before=self._mode_card)
            else:
                self.structure_hint.pack_forget()
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
        widget = entry[1] if entry else self.section_for_issue.button
        if widget is self.lookback:
            widget = self.lookback.entry
        while hasattr(widget, '_entry'):
            widget = widget._entry
        self.widget_for_issue = widget
        scroll_to_widget(self, widget)
        activate_and_focus(widget)
