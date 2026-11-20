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
from modules.config_document import read_document
from modules.config_document import save_document
from modules.gui.app import messagebox
from modules.gui.protocol import TestResult as GuiResult
from modules.gui.protocol import TestRequest as GuiRequest
from tests.gui_support import make_app


@pytest.fixture
def window(tmp_path, monkeypatch):
    """建立含结果窗的真实 Tk 界面并隔离系统文件对话框。"""
    root = tk.Tk()
    root.withdraw()
    monkeypatch.setattr('modules.gui.app.messagebox.showerror', Mock())
    monkeypatch.setattr('modules.gui.app.messagebox.askyesnocancel', lambda *_, **__: None)
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
@pytest.mark.parametrize('damage', ['invalid', 'missing'])
def test_dirty_picker_unavailable_bytes_preserve_draft(window, monkeypatch, action, damage):
    """真实选择器返回坏 YAML 或缺失路径时保留草稿代次并冻结原路径。"""
    app = window
    path = app.model.document.path
    app.model.set(('monitor', 'target'), 'draft.exe')
    generation = app.model.document_generation
    revision = app.model.revision
    app.controller.confirm = lambda kind, _: 'discard' if kind == 'unsaved' else 'confirm'

    def picker(**_):
        """在选择器期间破坏原始磁盘字节。"""
        if damage == 'invalid':
            path.write_bytes(b'\xff')
        else:
            path.unlink()
        return str(path)

    name = 'askopenfilename' if action == 'open' else 'asksaveasfilename'
    monkeypatch.setattr('modules.gui.app.filedialog.' + name, picker)
    getattr(app, action)()

    assert app.model.document.path == path
    assert app.model.document_generation == generation
    assert app.model.revision == revision
    assert app.model.get(('monitor', 'target')) == 'draft.exe'
    assert app.model.dirty and app.controller.conflicted
    assert not app.controller.save()
    if damage == 'invalid':
        assert path.read_bytes() == b'\xff'
    else:
        assert not path.exists()


@pytest.mark.parametrize('action', ['new', 'open', 'save_as'])
def test_dirty_picker_valid_update_preserves_full_disk_bytes(window, monkeypatch, action):
    """有效 YAML 的任意原始字节更新在选择期间都不被旧草稿覆盖。"""
    app = window
    path = app.model.document.path
    app.model.set(('monitor', 'target'), 'draft.exe')
    generation = app.model.document_generation
    app.controller.confirm = lambda kind, _: 'discard' if kind == 'unsaved' else 'confirm'
    original = path.read_bytes()
    updated = original + b'\n# full bytes\n'

    def picker(**_):
        """在选择器内更改有效 YAML 文件的末尾字节。"""
        path.write_bytes(updated)
        return str(path)

    name = 'askopenfilename' if action == 'open' else 'asksaveasfilename'
    monkeypatch.setattr('modules.gui.app.filedialog.' + name, picker)
    getattr(app, action)()

    if action == 'open':
        assert app.model.document_generation == generation + 1
        assert not app.model.dirty
    else:
        assert app.model.document_generation == generation
        assert app.model.get(('monitor', 'target')) == 'draft.exe'
        assert app.controller.conflicted and not app.controller.save()
    assert path.read_bytes() == updated


@pytest.mark.parametrize('cancel_at', ['unsaved', 'picker'])
def test_open_cancel_does_not_show_failure(window, monkeypatch, cancel_at):
    """真实 Tk 未保存确认或选择器取消都不报错且保留草稿。"""
    app = window
    app.model.set(('monitor', 'target'), 'draft.exe')
    app.controller.confirm = lambda *_: 'cancel' if cancel_at == 'unsaved' else 'discard'
    generation = app.model.document_generation
    picker = Mock(return_value='')
    monkeypatch.setattr('modules.gui.app.filedialog.askopenfilename', picker)
    error = messagebox.showerror

    app.open()

    error.assert_not_called()
    if cancel_at == 'unsaved':
        picker.assert_not_called()
    else:
        picker.assert_called_once()
    assert app.model.document_generation == generation
    assert app.model.get(('monitor', 'target')) == 'draft.exe'
    assert app.model.dirty


def test_open_invalid_file_shows_failure(window, monkeypatch, tmp_path):
    """真实 Tk 选择坏文件时显示错误且保留旧文档。"""
    app = window
    path = app.model.document.path
    broken = tmp_path / 'broken.yaml'
    broken.write_bytes(b'\xff')
    monkeypatch.setattr('modules.gui.app.filedialog.askopenfilename',
                        lambda **_: str(broken))

    app.open()

    messagebox.showerror.assert_called_once_with('打开配置', '无法打开配置',
                                                 parent=app.root)
    assert app.model.document.path == path


@pytest.mark.parametrize('outer_broken', [True, False], ids=['outer-broken', 'inner-broken'])
def test_open_modal_reentry_keeps_failure_with_its_own_call(
        window, monkeypatch, tmp_path, outer_broken):
    """真实 Tk 模态循环重入时，失败只归属于选中坏文件的那次打开。"""
    app = window
    original = app.model.document.path
    app.model.set(('monitor', 'target'), 'draft.exe')
    app.controller.confirm = lambda *_: 'discard'
    generation = app.model.document_generation
    revision = app.model.revision
    capture, upload, dialog, image = seed_result(app)
    broken = tmp_path / 'broken.yaml'
    broken.write_bytes(b'\xff')
    selections = iter((str(broken) if outer_broken else '',
                       '' if outer_broken else str(broken)))
    events = []
    callback_errors = []

    def picker(**_):
        """首次选择进入真实 Tk 模态循环，并在其中执行第二次打开。"""
        selected = next(selections)
        if events:
            events.append(('inner-picker', selected))
            return selected
        events.append(('outer-picker', selected))
        modal = tk.Toplevel(app.root)
        modal.update()
        assert modal.winfo_ismapped()
        modal.grab_set()

        def reenter():
            """在模态事件循环里重入，返回前记录内层错误数量。"""
            events.append(('reenter', modal.grab_current() is modal))
            try:
                app.open()
                events.append(('inner-return', messagebox.showerror.call_count))
            except Exception as exc:
                callback_errors.append(exc)
            finally:
                modal.destroy()

        modal.after(0, reenter)
        modal.wait_window()
        if callback_errors:
            raise callback_errors[0]
        events.append(('outer-resume', messagebox.showerror.call_count))
        return selected

    monkeypatch.setattr('modules.gui.app.filedialog.askopenfilename', picker)

    app.open()

    assert events == [('outer-picker', str(broken) if outer_broken else ''),
                      ('reenter', True),
                      ('inner-picker', '' if outer_broken else str(broken)),
                      ('inner-return', 0 if outer_broken else 1),
                      ('outer-resume', 0 if outer_broken else 1)]
    messagebox.showerror.assert_called_once_with('打开配置', '无法打开配置',
                                                 parent=app.root)
    assert app.model.document.path == original
    assert app.model.document_generation == generation
    assert app.model.revision == revision
    assert app.model.get(('monitor', 'target')) == 'draft.exe'
    assert app.model.dirty
    assert app.test_session.last_capture is capture
    assert app.test_session.last_upload is upload
    assert app.test_dialogs.result_window is dialog
    assert dialog.winfo_exists()
    assert app.preview_image is image
    assert app.result_text == '旧结果'


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


def test_running_worker_conflict_dialog_save_as_is_blocked(window, monkeypatch, tmp_path):
    """测试运行中从冲突弹窗另存须提示等待且不清除结果或关闭运行器。"""
    app = window
    original = app.model.document.path
    generation = app.model.document_generation
    capture, upload, dialog, image = seed_result(app)
    request = GuiRequest('active', generation, app.model.revision, 'capture', {},
                         str(tmp_path), str(app.session_dir), 0, None, None, None)
    app.test_session.active_request = request
    app.controller.runner.active = True
    picker = Mock(return_value=str(tmp_path / 'saved.yaml'))
    info = Mock()
    monkeypatch.setattr('modules.gui.app.filedialog.asksaveasfilename', picker)
    monkeypatch.setattr('modules.gui.app.messagebox.showinfo', info)
    monkeypatch.setattr('modules.gui.app.messagebox.askyesnocancel',
                        lambda *_, **__: False)

    app._show_conflict()

    info.assert_called_once_with('配置切换', '请等待测试完成或取消测试后切换配置',
                                 parent=app.root)
    picker.assert_not_called()
    assert app.model.document.path == original
    assert app.model.document_generation == generation
    assert app.test_session.active_request is request
    assert app.test_session.last_capture is capture
    assert app.test_session.last_upload is upload
    assert app.test_dialogs.result_window is dialog
    assert dialog.winfo_exists()
    assert app.preview_image is image
    assert app.result_text == '旧结果'
    app.controller.runner.close.assert_not_called()


def test_conflict_reload_from_save_does_not_report_unsaved_failure(window, monkeypatch):
    """冲突中选择重新加载磁盘版本后不得提示配置未保存。"""
    app = window
    path = app.model.document.path
    app.model.set(('monitor', 'target'), 'draft.exe')
    disk = read_document(path)
    disk.tree['monitor']['target'] = 'external.exe'
    save_document(disk, path, expected_fingerprint=disk.fingerprint)
    app.controller.confirm = lambda *_: 'confirm'
    monkeypatch.setattr('modules.gui.app.messagebox.askyesnocancel',
                        lambda *_, **__: True)

    app.save()

    messagebox.showerror.assert_not_called()
    assert app.status.cget('text') != '配置未保存'
    assert app.model.get(('monitor', 'target')) == 'external.exe'
    assert app.model.snapshot() == read_document(path).tree
    assert not app.model.dirty
    assert not app.controller.conflicted
