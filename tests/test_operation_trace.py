#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证阶段事件模型、关联身份、槽位终态与安全字段白名单。"""

from contextlib import contextmanager
from importlib import import_module
import inspect
import io
import logging
import time
from unittest.mock import Mock

import pytest


REQUIRED_SUMMARY_KEYS = (
    'images_processed', 'images_succeeded', 'images_failed',
    'images_cancelled', 'images_skipped',
    'capture_started', 'capture_succeeded', 'capture_failed',
    'capture_retries', 'capture_format_fallbacks',
    'encode_started', 'encode_succeeded', 'encode_failed',
    'group_get_attempts', 'profile_get_attempts', 'upload_post_attempts',
    'preupload_refreshes', 'postupload_recoveries',
    'boltp_candidate_switches', 'host_failovers',
    'slots_planned', 'slots_succeeded', 'slots_failed', 'slots_cancelled',
    'slots_skipped',
)


def trace_module():
    """加载真实事件模块，使缺失接口成为明确断言失败。"""
    return import_module('modules.operation_trace')


def read_handler_bytes(handler):
    """读取真实文件处理器的最终字节并解码，用于核对落盘事实。"""
    handler.flush()
    with open(handler.baseFilename, 'rb') as stream:
        return stream.read().decode('utf-8')


class StageRecordCollector(logging.Handler):
    """收集事件模块的 LogRecord，供结构化断言，不渲染任何文本。"""

    def __init__(self):
        """初始化只收集记录、级别为 DEBUG 的内存处理器。"""
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        """保存一条记录，保持真实发生顺序。"""
        self.records.append(record)


def stage_events(records, stage):
    """返回指定阶段的安全 StageEvent，保持发生顺序。"""
    return [record.stage_event for record in records
            if getattr(record, 'stage_event', None) is not None
            and record.stage_event.stage == stage]


@contextmanager
def captured_records(logger):
    """在目标 logger 上临时挂载收集处理器并恢复原配置。"""
    collector = StageRecordCollector()
    saved_handlers = logger.handlers[:]
    saved_level = logger.level
    saved_propagate = logger.propagate
    logger.handlers = [collector]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        yield collector.records
    finally:
        logger.handlers = saved_handlers
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate


def test_no_active_scope_is_io_free_noop(monkeypatch):
    """无作用域时操作与跳过不分配身份、不抛错、不产生任何输出。"""
    module = trace_module()
    logger = Mock()
    monkeypatch.setattr(module, 'LOGGER', logger)
    monkeypatch.setattr(
        module, '_new_id',
        Mock(side_effect=AssertionError('无作用域不得分配标识')))
    with module.trace_operation('capture', 'read', source='adb') as operation:
        operation.finish('succeeded', width=1, height=1)
    module.trace_skipped('save', reason_code='not_configured')
    module.trace_skipped('save', reason_code='not_configured', path='忽略值')
    logger.log.assert_not_called()


def test_summary_exposes_all_required_keys():
    """汇总必须逐字提供后续任务引用的全部键。"""
    module = trace_module()
    with module.trace_scope(
            mode='automatic', images_planned=2, slots_planned=4) as trace:
        summary = trace.summary()
    for key in REQUIRED_SUMMARY_KEYS:
        assert key in summary
    owned = set(REQUIRED_SUMMARY_KEYS) - {'slots_planned'}
    assert all(summary[key] == 0 for key in owned)
    assert summary['slots_planned'] == 4
    assert summary['mode'] == 'automatic'
    assert isinstance(summary['status'], str)


def test_image_summary_counts_logical_terminal_states_independently():
    """逻辑图片终态独立计数，不以捕获操作次数替代图片结果。"""
    module = trace_module()
    with module.trace_scope(mode='automatic', images_planned=2) as trace:
        for _ in range(3):
            with module.trace_operation(
                    'capture', 'read', source='adb') as operation:
                operation.finish('succeeded', width=1, height=1)
        trace.finish_image(1, 'succeeded')
        trace.finish_image(2, 'failed')
        summary = trace.summary()
    assert (summary['images_succeeded'], summary['images_failed'],
            summary['images_cancelled'], summary['images_skipped']) == (
                1, 1, 0, 0)
    assert summary['images_processed'] == 2
    assert summary['images_processed'] == sum(
        summary['images_' + state]
        for state in ('succeeded', 'failed', 'cancelled', 'skipped'))
    assert summary['capture_succeeded'] == 3


def test_image_summary_counts_cancelled_and_skipped_once():
    """取消与跳过均为图片终态，重复收尾不改写首次结果。"""
    module = trace_module()
    with module.trace_scope(mode='automatic', images_planned=2) as trace:
        trace.finish_image(1, 'cancelled')
        trace.finish_image(1, 'succeeded')
        trace.finish_image(2, 'skipped')
        trace.finish_image(3, 'invalid')
        summary = trace.summary()
    assert (summary['images_succeeded'], summary['images_failed'],
            summary['images_cancelled'], summary['images_skipped']) == (
                0, 0, 1, 1)
    assert summary['images_processed'] == 1


def test_operation_pairing_and_monotonic_duration():
    """每个开始恰有一个终态，耗时取单调时钟且批次不等于子阶段之和。"""
    module = trace_module()
    events = []
    now = [1000.0]

    def clock():
        """返回可控的单调时间原点。"""
        return now[0]

    with module.trace_scope(
            mode='automatic', images_planned=1, clock=clock,
            on_status=events.append) as trace:
        with module.trace_operation(
                'capture', 'read', source='adb') as operation:
            now[0] += 0.25
            operation.finish('succeeded', width=4, height=5)
        with module.trace_operation(
                'encode', 'encode', image_format='jpeg') as operation:
            now[0] += 0.25
            operation.finish('failed', reason_code='encoding_failed')
        module.trace_skipped('save', reason_code='not_configured')
        now[0] += 1.0
        trace.summary()

    started = [event for event in events if event.status == 'started'
               and event.stage != 'batch']
    terminals = [event for event in events
                 if event.status in ('succeeded', 'failed', 'cancelled')]
    assert len(terminals) == len(started)
    assert len({event.operation_id for event in started}) == len(started)
    assert all(event.operation_id in {item.operation_id for item in started}
               for event in terminals)
    by_stage = {}
    for event in events:
        if event.stage == 'batch':
            continue
        counts = by_stage.setdefault(event.stage, {})
        counts[event.status] = counts.get(event.status, 0) + 1
    for counts in by_stage.values():
        assert counts.get('started', 0) == (
            counts.get('succeeded', 0) + counts.get('failed', 0)
            + counts.get('cancelled', 0))
    skipped = [event for event in events if event.status == 'skipped']
    assert len(skipped) == 1 and skipped[0].duration_ms is None
    capture = next(event for event in events
                   if event.stage == 'capture' and event.status == 'succeeded')
    encode = next(event for event in events
                  if event.stage == 'encode' and event.status == 'failed')
    assert capture.duration_ms == 250
    assert encode.duration_ms == 250
    batch = next(event for event in events if event.stage == 'batch'
                 and event.status != 'started')
    assert batch.duration_ms == 1500
    assert batch.duration_ms != capture.duration_ms + encode.duration_ms
    assert inspect.signature(
        module.trace_scope).parameters['clock'].default is time.monotonic


def test_slot_finish_is_only_terminal_producer():
    """槽位仅一个开始与一个终态，重复完成与未开始跳过互不干扰。"""
    module = trace_module()
    events = []
    with module.trace_scope(
            mode='cli', images_planned=1, slots_planned=3,
            on_status=events.append) as trace:
        trace.start_slot(1, 1, provider='catbox')
        trace.start_slot(1, 1, provider='catbox')
        trace.finish_slot(1, 1, 'succeeded')
        trace.finish_slot(1, 1, 'failed', reason_code='late')
        trace.finish_slot(2, 1, 'skipped', reason_code='host_succeeded')
        trace.finish_slot(3, 1, 'skipped', reason_code='not_configured')
        summary = trace.summary()
    slots = [event for event in events if event.stage == 'slot']
    starts = [event for event in slots if event.status == 'started']
    assert len(starts) == 1 and starts[0].action == 'upload'
    succeeded = [event for event in slots if event.status == 'succeeded']
    assert len(succeeded) == 1
    assert succeeded[0].operation_id == starts[0].operation_id
    assert not [event for event in slots if event.status == 'failed']
    assert len([event for event in slots if event.status == 'skipped']) == 2
    assert summary['slots_succeeded'] == 1
    assert summary['slots_failed'] == 0
    assert summary['slots_skipped'] == 2
    assert summary['slots_planned'] == 3
    for event in slots:
        assert event.request_id is None and event.attempt_index is None


def test_slot_cancellation_finishes_started_slot():
    """控制信号收尾时未完成槽位由唯一终态生产者标记取消。"""
    module = trace_module()
    events = []
    with pytest.raises(KeyboardInterrupt):
        with module.trace_scope(
                mode='cli', images_planned=1, slots_planned=1,
                on_status=events.append) as trace:
            trace.start_slot(1, 1, provider='catbox')
            raise KeyboardInterrupt('cancel')
    slots = [event for event in events if event.stage == 'slot']
    assert [event.status for event in slots] == ['started', 'cancelled']
    assert slots[0].operation_id == slots[1].operation_id
    assert slots[1].reason_code == 'batch_cancelled'
    assert slots[1].duration_ms is not None
    batch = next(event for event in events if event.stage == 'batch'
                 and event.status != 'started')
    assert batch.status == 'cancelled'


def test_storage_id_is_validated_by_provider():
    """存储编号按 provider 分支校验，未知或非法值一律不输出。"""
    module = trace_module()
    events = []
    with module.trace_scope(mode='cli', on_status=events.append):
        for provider, storage_id in (
                ('boltp', 0), ('boltp', -1), ('beeimg_cn', 5),
                ('beeimg_cn', 0), ('catbox', 3), ('boltp', True)):
            with module.trace_operation(
                    'upload_attempt', 'post', provider=provider,
                    storage_id=storage_id) as operation:
                operation.finish('succeeded')
    observed = [event.storage_id for event in events
                if event.stage == 'upload_attempt'
                and event.status == 'succeeded']
    assert observed == [0, None, 5, None, None, None]


def test_unknown_fields_and_objects_are_dropped():
    """未知键与对象值被丢弃，不 stringify 也不进入事件。"""
    module = trace_module()
    events = []
    marker = 'PRIVATE_OBJECT_8823'
    with module.trace_scope(mode='cli', on_status=events.append):
        with module.trace_operation(
                'capture', 'read', source=object(), target=marker,
                path=marker, width='not-an-int') as operation:
            operation.finish('succeeded', cache_key=marker, notes=marker)
    terminal = next(event for event in events
                    if event.stage == 'capture'
                    and event.status == 'succeeded')
    assert terminal.source is None
    assert terminal.width is None
    assert marker not in repr(terminal)


def test_stage_fields_never_serialize_private_values(tmp_path):
    """在 target/path/缓存键/bytes 等位置注入标记，最终文件不得出现。"""
    module = trace_module()
    logger_module = import_module('modules.logger')
    handler = logger_module._create_file_handler(str(tmp_path / 'logs'), 'app')
    assert handler is not None
    target = logging.getLogger('modules.operation_trace')
    collector = StageRecordCollector()
    saved_handlers = target.handlers[:]
    saved_level = target.level
    saved_propagate = target.propagate
    target.handlers = [handler, collector]
    target.setLevel(logging.DEBUG)
    target.propagate = False
    marker = 'PRIVATE_MARKER_4711'
    try:
        with module.trace_scope(mode='automatic', images_planned=1) as trace:
            with module.trace_operation(
                    'capture', 'read', source='adb', target=marker,
                    path=marker, cache_key=marker,
                    image_bytes=marker) as operation:
                operation.finish('succeeded', width=2, height=2, notes=marker)
            trace.start_slot(1, 1, provider='catbox')
            trace.finish_slot(1, 1, 'succeeded')
            module.trace_skipped(
                'save', reason_code='not_configured', path=marker)
            trace.summary()
    finally:
        target.handlers = saved_handlers
        target.setLevel(saved_level)
        target.propagate = saved_propagate
    path = handler.baseFilename
    handler.close()
    with open(path, 'rb') as stream:
        text = stream.read().decode('utf-8')
    observed = {event.stage for event in (
        record.stage_event for record in collector.records
        if getattr(record, 'stage_event', None) is not None)}
    assert {'capture', 'slot', 'batch'} <= observed
    assert 'stage_event ' not in text
    assert marker not in text


def test_unregistered_provider_falls_back_to_unknown(tmp_path):
    """未登记 provider 归入固定 unknown，原值不进入事件、文件与终端。"""
    module = trace_module()
    upload_status = import_module('modules.upload_status')
    logger_module = import_module('modules.logger')
    handler = logger_module._create_file_handler(str(tmp_path / 'logs'), 'app')
    assert handler is not None
    target = logging.getLogger('modules.operation_trace')
    saved_handlers = target.handlers[:]
    saved_level = target.level
    saved_propagate = target.propagate
    target.handlers = [handler]
    target.setLevel(logging.DEBUG)
    target.propagate = False
    marker = 'PRIVATE_HOST_8823'
    stream = io.StringIO()
    consume = upload_status.make_status_consumer(stream=stream)
    events = []

    def on_status(event):
        """记录事件并转发给真实终端消费者。"""
        events.append(event)
        consume(event)

    try:
        with module.trace_scope(
                mode='cli', images_planned=1, slots_planned=1,
                on_status=on_status) as trace:
            trace.start_slot(1, 1, provider=marker)
            trace.finish_slot(1, 1, 'succeeded')
    finally:
        target.handlers = saved_handlers
        target.setLevel(saved_level)
        target.propagate = saved_propagate
    text = read_handler_bytes(handler)
    handler.close()
    terminal = stream.getvalue()
    slots = [event for event in events if event.stage == 'slot']
    assert slots and all(event.provider == 'unknown' for event in slots)
    assert marker not in text and marker not in terminal
    assert '未知图床 第 2 项' in terminal


def test_provider_whitelist_matches_registry_keys():
    """阶段模块的 provider 白名单必须与注册表键集合一致，防止两处漂移。"""
    module = trace_module()
    registry = import_module('modules.image_host.registry')
    assert module._PROVIDER_WHITELIST == frozenset(registry.UPLOADERS)


def test_interrupted_started_event_still_emits_cancelled(tmp_path):
    """started 事件被控制信号打断时仍产生唯一 cancelled 终态并原样传播。"""
    module = trace_module()
    logger_module = import_module('modules.logger')
    handler = logger_module._create_file_handler(str(tmp_path / 'logs'), 'app')
    assert handler is not None
    target = logging.getLogger('modules.operation_trace')
    collector = StageRecordCollector()
    saved_handlers = target.handlers[:]
    saved_level = target.level
    saved_propagate = target.propagate
    target.handlers = [handler, collector]
    target.setLevel(logging.DEBUG)
    target.propagate = False
    signal = KeyboardInterrupt('synthetic-stop')

    def on_status(event):
        """在 capture 开始事件处抛出控制信号，模拟终端消费被中断。"""
        if event.stage == 'capture' and event.status == 'started':
            raise signal

    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            with module.trace_scope(
                    mode='automatic', images_planned=1, on_status=on_status):
                with module.trace_operation('capture', 'read', source='adb'):
                    pass
    finally:
        target.handlers = saved_handlers
        target.setLevel(saved_level)
        target.propagate = saved_propagate
    assert caught.value is signal
    records = stage_events(collector.records, 'capture')
    handler.close()
    started = [item for item in records if item.status == 'started']
    cancelled = [item for item in records if item.status == 'cancelled']
    assert len(started) == 1 and len(cancelled) == 1
    assert started[0].operation_id == cancelled[0].operation_id


def test_cache_failure_warns_and_slot_failure_errors(caplog):
    """缓存 IO 降级为 WARNING，槽位最终失败为 ERROR，各只记一次。"""
    module = trace_module()
    with caplog.at_level(logging.DEBUG, logger='modules.operation_trace'):
        with module.trace_scope(
                mode='cli', images_planned=1, slots_planned=1) as trace:
            with module.trace_operation(
                    'metadata_cache', 'load', cache_layer='disk',
                    provider='beeimg_cn') as operation:
                operation.finish('failed', reason_code='invalid')
            trace.start_slot(1, 1, provider='catbox')
            trace.finish_slot(1, 1, 'failed', reason_code='upload_failed')
    levels = {}
    for record in caplog.records:
        event = getattr(record, 'stage_event', None)
        if event is None or event.status != 'failed':
            continue
        if event.stage == 'metadata_cache':
            levels['cache'] = record.levelno
        if event.stage == 'slot':
            levels['slot'] = record.levelno
    assert levels['cache'] == logging.WARNING
    assert levels['slot'] == logging.ERROR


def test_metadata_query_levels_keep_details_debug_and_failure_warning(caplog):
    """元数据查询开始与成功为 DEBUG，失败为 WARNING，取消为 INFO。"""
    module = trace_module()
    with caplog.at_level(logging.DEBUG, logger='modules.operation_trace'):
        with module.trace_scope(mode='cli'):
            with module.trace_operation(
                    'metadata_query', 'group', provider='beeimg_cn') as operation:
                operation.finish('succeeded')
            with module.trace_operation(
                    'metadata_query', 'profile', provider='beeimg_cn') as operation:
                operation.finish('failed', reason_code='transport_failed')
            with pytest.raises(KeyboardInterrupt):
                with module.trace_operation(
                        'metadata_query', 'group', provider='beeimg_cn'):
                    raise KeyboardInterrupt('cancel')
    levels = {}
    for record in caplog.records:
        event = getattr(record, 'stage_event', None)
        if event is None or event.stage != 'metadata_query':
            continue
        levels.setdefault(event.status, []).append(record.levelno)
    assert levels['started'] == [logging.DEBUG] * 3
    assert levels['succeeded'] == [logging.DEBUG]
    assert levels['failed'] == [logging.WARNING]
    assert levels['cancelled'] == [logging.INFO]


def test_recoverable_failures_warn_while_skip_and_cancel_stay_info(caplog):
    """捕获与编码可恢复失败为 WARNING，跳过和取消仍为 INFO。"""
    module = trace_module()
    with caplog.at_level(logging.DEBUG, logger='modules.operation_trace'):
        with module.trace_scope(mode='automatic'):
            with module.trace_operation(
                    'capture', 'read', source='adb') as operation:
                operation.finish('failed', reason_code='capture_failed')
            with module.trace_operation(
                    'encode', 'encode', image_format='jpeg') as operation:
                operation.finish('failed', reason_code='encoding_failed')
            module.trace_skipped('capture', reason_code='not_configured')
            with pytest.raises(KeyboardInterrupt):
                with module.trace_operation(
                        'encode', 'encode', image_format='jpeg'):
                    raise KeyboardInterrupt('cancel')
    levels = {}
    for record in caplog.records:
        event = getattr(record, 'stage_event', None)
        if event is None or event.stage not in ('capture', 'encode'):
            continue
        if event.status in ('failed', 'skipped', 'cancelled'):
            levels[(event.stage, event.status)] = record.levelno
    assert levels[('capture', 'failed')] == logging.WARNING
    assert levels[('encode', 'failed')] == logging.WARNING
    assert levels[('capture', 'skipped')] == logging.INFO
    assert levels[('encode', 'cancelled')] == logging.INFO


def test_milliseconds_truncates_fractional_millisecond():
    """全局指标保持原有 int 截断口径。"""
    module = trace_module()
    assert module._milliseconds(0.0019) == 1


def test_window_capture_total_is_whitelisted_and_labeled():
    """窗口截图全流程为独立阶段，动作与中文标签进入固定白名单。"""
    module = trace_module()
    whitelists = module._CLASSIFICATION_WHITELISTS
    assert 'window_capture' in whitelists['stage']
    assert 'total' in whitelists['action']
    for status, word in (
            ('started', '开始'), ('succeeded', '成功'),
            ('failed', '失败'), ('cancelled', '取消')):
        line = module._format_event(module._create_event(
            stage='window_capture', action='total', status=status))
        assert line == f'窗口截图全流程{word}'


def test_window_capture_total_levels(caplog):
    """总耗时观察的开始为 DEBUG，成功与取消为 INFO，失败为 WARNING。"""
    module = trace_module()
    with caplog.at_level(logging.DEBUG, logger='modules.operation_trace'):
        with module.trace_scope(mode='cli'):
            with module.trace_operation('window_capture', 'total'):
                pass
            with module.trace_operation(
                    'window_capture', 'total') as operation:
                operation.finish('failed', reason_code='window_gone')
            with pytest.raises(KeyboardInterrupt):
                with module.trace_operation('window_capture', 'total'):
                    raise KeyboardInterrupt('cancel')
    levels = {}
    for record in caplog.records:
        event = getattr(record, 'stage_event', None)
        if event is None or event.stage != 'window_capture':
            continue
        levels.setdefault(event.status, []).append(record.levelno)
    assert levels['started'] == [logging.DEBUG] * 3
    assert levels['succeeded'] == [logging.INFO]
    assert levels['failed'] == [logging.WARNING]
    assert levels['cancelled'] == [logging.INFO]


def test_window_capture_total_keeps_capture_identity_and_counts():
    """总耗时只作观察，不冒充截图关联标识，也不增加截图与编码计数。"""
    module = trace_module()
    events = []
    with module.trace_scope(
            mode='cli', images_planned=1, on_status=events.append) as trace:
        with module.trace_operation('window_capture', 'total'):
            pass
        assert module.current_capture_operation_id() is None
        with module.trace_operation('capture', 'read', source='window'):
            pass
        capture_id = next(
            event.operation_id for event in events
            if event.stage == 'capture' and event.status == 'started')
        assert module.current_capture_operation_id() == capture_id
        summary = trace.summary()
    assert summary['capture_started'] == summary['capture_succeeded'] == 1
    assert summary['encode_started'] == 0
    totals = [event for event in events if event.stage == 'window_capture']
    assert [event.status for event in totals] == ['started', 'succeeded']
    assert totals[0].operation_id == totals[1].operation_id
    assert capture_id not in {event.operation_id for event in totals}


BATCH_LINE_SUMMARY_KEYS = (
    'mode', 'status', 'images_planned', 'images_processed',
    'images_succeeded', 'images_failed', 'images_cancelled', 'images_skipped',
    'capture_started', 'capture_succeeded', 'capture_failed',
    'capture_cancelled', 'capture_skipped', 'capture_retries',
    'capture_format_fallbacks',
    'encode_started', 'encode_succeeded', 'encode_failed',
    'encode_cancelled', 'encode_skipped',
    'save_started', 'save_succeeded', 'save_failed', 'save_cancelled',
    'save_skipped',
    'group_get_attempts', 'group_get_succeeded', 'group_get_failed',
    'group_get_cancelled',
    'profile_get_attempts', 'profile_get_succeeded', 'profile_get_failed',
    'profile_get_cancelled',
    'upload_post_attempts', 'upload_post_succeeded', 'upload_post_failed',
    'upload_post_cancelled',
    'preupload_refreshes', 'postupload_recoveries',
    'boltp_candidate_switches', 'host_failovers', 'fallbacks_skipped',
    'slots_planned', 'slots_succeeded', 'slots_failed', 'slots_cancelled',
    'slots_skipped', 'duration_ms',
)


@contextmanager
def bound_file_handler(tmp_path, name='trace'):
    """建立真实文件处理器与收集处理器并绑定，供落盘与结构化双重断言。"""
    logger_module = import_module('modules.logger')
    handler = logger_module._create_file_handler(str(tmp_path / 'logs'), name)
    assert handler is not None
    collector = StageRecordCollector()
    target = logging.getLogger('modules.operation_trace')
    saved_handlers = target.handlers[:]
    saved_level = target.level
    saved_propagate = target.propagate
    target.handlers = [handler, collector]
    target.setLevel(logging.DEBUG)
    target.propagate = False
    try:
        yield handler, collector.records
    finally:
        target.handlers = saved_handlers
        target.setLevel(saved_level)
        target.propagate = saved_propagate
        handler.close()


def test_batch_line_carries_required_summary_dimensions(tmp_path):
    """唯一批次行携带规格汇总维度，结构化取值与真实事实一致。"""
    module = trace_module()
    now = [1000.0]

    def clock():
        """返回可控的单调时间原点。"""
        return now[0]

    with bound_file_handler(tmp_path) as (handler, records):
        with module.trace_scope(
                mode='cli', images_planned=2, slots_planned=3,
                clock=clock) as trace:
            with module.trace_operation(
                    'capture', 'read', source='adb') as operation:
                now[0] += 0.1
                operation.finish('succeeded', width=1, height=1)
            with module.trace_operation(
                    'encode', 'encode', image_format='jpeg') as operation:
                now[0] += 0.1
                operation.finish('succeeded', byte_count=10)
            module.trace_skipped('save', reason_code='not_configured')
            with module.trace_request('beeimg_cn', 'group') as request:
                request.mark_transport_started()
                request.finish('succeeded')
            with module.trace_request('beeimg_cn', 'profile') as request:
                request.mark_transport_started()
                request.finish('failed', reason_code='forbidden')
            with module.trace_request('catbox', 'upload_attempt') as request:
                request.mark_transport_started()
                request.finish('succeeded')
            module.trace_skipped('fallback', reason_code='budget_used',
                                  fallback_kind='postupload_recovery')
            trace.start_slot(1, 0, provider='catbox')
            trace.finish_slot(1, 0, 'succeeded')
            trace.finish_slot(1, 1, 'skipped', reason_code='host_succeeded')
            trace.finish_slot(1, 2, 'failed', reason_code='upload_failed')
        text = read_handler_bytes(handler)

    finals = [record for record in records
              if getattr(record, 'stage_event', None) is not None
              and record.stage_event.stage == 'batch'
              and record.stage_event.status != 'started']
    assert len(finals) == 1
    fields = finals[0].stage_summary
    for key in BATCH_LINE_SUMMARY_KEYS:
        if key == 'duration_ms':
            continue
        assert key in fields, key
    assert fields['mode'] == 'cli'
    assert fields['images_planned'] == 2
    assert (fields['capture_started'], fields['capture_succeeded'],
            fields['capture_failed']) == (1, 1, 0)
    assert (fields['encode_started'], fields['encode_succeeded'],
            fields['encode_failed']) == (1, 1, 0)
    assert (fields['save_started'], fields['save_skipped']) == (0, 1)
    assert (fields['group_get_attempts'],
            fields['group_get_succeeded']) == (1, 1)
    assert (fields['profile_get_attempts'],
            fields['profile_get_failed']) == (1, 1)
    assert (fields['upload_post_attempts'],
            fields['upload_post_succeeded']) == (1, 1)
    assert fields['fallbacks_skipped'] == 1
    assert (fields['slots_planned'], fields['slots_succeeded'],
            fields['slots_failed'], fields['slots_skipped']) == (3, 1, 1, 1)
    assert finals[0].stage_event.duration_ms == 200
    assert text.count('截图批次') == 2
    assert text.count('生成成功') == 1
    assert 'stage_event ' not in text


@pytest.mark.parametrize(('kind', 'counter'), [
    ('preupload_refresh', 'preupload_refreshes'),
    ('boltp_candidate', 'boltp_candidate_switches'),
])
def test_generic_fallback_counters_remain_independent(kind, counter):
    """新上传链不再产生旧回退，通用计数仍按回退种类独立累计。"""
    module = trace_module()
    with module.trace_scope(mode='cli') as trace:
        with module.trace_fallback(kind, 'switch', reason_code='legacy'):
            pass
    assert trace.summary()[counter] == 1


@pytest.mark.parametrize('planned', [None, 0])
def test_planned_counts_preserve_unknown_and_explicit_zero(tmp_path, planned):
    """未知计划量保留 None 且中文摘要显示未知，显式零仍为零。"""
    module = trace_module()
    with bound_file_handler(tmp_path, f'planned-{planned}') as (handler, records):
        with module.trace_scope(
                mode='cli', images_planned=planned,
                slots_planned=planned) as trace:
            summary = trace.summary()
        text = read_handler_bytes(handler)
    assert summary['images_planned'] is planned
    assert summary['slots_planned'] is planned
    finals = [record for record in records
              if getattr(record, 'stage_event', None) is not None
              and record.stage_event.stage == 'batch'
              and record.stage_event.status != 'started']
    assert len(finals) == 1
    assert finals[0].stage_summary['images_planned'] is planned
    assert finals[0].stage_summary['slots_planned'] is planned
    assert text.count('截图批次') == 2
    expected = '未知' if planned is None else '0'
    assert f'计划图片 {expected}' in text
    assert f'计划槽位 {expected}' in text


def test_batch_summary_balances_terminal_counts_on_cancel():
    """取消收尾时各阶段开始数等于成功、失败与取消数之和。"""
    module = trace_module()
    holder = {}
    with pytest.raises(KeyboardInterrupt):
        with module.trace_scope(mode='cli', images_planned=1) as trace:
            holder['trace'] = trace
            with module.trace_operation(
                    'capture', 'read', source='adb') as operation:
                operation.finish('failed', reason_code='no_frame')
            with module.trace_operation('save', 'save'):
                raise KeyboardInterrupt('cancel')
    summary = holder['trace'].summary()
    for stage in ('capture', 'encode', 'save'):
        assert summary[f'{stage}_started'] == (
            summary[f'{stage}_succeeded'] + summary[f'{stage}_failed']
            + summary[f'{stage}_cancelled'])
    assert summary['capture_failed'] == 1
    assert summary['save_cancelled'] == 1


@pytest.mark.parametrize('signal', [KeyboardInterrupt('stop'), SystemExit(9)])
def test_batch_status_control_signal_restores_parent_context(signal):
    """批次最终事件回调抛控制信号时恢复进入作用域前的全部上下文。"""
    module = trace_module()
    parent_trace = object()
    parent_item = (7, 8)
    parent_request = 'parent-request'
    trace_token = module._TRACE.set(parent_trace)
    item_token = module._ITEM.set(parent_item)
    request_token = module._REQUEST.set(parent_request)

    def interrupt_batch(event):
        """仅在批次最终事件写出时抛出指定控制信号。"""
        if event.stage == 'batch' and event.status != 'started':
            raise signal

    try:
        with pytest.raises(type(signal)) as caught:
            with module.trace_scope(mode='cli', on_status=interrupt_batch):
                pass
        assert caught.value is signal
        assert module._TRACE.get() is parent_trace
        assert module._ITEM.get() == parent_item
        assert module._REQUEST.get() == parent_request
    finally:
        module._REQUEST.reset(request_token)
        module._ITEM.reset(item_token)
        module._TRACE.reset(trace_token)


@pytest.mark.parametrize('signal', [KeyboardInterrupt('stop'), SystemExit(9)])
def test_upload_started_control_signal_does_not_count_transport(signal):
    """上传开始事件写出被控制信号打断时配对取消但不计真实 POST。"""
    module = trace_module()
    events = []
    holder = {}

    def interrupt_upload_start(event):
        """记录事件并仅在上传开始事件处中断。"""
        events.append(event)
        if event.stage == 'upload_attempt' and event.status == 'started':
            raise signal

    with pytest.raises(type(signal)) as caught:
        with module.trace_scope(
                mode='cli', on_status=interrupt_upload_start) as trace:
            holder['trace'] = trace
            with module.trace_request('catbox', 'upload_attempt'):
                raise AssertionError('不得进入真实传输调用')
    assert caught.value is signal
    attempts = [event for event in events if event.stage == 'upload_attempt']
    assert [event.status for event in attempts] == ['started', 'cancelled']
    assert attempts[0].operation_id == attempts[1].operation_id
    summary = holder['trace'].summary()
    assert summary['upload_post_attempts'] == 0
    assert summary['upload_post_cancelled'] == 0


def test_batch_finalize_adds_no_requests(monkeypatch, tmp_path):
    """批次收尾只汇总已有事实，不新增任何真实请求。"""
    module = trace_module()
    stage = import_module('test_upload_stage_logging')
    registry = import_module('modules.image_host.registry')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    context, cache, transport = stage.build(
        monkeypatch, tmp_path,
        replies=[stage.group_reply(), stage.SUCCESS_UPLOAD])
    guards = []

    def forbidden(*args, **kwargs):
        """收尾阶段的额外业务 IO 或凭证读取必须立即失败。"""
        pytest.fail('批次收尾不得新增 IO 或凭证读取')

    with module.trace_scope(
            mode='cli', images_planned=1, slots_planned=1) as trace:
        with module.trace_item(image_index=1, slot_index=0):
            registry.upload_with_fallback(
                b'image', 'image.png',
                [{'provider': 'beeimg_cn', 'token': '',
                  'options': {'storage_id': 13}}], context=context)
        before = (len(transport.posts), len(transport.gets('group')),
                  len(transport.gets('profile')))
        for owner, names in (
                (cache, ('identity', 'load', 'save', 'invalidate')),
                (context, ('_credential', 'get_metadata')),
                (import_module('modules.image_host.context'), ('resolve_token',)),
                (import_module('builtins'), ('open',)),
                (import_module('requests.adapters').HTTPAdapter, ('send',))):
            for name in names:
                guard = Mock(side_effect=forbidden)
                guards.append(guard)
                monkeypatch.setattr(owner, name, guard)
    after = (len(transport.posts), len(transport.gets('group')),
             len(transport.gets('profile')))
    context.close()
    assert before[0] == 1
    assert after == before
    for guard in guards:
        guard.assert_not_called()


SUMMARY_KEYS = frozenset(BATCH_LINE_SUMMARY_KEYS) - {'duration_ms'}


def contains_cjk(text):
    """判断文本是否含中日韩统一表意文字，用于确认中文投影。"""
    return any('\u4e00' <= char <= '\u9fff' for char in text)


def test_chinese_projection_all_categories():
    """全部既有阶段、动作与原因白名单投影为固定中文，未知安全回退。"""
    module = trace_module()
    whitelists = module._CLASSIFICATION_WHITELISTS
    for stage in whitelists['stage']:
        if stage == 'batch':
            continue
        for status in whitelists['status']:
            event = module._create_event(
                stage=stage, action='read', status=status)
            line = module._format_event(event)
            assert 'stage_event' not in line
            assert contains_cjk(line), (stage, status, line)
    unknown_stage = module._format_event(
        module._create_event(stage='secret', status='succeeded'))
    assert '未知阶段' in unknown_stage
    for code in whitelists['reason_code']:
        if code == '':
            continue
        line = module._format_event(module._create_event(
            stage='slot', action='upload', status='failed',
            provider='catbox', reason_code=code))
        assert '，原因：' in line, code
        assert contains_cjk(line.split('原因：', 1)[1]), (code, line)
    unknown_reason = module._format_event(module._create_event(
        stage='slot', action='upload', status='failed',
        provider='catbox', reason_code='secret'))
    assert '，原因：未知原因' in unknown_reason


def test_request_diagnostics_preserved():
    """请求终态保留已知 HTTP、耗时与安全原因，缺失值不捏造。"""
    module = trace_module()
    full = module._format_event(module._create_event(
        stage='upload_attempt', action='post', status='failed',
        provider='beeimg_cn', image_index=1, slot_index=1, attempt_index=1,
        http_status=200, duration_ms=83, reason_code='storage_unavailable'))
    assert ('通道 [BeeIMG.CN] 第 1 次上传请求失败，HTTP 200，耗时 83 ms，'
            '原因：storage_id 不可用') in full
    no_response = module._format_event(module._create_event(
        stage='upload_attempt', action='post', status='failed',
        provider='beeimg_cn', attempt_index=1, duration_ms=83,
        reason_code='transport_failed'))
    assert 'HTTP' not in no_response
    no_duration = module._format_event(module._create_event(
        stage='upload_attempt', action='post', status='failed',
        provider='beeimg_cn', attempt_index=1, http_status=200,
        reason_code='http_failed'))
    assert '耗时' not in no_duration


def test_image_upload_stats_from_slots(caplog):
    """逐图上传统计以图片身份派生，业务显式终态与槽位计数逐项一致。"""
    module = trace_module()
    with caplog.at_level(logging.DEBUG, logger='modules.operation_trace'):
        with module.trace_scope(
                mode='automatic', images_planned=2, slots_planned=4) as trace:
            trace.finish_image(1, 'succeeded')
            trace.start_slot(1, 0, provider='catbox')
            trace.finish_slot(1, 0, 'failed', reason_code='upload_failed')
            trace.start_slot(1, 1, provider='wmimg')
            trace.finish_slot(1, 1, 'failed', reason_code='http_failed')
            trace.finish_image(2, 'failed')
            trace.start_slot(2, 0, provider='catbox')
            trace.finish_slot(2, 0, 'skipped', reason_code='host_failed')
            trace.start_slot(2, 1, provider='wmimg')
            trace.finish_slot(2, 1, 'skipped', reason_code='host_failed')
            trace.set_status('failed')
    summary = trace.summary()
    assert trace._image_upload_stats() == {
        'succeeded': 0, 'cancelled': 0, 'failed': 1, 'not_executed': 1}
    assert summary['status'] == 'failed'
    assert (summary['slots_failed'], summary['slots_skipped']) == (2, 2)
    assert '生成成功 1/2' in caplog.text


def test_image_upload_stats_groups_images_not_slot_totals():
    """成功图片数按图片身份分组，不等同于槽位成功总数。"""
    module = trace_module()
    with module.trace_scope(
            mode='cli', images_planned=2, slots_planned=4) as trace:
        for image_index, statuses in ((1, ('succeeded', 'succeeded')),
                                      (2, ('succeeded', 'failed'))):
            trace.finish_image(image_index, 'succeeded')
            for slot_index, status in enumerate(statuses):
                trace.start_slot(image_index, slot_index, provider='catbox')
                trace.finish_slot(image_index, slot_index, status)
    summary = trace.summary()
    assert trace._image_upload_stats()['succeeded'] == 2
    assert summary['slots_succeeded'] == 3


@pytest.mark.parametrize('image_status', ['succeeded', 'failed'])
def test_image_upload_stats_counts_images_without_slots_as_not_executed(
        image_status):
    """无槽位的仅保存与配置失败图片计入未执行上传，不显示上传成功。"""
    module = trace_module()
    with module.trace_scope(mode='cli', images_planned=1, slots_planned=0) as trace:
        trace.finish_image(1, image_status)
    assert trace._image_upload_stats() == {
        'succeeded': 0, 'cancelled': 0, 'failed': 0, 'not_executed': 1}


def test_image_upload_stats_cancelled_outranks_failed():
    """同一图片无成功但含取消时归为上传取消，优先于失败。"""
    module = trace_module()
    with module.trace_scope(mode='cli', images_planned=1, slots_planned=2) as trace:
        trace.finish_image(1, 'failed')
        trace.start_slot(1, 0, provider='catbox')
        trace.finish_slot(1, 0, 'failed', reason_code='upload_failed')
        trace.start_slot(1, 1, provider='catbox')
        trace.finish_slot(1, 1, 'cancelled', reason_code='batch_cancelled')
    assert trace._image_upload_stats() == {
        'succeeded': 0, 'cancelled': 1, 'failed': 0, 'not_executed': 0}


def test_structured_extra_and_safe_formatter(tmp_path):
    """结构化 extra 保留安全事件与摘要副本，落盘文本无旧英文长串。"""
    module = trace_module()
    events = []
    with bound_file_handler(tmp_path) as (handler, records):
        with module.trace_scope(
                mode='automatic', images_planned=1,
                on_status=events.append) as trace:
            trace.finish_image(1, 'failed')
            with module.trace_operation(
                    'capture', 'read', source='window') as operation:
                operation.finish('succeeded', width=1, height=1)
        summary = trace.summary()
        text = read_handler_bytes(handler)
    assert [record.stage_event for record in records] == events
    assert all(record.stage_event is event
               for record, event in zip(records, events))
    final = records[-1]
    assert final.stage_event.stage == 'batch'
    assert final.stage_event.status == 'failed'
    assert final.stage_summary == summary
    assert final.image_upload_stats == {
        'succeeded': 0, 'cancelled': 0, 'failed': 0, 'not_executed': 1}
    assert set(summary) == SUMMARY_KEYS
    for record in records:
        if record.stage_event.stage == 'batch':
            continue
        assert record.stage_summary is None
        assert record.image_upload_stats is None
    assert 'stage_event ' not in text


def test_transport_counts_not_started_counts(monkeypatch, tmp_path):
    """请求计数只由真实传输登记驱动，与替身实际发送逐项相等。"""
    module = trace_module()
    stage = import_module('test_upload_stage_logging')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    run = stage.run_chain(
        monkeypatch, tmp_path,
        [{'provider': 'beeimg_cn', 'token': 'synthetic',
          'options': {'storage_id': 13}}],
        replies=[stage.SUCCESS_UPLOAD])
    summary = run.summary
    assert summary['group_get_attempts'] == len(
        run.transport.gets('group')) == 0
    assert summary['profile_get_attempts'] == len(
        run.transport.gets('profile')) == 0
    assert summary['upload_post_attempts'] == len(run.transport.posts) == 1
    assert (summary['group_get_succeeded'], summary['group_get_failed'],
            summary['group_get_cancelled']) == (0, 0, 0)
    assert (summary['profile_get_succeeded'], summary['profile_get_failed'],
            summary['profile_get_cancelled']) == (0, 0, 0)
    assert (summary['upload_post_succeeded'], summary['upload_post_failed'],
            summary['upload_post_cancelled']) == (1, 0, 0)

    cancelled = []

    def cancel_at_started(event):
        """记录事件并在查询开始处抛出控制信号，模拟传输前取消。"""
        cancelled.append(event)
        if event.stage == 'metadata_query' and event.status == 'started':
            raise KeyboardInterrupt('stop')

    with pytest.raises(KeyboardInterrupt):
        with module.trace_scope(mode='cli', on_status=cancel_at_started) as trace:
            with module.trace_request('beeimg_cn', 'group'):
                pass
    assert len([event for event in cancelled if event.stage == 'metadata_query'
                and event.status == 'started']) == 1
    assert trace.summary()['group_get_attempts'] == 0

    with pytest.raises(KeyboardInterrupt):
        with module.trace_scope(mode='cli') as trace:
            with module.trace_request('catbox', 'upload_attempt') as request:
                request.mark_transport_started()
                raise KeyboardInterrupt('stop')
    during = trace.summary()
    assert during['upload_post_attempts'] == 1
    assert during['upload_post_cancelled'] == 1
