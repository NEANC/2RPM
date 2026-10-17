#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notification 推送反馈的完成顺序、异常隔离与安全取消测试。

全部边界使用合成配置与本地替身：截图编排被替换为空批次，通道发送被替换为
可阻塞的假实现，真实网络与外部 IO 在夹具中被 fail-closed 阻断。
"""

import logging
import socket
import threading
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor as RealExecutor
from unittest.mock import Mock

import pytest

from modules import notification as notif
from modules.screenshot.pipeline import ScreenshotBatch


@pytest.fixture(autouse=True)
def isolate_feedback_boundaries(monkeypatch):
    """阻断意外外部网络，并把截图编排替换为不产生 IO 的空批次。"""

    def forbidden(*args, **kwargs):
        """对本测试中任何真实网络尝试直接失败。"""
        raise AssertionError('禁止真实网络访问')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)

    def empty_batch(section, enabled, reserved, *, runtime=None, event='event'):
        """返回不含任何截图项的空批次。"""
        return ScreenshotBatch({}, (), ())

    monkeypatch.setattr(notif, 'prepare_screenshots', empty_batch)


_DEFAULT_TEMPLATE = {
    'enable': True,
    'capture_screenshot': False,
    'title': '标题',
    'content': '正文',
}


def _config(channels, *, retry=None, template=None):
    """构造只含单一模板的最小推送配置。"""
    return {
        'push': {
            'screenshot': {'targets': []},
            'templates': {'on_end': dict(template or _DEFAULT_TEMPLATE)},
            'push_channel_settings': {'channels': channels},
            'retry': retry or {'interval': '0s', 'max_count': 1},
        },
    }


def _response(status=200):
    """构造仅含成败判定所需字段的伪响应对象。"""
    resp = Mock()
    resp.status_code = status
    resp.text = 'body'
    resp.json.return_value = {'code': 0}
    return resp


def _install_channel_double(monkeypatch, impl):
    """把单通道发送整体替换为给定实现。"""
    monkeypatch.setattr(notif, '_notify_single_channel', impl)
    monkeypatch.setattr(notif.time, 'sleep', Mock())


class _CallerThread(threading.Thread):
    """在独立调用线程内执行 send_notification 并记录返回值与异常。"""

    def __init__(self, config, *, hook=None, kwargs=None):
        """保存配置与可选回调钩子，初始化结果容器。"""
        super().__init__(daemon=True)
        self._config = config
        self._hook = hook
        self._kwargs = dict(kwargs) if kwargs else {'process_name': 'demo'}
        self.result = None
        self.caught = None
        self.seen = []
        self.callback_thread_ids = []
        self.thread_id = None

    def run(self):
        """在反馈作用域内执行 send_notification，按原对象捕获控制异常。"""
        self.thread_id = threading.get_ident()

        def observer(value):
            """记录终态身份与分发线程，随后触发可选钩子。"""
            self.seen.append(value)
            self.callback_thread_ids.append(threading.get_ident())
            if self._hook is not None:
                self._hook(value)

        try:
            with notif.feedback_scope(on_result=observer):
                self.result = notif.send_notification(
                    self._config, 'on_end', **self._kwargs)
        except BaseException as exc:
            self.caught = exc


def test_fast_channel_before_slow_release(monkeypatch):
    """快通道释放前即反馈，慢通道随后完成，返回仍按配置顺序。"""
    release = threading.Event()
    fast_seen = threading.Event()
    slow_started = threading.Event()
    worker_ids = []

    def fake(channel, title, content, retry_interval, max_count,
             screenshot_count=0, *, render_context=None):
        """index 1 立即失败，index 0 阻塞至主线程释放后成功。"""
        worker_ids.append(threading.get_ident())
        if channel.get('note') == 'slow':
            slow_started.set()
            release.wait(timeout=10)
            return True
        return False

    _install_channel_double(monkeypatch, fake)
    channels = [
        {'provider': 'qmsg', 'note': 'slow'},
        {'provider': 'qmsg', 'note': 'fast'},
    ]
    caller = _CallerThread(
        _config(channels),
        hook=lambda value: fast_seen.set() if value.channel_index == 1 else None)
    caller.start()
    try:
        assert slow_started.wait(timeout=10)
        assert fast_seen.wait(timeout=10)
        first = caller.seen[0]
        assert first.channel_index == 1
        assert not release.is_set()
        assert caller.is_alive()
    finally:
        release.set()
    caller.join(timeout=10)

    assert not caller.is_alive()
    assert caller.result == [('qmsg', True), ('qmsg', False)]
    assert [r.channel_index for r in caller.seen] == [1, 0]
    assert caller.callback_thread_ids == [caller.thread_id, caller.thread_id]
    assert set(caller.callback_thread_ids).isdisjoint(worker_ids)
    assert len({(r.event_id, r.channel_index) for r in caller.seen}) == 2


def test_duplicate_provider_and_scope_restore(monkeypatch, capsys):
    """同 provider 两条独立终态，作用域退出后不再回调且无新增终端输出。"""
    def fake(channel, title, content, retry_interval, max_count,
             screenshot_count=0, *, render_context=None):
        """按标记返回各自的布尔结果。"""
        return channel.get('note') == 'ok'

    _install_channel_double(monkeypatch, fake)
    channels = [
        {'provider': 'qmsg', 'note': 'ok'},
        {'provider': 'qmsg', 'note': 'bad'},
    ]
    config = _config(channels)
    seen = []
    with notif.feedback_scope(on_result=seen.append):
        result = notif.send_notification(config, 'on_end', process_name='demo')

    assert result == [('qmsg', True), ('qmsg', False)]
    assert {r.channel_index for r in seen} == {0, 1}
    assert {r.provider for r in seen} == {'qmsg'}
    assert len({(r.event_id, r.channel_index) for r in seen}) == 2

    notif.send_notification(config, 'on_end', process_name='demo')
    assert len(seen) == 2
    assert capsys.readouterr().out == ''


def test_retry_and_smtp_single_terminal(monkeypatch, capsys):
    """重试后成功与 SMTP 接受都只产生一条终态，且不改变既有发送次数。"""
    monkeypatch.setattr(notif.time, 'sleep', Mock())

    attempts = []
    notifier = Mock()

    def notify(**kwargs):
        """首次失败、第二次成功，用于验证重试路径。"""
        attempts.append(1)
        return _response(200 if len(attempts) >= 2 else 500)

    notifier.notify.side_effect = notify
    monkeypatch.setattr(notif, 'get_notifier', Mock(return_value=notifier))
    seen = []
    with notif.feedback_scope(on_result=seen.append):
        result = notif.send_notification(
            _config([{'provider': 'qmsg'}],
                    retry={'interval': '0s', 'max_count': 2}),
            'on_end', process_name='demo')
    assert result == [('qmsg', True)]
    assert len(attempts) == 2
    assert [r.ok for r in seen] == [True]

    for response in ({}, {'private@example.test': (550, b'SECRET')}):
        smtp_calls = []
        smtp = Mock()

        def smtp_notify(response=response, **kwargs):
            """记录调用次数并返回固定的 SMTP 接受结果。"""
            smtp_calls.append(1)
            return response

        smtp.notify.side_effect = smtp_notify
        monkeypatch.setattr(notif, 'get_notifier', Mock(return_value=smtp))
        seen = []
        with notif.feedback_scope(on_result=seen.append):
            result = notif.send_notification(
                _config([{'provider': 'smtp'}],
                        retry={'interval': '0s', 'max_count': 3}),
                'on_end', process_name='demo')
        assert result == [('smtp', True)]
        assert len(smtp_calls) == 1
        assert [r.ok for r in seen] == [True]

    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('mode', ('future_exception', 'observer_exception'))
def test_future_and_observer_exception_isolated(monkeypatch, caplog, mode):
    """Future 普通异常归 False 且继续；回调普通异常不改变结果也不重传。"""
    if mode == 'future_exception':
        worker_calls = []

        def fake(channel, title, content, retry_interval, max_count,
                 screenshot_count=0, *, render_context=None):
            """boom 通道抛普通异常，其余成功。"""
            worker_calls.append(channel.get('note'))
            if channel.get('note') == 'boom':
                raise RuntimeError('SECRET')
            return True

        _install_channel_double(monkeypatch, fake)
        channels = [
            {'provider': 'qmsg', 'note': 'boom'},
            {'provider': 'qmsg', 'note': 'ok'},
        ]
        seen = []
        with caplog.at_level(logging.ERROR, logger='modules.notification'):
            with notif.feedback_scope(on_result=seen.append):
                result = notif.send_notification(
                    _config(channels), 'on_end', process_name='demo')
        assert result == [('qmsg', False), ('qmsg', True)]
        assert sorted(r.ok for r in seen) == [False, True]
        assert worker_calls.count('boom') == 1
        assert 'SECRET' not in caplog.text
        assert '通道执行异常' in caplog.text
        return

    sender_calls = []

    def fake(channel, title, content, retry_interval, max_count,
             screenshot_count=0, *, render_context=None):
        """记录发送次数并统一成功。"""
        sender_calls.append(1)
        return True

    _install_channel_double(monkeypatch, fake)
    hooked = []

    def hook(value):
        """记录终态后抛普通异常，验证其被安全隔离。"""
        hooked.append(value)
        raise RuntimeError('SECRET')

    channels = [
        {'provider': 'qmsg', 'note': 'a'},
        {'provider': 'qmsg', 'note': 'b'},
    ]
    with caplog.at_level(logging.ERROR, logger='modules.notification'):
        with notif.feedback_scope(on_result=hook):
            result = notif.send_notification(
                _config(channels), 'on_end', process_name='demo')
    assert result == [('qmsg', True), ('qmsg', True)]
    assert len(sender_calls) == 2
    assert len(hooked) == 2
    assert 'SECRET' not in caplog.text


class _RecordingExecutor:
    """包装真实单工作线程 executor，记录 submit/cancel/shutdown 顺序。"""

    def __init__(self, events):
        """创建 max_workers=1 的真实 executor 与记录用的顺序表。"""
        self._events = events
        self._real = RealExecutor(max_workers=1)
        self.futures = []
        self.shutdown_entered = threading.Event()
        self.submit_fail_on = None
        self.fail_signal = None
        self.gate = None
        self.inject_result_index = None
        self.inject_result_exc = None
        self.inject_result_gate = None

    def __enter__(self):
        """进入真实 executor 上下文。"""
        self._real.__enter__()
        return self

    def __exit__(self, *exc_info):
        """先记录 shutdown 再交由真实 executor 等待运行中任务。"""
        self.shutdown_entered.set()
        self._events.append(('shutdown',))
        return self._real.__exit__(*exc_info)

    def submit(self, fn, *args, **kwargs):
        """按配置在指定次序抛错或在指定 Future 上注入 result 异常。"""
        index = len(self.futures) + 1
        if self.submit_fail_on == index:
            if self.gate is not None:
                self.gate()
            self._events.append(('submit_fail', index))
            raise self.fail_signal
        future = self._real.submit(fn, *args, **kwargs)
        if index == self.inject_result_index:
            future._inject_exc = self.inject_result_exc
            future._inject_gate = self.inject_result_gate
        self.futures.append(future)
        self._events.append(('submit', index))
        return future


def _install_executor(monkeypatch, executor):
    """把 notification 的线程池工厂替换为给定记录器。"""
    monkeypatch.setattr(notif, 'ThreadPoolExecutor', lambda *a, **k: executor)


def _record_future_cancel(monkeypatch, events, *, boom_on=None):
    """记录每次 Future.cancel，并可在指定序号抛出普通清理异常。"""
    original = Future.cancel
    counter = {'n': 0}

    def cancel(self):
        """记录后转交原始 cancel，指定序号先抛清理异常。"""
        counter['n'] += 1
        events.append(('cancel', counter['n']))
        if boom_on is not None and counter['n'] == boom_on:
            raise RuntimeError('SECRET')
        return original(self)

    monkeypatch.setattr(Future, 'cancel', cancel)


def _inject_future_result(monkeypatch):
    """按 Future 上的注入标记在 result 处抛出预设异常。"""
    original = Future.result

    def result(self, timeout=None):
        """命中注入标记时按闸门等待后抛出异常。"""
        gate = getattr(self, '_inject_gate', None)
        if gate is not None:
            gate()
        exc = getattr(self, '_inject_exc', None)
        if exc is not None:
            raise exc
        return original(self, timeout)

    monkeypatch.setattr(Future, 'result', result)


def _install_raising_as_completed(monkeypatch, gate, signal):
    """把 as_completed 替换为等待闸门后抛出控制信号的实现。"""
    def raising(fs, timeout=None):
        """在等待点注入控制信号。"""
        gate()
        raise signal

    monkeypatch.setattr(notif, 'as_completed', raising, raising=False)


_SIGNALS = {
    'keyboard': lambda: KeyboardInterrupt('stop'),
    'systemexit': lambda: SystemExit(3),
    'cancelled': lambda: FutureCancelledError('stop'),
}


@pytest.mark.parametrize(
    'location', ('submit', 'wait', 'result', 'observer', 'cancel_cleanup'))
@pytest.mark.parametrize('signal_name', ('keyboard', 'systemexit', 'cancelled'))
def test_control_signal_cleanup_order(monkeypatch, location, signal_name):
    """控制异常在 executor 内先取消已登记 Future 再等待，原对象传播。"""
    signal = _SIGNALS[signal_name]()
    release = threading.Event()
    slow_started = threading.Event()
    worker_ids = []
    calls = []

    def fake(channel, title, content, retry_interval, max_count,
             screenshot_count=0, *, render_context=None):
        """首个执行的任务阻塞，其余立即成功；排队任务永不应被执行。"""
        worker_ids.append(threading.get_ident())
        note = channel.get('note')
        calls.append(note)
        if note == 'slow':
            slow_started.set()
            release.wait(timeout=10)
        return True

    _install_channel_double(monkeypatch, fake)
    events = []
    executor = _RecordingExecutor(events)
    _install_executor(monkeypatch, executor)
    _inject_future_result(monkeypatch)

    def gate():
        """等待首个执行任务已开始，避免取消尚未开始的任务。"""
        slow_started.wait(timeout=10)

    hook = None
    if location in ('result', 'observer'):
        channels = [
            {'provider': 'qmsg', 'note': 'fast'},
            {'provider': 'qmsg', 'note': 'slow'},
            {'provider': 'qmsg', 'note': 'queued'},
        ]
    else:
        channels = [
            {'provider': 'qmsg', 'note': 'slow'},
            {'provider': 'qmsg', 'note': 'queued'},
        ]

    if location == 'submit':
        executor.submit_fail_on = 2
        executor.fail_signal = signal
        executor.gate = gate
    elif location == 'wait':
        _install_raising_as_completed(monkeypatch, gate, signal)
    elif location == 'result':
        executor.inject_result_index = 1
        executor.inject_result_exc = signal
        executor.inject_result_gate = gate
    elif location == 'observer':
        def hook(value):
            """在观察者内等待后抛出控制信号。"""
            gate()
            raise signal
    else:
        _install_raising_as_completed(monkeypatch, gate, signal)

    _record_future_cancel(
        monkeypatch, events, boom_on=1 if location == 'cancel_cleanup' else None)

    caller = _CallerThread(_config(channels), hook=hook)
    caller.start()
    try:
        assert slow_started.wait(timeout=10)
        assert executor.shutdown_entered.wait(timeout=10)
        assert not release.is_set()
        assert caller.is_alive()
    finally:
        release.set()
    caller.join(timeout=10)

    assert not caller.is_alive()
    assert caller.caught is signal

    cancel_positions = [i for i, e in enumerate(events) if e[0] == 'cancel']
    shutdown_positions = [i for i, e in enumerate(events) if e[0] == 'shutdown']
    assert cancel_positions and shutdown_positions
    assert max(cancel_positions) < min(shutdown_positions)

    assert 'queued' not in calls
    if location == 'submit':
        assert len(executor.futures) == 1
    else:
        assert len(executor.futures) == len(channels)

    if location == 'observer':
        assert len(caller.seen) == 1
        assert len({(r.event_id, r.channel_index) for r in caller.seen}) == 1
        assert set(caller.callback_thread_ids).isdisjoint(worker_ids)
    else:
        assert caller.seen == []


def test_template_kwargs_and_early_returns(monkeypatch, capsys):
    """on_result/spinner 仍按模板变量处理；提前返回路径零回调无终端输出。"""
    _install_channel_double(monkeypatch, lambda *a, **k: True)
    captured = {}

    def fake(channel, title, content, retry_interval, max_count,
             screenshot_count=0, *, render_context=None):
        """记录渲染后的正文以验证模板变量未被吞掉。"""
        captured['content'] = content
        return True

    monkeypatch.setattr(notif, '_notify_single_channel', fake)
    template = {
        'enable': True,
        'capture_screenshot': False,
        'title': '标题',
        'content': '{on_result} {spinner} {process_name}',
    }
    seen = []
    with notif.feedback_scope(on_result=seen.append):
        result = notif.send_notification(
            _config([{'provider': 'qmsg'}], template=template),
            'on_end', process_name='demo',
            on_result='用户模板值', spinner='用户模板值')

    assert result == [('qmsg', True)]
    captured_template_values = dict(zip(
        ('on_result', 'spinner', 'process_name'),
        captured['content'].split(' ')))
    assert captured_template_values['on_result'] == '用户模板值'
    assert captured_template_values['spinner'] == '用户模板值'
    assert len(seen) == 1

    disabled = _config([{'provider': 'qmsg'}],
                       template={'enable': False, 'content': '正文'})
    seen = []
    with notif.feedback_scope(on_result=seen.append):
        assert notif.send_notification(
            disabled, 'on_end', process_name='demo') == []
    assert seen == []

    missing = _config(
        [{'provider': 'qmsg'}],
        template={'enable': True, 'capture_screenshot': False,
                  'title': '标题', 'content': '{absent_var}'})
    seen = []
    with notif.feedback_scope(on_result=seen.append):
        assert notif.send_notification(
            missing, 'on_end', process_name='demo') == []
    assert seen == []

    seen = []
    with notif.feedback_scope(on_result=seen.append):
        assert notif.send_notification(
            _config([]), 'on_end', process_name='demo') == []
    assert seen == []

    assert capsys.readouterr().out == ''
