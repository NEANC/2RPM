#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""外部动作与日志页可见字段回写验证。"""

import tkinter as tk
from unittest.mock import Mock

import ctk_kit

from modules.config_document import new_document
from modules.gui.model import EditorModel


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
