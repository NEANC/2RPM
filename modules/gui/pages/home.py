#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""独立配置主页：安全摘要、诊断及独立 CLI 启动。"""

from pathlib import Path

import ctk_kit

from modules.gui.appearance import COLORS, FONT_BODY, FONT_CARD_TITLE, FONT_TITLE
from modules.gui.home_summary import summary


class Page(ctk_kit.CTkScrollableFrame):
    """同一窗口内常驻的配置主页。"""

    def __init__(self, parent, app):
        """构造三张卡片并将动作委托给窗口控制器。"""
        super().__init__(parent, corner_radius=0)
        self.app = app
        header = ctk_kit.CTkFrame(self, fg_color='transparent')
        header.pack(fill='x', pady=(0, 12))
        ctk_kit.CTkLabel(header, text='主页', font=FONT_TITLE).pack(side='left')
        for label, action in (('打开配置', app.open), ('新建配置', app.new)):
            ctk_kit.CTkButton(header, text=label, command=action, font=FONT_BODY).pack(side='right', padx=4)
        card = self._card('配置摘要', header=True)
        self.save_as_button = ctk_kit.CTkButton(
            self.summary_header, text='另存为', command=app.save_as, font=FONT_BODY)
        self.save_as_button.pack(side='right', padx=4)
        self.save_button = ctk_kit.CTkButton(
            self.summary_header, text='保存', command=app.save, font=FONT_BODY)
        self.save_button.pack(side='right', padx=4)
        path_row = ctk_kit.CTkFrame(card, fg_color='transparent')
        path_row.pack(fill='x', padx=14)
        ctk_kit.CTkLabel(path_row, text='配置路径：', font=FONT_BODY).pack(side='left')
        self.path_value = ctk_kit.CTkLabel(
            path_row, text='', font=FONT_BODY, anchor='w',
            text_color=COLORS['accent'])
        self.path_value.pack(side='left', fill='x', expand=True)
        self.path_tip = ctk_kit.Tooltip(self.path_value, lambda: self._full_path)
        self.summary = ctk_kit.CTkLabel(card, text='', font=FONT_BODY, anchor='w', justify='left')
        self.summary.pack(fill='x', padx=14)
        self.groups = {}
        self.screenshot_rows = {}
        for title in ('通知事件', '事件截图', '外部程序'):
            group = ctk_kit.Collapsible(card, collapsed_text=title, expanded_text=title)
            group.button.configure(font=FONT_BODY, text_color=COLORS['text_primary'], anchor='w')
            group.detail_label.configure(font=FONT_BODY, text_color=COLORS['text_primary'])
            group.body.configure(fg_color='transparent')
            group.pack(fill='x', padx=14)
            self.groups[title] = group
        card = self._card('配置检查')
        ctk_kit.CTkButton(card, text='校验配置', command=app.validate, font=FONT_BODY).pack(anchor='e')
        self.check = ctk_kit.CTkLabel(card, text='尚未校验', font=FONT_BODY, anchor='w')
        self.check.pack(fill='x')
        self.check_body = ctk_kit.CTkFrame(card, fg_color='transparent')
        self.check_body.pack(fill='x')
        self.validation_rows = {}
        self.issue_hint = ctk_kit.CTkLabel(card, text='', font=FONT_BODY, anchor='w')
        self.issue_hint.pack(fill='x')
        card = self._card('运行配置')
        ctk_kit.CTkLabel(card, text='启动独立 CLI 进程运行本配置，关闭 GUI 不影响其运行。',
                         font=FONT_BODY, anchor='w').pack(fill='x')
        filename_row = ctk_kit.CTkFrame(card, fg_color='transparent')
        filename_row.pack(fill='x', padx=14)
        ctk_kit.CTkLabel(filename_row, text='当前配置：', font=FONT_BODY).pack(side='left')
        self.filename_value = ctk_kit.CTkLabel(
            filename_row, text='', font=FONT_BODY, anchor='w', text_color=COLORS['accent'])
        self.filename_value.pack(side='left', fill='x', expand=True)
        self.filename_tip = ctk_kit.Tooltip(self.filename_value, lambda: self._full_path)
        ctk_kit.CTkButton(card, text='启动 CLI', command=app.start_cli, font=FONT_BODY).pack(anchor='e')
        self.refresh()

    def _card(self, title, *, header=False):
        """创建统一内圆角内容卡片。"""
        card = ctk_kit.CTkFrame(self, corner_radius=12)
        card.pack(fill='x', pady=9, padx=4)
        title_parent = ctk_kit.CTkFrame(card, fg_color='transparent') if header else card
        if header:
            title_parent.pack(fill='x', padx=14, pady=(12, 4))
            self.summary_header = title_parent
        ctk_kit.CTkLabel(title_parent, text=title, font=FONT_CARD_TITLE, anchor='w').pack(
            side='left' if header else 'top', fill='x', expand=header,
            padx=0 if header else 14, pady=0 if header else (12, 4))
        return card

    def _rows(self, group, items):
        """重绘已展开容器中的安全状态行。"""
        for child in group.body.winfo_children():
            child.destroy()
        for name, state in items:
            row = ctk_kit.CTkFrame(group.body, fg_color='transparent')
            row.pack(fill='x', padx=12)
            ctk_kit.CTkLabel(row, text=name, font=FONT_BODY, anchor='w').pack(
                side='left', fill='x', expand=True)
            ctk_kit.CTkLabel(row, text=state, font=FONT_BODY, anchor='e').pack(side='right')

    def refresh(self):
        """由当前草稿计算状态，路径只描述配置文件。"""
        path = self.app.model.document.path
        if path is None:
            display = '未保存的新配置'
        else:
            try:
                display = str(path.relative_to(self.app.controller.program_dir))
            except ValueError:
                display = str(path)
        self._full_path = str(path) if path else '未保存的新配置'
        self.path_value.configure(text=display)
        self.filename_value.configure(text=Path(path).name if path else '未保存的新配置')
        text, conditions = summary(self.app.model.snapshot())
        lines = text.splitlines()
        self.summary.configure(text='\n'.join(lines[:5]))
        events = [line.split('：', 1) for line in lines[5:9]]
        external = [line.split('：', 1) for line in lines[9:12]]
        self.groups['通知事件'].set_detail(
            f'{sum(state == "启用" for _, state in events)}/{len(events)} 已启用')
        self.groups['事件截图'].set_detail(
            f'{sum(state == "条件满足" for _, state, _ in conditions)}/{len(conditions)} 条件满足')
        self.groups['外部程序'].set_detail(
            f'{sum(state == "已配置" for _, state in external)}/{len(external)} 已配置')
        self._rows(self.groups['通知事件'], events)
        self._rows(self.groups['外部程序'], external)
        group = self.groups['事件截图']
        expanded = {name for name, row in self.screenshot_rows.items()
                    if row['reasons'] is not None and row['reasons'].expanded}
        for child in group.body.winfo_children():
            child.destroy()
        self.screenshot_rows = {}
        for name, state, reasons in conditions:
            row = ctk_kit.CTkFrame(group.body, fg_color='transparent')
            row.pack(fill='x', padx=12)
            if reasons and state == '条件未满足':
                detail = ctk_kit.Collapsible(row, collapsed_text=name, expanded_text=name,
                                             expanded=name in expanded, detail=state)
                detail.button.configure(font=FONT_BODY, text_color=COLORS['text_primary'], anchor='w')
                detail.detail_label.configure(font=FONT_BODY, text_color=COLORS['text_primary'])
                detail.body.configure(fg_color='transparent')
                detail.pack(fill='x')
                for reason in reasons:
                    ctk_kit.CTkLabel(detail.body, text='· ' + reason, font=FONT_BODY,
                                     anchor='w').pack(fill='x', padx=20)
            else:
                detail = None
                ctk_kit.CTkLabel(row, text=name, font=FONT_BODY, anchor='w').pack(
                    side='left', fill='x', expand=True)
                ctk_kit.CTkLabel(row, text=state, font=FONT_BODY, anchor='e').pack(side='right')
            self.screenshot_rows[name] = {'reasons': detail}

    def invalidate_validation(self):
        """草稿已变化时清空旧校验结果与诊断落点。"""
        self.check.configure(text='尚未校验')
        self.issue_hint.configure(text='')
        self._clear_validation_rows()

    def _clear_validation_rows(self):
        """销毁旧分组行并清空可导航入口。"""
        for child in self.check_body.winfo_children():
            child.destroy()
        self.validation_rows = {}

    def show_validation(self, total, groups):
        """按页面分组展示问题数量及前往设置入口。"""
        self._clear_validation_rows()
        errors = sum(error for _name, error, _other, _issues in groups)
        others = total - errors
        self.check.configure(
            text=(f'存在 {total} 个问题（错误 {errors}，其他 {others}），请检查对应配置'
                  if total else '配置检查：无问题'))
        for name, error_count, other_count, issues in groups:
            row = ctk_kit.CTkFrame(self.check_body, fg_color='transparent')
            row.pack(fill='x', pady=2)
            label = ctk_kit.CTkLabel(
                row, text=f'{name}：{error_count + other_count} 个问题'
                          f'（错误 {error_count}，其他 {other_count}）',
                font=FONT_BODY, anchor='w')
            label.pack(fill='x')
            first_issue = next((issue for issue in issues if issue.severity == 'error'), issues[0])
            button = ctk_kit.CTkButton(
                row, text='前往设置', width=80, font=FONT_BODY,
                command=lambda target=name, issue=first_issue: self.app.focus_validation_issue(target, issue))
            button.pack(anchor='e')
            entries = []
            for issue in issues:
                item = ctk_kit.CTkFrame(row, fg_color='transparent')
                item.pack(fill='x', padx=12)
                category = '错误' if issue.severity == 'error' else '其他'
                issue_label = ctk_kit.CTkLabel(
                    item, text=f'{category} · {self._issue_category(issue.path)}',
                    font=FONT_BODY, anchor='w')
                issue_label.pack(side='left', fill='x', expand=True)
                link = ctk_kit.CTkButton(
                    item, text='前往设置', width=80, font=FONT_BODY,
                    command=lambda target=name, current=issue: self.app.focus_validation_issue(target, current))
                link.pack(side='right')
                entries.append({'button': link, 'label': issue_label})
            self.validation_rows[name] = {'label': label, 'button': button, 'issues': entries}

    @staticmethod
    def _issue_category(path):
        """仅从可信字段路径生成安全字段名称。"""
        if not path:
            return '配置结构与兼容性'
        if tuple(path) == ('push', 'screenshot', 'retention', 'max_days'):
            return '保留天数'
        if path[:3] == ('push', 'screenshot', 'image_host'):
            return '图床配置'
        if path[:3] == ('push', 'screenshot', 'targets'):
            return '截图目标'
        if path[:3] == ('push', 'push_channel_settings', 'channels'):
            return '通知通道'
        if path[:2] == ('push', 'retry'):
            return '重试设置'
        if path[:2] == ('push', 'templates'):
            return '通知事件'
        if tuple(path) == ('monitor', 'target'):
            return '监视目标'
        if path[:2] == ('monitor', 'common'):
            return '监视通用设置'
        return {'monitor': '监视配置', 'push': '消息与截图配置',
                'external': '外部程序配置', 'log': '日志配置'}.get(path[0], '配置结构与兼容性')

    def show_issue(self, code):
        """结构问题只显示类别，不显示未知诊断原文。"""
        self.issue_hint.configure(text='配置结构与兼容性问题，请检查 YAML 文件')

    def focus_issue(self, issue):
        """主页是无法定位到可编辑字段的诊断落点。"""
        self.show_issue('structure')
