#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""ctk-kit 通道列表、事件模板与无外发消息预览。"""

from collections.abc import Mapping
import tkinter as tk
from tkinter import messagebox

import ctk_kit

from modules.gui.appearance import FONT_BODY, FONT_CARD_TITLE, FONT_SMALL, FONT_TITLE
from modules.gui.forms import event_variables, preview_template, provider_fields, screenshot_conditions
from modules.gui.widgets import PRESAVE_HINT, STRUCTURE_HINT, path_writable, scroll_to_widget
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
        self.variables = {}
        self._event_switches = {}
        self._refreshing = False
        self._expanded_channel = None
        self._expanded_events = set()
        self._preview_events = set()
        self._preview_text = {}
        self.generation = self.model.document_generation
        header = ctk_kit.CTkFrame(self, fg_color='transparent')
        header.pack(fill='x', pady=(0, 12))
        ctk_kit.CTkLabel(header, text='消息推送设置', font=FONT_TITLE).pack(side='left')
        self.notify_button = ctk_kit.CTkButton(header, text='通知测试', font=FONT_BODY,
                                               command=app.notify)
        self.notify_button.pack(side='right')
        self.structure_hint = ctk_kit.CTkLabel(self, text='', font=FONT_BODY, anchor='w')
        self.structure_hint.pack(fill='x', pady=(0, 8))
        card = self._card('推送通道')
        self.add_channel_button = ctk_kit.CTkButton(
            card, text='添加通道', font=FONT_BODY, command=self.open_add_dialog)
        self.add_channel_button.pack(anchor='e', padx=16)
        self.channel_body = ctk_kit.CTkFrame(card, fg_color='transparent')
        self.channel_body.pack(fill='x', padx=16, pady=(4, 8))
        self.channel_hint = ctk_kit.CTkLabel(card, text='', font=FONT_SMALL, anchor='w')
        self.channel_hint.pack(fill='x', padx=16, pady=(0, 8))
        card = self._card('通知事件与消息')
        self.events_body = ctk_kit.CTkFrame(card, fg_color='transparent')
        self.events_body.pack(fill='x', padx=16, pady=(4, 8))
        self.events_hint = ctk_kit.CTkLabel(card, text='', font=FONT_SMALL, anchor='w')
        self.events_hint.pack(fill='x', padx=16, pady=(0, 8))
        card = self._card('错误处理')
        for label, name in (('重试间隔', 'interval'), ('最大重试次数', 'max_count')):
            row = ctk_kit.CTkFrame(card, fg_color='transparent')
            row.pack(fill='x', padx=16, pady=8)
            ctk_kit.CTkLabel(row, text=label, font=FONT_BODY, width=130,
                             anchor='w').pack(side='left')
            self._entry(row, ('push', 'retry', name), numeric=name == 'max_count')
        self.refresh()

    def _card(self, title):
        """绘制单组业务卡片。"""
        card = ctk_kit.CTkFrame(self, corner_radius=12)
        card.pack(fill='x', pady=8)
        ctk_kit.CTkLabel(card, text=title, font=FONT_CARD_TITLE).pack(anchor='w', padx=16, pady=(12, 4))
        return card

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

    def _entry(self, parent, path, *, numeric=False):
        """绑定固定字段，保留数字原始输入。"""
        variable = tk.StringVar(master=self, value=(self._safe_input(path) if numeric else
                                                   str(self._safe_get(path) or '')))
        widget = ctk_kit.CTkEntry(parent, textvariable=variable, font=FONT_BODY)
        widget.pack(side='left', fill='x', expand=True, padx=4)
        variable.trace_add('write', lambda *_: self._changed(path, variable, numeric))
        self.fields[path] = (variable, widget, 'int' if numeric else 'text')
        return widget

    def _changed(self, path, variable, numeric):
        """模型刷新期间或结构异常时不写回旧值。"""
        if not self._refreshing and path_writable(self.model, path):
            if numeric:
                self.model.set_input(path, variable.get(), kind='int')
            else:
                self.model.set(path, variable.get())

    def _text_changed(self, path, widget):
        """同步正文编辑器的当前文本。"""
        if not self._refreshing and path_writable(self.model, path):
            self.model.set(path, widget.get('1.0', 'end-1c'))

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
            self.channel_rows[index]['status'].configure(text=self._status(channel))

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
            self.app.status.configure(text=PRESAVE_HINT)
            return
        if identity != (self.model.document_generation, self.model.revision):
            return
        if not messagebox.askyesno('删除通道', '删除该推送通道？', parent=self):
            return
        channels = self._safe_get(CHANNELS, [])
        channels.pop(source_index)
        self.model.set(CHANNELS, channels)
        self._expanded_channel = None
        self.refresh()
        self.model.on_list_edit()

    def open_add_dialog(self):
        """未选择 provider 时不能添加，关闭或取消均不修改配置。"""
        dialog = ctk_kit.CTkToplevel(self)
        dialog.title('添加通道')
        dialog.geometry('400x220')
        dialog.transient(self.winfo_toplevel())
        panel = ctk_kit.CTkFrame(dialog, corner_radius=12)
        panel.pack(fill='both', expand=True, padx=16, pady=16)
        ctk_kit.CTkButton(panel, text='×', width=32, command=dialog.destroy).pack(anchor='e')
        ctk_kit.CTkLabel(panel, text='选择通道', font=FONT_CARD_TITLE).pack(anchor='w', padx=12)
        names = ['请选择通道', *PROVIDERS]
        selected = ctk_kit.CTkOptionMenu(panel, values=names, font=FONT_BODY)
        selected.set(names[0])
        selected.pack(fill='x', padx=12, pady=12)
        row = ctk_kit.CTkFrame(panel, fg_color='transparent')
        row.pack(anchor='e', padx=12)
        ctk_kit.CTkButton(row, text='取消', command=dialog.destroy).pack(side='left', padx=4)
        add = ctk_kit.CTkButton(row, text='添加', state='disabled')
        def submit():
            """确认当前选定通道并关闭弹窗。"""
            if selected.get() in PROVIDERS:
                self.add_channel(PROVIDERS[selected.get()])
                dialog.destroy()
        add.configure(command=submit)
        add.pack(side='left')
        selected.configure(command=lambda value: add.configure(
            state='normal' if value in PROVIDERS else 'disabled'))
        dialog.grab_set()

    def expand_channel(self, index):
        """只展开所点击的行。"""
        self._expanded_channel = None if self._expanded_channel == index else index
        self.refresh()

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
            line = ctk_kit.CTkFrame(self.channel_body, corner_radius=8)
            line.pack(fill='x', pady=4)
            top = ctk_kit.CTkFrame(line, fg_color='transparent')
            top.pack(fill='x', padx=8, pady=6)
            provider = channel.get('provider')
            provider = provider if isinstance(provider, str) else 'unknown'
            status_text = self._status(channel)
            toggle = ctk_kit.CTkButton(top, text=f'{index + 1}. {provider}  '
                                          + ('⌄' if self._expanded_channel == index else '›'),
                                         font=FONT_BODY, fg_color='transparent',
                                         command=lambda i=index: self.expand_channel(i))
            toggle.pack(side='left', fill='x', expand=True)
            colors = (('#067647', '#12B76A') if status_text == '已配置'
                      else ('#9A6700', '#F0B429'))
            status = ctk_kit.CTkLabel(top, text=status_text, font=FONT_SMALL,
                                      text_color=colors)
            status.pack(side='left', padx=8)
            ctk_kit.CTkButton(top, text='删除', width=60,
                              text_color=('#B42318', '#FF7575'),
                              command=lambda i=index: self.remove_channel(i)).pack(side='left')
            self.channel_rows.append({'toggle': toggle, 'status': status,
                                      'provider': None, 'source_index': source_index})
            if index != self._expanded_channel:
                continue
            selector_row = ctk_kit.CTkFrame(line, fg_color='transparent')
            selector_row.pack(fill='x', padx=12, pady=(6, 2))
            ctk_kit.CTkLabel(selector_row, text='通道类型', font=FONT_BODY,
                             width=130, anchor='w').pack(side='left')
            values = list(PROVIDERS.values())
            if provider not in values:
                values.append(provider)
            selector = ctk_kit.CTkOptionMenu(
                selector_row, values=values, font=FONT_BODY,
                command=lambda value, i=index: self.change_channel_provider(i, value))
            selector.set(provider)
            selector.pack(side='left', fill='x', expand=True, padx=4)
            self.channel_rows[index]['provider'] = selector
            self.channel_entries['provider'] = selector
            fields = provider_fields(provider)
            for field in fields:
                row = ctk_kit.CTkFrame(line, fg_color='transparent')
                row.pack(fill='x', padx=12, pady=3)
                ctk_kit.CTkLabel(row, text=field.label + (' *' if field.required else ''),
                                 font=FONT_BODY, width=130, anchor='w').pack(side='left')
                value = tk.StringVar(master=self, value=str(channel.get(field.name, '')))
                entry = ctk_kit.CTkEntry(row, textvariable=value, font=FONT_BODY,
                                         show='●' if field.secret else '')
                entry.pack(side='left', fill='x', expand=True)
                value.trace_add('write', lambda *_, i=index, name=field.name, var=value:
                                self._set_channel(i, name, var.get()))
                self.channel_entries[field.name] = entry
                if field.secret:
                    ctk_kit.CTkButton(row, text='显示', width=55,
                                      command=lambda widget=entry: widget.configure(
                                          show='' if widget.cget('show') else '●')).pack(side='left')

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
        self.refresh()

    def _render_events(self):
        """在展开的事件卡片中编辑标题、正文并提供示例预览。"""
        for child in self.events_body.winfo_children():
            child.destroy()
        self.preview_labels = {}
        self.variables = {}
        self._event_switches = {}
        raw = self._safe_get(('push', 'templates'), None)
        templates = raw if isinstance(raw, Mapping) else {}
        self.events_hint.configure(
            text='' if isinstance(raw, Mapping) else '通知事件配置缺失或结构无效，请在 YAML 中检查')
        for event, label in EVENTS:
            path = ('push', 'templates', event)
            card = ctk_kit.CTkFrame(self.events_body, corner_radius=8)
            card.pack(fill='x', pady=4)
            row = ctk_kit.CTkFrame(card, fg_color='transparent')
            row.pack(fill='x', padx=12, pady=8)
            ctk_kit.CTkLabel(row, text=label, font=FONT_BODY).pack(side='left')
            enabled = ctk_kit.CTkSwitch(row, text='', font=FONT_BODY)
            enabled.configure(command=lambda key=event, widget=enabled:
                              self.model.set(('push', 'templates', key, 'enable'), bool(widget.get())))
            enabled.pack(side='right')
            self._event_switches[event] = enabled
            if not path_writable(self.model, (*path, 'enable')):
                enabled.configure(state='disabled')
            if isinstance(templates, dict) and isinstance(templates.get(event), dict) and templates[event].get('enable'):
                enabled.select()
            ctk_kit.CTkButton(row, text='编辑消息 ' + ('⌄' if event in self._expanded_events else '›'),
                              font=FONT_BODY,
                              command=lambda key=event: self.expand_event(key)).pack(side='right')
            if event not in self._expanded_events:
                continue
            ctk_kit.CTkLabel(card, text='标题', font=FONT_BODY).pack(anchor='w', padx=12)
            self._entry(card, (*path, 'title'))
            ctk_kit.CTkLabel(card, text='正文', font=FONT_BODY).pack(anchor='w', padx=12)
            text = ctk_kit.CTkTextbox(card, height=100, font=FONT_BODY, wrap='word')
            text.pack(fill='x', padx=12, pady=4)
            text.insert('1.0', str(self._safe_get((*path, 'content')) or ''))
            text.bind('<KeyRelease>', lambda _, key=(*path, 'content'), widget=text:
                      self._text_changed(key, widget))
            text.bind('<FocusOut>', lambda _, key=(*path, 'content'), widget=text:
                      self._text_changed(key, widget))
            text.bind('<<Paste>>', lambda _, key=(*path, 'content'), widget=text:
                      self.after_idle(lambda: self._text_changed(key, widget)))
            self.fields[(*path, 'content')] = (None, text, 'multiline')
            controls = ctk_kit.CTkFrame(card, fg_color='transparent')
            controls.pack(fill='x', padx=12, pady=6)
            names = self._variable_names(event)
            options = {f'{VARIABLE_LABELS.get(name, name)} ({name})': name for name in names}
            variable = ctk_kit.CTkOptionMenu(controls, values=list(options), font=FONT_BODY)
            variable.pack(side='left', fill='x', expand=True)
            self.variables[event] = (variable, options)
            ctk_kit.CTkButton(controls, text='插入正文', font=FONT_BODY,
                              command=lambda key=event: self.insert_variable(
                                  key, self.variables[key][1][self.variables[key][0].get()])).pack(side='left')
            ctk_kit.CTkButton(controls, text='预览消息', font=FONT_BODY,
                              command=lambda key=event: self.preview_event(key)).pack(side='left')
            preview = ctk_kit.CTkLabel(card, text=self._preview_text.get(event, ''), font=FONT_BODY,
                                       justify='left', anchor='w')
            self.preview_labels[event] = preview
            if event in self._preview_events:
                preview.pack(fill='x', padx=12, pady=8)

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

    def preview_event(self, event):
        """预览当前草稿，不触发通知、截图或上传。"""
        if event not in self._expanded_events:
            self.expand_event(event)
        preview = self.preview_labels[event]
        if event in self._preview_events:
            self._preview_events.remove(event)
            preview.pack_forget()
            return
        self._preview_events.add(event)
        snapshot = self.model.snapshot()
        try:
            title, content = preview_template(snapshot, event)
        except (ValueError, KeyError, TypeError):
            self._preview_text[event] = '示例预览：通知模板变量或格式无效'
        else:
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
            self._preview_text[event] = f'示例预览\n{title}\n{content}\n{detail}'
        preview.configure(text=self._preview_text[event])
        preview.pack(fill='x', padx=12, pady=8)

    def refresh(self):
        """同步数字输入及列表，不覆盖未展示的 YAML 字段。"""
        if self.generation != self.model.document_generation:
            self._expanded_channel = None
            self._expanded_events.clear()
            self._preview_events.clear()
            self._preview_text.clear()
            self.generation = self.model.document_generation
        self._refreshing = True
        try:
            self.fields = {path: value for path, value in self.fields.items()
                           if len(path) == 3 and path[:2] == ('push', 'retry')}
            for path, (variable, _widget, kind) in self.fields.items():
                variable.set(self._safe_input(path) if kind == 'int' else
                             str(self._safe_get(path) or ''))
            self._render_channels()
            self._render_events()
            snapshot = self.model.snapshot()
            push = snapshot.get('push')
            push_invalid = 'push' in snapshot and not isinstance(push, Mapping)
            self.structure_hint.configure(text=STRUCTURE_HINT if push_invalid else '')
            self.add_channel_button.configure(
                state='disabled' if push_invalid else 'normal')
            for path, (_variable, widget, _kind) in self.fields.items():
                widget.configure(
                    state='normal' if path_writable(self.model, path) else 'disabled')
        finally:
            self._refreshing = False

    def _focus_widget(self, widget):
        """滚动到目标控件并聚焦，使诊断字段真实进入可见区域。"""
        scroll_to_widget(self, widget)
        widget.focus_force()

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
