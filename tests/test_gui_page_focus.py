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


def test_hidden_host_options_roundtrip_after_visible_edit(tmp_path):
    """旧图床高级字段从磁盘打开、编辑基础字段并重开后保持原值。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = ('push', 'screenshot', 'image_host')
        hosts = app.model.get(path)
        hosts[0].update({'options': {'api_key': 'hidden', 'enabled': False},
                         'expiration': '7d', 'custom': {'nested': 12}})
        app.model.set(path, hosts)
        assert app.controller.save()
        app.model.replace_document(read_document(app.model.document.path))
        page = app.visible_pages['截图设置']
        page.refresh()
        assert len(page.hosts_body.winfo_children()) == len(page.host_rows)
        assert 'options' not in page.host_rows[0]
        page.host_rows[0]['token'].insert('end', 'updated')
        assert app.controller.save()
        assert read_document(app.model.document.path).tree['push']['screenshot']['image_host'][0] == {
            **hosts[0], 'token': 'updated'}
    finally:
        root.destroy()


def test_hidden_host_options_do_not_block_reorder(tmp_path):
    """磁盘中的高级选项随整条图床移动，不需要恢复旧选项输入框。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['截图设置']
        path = ('push', 'screenshot', 'image_host')
        hosts = app.model.get(path)
        hosts[0]['options'] = {'album_id': 12}
        hosts.append({'provider': 'boltp', 'token': '', 'options': {'storage_id': 3}})
        app.model.set(path, hosts)
        page.refresh()
        page.move_host(0, 1)
        assert app.model.get(path) == [hosts[1], hosts[0]]
        assert read_document(app.model.document.path).tree['push']['screenshot']['image_host'] == [
            hosts[1], hosts[0]]
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


def test_validation_navigation_focuses_monitor_target_entry(tmp_path):
    """点击主页真实校验按钮并处理完整事件后目标输入框获得焦点。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        root.update()
        page = app.visible_pages['监视设置']
        home = app.visible_pages['主页']
        app.model.validate = lambda _: SimpleNamespace(issues=[
            SimpleNamespace(path=('monitor', 'target'), severity='error')])
        button = home.validate_button
        button.invoke()
        root.update()
        assert app.current_page == '监视设置'
        assert root.focus_get() is page.fields[('monitor', 'target')][1]._entry
    finally:
        root.destroy()


@pytest.mark.parametrize('raw', ['', '12x', '007'])
def test_retry_diagnostic_focuses_inner_entry_without_losing_raw_input(tmp_path, raw):
    """重试次数诊断落在真实输入框，刷新和重复导航不覆盖原文。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['消息推送设置']
        path = ('push', 'retry', 'max_count')
        entry = page.retry_spinner.entry._entry
        app.show_page('消息推送设置')
        root.update()
        entry.delete(0, 'end')
        entry.insert(0, raw)
        root.update()
        assert app.model.get_input(path) == raw
        issue = SimpleNamespace(path=path, severity='error')
        for _ in range(2):
            app.focus_validation_issue('消息推送设置', issue)
            root.update()
            assert root.focus_get() is entry
            assert page.fields[path][1] is entry
            assert entry.get() == raw
            assert app.model.get_input(path) == raw
    finally:
        root.destroy()


def test_hidden_push_diagnostic_only_offers_yaml_hint(tmp_path):
    """隐藏的截图通知高级字段仍仅给出 YAML 提示。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.focus_validation_issue('消息推送设置', SimpleNamespace(
            path=('push', 'templates', 'on_end', 'capture_screenshot')))
        assert app.current_page == '消息推送设置'
        assert 'YAML' in app.status.cget('text')
    finally:
        root.destroy()


def test_external_path_field_input_clear_and_real_focus(tmp_path):
    """内部输入事件写回模型，清空及换文档不恢复旧路径或旧按钮。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.show_page('外部程序设置')
        root.update()
        page = app.visible_pages['外部程序设置']
        path = ('external', 'on_end')
        field = page.path_fields[path]
        entry = field.entry._entry
        page.focus_issue(path)
        root.update()
        assert root.focus_get() is entry
        entry.delete(0, 'end')
        entry.insert(0, 'C:\\task.bat')
        assert app.model.get(path) != 'C:\\task.bat'
        entry.event_generate('<KeyRelease>', keysym='t')
        root.update()
        assert app.model.get(path) == 'C:\\task.bat'
        assert field.get() == 'C:\\task.bat'
        page.clear_file('on_end')
        assert field.get() == ''
        assert app.model.get(path) == ''
        app.model.replace_document(new_document())
        page.refresh()
        assert field.get() == app.model.get(path)
        assert field.get() != 'C:\\task.bat'
        assert not hasattr(page, 'restore_buttons')
        assert not hasattr(page, 'restore_file')
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


def test_external_diagnostic_focus_survives_unmapped_window(tmp_path):
    """窗口暂不可见时导航外部动作，显示后真实输入框取得焦点。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        root.update()
        root.withdraw()
        path = ('external', 'on_timeout')
        app.model.validate = lambda _: SimpleNamespace(issues=[
            SimpleNamespace(path=path, severity='error')])
        app.validate()
        root.deiconify()
        root.update()
        root.after(250, root.quit)
        root.mainloop()
        assert app.current_page == '外部程序设置'
        assert root.focus_get() is app.visible_pages['外部程序设置'].fields[path][1]._entry
    finally:
        root.destroy()


def test_external_diagnostic_focus_after_ctk_app_restore(tmp_path, monkeypatch):
    """根窗口 Map 后才排程 120 ms，恢复后真实诊断字段取得焦点。"""
    root = ctk_kit.App(title='焦点测试', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        root.update()
        root.withdraw()
        path = ('external', 'on_timeout')
        from modules.gui.pages.external import Page

        scheduled = []
        maps = []
        original_after = Page.after

        def record_after(self, delay, callback=None, *args):
            """记录诊断焦点排程，保留真实 Tk 计时器。"""
            if delay == 120:
                scheduled.append((len(maps), callback))
            return original_after(self, delay, callback, *args)

        monkeypatch.setattr(Page, 'after', record_after)
        root.bind('<Map>', lambda event: maps.append(event.widget)
                  if event.widget is root else None, add='+')
        app.focus_validation_issue('外部程序设置', SimpleNamespace(path=path))
        page = app.visible_pages['外部程序设置']
        assert scheduled == []
        root.deiconify()
        root.update()
        assert maps == [root]
        assert len(scheduled) == 1
        assert scheduled[0][0] == 1
        assert callable(scheduled[0][1])
        root.after(250, root.quit)
        root.mainloop()
        assert root.focus_get() is page.fields[path][1]._entry
    finally:
        root.destroy()


def test_external_hidden_navigation_only_focuses_latest_target(tmp_path):
    """隐藏期间连续导航不让旧目标短暂或最终夺回焦点。"""
    root = ctk_kit.App(title='焦点测试', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        app.show_page('外部程序设置')
        root.update()
        root.withdraw()
        page = app.visible_pages['外部程序设置']
        old = page.fields[('external', 'on_end')][1]._entry
        latest = page.fields[('external', 'on_timeout')][1]._entry
        old_focus = []
        old.bind('<FocusIn>', lambda event: old_focus.append(event.widget), add='+')
        app.focus_validation_issue('外部程序设置',
                                   SimpleNamespace(path=('external', 'on_end')))
        app.focus_validation_issue('外部程序设置',
                                   SimpleNamespace(path=('external', 'on_timeout')))
        root.deiconify()
        root.update()
        root.after(250, root.quit)
        root.mainloop()
        assert root.focus_get() is latest
        assert old_focus == []
    finally:
        root.destroy()


@pytest.mark.parametrize('destination', ['主页', '监视设置', '隐藏'])
def test_external_pending_focus_does_not_steal_after_navigation_or_remap(
        tmp_path, destination, monkeypatch):
    """真实窗口恢复后切页或再次隐藏时，旧诊断延迟回调不夺焦。"""
    root = ctk_kit.App(title='焦点测试', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        app.show_page('外部程序设置')
        root.update()
        root.withdraw()
        page = app.visible_pages['外部程序设置']
        stale = page.fields[('external', 'on_timeout')][1]
        focus = Mock(wraps=stale.focus_force)
        monkeypatch.setattr(stale, 'focus_force', focus)
        app.focus_validation_issue('外部程序设置',
                                   SimpleNamespace(path=('external', 'on_timeout')))
        root.deiconify()
        root.update_idletasks()
        assert root.winfo_viewable()

        if destination == '隐藏':
            root.after(30, root.withdraw)
        else:
            app.show_page(destination)
            root.after(30, root.withdraw)
            root.after(55, root.deiconify)

        root.after(250, root.quit)
        root.mainloop()
        focus.assert_not_called()
        if destination != '隐藏':
            assert app.current_page == destination
    finally:
        root.destroy()


def test_external_pending_focus_schedules_once_across_repeated_maps(tmp_path, monkeypatch):
    """重复映射不重新注册旧诊断焦点排程。"""
    root = ctk_kit.App(title='焦点测试', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    try:
        app = make_app(root, tmp_path)
        app.show_page('外部程序设置')
        root.update()
        root.withdraw()
        page = app.visible_pages['外部程序设置']
        scheduled = []
        original_after = page.after

        def record_after(delay, callback=None, *args):
            """统计真实 Map 后的诊断排程次数。"""
            if delay == 120:
                scheduled.append(callback)
            return original_after(delay, callback, *args)

        monkeypatch.setattr(page, 'after', record_after)
        app.focus_validation_issue('外部程序设置',
                                   SimpleNamespace(path=('external', 'on_timeout')))
        assert scheduled == []
        root.deiconify()
        root.update_idletasks()
        root.update()
        assert len(scheduled) == 1
        root.withdraw()
        root.update()
        root.deiconify()
        root.update()
        assert len(scheduled) == 1
        root.after(250, root.quit)
        root.mainloop()
        assert len(scheduled) == 1
    finally:
        root.destroy()


@pytest.mark.parametrize('path,group,category,names', [
    (('external',), '外部程序', '外部程序配置',
     ('目标结束外部程序', '运行超时外部程序', '等待启动超时外部程序')),
    (('push', 'templates'), '通知事件', '通知事件',
     ('目标结束', '运行超时', '等待启动超时', '外部程序执行通知')),
])
def test_review_invalid_section_refresh_and_real_issue_navigation(tmp_path, path,
                                                                  group, category, names):
    """非映射动作或模板在对应分组待校验，诊断安全且可导航。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        app.model.set(('monitor', 'target'), 'demo.exe')
        app.model.set(path, 'private-structure-secret')
        app.refresh()
        home = app.visible_pages['主页']
        states = [tuple(label.cget('text') for label in row.winfo_children())
                  for row in home.groups[group].body.winfo_children()]
        assert states == [(name, '待校验') for name in names]
        assert 'private-structure-secret' not in (
            app.summary_text + str(states) + home.groups[group].detail)
        candidate = app.validate()
        assert any(issue.path == path and issue.code == 'mapping'
                   for issue in candidate.issues)
        assert app.current_page == '主页'
        assert '配置结构' in home.issue_hint.cget('text')
        row = home.validation_rows['主页']
        assert any(category in item['label'].cget('text') for item in row['issues'])
        displayed = (row['label'].cget('text') + home.check.cget('text')
                     + home.issue_hint.cget('text')
                     + str([item['label'].cget('text') for item in row['issues']]))
        assert 'private-structure-secret' not in displayed
        entry = next(item for item in row['issues']
                     if category in item['label'].cget('text'))
        app.show_page('监视设置')
        entry['button'].invoke()
        assert app.current_page == '主页'
        assert '配置结构' in home.issue_hint.cget('text')
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


def test_validation_focus_survives_window_deactivation(tmp_path):
    """主页校验定位后，主窗重新激活时恢复目标输入焦点并接收键盘输入。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        root.update()
        home = app.visible_pages['主页']
        button = home.validate_button
        path = ('monitor', 'target')
        app.model.validate = lambda _: SimpleNamespace(issues=[
            SimpleNamespace(path=path, severity='error')])
        button.invoke()
        root.update()
        target = app.visible_pages['监视设置'].fields[path][1]._entry
        other = tk.Toplevel(root)
        root.update()
        other.focus_force()
        root.update()
        assert root.focus_get() is other
        other.destroy()
        root.update()
        assert root.focus_get() is target
        before = target.get()
        target.event_generate('<KeyPress-a>')
        root.update()
        assert target.get() == before + 'a'
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
            assert page.host_rows[1]['number'].winfo_exists()
        else:
            assert page.target_rows[1]['entry'].winfo_exists()
    finally:
        root.destroy()
