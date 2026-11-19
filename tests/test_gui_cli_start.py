#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""真实 Tk 启动入口的冲突与模态重入回归。"""

import tkinter as tk
from unittest.mock import Mock

from tests.gui_support import make_app


def test_start_cli_conflict_choice_does_not_show_second_popup(tmp_path, monkeypatch):
    """磁盘冲突三选项已展示时不重复报告启动失败。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        launch = Mock()
        monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
        choice = Mock(return_value=None)
        error = Mock()
        monkeypatch.setattr('modules.gui.app.messagebox.askyesnocancel', choice)
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', error)
        app.model.set(('monitor', 'target'), 'draft.exe')
        app.model.document.path.write_bytes(b'external')

        app.start_cli()

        choice.assert_called_once()
        error.assert_not_called()
        launch.assert_not_called()
        assert app.controller.conflicted
    finally:
        root.destroy()


def test_start_cli_nested_cancel_does_not_hide_outer_block(tmp_path, monkeypatch):
    """内层取消不应将外层配置变动后的阻断当作取消。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.controller.launch_count = 1
        launch = Mock()
        monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
        error = Mock()
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', error)
        confirmations = []

        def confirm(kind, detail):
            """在外层确认期间重入启动，内层取消后改变草稿。"""
            confirmations.append(kind)
            if len(confirmations) == 1:
                app.start_cli()
                app.model.set(('monitor', 'target'), 'new-draft.exe')
                return 'confirm'
            return 'cancel'

        app.controller.confirm = confirm

        app.start_cli()

        assert confirmations == ['repeat_launch', 'repeat_launch']
        error.assert_called_once_with('启动 CLI',
                                      '未能启动 CLI，请检查配置、磁盘文件或保存状态。',
                                      parent=root)
        launch.assert_not_called()
    finally:
        root.destroy()
