#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""独立的 GUI 外观偏好及 ctk-kit 材质适配。"""

import configparser
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ctk_kit.core.win32 import enable_acrylic
import darkdetect


FONT_FAMILY = 'Microsoft YaHei UI'
FONT_TITLE = (FONT_FAMILY, 30, 'bold')
FONT_CARD_TITLE = (FONT_FAMILY, 18, 'bold')
FONT_BODY = (FONT_FAMILY, 15)
FONT_SMALL = (FONT_FAMILY, 10)

COLORS = {
    'panel_bg': ('#F3F3F3B3', '#1C1C1C99'),
    'card_bg': ('#FFFFFF99', '#2D2D2D66'),
    'card_hover': ('#0000000D', '#FFFFFF0D'),
    'border': ('#0000001A', '#FFFFFF1A'),
    'text_primary': ('#1A1A1A', '#F3F3F3'),
    'text_secondary': ('#5F5F5F', '#A0A0A0'),
    'accent': ('#0067C0', '#60CDFF'),
    'accent_hover': ('#005A9E', '#87D9FF'),
    'accent_text': ('#FFFFFF', '#000000'),
    'side': ('#F3F3F3', '#1C1C1C'),
    'soft': ('#EBEBEB', '#2A2A2A'),
    'soft_hover': ('#E0E0E0', '#363636'),
    'input_bg': ('#FFFFFF', '#242424'),
    'titlebar': ('#F3F3F3', '#1C1C1C'),
    'border_strong': ('#C9C9C9', '#4A4A4A'),
}
CARD_STYLE = {
    'corner_radius': 10,
    'border_width': 1,
    'border_color': ('#DADADA', '#333333'),
    'fg_color': ('#FAFAFA', '#232323'),
}
PALETTE_KEYS = {
    'panel_bg': 'COLOR_BG', 'card_bg': 'COLOR_CARD',
    'card_hover': 'COLOR_HOVER_FAINT', 'border': 'COLOR_BORDER',
    'text_primary': 'COLOR_TEXT', 'text_secondary': 'COLOR_MUTED',
    'accent': 'COLOR_ACCENT', 'accent_hover': 'COLOR_ACCENT_HOVER',
    'accent_text': 'COLOR_ACCENT_TEXT', 'side': 'COLOR_SIDE',
    'soft': 'COLOR_SOFT', 'soft_hover': 'COLOR_SOFT_HOVER',
    'input_bg': 'COLOR_INPUT', 'titlebar': 'COLOR_TITLEBAR',
    'border_strong': 'COLOR_BORDER_STRONG',
}


@dataclass(frozen=True)
class AppearancePreferences:
    """手动深浅色覆盖与期望材质。"""

    dark: bool | None = None
    material: str = 'Mica'


def preferences_path() -> Path:
    """获取与业务配置分离的 Windows 用户偏好路径。"""
    base = (Path(os.environ['LOCALAPPDATA']) if os.environ.get('LOCALAPPDATA')
            else Path.home() / 'AppData' / 'Local')
    return base / '2RPM' / 'gui.ini'


def load_preferences(path: Path) -> AppearancePreferences:
    """读取偏好；不存在时保持系统深浅主题。"""
    parser = configparser.ConfigParser()
    parser.read(path, encoding='utf-8')
    dark = parser.get('appearance', 'dark', fallback='system')
    material = parser.get('appearance', 'material', fallback='Mica')
    if material not in ('Mica', 'Acrylic', 'none'):
        material = 'Mica'
    return AppearancePreferences(None if dark == 'system' else dark == 'true', material)


def save_preferences(path: Path, preferences: AppearancePreferences) -> None:
    """保存手动选择；失败时由调用界面提示并保留会话偏好。"""
    parser = configparser.ConfigParser()
    parser['appearance'] = {
        'dark': 'system' if preferences.dark is None else str(preferences.dark).lower(),
        'material': preferences.material,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as stream:
        parser.write(stream)


def _composite(color: str, background: str) -> str:
    """将规格中的透明色与当前底色合成标准 RGB 值。"""
    if len(color) == 7:
        return color
    alpha = int(color[7:9], 16)
    channels = [round((int(color[index:index + 2], 16) * alpha
                       + int(background[index:index + 2], 16) * (255 - alpha)) / 255)
                for index in (1, 3, 5)]
    return '#' + ''.join(f'{channel:02X}' for channel in channels)


def apply_appearance(root, preferences: AppearancePreferences) -> str:
    """先尝试期望材质，失败时降级，不修改持久化的偏好。"""
    materials = {'Mica': ('Mica', 'Acrylic', 'none'),
                 'Acrylic': ('Acrylic', 'none'), 'none': ('none',)}
    win10 = sys.platform == 'win32' and sys.getwindowsversion().build < 22000
    for material in materials[preferences.material]:
        if material == 'none':
            root.set_backdrop('none')
            if win10 and root.glass_options()['wca'] == 19:
                root.set_glass_options(wca=30)
        else:
            if material == 'Mica' and win10:
                continue
            try:
                root.set_theme(material)
                options = root.glass_options()
                if not enable_acrylic(
                        root,
                        tint=None if material == 'Mica' else root._glass_tint(),
                        dark=options['dark'],
                        system_backdrop=2 if material == 'Mica' else 0,
                        frame=options['frame'],
                        accent=options['state'],
                        attribute=19 if win10 else options['wca'],
                        extend=options['extend']):
                    continue
                if win10:
                    root.set_glass_options(wca=19)
            except (OSError, RuntimeError, ValueError):
                continue
        dark = preferences.dark if preferences.dark is not None else darkdetect.isDark()
        root.set_appearance_mode('dark' if dark else 'light')
        index = int(dark)
        background = '#000000' if dark else '#FFFFFF'
        palette = {}
        for name, key in PALETTE_KEYS.items():
            color = _composite(COLORS[name][index], background)
            palette[key] = color
            if name == 'panel_bg':
                background = color
        root.apply_palette(palette, save=False)
        return material
    raise RuntimeError('无法设置窗口背景材质')
