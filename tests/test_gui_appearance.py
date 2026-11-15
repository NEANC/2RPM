#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 外观偏好与材质降级测试。"""

from unittest.mock import Mock

import darkdetect

from modules.gui.appearance import (AppearancePreferences, FONT_BODY, FONT_TITLE,
                                    apply_appearance, load_preferences,
                                    preferences_path, save_preferences)


def test_first_run_uses_system_theme(tmp_path):
    """首次启动不将系统主题写成手动覆盖。"""
    preferences = load_preferences(tmp_path / 'gui.ini')
    assert preferences.dark is None
    assert preferences.material == 'Mica'
    root = Mock()
    apply_appearance(root, preferences)
    root.set_appearance_mode.assert_called_once_with('dark' if darkdetect.isDark() else 'light')


def test_manual_theme_persists(tmp_path):
    """手动选择独立保存并在下次启动沿用。"""
    path = tmp_path / 'gui.ini'
    save_preferences(path, AppearancePreferences(dark=True, material='Acrylic'))
    assert load_preferences(path) == AppearancePreferences(dark=True, material='Acrylic')
    root = Mock()
    apply_appearance(root, load_preferences(path))
    root.set_appearance_mode.assert_called_once_with('dark')


def test_mica_falls_back_without_changing_preference(tmp_path, monkeypatch):
    """Mica 失败依次尝试 Acrylic 和纯色，不覆盖已保存选择。"""
    path = tmp_path / 'gui.ini'
    preferences = AppearancePreferences(dark=False, material='Mica')
    save_preferences(path, preferences)
    monkeypatch.setattr('modules.gui.appearance.sys.getwindowsversion',
                        lambda: Mock(build=22621))
    root = Mock()
    root.set_theme.side_effect = [RuntimeError('Mica unavailable'),
                                  RuntimeError('Acrylic unavailable')]
    assert apply_appearance(root, preferences) == 'none'
    assert [call.args for call in root.set_theme.call_args_list] == [
        ('Mica',), ('Acrylic',)]
    root.set_backdrop.assert_called_with('none')
    assert load_preferences(path) == preferences


def test_win10_skips_unsupported_mica():
    """Windows 10 不把设置主题返回成功误认为 Mica 真正可用。"""
    root = Mock()
    assert apply_appearance(root, AppearancePreferences()) == 'Acrylic'
    root.set_theme.assert_called_once_with('Acrylic')


def test_palette_composites_alpha_and_uses_yahei():
    """半透明设计色在不支持 alpha 的控件上与底色合成。"""
    root = Mock()
    apply_appearance(root, AppearancePreferences(dark=False, material='none'))
    palette = root.apply_palette.call_args.args[0]
    assert palette['COLOR_CARD'] == '#FCFCFC'
    assert palette['COLOR_ACCENT'] == '#0067C0'
    assert palette['COLOR_TEXT'] == '#1A1A1A'
    assert root.apply_palette.call_args.kwargs == {'save': False}
    assert FONT_BODY[0] == FONT_TITLE[0] == 'Microsoft YaHei UI'
    assert FONT_TITLE[2] == 'bold'


def test_preferences_path_uses_local_app_data(monkeypatch, tmp_path):
    """偏好文件与业务 YAML 分离，落在当前用户的本地目录。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert preferences_path() == tmp_path / '2RPM' / 'gui.ini'


def test_ctk_appearance_does_not_override_light_mode():
    """发布版真实窗口在系统浅色下套用材质仍保持浅色。"""
    import ctk_kit

    root = ctk_kit.App(title='外观测试', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    root.withdraw()
    try:
        assert apply_appearance(root, AppearancePreferences(dark=False,
                                                             material='Acrylic')) == 'Acrylic'
        assert ctk_kit.get_appearance_mode() == 'Light'
    finally:
        root.destroy()
