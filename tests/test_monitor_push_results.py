#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""monitor 推送反馈包装、纯聚合与十处迁移的静态与集成测试。

全部使用合成配置与本地替身：替身 send_notification 通过真实反馈作用域
（feedback_scope + _notify_observer）分发终态，确保验证的是实时反馈路径，
而不是绕开实时反馈只返回元组；真实网络与外部 IO 均被替换。
"""

import ast
import os
import subprocess
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import monitor
from modules import notification as notif
from modules.notification import PushChannelResult
from modules.monitor import (
    ProcessTimeoutExitRequested,
    _all_push_failed,
    _render_push_results,
)

_SMTP_OK = '通道 [smtp] SMTP 服务器至少部分接受（不代表最终到达）'
_SMTP_FAIL = '通道 [smtp] SMTP 未确认接受'


class _FakeTty:
    """伪造 TTY 输出流，仅提供 isatty 判定。"""

    def isatty(self):
        """始终返回 True，模拟 TTY 环境。"""
        return True


class _FakeNonTty:
    """伪造非 TTY 输出流，仅提供 isatty 判定。"""

    def isatty(self):
        """始终返回 False，模拟非 TTY 环境。"""
        return False


class _FlushCapture:
    """捕获 stdout 写入并记录 flush 次数的替身流。"""

    def __init__(self):
        """初始化写入缓冲与 flush 计数。"""
        self.data = []
        self.flushed = 0

    def write(self, text):
        """记录一次写入内容。"""
        self.data.append(text)

    def flush(self):
        """记录一次 flush。"""
        self.flushed += 1


class _RecordingHandle:
    """记录 write_done/write_fail 的 TTY 替身句柄。"""

    def __init__(self):
        """初始化终态记录、线程身份与 TTY 流。"""
        self.terminal = []
        self.owner_thread_id = threading.get_ident()
        self.closed = False
        self.stream = _FakeTty()

    def text(self, message):
        """忽略文案更新。"""

    def done(self, message):
        """忽略定格收尾。"""

    def fail(self, message):
        """忽略定格收尾。"""

    def write(self, message):
        """忽略插入打印。"""

    def write_done(self, message):
        """记录一条成功终态。"""
        self.terminal.append(('done', message))

    def write_fail(self, message):
        """记录一条失败终态。"""
        self.terminal.append(('fail', message))


class _NonTtyHandle:
    """显式非 TTY 的替身句柄，用于断言不会调用 write_done/write_fail。"""

    def __init__(self):
        """初始化非 TTY 流与调用记录。"""
        self.owner_thread_id = threading.get_ident()
        self.closed = False
        self.stream = _FakeNonTty()
        self.writes = []

    def write_done(self, message):
        """记录本不应发生的成功写入。"""
        self.writes.append(message)

    def write_fail(self, message):
        """记录本不应发生的失败写入。"""
        self.writes.append(message)


class _PhaseStub:
    """spinner_phase 替身上下文，产出给定句柄。"""

    def __init__(self, handle):
        """保存产出句柄。"""
        self.handle = handle

    def __enter__(self):
        """返回产出句柄。"""
        return self.handle

    def __exit__(self, *exc_info):
        """不抑制异常。"""
        return False


def _make_feedback_send(channel_map, *, raise_on=None, sink=None):
    """构造通过真实反馈作用域分发终态的 send_notification 替身。

    替身读取当前 feedback_scope 观察者并借助真实 _notify_observer 分发，
    从而验证 monitor 包装进入作用域后确实走了实时反馈路径。

    Args:
        channel_map (dict): {template_key: [(provider, ok), ...]}。
        raise_on (dict | None): {'key', 'exc'}，命中键时抛出给定异常对象。
        sink (list | None): 记录每次分发出的 PushChannelResult。

    Returns:
        MagicMock: 带 call_count 的替身，副作用为上述分发逻辑。
    """
    counter = {'n': 0}
    record = [] if sink is None else sink

    def side_effect(config, template_key, **kwargs):
        """按模板键分发终态或抛出预设异常。"""
        if raise_on is not None and template_key == raise_on['key']:
            raise raise_on['exc']
        pairs = channel_map.get(template_key, [])
        owner, observer = notif._current_feedback()
        dispatch_thread = threading.get_ident()
        event_id = f'ev{counter["n"]}'
        counter['n'] += 1
        for index, (provider, ok) in enumerate(pairs):
            result = PushChannelResult(event_id, index, provider, ok)
            record.append(result)
            notif._notify_observer(observer, owner, dispatch_thread, result)
        return list(pairs)

    return MagicMock(side_effect=side_effect)


def _base_config(**external):
    """构造 psutil 模式的最小监视配置。"""
    return {
        'monitor': {
            'mode': 'psutil',
            'common': {
                'timeout_interval': '1s', 'loop_interval': '1s',
                'timeout_threshold': 1, 'max_wait': '0s',
                'check_interval': '1s',
            },
            'psutil': {'process_name': 'missing.exe'},
            'task_scheduler': {
                'task_name': r'\Custom\MainTask',
                'lookback_minutes': 5,
            },
            'launch': {
                'type': 'task', 'path': None, 'args': None,
                'cwd': None, 'task_name': r'\Custom\MainTask',
            },
        },
        'external': {
            'on_end': '', 'on_timeout': '',
            'on_wait_timeout': '', **external,
        },
    }


def _external_result(action_type='task', target=r'\Custom\MyTask'):
    """构造 run_external_action 的返回元数据。"""
    display_name = 'MyTask' if action_type == 'task' else os.path.basename(target)
    return {
        'type': action_type,
        'target': target,
        'display_name': display_name,
        'display_path': target,
    }


def _drive_handle_process_end(handle):
    """事件 on_end：直接调用结束处理（无外部动作）。"""
    monitor._handle_process_end(_base_config(), 'demo.exe', 123, 10, '', handle)


def _drive_handle_process_end_external(handle):
    """事件 on_external：结束处理内联外部动作成功后发送。"""
    config = _base_config(on_end=r'task:\Custom\MyTask')
    with patch('modules.monitor.run_external_action',
               return_value=_external_result()):
        monitor._handle_process_end(
            config, 'demo.exe', 123, 10, r'task:\Custom\MyTask', handle)


def _drive_check_timeout(handle):
    """事件 on_timeout：直接调用超时检查（阈值高，不触发外部动作）。"""
    process_info = {
        'name': 'demo.exe', 'start_time': 0,
        'last_warning_time': 0, 'timeout_count': 0,
    }
    monitor._check_process_timeout(
        _base_config(), process_info, 123, 10, 1, '', 1000, spinner=handle)


def _drive_check_timeout_external(handle):
    """事件 on_external：超时达阈值后的外部动作通知。"""
    config = _base_config(on_timeout=r'task:\Custom\MyTask')
    process_info = {
        'name': 'demo.exe', 'start_time': 0,
        'last_warning_time': 0, 'timeout_count': 0,
    }
    with patch('modules.monitor.run_external_action',
               return_value=_external_result()):
        with pytest.raises(ProcessTimeoutExitRequested):
            monitor._check_process_timeout(
                config, process_info, 123, 10, 1,
                r'task:\Custom\MyTask', 1, spinner=handle)


def _drive_launch_task(handle):
    """事件 on_wait_timeout：launch task 等待启动超时。"""
    config = _base_config()
    with patch('modules.monitor.subprocess.run',
               return_value=subprocess.CompletedProcess(args=[], returncode=0)), \
            patch('modules.monitor.query_task_pid',
                  return_value={'state': 'not_found'}), \
            patch('modules.monitor.spinner_phase',
                  side_effect=lambda *a, **k: _PhaseStub(handle)), \
            patch('modules.monitor.notify_fail'), \
            patch('modules.monitor.time.sleep'):
        with pytest.raises(SystemExit):
            monitor._launch_task(config['monitor']['launch'], config)


def _drive_launch_task_external(handle):
    """事件 on_external：launch task 等待超时后的外部动作通知。"""
    config = _base_config(on_wait_timeout=r'task:\Custom\MyTask')
    with patch('modules.monitor.subprocess.run',
               return_value=subprocess.CompletedProcess(args=[], returncode=0)), \
            patch('modules.monitor.query_task_pid',
                  return_value={'state': 'not_found'}), \
            patch('modules.monitor.run_external_action',
                  return_value=_external_result()), \
            patch('modules.monitor.spinner_phase',
                  side_effect=lambda *a, **k: _PhaseStub(handle)), \
            patch('modules.monitor.notify_fail'), \
            patch('modules.monitor.time.sleep'):
        with pytest.raises(SystemExit):
            monitor._launch_task(config['monitor']['launch'], config)


def _drive_monitor_processes(handle):
    """事件 on_wait_timeout：进程名多 PID 模式等待启动超时。"""
    config = _base_config()
    with patch('modules.monitor._collect_matching_processes', return_value={}), \
            patch('modules.monitor.spinner_phase',
                  side_effect=lambda *a, **k: _PhaseStub(handle)), \
            patch('modules.monitor.notify_fail'), \
            patch('modules.monitor.time.sleep'):
        with pytest.raises(SystemExit):
            monitor.monitor_processes(config)


def _drive_monitor_processes_external(handle):
    """事件 on_external：进程名多 PID 模式等待超时后的外部动作通知。"""
    config = _base_config(on_wait_timeout=r'task:\Custom\MyTask')
    with patch('modules.monitor._collect_matching_processes', return_value={}), \
            patch('modules.monitor.run_external_action',
                  return_value=_external_result()), \
            patch('modules.monitor.spinner_phase',
                  side_effect=lambda *a, **k: _PhaseStub(handle)), \
            patch('modules.monitor.notify_fail'), \
            patch('modules.monitor.time.sleep'):
        with pytest.raises(SystemExit):
            monitor.monitor_processes(config)


def _drive_task_scheduler(handle):
    """事件 on_wait_timeout：计划任务调度模式等待触发超时。"""
    config = _base_config()
    config['monitor']['mode'] = 'task_scheduler'
    with patch('modules.monitor.query_task_pid',
               return_value={'state': 'not_found'}), \
            patch('modules.monitor.spinner_phase',
                  side_effect=lambda *a, **k: _PhaseStub(handle)), \
            patch('modules.monitor.notify_fail'), \
            patch('modules.monitor.time.sleep'):
        with pytest.raises(SystemExit):
            monitor.monitor_via_task_scheduler(config)


def _drive_task_scheduler_external(handle):
    """事件 on_external：计划任务调度等待超时后的外部动作通知。"""
    config = _base_config(on_wait_timeout=r'task:\Custom\MyTask')
    config['monitor']['mode'] = 'task_scheduler'
    with patch('modules.monitor.query_task_pid',
               return_value={'state': 'not_found'}), \
            patch('modules.monitor.run_external_action',
                  return_value=_external_result()), \
            patch('modules.monitor.spinner_phase',
                  side_effect=lambda *a, **k: _PhaseStub(handle)), \
            patch('modules.monitor.notify_fail'), \
            patch('modules.monitor.time.sleep'):
        with pytest.raises(SystemExit):
            monitor.monitor_via_task_scheduler(config)


_TEN_SITES = [
    ('handle_process_end_on_end', _drive_handle_process_end,
     {'on_end': [('qmsg', True), ('smtp', False)]},
     ['通道 [qmsg] 推送成功', _SMTP_FAIL]),
    ('handle_process_end_on_external', _drive_handle_process_end_external,
     {'on_external': [('qmsg', False)]}, ['通道 [qmsg] 推送失败']),
    ('check_timeout_on_timeout', _drive_check_timeout,
     {'on_timeout': [('qmsg', True)]}, ['通道 [qmsg] 推送成功']),
    ('check_timeout_on_external', _drive_check_timeout_external,
     {'on_external': [('qmsg', False)]}, ['通道 [qmsg] 推送失败']),
    ('launch_task_on_wait_timeout', _drive_launch_task,
     {'on_wait_timeout': [('qmsg', True), ('smtp', True)]},
     ['通道 [qmsg] 推送成功', _SMTP_OK]),
    ('launch_task_on_external', _drive_launch_task_external,
     {'on_external': [('qmsg', False)]}, ['通道 [qmsg] 推送失败']),
    ('monitor_processes_on_wait_timeout', _drive_monitor_processes,
     {'on_wait_timeout': [('qmsg', False)]}, ['通道 [qmsg] 推送失败']),
    ('monitor_processes_on_external', _drive_monitor_processes_external,
     {'on_external': [('qmsg', False)]}, ['通道 [qmsg] 推送失败']),
    ('task_scheduler_on_wait_timeout', _drive_task_scheduler,
     {'on_wait_timeout': [('smtp', False)]}, [_SMTP_FAIL]),
    ('task_scheduler_on_external', _drive_task_scheduler_external,
     {'on_external': [('qmsg', True)]}, ['通道 [qmsg] 推送成功']),
]


def test_smtp_result_words():
    """兼容门面仍按 SMTP 成功与失败使用各自固定文案。"""
    sp = MagicMock()
    assert _render_push_results([('smtp', True)], sp) is False
    sp.write_done.assert_called_once_with(_SMTP_OK)
    sp.write_fail.assert_not_called()

    other_sp = MagicMock()
    assert _render_push_results([('smtp', False)], other_sp) is True
    other_sp.write_fail.assert_called_once_with(_SMTP_FAIL)
    other_sp.write_done.assert_not_called()

    empty_sp = MagicMock()
    assert _render_push_results([], empty_sp) is False
    empty_sp.write_done.assert_not_called()
    empty_sp.write_fail.assert_not_called()


def test_mixed_result_aggregation():
    """混合结果中 SMTP 与非 SMTP 文案独立，布尔聚合保持不变。"""
    mixed_sp = MagicMock()
    assert _render_push_results(
        [('smtp', True), ('qmsg', False)], mixed_sp) is False
    mixed_sp.write_done.assert_called_once_with(_SMTP_OK)
    mixed_sp.write_fail.assert_called_once_with('通道 [qmsg] 推送失败')


def test_all_push_failed_is_pure():
    """空结果为 False，非空全失败为 True，存在成功为 False。"""
    assert [_all_push_failed(r) for r in (
        [], [('qmsg', False)], [('qmsg', False), ('smtp', True)]
    )] == [False, True, False]


def test_feedback_wrapper_single_send():
    """包装进入反馈作用域后恰好调用一次原函数，且不补打后置渲染。"""
    handle = _RecordingHandle()
    dispatched = []
    send = _make_feedback_send(
        {'on_end': [('qmsg', True), ('smtp', False)]}, sink=dispatched)
    post_render = MagicMock()

    with patch.object(monitor, 'send_notification', send), \
            patch.object(monitor, '_render_push_results', post_render):
        results = monitor._send_notification_with_feedback(
            _base_config(), 'on_end', spinner=handle,
            process_name='demo.exe', process_pid=1, process_run_time='0:00:10')

    assert send.call_count == 1
    assert post_render.call_count == 0
    assert results == [('qmsg', True), ('smtp', False)]
    assert [tag for tag, _text in handle.terminal] == ['done', 'fail']
    assert 'spinner' not in send.call_args.kwargs
    assert len(dispatched) == 2


@pytest.mark.parametrize(
    'site_id,driver,channel_map,expected',
    _TEN_SITES, ids=[s[0] for s in _TEN_SITES])
def test_monitor_all_ten_feedback_sites(site_id, driver, channel_map, expected):
    """十处调用点：每通道恰一条终态，且不调用后置渲染补打。"""
    handle = _RecordingHandle()
    send = _make_feedback_send(channel_map)
    render = MagicMock()

    with patch.object(monitor, 'send_notification', send), \
            patch.object(monitor, '_render_push_results', render):
        driver(handle)

    # 外部动作成功行独立，通道终态按每通道恰一条统计
    channel_lines = [
        text for _ok, text in handle.terminal if text.startswith('通道 [')]
    assert channel_lines == expected
    assert len(channel_lines) == len(expected)
    assert render.call_count == 0


def test_two_timeout_rounds():
    """连续两轮超时：同 provider 每轮各一条终态，各自独立 event_id。"""
    handle = _RecordingHandle()
    two_rounds = []
    send = _make_feedback_send(
        {'on_timeout': [('qmsg', True)]}, sink=two_rounds)
    process_info = {
        'name': 'demo.exe', 'start_time': 0,
        'last_warning_time': 0, 'timeout_count': 0,
    }

    with patch.object(monitor, 'send_notification', send):
        monitor._check_process_timeout(
            _base_config(), process_info, 123, 10, 1, '', 1000, spinner=handle)
        monitor._check_process_timeout(
            _base_config(), process_info, 123, 20, 1, '', 1000, spinner=handle)

    assert [text for _ok, text in handle.terminal] == [
        '通道 [qmsg] 推送成功', '通道 [qmsg] 推送成功']
    assert len({r.event_id for r in two_rounds}) == 2


def test_timeout_handle_forwarding_single_pid():
    """单 PID 监视把活动 spinner 传入超时检查。"""
    handle = _RecordingHandle()
    pid_info = {
        'pid': 123, 'name': 'demo.exe', 'create_time': 100.0,
        'start_time': 0, 'last_warning_time': 0, 'timeout_count': 0,
    }
    alive_proc = MagicMock()
    alive_proc.create_time.return_value = 100.0
    timeout_mock = MagicMock(
        side_effect=ProcessTimeoutExitRequested('stop', 123))

    with patch('modules.monitor.time.sleep'), \
            patch('modules.monitor.time.time', return_value=10), \
            patch('modules.monitor.psutil.Process', return_value=alive_proc), \
            patch('modules.monitor._check_process_timeout', timeout_mock):
        monitor._monitor_single_pid(pid_info, _base_config(), handle)

    timeout_mock.assert_called_once()
    assert timeout_mock.call_args.kwargs['spinner'] is handle


def test_timeout_handle_forwarding_process_name():
    """进程名多 PID 监视把活动 spinner 传入超时检查。"""
    handle = _RecordingHandle()
    config = _base_config()
    config['monitor']['psutil']['process_name'] = 'target.exe'
    timeout_mock = MagicMock(
        side_effect=ProcessTimeoutExitRequested('stop', 1234))

    with patch('modules.monitor._collect_matching_processes',
               return_value={1234: {'name': 'target.exe', 'create_time': 100.0}}), \
            patch('modules.monitor.spinner_phase',
                  side_effect=lambda *a, **k: _PhaseStub(handle)), \
            patch('modules.monitor._check_process_timeout', timeout_mock), \
            patch('modules.monitor.time.sleep'), \
            patch('modules.monitor.notify_fail'):
        monitor.monitor_processes(config)

    timeout_mock.assert_called_once()
    assert timeout_mock.call_args.kwargs['spinner'] is handle


def test_non_tty_feedback_writes_plain_stdout():
    """显式非 TTY 句柄改走 stdout 普通行，无 ANSI 且不调用 write_done。"""
    handle = _NonTtyHandle()
    capture = _FlushCapture()
    send = _make_feedback_send(
        {'on_end': [('qmsg', True), ('smtp', False)]})

    with patch.object(monitor, 'send_notification', send), \
            patch.object(monitor.sys, 'stdout', capture):
        monitor._send_notification_with_feedback(
            _base_config(), 'on_end', spinner=handle, process_name='demo.exe')

    text = ''.join(capture.data)
    assert '✔️  通道 [qmsg] 推送成功' in text
    assert '❌ 通道 [smtp]' in text
    assert _SMTP_FAIL in text
    assert '\x1b' not in text
    assert capture.flushed == 2
    assert handle.writes == []


def _monitor_tree():
    """解析 modules/monitor.py 得到 AST。"""
    path = os.path.join(
        os.path.dirname(__file__), '..', 'modules', 'monitor.py')
    with open(path, encoding='utf-8') as handle:
        return ast.parse(handle.read())


def _find_function(tree, name):
    """在模块顶层查找指定名称的函数定义节点。"""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.name == name:
            return node
    return None


def _bare_calls(tree, name):
    """收集模块内以裸名调用的指定函数的所有 Call 节点。"""
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == name:
            calls.append(node)
    return calls


def test_monitor_bare_send_notification_only_inside_wrapper():
    """monitor 包装外无裸 send_notification 调用，包装内恰一处。"""
    tree = _monitor_tree()
    calls = _bare_calls(tree, 'send_notification')
    assert len(calls) == 1
    wrapper = _find_function(tree, '_send_notification_with_feedback')
    assert wrapper is not None
    assert wrapper.lineno <= calls[0].lineno <= wrapper.end_lineno


def test_monitor_ten_sites_use_wrapper():
    """十个业务点均调用包装，模板键分布符合十处迁移表。"""
    tree = _monitor_tree()
    calls = _bare_calls(tree, '_send_notification_with_feedback')
    assert len(calls) == 10
    keys = [call.args[1].value for call in calls]
    assert keys.count('on_end') == 1
    assert keys.count('on_timeout') == 1
    assert keys.count('on_wait_timeout') == 3
    assert keys.count('on_external') == 5


def test_monitor_render_push_results_not_invoked_outside_facade():
    """兼容门面外无 _render_push_results 调用，门面本身仍保留。"""
    tree = _monitor_tree()
    assert _bare_calls(tree, '_render_push_results') == []
    assert _find_function(tree, '_render_push_results') is not None
