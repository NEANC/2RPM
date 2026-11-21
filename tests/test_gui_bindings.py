#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""可回调控件的程序刷新不得编辑配置模型。"""

from unittest.mock import Mock

import pytest
import tkinter as tk

from modules.config_document import new_blank_document, new_document
from modules.gui.model import EditorModel
from modules.gui.pages.base import Page
from modules.gui.widgets import ValueBinding, set_optional_bool


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


@pytest.mark.parametrize(('selection', 'expected'), [('开启', True), ('关闭', False)])
def test_optional_bool_roundtrip(selection, expected):
    """三态选择只写布尔值，未设置删除键并保留父映射。"""
    path = ('push', 'templates', 'on_end', 'enable')
    model = EditorModel(new_blank_document())

    set_optional_bool(model, path, '未设置')
    assert model.snapshot() == {}
    assert model.revision == 0

    set_optional_bool(model, path, selection)
    assert model.get(path) is expected

    set_optional_bool(model, path, '未设置')
    assert model.snapshot() == {'push': {'templates': {'on_end': {}}}}

    with pytest.raises(ValueError):
        set_optional_bool(model, path, '未选中')


def _blank_page(name, root, model):
    """按页面名装配空白草稿页面。"""
    if name == 'monitor':
        from modules.gui.pages.monitor import Page as MonitorPage
        return MonitorPage(root, model)
    if name == 'external':
        from modules.gui.pages.external import Page as ExternalPage
        return ExternalPage(root, Mock(model=model))
    from modules.gui.pages.logs import Page as LogsPage
    return LogsPage(root, Mock(model=model, open_logs=Mock()))


@pytest.mark.parametrize('name', ['monitor', 'external', 'logs'])
def test_missing_field_render_does_not_edit(name):
    """空白草稿的缺失字段显示为空，刷新不建立配置键也不递增修订。"""
    root = tk.Tk()
    root.withdraw()
    try:
        model = EditorModel(new_blank_document())
        page = _blank_page(name, root, model)
        page.pack(fill='both', expand=True)
        root.update()
        if name == 'monitor':
            assert page.mode.get() == '未选择'
            shown = [page.file_entry.get(), page.task_entry.get(),
                     page.lookback.entry.get()]
        elif name == 'external':
            shown = [page.path_fields[('external', 'on_end')].entry.get(),
                     page.spinners[('monitor', 'common', 'timeout_threshold')].entry.get()]
        else:
            shown = [page.path_field.entry.get(),
                     page.spinners[('log', 'max_log_files')].entry.get(),
                     page.spinners[('log', 'retention_days')].entry.get()]
        assert shown == [''] * len(shown)
        assert model.snapshot() == {}
        before = model.revision
        page.refresh()
        assert model.snapshot() == {}
        assert model.revision == before
    finally:
        root.destroy()
