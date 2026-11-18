#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 会话目录初始化异常与正常路径测试。"""

import shutil
import tkinter as tk
from unittest.mock import Mock

import pytest

from modules.gui import app as app_module
from tests.gui_support import make_app


def test_mkdtemp_failure_leaves_no_poll_callback(tmp_path, monkeypatch):
    """目录创建失败时不留下已注册的轮询回调。"""
    root = tk.Tk()
    original_after = root.after
    poll_ids = []

    def record_after(delay, callback=None, *args):
        """记录构造期间注册的轮询回调标识。"""
        after_id = original_after(delay, callback, *args)
        if getattr(callback, '__name__', '') == '_poll':
            poll_ids.append(after_id)
        return after_id

    monkeypatch.setattr(root, 'after', record_after)
    monkeypatch.setattr(app_module.tempfile, 'mkdtemp',
                        Mock(side_effect=OSError('创建失败')))
    try:
        with pytest.raises(OSError, match='创建失败'):
            make_app(root, tmp_path)
        assert not set(poll_ids) & set(root.tk.call('after', 'info'))
        assert not list(tmp_path.glob('2rpm-gui-*'))
    finally:
        root.destroy()


def test_session_resolve_failure_removes_created_directory(tmp_path, monkeypatch):
    """目录创建后初始化失败时清理已创建的会话目录。"""
    root = tk.Tk()
    monkeypatch.setattr(app_module.tempfile, 'tempdir', str(tmp_path))
    original_resolve = app_module.Path.resolve

    def fail_session_resolve(path, *args, **kwargs):
        """仅模拟会话目录解析失败。"""
        if path.name.startswith('2rpm-gui-'):
            raise RuntimeError('解析失败')
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(app_module.Path, 'resolve', fail_session_resolve)
    try:
        with pytest.raises(RuntimeError, match='解析失败'):
            make_app(root, tmp_path)
        assert not list(tmp_path.glob('2rpm-gui-*'))
    finally:
        root.destroy()


@pytest.mark.parametrize('failure', ['early', 'session'])
def test_failed_app_constructor_cancels_coordinator_poll(tmp_path, monkeypatch, failure):
    """构造任一阶段失败都撤销协调器真实注册的轮询。"""
    root = tk.Tk()
    original_after = root.after
    polls = []

    def record_after(delay, callback=None, *args):
        """保留协调器真实 Tk 定时器标识。"""
        after_id = original_after(delay, callback, *args)
        if delay == 1000 and callback is not None:
            polls.append(after_id)
        return after_id

    monkeypatch.setattr(root, 'after', record_after)
    monkeypatch.setattr(app_module.tempfile, 'tempdir', str(tmp_path))
    if failure == 'early':
        monkeypatch.setattr(app_module, 'work_area_size',
                            Mock(side_effect=RuntimeError('构造失败')))
    else:
        original_resolve = app_module.Path.resolve

        def fail_session_resolve(path, *args, **kwargs):
            """仅让已创建的会话目录解析失败。"""
            if path.name.startswith('2rpm-gui-'):
                raise RuntimeError('解析失败')
            return original_resolve(path, *args, **kwargs)

        monkeypatch.setattr(app_module.Path, 'resolve', fail_session_resolve)
    try:
        with pytest.raises(RuntimeError):
            make_app(root, tmp_path)
        assert polls
        assert not set(polls) & set(root.tk.call('after', 'info'))
        assert not list(tmp_path.glob('2rpm-gui-*'))
    finally:
        root.destroy()


def test_session_creation_registers_poll_and_keeps_directory(tmp_path, monkeypatch):
    """正常构造保留轮询回调和会话目录供后续测试使用。"""
    root = tk.Tk()
    monkeypatch.setattr(app_module.tempfile, 'tempdir', str(tmp_path))
    app = None
    try:
        app = make_app(root, tmp_path)
        assert app._after_id in root.tk.call('after', 'info')
        assert app.session_dir.is_dir()
        assert app.session_dir.parent == tmp_path
    finally:
        if app is not None:
            root.after_cancel(app._after_id)
        root.destroy()
        if app is not None:
            shutil.rmtree(app.session_dir)
