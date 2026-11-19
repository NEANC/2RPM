#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""真实 Tk 窗口的显式文件操作与测试结果归属回归。"""

import tkinter as tk
from unittest.mock import Mock

import ctk_kit
import pytest
from PIL import Image

from modules.config_document import ConfigSaveError
from modules.config_document import new_document
from modules.config_document import save_document
from modules.gui.protocol import TestResult as GuiResult
from tests.gui_support import make_app


@pytest.fixture
def window(tmp_path, monkeypatch):
    """建立含结果窗的真实 Tk 界面并隔离系统文件对话框。"""
    root = tk.Tk()
    root.withdraw()
    monkeypatch.setattr('modules.gui.app.messagebox.showerror', Mock())
    app = None
    try:
        app = make_app(root, tmp_path)
        yield app
    finally:
        if app is not None:
            app.save_coordinator.close()
            root.after_cancel(app._after_id)
        root.destroy()


def seed_result(app):
    """模拟已有截图和上传结果及窗口持有的真实 Tk 图片。"""
    generation = app.model.document_generation
    capture = GuiResult('capture', generation, 0, 'success', '', ())
    upload = GuiResult('upload', generation, 0, 'success', '', (), 'https://example.org/image')
    app.test_session.last_capture = capture
    app.test_session.last_upload = upload
    app.test_dialogs.show_result('capture', capture)
    preview = Image.new('RGB', (2, 2))
    image = ctk_kit.CTkImage(light_image=preview, dark_image=preview, size=(2, 2))
    app.preview_image = image
    app.preview_label.configure(image=image)
    app.result_text = '旧结果'
    return capture, upload, app.test_dialogs.result_window, image


@pytest.mark.parametrize('action,same_path', [
    ('new', False), ('new', True), ('open', False), ('open', True),
    ('save_as', False), ('save_as', True),
])
def test_explicit_file_success_clears_old_results(window, tmp_path, monkeypatch,
                                                   action, same_path):
    """成功显式新建、打开或另存后，即使仍为原路径也丢弃旧窗口图片。"""
    app = window
    original = app.model.document.path
    target = original if same_path else tmp_path / 'other.yaml'
    if action == 'open' and not same_path:
        save_document(new_document(), target, expected_fingerprint=None)
    app.controller.confirm = lambda *_: 'confirm'
    picker_name = 'askopenfilename' if action == 'open' else 'asksaveasfilename'
    monkeypatch.setattr('modules.gui.app.filedialog.' + picker_name,
                        lambda **_: str(target))
    capture, upload, dialog, image = seed_result(app)

    getattr(app, action)()

    assert app.model.document.path == target
    assert app.test_session.last_capture is None
    assert app.test_session.last_upload is None
    assert app.test_dialogs.result_window is None
    assert not dialog.winfo_exists()
    assert app.preview_image is None
    assert app.preview_label.cget('image') in ('', None)
    assert app.result_text == ''


@pytest.mark.parametrize('action', ['new', 'open', 'save_as'])
def test_cancelled_file_action_keeps_old_results(window, monkeypatch, action):
    """文件选择取消不得清除原配置的结果和图片。"""
    app = window
    original = app.model.document.path
    picker_name = 'askopenfilename' if action == 'open' else 'asksaveasfilename'
    monkeypatch.setattr('modules.gui.app.filedialog.' + picker_name, lambda **_: '')
    capture, upload, dialog, image = seed_result(app)

    getattr(app, action)()

    assert app.model.document.path == original
    assert app.test_session.last_capture is capture
    assert app.test_session.last_upload is upload
    assert app.test_dialogs.result_window is dialog
    assert dialog.winfo_exists()
    assert app.preview_image is image
    assert app.result_text == '旧结果'


@pytest.mark.parametrize('action', ['new', 'open', 'save_as'])
def test_failed_file_action_keeps_old_results(window, tmp_path, monkeypatch, action):
    """真实文件读取或写入失败不能销毁当前测试结果。"""
    app = window
    target = tmp_path / 'other.yaml'
    app.controller.confirm = lambda *_: 'confirm'
    picker_name = 'askopenfilename' if action == 'open' else 'asksaveasfilename'
    monkeypatch.setattr('modules.gui.app.filedialog.' + picker_name,
                        lambda **_: str(target))
    if action != 'open':
        def fail_save(*args, **kwargs):
            """仅在真实控制器的写盘边界模拟失败。"""
            raise ConfigSaveError('写入失败')

        monkeypatch.setattr('modules.gui.controller.save_document', fail_save)
    capture, upload, dialog, image = seed_result(app)
    original = app.model.document.path

    getattr(app, action)()

    assert app.model.document.path == original
    assert app.test_session.last_capture is capture
    assert app.test_session.last_upload is upload
    assert app.test_dialogs.result_window is dialog
    assert dialog.winfo_exists()
    assert app.preview_image is image
    assert app.result_text == '旧结果'


@pytest.mark.parametrize('action', ['new', 'open', 'save_as'])
def test_running_worker_blocks_file_action_before_picker(window, monkeypatch, action):
    """正在运行时所有显式文件操作均不打开选择器或清除结果。"""
    app = window
    capture, upload, dialog, image = seed_result(app)
    app.controller.runner.active = True
    picker_name = 'askopenfilename' if action == 'open' else 'asksaveasfilename'
    picker = Mock()
    monkeypatch.setattr('modules.gui.app.filedialog.' + picker_name, picker)

    getattr(app, action)()

    picker.assert_not_called()
    assert app.test_session.last_capture is capture
    assert app.test_session.last_upload is upload
    assert app.test_dialogs.result_window is dialog
    assert app.preview_image is image
