#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""检查、日志位置及启动页。"""

from tkinter import ttk

from modules.gui.pages.base import Page as BasePage


class Page(BasePage):
    """只显示脱敏结构摘要和日志设置。"""

    def __init__(self, parent, model):
        """创建摘要标签及折叠日志字段。"""
        super().__init__(parent, model)
        self.summary = ttk.Label(self.body, justify='left', wraplength=650)
        self.summary.pack(fill='x')
        self.section_for_issue.pack(fill='x')
        self.field(self.section_for_issue.body, '日志目录', ('log', 'log_directory'))
        self.field(self.section_for_issue.body, '最大日志数', ('log', 'max_log_files'), kind='int')
        self.field(self.section_for_issue.body, '保留天数', ('log', 'retention_days'), kind='int')
        self.refresh()

    def refresh(self):
        """用静态字段而非原始配置生成安全摘要。"""
        super().refresh()
        snapshot = self.model.snapshot()
        monitor = snapshot['monitor']
        push = snapshot['push']
        text = (f"模式：{monitor['mode']}\n目标：已配置\n"
                f"等待：{monitor['common']['max_wait']}\n"
                f"通知通道：{len(push['push_channel_settings']['channels'])} 个\n"
                f"截图目标：{len(push['screenshot']['targets'])} 个")
        self.summary.configure(text=text)
