#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""检查、日志位置及启动页。"""

from collections.abc import Mapping
from tkinter import ttk

from modules.config import DEFAULT_VALUES
from modules.gui.forms import screenshot_conditions
from modules.gui.pages.base import Page as BasePage


class Page(BasePage):
    """只显示脱敏结构摘要和日志设置。"""

    def __init__(self, parent, model):
        """创建摘要标签及折叠日志字段。"""
        super().__init__(parent, model)
        self.summary = ttk.Label(self.body, justify='left', wraplength=650)
        self.summary.pack(fill='x')
        self.issue_hint = ttk.Label(self.body, text='')
        self.issue_hint.pack(fill='x')
        self.issue_anchor = ttk.Button(self.body, text='配置结构与兼容性问题')
        self.issue_anchor.pack(anchor='w')
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
        common = monitor['common']
        mode = monitor['mode'] if monitor['mode'] in ('watch', 'launch') else '待校验'
        wait = common['max_wait'] if isinstance(common, Mapping) else None
        if wait not in (DEFAULT_VALUES['monitor']['common']['max_wait'], '待校验'):
            wait = '已配置' if isinstance(wait, str) and wait.strip() else '待校验'
        if wait is None:
            wait = '待校验'
        events = push['templates']
        actions = snapshot['external']
        event_names = ('on_end', 'on_timeout', 'on_wait_timeout', 'on_external')
        event_status = '、'.join(
            f'{name}：{("启用" if events[name]["enable"] is True else "关闭") if isinstance(events, Mapping) and isinstance(events.get(name), Mapping) else "待校验"}'
            for name in event_names)
        capture_status = '、'.join(
            f'{name}：{("条件满足" if all(screenshot_conditions(snapshot, name).values()) else "条件未满足") if isinstance(events, Mapping) and isinstance(events.get(name), Mapping) else "待校验"}'
            for name in event_names)
        action_status = '、'.join(
            f'{name}：{("已配置" if isinstance(actions[name], str) and actions[name].strip() else "未配置") if isinstance(actions, Mapping) and name in actions else "待校验"}'
            for name in ('on_end', 'on_timeout', 'on_wait_timeout'))
        target = monitor['target']
        target_status = ('已配置' if isinstance(target, str) and target.strip()
                         else '未配置' if isinstance(target, str) else '待校验')
        text = (f"模式：{mode}\n目标：{target_status}\n"
                f"等待：{wait}\n"
                f"通知通道：{len(push['push_channel_settings']['channels'])} 个\n"
                f"通知事件：{event_status}\n"
                f"截图目标：{len(push['screenshot']['targets'])} 个\n"
                f"事件截图：{capture_status}\n外部动作：{action_status}")
        self.summary.configure(text=text)

    def show_issue(self, code):
        """仅显示已知诊断类别，不展示配置值及未知诊断文本。"""
        category = code if code in ('root_type', 'legacy', 'mapping', 'required') else 'structure'
        self.issue_hint.configure(text=f'{category}：请检查配置结构或旧布局')
        self.focus_widget(self.issue_anchor)
