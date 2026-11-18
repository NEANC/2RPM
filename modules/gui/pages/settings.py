#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""与业务 YAML 分离的外观设置页。"""

import ctk_kit

from modules.gui.appearance import CARD_STYLE, FONT_BODY, FONT_CARD_TITLE, FONT_TITLE


class Page(ctk_kit.CTkFrame):
    """深色模式与背景材质选择。"""

    def __init__(self, parent, app):
        """显示外观卡片，首次选择以系统主题的当前状态呈现。"""
        super().__init__(parent, corner_radius=0)
        self.app = app
        ctk_kit.CTkLabel(self, text='设置', font=FONT_TITLE).pack(anchor='w', pady=(0, 12))
        card = ctk_kit.CTkFrame(self, **CARD_STYLE)
        card.pack(fill='x', pady=8)
        ctk_kit.CTkLabel(card, text='外观', font=FONT_CARD_TITLE).pack(anchor='w', padx=16, pady=12)
        self.theme = ctk_kit.CTkSwitch(card, text='深色模式', font=FONT_BODY,
                                       command=self._change_theme)
        self.theme.pack(anchor='w', padx=16, pady=10)
        ctk_kit.CTkLabel(card, text='背景材质', font=FONT_BODY).pack(anchor='w', padx=16)
        self.material = ctk_kit.CTkOptionMenu(card, values=['Mica', 'Acrylic', '无材质'],
                                              font=FONT_BODY, dropdown_font=FONT_BODY,
                                              command=self._change_material)
        self.material.pack(anchor='w', padx=16, pady=(6, 16))
        self.refresh()

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
