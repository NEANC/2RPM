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
from modules.gui.controller import SaveResult
from modules.gui.model import EditorModel
from modules.gui.forms import screenshot_conditions
from modules.gui.protocol import TestRequest as GuiTestRequest
from modules.gui.protocol import TestResult as GuiTestResult


from tests.gui_support import make_app


def test_conflict_can_be_reopened_after_later(tmp_path, monkeypatch):
    """暂不处理后可再次进入冲突处理并重新加载。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = app.model.document.path
        app.model.set(('monitor', 'target'), 'draft.exe')
        path.write_bytes(b'\xff')
        answers = iter((None, True))
        monkeypatch.setattr('modules.gui.app.messagebox.askyesnocancel',
                            lambda *args, **kwargs: next(answers))
        app.save_coordinator.check_external()
        assert app.controller.conflicted
        assert app.actions['处理冲突'].winfo_manager() == 'pack'
        disk = new_document()
        disk.tree['monitor']['target'] = 'external.exe'
        path.unlink()
        save_document(disk, path, expected_fingerprint=None)
        app.controller.confirm = lambda *_: 'confirm'
        root.update_idletasks()
        app.actions['处理冲突'].invoke()
        assert not app.controller.conflicted
        assert app.model.get(('monitor', 'target')) == 'external.exe'
    finally:
        root.destroy()


@pytest.mark.parametrize('action', ['new', 'open', 'close'])
def test_incomplete_visible_input_requires_discard_confirmation(tmp_path, action):
    """基础页面未完成输入未改变配置值时，切换文档仍询问是否放弃。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        destination = tmp_path / 'another.yaml'
        if action == 'open':
            save_document(new_document(), destination, expected_fingerprint=None)
        original_days = app.model.get(('push', 'screenshot', 'retention', 'max_days'))
        page.days.number.delete(0, 'end')
        page.days.number.insert(0, '-')
        assert app.model.get(('push', 'screenshot', 'retention', 'max_days')) == original_days
        generation = app.model.document_generation
        confirm = Mock(return_value='cancel')
        app.controller.confirm = confirm
        result = app.controller.open(destination) if action == 'open' else getattr(app.controller, action)()
        assert result is False
        confirm.assert_called_once_with('unsaved', app.model.document.path)
        assert app.model.document_generation == generation
        assert page.days.number.get() == '-'
        if action == 'close':
            app.controller.runner.close.assert_not_called()
    finally:
        root.destroy()


def test_cli_launch_keeps_forms_editable(tmp_path, monkeypatch):
    """七页导航下 CLI 启动后仍可编辑，不提供日志或停止入口。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        assert app.page_names == ('主页', '监视设置', '截图设置', '消息推送设置',
                                  '外部程序设置', '日志设置', '设置')
        assert not any(name in app.actions for name in ('停止 CLI', '查看日志', '复制日志'))
        app.visible_pages['监视设置'].set_field(('monitor', 'target'), '继续编辑.exe')
        assert app.controller.model.dirty
        home = app.visible_pages['主页']
        assert 'SCTxxxx' not in home.summary_text
        assert all('SCTxxxx' not in group.detail for group in home.groups.values())
    finally:
        root.destroy()


@pytest.mark.parametrize('scaling', [1.0, 1.5, 2.0])
def test_real_tk_scaling_keeps_long_form_scrollable_and_keyboard_reachable(
        tmp_path, scaling):
    """实际 Tk 缩放及小窗口下长表单仍能滚动并经 Tab 遍历。"""
    root = ctk_kit.App(title='测试', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    root.withdraw()
    previous = float(root.tk.call('tk', 'scaling'))
    try:
        root.tk.call('tk', 'scaling', scaling)
        app = make_app(root, tmp_path / '中文目录')
        root.deiconify()
        root.geometry('600x420')
        app.show_page('消息推送设置')
        root.update()
        page = app.visible_pages['消息推送设置']
        path = ('push', 'templates', 'on_end', 'content')
        page.focus_issue(path)
        root.update()
        widget = page.fields[path][1]
        assert widget.winfo_ismapped()
        assert widget._textbox.winfo_ismapped()
        assert page._parent_canvas.yview() != (0.0, 1.0)
        assert widget.tk_focusNext() is not widget
        assert str(widget.cget('takefocus')) != '0'
        assert root.focus_get() is widget._textbox
        text = ('缩放后编辑的中文正文。' * 20) + '\n第二段'
        widget.delete('1.0', 'end')
        widget.insert('1.0', text)
        widget._textbox.event_generate('<KeyRelease>')
        root.update()
        assert app.model.get(path) == text
        assert page.fields[path][1] is widget
        assert root.focus_get() is widget._textbox
    finally:
        root.tk.call('tk', 'scaling', previous)
        if 'app' in locals():
            root.after_cancel(app._after_id)
        root.destroy()


def test_leaving_config_page_flushes_immediately(tmp_path):
    """离开配置页立即落盘，不依赖 500ms 防抖回调。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = app.model.document.path
        assert app.show_page('监视设置')
        app.model.set(('monitor', 'target'), 'switch.exe')
        assert app.model.dirty
        assert app.show_page('主页')
        assert not app.model.dirty
        assert read_document(path).tree['monitor']['target'] == 'switch.exe'
    finally:
        root.destroy()


def test_leaving_config_page_flush_failure_keeps_draft_and_warns(tmp_path):
    """补存失败仍允许回主页，并提示草稿已保留。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        assert app.show_page('监视设置')
        app.model.set(('monitor', 'target'), 'draft.exe')
        app.save_coordinator.flush_result = Mock(return_value=SaveResult.FAILED)
        assert app.show_page('主页')
        assert '配置未保存，已保留草稿' in app.status.cget('text')
        assert app.current_page == '主页'
        assert app.model.get(('monitor', 'target')) == 'draft.exe'
    finally:
        root.destroy()
