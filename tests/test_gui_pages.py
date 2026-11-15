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

from modules.config_document import new_document, save_document
from modules.gui.model import EditorModel
from modules.gui.forms import screenshot_conditions
from modules.gui.protocol import TestResult as GuiTestResult


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
    return App(root, controller)


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


def test_list_operations_persist_immediately(tmp_path):
    """通知通道、截图目标和图床列表操作立即补存。"""
    from modules.config_document import read_document

    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = app.model.document.path
        channels = app.pages[1]
        attachments = app.pages[2]
        channels._add()
        assert len(read_document(path).tree['push']['push_channel_settings']['channels']) == len(app.model.get(channels.channels_path))
        channels.channel_list.current(0)
        channels._remove()
        assert len(read_document(path).tree['push']['push_channel_settings']['channels']) == len(app.model.get(channels.channels_path))
        attachments._add_target('window')
        assert len(read_document(path).tree['push']['screenshot']['targets']) == len(app.model.get(attachments.targets_path))
        attachments._add_host()
        assert len(read_document(path).tree['push']['screenshot']['image_host']) == len(app.model.get(attachments.hosts_path))
    finally:
        root.destroy()


def test_pages_bind_variables_without_default_root(tmp_path, monkeypatch):
    """页面变量绑定所属控件，不依赖 Tk 的默认根窗口。"""
    root = tk.Tk()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(tk, '_support_default_root', False)
            app = make_app(root, tmp_path)
            app.pages[0].set_field(('monitor', 'target'), 'demo.exe')
            app.pages[0].set_field(('monitor', 'common', 'loop_interval'), '2s')
            app.pages[1].channel_entries['provider'].insert('end', 'x')
            app.pages[2].host_entries['token'].insert('end', 'x')
            assert app.model.get(('monitor', 'target')) == 'demo.exe'
            assert app.model.get(app.pages[1].channels_path)[0]['provider'].endswith('x')
            assert app.model.get(app.pages[2].hosts_path)[0]['token'].endswith('x')
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
        assert app.appearance_theme.get() == '跟随系统'
        assert app.appearance_material.get() == 'Mica'
        assert isinstance(app.notebook, tk.ttk.Notebook)
        app.appearance_theme.set('深色')
        app.choose_theme('深色')
        app.appearance_material.set('none')
        app.choose_material('none')
        assert load_preferences(path) == AppearancePreferences(dark=True, material='none')
        assert ctk_kit.get_appearance_mode() == 'Dark'
    finally:
        root.destroy()


def test_error_expands_and_focuses(tmp_path):
    """高级字段出错自动展开所在区域并将焦点移至控件。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.pages[0]
        path = ('monitor', 'common', 'loop_interval')
        page.section_for_issue.set_expanded(False)
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
        app.latest_request = ('old', app.controller.model.document_generation, 0)
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
        app.latest_request = ('current', 0, 0)
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
        page = app.pages[0]
        advanced = ('monitor', 'common', 'loop_interval')
        target = ('monitor', 'target')
        page.set_field(advanced, '3s')
        before = app.model.revision
        page.section_for_issue.set_expanded(False)
        root.update()
        assert app.model.get(advanced) == '3s'
        assert app.model.revision == before
        assert page.fields[target][1].winfo_ismapped()
        assert '已配置' in page.section_for_issue.button.cget('text')
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
        assert all(page.model is model for page in app.pages)
        assert app.pages[0].fields[('monitor', 'target')][0].get() == '中文目标.exe'
        app.pages[0].set_field(('monitor', 'target'), '新目标.exe')
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
        app.pages[0].refresh()
        text = app.pages[0].preview.cget('text')
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
        page = app.pages[2]
        hosts = app.model.get(page.hosts_path)
        hosts[0]['options'] = {'album_id': 12}
        app.model.set(page.hosts_path, hosts)
        page.refresh()
        app.model.set(page.hosts_path, app.model.get(page.hosts_path))
        assert isinstance(app.model.get(page.hosts_path)[0]['options'], dict)
    finally:
        root.destroy()


def test_test_request_uses_document_directory(tmp_path):
    """已保存配置从文件目录解析，测试请求不暴露在命令行。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.pages[2]._add_target('window')
        app.pages[2]._set_item(app.pages[2].targets_path, 0, 'target', '测试窗口')
        app.capture()
        request = app.controller.runner.start.call_args.args[0]
        assert request.config_dir == str(tmp_path.resolve())
        assert request.document_generation == app.model.document_generation
    finally:
        root.destroy()


def test_host_options_edit_stays_mapping(tmp_path):
    """编辑图床 options 不得把配置映射改成 Python repr 字符串。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.pages[2]
        page._set_item(page.hosts_path, 0, 'options', '{album_id: 12}')
        assert app.model.get(page.hosts_path)[0]['options'] == {'album_id': 12}
    finally:
        root.destroy()


def test_host_options_display_roundtrips_and_invalid_input_blocks_save(tmp_path):
    """真实输入框显示可解析映射，非法值显示错误且不能被保存吞掉。"""
    from modules.config_document import read_document

    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.pages[2]
        hosts = app.model.get(page.hosts_path)
        hosts[0]['options'] = {'album_id': 12, 'enabled': False}
        app.model.set(page.hosts_path, hosts)
        page.refresh()
        entry = page.host_entries['options']
        assert entry.get() == '{album_id: 12, enabled: false}'
        entry.delete(0, 'end')
        entry.insert(0, '{album_id: 13}')
        assert app.model.get(page.hosts_path)[0]['options'] == {'album_id': 13}
        entry.delete(0, 'end')
        entry.insert(0, '{album_id: [}')
        assert app.model.get(page.hosts_path)[0]['options'] == {}
        assert 'options' in page.host_error.cget('text')
        assert any(issue.path == (*page.hosts_path, 0, 'options')
                   for issue in app.model.validate(tmp_path).issues)
        assert not app.controller.save(tmp_path / 'invalid.yaml')
        assert not (tmp_path / 'invalid.yaml').exists()
        page.refresh()
        assert page.host_entries['options'].get() == '{album_id: [}'
        page.host_entries['options'].delete(0, 'end')
        page.host_entries['options'].insert(0, '{album_id: 14}')
        assert not page.host_error.cget('text')
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
        page = app.pages[2]
        app.pages[0].set_field(('monitor', 'target'), 'demo.exe')
        hosts = app.model.get(page.hosts_path)
        hosts[0]['custom'] = 'first'
        hosts.append({'provider': 'boltp', 'token': 'secret', 'custom': 'second'})
        app.model.set(page.hosts_path, hosts)
        page.refresh()
        assert app.controller.save(tmp_path / 'reordered.yaml')
        page.host_list.current(0)
        page.move_host(1)
        assert app.model.get(page.hosts_path)[1]['custom'] == 'first'
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


def test_invalid_options_follow_host_when_reordered(tmp_path):
    """图床移动后非法草稿仍属于原条目，不能保存。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.pages[2]
        page._add_host()
        page.host_list.current(0)
        page._selected_host()
        entry = page.host_entries['options']
        entry.insert(0, '{broken: [}')
        page.move_host(1)
        assert page.host_entries['options'].get() == '{broken: [}'
        assert any(issue.path == (*page.hosts_path, 1, 'options')
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
        page = app.pages[2]
        destination = tmp_path / 'another.yaml'
        if action == 'open':
            save_document(new_document(), destination, expected_fingerprint=None)
        page.host_entries['options'].insert(0, '{broken: [}')
        assert not app.model.dirty
        assert page.host_entries['options'].get() == '{broken: [}'
        generation = app.model.document_generation
        confirm = Mock(return_value='cancel')
        app.controller.confirm = confirm

        result = app.controller.open(destination) if action == 'open' else getattr(app.controller, action)()

        assert result is False
        confirm.assert_called_once_with('unsaved', app.model.document.path)
        assert app.model.document_generation == generation
        assert page.host_entries['options'].get() == '{broken: [}'
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
        page = app.pages[2]
        page.host_entries['options'].insert(0, '{broken: [}')
        app.controller.confirm = Mock(side_effect=lambda kind, _: 'confirm' if kind == 'exit' else answer)
        assert app.controller.close() is expected
        if expected:
            app.controller.runner.close.assert_called_once()
        else:
            app.controller.runner.close.assert_not_called()
            assert page.host_entries['options'].get() == '{broken: [}'
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
        app.pages[2]._add_target('window')
        app.capture()
        app.controller.runner.start.assert_not_called()
        assert '配置' in app.status.cget('text')
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
    """四页存在且 CLI 启动后仍可编辑，不提供日志或停止入口。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        assert tuple(app.page_names) == ('监视什么', '如何通知我', '附图与后续动作', '检查与启动')
        assert not any(name in app.actions for name in ('停止 CLI', '查看日志', '复制日志'))
        app.pages[0].set_field(('monitor', 'target'), '继续编辑.exe')
        assert app.controller.model.dirty
        assert 'SCTxxxx' not in app.summary_text
    finally:
        root.destroy()


def test_capture_request_requires_selected_target(tmp_path):
    """无截图目标时不允许构造默认索引零的请求。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.capture()
        app.controller.runner.start.assert_not_called()
        assert '目标' in app.status.cget('text')
    finally:
        root.destroy()


def test_notification_event_preview_and_variable_controls_are_visible(tmp_path):
    """通知页须可见预览及插入变量入口，而不依赖内部方法调用。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.pages[1]
        texts = [child.cget('text') for child in page.body.winfo_children()
                 if isinstance(child, tk.ttk.Button)]
        assert '模拟预览' in texts
        assert '插入变量' in texts
    finally:
        root.destroy()


def test_channel_required_secret_is_visible_outside_advanced_section(tmp_path):
    """选定通道的必填凭证可编辑且默认遮挡。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.pages[1]
        entries = [child for row in page.channel_body.winfo_children()
                   for child in row.winfo_children() if isinstance(child, tk.ttk.Entry)]
        assert any(entry.cget('show') == '●' for entry in entries)
        assert page.channel_body is not page.section_for_issue.body
    finally:
        root.destroy()


def test_channel_edit_survives_selection_and_refresh(tmp_path):
    """切换通道与刷新不会丢失输入或把第一个通道的值写到第二个。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.pages[1]
        app.model.set(page.channels_path, [{'provider': 'serverchan', 'sckey': ''},
                                           {'provider': 'serverchan', 'sckey': ''}])
        page.refresh()
        page.channel_list.current(0)
        page._channel_fields()
        entries = [child for row in page.channel_body.winfo_children()
                   for child in row.winfo_children() if isinstance(child, tk.ttk.Entry)]
        entries[-1].delete(0, 'end')
        entries[-1].insert(0, 'first-secret')
        page.channel_list.current(1)
        page._channel_fields()
        entries = [child for row in page.channel_body.winfo_children()
                   for child in row.winfo_children() if isinstance(child, tk.ttk.Entry)]
        entries[-1].delete(0, 'end')
        entries[-1].insert(0, 'second-secret')
        page.refresh()
        assert page.channel_list.current() == 1
        channels = app.model.get(page.channels_path)
        assert channels[0]['sckey'] == 'first-secret'
        assert channels[1]['sckey'] == 'second-secret'
    finally:
        root.destroy()


def test_provider_edit_refresh_keeps_selected_channel(tmp_path):
    """第二个通道改 provider 后失焦刷新仍显示第二项。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.pages[1]
        app.model.set(page.channels_path, [{'provider': 'serverchan', 'sckey': 'first'},
                                           {'provider': 'serverchan', 'sckey': 'second'}])
        page.refresh()
        page.channel_list.current(1)
        page._channel_fields()
        provider = [child for child in page.channel_body.winfo_children()[0].winfo_children()
                    if isinstance(child, tk.ttk.Entry)][0]
        provider.delete(0, 'end')
        provider.insert(0, 'bark')
        provider.event_generate('<FocusOut>')
        root.update()
        assert page.channel_list.current() == 1
        assert app.model.get(page.channels_path)[1]['provider'] == 'bark'
    finally:
        root.destroy()


def test_host_edit_refresh_keeps_selected_upload_host(tmp_path, monkeypatch):
    """编辑第二个图床后刷新和上传仍使用第二个槽位。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.pages[2]
        page._add_host()
        page.host_list.current(1)
        page._selected_host()
        provider = [child for child in page.host_body.winfo_children()
                    if isinstance(child, tk.ttk.Entry)][0]
        provider.delete(0, 'end')
        provider.insert(0, 'superbed')
        provider.event_generate('<FocusOut>')
        page.refresh()
        assert page.host_list.current() == 1
        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *_, **__: True)
        monkeypatch.setattr('modules.gui.app.filedialog.askopenfilename',
                            lambda **_: str(tmp_path / 'image.png'))
        app.upload()
        assert app.controller.runner.start.call_args.args[0].host_index == 1
    finally:
        root.destroy()


def test_host_options_credentials_are_masked(tmp_path):
    """图床 options 中的凭证不在输入框默认状态下明文显示。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.pages[2]
        hosts = app.model.get(page.hosts_path)
        hosts[0]['options'] = {'api_key': 'demo'}
        app.model.set(page.hosts_path, hosts)
        page.refresh()
        entries = [child for child in page.host_body.winfo_children()
                   if isinstance(child, tk.ttk.Entry)]
        assert entries[-1].cget('show') == '●'
    finally:
        root.destroy()


def test_request_uses_selected_event_and_host(tmp_path, monkeypatch):
    """通知与上传请求沿用界面选择，未选图床不偷偷退回零。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        monkeypatch.setattr('modules.gui.app.messagebox.askyesno', lambda *_, **__: True)
        monkeypatch.setattr('modules.gui.app.filedialog.askopenfilename', lambda **_: str(tmp_path / 'image.png'))
        app.pages[1].selected_event.set('on_timeout')
        app.notify()
        assert app.controller.runner.start.call_args.args[0].template_key == 'on_timeout'
        app.pages[2]._add_host()
        app.pages[2].host_list.current(1)
        app.upload()
        assert app.controller.runner.start.call_args.args[0].host_index == 1
        app.controller.runner.start.reset_mock()
        app.pages[2].host_list.set('')
        app.upload()
        app.controller.runner.start.assert_not_called()
    finally:
        root.destroy()


def test_advanced_summary_never_contains_secret(tmp_path):
    """折叠标签只显示安全字段名称，不能显示凭证原文。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('push', 'retry', 'interval'), 'private-98765')
        app.pages[1].refresh()
        label = app.pages[1].section_for_issue.button.cget('text')
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
        app.pages[2]._add_target('window')
        app.pages[2]._set_item(app.pages[2].targets_path, 0, 'target', '测试窗口')
        app.capture()
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
        page = app.pages[0]
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
        assert '(' in app.notebook.tab(0, 'text')
        assert app.notebook.index('current') == 0
    finally:
        root.destroy()


def test_six_image_hosts_edit_and_reorder(tmp_path):
    """六个已支持图床的字段输入及上下移动保持完整槽位。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.pages[2]
        for provider in ('wmimg', 'beeimg', 'beeimg_cn', 'boltp', 'superbed'):
            page._add_host()
            page.host_list.current(len(app.model.get(page.hosts_path)) - 1)
            page._selected_host()
            page.host_entries['provider'].delete(0, 'end')
            page.host_entries['provider'].insert(0, provider)
            page.refresh()
        assert [item['provider'] for item in app.model.get(page.hosts_path)] == [
            'catbox', 'wmimg', 'beeimg', 'beeimg_cn', 'boltp', 'superbed']
        page.host_list.current(3)
        page._selected_host()
        page.host_entries['token'].insert(0, 'private-token')
        page.host_entries['expiration'].insert(0, '7d')
        page.host_entries['options'].delete(0, 'end')
        page.host_entries['options'].insert(0, '{storage_id: 12}')
        page.move_host(-1)
        assert page.host_list.current() == 2
        assert app.model.get(page.hosts_path)[2] == {
            'provider': 'beeimg_cn', 'token': 'private-token',
            'expiration': '7d', 'options': {'storage_id': 12}}
        page.move_host(1)
        assert page.host_list.current() == 3
        page.host_list.current(0)
        page.move_host(-1)
        assert page.host_list.current() == 0
    finally:
        root.destroy()


def test_external_action_disable_restore_is_memory_only(tmp_path):
    """清空动作路径停用，撤销仅恢复本模型草稿。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.pages[2]
        path = ('external', 'on_end')
        page.set_field(path, 'C:\\task.bat')
        page.disable_action('on_end')
        assert app.model.get(path) == ''
        assert page.fields[path][0].get() == ''
        page.restore_action('on_end')
        assert app.model.get(path) == 'C:\\task.bat'
        page.disable_action('on_end')
        app.model.replace_document(new_document())
        page.refresh()
        page.restore_action('on_end')
        assert app.model.get(path) != 'C:\\task.bat'
    finally:
        root.destroy()


def test_each_event_displays_screenshot_conditions_and_explicit_reference(tmp_path):
    """四事件独立显示截图生效条件，插入引用只响应显式操作。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.pages[2]
        for event in ('on_end', 'on_timeout', 'on_wait_timeout', 'on_external'):
            assert event in page.condition_labels
            assert '引用' in page.condition_labels[event].cget('text')
            before = app.model.get(('push', 'templates', event, 'content'))
            page.refresh()
            assert app.model.get(('push', 'templates', event, 'content')) == before
        app.pages[1].insert_variable('on_end', 'screenshot')
        page.refresh()
        assert screenshot_conditions(app.model.snapshot(), 'on_end')['references']
        assert '已满足' in page.condition_labels['on_end'].cget('text')
    finally:
        root.destroy()


def test_dynamic_and_static_issue_paths_focus_exact_field(tmp_path):
    """列表索引、模板和四页普通字段均能定位具体控件。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        cases = [
            (0, ('monitor', 'launch', 'cwd')),
            (1, ('push', 'templates', 'on_external', 'content')),
            (1, ('push', 'push_channel_settings', 'channels', 1, 'sckey')),
            (2, ('push', 'screenshot', 'image_host', 1, 'token')),
            (2, ('push', 'screenshot', 'targets', 1, 'target')),
            (2, ('external', 'on_timeout')),
            (3, ('log', 'retention_days')),
        ]
        app.model.set(app.pages[1].channels_path, [
            {'provider': 'serverchan', 'sckey': ''}] * 2)
        app.pages[2]._add_host()
        app.pages[2]._add_target('window')
        app.pages[2]._add_target('adb')
        app.refresh()
        for index, path in cases:
            page = app.pages[index]
            app.notebook.select(index)
            root.update()
            page.focus_issue(path)
            root.update()
            assert root.focus_get() is page.widget_for_issue, path
            if isinstance(path[-2], int):
                selector = page.channel_list if index == 1 else (
                    page.host_list if path[2] == 'image_host' else page.target_list)
                assert selector.current() == 1
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
        assert app.notebook.index('current') == 2
        assert root.focus_get() is app.pages[2].fields[path][1]
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
        assert app.notebook.tab(0, 'text') == '监视什么'
        assert app.notebook.tab(2, 'text') == '附图与后续动作 (1)'
        assert app.notebook.index('current') == 2
        assert root.focus_get() is app.pages[2].fields[path][1]
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
        assert app.notebook.tab(2, 'text') == '附图与后续动作'
        assert app.notebook.index('current') == 0
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
        assert app.notebook.index('current') == 0
        page = app.pages[0]
        assert page.widget_for_issue is page.common_structure_anchor
        assert page.common_structure_anchor.winfo_ismapped()
        assert root.focus_get() is page.common_structure_anchor
        assert 'monitor.common' in page.common_structure_hint.cget('text')
        assert '映射' in page.common_structure_hint.cget('text')
        assert 'private-structure-secret' not in page.common_structure_hint.cget('text')
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
        assert 'launch' in summary
        assert 'on_end：关闭' in summary
        assert 'on_external：启用' in summary
        assert '截图' in summary
        assert '外部动作' in summary
        assert 'on_end：已配置' in summary
        assert 'on_timeout：未配置' in summary
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
        assert app.notebook.index('current') == 3
        assert root.focus_get() is app.pages[3].issue_anchor
    finally:
        root.destroy()


def test_review_empty_target_is_not_configured(tmp_path):
    """未填目标不显示已配置，填入后仍不泄露目标内容。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('monitor', 'target'), '')
        app.refresh()
        assert '目标：未配置' in app.summary_text
        app.model.set(('monitor', 'target'), '  ')
        app.refresh()
        assert '目标：未配置' in app.summary_text
        app.model.set(('monitor', 'target'), 'private-target.exe')
        app.refresh()
        assert '目标：已配置' in app.summary_text
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
        assert 'on_end：条件未满足' in app.summary_text
        app.model.set(('monitor', 'target'), 'demo.exe')
        app.model.set(('push', 'templates', event, 'content'), '{screenshot}')
        app.model.set(('push', 'push_channel_settings', 'channels'),
                      [{'provider': 'serverchan', 'sckey': 'private-key'}])
        app.pages[2]._add_target('window')
        app.pages[2]._set_item(app.pages[2].targets_path, 0, 'target', '测试窗口')
        app.refresh()
        assert all(screenshot_conditions(app.model.snapshot(), event).values())
        assert 'on_end：条件满足' in app.summary_text
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
        assert '模式：待校验' in app.summary_text
        assert '等待：已配置' in app.summary_text
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
        assert app.notebook.index('current') == 3
        page = app.pages[3]
        assert root.focus_get() is page.widget_for_issue
        assert page.widget_for_issue.winfo_ismapped()
        assert code in page.issue_hint.cget('text')
        assert 'private-' not in page.issue_hint.cget('text')
    finally:
        root.destroy()


@pytest.mark.parametrize('scaling', [1.0, 1.5, 2.0])
def test_real_tk_scaling_keeps_long_form_scrollable_and_keyboard_reachable(
        tmp_path, scaling):
    """实际 Tk 缩放及小窗口下长表单仍能滚动并经 Tab 遍历。"""
    root = tk.Tk()
    previous = float(root.tk.call('tk', 'scaling'))
    try:
        root.tk.call('tk', 'scaling', scaling)
        app = make_app(root, tmp_path / '中文目录')
        root.geometry('600x420')
        app.notebook.select(1)
        root.update()
        page = app.pages[1]
        path = ('push', 'templates', 'on_end', 'content')
        page.focus_issue(path)
        root.update_idletasks()
        widget = page.fields[path][1]
        assert page.section_for_issue.expanded
        assert widget.winfo_ismapped()
        assert page.canvas.yview()[1] < 1 or page.canvas.yview()[0] > 0
        assert widget.tk_focusNext() is not widget
        assert str(widget.cget('takefocus')) != '0'
        assert root.focus_get() is widget
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
        app.notebook.select(3)
        root.update()
        assert '等待：待校验' in app.summary_text
        assert 'private-structure-secret' not in app.summary_text
        candidate = app.validate()
        assert any(issue.path == ('monitor', 'common') and issue.code == 'mapping'
                   for issue in candidate.issues)
        assert app.notebook.index('current') == 0
        assert root.focus_get() is app.pages[0].common_structure_anchor
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
    """通知页切换事件截图开关后进入附件页立即看到新条件。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        event = 'on_timeout'
        app.notebook.select(1)
        app.pages[1].set_field(('push', 'templates', event, 'capture_screenshot'), False)
        app.notebook.select(2)
        root.update()
        assert '事件开关：未满足' in app.pages[2].condition_labels[event].cget('text')
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
        app.pages[2]._add_target('window')
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
        page = app.pages[1]
        path = ('push', 'templates', 'on_end', 'content')
        app.notebook.select(1)
        root.update()
        page.focus_issue(path)
        root.update()
        widget = page.fields[path][1]
        assert isinstance(widget, tk.Text)
        text = ('这是一段用于通知显示的中文长正文。' * 20) + '\n第二段中文内容'
        widget.delete('1.0', 'end')
        widget.insert('1.0', text)
        root.update()
        assert app.model.get(path) == text
        assert root.focus_get() is widget
        assert widget.tk_focusNext() is not widget
        assert str(widget.cget('takefocus')) != '0'
        page.refresh()
        assert widget.get('1.0', 'end-1c') == text
    finally:
        root.destroy()


def test_validation_routes_every_registered_field_to_its_widget(tmp_path):
    """全部静态诊断路径均在校验入口导航到实际字段。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        cases = [(index, path) for index, page in enumerate(app.pages)
                 for path in page.fields]
        assert cases
        for index, path in cases:
            app.model.validate = lambda _, field=path: SimpleNamespace(issues=[
                SimpleNamespace(path=field, severity='error')])
            app.validate()
            root.update()
            assert app.notebook.index('current') == index, path
            assert root.focus_get() is app.pages[index].fields[path][1], path
    finally:
        root.destroy()


@pytest.mark.parametrize('path,page_index,selector_name,entry_name', [
    (('push', 'push_channel_settings', 'channels', 1, 'sckey'), 1, 'channel_list', 'channel_entries'),
    (('push', 'screenshot', 'image_host', 1, 'options'), 2, 'host_list', 'host_entries'),
    (('push', 'screenshot', 'targets', 1, 'out'), 2, 'target_list', 'target_entries'),
])
def test_validation_routes_dynamic_diagnostic_path(
        tmp_path, path, page_index, selector_name, entry_name):
    """列表项诊断从校验入口切到索引并聚焦字段。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(app.pages[1].channels_path, [
            {'provider': 'serverchan', 'sckey': ''} for _ in range(2)])
        app.pages[2]._add_host()
        app.pages[2]._add_target('window')
        app.pages[2]._add_target('adb')
        app.refresh()
        app.model.validate = lambda _: SimpleNamespace(issues=[
            SimpleNamespace(path=path, severity='error')])
        app.validate()
        root.update()
        page = app.pages[page_index]
        assert app.notebook.index('current') == page_index
        assert getattr(page, selector_name).current() == 1
        assert root.focus_get() is getattr(page, entry_name)[path[-1]]
    finally:
        root.destroy()
