#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 Event 129 的精确路径匹配查询与句柄关闭行为。

所有事件日志调用均使用替身，不接触真实通道。
"""

import os
import sys
import unittest
from unittest.mock import patch

import pywintypes

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import task_monitor

EVENT_XML_TEMPLATE = (
    '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">'
    '<System>'
    '<Provider Name="Microsoft-Windows-TaskScheduler" '
    'Guid="{{de7b24ea-73c8-4a09-985d-5bdadcfa9017}}"/>'
    '<EventID>129</EventID>'
    '<Version>2</Version>'
    '<Level>4</Level>'
    '<Task>129</Task>'
    '<Opcode>129</Opcode>'
    '<Keywords>0x8000000000000000</Keywords>'
    '<TimeCreated SystemTime="{event_time}"/>'
    '<EventRecordID>{record_id}</EventRecordID>'
    '<Correlation/>'
    '<Execution ProcessID="4" ThreadID="1088"/>'
    '<Channel>Microsoft-Windows-TaskScheduler/Operational</Channel>'
    '<Computer>TEST-PC</Computer>'
    '<Security UserID="S-1-5-18"/>'
    '</System>'
    '<EventData>{data}</EventData>'
    '</Event>'
)

DEFAULT_EVENT_TIME = '2026-11-10T04:30:28.1800210Z'
DEFAULT_EXE = 'E:\\GitHub\\2RPM\\.venv\\Scripts\\python.exe'


class _FakeHandle:
    """记录 Close 调用次数的替身事件句柄。"""

    def __init__(self, xml_text=''):
        self.xml_text = xml_text
        self.close_calls = 0

    def Close(self):
        """模拟 pywin32 PyHANDLE 的关闭方法。"""
        self.close_calls += 1


class TestTaskChannelEnabled(unittest.TestCase):
    """验证配置属性检查与句柄生命周期。"""

    def test_enabled_property_allows_launch(self):
        """只接受 pywin32 返回元组中的 Enabled=True。"""
        handle = _FakeHandle()
        with patch.object(task_monitor.win32evtlog, 'EvtOpenChannelConfig',
                          return_value=handle) as open_config, \
                patch.object(task_monitor.win32evtlog,
                             'EvtGetChannelConfigProperty',
                             return_value=(True, 13)) as get_property:
            task_monitor.ensure_task_channel_enabled()

        open_config.assert_called_once_with(task_monitor.TASK_LOG_CHANNEL)
        get_property.assert_called_once_with(
            handle, task_monitor.win32evtlog.EvtChannelConfigEnabled)
        self.assertEqual(handle.close_calls, 1)

    def test_disabled_property_rejected(self):
        """历史可读但配置禁用的通道不得视为可触发。"""
        handle = _FakeHandle()
        with patch.object(task_monitor.win32evtlog, 'EvtOpenChannelConfig',
                          return_value=handle), \
                patch.object(task_monitor.win32evtlog,
                             'EvtGetChannelConfigProperty',
                             return_value=(False, 13)):
            with self.assertRaises(task_monitor.TaskQueryError):
                task_monitor.ensure_task_channel_enabled()
        self.assertEqual(handle.close_calls, 1)

    def test_open_failure_rejected(self):
        """无法打开通道配置时不尝试属性读取。"""
        with patch.object(task_monitor.win32evtlog, 'EvtOpenChannelConfig',
                          side_effect=RuntimeError('open failed')), \
                patch.object(task_monitor.win32evtlog,
                             'EvtGetChannelConfigProperty') as get_property:
            with self.assertRaises(task_monitor.TaskQueryError):
                task_monitor.ensure_task_channel_enabled()
        get_property.assert_not_called()

    def test_property_failure_rejected_and_closed(self):
        """属性查询失败仍关闭已打开配置句柄。"""
        handle = _FakeHandle()
        with patch.object(task_monitor.win32evtlog, 'EvtOpenChannelConfig',
                          return_value=handle), \
                patch.object(task_monitor.win32evtlog,
                             'EvtGetChannelConfigProperty',
                             side_effect=RuntimeError('get failed')):
            with self.assertRaises(task_monitor.TaskQueryError):
                task_monitor.ensure_task_channel_enabled()
        self.assertEqual(handle.close_calls, 1)

    def test_missing_binding_rejected(self):
        """绑定不可用时不把可读历史日志当作启用证明。"""
        with patch.object(task_monitor.win32evtlog, 'EvtOpenChannelConfig',
                          None):
            with self.assertRaises(task_monitor.TaskQueryError):
                task_monitor.ensure_task_channel_enabled()

    def test_control_signal_propagates_and_closes(self):
        """读取配置时控制信号保持原对象，句柄只关闭一次。"""
        for signal in (KeyboardInterrupt(), SystemExit(2)):
            with self.subTest(signal=type(signal).__name__):
                handle = _FakeHandle()
                with patch.object(task_monitor.win32evtlog,
                                  'EvtOpenChannelConfig', return_value=handle), \
                        patch.object(task_monitor.win32evtlog,
                                     'EvtGetChannelConfigProperty',
                                     side_effect=signal):
                    with self.assertRaises(type(signal)) as caught:
                        task_monitor.ensure_task_channel_enabled()
                self.assertIs(caught.exception, signal)
                self.assertEqual(handle.close_calls, 1)


def _event_xml(task_name, pid=None, path=DEFAULT_EXE, record_id=100,
               event_time=DEFAULT_EVENT_TIME, extra_data=()):
    """按 G3 实测结构构造 Event 129 的 XML 文本。"""
    data_lines = [f'<Data Name="TaskName">{task_name}</Data>']
    if path is not None:
        data_lines.append(f'<Data Name="Path">{path}</Data>')
    if pid is not None:
        data_lines.append(f'<Data Name="ProcessID">{pid}</Data>')
    for name, value in extra_data:
        data_lines.append(f'<Data Name="{name}">{value}</Data>')
    return EVENT_XML_TEMPLATE.format(
        event_time=event_time,
        record_id=record_id,
        data=''.join(data_lines),
    )


def _fake_render(handle, event_type):
    """替身 EvtRender：直接把句柄携带的 XML 文本返回。"""
    return handle.xml_text


class TestQueryTaskEvents(unittest.TestCase):
    """验证 query_task_events 的匹配、过滤与异常语义。"""

    def _patch_query(self, handles_batches, query_handle=None,
                     query_error=None, next_error=None):
        """装配替身查询句柄、EvtNext 返回值与 EvtRender。"""
        if query_handle is None:
            query_handle = _FakeHandle()
        if query_error is not None:
            query_side_effect = query_error
        else:
            query_side_effect = None
        if next_error is not None:
            next_side_effect = next_error
        else:
            next_side_effect = list(handles_batches) + [[]]
        return (
            patch.object(task_monitor.win32evtlog, 'EvtQuery',
                         return_value=query_handle,
                         side_effect=query_side_effect),
            patch.object(task_monitor.win32evtlog, 'EvtNext',
                         side_effect=next_side_effect),
            patch.object(task_monitor.win32evtlog, 'EvtRender',
                         side_effect=_fake_render),
            query_handle,
        )

    def test_exact_task_path_case_insensitive(self):
        """完整路径忽略大小写匹配，子串不算命中。"""
        handles = [
            _FakeHandle(_event_xml('\\Folder\\TaskOther', pid=222, record_id=103)),
            _FakeHandle(_event_xml('\\Folder\\Task', pid=111, record_id=102)),
            _FakeHandle(_event_xml('\\folder\\task', pid=333, record_id=101)),
        ]
        query_patch, next_patch, render_patch, _ = self._patch_query([handles])
        with query_patch, next_patch, render_patch:
            events = task_monitor.query_task_events('\\Folder\\Task', 10)

        self.assertEqual([event.pid for event in events], [111, 333])
        self.assertEqual([event.record_id for event in events], [102, 101])

    def test_root_task_supported(self):
        """根目录任务 \\Task 可精确匹配。"""
        handles = [_FakeHandle(_event_xml('\\Task', pid=456))]
        query_patch, next_patch, render_patch, _ = self._patch_query([handles])
        with query_patch, next_patch, render_patch:
            events = task_monitor.query_task_events('\\Task', 10)

        self.assertEqual([event.pid for event in events], [456])
        self.assertEqual(events[0].task_path, '\\Task')

    def test_missing_pid_is_skipped(self):
        """缺少 ProcessID 的候选被跳过，其他候选仍返回。"""
        handles = [
            _FakeHandle(_event_xml('\\Folder\\Task', pid=None, record_id=201)),
            _FakeHandle(_event_xml('\\Folder\\Task', pid=789, record_id=200)),
        ]
        query_patch, next_patch, render_patch, _ = self._patch_query([handles])
        with query_patch, next_patch, render_patch:
            events = task_monitor.query_task_events('\\Folder\\Task', 10)

        self.assertEqual([event.pid for event in events], [789])

    def test_pid_not_guessed_from_other_integers(self):
        """只有 Priority 等其它整数时不产生 PID 候选。"""
        handles = [
            _FakeHandle(_event_xml(
                '\\Folder\\Task', pid=None, record_id=301,
                extra_data=(('Priority', '5'), ('UserContext', '7')))),
        ]
        query_patch, next_patch, render_patch, _ = self._patch_query([handles])
        with query_patch, next_patch, render_patch:
            events = task_monitor.query_task_events('\\Folder\\Task', 10)

        self.assertEqual(events, [])

    def test_empty_log_returns_empty_list(self):
        """EvtNext 返回空批次时返回空列表。"""
        query_patch, next_patch, render_patch, _ = self._patch_query([])
        with query_patch, next_patch, render_patch:
            events = task_monitor.query_task_events('\\Folder\\Task', 10)

        self.assertEqual(events, [])

    def test_evt_query_failure_raises(self):
        """EvtQuery 异常转换为 TaskQueryError。"""
        query_patch, next_patch, render_patch, _ = self._patch_query(
            [], query_error=RuntimeError('channel unavailable'))
        with query_patch, next_patch, render_patch:
            with self.assertRaises(task_monitor.TaskQueryError):
                task_monitor.query_task_events('\\Folder\\Task', 10)

    def test_evt_next_real_failure_raises(self):
        """EvtNext 的非 259 异常转换为 TaskQueryError。"""
        query_patch, next_patch, render_patch, _ = self._patch_query(
            [], next_error=pywintypes.error(5, 'EvtNext', 'access denied'))
        with query_patch, next_patch, render_patch:
            with self.assertRaises(task_monitor.TaskQueryError):
                task_monitor.query_task_events('\\Folder\\Task', 10)

    def test_evt_next_no_more_items_ends_enumeration(self):
        """winerror=259 视为正常结束，不抛异常。"""
        error_samples = (
            pywintypes.error(259, 'EvtNext', 'no more items'),
            _NoMoreItemsError(),
        )
        for error in error_samples:
            with self.subTest(error=type(error).__name__):
                query_patch, next_patch, render_patch, _ = self._patch_query(
                    [], next_error=error)
                with query_patch, next_patch, render_patch:
                    events = task_monitor.query_task_events('\\Folder\\Task', 10)
                self.assertEqual(events, [])

    def test_evt_render_failure_raises(self):
        """EvtRender 异常转换为 TaskQueryError，且事件句柄仍被关闭。"""
        handle = _FakeHandle(_event_xml('\\Folder\\Task', pid=555))
        query_patch, next_patch, render_patch, _ = self._patch_query([[handle]])
        with query_patch, next_patch, \
                patch.object(task_monitor.win32evtlog, 'EvtRender',
                             side_effect=RuntimeError('render failed')):
            with self.assertRaises(task_monitor.TaskQueryError):
                task_monitor.query_task_events('\\Folder\\Task', 10)

        self.assertEqual(handle.close_calls, 1)

    def test_query_closes_entire_batch_on_error(self):
        """首项渲染或中项解析失败都释放整批句柄。"""
        for failure_index, failure_kind in ((0, 'render'), (1, 'parse')):
            with self.subTest(index=failure_index, kind=failure_kind):
                handles = [_FakeHandle(_event_xml(
                    '\\Folder\\Task', pid=index + 1)) for index in range(3)]
                if failure_kind == 'parse':
                    handles[failure_index].xml_text = '<Event>'
                query_patch, next_patch, _, query_handle = self._patch_query(
                    [handles])

                def render(handle, event_type):
                    """在指定句柄上模拟渲染失败。"""
                    if failure_kind == 'render' and handle is handles[failure_index]:
                        raise RuntimeError('render failed')
                    return handle.xml_text

                with query_patch, next_patch, \
                        patch.object(task_monitor.win32evtlog, 'EvtRender',
                                     side_effect=render):
                    with self.assertRaises(task_monitor.TaskQueryError) as caught:
                        task_monitor.query_task_events('\\Folder\\Task', 10)
                self.assertIn('渲染' if failure_kind == 'render' else '解析',
                              str(caught.exception))
                self.assertEqual([handle.close_calls for handle in handles],
                                 [1, 1, 1])
                self.assertEqual(query_handle.close_calls, 1)

    def test_query_control_signal_closes_batch_even_if_close_fails(self):
        """控制信号保留原对象，关闭异常不妨碍其余句柄清理。"""
        for signal_type in (KeyboardInterrupt, SystemExit):
            for failure_index in (0, 1):
                with self.subTest(signal=signal_type.__name__,
                                  index=failure_index):
                    signal = signal_type(2)
                    handles = [_FakeHandle(_event_xml(
                        '\\Folder\\Task', pid=index + 1)) for index in range(3)]
                    query_patch, next_patch, _, query_handle = self._patch_query(
                        [handles])
                    close = handles[0].Close

                    def failing_close():
                        """先记录关闭，再模拟普通关闭错误。"""
                        close()
                        raise RuntimeError('close failed')

                    def render(handle, event_type):
                        """在首项或中项抛出原控制信号。"""
                        if handle is handles[failure_index]:
                            raise signal
                        return handle.xml_text

                    handles[0].Close = failing_close
                    with query_patch, next_patch, \
                            patch.object(task_monitor.win32evtlog, 'EvtRender',
                                         side_effect=render):
                        with self.assertRaises(signal_type) as caught:
                            task_monitor.query_task_events('\\Folder\\Task', 10)
                    self.assertIs(caught.exception, signal)
                    self.assertEqual([handle.close_calls for handle in handles],
                                     [1, 1, 1])
                    self.assertEqual(query_handle.close_calls, 1)

    def test_query_multiple_batches_and_next_failure_close_once(self):
        """正常跨批次不重复释放，枚举异常只释放已取得的资源。"""
        batches = [[_FakeHandle(_event_xml(
            '\\Folder\\Task', pid=pid)) for pid in pair]
                   for pair in ((1, 2), (3, 4))]
        query_patch, next_patch, render_patch, query_handle = self._patch_query(
            batches)
        with query_patch, next_patch, render_patch:
            events = task_monitor.query_task_events('\\Folder\\Task', 10)
        self.assertEqual([event.pid for event in events], [1, 2, 3, 4])
        self.assertEqual([handle.close_calls for batch in batches
                          for handle in batch], [1, 1, 1, 1])
        self.assertEqual(query_handle.close_calls, 1)

        handles = [_FakeHandle(_event_xml('\\Folder\\Task', pid=5))]
        query_patch, _, render_patch, query_handle = self._patch_query([])
        with query_patch, render_patch, \
                patch.object(task_monitor.win32evtlog, 'EvtNext',
                             side_effect=[handles, RuntimeError('next failed')]):
            with self.assertRaises(task_monitor.TaskQueryError):
                task_monitor.query_task_events('\\Folder\\Task', 10)
        self.assertEqual(handles[0].close_calls, 1)
        self.assertEqual(query_handle.close_calls, 1)

    def test_query_handle_closed_once(self):
        """查询句柄在正常与异常路径都恰好关闭一次。"""
        normal_handle = _FakeHandle()
        query_patch, next_patch, render_patch, _ = self._patch_query(
            [], query_handle=normal_handle)
        with query_patch, next_patch, render_patch:
            task_monitor.query_task_events('\\Folder\\Task', 10)
        self.assertEqual(normal_handle.close_calls, 1)

        failed_handle = _FakeHandle()
        query_patch, next_patch, render_patch, _ = self._patch_query(
            [], query_handle=failed_handle,
            next_error=pywintypes.error(5, 'EvtNext', 'access denied'))
        with query_patch, next_patch, render_patch:
            with self.assertRaises(task_monitor.TaskQueryError):
                task_monitor.query_task_events('\\Folder\\Task', 10)
        self.assertEqual(failed_handle.close_calls, 1)

    def test_results_newest_first(self):
        """结果保持事件日志的反向顺序，record_id 从大到小。"""
        handles = [
            _FakeHandle(_event_xml('\\Folder\\Task', pid=3, record_id=303)),
            _FakeHandle(_event_xml('\\Folder\\Task', pid=2, record_id=302)),
            _FakeHandle(_event_xml('\\Folder\\Task', pid=1, record_id=301)),
        ]
        query_patch, next_patch, render_patch, _ = self._patch_query([handles])
        with query_patch, next_patch, render_patch:
            events = task_monitor.query_task_events('\\Folder\\Task', 10)

        self.assertEqual([event.record_id for event in events], [303, 302, 301])

    def test_event_handle_closed_after_parse(self):
        """每个事件句柄解析后关闭一次。"""
        handles = [
            _FakeHandle(_event_xml('\\Folder\\Task', pid=11, record_id=401)),
            _FakeHandle(_event_xml('\\Folder\\Task', pid=12, record_id=400)),
        ]
        query_patch, next_patch, render_patch, _ = self._patch_query([handles])
        with query_patch, next_patch, render_patch:
            task_monitor.query_task_events('\\Folder\\Task', 10)

        self.assertEqual([handle.close_calls for handle in handles], [1, 1])

    def test_boundary_returns_empty_identity_when_empty(self):
        """可读空日志提供空边界且关闭查询句柄。"""
        query_handle = _FakeHandle()
        with patch.object(task_monitor.win32evtlog, 'EvtQuery',
                          return_value=query_handle), \
                patch.object(task_monitor.win32evtlog, 'EvtNext',
                             return_value=[]), \
                patch.object(task_monitor.win32evtlog, 'EvtRender',
                             side_effect=_fake_render):
            boundary = task_monitor.get_task_event_boundary()

        self.assertEqual(boundary, task_monitor.TaskEventBoundary(0, ''))
        self.assertEqual(query_handle.close_calls, 1)

    def test_boundary_returns_latest_record_id(self):
        """边界取最新一条事件的 EventRecordID，并关闭自身句柄。"""
        query_handle = _FakeHandle()
        event_handle = _FakeHandle(
            _event_xml('\\Folder\\Task', pid=99, record_id=339373))
        with patch.object(task_monitor.win32evtlog, 'EvtQuery',
                          return_value=query_handle), \
                patch.object(task_monitor.win32evtlog, 'EvtNext',
                             return_value=[event_handle]), \
                patch.object(task_monitor.win32evtlog, 'EvtRender',
                             side_effect=_fake_render):
            boundary = task_monitor.get_task_event_boundary()

        self.assertEqual(boundary.record_id, 339373)
        self.assertEqual(boundary.xml, event_handle.xml_text)
        self.assertEqual(query_handle.close_calls, 1)
        self.assertEqual(event_handle.close_calls, 1)


class _NoMoreItemsError(Exception):
    """带 winerror 属性的自定义异常，验证实现兼容两种抛出方式。"""

    winerror = 259


if __name__ == '__main__':
    unittest.main()
