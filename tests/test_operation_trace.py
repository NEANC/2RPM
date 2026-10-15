#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证阶段事件模型、关联身份、槽位终态与安全字段白名单。"""

from importlib import import_module
import inspect
import io
import logging
import time
from unittest.mock import Mock

import pytest


REQUIRED_SUMMARY_KEYS = (
    'images_processed',
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


def stage_records(text, stage):
    """从事件单行日志中提取指定阶段的字段映射，保持发生顺序。"""
    records = []
    marker = f' stage={stage} '
    for line in text.splitlines():
        if 'stage_event ' not in line or marker not in line + ' ':
            continue
        fields = {}
        for token in line.split('stage_event ', 1)[1].split():
            key, _, value = token.partition('=')
            fields[key] = value
        records.append(fields)
    return records


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

    started = [event for event in events if event.status == 'started']
    terminals = [event for event in events
                 if event.status in ('succeeded', 'failed', 'cancelled')]
    assert len(terminals) == len(started)
    assert len({event.operation_id for event in started}) == len(started)
    assert all(event.operation_id in {item.operation_id for item in started}
               for event in terminals)
    by_stage = {}
    for event in events:
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
    batch = next(event for event in events if event.stage == 'batch')
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
    batch = next(event for event in events if event.stage == 'batch')
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
    saved_handlers = target.handlers[:]
    saved_level = target.level
    saved_propagate = target.propagate
    target.handlers = [handler]
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
    assert 'stage=capture' in text and 'stage=slot' in text
    assert 'stage=batch' in text
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
    assert 'unknown' in terminal


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
    saved_handlers = target.handlers[:]
    saved_level = target.level
    saved_propagate = target.propagate
    target.handlers = [handler]
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
    records = stage_records(read_handler_bytes(handler), 'capture')
    handler.close()
    started = [item for item in records if item.get('status') == 'started']
    cancelled = [item for item in records if item.get('status') == 'cancelled']
    assert len(started) == 1 and len(cancelled) == 1
    assert started[0]['operation_id'] == cancelled[0]['operation_id']


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
        message = record.getMessage()
        if 'stage=metadata_cache' in message and 'status=failed' in message:
            levels['cache'] = record.levelno
        if 'stage=slot' in message and 'status=failed' in message:
            levels['slot'] = record.levelno
    assert levels['cache'] == logging.WARNING
    assert levels['slot'] == logging.ERROR
