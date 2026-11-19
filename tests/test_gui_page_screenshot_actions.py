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
        assert tuple(button.cget('state') for button in buttons) == (
            'disabled', 'disabled', 'disabled')
        app.controller.runner.active = False
        app._poll()
        assert tuple(button.cget('state') for button in buttons) == (
            'normal', 'normal', 'normal')
        assert buttons[2].cget('text') == '通知测试'
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
