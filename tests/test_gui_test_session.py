#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""隔离测试会话的单槽与结果归属测试。"""

from unittest.mock import Mock

from modules.gui.protocol import TestRequest as GuiRequest
from modules.gui.protocol import TestResult as GuiResult
from modules.gui.test_session import TestSession as GuiSession


def request(identifier='first', revision=2, action='capture'):
    """创建不依赖真实进程的请求。"""
    return GuiRequest(identifier, 1, revision, action, {}, 'C:/config', 'C:/session',
                       None, None, None, None, 'window' if action == 'capture' else None,
                       '窗口' if action == 'capture' else None)


def test_double_click_does_not_resubmit():
    """运行中的同类或不同类动作都不能占用第二个 worker。"""
    runner = Mock(active=False)
    session = GuiSession(runner)
    assert session.start(request())
    assert not session.start(request('second'))
    runner.start.assert_called_once()


def test_hide_keeps_worker_running():
    """结果窗口隐藏不取消工作进程。"""
    runner = Mock(active=False)
    session = GuiSession(runner)
    session.start(request())
    session.hide()
    assert session.active_request is not None
    runner.cancel.assert_not_called()


def test_stale_result_and_previous_revision():
    """旧请求不能替换结果，旧修订可显示但必须标明归属。"""
    runner = Mock(active=False)
    session = GuiSession(runner)
    session.start(request())
    assert not session.accept(GuiResult('other', 1, 2, 'success', '', ()))
    assert session.accept(GuiResult('first', 1, 2, 'success', '', ()), current_revision=3)
    assert session.previous_draft
    assert session.last_capture.request_id == 'first'
    session.clear()
    assert session.last_capture is None


def test_previous_capture_mark_survives_upload_start():
    """另一测试开始不能抹掉旧截图与旧草稿归属。"""
    runner = Mock(active=False)
    session = GuiSession(runner)
    session.start(request())
    session.accept(GuiResult('first', 1, 2, 'success', '', ()), current_revision=3)
    session.start(request('upload', 3, 'upload'))
    assert session.last_capture_previous


def test_cancel_does_not_clear_request_before_terminal_result():
    """取消只请求停止，直到 runner 交付终态才释放会话。"""
    runner = Mock(active=False)
    session = GuiSession(runner)
    session.start(request())
    session.cancel()
    assert session.active_request is not None
    runner.cancel.assert_called_once()
