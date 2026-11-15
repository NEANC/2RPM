#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""可回调控件的程序刷新不得编辑配置模型。"""

from unittest.mock import Mock

import tkinter as tk

from modules.config_document import new_document
from modules.gui.model import EditorModel
from modules.gui.pages.base import Page
from modules.gui.widgets import ValueBinding


def test_refresh_does_not_edit_model():
    """对程序设置时触发回调的控件抑制刷新期间的编辑。"""
    model = Mock()
    widget = Mock()
    binding = ValueBinding(widget, model.set, ('monitor', 'target'))
    widget.set_value.side_effect = lambda value: binding.changed(value)
    binding.refresh('new.exe')
    model.set.assert_not_called()
    binding.changed('user.exe')
    model.set.assert_called_once_with(('monitor', 'target'), 'user.exe')


def test_page_refresh_keeps_revision():
    """共享表单刷新不把程序设置误认为用户输入。"""
    root = tk.Tk()
    try:
        model = EditorModel(new_document())
        page = Page(root, model)
        path = ('monitor', 'target')
        page.field(page.body, '目标', path)
        model.set(path, 'updated.exe')
        before = model.revision
        page.refresh()
        assert model.revision == before
        assert page.fields[path][0].get() == 'updated.exe'
    finally:
        root.destroy()
