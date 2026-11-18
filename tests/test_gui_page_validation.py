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
        assert root.focus_get() is page.fields[path][1]._entry
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


def test_host_advanced_credentials_are_not_rendered(tmp_path):
    """高级选项中的凭证不出现在基础行，普通凭证仍被遮挡。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        path = ('push', 'screenshot', 'image_host')
        hosts = app.model.get(path)
        hosts[0]['options'] = {'api_key': 'demo'}
        app.model.set(path, hosts)
        page.refresh()
        assert len(page.hosts_body.winfo_children()) == len(page.host_rows)
        assert page.host_rows[0]['token'].cget('show') == '●'
        assert 'options' not in page.host_rows[0]
        assert app.model.get(path)[0]['options'] == {'api_key': 'demo'}
    finally:
        root.destroy()


def test_advanced_summary_never_contains_secret(tmp_path):
    """折叠标签只显示安全字段名称，不能显示凭证原文。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('push', 'retry', 'interval'), 'private-98765')
        app.refresh()
        label = app.visible_pages['主页'].summary_text
        assert '已配置' in label
        assert 'private-98765' not in label
    finally:
        root.destroy()


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
        assert '目标结束' in page.screenshot_rows
        assert page.screenshot_rows['目标结束']['reasons'] is not None
    finally:
        root.destroy()


def test_validation_routes_external_error_to_action_field(tmp_path):
    """外部动作诊断从校验入口导航至附件页而非检查页。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = ('external', 'on_timeout')
        root.update()
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
    """主页分组显示事件与动作状态，但不回显用户输入或凭证。"""
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
        home = app.visible_pages['主页']
        events = [tuple(label.cget('text') for label in row.winfo_children())
                  for row in home.groups['通知事件'].body.winfo_children()]
        actions = [tuple(label.cget('text') for label in row.winfo_children())
                   for row in home.groups['外部程序'].body.winfo_children()]
        assert '运行方式：主动启动并监视' in summary
        assert ('目标结束', '关闭') in events
        assert ('外部程序执行通知', '启用') in events
        assert '事件截图' in home.groups
        assert ('目标结束外部程序', '已配置') in actions
        assert ('运行超时外部程序', '未配置') in actions
        displayed = summary + str(events) + str(actions) + str(
            [group.detail for group in home.groups.values()])
        assert 'private-' not in displayed
        assert 'SCTxxxx' not in displayed
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
        home = app.visible_pages['主页']
        event = 'on_end'
        assert not all(screenshot_conditions(app.model.snapshot(), event).values())
        before = home.groups['事件截图'].detail
        assert home.screenshot_rows['目标结束']['reasons'].detail == '条件未满足'
        app.model.set(('monitor', 'target'), 'demo.exe')
        app.model.set(('push', 'templates', event, 'content'), '{screenshot}')
        app.model.set(('push', 'push_channel_settings', 'channels'),
                      [{'provider': 'serverchan', 'sckey': 'private-key'}])
        app.visible_pages['截图设置'].add_target()
        app.visible_pages['截图设置']._set_item(('push', 'screenshot', 'targets'), 0, 'target', '测试窗口')
        app.refresh()
        assert all(screenshot_conditions(app.model.snapshot(), event).values())
        assert home.screenshot_rows['目标结束']['reasons'] is None
        assert home.groups['事件截图'].detail != before
        row = home.groups['事件截图'].body.winfo_children()[0]
        assert [label.cget('text') for label in row.winfo_children()] == ['目标结束', '条件满足']
        assert 'private-key' not in app.summary_text
        assert 'private-key' not in home.groups['事件截图'].detail
        assert 'private-key' not in str([label.cget('text') for label in row.winfo_children()])
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


def test_screenshot_conditions_update_on_tab_switch(tmp_path, monkeypatch):
    """切页后主页新增截图禁用原因，且可见文本不泄露配置值。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        event = 'on_timeout'
        path = ('push', 'templates', event, 'capture_screenshot')
        secret = 'private-target-secret'
        app.model.set(('monitor', 'target'), secret)
        app.show_page('主页')
        home = app.visible_pages['主页']
        before = tuple(label.cget('text') for label in
                       home.screenshot_rows['运行超时']['reasons'].body.winfo_children())
        assert before
        assert '事件或事件截图未启用' not in ''.join(before)
        app.show_page('消息推送设置')
        page = app.visible_pages['消息推送设置']
        page.expand_event(event)
        assert not hasattr(page, 'capture_switches')
        assert path not in page.fields
        monkeypatch.setattr(app.controller, 'on_saved', lambda: None)
        app.model.set(path, False)
        page.preview_event(event)
        assert '截图被阻止' in page.preview_labels[event].cget('text')
        app.show_page('截图设置')
        root.update()
        unchanged = tuple(label.cget('text') for label in
                          home.screenshot_rows['运行超时']['reasons'].body.winfo_children())
        assert unchanged == before
        app.show_page('主页')
        after = tuple(label.cget('text') for label in
                      home.screenshot_rows['运行超时']['reasons'].body.winfo_children())
        assert set(after) == set(before) | {'· 事件或事件截图未启用'}
        assert secret not in ''.join(after)
        assert secret not in home.groups['事件截图'].detail
        assert home.screenshot_rows['运行超时']['reasons'].detail == '条件未满足'
        assert home.groups['事件截图'].detail == '0 个条件满足 / 4 个'
        assert app.model.get(path) is False
    finally:
        root.destroy()


def test_validation_routes_every_registered_field_to_its_widget(tmp_path):
    """全部静态诊断路径均在校验入口导航到实际字段。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        root.update()
        pages = [app.visible_pages[name] for name in
                 ('监视设置', '消息推送设置', '截图设置', '日志设置')]
        cases = [(index, path) for index, page in enumerate(pages)
                 for path in getattr(page, 'fields', {})]
        assert cases
        home = app.visible_pages['主页']
        button = home.validate_button
        for index, path in cases:
            app.show_page('主页')
            root.focus_force()
            app.model.validate = lambda _, field=path: SimpleNamespace(issues=[
                SimpleNamespace(path=field, severity='error')])
            button.invoke()
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
                if isinstance(widget, ctk_kit.CTkTextbox):
                    target = widget._textbox
                else:
                    target = widget
                    while True:
                        inner = (getattr(target, '_entry', None)
                                 or getattr(target, 'entry', None))
                        if inner is None or inner is target:
                            break
                        target = inner
                assert root.focus_get() is target, path
    finally:
        root.destroy()
