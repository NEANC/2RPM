#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 外观偏好与材质降级测试。"""

from unittest.mock import Mock, patch

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
    root._glass_tint.return_value = 0xE61A1A1A
    root.glass_options.return_value = {'dark': False, 'frame': False,
                                       'state': 4, 'wca': 30, 'extend': True}
    with patch('modules.gui.appearance.enable_acrylic', return_value=False):
        apply_appearance(root, preferences)
    root.set_appearance_mode.assert_called_once_with('dark' if darkdetect.isDark() else 'light')


def test_manual_theme_persists(tmp_path):
    """手动选择独立保存并在下次启动沿用。"""
    path = tmp_path / 'gui.ini'
    save_preferences(path, AppearancePreferences(dark=True, material='Acrylic'))
    assert load_preferences(path) == AppearancePreferences(dark=True, material='Acrylic')
    root = Mock()
    root._glass_tint.return_value = 0xE61A1A1A
    root.glass_options.return_value = {'dark': True, 'frame': False,
                                       'state': 4, 'wca': 30, 'extend': True}
    with patch('modules.gui.appearance.enable_acrylic', return_value=False):
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


def test_win10_does_not_report_unsupported_material():
    """Windows 10 的 Acrylic 原生调用失败时回退纯色。"""
    root = Mock()
    root._glass_tint.return_value = 0xE61A1A1A
    root.glass_options.return_value = {'kind': 'acrylic', 'backdrop': 0, 'dark': False,
                                       'frame': False, 'state': 4, 'wca': 30,
                                       'extend': True}
    with patch('modules.gui.appearance.enable_acrylic', return_value=False) as native:
        assert apply_appearance(root, AppearancePreferences()) == 'none'
        assert apply_appearance(root, AppearancePreferences(material='Acrylic')) == 'none'
    assert [call.args for call in root.set_theme.call_args_list] == [
        ('Acrylic',), ('Acrylic',)]
    assert native.call_count == 2
    assert all(call.kwargs['attribute'] == 19 for call in native.call_args_list)
    assert root.set_backdrop.call_count == 2


def test_win10_mica_falls_back_to_working_acrylic(tmp_path):
    """Win10 真正启用 Acrylic 后返回实际材质，保留磁盘上的 Mica 偏好。"""
    path = tmp_path / 'gui.ini'
    preferences = AppearancePreferences(dark=False, material='Mica')
    save_preferences(path, preferences)
    root = Mock()
    root._glass_tint.return_value = 0xE61A1A1A
    root.glass_options.return_value = {'kind': 'acrylic', 'backdrop': 0, 'dark': False,
                                       'frame': False, 'state': 4, 'wca': 30,
                                       'extend': True}
    with patch('modules.gui.appearance.enable_acrylic', return_value=True) as native:
        assert apply_appearance(root, preferences) == 'Acrylic'
    root.set_theme.assert_called_once_with('Acrylic')
    native.assert_called_once()
    assert native.call_args.kwargs['attribute'] == 19
    assert load_preferences(path) == preferences


def test_win11_mica_theme_without_native_effect_falls_back_to_acrylic(monkeypatch):
    """主题设置没有抛错但 Mica 未生效时，仍检查 Acrylic 的原生结果。"""
    monkeypatch.setattr('modules.gui.appearance.sys.getwindowsversion',
                        lambda: Mock(build=22621))
    root = Mock()
    root._glass_tint.return_value = 0xE61A1A1A
    root.glass_options.return_value = {'kind': 'acrylic', 'backdrop': 0, 'dark': True,
                                       'frame': False, 'state': 4, 'wca': 30,
                                       'extend': True}
    with patch('modules.gui.appearance.enable_acrylic', side_effect=[False, True]) as native:
        assert apply_appearance(root, AppearancePreferences(material='Mica')) == 'Acrylic'
    assert [call.args for call in root.set_theme.call_args_list] == [
        ('Mica',), ('Acrylic',)]
    assert native.call_count == 2


def test_failed_preference_write_keeps_session_choice(monkeypatch, tmp_path):
    """写盘失败只提示，不撤销本次会话的主题及材质选择。"""
    from modules.gui.app import App

    app = object.__new__(App)
    app.root = Mock()
    app.appearance_path = tmp_path / 'gui.ini'
    app.appearance = AppearancePreferences(dark=None, material='Mica')
    app.appearance_theme = Mock()
    app.appearance_material = Mock()
    applied = Mock()
    alerted = Mock()
    monkeypatch.setattr('modules.gui.app.save_preferences',
                        Mock(side_effect=OSError('write failed')))
    monkeypatch.setattr('modules.gui.app.apply_appearance', applied)
    monkeypatch.setattr('modules.gui.app.messagebox.showerror', alerted)

    app.choose_theme('深色')
    app.choose_material('Acrylic')

    assert app.appearance == AppearancePreferences(dark=True, material='Acrylic')
    applied.assert_called_with(app.root, app.appearance)
    app.appearance_theme.set.assert_not_called()
    app.appearance_material.set.assert_not_called()
    assert alerted.call_count == 2


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
    """发布版真实窗口在系统浅色下降级为纯色仍保持浅色。"""
    import ctk_kit

    root = ctk_kit.App(title='外观测试', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    root.withdraw()
    try:
        with patch('modules.gui.appearance.enable_acrylic', return_value=False):
            assert apply_appearance(root, AppearancePreferences(dark=False,
                                                                 material='Acrylic')) == 'none'
        assert ctk_kit.get_appearance_mode() == 'Light'
    finally:
        root.destroy()
