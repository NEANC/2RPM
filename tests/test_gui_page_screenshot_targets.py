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


def test_real_app_target_type_switch_validates_without_replacing_target(tmp_path):
    """真实 App 切换目标类型后按新类型校验，同时保留原目标。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        page = app.visible_pages['监视设置']
        page.set_field(('monitor', 'target'), r'C:\Apps\demo.exe')
        target = app.model.get(('monitor', 'target'))
        assert not any(issue.code == 'gui_file_path'
                       for issue in app.model.validate(tmp_path).issues)

        page._select_type('计划任务')
        assert app.model.get(('monitor', 'target')) == target
        assert any(issue.code == 'gui_task_path'
                   for issue in app.model.validate(tmp_path).issues)

        page._select_type('程序文件')
        assert app.model.get(('monitor', 'target')) == target
        assert not any(issue.code in ('gui_task_path', 'gui_file_path')
                       for issue in app.model.validate(tmp_path).issues)
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


def test_six_image_hosts_edit_and_reorder(tmp_path):
    """六个图床编辑基础字段并重排时保留隐藏的高级字段。"""
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
        hosts = app.model.get(path)
        hosts[3]['expiration'] = '7d'
        hosts[3]['options'] = {'storage_id': 12}
        app.model.set(path, hosts)
        page.refresh()
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
        entry.set('B-edited', notify=True)
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
        port.entry.set('16385')
        targets = app.model.get(path)
        assert targets[0] == 'bad'
        assert targets[1]['target'] == '127.0.0.1:16385'
    finally:
        root.destroy()


def test_open_monitor_mode_list_refreshes_home_without_crash(tmp_path):
    """打开模式为列表的配置后主页显示待校验。"""
    root = tk.Tk()
    try:
        app = make_app(root, tmp_path)
        path = tmp_path / 'monitor-mode-list.yaml'
        path.write_text('monitor:\n  mode: [watch]\n  target: demo.exe\n'
                        'push: {}\nlog: {}\nexternal: {}\n', encoding='utf-8')
        assert app.controller.open(path)
        assert app.show_page('主页')
        assert '运行方式：待校验' in app.summary_text
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
