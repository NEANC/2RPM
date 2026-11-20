#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""外部动作与日志页可见字段回写验证。"""

import shutil
import sys
import tkinter as tk
from unittest.mock import Mock

import ctk_kit
import pytest

from modules.config_document import new_document
from modules.gui.model import EditorModel
from tests.gui_support import make_app
from tests.gui_support import arrow_y


def test_external_page_edits_paths_and_threshold_without_running(monkeypatch):
    """选择或清除程序仅更新对应草稿，阈值按 monitor 字段保存。"""
    from modules.gui.pages.external import Page

    root = tk.Tk()
    tk._default_root = root
    model = EditorModel(new_document())
    model.set(('external', 'on_end'), r'C:\existing.bat')
    app = Mock(model=model)
    page = Page(root, app)
    try:
        assert isinstance(page, ctk_kit.CTkScrollableFrame)
        assert page.fields[('external', 'on_end')][1].get() == r'C:\existing.bat'
        monkeypatch.setattr('modules.gui.pages.external.filedialog.askopenfilename',
                            lambda **_: r'C:\new.bat')
        page.choose_file('on_end')
        assert model.get(('external', 'on_end')) == r'C:\new.bat'
        page.clear_file('on_end')
        assert model.get(('external', 'on_end')) == ''
        threshold = ('monitor', 'common', 'timeout_threshold')
        spinner = page.fields[threshold][0]
        assert isinstance(spinner, ctk_kit.Spinner)
        spinner.entry.delete(0, 'end')
        spinner.entry.insert(0, '-')
        spinner.entry._entry.event_generate('<Return>')
        root.update()
        assert model.get_input(threshold) == '-'
        assert ('external', 'timeout_threshold') not in model.snapshot()['external']
        app.start_cli.assert_not_called()
    finally:
        root.destroy()


@pytest.mark.parametrize('event', ['<Return>', '<FocusOut>'])
@pytest.mark.parametrize('raw', ['', '-', '12x', '007', '100', '-1'])
def test_external_threshold_real_event_preserves_raw(event, raw):
    """阈值内部输入事件保留草稿且只转换完整整数。"""
    from modules.gui.pages.external import Page, THRESHOLD

    root = tk.Tk()
    model = EditorModel(new_document())
    page = Page(root, Mock(model=model))
    try:
        entry = page.spinners[THRESHOLD].entry._entry
        before = model.get(THRESHOLD)
        entry.delete(0, 'end')
        entry.insert(0, raw)
        root.update()
        entry.event_generate(event)
        root.update()
        assert entry.get() == raw
        assert model.get_input(THRESHOLD) == raw
        assert model.get(THRESHOLD) == (int(raw) if raw.lstrip('-').isdigit() else before)
        assert (THRESHOLD in model.input_errors) == (raw in ('', '-', '12x'))
    finally:
        root.destroy()


@pytest.mark.parametrize('path', [('log', 'max_log_files'), ('log', 'retention_days')])
@pytest.mark.parametrize('event', ['<Return>', '<FocusOut>'])
@pytest.mark.parametrize('raw', ['', '-', '12x', '007', '10000', '-1'])
def test_logs_number_real_event_preserves_raw(path, event, raw):
    """两个日志数值在真实提交事件下均保留原文。"""
    from modules.gui.pages.logs import Page

    root = tk.Tk()
    model = EditorModel(new_document())
    page = Page(root, Mock(model=model, open_logs=Mock()))
    try:
        entry = page.spinners[path].entry._entry
        before = model.get(path)
        entry.delete(0, 'end')
        entry.insert(0, raw)
        root.update()
        entry.event_generate(event)
        root.update()
        assert entry.get() == raw
        assert model.get_input(path) == raw
        assert model.get(path) == (int(raw) if raw.lstrip('-').isdigit() else before)
        assert (path in model.input_errors) == (raw in ('', '-', '12x'))
    finally:
        root.destroy()


@pytest.mark.parametrize(('path', 'top'), [
    (('log', 'max_log_files'), 9999), (('log', 'retention_days'), 3650),
])
@pytest.mark.parametrize(('raw', 'direction', 'expected'), [
    ('007', 'up', '8'), ('7', 'down', '6'), ('', 'up', ''),
    ('-', 'up', '-'), ('12x', 'up', '12x'),
])
def test_logs_number_real_arrow(path, top, raw, direction, expected):
    """日志箭头只操作可解析整数且不替换非法草稿。"""
    from modules.gui.pages.logs import Page

    root = tk.Tk()
    model = EditorModel(new_document())
    page = Page(root, Mock(model=model, open_logs=Mock()))
    try:
        spinner = page.spinners[path]
        entry = spinner.entry._entry
        entry.delete(0, 'end')
        entry.insert(0, raw)
        root.update()
        before = model.get(path)
        spinner._arrows.event_generate(
            '<Button-1>', x=5, y=arrow_y(spinner, direction))
        root.update()
        assert entry.get() == expected
        assert model.get_input(path) == expected
        assert model.get(path) == (before if raw in ('', '-', '12x') else int(expected))
    finally:
        root.destroy()


@pytest.mark.parametrize(('path', 'top'), [
    (('log', 'max_log_files'), 9999), (('log', 'retention_days'), 3650),
])
@pytest.mark.parametrize('upper', [True, False])
def test_logs_number_arrow_stays_in_range(path, top, upper):
    """日志数值的主动步进不越过各自上下界。"""
    from modules.gui.pages.logs import Page

    root = tk.Tk()
    model = EditorModel(new_document())
    page = Page(root, Mock(model=model, open_logs=Mock()))
    try:
        spinner = page.spinners[path]
        spinner.entry._entry.delete(0, 'end')
        spinner.entry._entry.insert(0, str(top if upper else 1))
        root.update()
        spinner._arrows.event_generate(
            '<Button-1>', x=5, y=arrow_y(spinner, 'up' if upper else 'down'))
        root.update()
        assert spinner.entry.get() == str(top if upper else 1)
        assert model.get(path) == (top if upper else 1)
    finally:
        root.destroy()


@pytest.mark.parametrize('path', [('log', 'max_log_files'), ('log', 'retention_days')])
@pytest.mark.parametrize('direction', ['up', 'down'])
def test_logs_number_long_integer_arrow(path, direction):
    """过长整数在真实箭头操作下保留草稿并且不抛错。"""
    from modules.gui.pages.logs import Page

    previous = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    root = None
    try:
        root = tk.Tk()
        model = EditorModel(new_document())
        page = Page(root, Mock(model=model, open_logs=Mock()))
        spinner = page.spinners[path]
        raw = '9' * 641
        errors = []
        root.report_callback_exception = lambda *error: errors.append(error)
        spinner.entry._entry.delete(0, 'end')
        spinner.entry._entry.insert(0, raw)
        root.update()
        before = model.get(path)
        spinner._arrows.event_generate(
            '<Button-1>', x=5, y=arrow_y(spinner, direction))
        root.update()
        assert not errors
        assert spinner.entry.get() == raw
        assert model.get_input(path) == raw
        assert model.get(path) == before
        assert path in model.input_errors
    finally:
        if root is not None:
            root.destroy()
        sys.set_int_max_str_digits(previous)


@pytest.mark.parametrize('path', [('log', 'max_log_files'), ('log', 'retention_days')])
@pytest.mark.parametrize(('raw', 'saved'), [
    ('', False), ('-', False), ('12x', False), ('007', True),
    ('10000', True), ('-1', True), pytest.param('9' * 4301, False, id='超长整数'),
])
def test_logs_number_save_preserves_draft(tmp_path, path, raw, saved):
    """日志数值未完成时不落盘，完整非法值留给业务校验。"""
    from modules.config_document import read_document
    from modules.gui.pages.logs import Page
    from tests.test_gui_controller import make_controller

    root = tk.Tk()
    controller, _, _ = make_controller(tmp_path)
    page = Page(root, Mock(model=controller.model, open_logs=Mock()))
    try:
        entry = page.spinners[path].entry._entry
        entry.delete(0, 'end')
        entry.insert(0, raw)
        entry.event_generate('<Return>')
        root.update()
        assert controller.save() is saved
        assert (tmp_path / 'a.yaml').exists() is saved
        assert entry.get() == raw
        assert controller.model.get_input(path) == raw
        if saved:
            assert read_document(tmp_path / 'a.yaml').tree['log'][path[1]] == int(raw)
        else:
            assert path in controller.model.input_errors
    finally:
        root.destroy()


@pytest.mark.parametrize(('raw', 'direction', 'expected'), [
    ('007', 'up', '8'), ('7', 'down', '6'), ('99', 'up', '99'),
    ('1', 'down', '1'), ('', 'up', ''), ('-', 'up', '-'), ('12x', 'up', '12x'),
    ('100', 'up', '99'),
])
def test_external_threshold_real_arrow(raw, direction, expected):
    """真实箭头仅步进完整整数，且限制主动步进范围。"""
    from modules.gui.pages.external import Page, THRESHOLD

    root = tk.Tk()
    model = EditorModel(new_document())
    page = Page(root, Mock(model=model))
    try:
        spinner = page.spinners[THRESHOLD]
        entry = spinner.entry._entry
        entry.delete(0, 'end')
        entry.insert(0, raw)
        root.update()
        before = model.get(THRESHOLD)
        spinner._arrows.event_generate(
            '<Button-1>', x=5, y=arrow_y(spinner, direction))
        root.update()
        assert entry.get() == expected
        assert model.get_input(THRESHOLD) == expected
        assert model.get(THRESHOLD) == (before if raw in ('', '-', '12x') else int(expected))
    finally:
        root.destroy()


@pytest.mark.parametrize('direction', ['up', 'down'])
def test_external_threshold_long_integer_arrow(direction):
    """超出 Python 转换位数的草稿点击箭头不丢失或抛错。"""
    from modules.gui.pages.external import Page, THRESHOLD

    previous = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    root = None
    try:
        root = tk.Tk()
        model = EditorModel(new_document())
        page = Page(root, Mock(model=model))
        spinner = page.spinners[THRESHOLD]
        raw = '9' * 641
        errors = []
        root.report_callback_exception = lambda *error: errors.append(error)
        spinner.entry._entry.delete(0, 'end')
        spinner.entry._entry.insert(0, raw)
        root.update()
        before = model.get(THRESHOLD)
        spinner._arrows.event_generate(
            '<Button-1>', x=5, y=arrow_y(spinner, direction))
        root.update()
        assert not errors
        assert spinner.entry.get() == raw
        assert model.get_input(THRESHOLD) == raw
        assert model.get(THRESHOLD) == before
        assert THRESHOLD in model.input_errors
    finally:
        if root is not None:
            root.destroy()
        sys.set_int_max_str_digits(previous)


@pytest.mark.parametrize(('raw', 'saved'), [('', False), ('-', False),
                                               ('12x', False), ('007', True),
                                               ('100', True), ('-1', True),
                                               pytest.param('9' * 4301, False,
                                                            id='超长整数')])
def test_external_threshold_save_preserves_draft(tmp_path, raw, saved):
    """未完成数字阻断写盘，完整越界整数按原值写盘。"""
    from modules.config_document import read_document
    from modules.gui.pages.external import Page, THRESHOLD
    from tests.test_gui_controller import make_controller

    root = tk.Tk()
    controller, _, _ = make_controller(tmp_path)
    page = Page(root, Mock(model=controller.model))
    try:
        entry = page.spinners[THRESHOLD].entry._entry
        entry.delete(0, 'end')
        entry.insert(0, raw)
        entry.event_generate('<Return>')
        root.update()
        assert controller.save() is saved
        assert (tmp_path / 'a.yaml').exists() is saved
        assert entry.get() == raw
        assert controller.model.get_input(THRESHOLD) == raw
        if saved:
            assert read_document(tmp_path / 'a.yaml').tree['monitor']['common']['timeout_threshold'] == int(raw)
        else:
            assert THRESHOLD in controller.model.input_errors
    finally:
        root.destroy()


def test_external_path_label_above_native_path_field():
    """三个事件的路径标签置于原生 PathField 上方，浏览按钮由控件自带。"""
    from modules.gui.pages.external import EVENTS, Page

    root = tk.Tk()
    try:
        page = Page(root, Mock(model=EditorModel(new_document())))
        page.pack(fill='both', expand=True)
        root.geometry('900x700')
        root.update()
        for event, _title, _hint in EVENTS:
            field = page.path_fields[('external', event)]
            assert isinstance(field, ctk_kit.PathField)
            card = field.master
            labels = [child for child in card.winfo_children()
                      if isinstance(child, ctk_kit.CTkLabel)
                      and child.cget('text') == '外部程序／BAT 路径']
            assert len(labels) == 1
            label = labels[0]
            assert label.winfo_rooty() + label.winfo_height() <= field.winfo_rooty()
            assert field.button.winfo_manager()
            assert field.button.winfo_rootx() > field.entry.winfo_rootx()
    finally:
        root.destroy()


def test_page_refresh_does_not_write_path_or_number_fields():
    """程序刷新两个页面时，原生路径框与步进框的回调不得编辑草稿。"""
    from modules.gui.pages.external import Page as ExternalPage
    from modules.gui.pages.logs import Page as LogsPage

    root = tk.Tk()
    try:
        model = EditorModel(new_document())
        model.set(('external', 'on_end'), 'C:/keep.bat')
        revision = model.revision
        external = ExternalPage(root, Mock(model=model))
        assert external.fields[('external', 'on_end')][1].get() == 'C:/keep.bat'
        assert model.revision == revision
        model.set(('log', 'log_directory'), 'custom-logs')
        revision = model.revision
        logs = LogsPage(root, Mock(model=model, open_logs=Mock()))
        assert logs.fields[('log', 'log_directory')][1].get() == 'custom-logs'
        external.refresh()
        logs.refresh()
        assert model.revision == revision
    finally:
        root.destroy()


def test_make_app_failure_leaves_no_session_directory(tmp_path, monkeypatch):
    """构造过程中抛错时，不留下预览会话目录。"""
    from modules.gui import app as app_module

    root = tk.Tk()
    monkeypatch.setattr(app_module.tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setattr(app_module, 'work_area_size', Mock(side_effect=RuntimeError('构造失败')))
    try:
        with pytest.raises(RuntimeError, match='构造失败'):
            make_app(root, tmp_path)
        assert not list(tmp_path.glob('2rpm-gui-*'))
    finally:
        root.destroy()


@pytest.mark.parametrize('scaling', [1.0, 1.75])
def test_external_path_row_keeps_four_controls_reachable_after_resize_and_scroll(
        tmp_path, monkeypatch, scaling):
    """验证 Tk 缩放下两种窗宽的三事件控件布局、滚动及模型操作。"""
    from modules.gui.pages.external import EVENTS

    tk._default_root = None
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')
    previous = float(root.tk.call('tk', 'scaling'))
    try:
        root.withdraw()
        root.tk.call('tk', 'scaling', scaling)
        assert float(root.tk.call('tk', 'scaling')) == pytest.approx(scaling, abs=0.03)
        root._set_scaling(scaling, scaling)
        assert root._get_window_scaling() == pytest.approx(scaling)
        app = make_app(root, tmp_path)
        root.deiconify()
        assert app.show_page('外部程序设置')
        assert root._min_width == 600
        page = app.visible_pages['外部程序设置']
        canvas = page._parent_canvas
        expected = {}
        for width in ((800, 900) if scaling == 1.0 else (600, 900)):
            root.geometry(f'{width}x420+100+100')
            root.update()
            assert abs(app.sidebar.sidebar.winfo_width() /
                       root._get_window_scaling() - 246) <= 5
            for event, _title, _hint in EVENTS:
                field = page.path_fields[('external', event)]
                entry = field.entry
                page.update_idletasks()
                offset = field.master.winfo_y()
                canvas.yview_moveto(max(0.0, (offset - 12) / page.winfo_height()))
                root.update()
                root.attributes('-topmost', True)
                root.lift()
                root.update()
                root.attributes('-topmost', False)
                controls = (field.button, entry)
                left = canvas.winfo_rootx()
                right = left + canvas.winfo_width()
                top = canvas.winfo_rooty()
                bottom = top + canvas.winfo_height()
                for control in controls:
                    x = control.winfo_rootx() + control.winfo_width() // 2
                    y = control.winfo_rooty() + control.winfo_height() // 2
                    target = root.winfo_containing(x, y)
                    assert (control.winfo_ismapped() and control.winfo_width() > 1
                            and left <= control.winfo_rootx()
                            and control.winfo_rootx() + control.winfo_width() <= right
                            and top <= control.winfo_rooty()
                            and control.winfo_rooty() + control.winfo_height() <= bottom
                            and target is not None
                            and (target is control or str(target).startswith(str(control) + '.'))), (
                                scaling, width, event, control, target)
                selected = f'C:/chosen/{event}.bat'
                field.set(selected)
                assert app.model.get(('external', event)) == selected
                field.clear()
                assert app.model.get(('external', event)) == ''
                field.set(selected)
                expected[event] = selected
                assert all(app.model.get(('external', other)) == value
                           for other, value in expected.items())
        assert canvas.yview() != (0.0, 1.0)
    finally:
        root.tk.call('tk', 'scaling', previous)
        if 'app' in locals():
            root.after_cancel(app._after_id)
        root.destroy()
        tk._default_root = None
        if 'app' in locals():
            shutil.rmtree(app.session_dir)
            assert not app.session_dir.exists()


def test_logs_page_edits_policy_and_does_not_create_directory(tmp_path, monkeypatch):
    """路径按原值显示并保存，目录选择不读取日志。"""
    from modules.gui.pages.logs import Page

    root = tk.Tk()
    tk._default_root = root
    model = EditorModel(new_document())
    app = Mock(model=model, open_logs=Mock())
    page = Page(root, app)
    try:
        assert isinstance(page, ctk_kit.CTkScrollableFrame)
        path = ('log', 'log_directory')
        assert page.fields[path][1].get() == model.get(path)
        monkeypatch.setattr('modules.gui.pages.logs.filedialog.askdirectory',
                            lambda **_: str(tmp_path / 'chosen'))
        page.choose_directory()
        assert model.get(path) == str(tmp_path / 'chosen')
        assert not (tmp_path / 'chosen').exists()
        page.clear_directory()
        assert model.get(path) == ''
        number = ('log', 'max_log_files')
        spinner = page.fields[number][0]
        spinner.entry.delete(0, 'end')
        spinner.entry.insert(0, '19')
        spinner.entry._entry.event_generate('<Return>')
        root.update()
        assert model.get(number) == 19
        app.open_logs.assert_not_called()
    finally:
        root.destroy()
