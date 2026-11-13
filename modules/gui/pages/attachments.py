#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""截图、图床与外部动作表单。"""

from tkinter import ttk

from ruamel.yaml import YAML

from modules.gui.pages.base import Page as BasePage


class Page(BasePage):
    """编辑截图目标及后续动作但不运行目标。"""

    def __init__(self, parent, model):
        """创建基础目标列表和独立高级选项。"""
        super().__init__(parent, model)
        self.targets_path = ('push', 'screenshot', 'targets')
        self.hosts_path = ('push', 'screenshot', 'image_host')
        self.field(self.body, '超时触发阈值', ('monitor', 'common', 'timeout_threshold'), kind='int')
        for event in ('on_end', 'on_timeout', 'on_wait_timeout'):
            self.field(self.body, event + ' 外部程序', ('external', event))
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
        self.section_for_issue.pack(fill='x')
        self.field(self.section_for_issue.body, '截图保留天数', ('push', 'screenshot', 'retention', 'max_days'), kind='int')
        self.field(self.section_for_issue.body, '自动清理', ('push', 'screenshot', 'retention', 'enabled'), kind='bool')
        self.host_list = ttk.Combobox(self.section_for_issue.body, state='readonly')
        self.host_list.pack(fill='x')
        self.host_list.bind('<<ComboboxSelected>>', lambda _: self._selected_host())
        self.host_body = ttk.Frame(self.section_for_issue.body)
        self.host_body.pack(fill='x')
        ttk.Button(self.section_for_issue.body, text='添加图床', command=self._add_host).pack(anchor='w')
        ttk.Button(self.section_for_issue.body, text='删除图床', command=self._remove_host).pack(anchor='w')
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
        index = self.target_list.current()
        targets = self.model.get(self.targets_path)
        if index < 0 or index >= len(targets):
            return
        for name in ('provider', 'target', 'out'):
            entry = ttk.Entry(self.target_body)
            entry.insert(0, str(targets[index].get(name, '')))
            entry.pack(fill='x')
            entry.bind('<FocusOut>', lambda _, key=name, widget=entry:
                       self._set_item(self.targets_path, index, key, widget.get()))

    def _selected_host(self):
        """展示单个图床的凭证、期限及参数。"""
        for child in self.host_body.winfo_children():
            child.destroy()
        index = self.host_list.current()
        hosts = self.model.get(self.hosts_path)
        if index < 0 or index >= len(hosts):
            return
        for name in ('provider', 'token', 'expiration', 'options'):
            entry = ttk.Entry(self.host_body, show='●' if name == 'token' else '')
            value = hosts[index].get(name, '')
            if name == 'options' and isinstance(value, dict):
                value = ', '.join(f'{key}: {item}' for key, item in value.items())
            entry.insert(0, str(value))
            entry.pack(fill='x')
            entry.bind('<FocusOut>', lambda _, key=name, widget=entry:
                       self._set_item(self.hosts_path, index, key, widget.get()))

    def _set_item(self, path, index, name, value):
        """只写选定列表项，其他项保持原顺序。"""
        items = self.model.get(path)
        if name == 'options':
            try:
                parsed = YAML(typ='safe').load(value) if value.strip() else {}
            except Exception:
                return
            if not isinstance(parsed, dict):
                return
            value = parsed
        items[index][name] = value
        self.model.set(path, items)

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
            self.model.set(self.hosts_path, hosts)
            self.refresh()

    def refresh(self):
        """刷新独立目标和图床列表。"""
        super().refresh()
        targets = self.model.get(self.targets_path)
        self.target_list.configure(values=[f'{i + 1}. {item.get("provider", "")} {item.get("target", "")}'
                                           for i, item in enumerate(targets)])
        if targets:
            self.target_list.current(0)
        self._selected_target()
        hosts = self.model.get(self.hosts_path)
        self.host_list.configure(values=[f'{i + 1}. {item.get("provider", "")}'
                                         for i, item in enumerate(hosts)])
        if hosts:
            self.host_list.current(0)
        self._selected_host()
