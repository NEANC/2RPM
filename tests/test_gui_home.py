#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Fluent 主页、导航与设置的真实窗口回归。"""

import tkinter as tk
from types import SimpleNamespace

import ctk_kit

from modules.gui.home_summary import summary
from modules.config_document import new_document
from tests.test_gui_pages import make_app


NAMES = ('主页', '监视设置', '截图设置', '消息推送设置',
         '外部程序设置', '日志设置', '设置')


def test_summary_uses_draft_and_redacts():
    """摘要只使用结构状态，草稿内目标和凭证不出现。"""
    config = new_document().tree
    config['monitor']['target'] = 'C:/private/秘密程序.exe'
    config['push']['push_channel_settings']['channels'] = [
        {'provider': 'serverchan', 'sckey': 'private-token'}]
    text, _ = summary(config)
    assert '已配置' in text
    assert '秘密程序' not in text
    assert 'private-token' not in text


def test_all_unmet_conditions_are_shown():
    """事件截图只列出真实缺失项，不把已满足条件列为失败。"""
    config = new_document().tree
    config['push']['templates']['on_end']['capture_screenshot'] = False
    _, details = summary(config)
    end = next(item for item in details if item[0] == '目标结束')
    assert end[1] == '条件未满足'
    assert '事件或事件截图未启用' in end[2]
    assert len(end[2]) >= 2


def test_summary_tolerates_non_mapping_push():
    """push 为标量时所有事件按待校验展示而不崩溃。"""
    config = new_document().tree
    config['push'] = 'broken'
    text, details = summary(config)
    assert '通知通道：待校验' in text
    assert details
    assert all(item[1] == '待校验' for item in details)


def test_summary_tolerates_non_mapping_screenshot():
    """截图节为标量时事件条件按待校验展示而不崩溃。"""
    config = new_document().tree
    config['push']['screenshot'] = 'broken'
    _, details = summary(config)
    end = next(item for item in details if item[0] == '目标结束')
    assert end[1] == '待校验'


def test_sidebar_order_and_home(tmp_path, monkeypatch):
    """七页侧栏必须保持业务编辑入口可达，不把隐藏旧表单当作迁移。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    root = ctk_kit.App(title='测试', remember_geometry=False, remember_theme=False,
                       notifier=False, backdrop='none')
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        assert app.page_names == NAMES
        assert not hasattr(app, 'notebook')
        assert app.current_page == '主页'
        assert app.show_page('监视设置')
        monitor_page = app.visible_pages['监视设置']
        assert ('monitor', 'target') in monitor_page.fields
        assert app.show_page('截图设置')
        screenshot_page = app.visible_pages['截图设置']
        assert screenshot_page.model is app.model
        assert app.model.get(('push', 'screenshot', 'targets')) == []
        assert app.show_page('消息推送设置')
        push_page = app.visible_pages['消息推送设置']
        assert push_page.channels_path == ('push', 'push_channel_settings', 'channels')
        assert app.show_page('主页')
    finally:
        root.destroy()


def test_config_group_starts_open_and_keeps_five_pages_in_order(tmp_path, monkeypatch):
    """配置组默认展开，收起与展开不影响主页和底部设置。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    root = ctk_kit.App(title='测试', remember_geometry=False, remember_theme=False,
                       notifier=False, backdrop='none')
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        names = NAMES[1:-1]
        buttons = app.sidebar._buttons
        group = app.config_group_button
        assert app.sidebar._nav_names == list(NAMES[:-1])
        assert list(app.sidebar._bottom_buttons) == ['设置']
        assert group.cget('text') == '配置  ▾'
        assert app.sidebar.nav_scroll.pack_slaves().index(group) < app.sidebar.nav_scroll.pack_slaves().index(buttons[names[0]])
        assert all(buttons[name].winfo_manager() == 'pack' for name in names)

        group.invoke()
        assert group.cget('text') == '配置  ▸'
        assert all(not buttons[name].winfo_manager() for name in names)
        assert buttons['主页'].winfo_manager() == 'pack'
        assert app.sidebar._bottom_buttons['设置'].winfo_manager() == 'pack'

        group.invoke()
        assert group.cget('text') == '配置  ▾'
        packed = app.sidebar.nav_scroll.pack_slaves()
        assert [widget for widget in packed if widget in [buttons[name] for name in names]] == [buttons[name] for name in names]
        assert app.show_page('日志设置')
        assert app.current_page == '日志设置'
    finally:
        root.destroy()


def test_sidebar_navigation_buttons_use_project_font(tmp_path, monkeypatch):
    """主页、五个配置入口与底部设置均使用项目指定字体。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    root = ctk_kit.App(title='测试', remember_geometry=False, remember_theme=False,
                       notifier=False, backdrop='none')
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        buttons = [app.sidebar._buttons[name] for name in NAMES[:-1]]
        buttons.append(app.sidebar._bottom_buttons['设置'])
        assert all((font[0] if isinstance(font := button.cget('font'), tuple)
                    else font.cget('family')) == 'Microsoft YaHei UI'
                   for button in buttons)
    finally:
        root.destroy()


def test_collapsed_config_group_highlights_current_page(tmp_path, monkeypatch):
    """隐藏选中配置入口时由组标题提示，切页后恢复普通状态。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    root = ctk_kit.App(title='测试', remember_geometry=False, remember_theme=False,
                       notifier=False, backdrop='none')
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        group = app.config_group_button
        assert group.cget('fg_color') == 'transparent'
        assert app.show_page('监视设置')
        selected_color = app.sidebar._buttons['监视设置'].cget('fg_color')
        group.invoke()
        assert app.current_page == '监视设置'
        assert app.sidebar.rebuilder.current == '监视设置'
        assert group.cget('fg_color') == selected_color
        assert app.show_page('主页')
        assert group.cget('fg_color') == 'transparent'
        assert app.show_page('截图设置')
        assert group.cget('fg_color') == selected_color
        group.invoke()
        assert group.cget('fg_color') == 'transparent'
        assert app.show_page('设置')
        group.invoke()
        assert group.cget('fg_color') == 'transparent'
    finally:
        root.destroy()


def test_failed_flush_preserves_page_inputs(tmp_path, monkeypatch):
    """数字草稿未完成时可回主页并再次回原页。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    root = ctk_kit.App(title='测试', remember_geometry=False, remember_theme=False,
                       notifier=False, backdrop='none')
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.model.set_input(('monitor', 'common', 'timeout_threshold'), '-', kind='int')
        assert app.show_page('监视设置')
        assert app.show_page('主页')
        assert app.model.get_input(('monitor', 'common', 'timeout_threshold')) == '-'
        assert '未保存' in app.path_label.cget('text')
    finally:
        root.destroy()


def test_settings_persist_without_editing_business_revision(tmp_path, monkeypatch):
    """设置页切换外观，业务草稿修订不受影响。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    root = ctk_kit.App(title='测试', remember_geometry=False, remember_theme=False,
                       notifier=False, backdrop='none')
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        revision = app.model.revision
        assert app.show_page('设置')
        app.choose_material('none')
        assert app.model.revision == revision
        assert app.visible_pages['设置'].material.get() == '无材质'
    finally:
        root.destroy()


def test_sidebar_business_pages_keep_editing_and_actions(tmp_path, monkeypatch):
    """桥接期各业务入口均能编辑对应字段且测试操作可访问。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    root = ctk_kit.App(title='测试', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        for name, path in (
                ('监视设置', ('monitor', 'target')),
                ('消息推送设置', ('push', 'retry', 'interval')),
                ('外部程序设置', ('external', 'on_end')),
                ('日志设置', ('log', 'log_directory'))):
            assert app.show_page(name)
            page = app.visible_pages[name]
            assert path in page.fields
            assert page.fields[path][1].winfo_exists()
        assert app.show_page('截图设置')
        assert app.visible_pages['截图设置'].days.winfo_exists()
        buttons = [child for header in app.visible_pages['截图设置'].winfo_children()
                   for child in header.winfo_children()]
        assert '截图测试' in [child.cget('text') for child in buttons
                           if isinstance(child, ctk_kit.CTkButton)]
    finally:
        root.destroy()


def test_recreated_ctk_root_keeps_font_resources(tmp_path, monkeypatch):
    """关闭旧窗口后创建新窗口仍可构建侧栏与字体。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    for index in range(2):
        root = ctk_kit.App(title='测试', remember_geometry=False,
                           remember_theme=False, notifier=False, backdrop='none')
        root.withdraw()
        try:
            app = make_app(root, tmp_path / str(index))
            assert app.show_page('监视设置')
        finally:
            root.destroy()


def test_validation_result_invalidated_after_edit(tmp_path, monkeypatch):
    """编辑后旧校验结果失效，回到主页显示尚未校验。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    root = ctk_kit.App(title='测试', remember_geometry=False, remember_theme=False,
                       notifier=False, backdrop='none')
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.validate()
        home = app.visible_pages['主页']
        assert home.check.cget('text') != '尚未校验'
        app.model.set(('monitor', 'target'), 'edited.exe')
        app.refresh()
        assert home.check.cget('text') == '尚未校验'
        assert home.issue_hint.cget('text') == ''
    finally:
        root.destroy()


def test_validation_groups_issues_by_page_and_navigates(tmp_path, monkeypatch):
    """主页按承载页面分组显示问题数与前往入口，且只显示安全类别。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    root = ctk_kit.App(title='测试', remember_geometry=False, remember_theme=False,
                       notifier=False, backdrop='none')
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        issues = [
            SimpleNamespace(path=('monitor', 'launch', 'cwd'), severity='error'),
            SimpleNamespace(path=('push', 'push_channel_settings', 'channels', 0, 'sckey'),
                            severity='error'),
            SimpleNamespace(path=('push', 'screenshot', 'targets', 0, 'target'),
                            severity='warning'),
            SimpleNamespace(path=('external', 'on_timeout'), severity='error'),
            SimpleNamespace(path=('log', 'retention_days'), severity='warning'),
        ]
        app.model.validate = lambda _: SimpleNamespace(issues=issues)
        app.validate()
        home = app.visible_pages['主页']
        assert '存在 5 个问题' in home.check.cget('text')
        rows = home.validation_rows
        assert set(rows) == {'监视设置', '消息推送设置', '截图设置',
                             '外部程序设置', '日志设置'}
        assert '1 个问题' in rows['消息推送设置']['label'].cget('text')
        assert '错误 1' in rows['消息推送设置']['label'].cget('text')
        assert '其他 0' in rows['消息推送设置']['label'].cget('text')
        assert '1 个问题' in rows['截图设置']['label'].cget('text')
        assert '错误 0' in rows['截图设置']['label'].cget('text')
        assert '其他 1' in rows['截图设置']['label'].cget('text')
        assert 'sckey' not in rows['消息推送设置']['label'].cget('text')
        rows['日志设置']['button']._command()
        assert app.current_page == '日志设置'
    finally:
        root.destroy()


def test_close_cancel_keeps_root(tmp_path, monkeypatch):
    """拒绝退出时窗口和草稿仍存在。"""
    monkeypatch.setattr('modules.gui.app.preferences_path', lambda: tmp_path / 'gui.ini')
    root = ctk_kit.App(title='测试', remember_geometry=False, remember_theme=False,
                       notifier=False, backdrop='none')
    root.withdraw()
    try:
        app = make_app(root, tmp_path)
        app.controller.confirm = lambda *_: 'cancel'
        app.close()
        assert root.winfo_exists()
    finally:
        root.destroy()
