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


from tests.gui_support import descendants, make_app


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


def test_notification_event_preview_and_variable_controls_are_visible(tmp_path):
    """通知页须可见预览及插入变量入口，而不依赖内部方法调用。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['消息推送设置']
        page.expand_event('on_end')
        # 展开后的事件卡片是原生 Collapsible，按钮在其标题行和正文区里。
        assert isinstance(page.event_bodies['on_end'], ctk_kit.Collapsible)
        texts = [widget.cget('text') for widget in descendants(page.events_body)
                 if isinstance(widget, ctk_kit.CTkButton)]
        assert '收起消息 ▴' in texts
        assert '编辑消息 ▾' in texts
        assert '预览消息' in texts
        assert '插入正文' in texts
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


def test_chinese_long_template_is_multiline_and_keyboard_reachable(tmp_path):
    """长中文正文可换行编辑，并可通过键盘焦点链抵达。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        root.deiconify()
        page = app.visible_pages['消息推送设置']
        path = ('push', 'templates', 'on_end', 'content')
        app.show_page('消息推送设置')
        root.update()
        page.focus_issue(path)
        root.update()
        widget = page.fields[path][1]
        assert isinstance(widget, ctk_kit.CTkTextbox)
        assert root.winfo_ismapped()
        assert widget.winfo_ismapped() and widget._textbox.winfo_ismapped()
        text = ('这是一段用于通知显示的中文长正文。' * 20) + '\n第二段中文内容'
        widget.delete('1.0', 'end')
        widget.insert('1.0', text)
        widget._textbox.event_generate('<KeyRelease>')
        root.update()
        assert app.model.get(path) == text
        assert root.focus_get() is widget._textbox
        assert widget.tk_focusNext() is not widget
        assert str(widget.cget('takefocus')) != '0'
        page.refresh()
        assert page.fields[path][1].get('1.0', 'end-1c') == text
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


def test_retry_max_count_uses_native_spinner(tmp_path):
    """最大重试次数使用原生 Spinner 并写回整数。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        assert app.show_page('消息推送设置')
        page = app.visible_pages['消息推送设置']
        assert isinstance(page.retry_spinner, ctk_kit.Spinner)
        path = ('push', 'retry', 'max_count')
        page.retry_spinner.set('5')
        page.retry_spinner._notify()
        assert app.model.get(path) == 5
    finally:
        root.destroy()


@pytest.mark.parametrize('event', ['<Return>', '<FocusOut>'])
@pytest.mark.parametrize('raw', ['', '-', '12x', '007', '1000', '-1'])
def test_retry_max_count_native_event_keeps_raw_draft(tmp_path, event, raw):
    """内部输入的真实提交事件保持原文与整数草稿。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['消息推送设置']
        path = ('push', 'retry', 'max_count')
        entry = page.retry_spinner.entry._entry
        entry.delete(0, 'end')
        entry.insert(0, raw)
        root.update()
        before = app.model.get(path)
        entry.event_generate(event)
        root.update()
        assert page.retry_spinner.entry.get() == raw
        assert app.model.get_input(path) == raw
        assert app.model.get(path) == (int(raw) if raw.lstrip('-').isdigit() else before)
        assert (path in app.model.input_errors) == (raw in ('', '-', '12x'))
    finally:
        root.destroy()


@pytest.mark.parametrize(('raw', 'saved'), [('', False), ('-', False),
                                               ('12x', False), ('007', True),
                                               ('1000', True), ('-1', True)])
def test_retry_max_count_save_respects_native_draft(tmp_path, raw, saved):
    """未完成草稿阻断保存，完整越界数字按原值写盘。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['消息推送设置']
        path = ('push', 'retry', 'max_count')
        entry = page.retry_spinner.entry._entry
        entry.delete(0, 'end')
        entry.insert(0, raw)
        entry.event_generate('<Return>')
        root.update()
        assert app.controller.save() is saved
        assert read_document(tmp_path / 'a.yaml').tree['push']['retry']['max_count'] == (
            int(raw) if saved else 3)
        if not saved:
            assert app.model.get_input(path) == raw
    finally:
        root.destroy()


@pytest.mark.parametrize(('raw', 'y', 'expected'), [
    ('007', 1, '8'), ('7', 30, '6'), ('99', 1, '99'),
    ('0', 30, '0'), ('1000', 1, '99'), ('-1', 30, '0'),
    ('', 1, ''), ('-', 1, '-'), ('12x', 1, '12x'),
])
def test_retry_max_count_arrow_steps_only_valid_integer(tmp_path, raw, y, expected):
    """真实箭头只步进完整整数，且上下界不越界。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['消息推送设置']
        path = ('push', 'retry', 'max_count')
        entry = page.retry_spinner.entry._entry
        entry.delete(0, 'end')
        entry.insert(0, raw)
        root.update()
        before = app.model.get(path)
        page.retry_spinner._arrows.event_generate('<Button-1>', x=5, y=y)
        root.update()
        assert page.retry_spinner.entry.get() == expected
        assert app.model.get_input(path) == expected
        assert app.model.get(path) == (before if raw in ('', '-', '12x') else int(expected))
    finally:
        root.destroy()


def test_retry_max_count_oversized_draft_survives_arrow_and_blocks_save(tmp_path):
    """超长数字点击真实箭头不报错、不覆盖原文，并阻断落盘。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['消息推送设置']
        path = ('push', 'retry', 'max_count')
        raw = '9' * 4301
        errors = []
        root.report_callback_exception = lambda *args: errors.append(args)
        entry = page.retry_spinner.entry._entry
        entry.delete(0, 'end')
        entry.insert(0, raw)
        root.update()
        page.retry_spinner._arrows.event_generate('<Button-1>', x=5, y=1)
        root.update()

        assert not errors
        assert entry.get() == raw
        assert app.model.get_input(path) == raw
        assert app.model.get(path) == 3
        assert path in app.model.input_errors
        assert not app.controller.save()
        assert read_document(tmp_path / 'a.yaml').tree['push']['retry']['max_count'] == 3
    finally:
        root.destroy()


def test_retry_max_count_refresh_keeps_entry_and_cursor(tmp_path):
    """切页刷新保持未完成原文与活动输入框的光标。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['消息推送设置']
        entry = page.retry_spinner.entry._entry
        entry.delete(0, 'end')
        entry.insert(0, '12x')
        entry.icursor(1)
        root.update()
        app.show_page('监视设置')
        app.show_page('消息推送设置')
        root.update()
        assert page.retry_spinner.entry._entry is entry
        assert entry.get() == '12x'
        assert entry.index('insert') == 1
        assert app.model.get_input(('push', 'retry', 'max_count')) == '12x'
    finally:
        root.destroy()
