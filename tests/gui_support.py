#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 测试共用的真实窗口装配与小工具。"""

import queue
from unittest.mock import Mock


def make_app(root, tmp_path):
    """使用真实 Tk 与无进程运行器搭建界面。"""
    from modules.gui.app import App
    from modules.gui.controller import Controller
    from modules.config_document import new_document, save_document
    from modules.gui.model import EditorModel

    runner = Mock()
    runner.results = queue.Queue()
    runner.active = False
    tmp_path.mkdir(parents=True, exist_ok=True)
    model = EditorModel(save_document(new_document(), tmp_path / 'a.yaml',
                                      expected_fingerprint=None))
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
