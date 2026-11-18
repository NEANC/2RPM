#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""按已批准 HTML 原型重绘的主页：摘要、检查与独立 CLI 启动。"""

from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont

import ctk_kit

from modules.gui.appearance import (COLORS, FONT_BODY, FONT_BUTTON,
                                    FONT_CARD_TITLE, FONT_TITLE)
from modules.gui.home_summary import summary as build_summary
from modules.gui.widgets import ChevronCollapsible

# 原型单窗内容宽约 440 CSS 像素，桌面窗口为 900 逻辑像素，比例约 2 倍。
PAGE_PAD = 28
CARD_GAP = 32
CARD_PAD = 28
CARD_RADIUS = 20
CARD_BORDER = 2
# Tk 不接受带 alpha 的颜色，按原型把半透明卡片/边框合成到面板底色。
CARD_SURFACE = ('#FAFAFA', '#232323')
CARD_BORDER_COLOR = ('#E1E1E1', '#3A3A3A')
CARD_HOVER_COLOR = ('#EDEDED', '#2E2E2E')
ROW_PADY = (10, 0)
BUTTON = dict(width=120, height=52, radius=12, font=FONT_BUTTON)


class Page(ctk_kit.CTkScrollableFrame):
    """同一窗口内常驻的配置主页。"""

    def __init__(self, parent, app):
        """构造页头、三张卡片与折叠摘要容器。"""
        super().__init__(parent, corner_radius=0)
        self.app = app
        self._dark = int(ctk_kit.get_appearance_mode() == 'Dark')
        self.groups = {}
        self.screenshot_rows = {}
        self.summary_rows = {}
        self.validation_rows = {}
        self.summary_text = ''
        self._build()
        self.refresh()

    # ---------------------------------------------------------------- 页头
    def _build_header(self):
        """主页标题左侧，两个强调色动作按钮右侧。"""
        header = ctk_kit.CTkFrame(self, fg_color='transparent')
        header.pack(fill='x', padx=PAGE_PAD, pady=(PAGE_PAD, 0))
        ctk_kit.CTkLabel(header, text='主页', font=FONT_TITLE).pack(side='left')
        self.open_button = self._primary(header, '打开配置', self.app.open)
        self.open_button.pack(side='right')
        self.new_button = self._primary(header, '新建配置', self.app.new)
        self.new_button.pack(side='right', padx=(0, 10))

    # ---------------------------------------------------------------- 卡片
    def _card(self):
        """创建原型比例的圆角内容卡并留出卡片间距。"""
        card = ctk_kit.Card(self, corner_radius=CARD_RADIUS,
                            border_width=CARD_BORDER,
                            border_color=CARD_BORDER_COLOR[self._dark],
                            fg_color=CARD_SURFACE[self._dark])
        card.pack(fill='x', padx=PAGE_PAD, pady=(CARD_GAP, 0))
        return card

    @staticmethod
    def _primary(parent, text, command, **kwargs):
        """原型里的动作按钮统一使用强调色主按钮。"""
        options = dict(BUTTON)
        options.update(kwargs)
        return ctk_kit.PrimaryButton(parent, text, command, **options)

    def _text(self, parent, text, *, muted=False, **kwargs):
        """正文标签；说明文字使用次要色。"""
        return ctk_kit.CTkLabel(
            parent, text=text, font=FONT_BODY,
            text_color=(COLORS['text_secondary'][self._dark] if muted
                        else COLORS['text_primary'][self._dark]),
            **kwargs)

    def _head(self, card, title):
        """卡片标题行：标题在左，调用方在右侧追加动作。"""
        head = ctk_kit.CTkFrame(card, fg_color='transparent')
        head.pack(fill='x', padx=CARD_PAD, pady=(CARD_PAD, 14))
        ctk_kit.CTkLabel(head, text=title, font=FONT_CARD_TITLE).pack(side='left')
        return head

    # ---------------------------------------------------------------- 摘要
    def _build_summary_card(self):
        """配置摘要卡：保存入口、键值行与三级折叠摘要。"""
        card = self._card()
        head = self._head(card, '配置摘要')
        self.summary_header = head
        self.save_as_button = self._primary(head, '另存为', self.app.save_as,
                                            width=100)
        self.save_as_button.pack(side='right', padx=(10, 0))
        self.save_button = self._primary(head, '保存', self.app.save,
                                         width=80)
        self.save_button.pack(side='right')
        for index, name in enumerate(('配置路径', '运行方式',
                                      '目标 · 最长等待', '通知通道 · 截图目标'), 1):
            row = card.row(index, padx=CARD_PAD, pady=ROW_PADY)
            label = self._text(row, name, muted=True)
            label.pack(side='left')
            value = self._text(row, '', anchor='e')
            value.pack(side='right')
            self.summary_rows[name] = (label, value)
        self.path_value = self.summary_rows['配置路径'][1]
        self.path_tip = ctk_kit.Tooltip(self.path_value, lambda: self._full_path)
        for title in ('通知事件', '事件截图', '外部程序'):
            group = ChevronCollapsible(card, collapsed_text=title,
                                        expanded_text=title, button_height=44)
            group.button.configure(font=FONT_BODY, height=44,
                                   text_color=COLORS['text_secondary'][self._dark],
                                   hover_color=CARD_HOVER_COLOR[self._dark])
            group.detail_label.configure(
                font=FONT_BODY, text_color=COLORS['text_primary'][self._dark])
            group.body.configure(fg_color='transparent')
            group.pack(fill='x', padx=CARD_PAD, pady=(2, 0))
            self.groups[title] = group
        self._summary_bottom = ctk_kit.CTkFrame(card, fg_color='transparent',
                                                height=18)
        self._summary_bottom.pack(fill='x')

    def _rows(self, group, items):
        """重绘折叠容器里的「名称 / 状态」两列行。"""
        for child in group.body.winfo_children():
            child.destroy()
        for name, state in items:
            row = ctk_kit.CTkFrame(group.body, fg_color='transparent')
            row.pack(fill='x', padx=CARD_PAD, pady=6)
            self._text(row, name, muted=True, anchor='w').pack(
                side='left', fill='x', expand=True)
            self._text(row, state, anchor='e').pack(side='right')

    # ---------------------------------------------------------------- 检查
    def _build_check_card(self):
        """配置检查卡：校验入口、结果说明与可导航问题行。"""
        card = self._card()
        head = self._head(card, '配置检查')
        self.validate_button = self._primary(head, '校验配置', self.app.validate)
        self.validate_button.pack(side='right')
        self.check = self._text(card, '尚未校验', muted=True, anchor='w')
        self.check.pack(fill='x', padx=CARD_PAD)
        # 空 CTkFrame 默认请求 200×200，会让检查卡出现大块空白；
        # 用自适应的原生容器承载问题行，按内容高度收缩。
        self.check_body = tk.Frame(card, bd=0, highlightthickness=0,
                                   bg=CARD_SURFACE[self._dark])
        self.check_body.pack(fill='x', padx=CARD_PAD)
        self.issue_hint = self._text(card, '', muted=True, anchor='w')
        self.issue_hint.pack(fill='x', padx=CARD_PAD, pady=(0, CARD_PAD))

    def _link(self, parent, command):
        """行内「前往设置」文字链接（原生按钮的透明底样式）。"""
        return ctk_kit.CTkButton(
            parent, text='前往设置', command=command, width=100, height=40,
            corner_radius=10, fg_color='transparent',
            hover_color=CARD_HOVER_COLOR[self._dark],
            text_color=COLORS['accent'][self._dark], font=FONT_BODY)

    # ---------------------------------------------------------------- 运行
    def _build_run_card(self):
        """运行配置卡：说明、当前文件名与启动 CLI 入口。"""
        card = self._card()
        ctk_kit.CTkLabel(card, text='运行配置', font=FONT_CARD_TITLE).pack(
            anchor='w', padx=CARD_PAD, pady=(CARD_PAD, 8))
        self._text(
            card, '启动独立 CLI 进程运行本配置，关闭 GUI 不影响其运行。',
            muted=True, anchor='w').pack(fill='x', padx=CARD_PAD)
        foot = ctk_kit.CTkFrame(card, fg_color='transparent')
        foot.pack(fill='x', padx=CARD_PAD, pady=(18, CARD_PAD))
        self._text(foot, '当前配置 ', muted=True).pack(side='left')
        self.filename_value = self._text(
            foot, '', anchor='w')
        self.filename_value.configure(text_color=COLORS['accent'][self._dark])
        self.filename_value.pack(side='left')
        self.filename_tip = ctk_kit.Tooltip(self.filename_value,
                                            lambda: self._full_path)
        self.start_button = self._primary(foot, '启动 CLI', self.app.start_cli)
        self.start_button.pack(side='right')

    def _build(self):
        """按原型顺序装配三张卡片。"""
        self._build_header()
        self._build_summary_card()
        self._build_check_card()
        self._build_run_card()
        ctk_kit.CTkFrame(self, fg_color='transparent', height=CARD_GAP).pack(
            fill='x')

    # ---------------------------------------------------------------- 刷新
    def refresh(self):
        """由当前草稿计算安全状态，不显示凭证、目标原文或未知诊断。"""
        path = self.app.model.document.path
        self._full_path = str(path) if path else '未保存的新配置'
        self.path_value.configure(text=self._display_path(path))
        self.filename_value.configure(
            text=Path(path).name if path else '未保存的新配置')
        text, conditions = build_summary(self.app.model.snapshot())
        self.summary_text = text
        lines = text.splitlines()
        values = dict(line.split('：', 1) for line in lines[:5])
        self.summary_rows['运行方式'][1].configure(text=values['运行方式'])
        self.summary_rows['目标 · 最长等待'][1].configure(
            text=f'{values["目标位置"]} · {values["最长等待"]}')
        self.summary_rows['通知通道 · 截图目标'][1].configure(
            text=f'{values["通知通道"]} · {values["截图目标"]}')
        events = [line.split('：', 1) for line in lines[5:9]]
        external = [line.split('：', 1) for line in lines[9:12]]
        self.groups['通知事件'].set_detail(
            f'{sum(state == "启用" for _, state in events)} 个启用 / {len(events)} 个')
        self.groups['事件截图'].set_detail(
            f'{sum(state == "条件满足" for _, state, _ in conditions)} 个条件满足 '
            f'/ {len(conditions)} 个')
        self.groups['外部程序'].set_detail(
            f'{sum(state == "已配置" for _, state in external)} 个已配置 '
            f'/ {len(external)} 个')
        self._rows(self.groups['通知事件'], events)
        self._rows(self.groups['外部程序'], external)
        self._screenshot_rows(conditions)

    def _screenshot_rows(self, conditions):
        """重绘事件截图行；仅未满足条件时有二级原因折叠。"""
        group = self.groups['事件截图']
        expanded = {name for name, row in self.screenshot_rows.items()
                    if row['reasons'] is not None and row['reasons'].expanded}
        for child in group.body.winfo_children():
            child.destroy()
        self.screenshot_rows = {}
        for name, state, reasons in conditions:
            row = ctk_kit.CTkFrame(group.body, fg_color='transparent')
            row.pack(fill='x', padx=CARD_PAD, pady=4)
            if reasons and state == '条件未满足':
                detail = ChevronCollapsible(
                    row, collapsed_text=name, expanded_text=name,
                    expanded=name in expanded, detail=state, button_height=40)
                detail.button.configure(
                    font=FONT_BODY,
                    text_color=COLORS['text_secondary'][self._dark],
                    hover_color=CARD_HOVER_COLOR[self._dark])
                detail.detail_label.configure(
                    font=FONT_BODY, text_color=COLORS['text_primary'][self._dark])
                detail.body.configure(fg_color='transparent')
                detail.pack(fill='x')
                for reason in reasons:
                    self._text(detail.body, '· ' + reason, muted=True,
                                anchor='w').pack(fill='x', padx=CARD_PAD, pady=4)
            else:
                detail = None
                self._text(row, name, muted=True, anchor='w').pack(
                    side='left', fill='x', expand=True)
                self._text(row, state, anchor='e').pack(side='right')
            self.screenshot_rows[name] = {'reasons': detail}

    def _display_path(self, path):
        """程序目录内显示相对路径，否则显示完整路径并按可用宽度截断。"""
        if path is None:
            return '未保存的新配置'
        try:
            text = str(Path(path).relative_to(self.app.controller.program_dir))
        except ValueError:
            text = str(path)
        return self._shorten(text)

    def _shorten(self, text):
        """用与正文相同的字体测量，超宽时尾部省略，完整值由提示展示。"""
        try:
            width = max(220, self.winfo_width() - 520)
            font = tkfont.Font(font=FONT_BODY)
            if font.measure(text) <= width:
                return text
            while text and font.measure(text + '…') > width:
                text = text[:-1]
            return text + '…'
        except (tkfont.TclError, RuntimeError):
            return text

    # ---------------------------------------------------------------- 校验
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
            label = self._text(
                row, f'{name}：{error_count + other_count} 个问题'
                     f'（错误 {error_count}，其他 {other_count}）', anchor='w')
            label.pack(fill='x')
            first_issue = next((issue for issue in issues if issue.severity == 'error'),
                               issues[0])
            button = self._link(
                row, lambda target=name, issue=first_issue:
                self.app.focus_validation_issue(target, issue))
            button.pack(anchor='e')
            entries = []
            for issue in issues:
                item = ctk_kit.CTkFrame(row, fg_color='transparent')
                item.pack(fill='x', padx=12)
                category = '错误' if issue.severity == 'error' else '其他'
                issue_label = self._text(
                    item, f'{category} · {self._issue_category(issue.path)}',
                    muted=True, anchor='w')
                issue_label.pack(side='left', fill='x', expand=True)
                link = self._link(
                    item, lambda target=name, current=issue:
                    self.app.focus_validation_issue(target, current))
                link.pack(side='right')
                entries.append({'button': link, 'label': issue_label})
            self.validation_rows[name] = {'label': label, 'button': button,
                                          'issues': entries}

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
                'external': '外部程序配置', 'log': '日志配置'}.get(path[0],
                                                                  '配置结构与兼容性')

    def show_issue(self, code):
        """结构问题只显示类别，不显示未知诊断原文。"""
        self.issue_hint.configure(text='配置结构与兼容性问题，请检查 YAML 文件')

    def focus_issue(self, issue):
        """主页是无法定位到可编辑字段的诊断落点。"""
        self.show_issue('structure')
