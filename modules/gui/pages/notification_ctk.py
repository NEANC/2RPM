#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""按原型重绘的消息推送设置：通道、事件消息与错误处理。"""

from collections.abc import Mapping
import re
import tkinter as tk
from tkinter import messagebox

import ctk_kit

from modules.gui.appearance import (COLORS, FONT_BODY, FONT_BUTTON,
                                    FONT_CARD_TITLE, FONT_SMALL, FONT_TITLE)
from modules.gui.forms import (event_variables, preview_template,
                               provider_fields, screenshot_conditions)
from modules.gui.widgets import (OPTIONAL_BOOL_CHOICES, PRESAVE_HINT,
                                 STRUCTURE_HINT, activate_and_focus,
                                 optional_bool_choice, path_writable,
                                 scroll_to_widget, set_optional_bool)
from modules.notification import _configured_screenshot_names


CHANNELS = ('push', 'push_channel_settings', 'channels')
EVENTS = (('on_end', '目标结束'), ('on_timeout', '运行超时'),
          ('on_wait_timeout', '等待启动超时'), ('on_external', '外部程序执行通知'))
PROVIDERS = {'Bark': 'bark', 'Discord': 'discord', 'Telegram': 'telegram',
             'Server酱': 'serverchan', 'Server酱 Turbo': 'serverchanturbo',
             '企业微信应用': 'wechatworkapp', '企业微信机器人': 'wechatworkbot',
             'PushPlus': 'pushplus', 'go-cqhttp': 'gocqhttp', 'Qmsg': 'qmsg',
             '钉钉': 'dingtalk', '飞书': 'lark', 'SMTP 邮件': 'smtp'}
VARIABLE_LABELS = {'host_name': '主机名', 'current_time': '当前时间',
                   'short_current_time': '当前时间（短）', 'process_name': '进程名',
                   'process_pid': '进程PID', 'process_run_time': '进程运行时间',
                   'process_wait_time': '进程积累等待时间',
                   'external_program_name': '调用的程序名',
                   'external_program_path': '调用的程序路径',
                   'screenshot': '截图结果聚合'}

# 与主页保持同一套原型比例：圆角 20、2px 边框、28px 页边距与卡片内边距。
PAGE_PAD = 28
CARD_GAP = 20
CARD_PAD = 28
CARD_RADIUS = 20
CARD_BORDER = 2
CARD_SURFACE = ('#FAFAFA', '#232323')
CARD_BORDER_COLOR = ('#E1E1E1', '#3A3A3A')
ROW_SURFACE = ('#F1F1F1', '#2C2C2C')
LABEL_WIDTH = 110
ROW_PAD_Y = 4
BOOL_WIDTH = 96


class Page(ctk_kit.CTkScrollableFrame):
    """只编辑显示的字段，完整配置仍由模型保管。"""

    def __init__(self, parent, app):
        """创建通道行、四个可展开事件和重试设置。"""
        super().__init__(parent, corner_radius=0)
        self.app = app
        self.model = app.model
        self.channels_path = CHANNELS
        self.fields = {}
        self.channel_entries = {}
        self.channel_rows = []
        self.event_bodies = {}
        self.preview_labels = {}
        self.preview_frames = {}
        self.preview_warning_labels = {}
        self.preview_buttons = {}
        self.variables = {}
        self.event_choices = {}
        self._refreshing = False
        self._expanded_channel = None
        self._expanded_events = set()
        self._preview_events = set()
        self._preview_text = {}
        self._preview_warnings = {}
        self.generation = self.model.document_generation
        self._build()
        self.refresh()

    # ---------------------------------------------------------------- 装配
    def _build(self):
        """页头之后按原型顺序放三张卡片。"""
        header = ctk_kit.CTkFrame(self, fg_color='transparent')
        header.pack(fill='x', padx=PAGE_PAD, pady=(PAGE_PAD, 0))
        ctk_kit.CTkLabel(header, text='消息推送设置', font=FONT_TITLE,
                         text_color=COLORS['text_primary']).pack(side='left')
        self.notify_button = ctk_kit.PrimaryButton(
            header, '通知测试', self.app.notify,
            width=120, height=52, radius=12, font=FONT_BUTTON)
        self.notify_button.pack(side='right')
        self.structure_hint = ctk_kit.CTkLabel(
            self, text='', font=FONT_BODY, anchor='w',
            text_color=COLORS['text_secondary'])
        self.structure_hint.pack(fill='x', padx=PAGE_PAD, pady=(6, 0))
        self._build_channels_card()
        self._build_events_card()
        self._build_retry_card()
        ctk_kit.CTkFrame(self, fg_color='transparent', height=CARD_GAP).pack(
            fill='x')

    def _card(self, title):
        """按主页比例创建圆角内容卡，返回卡片和标题行。"""
        card = ctk_kit.Card(self, corner_radius=CARD_RADIUS,
                            border_width=CARD_BORDER,
                            border_color=CARD_BORDER_COLOR,
                            fg_color=CARD_SURFACE)
        card.pack(fill='x', padx=PAGE_PAD, pady=(CARD_GAP, 0))
        head = ctk_kit.CTkFrame(card, fg_color='transparent')
        head.pack(fill='x', padx=CARD_PAD, pady=(CARD_PAD, 14))
        ctk_kit.CTkLabel(head, text=title, font=FONT_CARD_TITLE,
                         text_color=COLORS['text_primary']).pack(side='left')
        return card, head

    def _text(self, parent, text, *, muted=True, **kwargs):
        """正文标签；默认使用次要色。"""
        return ctk_kit.CTkLabel(
            parent, text=text, font=FONT_BODY,
            text_color=COLORS['text_secondary' if muted else 'text_primary'],
            **kwargs)

    def _option_menu(self, parent, values, *, command=None, width=140):
        """下拉框统一输入框底色，避免 CustomTkinter 默认的蓝色实心块。"""
        return ctk_kit.CTkOptionMenu(
            parent, values=values, font=FONT_BODY, dropdown_font=FONT_BODY,
            width=width, command=command,
            fg_color=COLORS['input_bg'],
            button_color=COLORS['input_bg'],
            button_hover_color=COLORS['soft_hover'],
            text_color=COLORS['text_primary'],
            dropdown_fg_color=COLORS['input_bg'],
            dropdown_hover_color=COLORS['soft'],
            dropdown_text_color=COLORS['text_primary'])

    def _build_channels_card(self):
        """推送通道卡：标题右侧添加通道，通道行折叠展开参数。"""
        card, head = self._card('推送通道')
        self.add_channel_button = ctk_kit.PrimaryButton(
            head, '＋ 添加通道', self.open_add_dialog,
            width=140, height=40, radius=10, font=FONT_BUTTON)
        self.add_channel_button.pack(side='right')
        self.channel_body = ctk_kit.CTkFrame(card, fg_color='transparent')
        self.channel_body.pack(fill='x', padx=CARD_PAD)
        self.channel_hint = ctk_kit.CTkLabel(
            card, text='', font=FONT_SMALL, anchor='w',
            text_color=COLORS['text_secondary'])
        self.channel_hint.pack(fill='x', padx=CARD_PAD, pady=(0, CARD_PAD))

    def _build_events_card(self):
        """通知事件与消息卡：四个事件各自独立展开。"""
        card, _head = self._card('通知事件与消息')
        self.events_body = ctk_kit.CTkFrame(card, fg_color='transparent')
        self.events_body.pack(fill='x', padx=CARD_PAD)
        self.events_hint = ctk_kit.CTkLabel(
            card, text='', font=FONT_SMALL, anchor='w',
            text_color=COLORS['text_secondary'])
        self.events_hint.pack(fill='x', padx=CARD_PAD, pady=(0, CARD_PAD))

    def _build_retry_card(self):
        """错误处理卡：重试间隔为文本，最大重试次数使用原生 Spinner。"""
        card, _head = self._card('错误处理')
        for label, name in (('重试间隔', 'interval'), ('最大重试次数', 'max_count')):
            row = ctk_kit.CTkFrame(card, fg_color='transparent')
            row.pack(fill='x', padx=CARD_PAD, pady=ROW_PAD_Y)
            self._text(row, label).pack(side='left')
            path = ('push', 'retry', name)
            if name == 'max_count':
                self.retry_variable = tk.StringVar(master=self)
                self.retry_spinner = ctk_kit.Spinner(
                    row, from_=0, to=99, as_int=True, width=140,
                    command=lambda _value: self._max_retry_changed())
                self.retry_spinner.entry._entry.unbind('<Return>')
                self.retry_spinner.entry._entry.unbind('<FocusOut>')
                self.retry_spinner.entry.configure(textvariable=self.retry_variable)
                self.retry_variable.trace_add('write', lambda *_: self._max_retry_changed())
                self.retry_spinner._arrows.bind('<Button-1>', self._step_retry)
                self.retry_spinner.pack(side='right')
                self.fields[path] = (self.retry_variable, self.retry_spinner.entry._entry, 'int')
            else:
                self._entry(row, path, side='right', expand=False, width=140)
        ctk_kit.CTkFrame(card, fg_color='transparent', height=(
            CARD_PAD - 2 * ROW_PAD_Y)).pack(fill='x')

    # ---------------------------------------------------------------- 字段
    def _safe_get(self, path, default=None):
        """读取可能缺失的节，缺失或中间节点类型异常时返回默认值。"""
        try:
            return self.model.get(path)
        except (KeyError, IndexError, TypeError):
            return default

    def _safe_input(self, path):
        """读取原始输入，缺失字段返回空文本。"""
        try:
            return self.model.get_input(path)
        except (KeyError, IndexError, TypeError):
            return ''

    def _entry(self, parent, path, *, numeric=False, side='left',
               expand=True, width=140):
        """绑定固定字段，保留数字原始输入。"""
        variable = tk.StringVar(master=self, value=(self._safe_input(path) if numeric else
                                                   str(self._safe_get(path) or '')))
        widget = ctk_kit.CTkEntry(parent, textvariable=variable, font=FONT_BODY,
                                  height=38, width=width)
        widget.pack(side=side, fill='x' if expand else 'none', expand=expand,
                    padx=(8, 0) if side == 'left' else (8, 0))
        variable.trace_add('write', lambda *_: self._changed(path, variable, numeric))
        self.fields[path] = (variable, widget, 'int' if numeric else 'text')
        return widget

    def _max_retry_changed(self):
        """将可见数字原文交给模型，不由步进器提交时钳制。"""
        path = ('push', 'retry', 'max_count')
        if self._refreshing or not path_writable(self.model, path):
            return
        raw = self.retry_spinner.entry.get()
        self.model.set_input(path, raw, kind='int')

    def _step_retry(self, event):
        """仅对完整整数按一步移动，并限制主动步进范围。"""
        if self.retry_spinner.is_readonly():
            return
        raw = self.retry_spinner.entry.get()
        if not re.fullmatch(r'[+-]?\d+', raw.strip()):
            return
        middle = max(1, self.retry_spinner._arrows.winfo_height()) / 2
        delta = 1 if event.y < middle else -1
        try:
            value = int(raw)
        except ValueError:
            return
        self.retry_spinner.set(max(0, min(99, value + delta)))

    def _changed(self, path, variable, numeric):
        """模型刷新期间或结构异常时不写回旧值。"""
        if not self._refreshing and path_writable(self.model, path):
            if numeric:
                self.model.set_input(path, variable.get(), kind='int')
            else:
                self.model.set(path, variable.get())

    def _change_event_enable(self, event, selection=None):
        """按三态选择写入事件启用布尔，未设置时删除该键。"""
        if self._refreshing:
            return
        path = ('push', 'templates', event, 'enable')
        if not path_writable(self.model, path):
            return
        if selection is None:
            selection = self.event_choices[event].get()
        set_optional_bool(self.model, path, selection)

    def _text_changed(self, path, widget):
        """仅在正文实际变化时写回，未编辑的空控件不创建键。"""
        if self._refreshing or not path_writable(self.model, path):
            return
        text = widget.get('1.0', 'end-1c')
        current = self._safe_get(path, None)
        if current is None and text == '':
            return
        if current == text:
            return
        self.model.set(path, text)

    # ---------------------------------------------------------------- 通道
    def _source_index(self, display_index):
        """把可见行号转换为原始列表索引，结构异常项保持原位置。"""
        if 0 <= display_index < len(self.channel_rows):
            return self.channel_rows[display_index].get('source_index', display_index)
        return display_index

    def _set_channel(self, index, name, value):
        """修改指定通道参数而不删除未展示的高级参数。"""
        channels = self.model.get(CHANNELS)
        source_index = self._source_index(index)
        if (not isinstance(channels, list) or not 0 <= source_index < len(channels)
                or not isinstance(channels[source_index], Mapping)):
            return
        channel = channels[source_index]
        channel[name] = value
        self.model.set(CHANNELS, channels)
        if name in ('provider',) or any(field.name == name and field.required
                                         for field in provider_fields(channel.get('provider', ''))):
            self._update_channel_status(self.channel_rows[index]['status'], self._status(channel))

    @staticmethod
    def _update_channel_status(widget, status):
        """同步通道状态徽标的文字、底色和边框。"""
        configured = status == '已配置'
        widget.configure(text=status,
                         text_color=('#067647', '#12B76A') if configured else ('#9A6700', '#F0B429'),
                         fg_color=('#E7F5EC', '#193D30') if configured else ('#FFF4D6', '#4A371A'),
                         border_color=('#6CCB91', '#31885B') if configured else ('#D8A32C', '#AB7824'))

    @staticmethod
    def _status(channel):
        """按当前 provider 的真实必填字段判断是否已配置。"""
        required = [field for field in provider_fields(channel.get('provider', '')) if field.required]
        return ('已配置' if required and all(str(channel.get(field.name, '')).strip()
                                           for field in required) else '需要填写参数')

    def change_channel_provider(self, index, provider):
        """通过可见选择器切换通道类型并重建当前行参数。"""
        channels = self._safe_get(CHANNELS, [])
        source_index = self._source_index(index)
        if (not isinstance(channels, list) or not 0 <= source_index < len(channels)
                or not isinstance(channels[source_index], Mapping)
                or provider not in PROVIDERS.values()):
            return
        if channels[source_index].get('provider') == provider:
            return
        channels[source_index]['provider'] = provider
        self.model.set(CHANNELS, channels)
        self._expanded_channel = index
        self.refresh()

    def add_channel(self, provider):
        """新增选择的通道并立即触发既有列表保存边界。"""
        if provider not in PROVIDERS.values() or not path_writable(self.model, CHANNELS):
            return
        identity = (self.model.document_generation, self.model.revision)
        writable = self.model.before_list_edit()
        if not writable:
            if identity == (self.model.document_generation, self.model.revision):
                self.app.status.configure(text=PRESAVE_HINT)
            return
        if identity != (self.model.document_generation, self.model.revision):
            return
        channels = self._safe_get(CHANNELS, [])
        if not isinstance(channels, list):
            return
        channels.append({'provider': provider, **{field.name: '' for field in provider_fields(provider)
                                                if field.required}})
        self.model.set(CHANNELS, channels)
        self.refresh()
        self._expanded_channel = len(self.channel_rows) - 1
        self.refresh()
        self.model.on_list_edit()

    def remove_channel(self, index):
        """确认后只删除当前行，列表变更前补存。"""
        channels = self._safe_get(CHANNELS, [])
        source_index = self._source_index(index)
        if not isinstance(channels, list) or not 0 <= source_index < len(channels):
            return
        identity = (self.model.document_generation, self.model.revision)
        writable = self.model.before_list_edit()
        if not writable:
            if identity == (self.model.document_generation, self.model.revision):
                self.app.status.configure(text=PRESAVE_HINT)
            return
        if identity != (self.model.document_generation, self.model.revision):
            return
        if not messagebox.askyesno('删除通道', '删除该推送通道？', parent=self):
            return
        if identity != (self.model.document_generation, self.model.revision):
            return
        channels = self._safe_get(CHANNELS, [])
        channels.pop(source_index)
        self.model.set(CHANNELS, channels)
        if self._expanded_channel == index:
            self._expanded_channel = None
        elif self._expanded_channel is not None and index < self._expanded_channel:
            self._expanded_channel -= 1
        self.refresh()
        self.model.on_list_edit()

    def open_add_dialog(self):
        """未选择 provider 时不能添加，关闭或取消均不修改配置。"""
        dialog = ctk_kit.CTkToplevel(self)
        dialog.title('添加通道')
        dialog.geometry('400x260')
        dialog.transient(self.winfo_toplevel())
        panel = ctk_kit.CTkFrame(dialog, corner_radius=12)
        panel.pack(fill='both', expand=True, padx=16, pady=16)
        ctk_kit.CTkButton(panel, text='×', width=32, command=dialog.destroy).pack(anchor='e')
        ctk_kit.CTkLabel(panel, text='选择通道', font=FONT_CARD_TITLE).pack(anchor='w', padx=12)
        names = ['请选择通道', *PROVIDERS]
        selected = self._option_menu(panel, names)
        selected.set(names[0])
        selected.pack(fill='x', padx=12, pady=12)
        self._text(panel, '选择后在推送通道列表中填写对应参数。',
                   anchor='w').pack(fill='x', padx=12)
        row = ctk_kit.CTkFrame(panel, fg_color='transparent')
        row.pack(anchor='e', padx=12, pady=(12, 0))
        ctk_kit.SecondaryButton(row, '取消', dialog.destroy,
                                width=80, height=38, font=FONT_BODY,
                                border=True).pack(side='left', padx=(0, 8))
        add = ctk_kit.PrimaryButton(row, '添加', width=80, height=38, font=FONT_BODY)
        add.configure(state='disabled')

        def submit():
            """确认当前选定通道并关闭弹窗。"""
            if selected.get() in PROVIDERS:
                self.add_channel(PROVIDERS[selected.get()])
                dialog.destroy()
        add.configure(command=submit)
        add.pack(side='left')
        selected.configure(command=lambda value: add.configure(
            state='normal' if value in PROVIDERS else 'disabled'))
        dialog.update_idletasks()
        parent = self.winfo_toplevel()
        x = parent.winfo_rootx() + (parent.winfo_width() - dialog.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f'+{x}+{y}')
        dialog.update_idletasks()
        x += parent.winfo_rootx() + parent.winfo_width() // 2 - (
            dialog.winfo_rootx() + dialog.winfo_width() // 2)
        y += parent.winfo_rooty() + parent.winfo_height() // 2 - (
            dialog.winfo_rooty() + dialog.winfo_height() // 2)
        dialog.geometry(f'+{x}+{y}')
        dialog.grab_set()

    def expand_channel(self, index):
        """只展开所点击的行。"""
        self._expanded_channel = None if self._expanded_channel == index else index
        self.refresh()

    def _bind_toggle(self, widget, index):
        """让标题行的非操作区域点击后展开或收起。"""
        handler = lambda _event=None, i=index: self.expand_channel(i)
        widget.bind('<Button-1>', handler)
        canvas = getattr(widget, '_canvas', None)
        if canvas is not None:
            canvas.bind('<Button-1>', handler)

    def _render_channels(self):
        """动态显示 provider 必填状态和遮挡的独立参数表单。"""
        for child in self.channel_body.winfo_children():
            child.destroy()
        self.channel_rows = []
        self.channel_entries = {}
        raw = self._safe_get(CHANNELS, None)
        if not isinstance(raw, list):
            channels = []
            hint = '推送通道配置缺失或结构无效，请在 YAML 中检查' if raw is not None else ''
        else:
            channels = [(source_index, item) for source_index, item in enumerate(raw)
                        if isinstance(item, Mapping)]
            hint = ('部分通道项结构无效，已跳过显示' if len(channels) != len(raw) else '')
        self.channel_hint.configure(text=hint)
        for index, (source_index, channel) in enumerate(channels):
            provider = channel.get('provider')
            provider = provider if isinstance(provider, str) else '未知'
            status_text = self._status(channel)
            expanded = self._expanded_channel == index
            # Collapsible 的标题按钮只能显示纯文本；标题行需要同时放徽标、
            # 删除和行尾箭头，因此保留折叠体、标题行改用自定义行容器。
            panel = ctk_kit.Collapsible(
                self.channel_body, collapsed_text='', expanded_text='',
                expanded=expanded, button_height=56)
            panel.pack(fill='x', pady=ROW_PAD_Y)
            panel.button.pack_forget()
            panel.detail_label.place_forget()
            panel.body.configure(fg_color=COLORS['soft'])
            if expanded:
                # 折叠体与标题行保持同一左右内缩，展开后看起来是一整块。
                panel.body.pack_configure(padx=8)
            head = panel.header
            line = ctk_kit.CTkFrame(head, fg_color=ROW_SURFACE,
                                    corner_radius=8)
            line.pack(fill='x', padx=8, pady=6)
            arrow = ctk_kit.CTkLabel(
                line, text='⌄' if expanded else '›', font=FONT_BODY, width=20,
                text_color=COLORS['text_secondary'])
            arrow.pack(side='right')
            # ctk-kit 的 DangerButton 令牌只有深色一套，浅色下仍是深红底；
            # 这里按原型给浅/深两套红色文字、底色与边框。
            delete = ctk_kit.CTkButton(
                line, text='删除', width=58, height=30, font=FONT_SMALL,
                text_color=('#B42318', '#FF7575'),
                fg_color=('#FDECEB', '#462426'),
                hover_color=('#F9D6D3', '#603034'),
                border_color=('#E8A49D', '#B74C50'), border_width=1,
                command=lambda i=index: self.remove_channel(i))
            delete.pack(side='right', padx=(8, 4))
            status = ctk_kit.CTkLabel(
                line, text=status_text, font=FONT_SMALL, corner_radius=6,
                border_width=1, padx=8, height=24)
            self._update_channel_status(status, status_text)
            status.pack(side='right', padx=8)
            number = ctk_kit.CTkLabel(
                line, text=f'{index + 1:02d}', font=FONT_SMALL, width=30, anchor='w',
                text_color=COLORS['text_secondary'])
            number.pack(side='left', padx=(4, 8))
            name = ctk_kit.CTkLabel(
                line, text=provider, font=FONT_BODY, anchor='w',
                text_color=COLORS['text_primary'])
            name.pack(side='left', fill='x', expand=True)
            for widget in (head, line, number, name, status, arrow):
                self._bind_toggle(widget, index)
            self.channel_rows.append({'panel': panel, 'toggle': name, 'header': line,
                                      'status': status, 'delete': delete,
                                      'provider': None, 'source_index': source_index})
            if not expanded:
                continue
            self._render_channel_fields(panel.body, index, channel, provider)

    def _render_channel_fields(self, parent, index, channel, provider):
        """展开行内按当前 provider 显示选择器和真实凭证字段。"""
        selector_row = ctk_kit.CTkFrame(parent, fg_color='transparent')
        selector_row.pack(fill='x', padx=28, pady=(10, 4))
        self._text(selector_row, '通道类型', width=LABEL_WIDTH, anchor='w').pack(
            side='left')
        values = list(PROVIDERS.values())
        if provider not in values:
            values.append(provider)
        selector = self._option_menu(
            selector_row, values,
            command=lambda value, i=index: self.change_channel_provider(i, value))
        selector.set(provider)
        selector.pack(side='left', fill='x', expand=True)
        self.channel_rows[index]['provider'] = selector
        self.channel_entries['provider'] = selector
        for field in provider_fields(provider):
            row = ctk_kit.CTkFrame(parent, fg_color='transparent')
            row.pack(fill='x', padx=28, pady=3)
            self._text(row, field.label + (' *' if field.required else ''),
                       width=LABEL_WIDTH, anchor='w').pack(side='left')
            value = tk.StringVar(master=self, value=str(channel.get(field.name, '')))
            entry = ctk_kit.CTkEntry(row, textvariable=value, font=FONT_BODY,
                                     show='●' if field.secret else '')
            entry.pack(side='left', fill='x', expand=True)
            value.trace_add('write', lambda *_, i=index, name=field.name, var=value:
                            self._set_channel(i, name, var.get()))
            self.channel_entries[field.name] = entry
            if field.secret:
                ctk_kit.CTkButton(
                    row, text='显示', width=56, height=32,
                    command=lambda widget=entry: widget.configure(
                        show='' if widget.cget('show') else '●'),
                    fg_color='transparent',
                    hover_color=COLORS['card_hover'],
                    text_color=COLORS['accent'],
                    font=FONT_SMALL).pack(side='left', padx=(8, 0))
        ctk_kit.CTkFrame(parent, fg_color='transparent', height=10).pack(fill='x')

    # ---------------------------------------------------------------- 事件
    def _variable_names(self, event):
        """组合事件合法变量与已配置截图输出名。"""
        snapshot = self.model.snapshot()
        push = snapshot.get('push') if isinstance(snapshot, Mapping) else None
        screenshot = push.get('screenshot') if isinstance(push, Mapping) else None
        names = event_variables(event)
        screenshot_names = _configured_screenshot_names(
            screenshot if isinstance(screenshot, Mapping) else {}, set(names))
        return dict.fromkeys([*names, 'screenshot', *sorted(screenshot_names)])

    def expand_event(self, event):
        """按事件独立展开或收起编辑区域。"""
        if event in self._expanded_events:
            self._expanded_events.remove(event)
        else:
            self._expanded_events.add(event)
        self._preview_events.discard(event)
        self._preview_text.pop(event, None)
        self._preview_warnings.pop(event, None)
        self.refresh()

    def _render_events(self):
        """在展开的事件卡片中编辑标题、正文并提供示例预览。"""
        for child in self.events_body.winfo_children():
            child.destroy()
        self.event_bodies = {}
        self.preview_labels = {}
        self.preview_frames = {}
        self.preview_warning_labels = {}
        self.preview_buttons = {}
        self.variables = {}
        self.event_choices = {}
        raw = self._safe_get(('push', 'templates'), None)
        templates = raw if isinstance(raw, Mapping) else {}
        self.events_hint.configure(
            text='' if raw is None or isinstance(raw, Mapping)
            else '通知事件配置缺失或结构无效，请在 YAML 中检查')
        for event, label in EVENTS:
            path = ('push', 'templates', event)
            expanded = event in self._expanded_events
            panel = ctk_kit.Collapsible(
                self.events_body, collapsed_text='', expanded_text='',
                expanded=expanded, button_height=48)
            panel.pack(fill='x', pady=ROW_PAD_Y)
            panel.button.pack_forget()
            panel.detail_label.place_forget()
            panel.body.configure(fg_color=COLORS['soft'])
            self.event_bodies[event] = panel
            row = ctk_kit.CTkFrame(panel.header, fg_color=ROW_SURFACE,
                                   corner_radius=8)
            row.pack(fill='x', padx=12, pady=8)
            name = ctk_kit.CTkLabel(
                row, text=label, font=FONT_BODY, anchor='w',
                text_color=COLORS['text_primary'])
            name.pack(side='left', fill='x', expand=True)
            # FlatButton 按文字实测宽度建控件，ctk-kit 的字体缓存跨 Tk 解释器
            # 不失效；这里用固定宽度的透明按钮，避免重建窗口后测量失败。
            edit = ctk_kit.CTkButton(
                row, text='收起消息 ▴' if expanded else '编辑消息 ▾',
                command=lambda key=event: self.expand_event(key),
                width=108, height=32, fg_color='transparent',
                hover_color=COLORS['card_hover'],
                text_color=COLORS['accent'], font=FONT_SMALL)
            edit.pack(side='right')
            choice = ctk_kit.CTkOptionMenu(
                row, values=list(OPTIONAL_BOOL_CHOICES), width=BOOL_WIDTH,
                height=32, font=FONT_SMALL,
                command=lambda value, key=event:
                self._change_event_enable(key, value))
            choice.pack(side='right', padx=(8, 12))
            self.event_choices[event] = choice
            event_value = templates.get(event) if isinstance(templates, Mapping) else None
            enable = event_value.get('enable') if isinstance(event_value, Mapping) else None
            choice.set(optional_bool_choice(enable))
            if not path_writable(self.model, (*path, 'enable')):
                choice.configure(state='disabled')
            for widget in (name, row):
                widget.bind('<Button-1>', lambda _event=None, key=event:
                            self.expand_event(key))
            if expanded:
                self._render_event_body(panel.body, event, path)
        self._sync_preview_states()

    def _render_event_body(self, body, event, path):
        """展开后依次绘制标题、正文、变量工具栏与预览区。"""
        title_row = ctk_kit.CTkFrame(body, fg_color='transparent')
        title_row.pack(fill='x', padx=28, pady=(10, 4))
        self._text(title_row, '标题', width=64, anchor='w').pack(side='left')
        self._entry(title_row, (*path, 'title'))
        self._text(body, '正文', anchor='w').pack(fill='x', padx=28)
        text = ctk_kit.CTkTextbox(body, height=110, font=FONT_BODY, wrap='word')
        text.pack(fill='x', padx=28, pady=(4, 6))
        text.insert('1.0', str(self._safe_get((*path, 'content')) or ''))
        text.bind('<KeyRelease>', lambda _, key=(*path, 'content'), widget=text:
                  self._text_changed(key, widget))
        text.bind('<FocusOut>', lambda _, key=(*path, 'content'), widget=text:
                  self._text_changed(key, widget))
        text.bind('<<Paste>>', lambda _, key=(*path, 'content'), widget=text:
                  self.after_idle(lambda: self._text_changed(key, widget)))
        self.fields[(*path, 'content')] = (None, text, 'multiline')
        controls = ctk_kit.CTkFrame(body, fg_color='transparent')
        controls.pack(fill='x', padx=28, pady=(2, 8))
        names = self._variable_names(event)
        options = {'选择变量': None}
        options.update({f'{VARIABLE_LABELS.get(name, name)} ({name})': name
                        for name in names})
        variable = self._option_menu(controls, list(options), width=220)
        variable.set('选择变量')
        variable.pack(side='left', fill='x', expand=True)
        self.variables[event] = (variable, options)
        ctk_kit.SecondaryButton(
            controls, '插入正文', lambda key=event: self._insert_selected(key),
            width=92, height=34, font=FONT_SMALL, border=True).pack(
                side='left', padx=8)
        button = ctk_kit.SecondaryButton(
            controls, '收起预览' if event in self._preview_events else '预览消息',
            lambda key=event: self.preview_event(key),
            width=92, height=34, font=FONT_SMALL, border=True)
        button.pack(side='left')
        self.preview_buttons[event] = button
        frame = ctk_kit.CTkFrame(body, corner_radius=8,
                                 border_width=1,
                                 border_color=COLORS['border_strong'],
                                 fg_color=COLORS['input_bg'])
        main = ctk_kit.CTkLabel(
            frame, text=self._preview_text.get(event, ''), font=FONT_BODY,
            justify='left', anchor='w', wraplength=520)
        main.pack(fill='x', padx=12, pady=(8, 0))
        warning = ctk_kit.CTkLabel(
            frame, text=self._preview_warnings.get(event, ''), font=FONT_BODY,
            justify='left', anchor='w', wraplength=520,
            text_color=('#9A6700', '#F0B429'))
        if self._preview_warnings.get(event):
            warning.pack(fill='x', padx=12, pady=(4, 8))
        else:
            main.pack_configure(pady=(8, 8))
        self.preview_labels[event] = main
        self.preview_warning_labels[event] = warning
        self.preview_frames[event] = frame
        if event in self._preview_events:
            frame.pack(fill='x', padx=28, pady=(0, 10))

    def _insert_selected(self, event):
        """变量下拉未选择时插入动作保持无副作用。"""
        variable, options = self.variables[event]
        name = options.get(variable.get())
        if name:
            self.insert_variable(event, name)

    def insert_variable(self, event, variable):
        """只在当前正文光标位置插入事件合法的占位符。"""
        if variable not in self._variable_names(event):
            raise ValueError('未知通知变量')
        if not path_writable(self.model, ('push', 'templates', event, 'content')):
            return
        if event not in self._expanded_events:
            self.expand_event(event)
        text = self.fields[('push', 'templates', event, 'content')][1]
        text.insert('insert', '{' + variable + '}')
        self._text_changed(('push', 'templates', event, 'content'), text)

    def _sync_preview_states(self):
        """刷新重建后恢复已展开预览的按钮文字与可见性。"""
        for event in self._preview_events & self.preview_buttons.keys():
            self.preview_buttons[event].configure(text='收起预览')
            if event in self.preview_frames:
                self.preview_frames[event].pack(fill='x', padx=28, pady=(0, 10))

    def preview_text(self, event):
        """返回当前预览的完整文本（正文与警告块）。"""
        parts = [self._preview_text.get(event, ''),
                 self._preview_warnings.get(event, '')]
        return '\n'.join(part for part in parts if part)

    def preview_event(self, event):
        """预览当前草稿，不触发通知、截图或上传。"""
        if event not in self._expanded_events:
            self.expand_event(event)
        frame = self.preview_frames[event]
        if event in self._preview_events:
            self._preview_events.remove(event)
            self.preview_buttons[event].configure(text='预览消息')
            frame.pack_forget()
            return
        self._preview_events.add(event)
        self.preview_buttons[event].configure(text='收起预览')
        snapshot = self.model.snapshot()
        try:
            title, content = preview_template(snapshot, event)
        except (ValueError, KeyError, TypeError):
            self._preview_text[event] = '示例预览：通知模板变量或格式无效'
            self._preview_warnings.pop(event, None)
        else:
            self._preview_text[event] = f'示例预览\n{title}\n{content}'
            try:
                conditions = screenshot_conditions(snapshot, event)
            except (KeyError, TypeError, ValueError):
                detail = '截图条件：待校验'
            else:
                capture = self._safe_get(
                    ('push', 'templates', event, 'capture_screenshot'))
                detail = ('截图被阻止，请检查配置文件：capture_screenshot 为 false'
                          if capture is False else
                          '截图条件：' + ('满足' if all(conditions.values()) else '未满足'))
            self._preview_warnings[event] = detail
        self.preview_labels[event].configure(text=self._preview_text.get(event, ''))
        warning = self.preview_warning_labels[event]
        warning.configure(text=self._preview_warnings.get(event, ''))
        if self._preview_warnings.get(event):
            warning.pack(fill='x', padx=12, pady=(4, 8))
        frame.pack(fill='x', padx=28, pady=(0, 10))

    # ---------------------------------------------------------------- 刷新
    def refresh(self):
        """同步数字输入及列表，不覆盖未展示的 YAML 字段。"""
        if self.generation != self.model.document_generation:
            self._expanded_channel = None
            self._expanded_events.clear()
            self._preview_events.clear()
            self._preview_text.clear()
            self._preview_warnings.clear()
            self.generation = self.model.document_generation
        self._refreshing = True
        try:
            self.fields = {path: value for path, value in self.fields.items()
                           if len(path) == 3 and path[:2] == ('push', 'retry')}
            for path, (variable, widget, kind) in self.fields.items():
                raw = self._safe_input(path) if kind == 'int' else str(self._safe_get(path) or '')
                variable.set(raw)
                if widget is self.retry_spinner.entry._entry and widget.get() != raw:
                    self.retry_spinner.set(raw)
            self.retry_spinner.set_readonly(
                not path_writable(self.model, ('push', 'retry', 'max_count')))
            self._render_channels()
            self._render_events()
            snapshot = self.model.snapshot()
            push = snapshot.get('push')
            push_invalid = 'push' in snapshot and not isinstance(push, Mapping)
            self.structure_hint.configure(text=STRUCTURE_HINT if push_invalid else '')
            self.add_channel_button.configure(
                state='disabled' if push_invalid else 'normal')
            for path, (_variable, widget, _kind) in self.fields.items():
                if widget is self.retry_spinner.entry._entry:
                    continue
                widget.configure(
                    state='normal' if path_writable(self.model, path) else 'disabled')
        finally:
            self._refreshing = False

    def _focus_widget(self, widget):
        """滚动到目标控件并聚焦，使诊断字段真实进入可见区域。"""
        scroll_to_widget(self, widget)
        activate_and_focus(widget)

    def focus_issue(self, path):
        """对诊断的通道或事件展开对应行并滚动聚焦字段。"""
        path = tuple(path)
        if path[:len(CHANNELS)] == CHANNELS and len(path) > len(CHANNELS):
            source_index = path[len(CHANNELS)]
            row_index = next((row for row, item in enumerate(self.channel_rows)
                              if item.get('source_index') == source_index), None)
            if isinstance(source_index, int) and row_index is not None:
                self._expanded_channel = row_index
                self.refresh()
                widget = self.channel_entries.get(path[-1],
                                                  self.channel_rows[row_index]['toggle'])
                self._focus_widget(widget)
            return
        if path[:2] == ('push', 'templates') and len(path) > 2:
            self._expanded_events.add(path[2])
            self.refresh()
        field = self.fields.get(path)
        if field:
            self._focus_widget(field[1])
