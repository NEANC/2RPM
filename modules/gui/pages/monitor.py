#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""监视目标、模式及时间表单。"""

from tkinter import filedialog, ttk

from modules.gui.pages.base import Page as BasePage
from modules.monitor_target import resolve_monitor_target


class Page(BasePage):
    """配置等待或主动启动模式。"""

    def __init__(self, parent, model):
        """创建基础目标与高级运行设置。"""
        super().__init__(parent, model)
        self.mode = ttk.Combobox(self.body, values=('watch', 'launch'), state='readonly')
        ttk.Label(self.body, text='模式：watch 等待 / launch 主动启动').pack(anchor='w')
        self.mode.pack(fill='x')
        self.mode.bind('<<ComboboxSelected>>', self._switch)
        self.field(self.body, '进程名 / 文件 / 计划任务', ('monitor', 'target'))
        ttk.Button(self.body, text='浏览目标文件', command=self._browse).pack(anchor='w')
        self.field(self.body, '等待时限', ('monitor', 'common', 'max_wait'))
        self.field(self.body, '超时告警间隔', ('monitor', 'common', 'timeout_interval'))
        self.section_for_issue.pack(fill='x')
        body = self.section_for_issue.body
        for label, path, kind in (
                ('检查间隔', ('monitor', 'common', 'check_interval'), 'text'),
                ('轮询间隔', ('monitor', 'common', 'loop_interval'), 'text'),
                ('任务回溯（分钟）', ('monitor', 'watch', 'lookback_minutes'), 'int'),
                ('启动参数原文', ('monitor', 'launch', 'args'), 'optional'),
                ('启动工作目录', ('monitor', 'launch', 'cwd'), 'optional')):
            self.field(body, label, path, kind=kind)
        self.preview = ttk.Label(self.body, text='目标路径预览不执行目标')
        self.preview.pack(fill='x')
        self.refresh()

    def _switch(self, _event=None):
        """切换模式时保留各自内存目标。"""
        self.model.switch_mode(self.mode.get())
        self.refresh()

    def _browse(self):
        """选择本地目标，不主动启动它。"""
        path = filedialog.askopenfilename(parent=self)
        if path:
            self.set_field(('monitor', 'target'), path)

    def refresh(self):
        """刷新模式及目标解释预览。"""
        super().refresh()
        self.mode.set(self.model.get(('monitor', 'mode')))
        try:
            config_dir = self.model.document.path.parent if self.model.document.path else self.winfo_toplevel()._gui_program_dir
            target = resolve_monitor_target(
                self.mode.get(), self.model.get(('monitor', 'target')),
                str(config_dir), self.model.get(('monitor', 'launch', 'cwd')))
            self.preview.configure(text=f'解析结果：{target.value}；工作目录：{target.cwd or "按 CLI 规则确定"}')
        except (ValueError, AttributeError, TypeError):
            self.preview.configure(text='目标路径待校验')
