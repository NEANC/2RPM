#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""ctk-kit 截图目标、图床与本地保留设置页。"""

from collections.abc import Mapping
import re
import tkinter as tk
from tkinter import messagebox

import ctk_kit
import win32gui

from modules.config_rules import Issue

from modules.gui.appearance import (COLORS, FONT_BODY, FONT_BUTTON,
                                    FONT_CARD_TITLE, FONT_SMALL, FONT_TITLE)
from modules.gui.target_order import (delete_screenshot_target, move_screenshot_target,
                                      swap_screenshot_targets)
from modules.gui.widgets import (NUMBER_HINT, OPTIONAL_BOOL_CHOICES, PRESAVE_HINT,
                                 activate_and_focus, optional_bool_choice,
                                 scroll_to_widget, set_optional_bool)


TARGETS = ('push', 'screenshot', 'targets')
HOSTS = ('push', 'screenshot', 'image_host')
RETENTION = ('push', 'screenshot', 'retention')
PROVIDERS = ('wmimg', 'beeimg', 'beeimg_cn', 'superbed', 'boltp')
MAX_RETENTION_DAYS = 10 ** 9
UNSELECTED = '未选择'
# 与主页一致的页面、卡片比例；截图页三张卡片间距取 20，内容更紧凑。
PAGE_PAD = 28
CARD_GAP = 20
CARD_PAD = 28
CARD_RADIUS = 20
CARD_BORDER = 2
CARD_SURFACE = ('#FAFAFA', '#232323')
CARD_BORDER_COLOR = ('#E1E1E1', '#3A3A3A')
CONTROL_HEIGHT = 36
PORT_WIDTH = 114
PROVIDER_WIDTH = 140
TARGET_PROVIDERS = (('window', '窗口'), ('adb', 'ADB'))
PROVIDER_LABELS = dict(TARGET_PROVIDERS)
HEADER_BUTTONS = dict(width=120, height=52, radius=12, font=FONT_BUTTON)
CARD_BUTTONS = dict(width=132, height=40, radius=10, font=FONT_BUTTON)
HOVER_FAINT = ('#EDEDED', '#2E2E2E')


class Page(ctk_kit.CTkScrollableFrame):
    """只编辑可见的基础字段，保留已有高级 YAML 项。"""

    def __init__(self, parent, app):
        """建立三组业务卡片与两个真实测试入口。"""
        super().__init__(parent, corner_radius=0)
        self.app = app
        self.model = app.model
        self._dark = int(ctk_kit.get_appearance_mode() == 'Dark')
        self.fields = {}
        self.target_rows = []
        self.host_rows = []
        self.widget_for_issue = None
        self.invalid_options = {}
        self.generation = self.model.document_generation
        self._build()
        self.refresh()

    # ---------------------------------------------------------------- 装配
    def _build(self):
        """页头之后按原型顺序放三张原生卡片。"""
        header = ctk_kit.CTkFrame(self, fg_color='transparent')
        header.pack(fill='x', padx=PAGE_PAD, pady=(PAGE_PAD, 0))
        ctk_kit.CTkLabel(header, text='截图设置', font=FONT_TITLE,
                         text_color=COLORS['text_primary'][self._dark]).pack(
            side='left')
        self.upload_button = self._primary(header, '上传测试', self.app.upload)
        self.upload_button.pack(side='right')
        self.capture_button = self._primary(header, '截图测试', self.app.capture)
        self.capture_button.pack(side='right', padx=(0, 10))
        self.number_hint = ctk_kit.CTkLabel(self, text='', font=FONT_BODY, anchor='w')
        self.number_hint.pack(fill='x', padx=PAGE_PAD)
        self._build_targets_card()
        self._build_hosts_card()
        self._build_retention_card()
        ctk_kit.CTkFrame(self, fg_color='transparent', height=CARD_GAP).pack(
            fill='x')

    def _card(self):
        """原型比例的圆角内容卡，标题由调用方放进卡片首行。"""
        card = ctk_kit.Card(self, corner_radius=CARD_RADIUS,
                            border_width=CARD_BORDER,
                            border_color=CARD_BORDER_COLOR[self._dark],
                            fg_color=CARD_SURFACE[self._dark])
        card.pack(fill='x', padx=PAGE_PAD, pady=(CARD_GAP, 0))
        return card

    @staticmethod
    def _primary(parent, text, command):
        """页头与卡片标题栏统一使用强调色主按钮。"""
        return ctk_kit.PrimaryButton(parent, text, command, **HEADER_BUTTONS)

    @staticmethod
    def _card_primary(parent, text, command):
        """卡片标题右侧的强调色动作按钮。"""
        return ctk_kit.PrimaryButton(parent, text, command, **CARD_BUTTONS)

    def _head(self, card, title):
        """卡片标题行：标题在左，动作按钮在右。"""
        head = ctk_kit.CTkFrame(card, fg_color='transparent')
        head.pack(fill='x', padx=CARD_PAD, pady=(CARD_PAD, 12))
        ctk_kit.CTkLabel(head, text=title, font=FONT_CARD_TITLE,
                         text_color=COLORS['text_primary'][self._dark]).pack(
            side='left')
        return head

    def _text(self, parent, text, *, muted=False, **kwargs):
        """正文标签；说明和字段名使用次要色。"""
        return ctk_kit.CTkLabel(
            parent, text=text, font=FONT_BODY,
            text_color=COLORS['text_secondary' if muted else 'text_primary'][self._dark],
            **kwargs)

    def _build_targets_card(self):
        """截图目标卡：编号步进、provider 与窗口／ADB 输入。"""
        card = self._card()
        head = self._head(card, '截图目标')
        self.add_target_button = self._card_primary(
            head, '＋ 添加目标', self.add_target)
        self.add_target_button.pack(side='right')
        self.targets_body = ctk_kit.CTkFrame(card, fg_color='transparent')
        self.targets_body.pack(fill='x', padx=CARD_PAD)
        self.targets_hint = self._text(card, '', muted=True, anchor='w')
        self.targets_hint.pack(fill='x', padx=CARD_PAD, pady=(6, 0))

    def _build_hosts_card(self):
        """图床卡：序号、provider、遮挡凭证与危险删除。"""
        card = self._card()
        head = self._head(card, '图床')
        self.add_host_button = self._card_primary(
            head, '＋ 添加图床', self.add_host)
        self.add_host_button.pack(side='right')
        self.hosts_body = ctk_kit.CTkFrame(card, fg_color='transparent')
        self.hosts_body.pack(fill='x', padx=CARD_PAD)
        self.hosts_hint = self._text(card, '', muted=True, anchor='w')
        self.hosts_hint.pack(fill='x', padx=CARD_PAD, pady=(6, 0))

    def _build_retention_card(self):
        """本地保留卡：清理开关与保留天数数字步进。"""
        card = self._card()
        self._head(card, '本地保留')
        row = ctk_kit.CTkFrame(card, fg_color='transparent')
        row.pack(fill='x', padx=CARD_PAD, pady=(0, 14))
        self._text(row, '清理过期自动截图').pack(side='left')
        self.cleanup = ctk_kit.CTkOptionMenu(
            row, values=list(OPTIONAL_BOOL_CHOICES), width=110,
            height=CONTROL_HEIGHT, font=FONT_BODY,
            command=self._change_cleanup)
        self.cleanup.pack(side='right')
        row = ctk_kit.CTkFrame(card, fg_color='transparent')
        row.pack(fill='x', padx=CARD_PAD, pady=(0, CARD_PAD))
        self._text(row, '保留天数').pack(side='left')
        self.days_value = tk.StringVar(master=self, value='')
        self._refreshing_days = False
        self.days = ctk_kit.Spinner(
            row, from_=1, to=MAX_RETENTION_DAYS, as_int=True, width=PORT_WIDTH,
            height=CONTROL_HEIGHT,
            command=lambda _value: self._days_stepped())
        self._keep_spinner_text(self.days)
        self.days.entry.configure(font=FONT_BODY,
                                  textvariable=self.days_value)
        self.days_value.trace_add(
            'write', lambda *_: self._edit_days(self.days_value.get()))
        self.days._arrows.bind(
            '<Button-1>', lambda event: self._step_days(event))
        # 与旧接口兼容：本页以 ``days.number`` 暴露内部输入框。
        self.days.number = self.days.entry
        self.days.pack(side='right')

    def _stepper(self, parent, action, number, total):
        """用原生 Spinner 显示两位序号，步进即移动整条配置。"""
        values = [f'{index:02d}' for index in range(1, total + 1)]

        def moved(value):
            """把用户选中的序号换算成相对当前行的位移。"""
            try:
                offset = int(value) - number
            except (TypeError, ValueError):
                return
            if offset:
                action(offset)

        return ctk_kit.Spinner(
            parent, values=values, value=f'{number:02d}',
            width=110, height=CONTROL_HEIGHT, command=moved)

    @staticmethod
    def _keep_spinner_text(spinner):
        """移除原生数字提交绑定，由输入变量保留键入原文。"""
        spinner.entry._entry.unbind('<Return>')
        spinner.entry._entry.unbind('<FocusOut>')

    def _before(self):
        """优先补存现有编辑；仅补存失败时提示，重载提示保持不动。"""
        generation = self.model.document_generation
        writable = self.model.before_list_edit()
        if not writable and generation == self.model.document_generation:
            self.app.status.configure(text=PRESAVE_HINT)
        return writable and generation == self.model.document_generation

    def _commit_list(self):
        """列表编辑后按既有保存协调器立即落盘。"""
        self.refresh()
        self.model.on_list_edit()

    def _remap_target_inputs(self, remap):
        """将目标端口草稿和输入错误映射到仍存在的目标索引。"""
        for mapping in (self.model.raw_inputs, self.model.input_errors):
            updated = {}
            for path, value in mapping.items():
                if not isinstance(path, tuple) or path[:len(TARGETS)] != TARGETS or len(path) <= len(TARGETS) or not isinstance(path[len(TARGETS)], int):
                    updated[path] = value
                    continue
                new_index = remap(path[len(TARGETS)])
                if new_index is None:
                    continue
                new_path = (*TARGETS, new_index, *path[len(TARGETS) + 1:])
                updated[new_path] = (Issue(new_path, value.code, value.severity, value.message)
                                     if mapping is self.model.input_errors else value)
            mapping.clear()
            mapping.update(updated)

    def _source_index(self, path, display_index):
        """把可见行号转换为原始列表索引，结构异常项保持原位置。"""
        rows = self.target_rows if tuple(path[:len(TARGETS)]) == TARGETS else self.host_rows
        if 0 <= display_index < len(rows):
            return rows[display_index].get('source_index', display_index)
        return display_index

    def _items(self, path):
        """读取可编辑列表；缺失允许创建，结构异常返回 None。"""
        try:
            items = self.model.get(path)
        except (KeyError, IndexError):
            return []
        except TypeError:
            return None
        return items if isinstance(items, list) else None

    def add_target(self):
        """新增空目标项，不预填来源类型或目标标题。"""
        if not self._before():
            return
        targets = self._items(TARGETS)
        if targets is None:
            self.targets_hint.configure(text='截图目标配置结构无效，请在 YAML 中修复')
            return
        targets.append({})
        self.model.set(TARGETS, targets)
        self._commit_list()

    def move_target(self, index, offset):
        """整项目标重排并一次替换 push 草稿，阻止错误映射保存。"""
        other = index + offset
        if not 0 <= index < len(self.target_rows) or not 0 <= other < len(self.target_rows):
            return
        if not self._before():
            return
        first = self._source_index(TARGETS, index)
        second = self._source_index(TARGETS, other)
        try:
            updated = swap_screenshot_targets(self.model.snapshot(), first, second)
        except ValueError as exc:
            messagebox.showerror('截图目标', str(exc), parent=self)
            return
        self.model.set(('push',), updated['push'])
        self._remap_target_inputs(lambda position: second if position == first else first if position == second else position)
        self._commit_list()

    def remove_target(self, index):
        """确认后原子删除目标及其消息引用。"""
        if not 0 <= index < len(self.target_rows) or not self._before():
            return
        source_index = self._source_index(TARGETS, index)
        if not messagebox.askyesno('删除截图目标',
                                   '删除该目标会同步移除其消息引用，是否继续？', parent=self):
            return
        try:
            updated = delete_screenshot_target(self.model.snapshot(), source_index)
        except ValueError as exc:
            messagebox.showerror('截图目标', str(exc), parent=self)
            return
        self.model.set(('push',), updated['push'])
        self._remap_target_inputs(lambda position: None if position == source_index else position - 1 if position > source_index else position)
        self._commit_list()

    def add_host(self):
        """新增空图床项，不预填服务商或凭证。"""
        if not self._before():
            return
        hosts = self._items(HOSTS)
        if hosts is None:
            self.hosts_hint.configure(text='图床配置结构无效，请在 YAML 中修复')
            return
        hosts.append({})
        self.model.set(HOSTS, hosts)
        self._commit_list()

    def move_host(self, index, offset):
        """移动图床完整配置及其隐含回退顺序。"""
        other = index + offset
        if not 0 <= index < len(self.host_rows) or not 0 <= other < len(self.host_rows):
            return
        if not self._before():
            return
        hosts = self._items(HOSTS)
        first = self._source_index(HOSTS, index)
        second = self._source_index(HOSTS, other)
        if (hosts is None or not 0 <= first < len(hosts)
                or not 0 <= second < len(hosts)):
            return
        hosts[first], hosts[second] = hosts[second], hosts[first]
        for mapping in (self.invalid_options, self.model.input_errors):
            first_key = first if mapping is self.invalid_options else (*HOSTS, first, 'options')
            second_key = second if mapping is self.invalid_options else (*HOSTS, second, 'options')
            left = mapping.pop(first_key, None)
            right = mapping.pop(second_key, None)
            if left is not None:
                mapping[second_key] = left if mapping is self.invalid_options else Issue(
                    second_key, left.code, left.severity, left.message)
            if right is not None:
                mapping[first_key] = right if mapping is self.invalid_options else Issue(
                    first_key, right.code, right.severity, right.message)
        self.model.set(HOSTS, hosts)
        self._commit_list()

    def remove_host(self, index):
        """只删除指定图床。"""
        if not 0 <= index < len(self.host_rows) or not self._before():
            return
        source_index = self._source_index(HOSTS, index)
        hosts = self._items(HOSTS)
        if hosts is None or not 0 <= source_index < len(hosts):
            return
        hosts.pop(source_index)
        invalid_options = {}
        for key, value in self.invalid_options.items():
            if key == source_index:
                continue
            new_key = key - 1 if isinstance(key, int) and key > source_index else key
            invalid_options[new_key] = value
        self.invalid_options = invalid_options
        errors = {}
        for key, issue in self.model.input_errors.items():
            if not isinstance(key, tuple) or key[:len(HOSTS)] != HOSTS:
                errors[key] = issue
                continue
            if len(key) <= len(HOSTS):
                errors[key] = issue
                continue
            index = key[len(HOSTS)]
            if not isinstance(index, int):
                errors[key] = issue
                continue
            if index == source_index:
                continue
            new_index = index - 1 if index > source_index else index
            new_key = (*HOSTS, new_index, *key[len(HOSTS) + 1:])
            errors[new_key] = Issue(new_key, issue.code, issue.severity, issue.message)
        self.model.input_errors = errors
        self.model.set(HOSTS, hosts)
        self._commit_list()

    def _set_item(self, path, index, key, value):
        """仅改写指定基础字段，保留未知字段与图床专有参数。"""
        items = self.model.get(path)
        source_index = self._source_index(path, index)
        if (not isinstance(items, list) or not 0 <= source_index < len(items)
                or not isinstance(items[source_index], Mapping)):
            return
        if items[source_index].get(key) == value:
            return
        items[source_index][key] = value
        self.model.set(path, items)

    def _entry(self, parent, value, callback, *, secret=False):
        """建立粘贴也能触发回写的单行输入框。"""
        value = '' if value is None else str(value)
        variable = tk.StringVar(master=self, value=value)
        entry = ctk_kit.CTkEntry(parent, textvariable=variable,
                                 show='●' if secret else '', font=FONT_BODY)
        entry.pack(side='left', fill='x', expand=True, padx=4)
        variable.trace_add('write', lambda *_: callback(variable.get()))
        return entry

    @staticmethod
    def _bind_paste(box):
        """右键或 Ctrl+V 粘贴不产生 KeyRelease，补一次写回。"""
        for event in ('<<Paste>>', '<<Cut>>', '<<Clear>>'):
            box._entry.bind(
                event, lambda _event, current=box: current.after_idle(current.search),
                add='+')

    def _provider_field(self, parent, values, current, command):
        """只可选不可键入的 provider 下拉；旧值与未知值仍按行显示。"""
        choices = list(values)
        if current not in choices:
            choices.append(current)
        field = ctk_kit.DropField(parent, values=choices, command=command,
                                  height=CONTROL_HEIGHT)
        field.configure(width=PROVIDER_WIDTH)
        field.entry.set_readonly(True)
        field.set(current)
        return field

    def _item_at(self, path, display_index):
        """按可见行号读取原始映射项，结构异常时返回空映射。"""
        source_index = self._source_index(path, display_index)
        items = self.model.get(path)
        if (isinstance(items, list) and 0 <= source_index < len(items)
                and isinstance(items[source_index], Mapping)):
            return items[source_index]
        return {}

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

    def _show_target(self, index, item, source_index=None, total=1):
        """显示窗口搜索框或可编辑的 ADB 地址和数字端口。"""
        row = ctk_kit.CTkFrame(self.targets_body, fg_color='transparent')
        row.pack(fill='x', pady=4)
        stepper = self._stepper(row, lambda delta: self.move_target(index, delta),
                                index + 1, total)
        stepper.pack(side='left')
        raw_provider = item.get('provider')
        if raw_provider is None:
            provider = 'window'
            current = UNSELECTED
        elif isinstance(raw_provider, str):
            provider = raw_provider
            current = PROVIDER_LABELS.get(provider, provider)
        else:
            provider = '未知'
            current = '未知'
        chooser = self._provider_field(
            row, [label for _value, label in TARGET_PROVIDERS], current,
            lambda value: self._change_provider(index, value))
        chooser.pack(side='left', padx=(8, 10))
        entry = None
        port_entry = None
        if provider == 'window':
            field = ctk_kit.CTkFrame(row, fg_color='transparent')
            field.pack(side='left', fill='x', expand=True)
            entry = ctk_kit.SearchBox(
                field, placeholder='输入窗口标题或匹配表达式', delay=0,
                command=lambda value: self._set_item(
                    TARGETS, index, 'target', value))
            entry._entry.configure(font=FONT_BODY)
            self._bind_paste(entry)
            entry.set('' if item.get('target') is None else str(item.get('target')))
            entry.pack(side='left', fill='x', expand=True)
            browse = ctk_kit.SecondaryButton(
                field, '浏览', lambda: self._choose_window(index),
                width=64, height=CONTROL_HEIGHT, font=FONT_BODY, border=True)
            browse.pack(side='left', padx=(8, 0))
        else:
            address, port = self._split_adb(item.get('target', ''))
            entry = self._entry(
                row, address,
                lambda value: self._set_adb(
                    index, value,
                    self._split_adb(
                        self._item_at(TARGETS, index).get('target', ''))[1]))
            entry.pack_configure(padx=(0, 8))
            port_path = (*TARGETS, source_index if source_index is not None else index, 'target')
            raw = self.model.raw_inputs.get(
                port_path, '' if port is None else str(port))
            port_value = tk.StringVar(master=self, value='')
            port_entry = ctk_kit.Spinner(
                row, from_=1, to=65535, as_int=True, width=PORT_WIDTH,
                height=CONTROL_HEIGHT)
            self._keep_spinner_text(port_entry)
            port_entry.entry.configure(font=FONT_BODY, textvariable=port_value,
                                       placeholder_text='端口')
            port_value.set(raw)
            port_value.trace_add(
                'write', lambda *_: self._edit_port(index, port_value.get()))
            # 箭头沿用「按现有目标端口步进」的业务语义。
            port_entry._arrows.bind(
                '<Button-1>',
                lambda event, current=port_entry: self._step_port(index, current, event))
            port_entry.pack(side='left')
        ctk_kit.DangerButton(row, '删除', lambda: self.remove_target(index)).pack(
            side='right')
        self.target_rows.append({'number': stepper, 'entry': entry,
                                 'port': port_entry, 'provider': chooser,
                                 'source_index': index if source_index is None else source_index})

    def _change_provider(self, index, value):
        """主动修改来源时保留可编辑的原始目标；未选择不写入来源键。"""
        value = {'窗口': 'window', 'ADB': 'adb'}.get(value, value)
        source_index = self._source_index(TARGETS, index)
        if value == UNSELECTED:
            self.model.unset((*TARGETS, source_index, 'provider'))
            self.refresh()
            return
        if self._item_at(TARGETS, index).get('provider') == 'adb' and value != 'adb':
            path = (*TARGETS, self._source_index(TARGETS, index), 'target')
            self.model.raw_inputs.pop(path, None)
            self.model.input_errors.pop(path, None)
        self._set_item(TARGETS, index, 'provider', value)
        self.refresh()

    def _step_port(self, index, spinner, event):
        """Spinner 的上下半格箭头按业务端口 +1／-1。"""
        middle = max(1, spinner._arrows.winfo_height()) / 2
        self._change_port(index, 1 if event.y < middle else -1)

    @staticmethod
    def _split_adb(target):
        """仅拆分明确的地址数字端口，其余 serial 原文保留。"""
        target = '' if target is None else str(target)
        match = re.fullmatch(r'(.+):(\d+)', target)
        if not match:
            return target, None
        try:
            return match.group(1), int(match.group(2))
        except ValueError:
            return target, None

    def _set_adb(self, index, address, port):
        """把地址与数字端口还原成既有单个 target 字段。"""
        self._set_item(TARGETS, index, 'target',
                       f'{address}:{port}' if port is not None else address)

    def _edit_port(self, index, raw):
        """数字端口回写单个 target；未完成输入保留在输入框。"""
        path = (*TARGETS, self._source_index(TARGETS, index), 'target')
        if not re.fullmatch(r'\d+', raw):
            self.model.set_input(path, raw, kind='int')
            return
        try:
            port = int(raw)
        except ValueError:
            self.model.raw_inputs[path] = raw
            self.model.input_errors[path] = Issue(path, 'invalid_port', 'error', 'ADB 端口输入无效')
            self.model.revision += 1
            self.model.dirty = True
            self.model.on_edit()
            return
        address, _port = self._split_adb(self._item_at(TARGETS, index).get('target', ''))
        self._set_adb(index, address, port)
        self.model.raw_inputs[path] = raw
        if self.model.input_errors.pop(path, None) is not None:
            self.model.revision += 1
            self.model.on_edit()

    def _change_port(self, index, delta):
        """步进合法数字端口且保留地址。"""
        raw = self.target_rows[index]['port'].entry.get()
        if not re.fullmatch(r'\d+', raw):
            self.number_hint.configure(text=NUMBER_HINT)
            return
        try:
            current = int(raw)
        except ValueError:
            self.number_hint.configure(text=NUMBER_HINT)
            return
        self.number_hint.configure(text='')
        address, port = self._split_adb(self._item_at(TARGETS, index).get('target', ''))
        if port is not None and 1 <= current + delta <= 65535:
            path = (*TARGETS, self._source_index(TARGETS, index), 'target')
            self.model.input_errors.pop(path, None)
            self._set_adb(index, address, current + delta)
            self.model.raw_inputs[path] = str(current + delta)
            self.refresh()

    def _show_host(self, index, item, source_index=None, total=1):
        """图床行展示当前 provider 与遮挡凭证。"""
        source_index = index if source_index is None else source_index
        row = ctk_kit.CTkFrame(self.hosts_body, fg_color='transparent')
        row.pack(fill='x', pady=4)
        stepper = self._stepper(row, lambda delta: self.move_host(index, delta),
                                index + 1, total)
        stepper.pack(side='left')
        raw_provider = item.get('provider')
        if raw_provider is None:
            current = UNSELECTED
        elif isinstance(raw_provider, str):
            current = raw_provider
        else:
            current = '未知'
        provider = self._provider_field(
            row, list(PROVIDERS), current,
            lambda value: self._change_host_provider(index, value))
        provider.pack(side='left', padx=(8, 10))
        self._text(row, 'API Key' if current == 'beeimg' else '凭证').pack(
            side='left')
        token = self._entry(row, item.get('token', ''),
                            lambda value: self._set_item(HOSTS, index, 'token', value), secret=True)
        self.host_rows.append({'number': stepper, 'provider': provider,
                               'token': token, 'source_index': source_index})
        ctk_kit.DangerButton(row, '删除', lambda: self.remove_host(index)).pack(
            side='right')

    def _change_host_provider(self, index, value):
        """修改图床服务商；未选择不写入服务商键。"""
        if value == UNSELECTED:
            self.model.unset((*HOSTS, self._source_index(HOSTS, index), 'provider'))
            self.refresh()
            return
        self._set_item(HOSTS, index, 'provider', value)

    def _retention(self):
        """返回保留节及是否可编辑；节缺失允许创建，结构异常禁用。"""
        section = self._screenshot_section()
        if section is None:
            return None, False
        retention = section.get('retention')
        if retention is None:
            return {}, True
        if isinstance(retention, Mapping):
            return retention, True
        return None, False

    def _change_cleanup(self, selection=None):
        """按三态选择写入本地清理布尔，未设置时删除该键。"""
        _retention, editable = self._retention()
        if not editable:
            return
        set_optional_bool(self.model, (*RETENTION, 'enabled'),
                          self.cleanup.get() if selection is None else selection)

    def _change_days(self, delta):
        """仅对已显示的完整天数步进，缺失时保持空白。"""
        retention, editable = self._retention()
        if not editable:
            return
        raw = self.days_value.get()
        if not re.fullmatch(r'\d+', raw):
            self.number_hint.configure(text=NUMBER_HINT)
            return
        self.number_hint.configure(text='')
        days = int(raw)
        if 1 <= days + delta <= MAX_RETENTION_DAYS:
            self.days_value.set(str(days + delta))

    def _step_days(self, event):
        """以当前可见天数处理上下箭头，保留未解析草稿。"""
        if self.days.is_readonly():
            return
        middle = max(1, self.days._arrows.winfo_height()) / 2
        self._change_days(1 if event.y < middle else -1)

    def _days_stepped(self):
        """Spinner 箭头或提交后由文本变量统一写回，无需重复处理。"""
        return

    def _edit_days(self, raw):
        """仅把完整正整数写入配置，空输入保持为未完成草稿。"""
        if self._refreshing_days:
            return
        _retention, editable = self._retention()
        if editable:
            self.model.set_input((*RETENTION, 'max_days'), raw, kind='int')

    def _screenshot_section(self):
        """返回映射类型的截图节；缺失为空映射，结构异常返回 None。"""
        snapshot = self.model.snapshot()
        if not isinstance(snapshot, Mapping):
            return None
        if 'push' not in snapshot:
            return {}
        push = snapshot['push']
        if not isinstance(push, Mapping):
            return None
        if 'screenshot' not in push:
            return {}
        screenshot = push['screenshot']
        return screenshot if isinstance(screenshot, Mapping) else None

    def refresh(self):
        """仅重绘列表与保留设置，不改写高级参数或修订号。"""
        if self.generation != self.model.document_generation:
            self.invalid_options.clear()
            self.generation = self.model.document_generation
        section = self._screenshot_section()
        for body in (self.targets_body, self.hosts_body):
            for child in body.winfo_children():
                child.destroy()
        self.target_rows = []
        self.host_rows = []
        broken = section is None
        if broken:
            self.targets_hint.configure(text='截图配置结构无效，请在 YAML 中修复')
            self.hosts_hint.configure(text='截图配置结构无效，请在 YAML 中修复')
        else:
            raw_targets = section.get('targets')
            targets = [(source_index, item) for source_index, item in enumerate(raw_targets)
                       if isinstance(item, Mapping)] if isinstance(raw_targets, list) else []
            raw_hosts = section.get('image_host')
            hosts = [(source_index, item) for source_index, item in enumerate(raw_hosts)
                     if isinstance(item, Mapping)] if isinstance(raw_hosts, list) else []
            self.targets_hint.configure(
                text=('部分截图目标结构无效，已跳过显示'
                      if len(targets) != len(raw_targets) else '')
                if isinstance(raw_targets, list) else
                ('截图目标配置缺失或结构无效，请在 YAML 中检查'
                 if raw_targets is not None else ''))
            self.hosts_hint.configure(
                text=('部分图床结构无效，已跳过显示'
                      if len(hosts) != len(raw_hosts) else '')
                if isinstance(raw_hosts, list) else
                ('图床配置缺失或结构无效，请在 YAML 中检查'
                 if raw_hosts is not None else ''))
            for index, (source_index, target) in enumerate(targets):
                self._show_target(index, target, source_index, len(targets))
            for index, (source_index, host) in enumerate(hosts):
                self._show_host(index, host, source_index, len(hosts))
        buttons = 'disabled' if broken else 'normal'
        self.add_target_button.configure(state=buttons)
        self.add_host_button.configure(state=buttons)
        retention, editable = self._retention()
        self.cleanup.set(optional_bool_choice(retention.get('enabled'))
                         if editable else OPTIONAL_BOOL_CHOICES[0])
        self.cleanup.configure(state='normal' if editable else 'disabled')
        self.days.set_readonly(not editable)
        days = retention.get('max_days') if editable else None
        self._refreshing_days = True
        try:
            self.days_value.set(self.model.raw_inputs.get(
                (*RETENTION, 'max_days'),
                str(days) if isinstance(days, int) else '') if editable else '--')
        finally:
            self._refreshing_days = False

    def focus_issue(self, path):
        """聚焦当前保留天数、截图目标或图床行。"""
        if tuple(path) == (*RETENTION, 'max_days'):
            widget = self.days.number._entry
            scroll_to_widget(self, widget)
            self.widget_for_issue = widget
            activate_and_focus(widget)
            return
        rows = self.target_rows if tuple(path[:len(TARGETS)]) == TARGETS else self.host_rows
        if len(path) > 3 and isinstance(path[3], int):
            row_index = next((row for row, item in enumerate(rows)
                              if item.get('source_index') == path[3]), None)
            if row_index is not None:
                widget = rows[row_index]['number']
                scroll_to_widget(self, widget)
                activate_and_focus(widget)
