#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""独立配置主页：安全摘要、诊断及独立 CLI 启动。"""

from pathlib import Path

import ctk_kit

from modules.gui.appearance import FONT_BODY, FONT_CARD_TITLE, FONT_TITLE
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
        card = self._card('配置摘要')
        for label, action in (('另存为', app.save_as), ('保存', app.save)):
            ctk_kit.CTkButton(card, text=label, command=action, font=FONT_BODY,
                              width=90).pack(anchor='e', pady=2)
        self.path_label = ctk_kit.CTkLabel(card, text='', font=FONT_BODY, anchor='w')
        self.path_label.pack(fill='x')
        self.summary = ctk_kit.CTkLabel(card, text='', font=FONT_BODY, anchor='w', justify='left')
        self.summary.pack(fill='x')
        self.conditions = ctk_kit.CTkLabel(card, text='', font=FONT_BODY, anchor='w', justify='left')
        self.conditions.pack(fill='x')
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
        self.filename = ctk_kit.CTkLabel(card, text='', font=FONT_BODY, anchor='w')
        self.filename.pack(fill='x')
        ctk_kit.CTkButton(card, text='启动 CLI', command=app.start_cli, font=FONT_BODY).pack(anchor='e')
        self.refresh()

    def _card(self, title):
        """创建统一内圆角内容卡片。"""
        card = ctk_kit.CTkFrame(self, corner_radius=12)
        card.pack(fill='x', pady=9, padx=4)
        ctk_kit.CTkLabel(card, text=title, font=FONT_CARD_TITLE, anchor='w').pack(
            fill='x', padx=14, pady=(12, 4))
        return card

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
        self.path_label.configure(text='配置路径：' + display)
        self.filename.configure(text='当前配置：' + (Path(path).name if path else '未保存的新配置'))
        text, conditions = summary(self.app.model.snapshot())
        self.summary.configure(text=text)
        self.conditions.configure(text='事件截图：\n' + '\n'.join(
            f'{name}：{state}' + (f'（{"；".join(reasons)}）' if reasons else '')
            for name, state, reasons in conditions))

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
        errors = sum(error for _name, error, _other in groups)
        others = total - errors
        self.check.configure(
            text=(f'存在 {total} 个问题（错误 {errors}，其他 {others}），请检查对应配置'
                  if total else '配置检查：无问题'))
        for name, error_count, other_count in groups:
            row = ctk_kit.CTkFrame(self.check_body, fg_color='transparent')
            row.pack(fill='x', pady=2)
            label = ctk_kit.CTkLabel(
                row, text=f'{name}：{error_count + other_count} 个问题'
                          f'（错误 {error_count}，其他 {other_count}）',
                font=FONT_BODY, anchor='w')
            label.pack(side='left', fill='x', expand=True)
            button = ctk_kit.CTkButton(
                row, text='前往设置', width=80, font=FONT_BODY,
                command=lambda target=name: self.app.show_page(target))
            button.pack(side='right')
            self.validation_rows[name] = {'label': label, 'button': button}

    def show_issue(self, code):
        """结构问题只显示类别，不显示未知诊断原文。"""
        self.issue_hint.configure(text='配置结构与兼容性问题，请检查 YAML 文件')

    def focus_issue(self, issue):
        """主页是无法定位到可编辑字段的诊断落点。"""
        self.show_issue('structure')
