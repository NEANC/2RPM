#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证截图、编码与保存按真实操作边界记录与计数，全部替身化。"""

from argparse import Namespace
from contextlib import contextmanager
from importlib import import_module
from io import BytesIO
import logging
import struct
from unittest.mock import call, Mock

from PIL import Image
import pytest

from modules.image_host.core import UploadResult
from modules.operation_trace import trace_item
from modules.operation_trace import trace_scope
from modules.screenshot.models import CaptureError
from modules.screenshot.models import CaptureResult
from modules.screenshot.retention import RetentionPolicy


SERIAL = '127.0.0.1:16384'


def adb_backend():
    """加载真实 ADB 后端，使缺失接口成为明确断言失败。"""
    return import_module('modules.screenshot.adb')


def make_png():
    """在内存生成可实际解码的合成 PNG。"""
    with Image.new('RGB', (2, 2)) as image:
        with BytesIO() as output:
            image.save(output, format='PNG')
            return output.getvalue()


def make_raw():
    """生成合法头部与精确长度的 RGBA_8888 合成 raw。"""
    return struct.pack('<III', 2, 2, 1) + bytes([1, 2, 3, 255] * 4)


def fail_first_saves(monkeypatch, failures):
    """让前若干次 Pillow 写入失败，模拟真实编码失败边界。"""
    original = Image.Image.save
    calls = []

    def save(image, stream, **kwargs):
        """记录写入次数，达到阈值后沿用真实编码。"""
        calls.append(kwargs.get('format'))
        if len(calls) <= failures:
            raise ValueError('encoding-failure-marker')
        return original(image, stream, **kwargs)

    monkeypatch.setattr(Image.Image, 'save', save)
    return calls


def run_capture(backend, monkeypatch, outcomes, **kwargs):
    """在同一批作用域内执行一次真实 ADB 采集并返回汇总。"""
    read = Mock(side_effect=outcomes)
    monkeypatch.setattr(backend, '_read_capture', read)
    with trace_scope(mode='automatic', images_planned=1) as trace:
        with trace_item(image_index=1):
            result = backend.capture_adb(SERIAL, **kwargs)
        trace.finish_image(1, 'succeeded')
        summary = trace.summary()
    return result, summary, read


def test_two_raw_then_png_counts_three_captures(monkeypatch):
    """两次 raw 解析失败后 PNG 成功：三捕获、两失败、一次格式回退。"""
    backend = adb_backend()
    result, summary, read = run_capture(
        backend, monkeypatch, [b'bad-one', b'bad-two', make_png()])
    assert result.image_format == 'jpeg'
    assert (summary['images_processed'], summary['capture_started']) == (1, 3)
    assert (summary['capture_succeeded'], summary['capture_failed']) == (1, 2)
    assert summary['capture_format_fallbacks'] == 1
    assert [item.args for item in read.call_args_list] == [
        (SERIAL, False), (SERIAL, False), (SERIAL, True)]


def test_encoding_retry_does_not_rewrite_capture_success(monkeypatch):
    """raw 采集成功但编码失败仍重采：三次捕获成功、两次编码失败。"""
    backend = adb_backend()
    raw, png = make_raw(), make_png()
    read = Mock(side_effect=[raw, raw, png])
    monkeypatch.setattr(backend, '_read_capture', read)
    fail_first_saves(monkeypatch, 2)
    with trace_scope(mode='automatic', images_planned=1) as trace:
        with trace_item(image_index=1):
            result = backend.capture_adb(SERIAL, purpose='automatic')
        trace.finish_image(1, 'succeeded')
        summary = trace.summary()
    assert result.image_format == 'jpeg'
    assert (summary['capture_started'], summary['capture_succeeded'],
            summary['capture_failed']) == (3, 3, 0)
    assert (summary['encode_started'], summary['encode_succeeded'],
            summary['encode_failed']) == (3, 1, 2)
    assert summary['capture_retries'] == 1
    assert summary['capture_format_fallbacks'] == 1


def test_first_encode_failure_second_raw_success(monkeypatch):
    """首次编码失败后第二次 raw 成功：两捕获成功、无格式回退。"""
    backend = adb_backend()
    raw = make_raw()
    read = Mock(side_effect=[raw, raw])
    monkeypatch.setattr(backend, '_read_capture', read)
    fail_first_saves(monkeypatch, 1)
    with trace_scope(mode='automatic', images_planned=1) as trace:
        with trace_item(image_index=1):
            result = backend.capture_adb(SERIAL, purpose='automatic')
        trace.finish_image(1, 'succeeded')
        summary = trace.summary()
    assert result.image_format == 'jpeg'
    assert (summary['capture_started'], summary['capture_succeeded'],
            summary['capture_failed']) == (2, 2, 0)
    assert (summary['encode_started'], summary['encode_succeeded'],
            summary['encode_failed']) == (2, 1, 1)
    assert summary['capture_retries'] == 1
    assert summary['capture_format_fallbacks'] == 0


def test_cli_png_has_no_encode_start(monkeypatch):
    """CLI 原生 PNG 复用原始字节，不重新编码也不产生编码开始。"""
    backend = adb_backend()
    png = make_png()
    read = Mock(return_value=png)
    monkeypatch.setattr(backend, '_read_capture', read)
    with trace_scope(mode='cli', images_planned=1) as trace:
        with trace_item(image_index=1):
            result = backend.capture_adb(
                SERIAL, purpose='cli', image_format='png')
        trace.finish_image(1, 'succeeded')
        summary = trace.summary()
    assert result.image_format == 'png' and result.image_bytes == png
    assert summary['capture_started'] == summary['capture_succeeded'] == 1
    assert summary['encode_started'] == 0
    assert read.call_args_list == [call(SERIAL, True)]


def test_cli_timeout_prefers_png_without_retry(monkeypatch):
    """CLI raw 超时立即回退 PNG，不重采也不编码。"""
    backend = adb_backend()
    read = Mock(side_effect=[backend._error('adb_timeout'), make_png()])
    monkeypatch.setattr(backend, '_read_capture', read)
    with trace_scope(mode='cli', images_planned=1) as trace:
        with trace_item(image_index=1):
            result = backend.capture_adb(SERIAL, purpose='cli')
        trace.finish_image(1, 'succeeded')
        summary = trace.summary()
    assert result.image_format == 'png'
    assert summary['capture_retries'] == 0
    assert summary['capture_format_fallbacks'] == 1
    assert (summary['capture_started'], summary['capture_failed']) == (2, 1)
    assert summary['encode_started'] == 0
    assert [item.args for item in read.call_args_list] == [
        (SERIAL, False), (SERIAL, True)]


def test_response_too_large_uses_single_png(monkeypatch):
    """raw 响应超限不重试，自动路径编码回退后的 PNG。"""
    backend = adb_backend()
    read = Mock(side_effect=[backend.ResponseTooLarge(), make_png()])
    monkeypatch.setattr(backend, '_read_capture', read)
    with trace_scope(mode='automatic', images_planned=1) as trace:
        with trace_item(image_index=1):
            result = backend.capture_adb(SERIAL, purpose='automatic')
        trace.finish_image(1, 'succeeded')
        summary = trace.summary()
    assert result.image_format == 'jpeg'
    assert summary['capture_retries'] == 0
    assert summary['capture_format_fallbacks'] == 1
    assert [item.args for item in read.call_args_list] == [
        (SERIAL, False), (SERIAL, True)]


def test_capture_cancellation_records_cancelled_and_propagates():
    """采集中的控制信号原对象传播，阶段终态为取消。"""
    module = import_module('modules.operation_trace')
    events = []
    signal = KeyboardInterrupt('stop')
    with pytest.raises(KeyboardInterrupt) as caught:
        with trace_scope(mode='automatic', images_planned=1,
                         on_status=events.append):
            with trace_item(image_index=1):
                with module.trace_operation(
                        'capture', 'read', source='adb') as operation:
                    raise signal
    assert caught.value is signal
    capture = [event for event in events if event.stage == 'capture']
    assert [event.status for event in capture] == ['started', 'cancelled']
    assert capture[0].operation_id == capture[1].operation_id
    assert capture[1].duration_ms is not None


def test_save_failure_is_recorded_once(monkeypatch, tmp_path):
    """保存真实执行且写入失败时记录开始与唯一失败终态。"""
    retention = import_module('modules.screenshot.retention')
    monkeypatch.setattr(retention.winfs, 'list_names', lambda path: ())

    def fail_write(path, data):
        """模拟受管目录写入失败。"""
        raise OSError('save-failure-marker')

    monkeypatch.setattr(retention.winfs, 'write_exclusive', fail_write)
    events = []
    result = CaptureResult(b'payload', 'window', '窗口', 2, 2)
    with trace_scope(mode='automatic', images_planned=1,
                     on_status=events.append) as trace:
        outcome = retention.save_automatic(
            result, {'program_dir': str(tmp_path), 'config_stem': 'config'},
            'event', RetentionPolicy(True, 7))
        trace.summary()
    assert outcome.path is None
    save = [event for event in events if event.stage == 'save']
    assert [event.status for event in save] == ['started', 'failed']
    assert save[1].reason_code == 'save_failed'
    assert save[1].duration_ms is not None
    assert save[1].operation_id == save[0].operation_id


def test_unconfigured_save_is_skipped_without_start(tmp_path):
    """未配置保存只记固定跳过，不伪造开始或耗时。"""
    retention = import_module('modules.screenshot.retention')
    events = []
    result = CaptureResult(b'payload', 'window', '窗口', 2, 2)
    with trace_scope(mode='automatic', images_planned=1,
                     on_status=events.append) as trace:
        outcome = retention.save_automatic(
            result, {'program_dir': str(tmp_path), 'config_stem': 'config'},
            'event', RetentionPolicy(False, 7))
        trace.summary()
    assert outcome.path is None
    save = [event for event in events if event.stage == 'save']
    assert [event.status for event in save] == ['skipped']
    assert save[0].reason_code == 'not_configured'
    assert save[0].duration_ms is None
    assert not [event for event in save if event.status == 'started']


def test_window_capture_separates_capture_and_encode(monkeypatch):
    """窗口读取记为一次捕获，实际编码另记一次。"""
    backend = import_module('modules.screenshot.window')
    window_support = import_module('test_screenshot_window')
    window_support.WindowEnvironment(backend, monkeypatch)
    events = []
    with trace_scope(mode='automatic', images_planned=1,
                     on_status=events.append) as trace:
        with trace_item(image_index=1):
            result = backend.capture_window('测试窗口')
        trace.finish_image(1, 'succeeded')
        summary = trace.summary()
    assert result.image_format == 'jpeg'
    assert summary['capture_started'] == summary['capture_succeeded'] == 1
    assert summary['encode_started'] == summary['encode_succeeded'] == 1
    capture = next(event for event in events
                   if event.stage == 'capture' and event.status == 'succeeded')
    assert (capture.source, capture.width, capture.height) == ('window', 2, 2)


def test_pipeline_save_failure_still_uploads_and_is_recorded(
        monkeypatch, tmp_path):
    """编排中保存失败单独记录且不阻断实际上传。"""
    pipeline = import_module('modules.screenshot.pipeline')
    retention = import_module('modules.screenshot.retention')
    logger_module = import_module('modules.logger')
    monkeypatch.setattr(retention.winfs, 'list_names', lambda path: ())
    monkeypatch.setattr(
        retention.winfs, 'write_exclusive',
        Mock(side_effect=OSError('save-failure-marker')))
    captured = CaptureResult(b'png-bytes', 'window', '窗口', 2, 3)
    monkeypatch.setattr(pipeline, 'capture', Mock(return_value=captured))
    upload = Mock(return_value=UploadResult(
        True, 'catbox', 'https://example.invalid/result', ('catbox',), ()))
    monkeypatch.setattr(pipeline, 'upload_with_fallback', upload)
    handler = logger_module._create_file_handler(str(tmp_path / 'logs'), 'app')
    assert handler is not None
    target = logging.getLogger('modules.operation_trace')
    saved_handlers = target.handlers[:]
    saved_level = target.level
    saved_propagate = target.propagate
    target.handlers = [handler]
    target.setLevel(logging.DEBUG)
    target.propagate = False
    try:
        batch = pipeline.prepare_screenshots(
            {'targets': [{'provider': 'window', 'target': '窗口'}],
             'image_host': [{'provider': 'catbox'}],
             'retention': {'enabled': True}},
            True, (),
            runtime={'program_dir': str(tmp_path), 'config_stem': 'config'})
    finally:
        target.handlers = saved_handlers
        target.setLevel(saved_level)
        target.propagate = saved_propagate
    path = handler.baseFilename
    handler.close()
    with open(path, 'rb') as stream:
        text = stream.read().decode('utf-8')
    assert upload.call_count == 1
    assert batch.values['screenshot_1'].startswith('![')
    assert 'stage=save' in text
    assert 'status=failed' in text and 'reason_code=save_failed' in text


def test_stage_log_file_has_no_private_capture_values(monkeypatch, tmp_path):
    """真实采集日志文件不含目标原文与注入标记。"""
    backend = adb_backend()
    logger_module = import_module('modules.logger')
    handler = logger_module._create_file_handler(str(tmp_path / 'logs'), 'app')
    assert handler is not None
    marker = 'PRIVATE_SERIAL_9931'
    serial = marker + ':5555'
    read = Mock(side_effect=[b'bad-one', b'bad-two', make_png()])
    monkeypatch.setattr(backend, '_read_capture', read)
    target = logging.getLogger('modules.operation_trace')
    saved_handlers = target.handlers[:]
    saved_level = target.level
    saved_propagate = target.propagate
    target.handlers = [handler]
    target.setLevel(logging.DEBUG)
    target.propagate = False
    try:
        with trace_scope(mode='automatic', images_planned=1) as trace:
            with trace_item(image_index=1):
                backend.capture_adb(serial, purpose='automatic')
            trace.finish_image(1, 'succeeded')
            trace.summary()
    finally:
        target.handlers = saved_handlers
        target.setLevel(saved_level)
        target.propagate = saved_propagate
    path = handler.baseFilename
    handler.close()
    with open(path, 'rb') as stream:
        text = stream.read().decode('utf-8')
    assert 'stage=capture' in text and 'stage=batch' in text
    assert marker not in text and serial not in text


def spy_trace_scope(monkeypatch, module, events, traces):
    """包装编排入口作用域以读取真实汇总，不改变生产接线。"""
    real = module.trace_scope

    @contextmanager
    def scope(**kwargs):
        """注入状态回调并保留可供断言的真实批次对象。"""
        inner = kwargs.get('on_status')

        def combined(event):
            """先记录事件，再交给生产消费者，两条出口互不影响。"""
            events.append(event)
            if inner is not None:
                inner(event)

        kwargs['on_status'] = combined
        with real(**kwargs) as trace:
            traces.append(trace)
            yield trace

    monkeypatch.setattr(module, 'trace_scope', scope)


def test_cli_capture_failure_batch_is_failed(monkeypatch, tmp_path, caplog):
    """CLI 截图失败时批次终态为失败，不写出完成的假事实。"""
    cli = import_module('modules.screenshot.cli')
    monkeypatch.setattr(
        cli, 'capture',
        Mock(side_effect=CaptureError('backend_failed', 'secret')))
    args = Namespace(source='window', target='窗口',
                     output=str(tmp_path / 'shot.png'), hosts=None)
    with caplog.at_level(logging.DEBUG, logger='modules.operation_trace'):
        code = cli.run_screenshot_cli(args, str(tmp_path))
    assert code == 1
    batches = [record.getMessage() for record in caplog.records
               if 'stage=batch' in record.getMessage()
               and ' summary ' in record.getMessage()]
    assert len(batches) == 1
    assert 'status=failed' in batches[0]
    assert 'status=completed' not in batches[0]


@pytest.mark.parametrize('signal', [KeyboardInterrupt('stop'), SystemExit(9)])
def test_cli_capture_control_signal_cancels_image_and_skips_slots(
        signal, monkeypatch, tmp_path):
    """CLI 捕获控制信号原样传播，并补齐图片取消与未开始槽位跳过。"""
    cli = import_module('modules.screenshot.cli')
    monkeypatch.setattr(cli, 'capture', Mock(side_effect=signal))
    events, traces = [], []
    spy_trace_scope(monkeypatch, cli, events, traces)
    args = Namespace(
        source='window', target='窗口', output=None,
        hosts=[{'provider': 'catbox'}, {'provider': 'wmimg'}])
    with pytest.raises(type(signal)) as caught:
        cli.run_screenshot_cli(args, str(tmp_path))
    assert caught.value is signal
    summary = traces[0].summary()
    assert summary['status'] == 'cancelled'
    assert summary['images_processed'] == 1
    assert traces[0]._images == {1: 'cancelled'}
    assert summary['slots_planned'] == 2
    assert (summary['slots_succeeded'], summary['slots_failed'],
            summary['slots_cancelled'], summary['slots_skipped']) == (0, 0, 0, 2)
    skipped = [(event.status, event.reason_code) for event in events
               if event.stage == 'slot']
    assert skipped == [
        ('skipped', 'batch_cancelled'), ('skipped', 'batch_cancelled')]


def test_pipeline_capture_failure_batch_is_failed(monkeypatch, tmp_path):
    """自动截图失败时批次终态为失败且实际处理量与目标数一致。"""
    pipeline = import_module('modules.screenshot.pipeline')
    monkeypatch.setattr(
        pipeline, 'capture',
        Mock(side_effect=CaptureError('backend_failed', 'secret')))
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)
    batch = pipeline.prepare_screenshots(
        {'targets': [{'provider': 'window', 'target': '窗口'}],
         'image_host': [{'provider': 'catbox'}]},
        True, (),
        runtime={'program_dir': str(tmp_path), 'config_stem': 'config'})
    assert batch.values['screenshot_1'].startswith('截图失败')
    summary = traces[0].summary()
    assert summary['status'] == 'failed'
    assert summary['images_processed'] == 1
    assert next(event for event in events
                if event.stage == 'batch'
                and event.status != 'started').status == 'failed'


def test_pipeline_success_counts_processed_images(monkeypatch, tmp_path):
    """自动截图成功时实际处理量等于目标数且批次完成。"""
    pipeline = import_module('modules.screenshot.pipeline')
    captured = CaptureResult(b'png-bytes', 'window', '窗口', 2, 3)
    monkeypatch.setattr(pipeline, 'capture', Mock(return_value=captured))
    monkeypatch.setattr(pipeline, 'upload_with_fallback', Mock(
        return_value=UploadResult(
            True, 'catbox', 'https://example.invalid/result', ('catbox',), ())))
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)
    pipeline.prepare_screenshots(
        {'targets': [{'provider': 'window', 'target': '窗口'},
                     {'provider': 'adb', 'target': '设备'}],
         'image_host': [{'provider': 'catbox'}]},
        True, (),
        runtime={'program_dir': str(tmp_path), 'config_stem': 'config'})
    summary = traces[0].summary()
    assert summary['images_processed'] == 2
    assert summary['status'] == 'completed'
    assert next(event for event in events
                if event.stage == 'batch'
                and event.status != 'started').status == 'completed'


def test_pipeline_capture_failure_skips_slots_with_real_reason(
        monkeypatch, tmp_path):
    """捕获失败导致未上传时，跳过原因取真实原因并补齐保存跳过。"""
    pipeline = import_module('modules.screenshot.pipeline')
    monkeypatch.setattr(pipeline, 'capture', Mock(
        side_effect=CaptureError('window_not_found', 'secret')))
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)
    pipeline.prepare_screenshots(
        {'targets': [{'provider': 'window', 'target': '窗口'}],
         'image_host': [{'provider': 'catbox', 'token': 't'},
                        {'provider': 'wmimg', 'token': 't'}]},
        True, (),
        runtime={'program_dir': str(tmp_path), 'config_stem': 'config'})
    summary = traces[0].summary()
    assert summary['slots_planned'] == 2
    assert summary['slots_skipped'] == 2
    assert summary['slots_planned'] == sum(
        summary['slots_' + state]
        for state in ('succeeded', 'failed', 'cancelled', 'skipped'))
    skipped = [event.reason_code for event in events
               if event.stage == 'slot' and event.status == 'skipped']
    assert skipped == ['capture_failed', 'capture_failed']
    assert 'first_success' not in skipped
    save = [(event.status, event.reason_code) for event in events
            if event.stage == 'save']
    assert save == [('skipped', 'capture_failed')]


def test_pipeline_capture_failure_save_skip_carries_image_index(
        monkeypatch, tmp_path):
    """捕获失败时的保存跳过带当前图片序号，与同类跳过保持同一归因口径。"""
    pipeline = import_module('modules.screenshot.pipeline')
    monkeypatch.setattr(pipeline, 'capture', Mock(
        side_effect=CaptureError('window_not_found', 'secret')))
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)
    pipeline.prepare_screenshots(
        {'targets': [{'provider': 'window', 'target': '窗口'}],
         'image_host': [{'provider': 'catbox', 'token': 't'}]},
        True, (),
        runtime={'program_dir': str(tmp_path), 'config_stem': 'config'})
    summary = traces[0].summary()
    save = [(event.status, event.reason_code, event.image_index)
            for event in events if event.stage == 'save']
    assert save == [('skipped', 'capture_failed', 1)]
    assert summary['save_started'] == 0
    assert summary['save_skipped'] == 1


def test_pipeline_first_success_skips_remaining_hosts(monkeypatch, tmp_path):
    """首站成功后自动模式停止剩余图床，未开始槽位跳过原因仍为首成功。"""
    pipeline = import_module('modules.screenshot.pipeline')
    registry = import_module('modules.image_host.registry')
    monkeypatch.setattr(pipeline, 'capture', Mock(
        return_value=CaptureResult(b'png-bytes', 'window', '窗口', 2, 3)))
    monkeypatch.setitem(
        registry.UPLOADERS, 'catbox',
        lambda image, filename, token, options: 'https://example.invalid/result')
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)
    batch = pipeline.prepare_screenshots(
        {'targets': [{'provider': 'window', 'target': '窗口'}],
         'image_host': [{'provider': 'catbox', 'token': 't'},
                        {'provider': 'wmimg', 'token': 't'}]},
        True, (),
        runtime={'program_dir': str(tmp_path), 'config_stem': 'config'})
    summary = traces[0].summary()
    assert batch.values['screenshot_1'].startswith('![')
    assert summary['slots_succeeded'] == 1
    assert summary['slots_skipped'] == 1
    skipped = [event.reason_code for event in events
               if event.stage == 'slot' and event.status == 'skipped']
    assert skipped == ['first_success']


def test_pipeline_cancellation_skips_remaining_slots_as_batch_cancelled(
        monkeypatch, tmp_path):
    """控制信号中断后续图片时，未开始槽位跳过原因仍为批次取消。"""
    pipeline = import_module('modules.screenshot.pipeline')
    registry = import_module('modules.image_host.registry')
    signal = KeyboardInterrupt('stop')
    monkeypatch.setitem(
        registry.UPLOADERS, 'catbox',
        lambda image, filename, token, options: 'https://example.invalid/result')
    monkeypatch.setattr(pipeline, 'capture', Mock(side_effect=[
        CaptureResult(b'png-bytes', 'window', '窗口', 2, 3), signal]))
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)
    section = {
        'targets': [{'provider': 'window', 'target': '窗口'},
                    {'provider': 'adb', 'target': '设备'}],
        'image_host': [{'provider': 'catbox', 'token': 't'}],
    }
    with pytest.raises(KeyboardInterrupt) as caught:
        pipeline.prepare_screenshots(
            section, True, (),
            runtime={'program_dir': str(tmp_path), 'config_stem': 'config'})
    assert caught.value is signal
    summary = traces[0].summary()
    assert summary['slots_planned'] == 2
    assert (summary['slots_succeeded'], summary['slots_skipped']) == (1, 1)
    skipped = [event.reason_code for event in events
               if event.stage == 'slot' and event.status == 'skipped']
    assert skipped == ['batch_cancelled']


def test_pipeline_partial_capture_failure_is_partial(monkeypatch, tmp_path):
    """部分截图失败时批次为部分失败而非完成。"""
    pipeline = import_module('modules.screenshot.pipeline')
    monkeypatch.setattr(pipeline, 'capture', Mock(side_effect=[
        CaptureError('window_not_found', 'secret'),
        CaptureResult(b'png-bytes', 'adb', '设备', 2, 3)]))
    monkeypatch.setattr(pipeline, 'upload_with_fallback', Mock(
        return_value=UploadResult(
            True, 'catbox', 'https://example.invalid/result', ('catbox',), ())))
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)
    pipeline.prepare_screenshots(
        {'targets': [{'provider': 'window', 'target': '窗口'},
                     {'provider': 'adb', 'target': '设备'}],
         'image_host': [{'provider': 'catbox'}]},
        True, (),
        runtime={'program_dir': str(tmp_path), 'config_stem': 'config'})
    summary = traces[0].summary()
    assert summary['images_processed'] == 2
    assert summary['status'] == 'partial_failure'
