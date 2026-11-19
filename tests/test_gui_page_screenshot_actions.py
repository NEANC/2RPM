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


def test_screenshot_autosave_keeps_editing_widget_and_cursor(tmp_path):
    """真实 Tk 自动保存同一文档时保留正在编辑的目标行及光标。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        page.add_target()
        app.show_page('截图设置')
        entry = page.target_rows[-1]['entry']
        root.update()
        entry.set('窗口标题', notify=True)
        entry._entry.icursor(2)
        entry._entry.focus_force()
        root.update_idletasks()
        assert root.focus_get() == entry._entry._entry

        assert app.save_coordinator.flush()

        assert read_document(app.model.document.path).tree['push']['screenshot']['targets'][-1]['target'] == '窗口标题'
        assert entry.winfo_exists()
        assert page.target_rows[-1]['entry'] is entry
        assert root.focus_get() == entry._entry._entry
        assert entry._entry.index('insert') == 2
    finally:
        root.destroy()


def test_same_document_save_updates_home_without_rebuilding_screenshot(tmp_path):
    """真实 Tk 纯保存同步主页，同时保持截图输入和校验状态。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        home = app.visible_pages['主页']
        page.add_target()
        app.show_page('截图设置')
        entry = page.target_rows[-1]['entry']
        root.update()
        app.validate()
        assert home.check.cget('text') != '尚未校验'
        assert '目标位置：已配置' in app.summary_text

        app.model.set(('monitor', 'target'), '')
        entry.set('窗口标题', notify=True)
        entry._entry.icursor(2)
        entry._entry.focus_force()
        root.update_idletasks()
        assert '（未保存）' in app.path_label.cget('text')
        assert home.check.cget('text') == '尚未校验'

        assert app.save_coordinator.flush()

        assert '目标位置：未配置' in app.summary_text
        assert home.check.cget('text') == '尚未校验'
        assert '（未保存）' not in app.path_label.cget('text')
        assert not app.model.dirty
        assert page.target_rows[-1]['entry'] is entry
        assert entry.winfo_exists()
        assert root.focus_get() == entry._entry._entry
        assert entry._entry.index('insert') == 2
    finally:
        root.destroy()


def test_screenshot_save_with_normalized_tree_refreshes_rows(tmp_path, monkeypatch):
    """保存结果发生规范化时仍用真实 Tk 刷新目标行。"""
    from modules.config_document import ConfigDocument
    import modules.gui.controller as controller_module

    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        page.add_target()
        entry = page.target_rows[-1]['entry']
        original_save = controller_module.save_document

        def normalized_save(document, destination, *, expected_fingerprint):
            """模拟持久化层返回经过规范化的文档树。"""
            tree = document.tree.copy()
            tree['push'] = tree['push'].copy()
            tree['push']['screenshot'] = tree['push']['screenshot'].copy()
            tree['push']['screenshot']['targets'] = [
                *tree['push']['screenshot']['targets'][:-1],
                {'provider': 'window', 'target': '规范化窗口'}]
            return original_save(ConfigDocument(document.path, tree, document.fingerprint),
                                 destination, expected_fingerprint=expected_fingerprint)

        monkeypatch.setattr(controller_module, 'save_document', normalized_save)
        app.model.set(('monitor', 'target'), '变更.exe')
        assert app.save_coordinator.flush()
        assert not entry.winfo_exists()
        assert page.target_rows[-1]['entry'].get() == '规范化窗口'
    finally:
        root.destroy()


def test_test_request_uses_document_directory(tmp_path):
    """已保存配置从文件目录解析，测试请求不暴露在命令行。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.test_dialogs.open_capture = Mock()
        app.test_dialogs.show_result = Mock()
        app.visible_pages['截图设置'].add_target()
        app.visible_pages['截图设置']._set_item(('push', 'screenshot', 'targets'), 0, 'target', '测试窗口')
        app.capture()
        assert app.test_dialogs.open_capture.call_args.args[0]('window', '测试窗口')
        request = app.controller.runner.start.call_args.args[0]
        assert request.capture_provider == 'window'
        assert request.capture_target == '测试窗口'
        assert request.config_dir == str(tmp_path.resolve())
        assert request.document_generation == app.model.document_generation
    finally:
        root.destroy()


def test_capture_button_saves_valid_draft_without_injecting_screenshot_target(tmp_path):
    """真实截图按钮补存合法草稿，手填目标只进入请求而不落入 YAML。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.show_page('截图设置')
        page = app.visible_pages['截图设置']
        original_targets = read_document(app.model.document.path).tree['push']['screenshot']['targets']
        app.visible_pages['监视设置'].set_field(('monitor', 'target'), 'draft.exe')

        page.capture_button.invoke()
        dialogs = app.test_dialogs
        assert dialogs.selection_window.winfo_exists()
        assert read_document(app.model.document.path).tree['monitor']['target'] == 'draft.exe'
        dialogs.capture_target.insert(0, '手填窗口')
        dialogs.capture_start.invoke()

        request = app.controller.runner.start.call_args.args[0]
        assert (request.capture_provider, request.capture_target) == ('window', '手填窗口')
        assert request.config['push']['screenshot']['targets'] == original_targets
        saved = read_document(app.model.document.path).tree
        assert saved['push']['screenshot']['targets'] == original_targets
        assert saved['monitor']['target'] == 'draft.exe'
    finally:
        root.destroy()


def test_capture_selection_rejects_document_change_during_request(tmp_path, monkeypatch):
    """截图选择提交后若补存改变文档身份，不得提交旧目标。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.test_dialogs.open_capture = Mock()
        app.capture()
        submit = app.test_dialogs.open_capture.call_args.args[0]
        original = app.save_coordinator.flush
        calls = 0

        def flush():
            """模拟选择提交与请求冻结之间发生文档重载。"""
            nonlocal calls
            calls += 1
            if calls == 2:
                app.model.replace_document(read_document(app.model.document.path))
            return original()

        monkeypatch.setattr(app.save_coordinator, 'flush', flush)
        assert not submit('window', '旧窗口')
        app.controller.runner.start.assert_not_called()
    finally:
        root.destroy()


@pytest.mark.parametrize('changed', ['source', 'target'])
def test_capture_selection_changed_during_request_is_not_sent(tmp_path, monkeypatch,
                                                              changed):
    """截图请求补存期间改变来源或目标时，不得提交先前选择。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.open_capture_selection()
        dialogs = app.test_dialogs
        dialogs.capture_target.insert(0, '旧窗口')
        original = app.save_coordinator.flush
        calls = 0

        def flush():
            """在选择提交后的第二次补存期间改变真实控件。"""
            nonlocal calls
            calls += 1
            if calls == 2:
                if changed == 'source':
                    dialogs.capture_provider.set('ADB')
                else:
                    dialogs.capture_target.delete(0, 'end')
                    dialogs.capture_target.insert(0, '新窗口')
            return original()

        monkeypatch.setattr(app.save_coordinator, 'flush', flush)
        dialogs.submit_capture()
        app.controller.runner.start.assert_not_called()
    finally:
        root.destroy()


def test_closed_capture_selection_callback_cannot_submit_after_reopen(tmp_path):
    """旧截图选择窗关闭重开后，旧回调不能提交或关闭新窗口。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.open_capture_selection()
        dialogs = app.test_dialogs
        old_callback = dialogs._capture_callback
        dialogs.selection_window.destroy()
        app.open_capture_selection()
        new_window = dialogs.selection_window
        dialogs.capture_target.insert(0, '旧窗口')

        assert not old_callback('window', '旧窗口')
        app.controller.runner.start.assert_not_called()
        assert new_window.winfo_exists()
    finally:
        root.destroy()


def test_upload_selection_changed_during_request_flush_is_not_sent(tmp_path, monkeypatch):
    """上传请求补存期间改选图床，不得沿用旧图床索引。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('push', 'screenshot', 'image_host'),
                      [{'provider': 'beeimg'}, {'provider': 'catbox'}])
        app.open_upload_selection()
        dialogs = app.test_dialogs
        dialogs.upload_host.set('01 beeimg')
        dialogs.upload_image.set(str(tmp_path / 'one.png'))
        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *args, **kwargs: True)
        original = app.save_coordinator.flush
        calls = 0

        def flush():
            """在请求冻结前的补存期间改选图床。"""
            nonlocal calls
            calls += 1
            if calls == 1:
                dialogs.upload_host.set('02 catbox')
            return original()

        monkeypatch.setattr(app.save_coordinator, 'flush', flush)
        dialogs.submit_upload()
        app.controller.runner.start.assert_not_called()
    finally:
        root.destroy()


@pytest.mark.parametrize('reopen', [False, True])
def test_upload_callback_rejects_window_closed_during_flush(tmp_path, monkeypatch, reopen):
    """补存模态期间旧上传窗关闭或重开，旧回调不得启动 worker。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('push', 'screenshot', 'image_host'), [{'provider': 'beeimg'}])
        app.open_upload_selection()
        dialogs = app.test_dialogs
        old_callback = dialogs._upload_callback
        old_window = dialogs.selection_window
        image = str(tmp_path / 'one.png')
        dialogs.upload_host.set('01 beeimg')
        dialogs.upload_image.set(image)
        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *args, **kwargs: True)
        original = app.save_coordinator.flush
        calls = 0

        def flush():
            """模拟模态补存时关闭旧窗并选择性重开同配置的新窗。"""
            nonlocal calls
            calls += 1
            if calls == 1:
                old_window.destroy()
                if reopen:
                    app.open_upload_selection()
                    dialogs.upload_host.set('01 beeimg')
                    dialogs.upload_image.set(image)
            return original()

        monkeypatch.setattr(app.save_coordinator, 'flush', flush)
        assert not old_callback(0, image)
        app.controller.runner.start.assert_not_called()
        if reopen:
            assert dialogs.selection_window.winfo_exists()
    finally:
        root.destroy()


@pytest.mark.parametrize('changed', ['host', 'image'])
def test_upload_selection_changed_during_confirmation_is_not_sent(tmp_path, monkeypatch,
                                                                  changed):
    """外发确认期间改选图床或图片，不得提交确认前的旧选择。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('push', 'screenshot', 'image_host'),
                      [{'provider': 'beeimg'}, {'provider': 'catbox'}])
        app.open_upload_selection()
        dialogs = app.test_dialogs
        dialogs.upload_host.set('01 beeimg')
        dialogs.upload_image.set(str(tmp_path / 'one.png'))

        def confirm(*args, **kwargs):
            """模拟模态确认期间用户改变原选择窗。"""
            if changed == 'host':
                dialogs.upload_host.set('02 catbox')
            else:
                dialogs.upload_image.set(str(tmp_path / 'two.png'))
            return True

        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', confirm)
        dialogs.submit_upload()
        app.controller.runner.start.assert_not_called()
    finally:
        root.destroy()


@pytest.mark.parametrize('action', ['capture', 'upload'])
def test_ctk_result_window_restores_active_request_and_cancels_once(
        tmp_path, monkeypatch, action):
    """真实 ctk App 隐藏后从同类型页恢复原结果窗，取消只针对原请求。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *args, **kwargs: True)
    root = ctk_kit.App(title='测试', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        app.show_page('截图设置')
        root.update()
        page = app.visible_pages['截图设置']
        button = page.capture_button if action == 'capture' else page.upload_button
        if action == 'upload':
            app.model.set(('push', 'screenshot', 'image_host'),
                          [{'provider': 'beeimg'}])
        button.invoke()
        selection = app.test_dialogs.selection_window
        assert selection.winfo_exists()
        if action == 'capture':
            app.test_dialogs.capture_target.insert(0, '受控窗口')
            app.test_dialogs.capture_start.invoke()
        else:
            app.test_dialogs.upload_host.set('01 beeimg')
            app.test_dialogs.upload_image.set(str(tmp_path / 'image.png'))
            app.test_dialogs.upload_start.invoke()
        request = app.test_session.active_request
        assert request is not None and request.action == action
        assert app.controller.runner.start.call_args.args[0] is request
        window = app.test_dialogs.result_window
        root.update()
        assert window.winfo_viewable()
        app.test_dialogs.hide_result()
        root.update()
        assert not window.winfo_viewable()
        assert app.test_session.active_request is request
        app.controller.runner.active = True
        button.invoke()
        root.update()
        assert app.test_dialogs.result_window is window
        assert window.winfo_viewable()
        assert app.test_session.active_request is request
        app.controller.runner.start.assert_called_once_with(request)
        app.test_dialogs.cancel_button.invoke()
        app.controller.runner.cancel.assert_called_once_with()
        assert app.test_session.active_request is request
        app.controller.runner.results.put(GuiTestResult(
            'other-request', request.document_generation, request.revision,
            'failure', '其他请求', ()))
        app.drain_results()
        assert app.test_session.active_request is request
        assert app.test_dialogs.result_label.cget('text') == '测试进行中'
        app.controller.runner.results.put(GuiTestResult(
            request.request_id, request.document_generation, request.revision,
            'failure', '测试已取消', ()))
        app.drain_results()
        assert app.test_session.active_request is request
        assert app.test_dialogs.result_label.cget('text') == '测试进行中'
        app.controller.runner.active = False
        app.drain_results()
        assert app.test_session.active_request is None
        assert app.test_dialogs.result_window is window
        assert '测试已取消' in app.test_dialogs.result_label.cget('text')
    finally:
        root.destroy()


@pytest.mark.parametrize('action, expected', [
    ('capture', ('normal', 'disabled', 'disabled')),
    ('upload', ('disabled', 'normal', 'disabled')),
    ('notify', ('disabled', 'disabled', 'disabled')),
])
@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancelled'])
def test_real_test_buttons_follow_worker_and_terminal_state(tmp_path, monkeypatch,
                                                            action, expected, outcome):
    """单 worker 运行、终态及回收时同步真实页面的三个测试按钮。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        buttons = (page.capture_button, page.upload_button,
                   app.visible_pages['消息推送设置'].notify_button)
        app.test_dialogs.show_result = Mock()
        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *args, **kwargs: True)
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', Mock())
        parameters = {'capture': {'capture_provider': 'window',
                                  'capture_target': '测试窗口'},
                      'upload': {'host_index': 0, 'image_path': str(tmp_path / 'image.png')},
                      'notify': {'template_key': 'on_end'}}
        assert app._request(action, **parameters[action])
        request = app.controller.runner.start.call_args.args[0]
        app.controller.runner.active = True
        assert tuple(button.cget('state') for button in buttons) == expected
        if action != 'notify':
            app.test_session.hide()
            buttons[0 if action == 'capture' else 1].invoke()
            assert app.test_dialogs.show_result.call_count == 2
            app.controller.runner.start.assert_called_once()
        else:
            assert buttons[2].cget('text') == '发送中…'
        app.controller.runner.results.put(GuiTestResult(
            request.request_id, request.document_generation, request.revision,
            'failure' if outcome == 'cancelled' else outcome,
            '测试已取消' if outcome == 'cancelled' else '', ()))
        app.drain_results()
        assert app.test_session.active_request is request
        assert tuple(button.cget('state') for button in buttons) == expected
        app.controller.runner.active = False
        app._poll()
        assert app.test_session.active_request is None
        assert tuple(button.cget('state') for button in buttons) == (
            'normal', 'normal', 'normal')
        assert buttons[2].cget('text') == '通知测试'
    finally:
        root.destroy()


@pytest.mark.parametrize('action', ['capture', 'upload', 'notify'])
def test_real_result_waits_for_runner_reclaim(tmp_path, monkeypatch, action):
    """真实 Tk 在结果入队但 worker 未回收时仍保持运行态。"""
    root = tk.Tk()
    release = threading.Event()
    worker = None
    try:
        app = make_app(root, tmp_path)
        runner = app.controller.runner
        queued = threading.Event()
        errors = Mock()
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', errors)
        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *args, **kwargs: True)
        page = app.visible_pages['截图设置']
        notify = app.visible_pages['消息推送设置'].notify_button
        if action == 'notify':
            notify.invoke()
        else:
            (page.capture_button if action == 'capture' else page.upload_button).invoke()
            dialogs = app.test_dialogs
            if action == 'capture':
                dialogs.capture_target.insert(0, '测试窗口')
                dialogs.capture_start.invoke()
            else:
                dialogs.upload_host.set(dialogs._host_names[0])
                dialogs.upload_image.set(str(tmp_path / 'image.png'))
                dialogs.upload_start.invoke()
        request = app.test_session.active_request
        assert request is not None
        runner.active = True

        def finish_after_cleanup():
            """模拟监督线程入队后等待清理屏障再释放运行槽位。"""
            runner.results.put(GuiTestResult(
                request.request_id, request.document_generation, request.revision,
                'failure', '测试失败', ()))
            queued.set()
            if release.wait(5):
                runner.active = False

        worker = threading.Thread(target=finish_after_cleanup)
        worker.start()
        assert queued.wait(5)
        assert runner.active
        app.drain_results()
        app._sync_test_buttons()
        assert app.test_session.active_request is request
        assert app.status.cget('text') == '测试进行中'
        assert notify.cget('text') == ('发送中…' if action == 'notify' else '通知测试')
        assert notify.cget('state') == 'disabled'
        if action != 'notify':
            assert dialogs.result_label.cget('text') == '测试进行中'
            assert not dialogs.retry.winfo_manager()
            assert dialogs.cancel_button.winfo_manager() == 'pack'
        errors.assert_not_called()

        release.set()
        worker.join(timeout=5)
        assert not runner.active
        app.drain_results()
        app._sync_test_buttons()
        assert app.test_session.active_request is None
        assert (page.capture_button.cget('state'), page.upload_button.cget('state'),
                notify.cget('state')) == ('normal', 'normal', 'normal')
        assert app.status.cget('text').startswith('failure:')
        if action == 'notify':
            errors.assert_called_once()
            assert notify.cget('text') == '通知测试'
        else:
            assert dialogs.retry.winfo_manager() == 'pack'
            assert not dialogs.cancel_button.winfo_manager()
    finally:
        release.set()
        if worker is not None:
            worker.join(timeout=5)
        root.destroy()


@pytest.mark.parametrize('action', ['capture', 'upload'])
@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancelled'])
def test_real_result_buttons_restore_without_resending(tmp_path, monkeypatch,
                                                       action, outcome):
    """真实结果窗在终态可恢复，重新测试只打开选择窗而不重复发请求。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *args, **kwargs: True)
        page = app.visible_pages['截图设置']
        button = page.capture_button if action == 'capture' else page.upload_button
        button.invoke()
        dialogs = app.test_dialogs
        if action == 'capture':
            dialogs.capture_target.insert(0, '测试窗口')
            dialogs.capture_start.invoke()
        else:
            dialogs.upload_host.set(dialogs._host_names[0])
            dialogs.upload_image.set(str(tmp_path / 'image.png'))
            dialogs.upload_start.invoke()
        request = app.test_session.active_request
        assert request is not None
        app.controller.runner.start.assert_called_once_with(request)
        dialogs.hide_result()
        app.controller.runner.results.put(GuiTestResult(
            request.request_id, request.document_generation, request.revision,
            'failure' if outcome == 'cancelled' else outcome,
            '测试已取消' if outcome == 'cancelled' else '测试失败', ()))
        app.drain_results()
        app.controller.runner.active = False
        app._sync_test_buttons()
        assert (page.capture_button.cget('state'), page.upload_button.cget('state'),
                app.visible_pages['消息推送设置'].notify_button.cget('state')) == (
                    'normal', 'normal', 'normal')
        button.invoke()
        assert dialogs.result_window.winfo_viewable()
        assert dialogs.retry.winfo_manager() == 'pack'
        assert not dialogs.cancel_button.winfo_manager()
        app.controller.runner.start.assert_called_once_with(request)
        dialogs.retry.invoke()
        assert dialogs.selection_window.winfo_exists()
        app.controller.runner.start.assert_called_once_with(request)
    finally:
        root.destroy()


@pytest.mark.parametrize('action', ['capture', 'upload', 'notify'])
def test_real_buttons_recover_after_start_failure(tmp_path, monkeypatch, action):
    """启动失败释放三个真实测试入口，后续点击可以重新发起选择。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *args, **kwargs: True)
        page = app.visible_pages['截图设置']
        notify_button = app.visible_pages['消息推送设置'].notify_button
        app.controller.runner.start.side_effect = RuntimeError('worker 无法启动')
        if action == 'notify':
            notify_button.invoke()
        else:
            (page.capture_button if action == 'capture' else page.upload_button).invoke()
            dialogs = app.test_dialogs
            if action == 'capture':
                dialogs.capture_target.insert(0, '测试窗口')
                dialogs.capture_start.invoke()
            else:
                dialogs.upload_host.set(dialogs._host_names[0])
                dialogs.upload_image.set(str(tmp_path / 'image.png'))
                dialogs.upload_start.invoke()
        assert app.controller.runner.start.call_count == 1
        assert app.test_session.active_request is None
        assert (page.capture_button.cget('state'), page.upload_button.cget('state'),
                notify_button.cget('state')) == ('normal', 'normal', 'normal')
        assert notify_button.cget('text') == '通知测试'
        if action != 'notify':
            assert dialogs.selection_window.winfo_exists()
            assert not dialogs.result_window or not dialogs.result_window.winfo_viewable()
            app.controller.runner.start.side_effect = None
            if action == 'capture':
                dialogs.capture_start.invoke()
            else:
                dialogs.upload_start.invoke()
            assert app.controller.runner.start.call_count == 2
            assert app.test_session.active_request is not None
        else:
            app.controller.runner.start.side_effect = None
            notify_button.invoke()
            assert app.controller.runner.start.call_count == 2
            assert app.test_session.active_request is not None
            assert app.test_dialogs.result_window is None
    finally:
        root.destroy()


@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancelled'])
def test_notify_terminal_has_no_reopen_or_independent_cancel(tmp_path, monkeypatch,
                                                             outcome):
    """通知结果只反馈终态，不创建结果窗或独立取消入口。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *args, **kwargs: True)
        errors = Mock()
        monkeypatch.setattr('modules.gui.app.messagebox.showerror', errors)
        button = app.visible_pages['消息推送设置'].notify_button
        button.invoke()
        request = app.test_session.active_request
        assert request is not None
        assert button.cget('state') == 'disabled'
        assert button.cget('text') == '发送中…'
        assert app.test_dialogs.result_window is None
        app.cancel_test()
        app.controller.runner.cancel.assert_not_called()
        app.controller.runner.results.put(GuiTestResult(
            request.request_id, request.document_generation, request.revision,
            'failure' if outcome == 'cancelled' else outcome,
            '测试已取消' if outcome == 'cancelled' else '发送失败', ()))
        app.drain_results()
        app.controller.runner.active = False
        app._sync_test_buttons()
        assert app.test_dialogs.result_window is None
        assert button.cget('state') == 'normal'
        assert button.cget('text') == '通知测试'
        assert app.status.cget('text') == app.result_text
        app.controller.runner.start.assert_called_once_with(request)
        assert errors.call_count == (0 if outcome == 'success' else 1)
        button.invoke()
        assert app.controller.runner.start.call_count == 2
        assert app.test_dialogs.result_window is None
    finally:
        root.destroy()


@pytest.mark.parametrize('action, lazy_page, expected', [
    ('notify', '截图设置', ('disabled', 'disabled')),
    ('capture', '消息推送设置', ('disabled', '通知测试')),
])
def test_lazy_test_page_created_during_worker_uses_current_busy_state(
        tmp_path, monkeypatch, action, lazy_page, expected):
    """worker 运行时首次打开真实懒加载页立即同步其测试入口。"""
    from modules.gui.app import App
    from modules.gui.controller import Controller

    root = tk.Tk()
    try:
        runner = Mock(active=False, results=queue.Queue())
        model = EditorModel(save_document(new_document(), tmp_path / 'a.yaml',
                                          expected_fingerprint=None))
        controller = Controller(model, confirm=lambda *_: 'cancel',
                                choose_path=lambda: tmp_path / 'a.yaml',
                                cli_command=('python.exe',), runner=runner,
                                program_dir=tmp_path)
        app = App(root, controller)
        assert lazy_page not in app.visible_pages
        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *args, **kwargs: True)
        parameters = ({'template_key': 'on_end'} if action == 'notify' else
                      {'capture_provider': 'window', 'capture_target': '测试窗口'})
        assert app._request(action, **parameters)
        runner.active = True
        assert app.show_page(lazy_page)
        if lazy_page == '截图设置':
            page = app.visible_pages[lazy_page]
            assert (page.capture_button.cget('state'),
                    page.upload_button.cget('state')) == expected
        else:
            button = app.visible_pages[lazy_page].notify_button
            assert (button.cget('state'), button.cget('text')) == expected
    finally:
        root.destroy()


@pytest.mark.parametrize('lazy_page', ['截图设置', '消息推送设置'])
def test_lazy_test_page_is_disabled_while_runner_active_without_request(
        tmp_path, lazy_page):
    """请求已清空但 worker 尚未回收时，首次进入真实页面仍禁用测试入口。"""
    from modules.gui.app import App
    from modules.gui.controller import Controller

    root = tk.Tk()
    try:
        runner = Mock(active=False, results=queue.Queue())
        model = EditorModel(save_document(new_document(), tmp_path / 'a.yaml',
                                          expected_fingerprint=None))
        controller = Controller(model, confirm=lambda *_: 'cancel',
                                choose_path=lambda: tmp_path / 'a.yaml',
                                cli_command=('python.exe',), runner=runner,
                                program_dir=tmp_path)
        app = App(root, controller)
        assert lazy_page not in app.visible_pages
        assert app.test_session.active_request is None
        runner.active = True

        assert app.show_page(lazy_page)
        page = app.visible_pages[lazy_page]
        if lazy_page == '截图设置':
            assert (page.capture_button.cget('state'),
                    page.upload_button.cget('state')) == ('disabled', 'disabled')
        else:
            assert (page.notify_button.cget('state'),
                    page.notify_button.cget('text')) == ('disabled', '通知测试')
    finally:
        root.destroy()


def test_failed_worker_start_restores_real_test_buttons(tmp_path, monkeypatch):
    """启动失败后真实截图、上传及通知入口均可再次使用。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        app.controller.runner.start.side_effect = RuntimeError('启动失败')
        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *args, **kwargs: True)
        assert not app._request('notify')
        assert tuple(button.cget('state') for button in (
            page.capture_button, page.upload_button,
            app.visible_pages['消息推送设置'].notify_button)) == (
                'normal', 'normal', 'normal')
    finally:
        root.destroy()


@pytest.mark.parametrize('answer, expected', [('save', False), ('discard', True)])
def test_incomplete_visible_input_save_or_discard(tmp_path, answer, expected):
    """未完成的可见输入不能假装保存成功；放弃后仍经最终退出确认。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        page.days.number.delete(0, 'end')
        page.days.number.insert(0, '-')
        app.controller.confirm = Mock(side_effect=lambda kind, _: 'confirm' if kind == 'exit' else answer)
        assert app.controller.close() is expected
        if expected:
            app.controller.runner.close.assert_called_once()
        else:
            app.controller.runner.close.assert_not_called()
            assert page.days.number.get() == '-'
    finally:
        root.destroy()


def test_real_gui_reorder_save_confirm_and_launch(tmp_path, monkeypatch):
    """真实页面交换图床，保存后确认规范化并创建 CLI 进程。"""
    from modules.config_document import read_document
    from modules.gui.launcher import LaunchResult

    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        app.visible_pages['监视设置'].set_field(('monitor', 'target'),
                                             str(tmp_path / 'demo.exe'))
        path = ('push', 'screenshot', 'image_host')
        hosts = app.model.get(path)
        hosts[0].update({'custom': 'first', 'options': {'group': 'primary'},
                         'expiration': 7})
        hosts.append({'provider': 'boltp', 'token': 'secret',
                      'options': {'group': 'backup'}, 'expiration': 30,
                      'custom': 'second'})
        expected = [dict(host) for host in reversed(hosts)]
        app.model.set(path, hosts)
        page.refresh()
        assert app.controller.save(tmp_path / 'reordered.yaml')
        page.move_host(0, 1)
        assert app.model.get(path) == expected
        assert app.controller.save()
        confirmations = []

        def confirm(kind, detail):
            """记录真实控制器的规范化确认并接受。"""
            confirmations.append(kind)
            return 'confirm'

        launch = Mock(return_value=LaunchResult(True, '已创建 CLI 进程'))
        app.controller.confirm = confirm
        monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
        app.start_cli()
        assert 'normalize' in confirmations
        assert launch.call_count == 1, (app.status.cget('text'),
                                        [(change.path, change.operation) for change in app.model.validate(tmp_path).changes],
                                        app.model.document.tree == read_document(tmp_path / 'reordered.yaml').tree)
        assert launch.call_args.args[1] == (tmp_path / 'reordered.yaml').resolve()
        disk = read_document(tmp_path / 'reordered.yaml').tree['push']['screenshot']['image_host']
        assert disk == expected
        assert app.model.validate(tmp_path).changes == ()
    finally:
        root.destroy()


def test_request_with_non_json_yaml_value_is_rejected_before_worker(tmp_path):
    """不兼容 JSON 的 YAML 值不进入 worker，且主线程保留草稿。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        config = app.model.get(('push', 'screenshot', 'image_host'))
        config[0]['options'] = {1: object()}
        app.model.set(('push', 'screenshot', 'image_host'), config)
        app.visible_pages['截图设置'].add_target()
        app.capture()
        app.controller.runner.start.assert_not_called()
        assert not app.model.get(('push', 'screenshot', 'targets'))
        assert app.model.dirty
    finally:
        root.destroy()


def test_relocation_confirmation_does_not_show_private_target(tmp_path):
    """跨目录确认仅显示解析后路径，不打印原始凭证字段。"""
    from modules.config_document import save_document

    source = tmp_path / 'source'
    target = tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        saved = save_document(new_document(), source / 'a.yaml', expected_fingerprint=None)
        app.model.replace_document(saved)
        app.model.set(('monitor', 'target'), 'demo.exe')
        app.controller.confirm = Mock(return_value='cancel')
        assert not app.controller.save(target / 'b.yaml')
        assert app.controller.confirm.call_args.args[1]['to'] == str(target)
    finally:
        root.destroy()


def test_capture_request_requires_selected_target(tmp_path):
    """截图测试必须在选择弹窗填写窗口标题或 ADB 地址。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.test_dialogs.open_capture = Mock()
        app.capture()
        app.controller.runner.start.assert_not_called()
        app.test_dialogs.open_capture.assert_called_once()
    finally:
        root.destroy()


def test_capture_session_is_not_written_next_to_program(tmp_path):
    """冻结程序目录可能只读，预览应使用系统临时会话。"""
    import tempfile
    from pathlib import Path

    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.test_dialogs.open_capture = Mock()
        app.test_dialogs.show_result = Mock()
        app.visible_pages['截图设置'].add_target()
        app.visible_pages['截图设置']._set_item(('push', 'screenshot', 'targets'), 0, 'target', '测试窗口')
        app.capture()
        assert app.test_dialogs.open_capture.call_args.args[0]('window', '测试窗口')
        session = Path(app.controller.runner.start.call_args.args[0].session_dir)
        assert session.is_relative_to(Path(tempfile.gettempdir()))
        assert not session.is_relative_to(tmp_path)
    finally:
        root.destroy()


def test_missing_common_mapping_is_editable_and_created_on_save(tmp_path):
    """monitor.common 缺失属于可自动创建的映射，不应禁用字段。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        source = tmp_path / 'missing-common.yaml'
        source.write_text('monitor:\n  mode: watch\n  target: demo.exe\n'
                          'push: {}\nlog: {}\nexternal: {}\n', encoding='utf-8')
        assert app.controller.open(source)
        assert app.show_page('监视设置')
        page = app.visible_pages['监视设置']
        path = ('monitor', 'common', 'loop_interval')
        widget = page.fields[path][1]
        assert widget.cget('state') == 'normal'
        widget.insert(0, '3s')
        assert app.model.get(path) == '3s'
        assert app.controller.save()
        assert read_document(source).tree['monitor']['common']['loop_interval'] == '3s'
    finally:
        root.destroy()
