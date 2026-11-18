#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""主页按已批准 HTML 原型对齐：页头、键值行、原生卡片与折叠计数。"""

import re
import tkinter as tk

import ctk_kit

from ctk_kit import tokens

from modules.gui.appearance import COLORS, FONT_BODY, FONT_TITLE
from modules.gui.home_summary import summary as build_summary
from tests.gui_support import make_app


def _make(tmp_path, monkeypatch):
    """建真实 ctk-kit 窗口与装配后的主页。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    root = ctk_kit.App(title='测试', remember_geometry=False, remember_theme=False,
                       notifier=False, backdrop='none')
    root.withdraw()
    app = make_app(root, tmp_path)
    return root, app


def test_home_heading_and_actions_follow_prototype(tmp_path, monkeypatch):
    """页头标题与两个强调色动作按钮使用原型尺寸和加粗正文。"""
    root, app = _make(tmp_path, monkeypatch)
    try:
        home = app.visible_pages['主页']
        header = home.winfo_children()[0]
        label = header.winfo_children()[0]
        assert label.cget('text') == '主页'
        assert label.cget('font') == FONT_TITLE
        buttons = {child.cget('text'): child for child in header.winfo_children()
                   if isinstance(child, ctk_kit.CTkButton)}
        assert set(buttons) == {'新建配置', '打开配置'}
        for button in buttons.values():
            assert button.cget('fg_color') == tokens.COLOR_ACCENT
            assert button.cget('height') == 52
            assert button.cget('width') == 120
            assert button.cget('font')[1:] == (15, 'bold')
        scale = header._get_widget_scaling()
        assert tuple(round(value / scale)
                     for value in header.pack_info()['pady']) == (28, 0)
        assert round(header.pack_info()['padx'] / scale) == 28
    finally:
        root.destroy()


def test_home_summary_rows_are_left_label_right_value(tmp_path, monkeypatch):
    """摘要首行配置路径，随后为三组键值行，值右对齐。"""
    root, app = _make(tmp_path, monkeypatch)
    try:
        home = app.visible_pages['主页']
        rows = home.summary_rows
        assert list(rows) == ['配置路径', '运行方式', '目标 · 最长等待',
                              '通知通道 · 截图目标']
        for name, (label, value) in rows.items():
            assert label.cget('text') == name
            assert label.cget('text_color') == COLORS['text_secondary'][0]
            assert label.cget('font') == FONT_BODY
            assert value.pack_info()['side'] == 'right'
            assert value.cget('font') == FONT_BODY
            assert value.cget('text_color') == COLORS['text_primary'][0]
        assert rows['配置路径'][1].cget('text') == 'a.yaml'
        assert rows['运行方式'][1].cget('text') == '等待目标运行'
        assert '·' in rows['目标 · 最长等待'][1].cget('text')
        text, _ = build_summary(app.model.snapshot())
        values = dict(line.split('：', 1) for line in text.splitlines()[:5])
        assert rows['通知通道 · 截图目标'][1].cget('text') == (
            f'{values["通知通道"]} · {values["截图目标"]}')
        assert home.path_tip.text == str(app.model.document.path)
    finally:
        root.destroy()


def test_home_cards_use_native_card_metrics(tmp_path, monkeypatch):
    """三张内容卡使用原生 Card 与原型圆角、边框和间距。"""
    root, app = _make(tmp_path, monkeypatch)
    try:
        home = app.visible_pages['主页']
        cards = [child for child in home.winfo_children()
                 if isinstance(child, ctk_kit.Card)]
        assert len(cards) == 3
        for card in cards:
            assert card.cget('corner_radius') == 20
            assert card.cget('border_width') == 2
            scale = card._get_widget_scaling()
            assert round(card.pack_info()['padx'] / scale) == 28
            assert tuple(round(value / scale)
                         for value in card.pack_info()['pady']) == (32, 0)
    finally:
        root.destroy()


def test_home_group_counts_use_prototype_format(tmp_path, monkeypatch):
    """三级折叠摘要使用「N 个状态 / 总数」文案并默认收起。"""
    root, app = _make(tmp_path, monkeypatch)
    try:
        home = app.visible_pages['主页']
        assert set(home.groups) == {'通知事件', '事件截图', '外部程序'}
        for group in home.groups.values():
            assert not group.expanded
            assert group.button.cget('font') == FONT_BODY
            assert group.button.cget('text_color') == COLORS['text_secondary'][0]
            assert group.button.cget('anchor') == 'w'
            assert group.button.cget('text').startswith('▶')
            assert group.detail.endswith(' 个')
        assert re.fullmatch(r'\d+ 个启用 / 4 个',
                            home.groups['通知事件'].detail)
        assert re.fullmatch(r'\d+ 个条件满足 / 4 个',
                            home.groups['事件截图'].detail)
        assert re.fullmatch(r'\d+ 个已配置 / 3 个',
                            home.groups['外部程序'].detail)
    finally:
        root.destroy()


def test_home_actions_are_primary_and_validation_button_exposed(tmp_path, monkeypatch):
    """保存、另存为、校验与启动 CLI 均为强调色主按钮，校验按钮可直接定位。"""
    root, app = _make(tmp_path, monkeypatch)
    try:
        home = app.visible_pages['主页']
        assert home.save_button.master is home.summary_header
        assert home.save_button.pack_info()['side'] == 'right'
        assert home.save_as_button.pack_info()['side'] == 'right'
        for button in (home.save_button, home.save_as_button,
                       home.validate_button, home.start_button):
            assert isinstance(button, ctk_kit.CTkButton)
            assert button.cget('fg_color') == tokens.COLOR_ACCENT
        assert home.validate_button.cget('text') == '校验配置'
        assert home.start_button.cget('text') == '启动 CLI'
    finally:
        root.destroy()
