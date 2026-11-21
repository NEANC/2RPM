#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 测试共用的真实窗口装配与小工具。"""

import queue
from unittest.mock import Mock


def make_app(root, tmp_path, *, blank=False):
    """使用真实 Tk 与无进程运行器搭建界面。

    Args:
        root: 真实 Tk 根窗口。
        tmp_path: 测试临时目录。
        blank: 为 True 时装配无路径的空白草稿，否则使用默认模板文档。
    """
    from modules.gui.app import App
    from modules.gui.controller import Controller
    from modules.config_document import (new_blank_document, new_document,
                                         save_document)
    from modules.gui.model import EditorModel

    runner = Mock()
    runner.results = queue.Queue()
    runner.active = False
    tmp_path.mkdir(parents=True, exist_ok=True)
    document = (new_blank_document() if blank else
                save_document(new_document(), tmp_path / 'a.yaml',
                              expected_fingerprint=None))
    model = EditorModel(document)
    controller = Controller(model, confirm=lambda kind, _: 'confirm' if kind == 'exit' else 'cancel',
                            choose_path=lambda: tmp_path / 'a.yaml', cli_command=('python.exe',),
                            runner=runner, program_dir=tmp_path)
    app = App(root, controller)
    for name in ('监视设置', '消息推送设置', '截图设置', '日志设置'):
        app.sidebar.page(name)
    # 系统标题栏窗口只有在应用处于前台时才能获得控件焦点；测试里显式激活。
    try:
        root.update()
        if root.winfo_viewable():
            root.focus_force()
    except Exception:
        pass
    return app


def arrow_y(spinner, direction):
    """按箭头画布的实际高度推导上下箭头点击坐标。

    开启 DPI 感知后控件高度随系统缩放变化，写死的像素可能落到相反半边；
    这里与 Spinner._on_arrow 使用同一判据（画布高度的一半）。

    参数:
        spinner: ctk-kit ``Spinner``。
        direction: ``'up'`` 点上箭头，``'down'`` 点下箭头。

    返回:
        相对箭头画布的 y 坐标。
    """
    spinner.update_idletasks()
    height = spinner._arrows.winfo_height()
    return 4 if direction == 'up' else height - 4


def monitor_page():
    """创建真实窗口中的监视表单（cfg 页测试用）。"""
    import tkinter as tk

    from modules.config_document import new_document
    from modules.gui.model import EditorModel
    from modules.gui.pages.monitor import Page

    root = tk.Tk()
    root.withdraw()
    model = EditorModel(new_document())
    page = Page(root, model)
    page.pack(fill='both', expand=True)
    return root, model, page


def descendants(parent):
    """枚举所有真实子控件。"""
    for child in parent.winfo_children():
        yield child
        yield from descendants(child)
