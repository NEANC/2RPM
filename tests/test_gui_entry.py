#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""独立 GUI 入口参数与无参副作用测试。"""

import importlib.util
from pathlib import Path
from unittest.mock import Mock


def test_gui_no_args_does_not_create_default(tmp_path, monkeypatch):
    """不带参数构建内存新文档，不创建默认 YAML。"""
    entry = Path(__file__).resolve().parents[1] / '2RPM_gui.py'
    spec = importlib.util.spec_from_file_location('gui_entry', entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = Mock()
    root.mainloop = Mock()
    monkeypatch.setattr('tkinter.Tk', lambda: root)
    monkeypatch.setattr('modules.gui.app.App', Mock())
    monkeypatch.chdir(tmp_path)
    assert module.main([]) == 0
    assert not list(tmp_path.glob('*.yaml'))
