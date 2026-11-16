#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""ctk-kit 截图目标、图床与本地保留设置页。"""

import re
from io import StringIO
import tkinter as tk
from tkinter import messagebox

import ctk_kit
from ruamel.yaml import YAML
import win32gui

from modules.config_rules import Issue

from modules.gui.appearance import COLORS, FONT_BODY, FONT_CARD_TITLE, FONT_SMALL, FONT_TITLE
from modules.gui.target_order import delete_screenshot_target, move_screenshot_target
from modules.gui.widgets import scroll_to_widget


TARGETS = ('push', 'screenshot', 'targets')
HOSTS = ('push', 'screenshot', 'image_host')
RETENTION = ('push', 'screenshot', 'retention')
PROVIDERS = ('wmimg', 'beeimg', 'beeimg_cn', 'superbed', 'boltp')


class Page(ctk_kit.CTkScrollableFrame):
    """只编辑可见的基础字段，保留已有高级 YAML 项。"""

    def __init__(self, parent, app):
        """建立三组业务卡片与两个真实测试入口。"""
        super().__init__(parent, corner_radius=0)
        self.app = app
        self.model = app.model
        self.target_rows = []
        self.host_rows = []
        self.invalid_options = {}
        self.generation = self.model.document_generation
        header = ctk_kit.CTkFrame(self, fg_color='transparent')
        header.pack(fill='x', pady=(0, 12))
        ctk_kit.CTkLabel(header, text='截图设置', font=FONT_TITLE).pack(side='left')
        ctk_kit.CTkButton(header, text='上传测试', command=app.upload, font=FONT_BODY).pack(
            side='right', padx=4)
        ctk_kit.CTkButton(header, text='截图测试', command=app.capture, font=FONT_BODY).pack(
            side='right', padx=4)
        targets_card = self._card('截图目标')
        ctk_kit.CTkButton(targets_card, text='添加目标', font=FONT_BODY,
                          command=self.add_target).pack(anchor='e', padx=16, pady=4)
        self.targets_body = ctk_kit.CTkFrame(targets_card, fg_color='transparent')
        self.targets_body.pack(fill='x', padx=16, pady=(4, 16))
        hosts_card = self._card('图床')
        ctk_kit.CTkButton(hosts_card, text='添加图床', font=FONT_BODY,
                          command=self.add_host).pack(anchor='e', padx=16, pady=4)
        self.hosts_body = ctk_kit.CTkFrame(hosts_card, fg_color='transparent')
        self.hosts_body.pack(fill='x', padx=16, pady=(4, 16))
        retention = self._card('本地保留')
        self.cleanup = ctk_kit.CTkSwitch(retention, text='清理过期自动截图', font=FONT_BODY,
                                         command=self._change_cleanup)
        self.cleanup.pack(anchor='w', padx=16, pady=8)
        ctk_kit.CTkLabel(retention, text='保留天数', font=FONT_BODY).pack(
            anchor='w', padx=16)
        self.days = self._stepper(retention, lambda delta: self._change_days(delta), 0)
        self.days.pack(anchor='w', padx=16, pady=(6, 16))
        self.refresh()

    def _card(self, title):
        """创建没有内部分割线的内容卡片。"""
        card = ctk_kit.CTkFrame(self, corner_radius=12)
        card.pack(fill='x', pady=8, padx=4)
        ctk_kit.CTkLabel(card, text=title, font=FONT_CARD_TITLE).pack(
            anchor='w', padx=16, pady=(12, 4))
        return card

    def _stepper(self, parent, action, number):
        """绘制窄编号与上下箭头。"""
        frame = ctk_kit.CTkFrame(parent, fg_color='transparent')
        frame.number = ctk_kit.CTkLabel(frame, text=f'{number:02d}', font=FONT_SMALL,
                                        text_color=COLORS['text_secondary'], width=38)
        frame.number.pack(side='left')
        arrows = ctk_kit.CTkFrame(frame, fg_color='transparent')
        arrows.pack(side='left')
        ctk_kit.CTkButton(arrows, text='▲', width=24, height=18,
                          command=lambda: action(-1)).pack()
        ctk_kit.CTkButton(arrows, text='▼', width=24, height=18,
                          command=lambda: action(1)).pack()
        return frame

    def _before(self):
        """优先补存现有编辑，若重载或冲突则停止本次列表操作。"""
        generation = self.model.document_generation
        return self.model.before_list_edit() and generation == self.model.document_generation

    def _commit_list(self):
        """列表编辑后按既有保存协调器立即落盘。"""
        self.refresh()
        self.model.on_list_edit()

    def add_target(self):
        """新增空窗口目标，留给用户填写完整标题。"""
        if not self._before():
            return
        targets = self.model.get(TARGETS)
        targets.append({'provider': 'window', 'target': ''})
        self.model.set(TARGETS, targets)
        self._commit_list()

    def move_target(self, index, offset):
        """整项目标重排并一次替换 push 草稿，阻止错误映射保存。"""
        if not 0 <= index + offset < len(self.model.get(TARGETS)) or not self._before():
            return
        try:
            updated = move_screenshot_target(self.model.snapshot(), index, offset)
        except ValueError as exc:
            messagebox.showerror('截图目标', str(exc), parent=self)
            return
        self.model.set(('push',), updated['push'])
        self._commit_list()

    def remove_target(self, index):
        """确认后原子删除目标及其消息引用。"""
        if not 0 <= index < len(self.model.get(TARGETS)) or not self._before():
            return
        if not messagebox.askyesno('删除截图目标',
                                   '删除该目标会同步移除其消息引用，是否继续？', parent=self):
            return
        try:
            updated = delete_screenshot_target(self.model.snapshot(), index)
        except ValueError as exc:
            messagebox.showerror('截图目标', str(exc), parent=self)
            return
        self.model.set(('push',), updated['push'])
        self._commit_list()

    def add_host(self):
        """新增默认 beeimg 图床，不改动旧 catbox。"""
        if not self._before():
            return
        hosts = self.model.get(HOSTS)
        hosts.append({'provider': 'beeimg', 'token': ''})
        self.model.set(HOSTS, hosts)
        self._commit_list()

    def move_host(self, index, offset):
        """移动图床完整配置及其隐含回退顺序。"""
        hosts = self.model.get(HOSTS)
        other = index + offset
        if not 0 <= other < len(hosts) or not self._before():
            return
        hosts[index], hosts[other] = hosts[other], hosts[index]
        for mapping in (self.invalid_options, self.model.input_errors):
            first = index if mapping is self.invalid_options else (*HOSTS, index, 'options')
            second = other if mapping is self.invalid_options else (*HOSTS, other, 'options')
            left = mapping.pop(first, None)
            right = mapping.pop(second, None)
            if left is not None:
                mapping[second] = left if mapping is self.invalid_options else Issue(
                    second, left.code, left.severity, left.message)
            if right is not None:
                mapping[first] = right if mapping is self.invalid_options else Issue(
                    first, right.code, right.severity, right.message)
        self.model.set(HOSTS, hosts)
        self._commit_list()

    def remove_host(self, index):
        """只删除指定图床。"""
        if not 0 <= index < len(self.model.get(HOSTS)) or not self._before():
            return
        hosts = self.model.get(HOSTS)
        hosts.pop(index)
        self.invalid_options = {key - (key > index): value
                                for key, value in self.invalid_options.items() if key != index}
        self.model.input_errors = {
            (*HOSTS, key[len(HOSTS)] - (key[len(HOSTS)] > index), 'options'):
            Issue((*HOSTS, key[len(HOSTS)] - (key[len(HOSTS)] > index), 'options'),
                  issue.code, issue.severity, issue.message)
            for key, issue in self.model.input_errors.items()
            if key != (*HOSTS, index, 'options')}
        self.model.set(HOSTS, hosts)
        self._commit_list()

    def _set_item(self, path, index, key, value):
        """仅改写指定基础字段，保留未知字段与图床专有参数。"""
        items = self.model.get(path)
        if key == 'options':
            try:
                parsed = YAML(typ='safe').load(value) if value.strip() else {}
            except Exception:
                parsed = None
            issue_path = (*path, index, key)
            if not isinstance(parsed, dict):
                self.invalid_options[index] = value
                self.model.input_errors[issue_path] = Issue(
                    issue_path, 'invalid_options', 'error', 'options 必须是有效 YAML 映射')
                self.host_rows[index]['error'].configure(text='options 必须是有效 YAML 映射')
                return
            self.invalid_options.pop(index, None)
            self.model.input_errors.pop(issue_path, None)
            self.host_rows[index]['error'].configure(text='')
            value = parsed
        if items[index].get(key) == value:
            return
        items[index][key] = value
        self.model.set(path, items)

    def _entry(self, parent, value, callback, *, secret=False):
        """建立粘贴也能触发回写的输入框。"""
        variable = tk.StringVar(master=self, value=value)
        entry = ctk_kit.CTkEntry(parent, textvariable=variable,
                                 show='●' if secret else '', font=FONT_BODY)
        entry.pack(side='left', fill='x', expand=True, padx=4)
        variable.trace_add('write', lambda *_: callback(variable.get()))
        return entry

    def _choose_window(self, index):
        """枚举可见窗口，取消或空列表不改变当前目标。"""
        titles = []

        def collect(hwnd, _argument):
            """收集有标题的可见窗口。"""
            title = win32gui.GetWindowText(hwnd)
            if title and win32gui.IsWindowVisible(hwnd):
                titles.append(title)

        win32gui.EnumWindows(collect, None)
        if not titles:
            messagebox.showinfo('窗口标题', '没有可选窗口，请手动输入标题。', parent=self)
            return
        dialog = ctk_kit.CTkToplevel(self)
        dialog.title('选择窗口标题')
        selected = ctk_kit.CTkOptionMenu(dialog, values=list(dict.fromkeys(titles)))
        selected.pack(fill='x', padx=16, pady=16)
        ctk_kit.CTkButton(dialog, text='选择', command=lambda: (
            self._set_item(TARGETS, index, 'target', selected.get()),
            dialog.destroy(), self.refresh())).pack(pady=(0, 16))

    def _show_target(self, index, item):
        """显示窗口搜索框或可编辑的 ADB 地址和数字端口。"""
        row = ctk_kit.CTkFrame(self.targets_body, fg_color='transparent')
        row.pack(fill='x', pady=4)
        stepper = self._stepper(row, lambda delta: self.move_target(index, delta), index + 1)
        stepper.pack(side='left')
        provider = item.get('provider', 'window')
        chooser = ctk_kit.CTkOptionMenu(row, values=['window', 'adb'], width=95,
                                         command=lambda value: self._change_provider(index, value))
        chooser.set(provider)
        chooser.pack(side='left', padx=4)
        entry = None
        port_entry = None
        if provider == 'window':
            ctk_kit.CTkButton(row, text='⌕', width=32,
                              command=lambda: self._choose_window(index)).pack(side='left')
            entry = self._entry(row, item.get('target', ''),
                                lambda value: self._set_item(TARGETS, index, 'target', value))
            ctk_kit.CTkButton(row, text='×', width=32,
                              command=lambda: entry.delete(0, 'end')).pack(side='left')
        else:
            address, port = self._split_adb(item.get('target', ''))
            self._entry(row, address, lambda value: self._set_adb(
                index, value, self._split_adb(self.model.get(TARGETS)[index].get('target', ''))[1]))
            port_value = tk.StringVar(master=self, value='' if port is None else str(port))
            port_entry = ctk_kit.CTkEntry(row, textvariable=port_value,
                                          width=74, font=FONT_BODY, placeholder_text='端口')
            port_entry.pack(side='left', padx=4)
            port_value.trace_add('write', lambda *_: self._edit_port(index, port_value.get()))
            ctk_kit.CTkButton(row, text='▲', width=24,
                              command=lambda: self._change_port(index, 1)).pack(side='left')
            ctk_kit.CTkButton(row, text='▼', width=24,
                              command=lambda: self._change_port(index, -1)).pack(side='left')
        ctk_kit.CTkButton(row, text='删除', width=60, text_color=('#B42318', '#FF7575'),
                          command=lambda: self.remove_target(index)).pack(side='right')
        self.target_rows.append({'number': stepper.number, 'entry': entry,
                                 'port': port_entry})

    def _change_provider(self, index, value):
        """主动修改来源时保留可编辑的原始目标。"""
        self._set_item(TARGETS, index, 'provider', value)
        self.refresh()

    @staticmethod
    def _split_adb(target):
        """仅拆分明确的地址数字端口，其余 serial 原文保留。"""
        match = re.fullmatch(r'(.+):(\d{1,5})', target)
        return (match.group(1), int(match.group(2))) if match else (target, None)

    def _set_adb(self, index, address, port):
        """把地址与数字端口还原成既有单个 target 字段。"""
        self._set_item(TARGETS, index, 'target',
                       f'{address}:{port}' if port is not None else address)

    def _edit_port(self, index, raw):
        """数字端口回写单个 target；未完成输入保留在输入框。"""
        if raw and not re.fullmatch(r'\d{1,5}', raw):
            return
        address, _port = self._split_adb(self.model.get(TARGETS)[index].get('target', ''))
        if raw and not 1 <= int(raw) <= 65535:
            return
        self._set_adb(index, address, int(raw) if raw else None)

    def _change_port(self, index, delta):
        """步进合法数字端口且保留地址。"""
        address, port = self._split_adb(self.model.get(TARGETS)[index].get('target', ''))
        if port is not None and 1 <= port + delta <= 65535:
            self._set_adb(index, address, port + delta)
            self.refresh()

    def _show_host(self, index, item):
        """图床行展示当前 provider 与遮挡凭证。"""
        row = ctk_kit.CTkFrame(self.hosts_body, fg_color='transparent')
        row.pack(fill='x', pady=4)
        stepper = self._stepper(row, lambda delta: self.move_host(index, delta), index + 1)
        stepper.pack(side='left')
        current = item.get('provider', 'beeimg')
        provider = ctk_kit.CTkOptionMenu(row, values=list(PROVIDERS) +
                                         ([current] if current not in PROVIDERS else []),
                                         width=120, command=lambda value: self._set_item(
                                             HOSTS, index, 'provider', value))
        provider.set(current)
        provider.pack(side='left', padx=4)
        ctk_kit.CTkLabel(row, text='API Key' if current == 'beeimg' else '凭证',
                         font=FONT_BODY).pack(side='left')
        token = self._entry(row, item.get('token', ''),
                            lambda value: self._set_item(HOSTS, index, 'token', value), secret=True)
        advanced = ctk_kit.CTkFrame(self.hosts_body, fg_color='transparent')
        advanced.pack(fill='x', pady=(0, 6))
        ctk_kit.CTkLabel(advanced, text='期限', font=FONT_BODY).pack(side='left')
        expiration = self._entry(advanced, str(item.get('expiration', '')),
                                 lambda value: self._set_item(HOSTS, index, 'expiration', value))
        ctk_kit.CTkLabel(advanced, text='选项', font=FONT_BODY).pack(side='left')
        value = item.get('options', {})
        if isinstance(value, dict):
            stream = StringIO()
            yaml = YAML()
            yaml.default_flow_style = True
            yaml.dump(value, stream)
            value = stream.getvalue().strip()
        options = self._entry(advanced, self.invalid_options.get(index, str(value)),
                              lambda raw: self._set_item(HOSTS, index, 'options', raw), secret=True)
        error = ctk_kit.CTkLabel(self.hosts_body,
                                 text='options 必须是有效 YAML 映射' if index in self.invalid_options else '',
                                 font=FONT_SMALL)
        error.pack(anchor='w')
        self.host_rows.append({'number': stepper.number, 'provider': provider,
                               'token': token, 'expiration': expiration,
                               'options': options, 'error': error})
        ctk_kit.CTkButton(row, text='删除', width=60, text_color=('#B42318', '#FF7575'),
                          command=lambda: self.remove_host(index)).pack(side='right')

    def _change_cleanup(self):
        """将本地清理开关写入既有保留字段。"""
        self.model.set((*RETENTION, 'enabled'), bool(self.cleanup.get()))

    def _change_days(self, delta):
        """以相同窄步进控件更改保留天数。"""
        days = self.model.get((*RETENTION, 'max_days'))
        if isinstance(days, int) and days + delta > 0:
            self.model.set((*RETENTION, 'max_days'), days + delta)
            self.days.number.configure(text=str(days + delta))

    def refresh(self):
        """仅重绘列表与保留设置，不改写高级参数或修订号。"""
        if self.generation != self.model.document_generation:
            self.invalid_options.clear()
            self.generation = self.model.document_generation
        for body in (self.targets_body, self.hosts_body):
            for child in body.winfo_children():
                child.destroy()
        self.target_rows = []
        self.host_rows = []
        for index, target in enumerate(self.model.get(TARGETS)):
            self._show_target(index, target)
        for index, host in enumerate(self.model.get(HOSTS)):
            self._show_host(index, host)
        if self.model.get((*RETENTION, 'enabled')):
            self.cleanup.select()
        else:
            self.cleanup.deselect()
        self.days.number.configure(text=str(self.model.get((*RETENTION, 'max_days'))))

    def focus_issue(self, path):
        """聚焦当前截图目标或图床行。"""
        rows = self.target_rows if tuple(path[:len(TARGETS)]) == TARGETS else self.host_rows
        if len(path) > 3 and isinstance(path[3], int) and 0 <= path[3] < len(rows):
            widget = rows[path[3]]['number']
            scroll_to_widget(self, widget)
            widget.focus_force()
