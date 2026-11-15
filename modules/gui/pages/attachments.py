#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""截图、图床与外部动作表单。"""

from collections.abc import Mapping
from io import StringIO
import tkinter as tk
from tkinter import ttk

from ruamel.yaml import YAML

from modules.config_rules import Issue
from modules.gui.forms import screenshot_conditions
from modules.gui.pages.base import Page as BasePage


class Page(BasePage):
    """编辑截图目标及后续动作但不运行目标。"""

    def __init__(self, parent, model):
        """创建基础目标列表和独立高级选项。"""
        super().__init__(parent, model)
        self.targets_path = ('push', 'screenshot', 'targets')
        self.hosts_path = ('push', 'screenshot', 'image_host')
        self.action_drafts = {}
        self.action_generation = model.document_generation
        self.field(self.body, '超时触发阈值', ('monitor', 'common', 'timeout_threshold'), kind='int')
        for event in ('on_end', 'on_timeout', 'on_wait_timeout'):
            self.field(self.body, event + ' 外部程序', ('external', event))
            row = ttk.Frame(self.body)
            row.pack(fill='x')
            ttk.Button(row, text=event + ' 停用', command=lambda key=event:
                       self.disable_action(key)).pack(side='left')
            ttk.Button(row, text=event + ' 撤销停用', command=lambda key=event:
                       self.restore_action(key)).pack(side='left')
        self.condition_labels = {}
        for event in ('on_end', 'on_timeout', 'on_wait_timeout', 'on_external'):
            label = ttk.Label(self.body, wraplength=650)
            label.pack(fill='x')
            self.condition_labels[event] = label
        self.target_list = ttk.Combobox(self.body, state='readonly')
        self.target_list.pack(fill='x')
        self.target_list.bind('<<ComboboxSelected>>', lambda _: self._selected_target())
        target_row = ttk.Frame(self.body)
        target_row.pack(fill='x')
        ttk.Button(target_row, text='添加窗口截图', command=lambda: self._add_target('window')).pack(side='left')
        ttk.Button(target_row, text='添加 ADB 截图', command=lambda: self._add_target('adb')).pack(side='left')
        ttk.Button(target_row, text='删除目标', command=self._remove_target).pack(side='left')
        self.target_body = ttk.Frame(self.body)
        self.target_body.pack(fill='x')
        self.target_entries = {}
        self.section_for_issue.pack(fill='x')
        self.field(self.section_for_issue.body, '截图保留天数', ('push', 'screenshot', 'retention', 'max_days'), kind='int')
        self.field(self.section_for_issue.body, '自动清理', ('push', 'screenshot', 'retention', 'enabled'), kind='bool')
        self.host_list = ttk.Combobox(self.section_for_issue.body, state='readonly')
        self.host_list.pack(fill='x')
        self.host_list.bind('<<ComboboxSelected>>', lambda _: self._selected_host())
        self.host_body = ttk.Frame(self.section_for_issue.body)
        self.host_body.pack(fill='x')
        self.host_entries = {}
        self.host_error = ttk.Label(self.host_body, foreground='red')
        self.host_error.pack(anchor='w')
        self.invalid_options = {}
        self.options_generation = model.document_generation
        ttk.Button(self.section_for_issue.body, text='添加图床', command=self._add_host).pack(anchor='w')
        ttk.Button(self.section_for_issue.body, text='删除图床', command=self._remove_host).pack(anchor='w')
        ttk.Button(self.section_for_issue.body, text='图床上移', command=lambda:
                   self.move_host(-1)).pack(anchor='w')
        ttk.Button(self.section_for_issue.body, text='图床下移', command=lambda:
                   self.move_host(1)).pack(anchor='w')
        self.refresh()

    def disable_action(self, event):
        """停用动作时仅在内存保留可撤销的路径。"""
        path = ('external', event)
        value = self.model.get(path)
        if value:
            self.action_drafts[event] = value
            self.set_field(path, '')

    def restore_action(self, event):
        """只恢复当前文档曾停用的动作。"""
        value = self.action_drafts.pop(event, None)
        if value is not None:
            self.set_field(('external', event), value)

    def move_host(self, offset):
        """交换图床槽位，并保持正在编辑的条目被选中。"""
        index = self.host_list.current()
        hosts = self.model.get(self.hosts_path)
        other = index + offset
        if index < 0 or not 0 <= other < len(hosts):
            return
        hosts[index], hosts[other] = hosts[other], hosts[index]
        for mapping in (self.invalid_options, self.model.input_errors):
            first = index if mapping is self.invalid_options else (*self.hosts_path, index, 'options')
            second = other if mapping is self.invalid_options else (*self.hosts_path, other, 'options')
            left = mapping.pop(first, None)
            right = mapping.pop(second, None)
            if left is not None:
                mapping[second] = left if mapping is self.invalid_options else Issue(
                    second, left.code, left.severity, left.message)
            if right is not None:
                mapping[first] = right if mapping is self.invalid_options else Issue(
                    first, right.code, right.severity, right.message)
        self.model.set(self.hosts_path, hosts)
        self.host_list.current(other)
        self.refresh()

    def _add_target(self, provider):
        """追加独立截图目标。"""
        targets = self.model.get(self.targets_path)
        targets.append({'provider': provider, 'target': '', 'out': ''})
        self.model.set(self.targets_path, targets)
        self.refresh()

    def _remove_target(self):
        """删除选中的截图目标。"""
        index = self.target_list.current()
        if index >= 0:
            targets = self.model.get(self.targets_path)
            targets.pop(index)
            self.model.set(self.targets_path, targets)
            self.refresh()

    def _selected_target(self):
        """展示目标名称及输出名编辑框。"""
        for child in self.target_body.winfo_children():
            child.destroy()
        self.target_entries = {}
        index = self.target_list.current()
        targets = self.model.get(self.targets_path)
        if index < 0 or index >= len(targets):
            return
        for name in ('provider', 'target', 'out'):
            ttk.Label(self.target_body, text=name).pack(anchor='w')
            entry = ttk.Entry(self.target_body)
            self.target_entries[name] = entry
            entry.insert(0, str(targets[index].get(name, '')))
            entry.pack(fill='x')
            entry.bind('<FocusOut>', lambda _, key=name, widget=entry:
                       self._set_item(self.targets_path, index, key, widget.get()))

    def _selected_host(self):
        """展示单个图床的凭证、期限及参数。"""
        for child in self.host_body.winfo_children():
            child.destroy()
        self.host_entries = {}
        self.host_error = ttk.Label(self.host_body, foreground='red')
        index = self.host_list.current()
        hosts = self.model.get(self.hosts_path)
        if index < 0 or index >= len(hosts):
            return
        for name in ('provider', 'token', 'expiration', 'options'):
            ttk.Label(self.host_body, text=name).pack(anchor='w')
            variable = tk.StringVar(master=self.host_body)
            entry = ttk.Entry(self.host_body, textvariable=variable,
                              show='●' if name in ('token', 'options') else '')
            self.host_entries[name] = entry
            value = hosts[index].get(name, '')
            if name == 'options' and isinstance(value, dict):
                stream = StringIO()
                yaml = YAML()
                yaml.default_flow_style = True
                try:
                    yaml.dump(value, stream)
                    value = stream.getvalue().strip()
                except Exception:
                    value = '（无法显示的选项）'
            if name == 'options':
                value = self.invalid_options.get(index, value)
            entry.insert(0, str(value))
            entry.pack(fill='x')
            variable.trace_add('write', lambda *_, key=name, field=variable:
                               self._set_item(self.hosts_path, index, key, field.get()))
            if name == 'options':
                ttk.Button(self.host_body, text='显示/隐藏选项', command=lambda widget=entry:
                           widget.configure(show='' if widget.cget('show') else '●')).pack(anchor='w')
        self.host_error.configure(text='options 必须是有效 YAML 映射' if index in self.invalid_options else '')
        self.host_error.pack(anchor='w')

    def _set_item(self, path, index, name, value):
        """只写选定列表项，其他项保持原顺序。"""
        items = self.model.get(path)
        if name == 'options':
            try:
                parsed = YAML(typ='safe').load(value) if value.strip() else {}
            except Exception:
                parsed = None
            issue_path = (*path, index, name)
            if not isinstance(parsed, dict):
                self.invalid_options[index] = value
                self.model.input_errors[issue_path] = Issue(
                    issue_path, 'invalid_options', 'error', 'options 必须是有效 YAML 映射')
                self.host_error.configure(text='options 必须是有效 YAML 映射')
                return
            self.invalid_options.pop(index, None)
            self.model.input_errors.pop(issue_path, None)
            self.host_error.configure(text='')
            value = parsed
        items[index][name] = value
        self.model.set(path, items)
        if path == self.hosts_path and name == 'provider':
            self.host_list.configure(values=[f'{i + 1}. {item.get("provider", "")}'
                                             for i, item in enumerate(items)])
            self.host_list.current(index)

    def _add_host(self):
        """在图床回退列表末尾追加条目。"""
        hosts = self.model.get(self.hosts_path)
        hosts.append({'provider': 'catbox', 'token': ''})
        self.model.set(self.hosts_path, hosts)
        self.refresh()

    def _remove_host(self):
        """删除选定图床。"""
        index = self.host_list.current()
        if index >= 0:
            hosts = self.model.get(self.hosts_path)
            hosts.pop(index)
            self.invalid_options = {key - (key > index): value
                                    for key, value in self.invalid_options.items() if key != index}
            self.model.input_errors = {
                (*self.hosts_path, key[len(self.hosts_path)] - (key[len(self.hosts_path)] > index), 'options'):
                Issue((*self.hosts_path, key[len(self.hosts_path)] - (key[len(self.hosts_path)] > index), 'options'),
                      issue.code, issue.severity, issue.message)
                for key, issue in self.model.input_errors.items()
                if key != (*self.hosts_path, index, 'options')}
            self.model.set(self.hosts_path, hosts)
            self.refresh()

    def refresh(self):
        """刷新独立目标和图床列表。"""
        if self.options_generation != self.model.document_generation:
            self.invalid_options.clear()
            self.options_generation = self.model.document_generation
        if self.action_generation != self.model.document_generation:
            self.action_drafts.clear()
            self.action_generation = self.model.document_generation
        super().refresh()
        snapshot = self.model.snapshot()
        templates = snapshot['push']['templates']
        for event, label in self.condition_labels.items():
            if not isinstance(templates, Mapping) or not isinstance(templates.get(event), Mapping):
                label.configure(text=f'{event} 截图条件：待校验')
                continue
            conditions = screenshot_conditions(snapshot, event)
            text = '；'.join(f'{name}：{"已满足" if valid else "未满足"}' for name, valid in (
                ('事件开关', conditions['enabled']), ('通道', conditions['channels']),
                ('引用', conditions['references']), ('目标', conditions['targets'])))
            label.configure(text=f'{event} 截图条件：{text}')
        selected_target = self.target_list.current()
        selected_host = self.host_list.current()
        targets = self.model.get(self.targets_path)
        self.target_list.configure(values=[f'{i + 1}. {item.get("provider", "")} {item.get("target", "")}'
                                           for i, item in enumerate(targets)])
        if targets:
            self.target_list.current(min(max(selected_target, 0), len(targets) - 1))
        self._selected_target()
        hosts = self.model.get(self.hosts_path)
        self.host_list.configure(values=[f'{i + 1}. {item.get("provider", "")}'
                                         for i, item in enumerate(hosts)])
        if hosts:
            self.host_list.current(min(max(selected_host, 0), len(hosts) - 1))
        self._selected_host()

    def focus_issue(self, path):
        """列表字段先切换槽位，再聚焦当前条目的对应输入。"""
        if path[:len(self.hosts_path)] == self.hosts_path:
            selector, select = self.host_list, self._selected_host
            prefix = len(self.hosts_path)
            section = self.section_for_issue
        elif path[:len(self.targets_path)] == self.targets_path:
            selector, select = self.target_list, self._selected_target
            prefix = len(self.targets_path)
            section = None
        else:
            super().focus_issue(path)
            return
        items = self.model.get(path[:prefix])
        if len(path) > prefix and isinstance(path[prefix], int) and 0 <= path[prefix] < len(items):
            selector.current(path[prefix])
            select()
        if section is not None:
            section.set_expanded(True)
        entries = self.host_entries if section is not None else self.target_entries
        widget = entries.get(path[prefix + 1] if len(path) > prefix + 1 else 'provider', selector)
        self.focus_widget(widget)
