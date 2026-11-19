#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""真实 Tk 启动入口的冲突与模态重入回归。"""

import tkinter as tk
from unittest.mock import Mock

from modules.config_document import read_document, save_document
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


def test_start_cli_reload_discard_stops_without_second_error(tmp_path, monkeypatch):
    """启动时发现外部有效变更，确认放弃草稿后只重载而不启动。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        launch = Mock()
        monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
        choice = Mock(return_value=True)
        error = Mock()
        monkeypatch.setattr('modules.gui.app.messagebox.askyesnocancel', choice)
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', error)
        confirmations = []

        def confirm(kind, detail):
            """记录真正触发的放弃确认。"""
            confirmations.append(kind)
            return 'confirm' if kind == 'discard_external' else 'cancel'

        app.controller.confirm = confirm
        path = app.model.document.path
        generation = app.model.document_generation
        app.model.set(('monitor', 'target'), 'draft.exe')
        external = read_document(path)
        external.tree['monitor']['target'] = 'external.exe'
        save_document(external, path, expected_fingerprint=external.fingerprint)

        app.start_cli()

        choice.assert_called_once()
        assert confirmations == ['discard_external']
        launch.assert_not_called()
        error.assert_not_called()
        assert app.model.document_generation != generation
        assert app.model.get(('monitor', 'target')) == 'external.exe'
        assert not app.controller.conflicted
    finally:
        root.destroy()


def test_start_cli_reload_discard_cancel_preserves_draft_and_disk(tmp_path, monkeypatch):
    """外部冲突选择重载但取消放弃时不改变草稿、代次和磁盘。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        launch = Mock()
        choice = Mock(return_value=True)
        error = Mock()
        monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
        monkeypatch.setattr('modules.gui.app.messagebox.askyesnocancel', choice)
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', error)
        confirmations = []

        def confirm(kind, detail):
            """只拒绝本次外部冲突的放弃草稿确认。"""
            confirmations.append(kind)
            return 'cancel'

        app.controller.confirm = confirm
        path = app.model.document.path
        app.model.set(('monitor', 'target'), 'draft.exe')
        generation = app.model.document_generation
        external = read_document(path)
        external.tree['monitor']['target'] = 'external.exe'
        save_document(external, path, expected_fingerprint=external.fingerprint)
        disk = path.read_bytes()

        app.start_cli()

        choice.assert_called_once()
        assert confirmations == ['discard_external']
        assert app.model.get(('monitor', 'target')) == 'draft.exe'
        assert app.model.document_generation == generation
        assert app.model.dirty
        assert path.read_bytes() == disk
        assert app.controller.conflicted
        error.assert_not_called()
        launch.assert_not_called()
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


def test_start_cli_repeat_launch_cancel_has_no_error(tmp_path, monkeypatch):
    """重复启动的确认取消不误报为配置失败。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.controller.launch_count = 1
        launch = Mock()
        error = Mock()
        monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', error)
        confirmations = []

        def confirm(kind, detail):
            """记录重复启动确认并选择取消。"""
            confirmations.append(kind)
            return 'cancel'

        app.controller.confirm = confirm

        app.start_cli()

        assert confirmations == ['repeat_launch']
        error.assert_not_called()
        launch.assert_not_called()
    finally:
        root.destroy()


def test_start_cli_conflict_after_flush_has_one_dialog(tmp_path, monkeypatch):
    """补存后磁盘变化仍只显示冲突对话框而不重复报错。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        launch = Mock()
        choice = Mock(return_value=None)
        error = Mock()
        monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
        monkeypatch.setattr('modules.gui.app.messagebox.askyesnocancel', choice)
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', error)
        path = app.model.document.path
        original = app.controller.start_cli

        def start_after_external_edit():
            """模拟补存与控制器复验之间的外部改动。"""
            external = read_document(path)
            external.tree['monitor']['target'] = 'external.exe'
            save_document(external, path, expected_fingerprint=external.fingerprint)
            return original()

        monkeypatch.setattr(app.controller, 'start_cli', start_after_external_edit)

        app.start_cli()

        choice.assert_called_once()
        error.assert_not_called()
        launch.assert_not_called()
        assert app.controller.conflicted
    finally:
        root.destroy()


def test_start_cli_save_document_failure_preserves_draft(tmp_path, monkeypatch):
    """启动前实际保存写盘失败时显示固定错误并保留草稿。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        launch = Mock()
        error = Mock()
        monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', error)
        path = app.model.document.path
        disk = path.read_bytes()
        app.model.set(('monitor', 'target'), 'draft.exe')
        generation = app.model.document_generation
        monkeypatch.setattr('modules.config_document.os.replace',
                            Mock(side_effect=OSError('private-write-failure')))

        app.start_cli()

        error.assert_called_once_with('配置未保存', '配置未保存，操作已停止。草稿已保留。',
                                      parent=root)
        assert app.model.get(('monitor', 'target')) == 'draft.exe'
        assert app.model.document_generation == generation
        assert app.model.dirty
        assert path.read_bytes() == disk
        launch.assert_not_called()
    finally:
        root.destroy()


def test_start_cli_invalid_config_shows_safe_error(tmp_path, monkeypatch):
    """已保存但业务无效的配置不能启动且给出固定可见提示。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        launch = Mock()
        error = Mock()
        monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', error)
        app.model.set(('monitor', 'mode'), 'bad')

        app.start_cli()

        error.assert_called_once_with('启动 CLI',
                                      '未能启动 CLI，请检查配置、磁盘文件或保存状态。',
                                      parent=root)
        launch.assert_not_called()
        assert not app.model.dirty
    finally:
        root.destroy()
