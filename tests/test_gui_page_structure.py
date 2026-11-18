#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 页面行为（GUI 测试分片）。"""

import queue
import tkinter as tk
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import ctk_kit

from modules.config_document import new_document, read_document, save_document
from modules.gui.model import EditorModel
from modules.gui.forms import screenshot_conditions
from modules.gui.protocol import TestRequest as GuiTestRequest
from modules.gui.protocol import TestResult as GuiTestResult


from tests.gui_support import make_app


def test_page_model_identity_and_refresh_after_open(tmp_path):
    """四页持有同一模型，替换文档后控件指向新内容。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        model = app.controller.model
        document = new_document()
        document.tree['monitor']['target'] = '中文目标.exe'
        model.replace_document(document)
        app.refresh()
        assert all(app.visible_pages[name].model is model
                   for name in ('监视设置', '消息推送设置', '截图设置', '日志设置'))
        assert app.visible_pages['监视设置'].fields[('monitor', 'target')][0].get() == '中文目标.exe'
        app.visible_pages['监视设置'].set_field(('monitor', 'target'), '新目标.exe')
        assert model.get(('monitor', 'target')) == '新目标.exe'
    finally:
        root.destroy()


def test_missing_sections_clear_hint_and_keep_controls_enabled(tmp_path):
    """整节缺失与存在但为 null 的提示和控件状态必须区分。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        monitor = 'monitor:\n  mode: watch\n  target: demo.exe\n  common: {}\n'
        cases = [
            (monitor + 'push: {}\nexternal: {}\n', '日志设置', 'missing_log'),
            (monitor + 'log: {}\nexternal: {}\n', '消息推送设置', 'missing_push'),
            ('monitor:\n  mode: watch\n  target: demo.exe\n'
             'push: {}\nlog: {}\nexternal: {}\n', '监视设置', 'missing_common'),
            (monitor + 'push: {}\nlog: null\nexternal: {}\n', '日志设置', 'null_log'),
            (monitor + 'push: null\nlog: {}\nexternal: {}\n', '消息推送设置', 'null_push'),
        ]
        for index, (text, page_name, name) in enumerate(cases):
            source = tmp_path / f'{name}-{index}.yaml'
            source.write_text(text, encoding='utf-8')
            assert app.controller.open(source)
            assert app.show_page(page_name)
            page = app.visible_pages[page_name]
            if name in ('missing_log', 'missing_push', 'missing_common'):
                assert page.structure_hint.cget('text') == ''
            else:
                assert page.structure_hint.cget('text') == '配置结构异常，请在 YAML 中修复'
            if name == 'missing_log':
                assert page.fields[('log', 'max_log_files')][1].cget('state') == 'normal'
                assert page.browse_button.cget('state') == 'normal'
            elif name == 'missing_push':
                assert page.fields[('push', 'retry', 'interval')][1].cget('state') == 'normal'
                assert page.add_channel_button.cget('state') == 'normal'
            elif name == 'missing_common':
                assert page.fields[('monitor', 'common', 'loop_interval')][1].cget('state') == 'normal'
            elif name == 'null_log':
                assert page.fields[('log', 'max_log_files')][1].cget('state') == 'disabled'
                assert page.browse_button.cget('state') == 'disabled'
            else:
                assert page.fields[('push', 'retry', 'interval')][1].cget('state') == 'disabled'
                assert page.add_channel_button.cget('state') == 'disabled'
    finally:
        root.destroy()


def test_structure_invalid_auxiliary_buttons_disable_and_keep_display(tmp_path,
                                                                      monkeypatch):
    """结构异常时辅助按钮禁用，禁用字段刷新仍能正确显示或清空。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        logs_path = tmp_path / 'null-log.yaml'
        logs_path.write_text('monitor:\n  mode: watch\n  target: demo.exe\n  common: {}\n'
                             'push: {}\nlog: null\nexternal: {}\n', encoding='utf-8')
        assert app.controller.open(logs_path)
        assert app.show_page('日志设置')
        logs_page = app.visible_pages['日志设置']
        assert logs_page.browse_button.cget('state') == 'disabled'
        assert logs_page.clear_button.cget('state') == 'disabled'
        monkeypatch.setattr('modules.gui.pages.logs.filedialog.askdirectory',
                            lambda **_: str(tmp_path / 'chosen'))
        logs_page.choose_directory()
        assert logs_page.fields[('log', 'log_directory')][1].get() == ''
        assert app.model.snapshot()['log'] is None
        external_path = tmp_path / 'null-external.yaml'
        external_path.write_text('monitor:\n  mode: watch\n  target: demo.exe\n  common: {}\n'
                                 'push: {}\nlog: {}\nexternal: null\n', encoding='utf-8')
        assert app.controller.open(external_path)
        assert app.show_page('外部程序设置')
        external_page = app.visible_pages['外部程序设置']
        assert external_page.browse_buttons['on_end'].cget('state') == 'disabled'
        assert external_page.clear_buttons['on_end'].cget('state') == 'disabled'
        assert external_page.restore_buttons['on_end'].cget('state') == 'disabled'
        monkeypatch.setattr('modules.gui.pages.external.filedialog.askopenfilename',
                            lambda **_: r'C:\chosen.bat')
        external_page.choose_file('on_end')
        external_page.clear_file('on_end')
        assert external_page.fields[('external', 'on_end')][1].get() == ''
        assert app.model.snapshot()['external'] is None
        null_monitor = tmp_path / 'null-monitor.yaml'
        null_monitor.write_text('monitor: null\npush: {}\nlog: {}\nexternal: {}\n',
                                encoding='utf-8')
        assert app.controller.open(null_monitor)
        assert app.show_page('监视设置')
        monitor_page = app.visible_pages['监视设置']
        target = monitor_page.fields[('monitor', 'target')][1]
        assert target.cget('state') == 'disabled'
        valid_monitor = tmp_path / 'valid-monitor.yaml'
        valid_monitor.write_text('monitor:\n  mode: watch\n  target: demo.exe\n'
                                 '  common: {}\npush: {}\nlog: {}\nexternal: {}\n',
                                 encoding='utf-8')
        assert app.controller.open(valid_monitor)
        assert app.show_page('监视设置')
        target = monitor_page.fields[('monitor', 'target')][1]
        assert target.cget('state') == 'normal'
        assert target.get() == 'demo.exe'
        assert app.controller.open(null_monitor)
        assert app.show_page('监视设置')
        target = monitor_page.fields[('monitor', 'target')][1]
        assert target.cget('state') == 'disabled'
        assert target.get() == ''
    finally:
        root.destroy()


def test_model_creates_missing_mapping_but_rejects_non_mapping_parent():
    """模型只为缺失中间映射建空映射，遇到 null 父节点仍拒绝写入。"""
    document = new_document()
    del document.tree['monitor']['common']
    model = EditorModel(document)
    model.set(('monitor', 'common', 'max_wait'), '5m')
    assert model.get(('monitor', 'common', 'max_wait')) == '5m'
    broken = new_document()
    broken.tree['monitor'] = None
    with pytest.raises(TypeError):
        EditorModel(broken).set(('monitor', 'common', 'max_wait'), '5m')


def test_null_sections_disable_editing_and_show_structure_hint(tmp_path):
    """节为 null 时禁用编辑控件，输入不写模型也不改磁盘。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        monitor = ('monitor:\n  mode: watch\n  target: demo.exe\n  common: {}\n')
        cases = [
            ('monitor: null\npush: {}\nlog: {}\nexternal: {}\n',
             '监视设置', ('monitor', 'target'), 'monitor'),
            (monitor + 'push: null\nlog: {}\nexternal: {}\n',
             '消息推送设置', ('push', 'retry', 'interval'), 'push'),
            (monitor + 'push: {}\nlog: null\nexternal: {}\n',
             '日志设置', ('log', 'retention_days'), 'log'),
            (monitor + 'push: {}\nlog: {}\nexternal: null\n',
             '外部程序设置', ('external', 'on_end'), 'external'),
        ]
        for index, (text, page_name, path, section) in enumerate(cases):
            source = tmp_path / f'null-section-{index}.yaml'
            source.write_text(text, encoding='utf-8')
            assert app.controller.open(source)
            assert app.show_page(page_name)
            page = app.visible_pages[page_name]
            assert '配置结构异常，请在 YAML 中修复' in page.structure_hint.cget('text')
            widget = page.fields[path][1]
            assert widget.cget('state') == 'disabled'
            before = app.model.snapshot()[section]
            widget.insert(0, '9')
            assert app.model.snapshot()[section] == before
            assert read_document(source).tree[section] == before
    finally:
        root.destroy()


def test_open_monitor_null_refreshes_without_crash(tmp_path):
    """monitor 节点为 null 时仍能打开并显示待校验状态。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = tmp_path / 'monitor-null.yaml'
        path.write_text('monitor: null\npush: {}\nlog: {}\nexternal: {}\n',
                        encoding='utf-8')
        assert app.controller.open(path)
        app.show_page('主页')
        assert '运行方式：待校验' in app.summary_text
        assert '目标位置：待校验' in app.summary_text
        assert app.show_page('监视设置')
        assert app.visible_pages['监视设置'].fields[('monitor', 'target')][1].winfo_exists()
    finally:
        root.destroy()
