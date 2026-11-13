#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""真实 Tk 页面折叠、定位及结果消费测试。"""

import queue
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.config_document import new_document
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
    model = EditorModel(new_document())
    controller = Controller(model, confirm=lambda *_: 'cancel',
                            choose_path=lambda: None, cli_command=('python.exe',),
                            runner=runner, program_dir=tmp_path)
    return App(root, controller)


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
    """未保存配置从程序目录解析，而测试请求不暴露在命令行。"""
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
    """非法控件值不能假装保存成功，明确放弃才允许关闭。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.pages[2]
        page.host_entries['options'].insert(0, '{broken: [}')
        app.controller.confirm = Mock(return_value=answer)
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
