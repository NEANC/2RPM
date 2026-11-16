#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""真实 Tk 页面折叠、定位及结果消费测试。"""

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


@pytest.fixture(autouse=True)
def _fresh_default_root():
    """每个用例前解除已销毁的 Tk 默认根，使新建窗口接管字体资源。"""
    tk._default_root = None


def make_app(root, tmp_path):
    """使用真实 Tk 与无进程运行器搭建界面。"""
    from modules.gui.app import App
    from modules.gui.controller import Controller

    runner = Mock()
    runner.results = queue.Queue()
    runner.active = False
    tmp_path.mkdir(parents=True, exist_ok=True)
    model = EditorModel(save_document(new_document(), tmp_path / 'a.yaml',
                                      expected_fingerprint=None))
    controller = Controller(model, confirm=lambda kind, _: 'confirm' if kind == 'exit' else 'cancel',
                            choose_path=lambda: tmp_path / 'a.yaml', cli_command=('python.exe',),
                            runner=runner, program_dir=tmp_path)
    app = App(root, controller)
    for name in ('监视设置', '消息推送设置', '截图设置', '日志设置'):
        app.sidebar.page(name)
    return app


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


def test_list_operations_persist_immediately(tmp_path, monkeypatch):
    """通知通道、截图目标和图床列表操作立即补存。"""
    from modules.config_document import read_document

    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = app.model.document.path
        channels = app.visible_pages['消息推送设置']
        attachments = app.visible_pages['截图设置']
        channels.add_channel('telegram')
        assert len(read_document(path).tree['push']['push_channel_settings']['channels']) == len(app.model.get(channels.channels_path))
        monkeypatch.setattr('modules.gui.pages.notification_ctk.messagebox.askyesno', lambda *a, **k: True)
        channels.remove_channel(len(app.model.get(channels.channels_path)) - 1)
        assert len(read_document(path).tree['push']['push_channel_settings']['channels']) == len(app.model.get(channels.channels_path))
        attachments.add_target()
        assert len(read_document(path).tree['push']['screenshot']['targets']) == len(app.model.get(('push', 'screenshot', 'targets')))
        attachments.add_host()
        assert len(read_document(path).tree['push']['screenshot']['image_host']) == len(app.model.get(('push', 'screenshot', 'image_host')))
    finally:
        root.destroy()


def test_list_edit_presave_failure_updates_status(tmp_path):
    """补存失败时截图与通知列表操作提示先保存或处理冲突。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        screenshot = app.visible_pages['截图设置']
        channels = app.visible_pages['消息推送设置']
        app.model.set_input(('monitor', 'common', 'timeout_threshold'), '-', kind='int')
        before = app.model.get(('push', 'screenshot', 'targets'))
        screenshot.add_target()
        assert '配置未保存' in app.status.cget('text')
        assert app.model.get(('push', 'screenshot', 'targets')) == before
        channels.add_channel('telegram')
        assert '配置未保存' in app.status.cget('text')
        assert all(item.get('provider') != 'telegram'
                   for item in app.model.get(channels.channels_path))
    finally:
        root.destroy()


@pytest.mark.parametrize('operation', ['channel_add', 'channel_remove', 'target_add',
                                       'target_remove', 'host_add', 'host_remove', 'host_move'])
def test_list_entry_presaves_before_mutation(tmp_path, operation, monkeypatch):
    """真实列表入口遇到外部冲突时保留旧列表和待保存草稿。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        channels = app.visible_pages['消息推送设置']
        attachments = app.visible_pages['截图设置']
        targets_path = ('push', 'screenshot', 'targets')
        hosts_path = ('push', 'screenshot', 'image_host')
        if operation == 'host_move':
            attachments.add_host()
        if operation == 'target_remove':
            attachments.add_target()
        monkeypatch.setattr('modules.gui.pages.notification_ctk.messagebox.askyesno',
                            lambda *a, **k: True)
        monkeypatch.setattr('modules.gui.pages.screenshot.messagebox.askyesno',
                            lambda *a, **k: True)
        list_path, action = {
            'channel_add': (channels.channels_path, lambda: channels.add_channel('telegram')),
            'channel_remove': (channels.channels_path, lambda: channels.remove_channel(0)),
            'target_add': (targets_path, attachments.add_target),
            'target_remove': (targets_path, lambda: attachments.remove_target(0)),
            'host_add': (hosts_path, attachments.add_host),
            'host_remove': (hosts_path, lambda: attachments.remove_host(0)),
            'host_move': (hosts_path, lambda: attachments.move_host(0, 1)),
        }[operation]
        path = app.model.document.path
        before = app.model.get(list_path)
        app.model.set(('monitor', 'target'), 'draft.exe')
        app.controller.on_conflict = lambda: None
        path.write_bytes(b'external: true\n')

        action()

        assert app.model.get(list_path) == before
        assert app.model.get(('monitor', 'target')) == 'draft.exe'
        assert app.controller.conflicted
        assert path.read_bytes() == b'external: true\n'
    finally:
        root.destroy()


@pytest.mark.parametrize('operation', ['channel_remove', 'target_remove',
                                       'host_remove', 'host_move'])
def test_list_entry_reselects_after_clean_draft_external_reload(tmp_path, operation, monkeypatch):
    """干净草稿补存自动重载后，不沿用旧列表选择执行删除或重排。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        channels = app.visible_pages['消息推送设置']
        attachments = app.visible_pages['截图设置']
        targets_path = ('push', 'screenshot', 'targets')
        hosts_path = ('push', 'screenshot', 'image_host')
        if operation == 'target_remove':
            attachments.add_target()
        if operation == 'host_move':
            attachments.add_host()
        monkeypatch.setattr('modules.gui.pages.notification_ctk.messagebox.askyesno',
                            lambda *a, **k: True)
        monkeypatch.setattr('modules.gui.pages.screenshot.messagebox.askyesno',
                            lambda *a, **k: True)
        list_path, action = {
            'channel_remove': (channels.channels_path, lambda: channels.remove_channel(0)),
            'target_remove': (targets_path, lambda: attachments.remove_target(0)),
            'host_remove': (hosts_path, lambda: attachments.remove_host(0)),
            'host_move': (hosts_path, lambda: attachments.move_host(0, 1)),
        }[operation]
        path = app.model.document.path
        disk = read_document(path)
        items = disk.tree
        for key in list_path:
            items = items[key]
        items.insert(0, {'provider': 'external', 'token': 'external'})
        expected = [dict(item) for item in items]
        save_document(disk, path, expected_fingerprint=disk.fingerprint)
        generation = app.model.document_generation

        action()

        assert '配置已更新，请重新选择列表项' in app.status.cget('text')
        assert app.model.document_generation == generation + 1
        assert app.model.get(list_path) == expected
        persisted = read_document(path).tree
        for key in list_path:
            persisted = persisted[key]
        assert persisted == expected
        assert not app.model.dirty
        if operation == 'host_move':
            attachments.move_host(1, -1)
        elif operation == 'channel_remove':
            channels.remove_channel(1)
        elif operation == 'target_remove':
            attachments.remove_target(1)
        else:
            attachments.remove_host(1)
        assert app.model.get(list_path) != expected
    finally:
        root.destroy()


def test_document_reload_refresh_keeps_lists_unselected(tmp_path):
    """文档重载后的通道编辑折叠状态和截图行必须重建。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        channels = app.visible_pages['消息推送设置']
        attachments = app.visible_pages['截图设置']
        attachments.add_target()
        channels.expand_channel(0)
        old_entry = channels.channel_entries['sckey']
        old_target = attachments.target_rows[0]['number']
        path = app.model.document.path
        disk = read_document(path)
        disk.tree['monitor']['target'] = 'external.exe'
        save_document(disk, path, expected_fingerprint=disk.fingerprint)

        assert not app.controller.check_external()
        assert app.model.get(('monitor', 'target')) == 'external.exe'
        app.refresh()
        assert channels._expanded_channel is None
        assert channels.channel_entries == {}
        assert not old_entry.winfo_exists()
        assert not old_target.winfo_exists()
        assert len(attachments.target_rows) == 1
    finally:
        root.destroy()


@pytest.mark.parametrize('kind', ['channel', 'target', 'host'])
def test_reload_clears_list_editors_until_explicit_selection(tmp_path, kind):
    """重载后旧控件不能写入新文档，显式点击才创建新编辑控件。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        channels = app.visible_pages['消息推送设置']
        attachments = app.visible_pages['截图设置']
        attachments.add_target()
        paths = {'channel': channels.channels_path,
                 'target': ('push', 'screenshot', 'targets'),
                 'host': ('push', 'screenshot', 'image_host')}
        path = paths[kind]
        field = {'channel': 'sckey', 'target': 'target', 'host': 'token'}[kind]
        if kind == 'channel':
            channels.expand_channel(0)
            old_entry = channels.channel_entries[field]
        elif kind == 'target':
            old_entry = attachments.target_rows[0]['entry']
        else:
            old_entry = attachments.host_rows[0]['provider']
        disk = read_document(app.model.document.path)
        node = disk.tree
        for key in path:
            node = node[key]
        node[0][field] = 'external-value'
        save_document(disk, disk.path, expected_fingerprint=disk.fingerprint)

        assert not app.controller.check_external()
        assert not old_entry.winfo_exists()
        assert app.model.get(path)[0][field] == 'external-value'
        if kind == 'channel':
            assert channels.channel_entries == {}
            channels.expand_channel(0)
            assert channels.channel_entries[field].get() == 'external-value'
        else:
            assert len(attachments.target_rows if kind == 'target'
                       else attachments.host_rows) == len(app.model.get(path))
    finally:
        root.destroy()


def test_pages_bind_variables_without_default_root(tmp_path, monkeypatch):
    """业务页面变量归属当前 Tk 根，不依赖外部默认根。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.visible_pages['监视设置'].set_field(('monitor', 'target'), 'demo.exe')
        app.visible_pages['监视设置'].set_field(('monitor', 'common', 'loop_interval'), '2s')
        app.visible_pages['消息推送设置'].expand_channel(0)
        app.visible_pages['消息推送设置'].channel_entries['sckey'].insert('end', 'x')
        host_entry = next(widget for widget in app.visible_pages['截图设置'].hosts_body.winfo_children()[0].winfo_children()
                          if isinstance(widget, ctk_kit.CTkEntry))
        host_entry.insert('end', 'x')
        assert app.model.get(('monitor', 'target')) == 'demo.exe'
        assert app.model.get(app.visible_pages['消息推送设置'].channels_path)[0]['sckey'].endswith('x')
        assert app.model.get(('push', 'screenshot', 'image_host'))[0]['token'].endswith('x')
    finally:
        root.destroy()


def test_appearance_controls_apply_and_persist_without_replacing_pages(tmp_path, monkeypatch):
    """外观选择更新 ctk 窗口并持久化，现有 ttk 页面仍可使用。"""
    import ctk_kit

    from modules.gui.appearance import AppearancePreferences, load_preferences

    path = tmp_path / 'gui.ini'
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: path)
    root = ctk_kit.App(title='测试', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        assert app.appearance.dark is None
        assert app.appearance.material == 'Mica'
        assert not hasattr(app, 'notebook')
        assert app.show_page('设置')
        settings_page = app.visible_pages['设置']
        settings_page.theme.select()
        settings_page._change_theme()
        settings_page.material.set('无材质')
        settings_page._change_material('无材质')
        assert app.show_page('监视设置')
        assert app.visible_pages['监视设置'].fields[('monitor', 'target')][1].winfo_exists()
        assert load_preferences(path) == AppearancePreferences(dark=True, material='none')
        assert ctk_kit.get_appearance_mode() == 'Dark'
    finally:
        root.destroy()


def test_error_expands_and_focuses(tmp_path):
    """高级字段出错自动展开所在区域并将焦点移至控件。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['监视设置']
        path = ('monitor', 'common', 'loop_interval')
        page.section_for_issue.set_expanded(False)
        assert app.show_page('监视设置')
        page.focus_issue(path)
        root.update()
        assert page.section_for_issue.expanded is True
        assert root.focus_get() is page.widget_for_issue
    finally:
        root.destroy()


def test_stale_generation_result_is_dropped(tmp_path):
    """打开另一文档后丢弃旧代次结果且不清除新请求忙态。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.test_session.active_request = GuiTestRequest(
            'old', app.model.document_generation, 0, 'capture', {}, str(tmp_path),
            str(app.session_dir), 0, None, None, None)
        app.controller.new()
        app.controller.runner.active = True
        app.controller.runner.results.put(GuiTestResult('old', 0, 0, 'success', '旧结果', ()))
        app.drain_results()
        assert '旧结果' not in app.result_text
        assert app.controller.runner.active
    finally:
        root.destroy()


def test_same_generation_stale_revision_is_labelled(tmp_path):
    """同文档旧草稿结果只标示旧草稿，非最近请求直接忽略。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.test_dialogs.show_result = Mock()
        app.test_session.active_request = GuiTestRequest('current', 0, 0, 'capture', {},
                                                        str(tmp_path), str(app.session_dir),
                                                        0, None, None, None)
        app.controller.model.set(('monitor', 'target'), 'changed.exe')
        app.controller.runner.results.put(GuiTestResult('older', 0, 0, 'success', '较旧', ()))
        app.controller.runner.results.put(GuiTestResult('current', 0, 0, 'success', '完成', ()))
        app.drain_results()
        assert '较旧' not in app.result_text
        assert '对应先前草稿' in app.result_text
    finally:
        root.destroy()


def test_fold_keeps_nondefault_value_and_required_fields(tmp_path):
    """高级折叠保持值，基础必填目标一直显示。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['监视设置']
        advanced = ('monitor', 'common', 'loop_interval')
        target = ('monitor', 'target')
        page.set_field(advanced, '3s')
        before = app.model.revision
        page.section_for_issue.set_expanded(False)
        root.update()
        assert app.model.get(advanced) == '3s'
        assert app.model.revision == before
        assert page.fields[target][1].winfo_ismapped()
        assert 'YAML' in page.section_for_issue.button.cget('text')
    finally:
        root.destroy()


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


def test_monitor_preview_resolves_relative_target_and_cwd(tmp_path):
    """文件预览采用当前配置目录及显式工作目录。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.switch_mode('launch')
        app.model.set(('monitor', 'target'), '.\\demo.exe')
        app.model.set(('monitor', 'launch', 'cwd'), '.\\work')
        app.visible_pages['监视设置'].refresh()
        text = app.visible_pages['监视设置'].preview.cget('text')
        assert 'demo.exe' in text
        assert 'work' in text
        assert '待校验' not in text
    finally:
        root.destroy()


def test_unedited_host_options_keep_mapping_type(tmp_path):
    """图床选项原有映射经过控件编辑不能被字符串覆盖。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        hosts = app.model.get(('push', 'screenshot', 'image_host'))
        hosts[0]['options'] = {'album_id': 12}
        app.model.set(('push', 'screenshot', 'image_host'), hosts)
        page.refresh()
        app.model.set(('push', 'screenshot', 'image_host'),
                      app.model.get(('push', 'screenshot', 'image_host')))
        assert isinstance(app.model.get(('push', 'screenshot', 'image_host'))[0]['options'], dict)
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


def test_host_options_edit_stays_mapping(tmp_path):
    """可见图床高级选项输入应解析为映射，不写入原始字符串。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        options = page.host_rows[0]['options']
        options.delete(0, 'end')
        options.insert(0, '{album_id: 12}')
        assert app.model.get(('push', 'screenshot', 'image_host'))[0]['options'] == {'album_id': 12}
    finally:
        root.destroy()


def test_host_options_display_roundtrips_and_invalid_input_blocks_save(tmp_path):
    """真实输入框显示可解析映射，非法值显示错误且不能被保存吞掉。"""
    from modules.config_document import read_document

    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        path = ('push', 'screenshot', 'image_host')
        hosts = app.model.get(path)
        hosts[0]['options'] = {'album_id': 12, 'enabled': False}
        app.model.set(path, hosts)
        page.refresh()
        entry = page.host_rows[0]['options']
        assert entry.get() == '{album_id: 12, enabled: false}'
        entry.delete(0, 'end')
        entry.insert(0, '{album_id: 13}')
        assert app.model.get(path)[0]['options'] == {'album_id': 13}
        entry.delete(0, 'end')
        entry.insert(0, '{album_id: [}')
        assert app.model.get(path)[0]['options'] == {}
        assert 'options' in page.host_rows[0]['error'].cget('text')
        assert any(issue.path == (*path, 0, 'options')
                   for issue in app.model.validate(tmp_path).issues)
        assert not app.controller.save(tmp_path / 'invalid.yaml')
        assert not (tmp_path / 'invalid.yaml').exists()
        page.refresh()
        assert page.host_rows[0]['options'].get() == '{album_id: [}'
        page.host_rows[0]['options'].delete(0, 'end')
        page.host_rows[0]['options'].insert(0, '{album_id: 14}')
        assert not page.host_rows[0]['error'].cget('text')
        assert app.controller.save(tmp_path / 'valid.yaml')
        assert read_document(tmp_path / 'valid.yaml').tree['push']['screenshot']['image_host'][0]['options'] == {'album_id': 14}
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
        app.visible_pages['监视设置'].set_field(('monitor', 'target'), 'demo.exe')
        path = ('push', 'screenshot', 'image_host')
        hosts = app.model.get(path)
        hosts[0]['custom'] = 'first'
        hosts.append({'provider': 'boltp', 'token': 'secret', 'custom': 'second'})
        app.model.set(path, hosts)
        page.refresh()
        assert app.controller.save(tmp_path / 'reordered.yaml')
        page.move_host(0, 1)
        assert app.model.get(path)[1]['custom'] == 'first'
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
        assert [item['custom'] for item in disk] == ['second', 'first']
        assert app.model.validate(tmp_path).changes == ()
    finally:
        root.destroy()


def test_invalid_options_block_host_reorder(tmp_path):
    """图床选项有非法草稿时不移动条目，也不改变错误归属。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        page.add_host()
        entry = page.host_rows[0]['options']
        entry.delete(0, 'end')
        entry.insert(0, '{broken: [}')
        before = app.model.get(('push', 'screenshot', 'image_host'))
        page.move_host(0, 1)
        assert page.host_rows[0]['options'].get() == '{broken: [}'
        assert app.model.get(('push', 'screenshot', 'image_host')) == before
        assert any(issue.path == ('push', 'screenshot', 'image_host', 0, 'options')
                   for issue in app.model.validate(tmp_path).issues)
        assert not app.controller.save(tmp_path / 'invalid.yaml')
    finally:
        root.destroy()


@pytest.mark.parametrize('action', ['new', 'open', 'close'])
def test_invalid_options_on_clean_model_requires_discard_confirmation(tmp_path, action):
    """真实 Tk 非法输入未改变模型时，离开文档仍要求确认。"""
    from modules.config_document import save_document

    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        destination = tmp_path / 'another.yaml'
        if action == 'open':
            save_document(new_document(), destination, expected_fingerprint=None)
        page.host_rows[0]['options'].insert('end', '{broken: [}')
        assert not app.model.dirty
        assert page.host_rows[0]['options'].get() == '{}{broken: [}'
        generation = app.model.document_generation
        confirm = Mock(return_value='cancel')
        app.controller.confirm = confirm

        result = app.controller.open(destination) if action == 'open' else getattr(app.controller, action)()

        assert result is False
        confirm.assert_called_once_with('unsaved', app.model.document.path)
        assert app.model.document_generation == generation
        assert page.host_rows[0]['options'].get() == '{}{broken: [}'
        if action == 'close':
            app.controller.runner.close.assert_not_called()
    finally:
        root.destroy()


@pytest.mark.parametrize('answer, expected', [('save', False), ('discard', True)])
def test_invalid_options_on_clean_model_save_or_discard(tmp_path, answer, expected):
    """非法控件值不能假装保存成功，放弃后仍需最终退出确认。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        page.host_rows[0]['options'].delete(0, 'end')
        page.host_rows[0]['options'].insert(0, '{broken: [}')
        app.controller.confirm = Mock(side_effect=lambda kind, _: 'confirm' if kind == 'exit' else answer)
        assert app.controller.close() is expected
        if expected:
            app.controller.runner.close.assert_called_once()
        else:
            app.controller.runner.close.assert_not_called()
            assert page.host_rows[0]['options'].get() == '{broken: [}'
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
        assert 'SCTxxxx' not in app.summary_text
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


def test_notification_event_preview_and_variable_controls_are_visible(tmp_path):
    """通知页须可见预览及插入变量入口，而不依赖内部方法调用。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['消息推送设置']
        page.expand_event('on_end')
        texts = [child.cget('text') for card in page.events_body.winfo_children()
                 for row in card.winfo_children() for child in row.winfo_children()
                 if isinstance(child, ctk_kit.CTkButton)]
        assert '预览消息' in texts
        assert '插入正文' in texts
    finally:
        root.destroy()


def test_channel_required_secret_is_visible_outside_advanced_section(tmp_path):
    """选定通道的必填凭证可编辑、默认遮挡且状态随输入更新。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['消息推送设置']
        page.expand_channel(0)
        entry = page.channel_entries['sckey']
        assert entry.cget('show') == '●'
        assert entry.winfo_exists()
        assert page.channel_rows[0]['status'].cget('text') == '已配置'
        entry.delete(0, 'end')
        assert page.channel_rows[0]['status'].cget('text') == '需要填写参数'
        entry.insert(0, 'private-sckey')
        assert page.channel_rows[0]['status'].cget('text') == '已配置'
    finally:
        root.destroy()


def test_channel_edit_survives_selection_and_refresh(tmp_path):
    """切换通道与刷新不会丢失输入或把第一个通道的值写到第二个。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['消息推送设置']
        app.model.set(page.channels_path, [{'provider': 'serverchan', 'sckey': ''},
                                           {'provider': 'serverchan', 'sckey': ''}])
        page.refresh()
        page.expand_channel(0)
        page.channel_entries['sckey'].insert(0, 'first-secret')
        page.expand_channel(1)
        page.channel_entries['sckey'].insert(0, 'second-secret')
        page.refresh()
        assert page._expanded_channel == 1
        channels = app.model.get(page.channels_path)
        assert channels[0]['sckey'] == 'first-secret'
        assert channels[1]['sckey'] == 'second-secret'
    finally:
        root.destroy()


def test_provider_edit_refresh_keeps_selected_channel(tmp_path):
    """第二个通道经可见选择器改 provider 后刷新仍显示第二项。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['消息推送设置']
        app.model.set(page.channels_path, [{'provider': 'serverchan', 'sckey': 'first'},
                                           {'provider': 'serverchan', 'sckey': 'second'}])
        page.refresh()
        page.expand_channel(1)
        page.change_channel_provider(1, 'bark')
        page.refresh()
        assert page._expanded_channel == 1
        assert page.channel_rows[1]['provider'].get() == 'bark'
        assert app.model.get(page.channels_path)[1]['provider'] == 'bark'
        page.channel_entries['key'].insert(0, 'private-key')
        assert app.model.get(page.channels_path)[1]['key'] == 'private-key'
    finally:
        root.destroy()


def test_host_edit_refresh_keeps_selected_upload_host(tmp_path, monkeypatch):
    """编辑第二个图床后刷新和上传仍使用第二个槽位。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.test_dialogs.open_upload = Mock()
        app.test_dialogs.show_result = Mock()
        page = app.visible_pages['截图设置']
        page.add_host()
        page.host_rows[1]['provider'].set('superbed')
        page._set_item(('push', 'screenshot', 'image_host'), 1, 'provider', 'superbed')
        page.refresh()
        assert page.host_rows[1]['provider'].get() == 'superbed'
        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *_, **__: True)
        monkeypatch.setattr('modules.gui.app.filedialog.askopenfilename',
                            lambda **_: str(tmp_path / 'image.png'))
        app.upload()
        _, submit = app.test_dialogs.open_upload.call_args.args
        assert submit(1, str(tmp_path / 'image.png'))
        assert app.controller.runner.start.call_args.args[0].host_index == 1
    finally:
        root.destroy()


def test_host_options_credentials_are_masked(tmp_path):
    """图床 options 中的凭证不在输入框默认状态下明文显示。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        path = ('push', 'screenshot', 'image_host')
        hosts = app.model.get(path)
        hosts[0]['options'] = {'api_key': 'demo'}
        app.model.set(path, hosts)
        page.refresh()
        options = page.host_rows[0]['options']
        assert options.cget('show') == '●'
        toggle = page.host_rows[0]['options_toggle']
        toggle._command()
        assert options.cget('show') == ''
        assert 'api_key' in options.get()
        toggle._command()
        assert options.cget('show') == '●'
    finally:
        root.destroy()


def test_request_uses_selected_event_and_host(tmp_path, monkeypatch):
    """通知固定消息与上传独立选择图床，未选择时不发送。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *_, **__: True)
        app.notify()
        assert app.controller.runner.start.call_args.args[0].template_key == 'on_end'
        app.test_session.active_request = None
        app.controller.runner.start.reset_mock()
        app.visible_pages['截图设置'].add_host()
        app.test_dialogs.open_upload = Mock()
        app.test_dialogs.show_result = Mock()
        app.upload()
        names, submit = app.test_dialogs.open_upload.call_args.args
        assert len(names) == 2
        app.controller.runner.start.assert_not_called()
        assert submit(1, str(tmp_path / 'image.png'))
        assert app.controller.runner.start.call_args.args[0].host_index == 1
    finally:
        root.destroy()


def test_advanced_summary_never_contains_secret(tmp_path):
    """折叠标签只显示安全字段名称，不能显示凭证原文。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('push', 'retry', 'interval'), 'private-98765')
        app.refresh()
        label = app.visible_pages['主页'].summary.cget('text')
        assert '已配置' in label
        assert 'private-98765' not in label
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


def test_normal_close_removes_unused_preview_session(tmp_path):
    """未发测试的会话目录也应在正常关闭时清理。"""
    root = tk.Tk()
    app = make_app(root, tmp_path)
    session = app.session_dir
    app.close()
    import time
    for _ in range(100):
        if not session.exists():
            break
        time.sleep(0.01)
    assert not session.exists()


def test_review_summary_does_not_echo_monitor_target_secret(tmp_path):
    """摘要不会把用户可能粘贴在目标字段中的敏感字符串回显。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('monitor', 'target'), 'private-98765.exe')
        app.refresh()
        assert 'private-98765' not in app.summary_text
    finally:
        root.destroy()


def test_error_focuses_advanced_field_without_changing_model_revision(tmp_path):
    """错误展开折叠与焦点切换均不属于草稿编辑。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['监视设置']
        before = app.model.revision
        page.focus_issue(('monitor', 'common', 'loop_interval'))
        root.update_idletasks()
        assert app.model.revision == before
    finally:
        root.destroy()


def test_validate_shows_issue_count_and_navigates_to_field(tmp_path):
    """问题数量及首项错误定位须在可见页面出现。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('monitor', 'mode'), 'invalid')
        candidate = app.validate()
        assert any(issue.severity == 'error' for issue in candidate.issues)
        assert '1 个问题' in app.visible_pages['主页'].check.cget('text')
        assert app.current_page == '监视设置'
    finally:
        root.destroy()


def test_six_image_hosts_edit_and_reorder(tmp_path):
    """六个已支持图床的字段输入及上下移动保持完整槽位。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        path = ('push', 'screenshot', 'image_host')
        for provider in ('wmimg', 'beeimg', 'beeimg_cn', 'boltp', 'superbed'):
            page.add_host()
            page._set_item(path, len(app.model.get(path)) - 1, 'provider', provider)
            page.refresh()
        assert [item['provider'] for item in app.model.get(path)] == [
            'catbox', 'wmimg', 'beeimg', 'beeimg_cn', 'boltp', 'superbed']
        row = page.host_rows[3]
        row['token'].insert(0, 'private-token')
        row['expiration'].insert(0, '7d')
        row['options'].delete(0, 'end')
        row['options'].insert(0, '{storage_id: 12}')
        page.move_host(3, -1)
        assert app.model.get(path)[2] == {
            'provider': 'beeimg_cn', 'token': 'private-token',
            'expiration': '7d', 'options': {'storage_id': 12}}
        page.move_host(2, 1)
        assert page.host_rows[3]['provider'].get() == 'beeimg_cn'
        page.move_host(0, -1)
        assert page.host_rows[0]['provider'].get() == 'catbox'
    finally:
        root.destroy()


def test_external_action_disable_restore_is_memory_only(tmp_path):
    """清空动作路径停用后可恢复，替换文档后旧草稿不再可选。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.show_page('外部程序设置')
        page = app.visible_pages['外部程序设置']
        path = ('external', 'on_end')
        page.fields[path][1].delete(0, 'end')
        page.fields[path][1].insert(0, 'C:\\task.bat')
        page.clear_file('on_end')
        assert app.model.get(path) == ''
        assert page.fields[path][0].get() == ''
        assert page.restore_buttons['on_end'].cget('state') == 'normal'
        page.restore_file('on_end')
        assert app.model.get(path) == 'C:\\task.bat'
        assert page.fields[path][1].winfo_exists()
        assert page.restore_buttons['on_end'].cget('state') == 'disabled'
        page.clear_file('on_end')
        app.model.replace_document(new_document())
        page.refresh()
        page.restore_file('on_end')
        assert app.model.get(path) != 'C:\\task.bat'
        assert page.restore_buttons['on_end'].cget('state') == 'disabled'
    finally:
        root.destroy()


def test_each_event_displays_screenshot_conditions_and_explicit_reference(tmp_path):
    """四事件独立显示截图生效条件，插入引用只响应显式操作。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['主页']
        for event in ('on_end', 'on_timeout', 'on_wait_timeout', 'on_external'):
            before = app.model.get(('push', 'templates', event, 'content'))
            page.refresh()
            assert app.model.get(('push', 'templates', event, 'content')) == before
        app.visible_pages['消息推送设置'].insert_variable('on_end', 'screenshot')
        page.refresh()
        assert screenshot_conditions(app.model.snapshot(), 'on_end')['references']
        assert '目标结束' in page.conditions.cget('text')
    finally:
        root.destroy()


def test_dynamic_and_static_issue_paths_focus_exact_field(tmp_path):
    """动态列表及静态字段诊断均可导航到相应页面。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(app.visible_pages['消息推送设置'].channels_path, [
            {'provider': 'serverchan', 'sckey': ''}] * 2)
        app.visible_pages['截图设置'].add_host()
        app.visible_pages['截图设置'].add_target()
        app.visible_pages['截图设置'].add_target()
        cases = [
            ('监视设置', ('monitor', 'launch', 'cwd')),
            ('消息推送设置', ('push', 'templates', 'on_external', 'content')),
            ('消息推送设置', ('push', 'push_channel_settings', 'channels', 1, 'sckey')),
            ('截图设置', ('push', 'screenshot', 'image_host', 1, 'token')),
            ('截图设置', ('push', 'screenshot', 'targets', 1, 'target')),
            ('外部程序设置', ('external', 'on_timeout')),
            ('日志设置', ('log', 'retention_days')),
        ]
        for name, path in cases:
            assert app.show_page(name)
            page = app.visible_pages[name]
            page.focus_issue(path)
            assert page.winfo_exists()
            if path in getattr(page, 'fields', {}):
                assert page.fields[path][1].winfo_exists()
            elif name == '消息推送设置':
                assert page._expanded_channel == 1
                assert page.channel_entries['sckey'].winfo_exists()
            elif name == '监视设置':
                assert 'YAML' in page.preview.cget('text')
            elif path[2] == 'image_host':
                assert page.host_rows[1]['token'].winfo_exists()
            else:
                assert page.target_rows[1]['entry'].winfo_exists()
    finally:
        root.destroy()


def test_validation_routes_external_error_to_action_field(tmp_path):
    """外部动作诊断从校验入口导航至附件页而非检查页。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = ('external', 'on_timeout')
        app.model.validate = lambda _: SimpleNamespace(issues=[
            SimpleNamespace(path=path, severity='error')])
        app.validate()
        root.update()
        assert app.current_page == '外部程序设置'
        assert root.focus_get() is app.visible_pages['外部程序设置'].fields[path][1]._entry
    finally:
        root.destroy()


def test_validation_counts_timeout_threshold_on_its_destination_page(tmp_path):
    """超时阈值诊断计数与定位均属于实际承载字段的附件页。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = ('monitor', 'common', 'timeout_threshold')
        app.model.validate = lambda _: SimpleNamespace(issues=[
            SimpleNamespace(path=path, severity='error')])
        app.validate()
        assert app.page_names[1] == '监视设置'
        assert '1 个问题' in app.visible_pages['主页'].check.cget('text')
        assert app.current_page == '外部程序设置'
        assert root.focus_get() is app.visible_pages['外部程序设置'].fields[path][1]._entry
    finally:
        root.destroy()


def test_real_model_invalid_threshold_is_change_not_issue(tmp_path):
    """真实模型的非法阈值产生待确认变更，不虚构字段错误。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = ('monitor', 'common', 'timeout_threshold')
        app.model.set(('monitor', 'target'), 'demo.exe')
        app.model.set(path, 'invalid-threshold-secret')
        candidate = app.validate()
        assert not candidate.issues
        assert any(change.path == path for change in candidate.changes)
        assert app.current_page == '主页'
        assert '0 个问题' in app.status.cget('text')
    finally:
        root.destroy()


def test_real_monitor_common_structure_error_has_focusable_page_hint(tmp_path):
    """非映射公共节点提供监视页内可聚焦落点和安全文本。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('monitor', 'common'), 'private-structure-secret')
        candidate = app.validate()
        issue = next(issue for issue in candidate.issues
                     if issue.path == ('monitor', 'common'))
        assert issue.code == 'mapping'
        assert app.current_page == '主页'
        assert '配置结构' in app.visible_pages['主页'].issue_hint.cget('text')
        assert 'private-structure-secret' not in app.visible_pages['主页'].issue_hint.cget('text')
    finally:
        root.destroy()


def test_review_summary_handles_invalid_common_during_global_refresh(tmp_path):
    """全局刷新遇到已诊断的公共节点结构错误时只显示安全占位。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('monitor', 'common'), 'private-structure-secret')
        app.refresh()
        assert '等待：待校验' in app.summary_text
        assert 'private-structure-secret' not in app.summary_text
        assert any(issue.path == ('monitor', 'common') and issue.code == 'mapping'
                   for issue in app.model.validate(tmp_path).issues)
    finally:
        root.destroy()


def test_review_summary_covers_events_actions_without_values(tmp_path):
    """检查摘要包含事件与动作状态，但不回显用户输入或凭证。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('monitor', 'mode'), 'launch')
        app.model.set(('monitor', 'target'), 'private-target-98765.exe')
        app.model.set(('push', 'templates', 'on_end', 'enable'), False)
        app.model.set(('push', 'templates', 'on_external', 'enable'), True)
        app.model.set(('push', 'templates', 'on_external', 'capture_screenshot'), False)
        app.model.set(('external', 'on_end'), 'C:\\private-action-98765.bat')
        app.model.set(('external', 'on_timeout'), '')
        app.refresh()
        summary = app.summary_text
        assert '运行方式：主动启动并监视' in summary
        assert '目标结束：关闭' in summary
        assert '外部程序执行通知：启用' in summary
        assert '事件截图' in app.visible_pages['主页'].conditions.cget('text')
        assert '目标结束外部程序：已配置' in summary
        assert '运行超时外部程序：未配置' in summary
        assert 'private-' not in summary
        assert 'SCTxxxx' not in summary
    finally:
        root.destroy()


@pytest.mark.parametrize('path', [('external',), ('push', 'templates')])
def test_review_invalid_section_refresh_and_real_issue_navigation(tmp_path, path):
    """非映射动作或模板全局刷新安全，真实诊断可进入检查页。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('monitor', 'target'), 'demo.exe')
        app.model.set(path, 'private-structure-secret')
        app.refresh()
        assert '待校验' in app.summary_text
        assert 'private-structure-secret' not in app.summary_text
        candidate = app.validate()
        assert any(issue.path == path and issue.code == 'mapping'
                   for issue in candidate.issues)
        assert app.current_page == '主页'
        assert '配置结构' in app.visible_pages['主页'].issue_hint.cget('text')
    finally:
        root.destroy()


def test_review_empty_target_is_not_configured(tmp_path):
    """未填目标不显示已配置，填入后仍不泄露目标内容。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('monitor', 'target'), '')
        app.refresh()
        assert '目标位置：未配置' in app.summary_text
        app.model.set(('monitor', 'target'), '  ')
        app.refresh()
        assert '目标位置：未配置' in app.summary_text
        app.model.set(('monitor', 'target'), 'private-target.exe')
        app.refresh()
        assert '目标位置：已配置' in app.summary_text
        assert 'private-target' not in app.summary_text
    finally:
        root.destroy()


def test_review_screenshot_requires_all_effective_conditions(tmp_path):
    """只有开关不表示截图执行，引用、通道与目标齐备才显示条件满足。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        event = 'on_end'
        assert not all(screenshot_conditions(app.model.snapshot(), event).values())
        assert '目标结束：条件未满足' in app.visible_pages['主页'].conditions.cget('text')
        app.model.set(('monitor', 'target'), 'demo.exe')
        app.model.set(('push', 'templates', event, 'content'), '{screenshot}')
        app.model.set(('push', 'push_channel_settings', 'channels'),
                      [{'provider': 'serverchan', 'sckey': 'private-key'}])
        app.visible_pages['截图设置'].add_target()
        app.visible_pages['截图设置']._set_item(('push', 'screenshot', 'targets'), 0, 'target', '测试窗口')
        app.refresh()
        assert all(screenshot_conditions(app.model.snapshot(), event).values())
        assert '目标结束：条件满足' in app.visible_pages['主页'].conditions.cget('text')
        assert 'private-key' not in app.summary_text
    finally:
        root.destroy()


def test_review_summary_masks_untrusted_time_and_mode(tmp_path):
    """未校验的时间和模式不允许泄露到检查摘要。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('monitor', 'mode'), 'private-mode-98765')
        app.model.set(('monitor', 'common', 'max_wait'), 'private-time-98765')
        app.refresh()
        assert 'private-' not in app.summary_text
        assert '运行方式：待校验' in app.summary_text
        assert '最长等待：已配置' in app.summary_text
    finally:
        root.destroy()


@pytest.mark.parametrize('path,code', [
    ((), 'root_type'),
    (('task',), 'legacy'),
    (('monitor', 'launch', 'type'), 'legacy'),
    (('push', 'retry'), 'mapping'),
    (('external',), 'mapping'),
])
def test_non_field_issue_has_safe_focusable_destination(tmp_path, path, code):
    """没有叶字段的结构与旧布局问题仍有安全、可聚焦的诊断落点。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.validate = lambda _: SimpleNamespace(issues=[
            SimpleNamespace(path=path, code=code, severity='error', message='布局错误')])
        app.validate()
        root.update_idletasks()
        assert app.current_page == '主页'
        page = app.visible_pages['主页']
        assert page.issue_hint.winfo_exists()
        assert '配置结构' in page.issue_hint.cget('text')
        assert 'private-' not in page.issue_hint.cget('text')
    finally:
        root.destroy()


def test_nested_field_focus_scrolls_to_content(tmp_path):
    """嵌套折叠字段聚焦时滚动至其在表单中的真实位置。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        root.geometry('600x420')
        app.show_page('消息推送设置')
        root.update()
        page = app.visible_pages['消息推送设置']
        path = ('push', 'templates', 'on_end', 'content')
        page.focus_issue(path)
        root.update_idletasks()
        widget = page.fields[path][1]
        assert widget.winfo_exists()
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
        assert page._parent_canvas.yview() != (0.0, 1.0)
        assert widget.tk_focusNext() is not widget
        assert str(widget.cget('takefocus')) != '0'
        assert root.focus_get() is widget._textbox
    finally:
        root.tk.call('tk', 'scaling', previous)
        if 'app' in locals():
            root.after_cancel(app._after_id)
        root.destroy()


def test_review_tab_switch_with_invalid_common_keeps_issue_location(tmp_path):
    """切换检查页仍可返回监视页定位结构错误，不泄露错误节点原文。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('monitor', 'common'), 'private-structure-secret')
        app.show_page('主页')
        root.update()
        assert '等待：待校验' in app.summary_text
        assert 'private-structure-secret' not in app.summary_text
        candidate = app.validate()
        assert any(issue.path == ('monitor', 'common') and issue.code == 'mapping'
                   for issue in candidate.issues)
        assert app.current_page == '主页'
        assert '配置结构' in app.visible_pages['主页'].issue_hint.cget('text')
    finally:
        root.destroy()


def test_normal_close_cancels_pending_poll(tmp_path):
    """正常关闭窗口后不再执行结果轮询回调。"""
    root = tk.Tk()
    app = make_app(root, tmp_path)
    pending = app._after_id
    app.close()
    assert pending not in root.tk.call('after', 'info')
    app.controller.runner.close.assert_called_once()


def test_screenshot_conditions_update_on_tab_switch(tmp_path):
    """隐藏的截图开关由模型保存，预览和主页条件同步反映该值。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        event = 'on_timeout'
        path = ('push', 'templates', event, 'capture_screenshot')
        app.show_page('消息推送设置')
        page = app.visible_pages['消息推送设置']
        page.expand_event(event)
        assert not hasattr(page, 'capture_switches')
        assert path not in page.fields
        app.model.set(path, False)
        page.preview_event(event)
        assert '截图被阻止' in page.preview_labels[event].cget('text')
        app.show_page('截图设置')
        root.update()
        app.visible_pages['主页'].refresh()
        assert '运行超时：条件未满足' in app.visible_pages['主页'].conditions.cget('text')
        assert app.model.get(path) is False
    finally:
        root.destroy()


def test_after_heartbeat_continues_during_long_worker(tmp_path):
    """真实 Tk 定时器在后台测试未结束时继续执行。"""
    root = tk.Tk()
    release = threading.Event()
    worker = None
    heartbeat_id = None
    try:
        app = make_app(root, tmp_path)
        app.visible_pages['截图设置'].add_target()
        app.test_dialogs.open_capture = Mock()
        app.test_dialogs.show_result = Mock()
        ticks = []

        def heartbeat():
            """以真实 after 记录主线程定时回调。"""
            nonlocal heartbeat_id
            ticks.append(threading.get_ident())
            heartbeat_id = root.after(15, heartbeat)

        def start(_request):
            """模拟长期阻塞的后台 worker，不占用 Tk 线程。"""
            nonlocal worker
            app.controller.runner.active = True
            worker = threading.Thread(target=release.wait, daemon=True)
            worker.start()

        app.controller.runner.start.side_effect = start
        heartbeat_id = root.after(15, heartbeat)
        app.capture()
        assert app.test_dialogs.open_capture.call_args.args[0]('window', '窗口')
        deadline = time.monotonic() + 0.4
        while len(ticks) < 4 and time.monotonic() < deadline:
            root.update()
            time.sleep(0.005)
        assert worker is not None and worker.is_alive()
        assert len(ticks) >= 4
        assert set(ticks) == {threading.get_ident()}
    finally:
        release.set()
        if worker is not None:
            worker.join(timeout=1)
        if heartbeat_id is not None:
            root.after_cancel(heartbeat_id)
        if 'app' in locals():
            root.after_cancel(app._after_id)
        root.destroy()


def test_chinese_long_template_is_multiline_and_keyboard_reachable(tmp_path):
    """长中文正文可换行编辑，并可通过键盘焦点链抵达。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['消息推送设置']
        path = ('push', 'templates', 'on_end', 'content')
        app.show_page('消息推送设置')
        root.update()
        page.focus_issue(path)
        root.update()
        widget = page.fields[path][1]
        assert isinstance(widget, ctk_kit.CTkTextbox)
        text = ('这是一段用于通知显示的中文长正文。' * 20) + '\n第二段中文内容'
        widget.delete('1.0', 'end')
        widget.insert('1.0', text)
        widget.event_generate('<KeyRelease>')
        root.update()
        assert app.model.get(path) == text
        assert root.focus_get() is widget._textbox
        assert widget.tk_focusNext() is not widget
        assert str(widget.cget('takefocus')) != '0'
        page.refresh()
        assert page.fields[path][1].get('1.0', 'end-1c') == text
    finally:
        root.destroy()


def test_validation_routes_every_registered_field_to_its_widget(tmp_path):
    """全部静态诊断路径均在校验入口导航到实际字段。"""
    root = ctk_kit.App(title='测试', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        root.deiconify()
        pages = [app.visible_pages[name] for name in
                 ('监视设置', '消息推送设置', '截图设置', '日志设置')]
        cases = [(index, path) for index, page in enumerate(pages)
                 for path in getattr(page, 'fields', {})]
        assert cases
        for index, path in cases:
            app.model.validate = lambda _, field=path: SimpleNamespace(issues=[
                SimpleNamespace(path=field, severity='error')])
            app.validate()
            root.update()
            if path == ('monitor', 'common', 'timeout_threshold') or path[0] == 'external':
                destination = '外部程序设置'
            elif path[:2] == ('push', 'screenshot'):
                destination = '截图设置'
            else:
                destination = {0: '监视设置', 1: '消息推送设置',
                               3: '日志设置'}[index]
            assert app.current_page == destination, path
            focused = app.visible_pages[destination]
            widget = focused.fields[path][1]
            if destination == '监视设置' and path[:2] == ('monitor', 'launch'):
                assert focused.widget_for_issue.winfo_exists()
            else:
                assert root.focus_get() is (widget._textbox if isinstance(widget, ctk_kit.CTkTextbox)
                                            else widget._entry), path
    finally:
        root.destroy()


@pytest.mark.parametrize('path,page_index', [
    (('push', 'push_channel_settings', 'channels', 1, 'sckey'), 1),
    (('push', 'screenshot', 'image_host', 1, 'options'), 2),
    (('push', 'screenshot', 'targets', 1, 'target'), 2),
])
def test_validation_routes_dynamic_diagnostic_path(
        tmp_path, path, page_index):
    """列表项诊断切换至其所在页面并突出指定索引。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(app.visible_pages['消息推送设置'].channels_path, [
            {'provider': 'serverchan', 'sckey': ''} for _ in range(2)])
        app.visible_pages['截图设置'].add_host()
        app.visible_pages['截图设置'].add_target()
        app.visible_pages['截图设置'].add_target()
        app.refresh()
        app.model.validate = lambda _: SimpleNamespace(issues=[
            SimpleNamespace(path=path, severity='error')])
        app.validate()
        root.update()
        page = app.visible_pages['消息推送设置' if page_index == 1 else '截图设置']
        assert app.current_page == {1: '消息推送设置', 2: '截图设置'}[page_index]
        if page_index == 1:
            assert page._expanded_channel == 1
            assert page.channel_entries['sckey'].winfo_exists()
        elif path[2] == 'image_host':
            assert page.host_rows[1]['options'].winfo_exists()
        else:
            assert page.target_rows[1]['entry'].winfo_exists()
    finally:
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
        app.save_coordinator.flush = Mock(return_value=False)
        assert app.show_page('主页')
        assert '配置未保存，已保留草稿' in app.status.cget('text')
        assert app.current_page == '主页'
        assert app.model.get(('monitor', 'target')) == 'draft.exe'
    finally:
        root.destroy()


def test_step_creates_missing_middle_sections(tmp_path):
    """common/log 缺失时步进从默认值起步并创建缺失节。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        source = tmp_path / 'step-missing.yaml'
        source.write_text('monitor:\n  mode: watch\n  target: demo.exe\n'
                          'push: {}\nexternal: {}\n', encoding='utf-8')
        assert app.controller.open(source)
        assert app.show_page('外部程序设置')
        external_page = app.visible_pages['外部程序设置']
        external_page._step(1)
        threshold = ('monitor', 'common', 'timeout_threshold')
        assert app.model.get(threshold) == 4
        external_page._step(-1)
        assert app.model.get(threshold) == 3
        assert app.show_page('日志设置')
        logs_page = app.visible_pages['日志设置']
        files = ('log', 'max_log_files')
        logs_page._step(files, 1)
        assert app.model.get(files) == 16
        logs_page._step(files, -1)
        assert app.model.get(files) == 15
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


def test_empty_push_page_enables_retry_and_creates_channel_section(tmp_path):
    """push 为空映射时重试字段可写，添加通道会创建缺失子节。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        source = tmp_path / 'empty-push.yaml'
        source.write_text('monitor:\n  mode: watch\n  target: demo.exe\n  common: {}\n'
                          'push: {}\nlog: {}\nexternal: {}\n', encoding='utf-8')
        assert app.controller.open(source)
        assert app.show_page('消息推送设置')
        page = app.visible_pages['消息推送设置']
        assert page.structure_hint.cget('text') == ''
        retry = ('push', 'retry', 'interval')
        widget = page.fields[retry][1]
        assert widget.cget('state') == 'normal'
        widget.insert(0, '9s')
        assert app.model.get(retry) == '9s'
        assert page.add_channel_button.cget('state') == 'normal'
        page.add_channel('telegram')
        channels = app.model.get(page.channels_path)
        assert channels[-1]['provider'] == 'telegram'
        assert app.controller.save()
        disk = read_document(source).tree
        assert disk['push']['retry']['interval'] == '9s'
        assert disk['push']['push_channel_settings']['channels'][-1]['provider'] == 'telegram'
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


def test_missing_log_leaf_is_editable_and_created_on_save(tmp_path):
    """log 映射存在但叶子缺失时可编辑，并在保存时创建键。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        source = tmp_path / 'empty-log.yaml'
        source.write_text('monitor:\n  mode: watch\n  target: demo.exe\n  common: {}\n'
                          'push: {}\nlog: {}\nexternal: {}\n', encoding='utf-8')
        assert app.controller.open(source)
        assert app.show_page('日志设置')
        page = app.visible_pages['日志设置']
        path = ('log', 'max_log_files')
        widget = page.fields[path][1]
        assert widget.cget('state') == 'normal'
        widget.insert(0, '19')
        assert app.model.get(path) == 19
        assert app.controller.save()
        assert read_document(source).tree['log']['max_log_files'] == 19
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


def test_invalid_channel_item_keeps_visible_row_actions(tmp_path, monkeypatch):
    """坏通道项在首位时，编辑、切换与删除仍作用于用户所见行。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['消息推送设置']
        path = page.channels_path
        app.model.set(path, ['bad',
                             {'provider': 'serverchan', 'sckey': 'first'},
                             {'provider': 'serverchan', 'sckey': 'second'}])
        page.refresh()
        assert len(page.channel_rows) == 2
        page.expand_channel(1)
        page.channel_entries['sckey'].insert(0, 'edited-')
        channels = app.model.get(path)
        assert channels[0] == 'bad'
        assert channels[1]['sckey'] == 'first'
        assert channels[2]['sckey'] == 'edited-second'
        page.change_channel_provider(1, 'bark')
        channels = app.model.get(path)
        assert channels[1]['provider'] == 'serverchan'
        assert channels[2]['provider'] == 'bark'
        monkeypatch.setattr('modules.gui.pages.notification_ctk.messagebox.askyesno',
                            lambda *a, **k: True)
        page.remove_channel(1)
        assert app.model.get(path) == ['bad', {'provider': 'serverchan', 'sckey': 'first'}]
    finally:
        root.destroy()


def test_invalid_target_item_keeps_visible_row_actions(tmp_path, monkeypatch):
    """坏目标项在首位时，编辑、移动与删除均按原始索引执行。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        path = ('push', 'screenshot', 'targets')
        app.model.set(path, ['bad',
                             {'provider': 'window', 'target': 'A'},
                             {'provider': 'window', 'target': 'B'}])
        page.refresh()
        assert len(page.target_rows) == 2
        entry = page.target_rows[1]['entry']
        entry.delete(0, 'end')
        entry.insert(0, 'B-edited')
        targets = app.model.get(path)
        assert targets[0] == 'bad'
        assert targets[1]['target'] == 'A'
        assert targets[2]['target'] == 'B-edited'
        page.move_target(0, 1)
        assert app.model.get(path) == ['bad',
                                       {'provider': 'window', 'target': 'B-edited'},
                                       {'provider': 'window', 'target': 'A'}]
        monkeypatch.setattr('modules.gui.pages.screenshot.messagebox.askyesno',
                            lambda *a, **k: True)
        page.remove_target(1)
        assert app.model.get(path) == ['bad',
                                       {'provider': 'window', 'target': 'B-edited'}]
    finally:
        root.destroy()


def test_invalid_host_item_keeps_visible_row_actions(tmp_path):
    """坏图床项在首位时，编辑、排序与删除均按原始索引执行。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        path = ('push', 'screenshot', 'image_host')
        app.model.set(path, ['bad',
                             {'provider': 'catbox', 'token': 'first'},
                             {'provider': 'beeimg', 'token': 'second'}])
        page.refresh()
        assert len(page.host_rows) == 2
        token = page.host_rows[1]['token']
        token.delete(0, 'end')
        token.insert(0, 'second-edited')
        hosts = app.model.get(path)
        assert hosts[0] == 'bad'
        assert hosts[1]['token'] == 'first'
        assert hosts[2]['token'] == 'second-edited'
        page.move_host(0, 1)
        assert app.model.get(path) == ['bad',
                                       {'provider': 'beeimg', 'token': 'second-edited'},
                                       {'provider': 'catbox', 'token': 'first'}]
        page.remove_host(1)
        assert app.model.get(path) == ['bad',
                                       {'provider': 'beeimg', 'token': 'second-edited'}]
    finally:
        root.destroy()


def test_target_move_across_invalid_item_roundtrips(tmp_path):
    """跨越坏项移动与连续反向移动只交换有效项并还原引用。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        path = ('push', 'screenshot', 'targets')
        content_path = ('push', 'templates', 'on_end', 'content')
        app.model.set(path, [{'provider': 'window', 'target': 'X'}, 'bad',
                             {'provider': 'window', 'target': 'Y'}])
        app.model.set(content_path, '{screenshot_1}/{screenshot_3}')
        page.refresh()
        assert len(page.target_rows) == 2
        page.move_target(0, 1)
        targets = app.model.get(path)
        assert targets[0]['target'] == 'Y'
        assert targets[1] == 'bad'
        assert targets[2]['target'] == 'X'
        assert app.model.get(content_path) == '{screenshot_3}/{screenshot_1}'
        page.move_target(1, -1)
        assert app.model.get(path) == [{'provider': 'window', 'target': 'X'}, 'bad',
                                       {'provider': 'window', 'target': 'Y'}]
        assert app.model.get(content_path) == '{screenshot_1}/{screenshot_3}'
    finally:
        root.destroy()


def test_invalid_target_item_keeps_adb_port_editing(tmp_path):
    """坏目标项在首位时，ADB 端口仍写入所见目标。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        path = ('push', 'screenshot', 'targets')
        app.model.set(path, ['bad',
                             {'provider': 'adb', 'target': '127.0.0.1:16384'}])
        page.refresh()
        port = page.target_rows[0]['port']
        port.delete(0, 'end')
        port.insert(0, '16385')
        targets = app.model.get(path)
        assert targets[0] == 'bad'
        assert targets[1]['target'] == '127.0.0.1:16385'
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


def test_open_missing_push_and_log_sections_refreshes_empty_state(tmp_path):
    """缺少 push/log 节时页面按空状态刷新，不抛 KeyError。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = tmp_path / 'missing-sections.yaml'
        path.write_text('monitor:\n  mode: watch\n  target: demo.exe\n  common: {}\n'
                        'external: {}\n', encoding='utf-8')
        assert app.controller.open(path)
        app.show_page('主页')
        assert '通知通道：待校验' in app.summary_text
        assert '截图目标：待校验' in app.summary_text
        assert app.show_page('消息推送设置')
        assert app.visible_pages['消息推送设置'].channel_rows == []
        assert app.show_page('日志设置')
        assert app.visible_pages['日志设置'].fields[('log', 'log_directory')][1].get() == ''
        assert app.visible_pages['日志设置'].fields[('log', 'retention_days')][1].get() == ''
    finally:
        root.destroy()


def test_empty_push_and_log_sections_allow_creation_and_actions(tmp_path, monkeypatch):
    """push 为空且缺少 log 节时，列表创建与测试入口给出提示而不抛 KeyError。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = tmp_path / 'empty-push.yaml'
        path.write_text('monitor:\n  mode: watch\n  target: demo.exe\n  common: {}\n'
                        'push: {}\nlog: {}\nexternal: {}\n', encoding='utf-8')
        assert app.controller.open(path)
        app.test_dialogs.open_upload = Mock()
        app.upload()
        assert '请先添加图床' in app.status.cget('text')
        app.test_dialogs.open_upload.assert_not_called()
        app.notify()
        assert '请先添加推送通道' in app.status.cget('text')
        notices = []
        monkeypatch.setattr('modules.gui.app.messagebox.showinfo',
                            lambda *args, **kwargs: notices.append(args))
        app.open_logs()
        assert notices
        screenshot = app.visible_pages['截图设置']
        screenshot.add_target()
        screenshot.add_host()
        assert app.model.get(('push', 'screenshot', 'targets'))
        assert app.model.get(('push', 'screenshot', 'image_host'))
    finally:
        root.destroy()


def test_malformed_screenshot_and_channel_sections_hint_without_overwrite(tmp_path):
    """截图或通道节为标量时测试入口只提示结构异常，不覆盖原值。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('push', 'screenshot'), 'broken')
        app.upload()
        assert '结构无效' in app.status.cget('text')
        app.model.set(('push', 'push_channel_settings'), 'broken')
        app.notify()
        assert '结构无效' in app.status.cget('text')
        assert app.model.get(('push', 'screenshot')) == 'broken'
        assert app.model.get(('push', 'push_channel_settings')) == 'broken'
    finally:
        root.destroy()


def test_open_non_mapping_list_items_skips_and_hints(tmp_path):
    """通道、目标与图床列表中的非映射项被跳过并提示结构问题。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = tmp_path / 'invalid-items.yaml'
        path.write_text(
            'monitor:\n  mode: watch\n  target: demo.exe\n  common: {}\n'
            'push:\n  push_channel_settings:\n    channels:\n      - bad\n'
            '      - {provider: serverchan, sckey: ""}\n'
            '  screenshot:\n    targets:\n      - bad\n'
            '      - {provider: window, target: A}\n'
            '    image_host:\n      - bad\n'
            '      - {provider: beeimg, token: ""}\n'
            '  templates: {}\n  retry: {}\n'
            'log: {}\nexternal: {}\n', encoding='utf-8')
        assert app.controller.open(path)
        app.show_page('消息推送设置')
        push_page = app.visible_pages['消息推送设置']
        assert len(push_page.channel_rows) == 1
        assert '结构无效' in push_page.channel_hint.cget('text')
        app.show_page('截图设置')
        screenshot_page = app.visible_pages['截图设置']
        assert len(screenshot_page.target_rows) == 1
        assert len(screenshot_page.host_rows) == 1
        assert '结构无效' in screenshot_page.targets_hint.cget('text')
        assert '结构无效' in screenshot_page.hosts_hint.cget('text')
    finally:
        root.destroy()
