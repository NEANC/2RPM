#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""与业务 YAML 分离的外观设置页。"""

import ctk_kit

from modules.gui.appearance import (COLORS, FONT_BODY, FONT_CARD_TITLE,
                                    FONT_TITLE)

# 与其它重绘页保持同一套原型比例：圆角 20、2px 边框、28px 页边距与内边距。
PAGE_PAD = 28
CARD_PAD = 28
CARD_RADIUS = 20
CARD_BORDER = 2
CARD_SURFACE = ('#FAFAFA', '#232323')
CARD_BORDER_COLOR = ('#E1E1E1', '#3A3A3A')
SWITCH_STYLE = {
    'fg_color': ('#A0A0A0', '#5F5F5F'),
    'progress_color': COLORS['accent'],
    'button_color': ('#FFFFFF', '#000000'),
    'button_hover_color': ('#FFFFFF', '#333333'),
}


class Page(ctk_kit.CTkFrame):
    """深色模式与背景材质选择。"""

    def __init__(self, parent, app):
        """显示外观卡片，首次选择以系统主题的当前状态呈现。"""
        super().__init__(parent, corner_radius=0)
        self.app = app
        ctk_kit.CTkLabel(self, text='设置', font=FONT_TITLE,
                         text_color=COLORS['text_primary']).pack(
            anchor='w', padx=PAGE_PAD, pady=(PAGE_PAD, 12))
        card = ctk_kit.Card(self, corner_radius=CARD_RADIUS,
                            border_width=CARD_BORDER,
                            border_color=CARD_BORDER_COLOR,
                            fg_color=CARD_SURFACE)
        card.pack(fill='x', padx=PAGE_PAD, pady=(0, PAGE_PAD))
        ctk_kit.CTkLabel(card, text='外观', font=FONT_CARD_TITLE,
                         text_color=COLORS['text_primary']).pack(
            anchor='w', padx=CARD_PAD, pady=(CARD_PAD, 14))
        self._row(card, '深色模式', self._theme_control)
        self._row(card, '背景材质', self._material_control)
        # 行间距已含 14px，再补 14px 让卡片底部保持原型 28px 内边距。
        ctk_kit.CTkFrame(card, fg_color='transparent',
                         height=CARD_PAD - 14).pack(fill='x')
        self.refresh()

    def _row(self, card, label, factory):
        """左标签右控件的一行，行间不画分隔线。"""
        row = ctk_kit.CTkFrame(card, fg_color='transparent')
        row.pack(fill='x', padx=CARD_PAD, pady=(0, 14))
        ctk_kit.CTkLabel(row, text=label, font=FONT_BODY,
                         text_color=COLORS['text_primary']).pack(
            side='left')
        factory(row).pack(side='right')
        return row

    def _theme_control(self, row):
        """深色模式开关；正文由行标签承载，开关只保留指示器。"""
        self.theme = ctk_kit.CTkSwitch(row, text='', font=FONT_BODY,
                                       command=self._change_theme,
                                       **SWITCH_STYLE)
        return self.theme

    def _material_control(self, row):
        """背景材质下拉，显示文案与持久化值分开映射。"""
        self.material = ctk_kit.CTkOptionMenu(
            row, values=['Mica', 'Acrylic', '无材质'],
            font=FONT_BODY, dropdown_font=FONT_BODY, width=140,
            fg_color=COLORS['input_bg'],
            button_color=COLORS['input_bg'],
            button_hover_color=COLORS['soft_hover'],
            text_color=COLORS['text_primary'],
            dropdown_fg_color=COLORS['input_bg'],
            dropdown_hover_color=COLORS['soft'],
            dropdown_text_color=COLORS['text_primary'],
            command=self._change_material)
        return self.material

    def _change_theme(self):
        """仅用户主动切换时覆盖系统主题偏好。"""
        self.app.choose_theme('深色' if self.theme.get() else '浅色')

    def _change_material(self, value):
        """将显示文案映射到偏好存储值。"""
        self.app.choose_material('none' if value == '无材质' else value)

    def refresh(self):
        """刷新显示不得写回偏好或业务模型。"""
        dark = self.app.appearance.dark
        if dark is None:
            dark = ctk_kit.get_appearance_mode() == 'Dark'
        if dark:
            self.theme.select()
        else:
            self.theme.deselect()
        self.material.set('无材质' if self.app.appearance.material == 'none'
                          else self.app.appearance.material)

    def focus_issue(self, issue):
        """外观页不包含业务诊断字段。"""
