#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""通知通道与模板表单。"""

import tkinter as tk
from tkinter import messagebox, ttk

from modules.gui.forms import event_variables, preview_template, provider_fields
from modules.gui.pages.base import Page as BasePage


EVENTS = ('on_end', 'on_timeout', 'on_wait_timeout', 'on_external')


class Page(BasePage):
    """管理已安装 OnePush 通道与四类通知事件。"""

    def __init__(self, parent, model):
        """创建列表、模板及折叠的可选项。"""
        super().__init__(parent, model)
        self.channels_path = ('push', 'push_channel_settings', 'channels')
        self.channel_list = ttk.Combobox(self.body, state='readonly')
        self.channel_list.pack(fill='x')
        self.channel_list.bind('<<ComboboxSelected>>', lambda _: self._channel_fields())
        toolbar = ttk.Frame(self.body)
        toolbar.pack(fill='x')
        ttk.Button(toolbar, text='添加通道', command=self._add).pack(side='left')
        ttk.Button(toolbar, text='删除通道', command=self._remove).pack(side='left')
        self.channel_body = ttk.Frame(self.body)
        self.channel_body.pack(fill='x')
        self.channel_entries = {}
        ttk.Label(self.body, text='通知事件').pack(anchor='w')
        for event in EVENTS:
            self.field(self.body, event + ' 启用', ('push', 'templates', event, 'enable'), kind='bool')
            self.field(self.body, event + ' 标题', ('push', 'templates', event, 'title'))
        self.selected_event = ttk.Combobox(self.body, values=EVENTS, state='readonly')
        self.selected_event.pack(fill='x')
        self.selected_event.current(0)
        ttk.Button(self.body, text='模拟预览', command=self._show_preview).pack(anchor='w')
        self.variable = ttk.Combobox(self.body, state='readonly')
        self.variable.pack(fill='x')
        self.selected_event.bind('<<ComboboxSelected>>', lambda _: self._update_variables())
        self._update_variables()
        ttk.Button(self.body, text='插入变量', command=self._insert_selected).pack(anchor='w')
        self.section_for_issue.pack(fill='x')
        for event in EVENTS:
            path = ('push', 'templates', event)
            self.field(self.section_for_issue.body, event + ' 正文', (*path, 'content'), kind='multiline')
            self.field(self.section_for_issue.body, event + ' 截图', (*path, 'capture_screenshot'), kind='bool')
        self.field(self.section_for_issue.body, '重试间隔', ('push', 'retry', 'interval'))
        self.field(self.section_for_issue.body, '重试次数', ('push', 'retry', 'max_count'), kind='int')
        self.refresh()

    def _add(self):
        """追加一个真实 provider 的通道。"""
        if not self.model.before_list_edit():
            return
        channels = self.model.get(self.channels_path)
        channels.append({'provider': 'serverchan', 'sckey': ''})
        self.model.set(self.channels_path, channels)
        self.refresh()
        self.channel_list.current(len(channels) - 1)
        self._channel_fields()
        self.model.on_list_edit()

    def _remove(self):
        """移除当前选中通道，不触碰其它通道。"""
        index = self.channel_list.current()
        if index < 0:
            return
        if not self.model.before_list_edit():
            return
        channels = self.model.get(self.channels_path)
        channels.pop(index)
        self.model.set(self.channels_path, channels)
        self.refresh()
        self.model.on_list_edit()

    def _channel_fields(self):
        """动态展示所选 provider 的必要字段并遮挡凭证。"""
        for child in self.channel_body.winfo_children():
            child.destroy()
        self.channel_entries = {}
        index = self.channel_list.current()
        channels = self.model.get(self.channels_path)
        if index < 0 or index >= len(channels):
            return
        channel = channels[index]
        row = ttk.Frame(self.channel_body)
        row.pack(fill='x')
        ttk.Label(row, text='通道类型').pack(side='left')
        provider_value = tk.StringVar(master=row, value=channel.get('provider', ''))
        provider = ttk.Entry(row, textvariable=provider_value)
        self.channel_entries['provider'] = provider
        provider.pack(side='left', fill='x', expand=True)
        provider_value.trace_add('write', lambda *_: self._set_channel(index, 'provider', provider_value.get()))
        provider.bind('<FocusOut>', lambda _: self.refresh())
        for field in provider_fields(channel.get('provider', '')):
            row = ttk.Frame(self.channel_body)
            row.pack(fill='x')
            ttk.Label(row, text=field.label + (' *' if field.required else '')).pack(side='left')
            value = tk.StringVar(master=row, value=str(channel.get(field.name, '')))
            entry = ttk.Entry(row, textvariable=value, show='●' if field.secret else '')
            self.channel_entries[field.name] = entry
            entry.pack(side='left', fill='x', expand=True)
            value.trace_add('write', lambda *_, name=field.name, variable=value:
                            self._set_channel(index, name, variable.get()))
            if field.secret:
                ttk.Button(row, text='显示', command=lambda widget=entry:
                           widget.configure(show='' if widget.cget('show') else '●')).pack(side='left')

    def _set_channel(self, index, name, value):
        """将选定通道字段写回原列表。"""
        channels = self.model.get(self.channels_path)
        channels[index][name] = value
        self.model.set(self.channels_path, channels)

    def preview_event(self, event):
        """只渲染模拟通知，不发送网络请求。"""
        return preview_template(self.model.snapshot(), event)

    def _show_preview(self):
        """通过原生窗口展示显式模拟的通知内容。"""
        try:
            title, content = self.preview_event(self.selected_event.get())
        except ValueError:
            messagebox.showerror('模板问题', '通知模板变量或格式无效', parent=self)
            return
        messagebox.showinfo(title, content, parent=self)

    def _update_variables(self):
        """只列当前事件有效的可插入变量。"""
        names = [*event_variables(self.selected_event.get()), 'screenshot']
        self.variable.configure(values=names)
        self.variable.set(names[0])

    def _insert_selected(self):
        """按用户当前选择显式插入正文变量。"""
        self.insert_variable(self.selected_event.get(), self.variable.get())

    def insert_variable(self, event, variable):
        """仅在用户明确选择合法变量时插入到正文。"""
        if variable not in event_variables(event) and variable != 'screenshot':
            raise ValueError('未知通知变量')
        path = ('push', 'templates', event, 'content')
        self.model.set(path, self.model.get(path) + '{' + variable + '}')
        self.refresh()

    def refresh(self):
        """刷新通道摘要和模板字段。"""
        super().refresh()
        selected = self.channel_list.current()
        channels = self.model.get(self.channels_path)
        self.channel_list.configure(values=[f'{i + 1}. {item.get("provider", "未知")}'
                                            for i, item in enumerate(channels)])
        if channels and selected >= 0:
            self.channel_list.current(min(selected, len(channels) - 1))
        elif not channels:
            self.channel_list.set('无通道：不会发送通知')
        self._channel_fields()

    def focus_issue(self, path):
        """通道错误按列表索引切换并聚焦实际参数。"""
        if path[:len(self.channels_path)] != self.channels_path:
            super().focus_issue(path)
            return
        index_at = len(self.channels_path)
        channels = self.model.get(self.channels_path)
        if len(path) > index_at and isinstance(path[index_at], int) and 0 <= path[index_at] < len(channels):
            self.channel_list.current(path[index_at])
            self._channel_fields()
        name = path[index_at + 1] if len(path) > index_at + 1 else 'provider'
        self.focus_widget(self.channel_entries.get(name, self.channel_list))
