#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""真实 Tk 窗口的显式文件操作与测试结果归属回归。"""

import os
import tkinter as tk
from pathlib import Path
from unittest.mock import Mock

import ctk_kit
import pytest
from PIL import Image

from modules.config_document import ConfigSaveError
from modules.config_document import new_document
from modules.config_document import save_document
from modules.gui.protocol import TestResult as GuiResult
from modules.gui.protocol import TestRequest as GuiRequest
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


def seed_preview(app, tmp_path):
    """用已接纳的截图结果登记会话预览，另放两种用户资源。"""
    preview = app.session_dir / 'owned.jpg'
    preview.write_bytes(b'owned')
    upload = tmp_path / 'upload.jpg'
    upload.write_bytes(b'upload')
    business = tmp_path / 'business.jpg'
    business.write_bytes(b'business')
    request = GuiRequest('owned', app.model.document_generation, app.model.revision,
                         'capture', {}, str(tmp_path), str(app.session_dir), 0,
                         None, None, None)
    app.test_session.active_request = request
    app.controller.runner.results.put(GuiResult('owned', request.document_generation,
                                                request.revision, 'success', '',
                                                (str(preview), str(business))))
    app.drain_results()
    upload_request = GuiRequest('upload', request.document_generation, request.revision,
                                'upload', {}, str(tmp_path), str(app.session_dir),
                                None, 0, str(upload), None)
    app.test_session.active_request = upload_request
    app.controller.runner.results.put(GuiResult('upload', request.document_generation,
                                                request.revision, 'success', '',
                                                (str(upload),)))
    app.drain_results()
    return preview, upload, business


@pytest.mark.parametrize('action', ['new', 'open', 'save_as'])
def test_successful_switch_deletes_only_owned_preview(window, tmp_path, monkeypatch,
                                                       action):
    """成功切换只删除会话截图，保留上传原图和业务截图。"""
    app = window
    preview, upload, business = seed_preview(app, tmp_path)
    target = tmp_path / 'other.yaml'
    if action == 'open':
        save_document(new_document(), target, expected_fingerprint=None)
    app.controller.confirm = lambda *_: 'confirm'
    picker = 'askopenfilename' if action == 'open' else 'asksaveasfilename'
    monkeypatch.setattr('modules.gui.app.filedialog.' + picker,
                        lambda **_: str(target))

    getattr(app, action)()

    assert app.model.document.path == target
    assert not preview.exists()
    assert upload.read_bytes() == b'upload'
    assert business.read_bytes() == b'business'


def test_preview_delete_failure_keeps_retry_path_and_new_config_usable(
        window, tmp_path, monkeypatch):
    """清理失败不回滚新配置，下次成功切换可重试原预览。"""
    app = window
    preview, upload, business = seed_preview(app, tmp_path)
    target = tmp_path / 'next.yaml'
    later = tmp_path / 'later.yaml'
    app.controller.confirm = lambda *_: 'confirm'
    destinations = iter((str(target), str(later)))
    monkeypatch.setattr('modules.gui.app.filedialog.asksaveasfilename',
                        lambda **_: next(destinations))
    original_unlink = Path.unlink
    attempts = []

    def fail_once(path, *args, **kwargs):
        """仅让所属预览首次删除失败，其余文件操作保持真实。"""
        if path == preview:
            attempts.append(path)
            if len(attempts) <= 2:
                raise PermissionError('文件占用')
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail_once)
    app.save_as()
    assert app.model.document.path == target
    assert preview.exists()
    assert attempts == [preview, preview]
    assert preview in app._owned_previews
    assert app.test_session.last_capture is None
    app.model.set(('monitor', 'target'), 'next.exe')
    assert app.controller.save()
    app.save_as()

    assert app.model.document.path == later
    assert attempts == [preview, preview, preview]
    assert not preview.exists()
    assert preview not in app._owned_previews
    assert upload.read_bytes() == b'upload'
    assert business.read_bytes() == b'business'


def test_replaced_session_directory_does_not_delete_outside_preview(
        window, tmp_path):
    """会话目录被换成有效目录链接时，不能误删外部同名文件。"""
    app = window
    preview, _, _ = seed_preview(app, tmp_path)
    original_dir = app.session_dir.with_name(app.session_dir.name + '-original')
    outside_dir = app.session_dir.with_name(app.session_dir.name + '-outside')
    outside_dir.mkdir()
    outside = outside_dir / preview.name
    outside.write_bytes(b'outside')
    app.session_dir.rename(original_dir)
    os.symlink(outside_dir.name, app.session_dir, target_is_directory=True)
    assert app.session_dir.is_symlink()
    assert preview.read_bytes() == b'outside'

    app._clear_test_results()

    assert outside.read_bytes() == b'outside'
    assert (original_dir / preview.name).read_bytes() == b'owned'
    assert preview in app._owned_previews


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
