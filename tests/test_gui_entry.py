#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""独立 GUI 入口参数与无参副作用测试。"""

import importlib.util
import tkinter as tk
from pathlib import Path
from unittest.mock import Mock

import pytest


def test_gui_no_args_does_not_create_default(tmp_path, monkeypatch):
    """不带参数构建内存新文档，不创建默认 YAML。"""
    entry = Path(__file__).resolve().parents[1] / '2RPM_gui.py'
    spec = importlib.util.spec_from_file_location('gui_entry', entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = Mock()
    root.mainloop = Mock()
    monkeypatch.setattr('ctk_kit.App', lambda **kwargs: root)
    monkeypatch.setattr('tkinter.Tk', Mock(side_effect=AssertionError('应使用 ctk-kit 窗口')))
    monkeypatch.setattr('modules.gui.app.App', Mock())
    monkeypatch.chdir(tmp_path)
    assert module.main([]) == 0
    assert not list(tmp_path.glob('*.yaml'))


def test_gui_entry_uses_ctk_window_without_geometry_persistence(monkeypatch):
    """入口创建 ctk-kit 窗口，几何与主题交给独立偏好处理。"""
    entry = Path(__file__).resolve().parents[1] / '2RPM_gui.py'
    spec = importlib.util.spec_from_file_location('gui_entry_ctk', entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = Mock()
    created = Mock(return_value=root)
    monkeypatch.setattr('ctk_kit.App', created)
    monkeypatch.setattr('tkinter.Tk', Mock(side_effect=AssertionError('应使用 ctk-kit 窗口')))
    monkeypatch.setattr('modules.gui.app.App', Mock())
    assert module.main([]) == 0
    assert created.call_args.kwargs == {'title': '2RPM 配置',
                                       'remember_geometry': False,
                                       'remember_theme': False,
                                       'notifier': False,
                                       'backdrop': 'none'}


@pytest.mark.parametrize(('kind', 'detail'), [
    ('relocate', {'from': '旧目录', 'to': '新目录'}),
    ('overwrite', '配置.yaml'),
    ('repeat_launch', '配置.yaml'),
])
def test_non_normalize_confirmation_uses_real_tk(tmp_path, monkeypatch, kind, detail):
    """非规范化确认在真实 Tk 根窗口中不遍历 detail。"""
    entry = Path(__file__).resolve().parents[1] / '2RPM_gui.py'
    spec = importlib.util.spec_from_file_location('gui_entry_confirm', entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tk.Tk()
    seen = []
    try:
        monkeypatch.setattr('ctk_kit.App', lambda **kwargs: root)
        monkeypatch.setattr('tkinter.Tk', Mock(side_effect=AssertionError('应使用 ctk-kit 窗口')))
        monkeypatch.setattr('modules.gui.app.App', Mock())
        monkeypatch.setattr('tkinter.messagebox.askyesno',
                            lambda title, text, **kwargs: seen.append((text, kwargs['parent'])) or True)

        def exercise_confirmation():
            """在入口建好控制器后走一次原生确认回调。"""
            assert controller_holder[0].confirm(kind, detail) == 'confirm'

        from modules.gui.controller import Controller
        controller_holder = []

        def capture_controller(*args, **kwargs):
            """保留真实控制器并暴露给主循环验证。"""
            controller = Controller(*args, **kwargs)
            controller_holder.append(controller)
            return controller

        monkeypatch.setattr('modules.gui.controller.Controller', capture_controller)
        monkeypatch.setattr(root, 'mainloop', exercise_confirmation)
        assert module.main([]) == 0
        assert seen and seen[0][1] is root
    finally:
        root.destroy()
