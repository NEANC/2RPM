#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""外部动作与日志页可见字段回写验证。"""

import shutil
import tkinter as tk
from unittest.mock import Mock

import ctk_kit
import pytest

from modules.config_document import new_document
from modules.gui.model import EditorModel
from tests.test_gui_pages import make_app


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
        page.fields[threshold][1].delete(0, 'end')
        page.fields[threshold][1].insert(0, '-')
        assert model.get_input(threshold) == '-'
        assert ('external', 'timeout_threshold') not in model.snapshot()['external']
        app.start_cli.assert_not_called()
    finally:
        root.destroy()


def test_external_path_label_above_input_and_browse_before_input():
    """三个事件的路径标签置于输入上方，浏览入口位于输入左侧。"""
    from modules.gui.pages.external import EVENTS, Page

    root = tk.Tk()
    try:
        page = Page(root, Mock(model=EditorModel(new_document())))
        page.pack(fill='both', expand=True)
        root.geometry('900x700')
        root.update()
        for event, _title, _hint in EVENTS:
            entry = page.fields[('external', event)][1]
            browse = page.browse_buttons[event]
            card = entry.master.master
            labels = [child for child in card.winfo_children()
                      if isinstance(child, ctk_kit.CTkLabel)
                      and child.cget('text') == '外部程序／BAT 路径']
            assert len(labels) == 1
            label = labels[0]
            assert label.winfo_rooty() + label.winfo_height() <= entry.winfo_rooty()
            assert browse.winfo_rootx() < entry.winfo_rootx()
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
                       root._get_window_scaling() - 280) <= 5
            for event, _title, _hint in EVENTS:
                entry = page.fields[('external', event)][1]
                page.update_idletasks()
                offset = entry.master.master.winfo_y()
                canvas.yview_moveto(max(0.0, (offset - 12) / page.winfo_height()))
                root.update()
                root.attributes('-topmost', True)
                root.lift()
                root.update()
                root.attributes('-topmost', False)
                controls = (page.browse_buttons[event], entry,
                            page.clear_buttons[event], page.restore_buttons[event])
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
                monkeypatch.setattr('modules.gui.pages.external.filedialog.askopenfilename',
                                    lambda **_: selected)
                page.browse_buttons[event].invoke()
                assert app.model.get(('external', event)) == selected
                page.clear_buttons[event].invoke()
                assert app.model.get(('external', event)) == ''
                assert page.restore_buttons[event].cget('state') == 'normal'
                page.restore_buttons[event].invoke()
                assert app.model.get(('external', event)) == selected
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
        page.fields[number][1].delete(0, 'end')
        page.fields[number][1].insert(0, '19')
        assert model.get(number) == 19
        app.open_logs.assert_not_called()
    finally:
        root.destroy()
