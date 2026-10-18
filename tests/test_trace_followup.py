from importlib import import_module
from unittest.mock import Mock
import logging

import pytest

from modules.screenshot.models import CaptureResult
from test_screenshot_stage_logging import spy_trace_scope


@pytest.mark.parametrize('hosts', [None, [], 'invalid', {}])
def test_automatic_missing_hosts_is_failed_without_slots(monkeypatch, tmp_path, hosts):
    pipeline = import_module('modules.screenshot.pipeline')
    monkeypatch.setattr(pipeline, 'capture', Mock(return_value=CaptureResult(
        b'image', 'window', 'synthetic', 2, 2)))
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)
    pipeline.prepare_screenshots(
        {'targets': [{'provider': 'window', 'target': 'synthetic'}],
         'image_host': hosts}, True, (),
        runtime={'program_dir': str(tmp_path), 'config_stem': 'synthetic'})
    summary = traces[0].summary()
    assert summary['status'] == 'failed'
    assert summary['slots_planned'] == 0
    assert not [event for event in events if event.stage == 'slot']


@pytest.mark.parametrize('warning_source', ['capture', 'config', 'expiration'])
def test_fixed_warning_marks_completed_batch(monkeypatch, tmp_path, caplog, warning_source):
    pipeline = import_module('modules.screenshot.pipeline')
    registry = import_module('modules.image_host.registry')
    monkeypatch.setattr(pipeline, 'capture', Mock(return_value=CaptureResult(
        b'image', 'window', 'synthetic', 2, 2,
        ('图像为纯色或低方差，可能未正确渲染或未更新',)
        if warning_source == 'capture' else ())))
    monkeypatch.setitem(registry.UPLOADERS, 'catbox', lambda *args: 'https://example.invalid/a')
    section = {'targets': [{'provider': 'window', 'target': 'synthetic'}],
               'image_host': [{'provider': 'catbox'}]}
    if warning_source == 'config':
        section['retention'] = 'invalid'
    if warning_source == 'expiration':
        section['image_host'][0]['expiration'] = '1d'
    with caplog.at_level(logging.DEBUG, logger='modules.operation_trace'):
        pipeline.prepare_screenshots(section, True, (), runtime={
            'program_dir': str(tmp_path), 'config_stem': 'synthetic'})
    records = [record for record in caplog.records
               if 'stage=batch' in record.getMessage() and ' summary ' in record.getMessage()]
    assert len(records) == 1
    assert 'status=completed' in records[0].getMessage()
    assert records[0].levelno == logging.WARNING


@pytest.mark.parametrize('action', ['save', 'invalidate'])
def test_cache_none_identity_records_skip(tmp_path, action):
    from modules.image_host.storage_cache import StorageCache
    from modules.operation_trace import trace_scope

    events = []
    cache = StorageCache(tmp_path)
    with trace_scope(mode='cli', on_status=events.append):
        getattr(cache, action)(None, *([None] if action == 'save' else []))
    actual = [event for event in events if event.stage == 'metadata_cache']
    assert [(event.action, event.status, event.reason_code) for event in actual] == [
        (action, 'skipped', 'unavailable')]


@pytest.mark.parametrize('error, reason', [(PermissionError, 'io_failed'), (ValueError, 'invalid')])
def test_cache_load_failure_classification(monkeypatch, tmp_path, error, reason):
    from modules.image_host.storage_cache import StorageCache
    from modules.operation_trace import trace_scope

    cache = StorageCache(tmp_path)
    identity = cache.identity('beeimg_cn', 'synthetic')
    monkeypatch.setattr('builtins.open', Mock(side_effect=error('synthetic')))
    events = []
    with trace_scope(mode='cli', on_status=events.append):
        assert cache.load(identity) is None
    failed = [event for event in events if event.stage == 'metadata_cache' and event.status == 'failed']
    assert len(failed) == 1
    assert failed[0].reason_code == reason


@pytest.mark.parametrize('failed', [False, True])
def test_boltp_retention_reuse_has_query_source(monkeypatch, tmp_path, failed):
    from test_upload_stage_logging import group_reply, run_chain, SUCCESS_UPLOAD

    run = run_chain(monkeypatch, tmp_path,
                    [{'provider': 'boltp', 'token': 'synthetic', 'expiration': '1d'}],
                    images=(1, 2), replies=([group_reply(status=500)] if failed else
                                           [group_reply(), SUCCESS_UPLOAD, SUCCESS_UPLOAD]))
    reused = [event for event in run.events if event.cache_layer == 'event']
    query = next(event for event in run.events if event.stage == 'metadata_query')
    assert len(reused) == 1
    assert reused[0].source_operation_id == query.operation_id
    assert reused[0].reason_code == ('reused_failure' if failed else 'reused_metadata')
    assert len(run.transport.gets('group')) == 1


def test_beeimg_no_same_slot_second_metadata_reuse(monkeypatch, tmp_path):
    from test_upload_stage_logging import group_reply, profile_reply, run_chain, SUCCESS_UPLOAD

    run = run_chain(monkeypatch, tmp_path,
                    [{'provider': 'beeimg_cn', 'token': 'synthetic'}], images=(1, 2),
                    replies=[group_reply(), profile_reply(), SUCCESS_UPLOAD, SUCCESS_UPLOAD])
    reused = [event for event in run.events if event.cache_layer == 'event']
    assert len(reused) == 1
    assert reused[0].image_index == 2


@pytest.mark.parametrize('source', ['window', 'png'])
def test_capture_failure_has_fixed_reason_and_encode_skip(monkeypatch, source):
    from modules.operation_trace import trace_scope
    from modules.screenshot.models import CaptureError

    events = []
    if source == 'window':
        backend = import_module('modules.screenshot.window')
        support = import_module('test_screenshot_window')
        support.WindowEnvironment(backend, monkeypatch)
        monkeypatch.setattr(backend._user32, 'PrintWindow', Mock(return_value=0))
        invoke = lambda: backend.capture_window('测试窗口')
        reason = 'print_failed'
    else:
        backend = import_module('modules.screenshot.adb')
        monkeypatch.setattr(backend, '_read_capture', Mock(return_value=b'invalid'))
        invoke = lambda: backend.capture_adb('synthetic', purpose='cli', image_format='png')
        reason = 'adb_image_failed'
    with trace_scope(mode='cli', on_status=events.append) as trace:
        with pytest.raises(CaptureError):
            invoke()
    failed = [event for event in events if event.stage == 'capture' and event.status == 'failed']
    assert len(failed) == 1
    assert failed[0].reason_code == reason
    assert trace.summary()['encode_started'] == 0
    assert trace.summary()['encode_skipped'] == 1
    assert trace.summary()['capture_retries'] == 0


def test_fallback_ui_is_silent_and_file_keeps_events(caplog):
    """备用域名回退不再产生逐站 UI 行，但文件仍记录两条真实 fallback 事件。"""
    from io import StringIO
    from modules.operation_trace import trace_scope, trace_fallback
    from modules.upload_status import make_status_consumer

    stream = StringIO()
    consume = make_status_consumer(stream=stream)
    events = []

    def duplicated(event):
        events.append(event)
        consume(event)
        consume(event)

    with caplog.at_level(logging.DEBUG, logger='modules.operation_trace'):
        with trace_scope(mode='cli', on_status=duplicated):
            with trace_fallback('host_failover', 'failover', reason_code='slot_failed'):
                pass
    assert stream.getvalue() == ''
    assert sum('stage=fallback' in record.getMessage() for record in caplog.records) == 2


def test_cli_permission_without_status_callback(monkeypatch, tmp_path, capsys, caplog):
    from argparse import Namespace
    from io import StringIO
    from modules.image_host.core import ImageHostError
    from modules.upload_status import make_status_consumer
    from test_upload_stage_logging import Transport, group_reply, profile_reply

    cli = import_module('modules.screenshot.cli')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    # 关闭状态回调：真实消费者把显示写入丢弃流，终端不出现上传反馈
    monkeypatch.setattr(
        cli, 'make_status_consumer',
        lambda **kwargs: make_status_consumer(stream=StringIO()))
    monkeypatch.setattr(cli, 'capture', Mock(return_value=CaptureResult(
        b'image', 'window', 'synthetic', 2, 2)))
    transport = Transport(monkeypatch, [group_reply(), profile_reply(status=403)])
    args = Namespace(source='window', target='synthetic', output=None,
                     hosts=[{'provider': 'beeimg_cn', 'token': 'synthetic'}])
    with caplog.at_level(logging.DEBUG):
        assert cli.run_screenshot_cli(args, str(tmp_path)) == 1
    assert ImageHostError('profile_forbidden', '').message in capsys.readouterr().out
    assert 'reason_code=profile_forbidden' in caplog.text
    assert not transport.posts


@pytest.mark.parametrize('saved', [True, False])
def test_cli_save_only_uses_actual_save_and_warning(monkeypatch, tmp_path, caplog, saved):
    from argparse import Namespace
    from modules.screenshot.retention import SaveOutcome

    cli = import_module('modules.screenshot.cli')
    monkeypatch.setattr(cli, 'capture', Mock(return_value=CaptureResult(
        b'image', 'window', 'synthetic', 2, 2, ('固定捕获告警',))))
    monkeypatch.setattr(cli, 'save_cli', Mock(return_value=SaveOutcome(
        str(tmp_path / 'image.jpg') if saved else None, 'image.jpg')))
    args = Namespace(source='window', target='synthetic', hosts=None,
                     output=str(tmp_path / 'image.jpg'))
    with caplog.at_level(logging.DEBUG, logger='modules.operation_trace'):
        assert cli.run_screenshot_cli(args, str(tmp_path)) == (0 if saved else 1)
    final = next(record for record in caplog.records
                 if 'stage=batch' in record.getMessage() and ' summary ' in record.getMessage())
    assert ('status=completed' if saved else 'status=failed') in final.getMessage()
    assert final.levelno == (logging.WARNING if saved else logging.ERROR)
    assert 'slots_planned=0' in final.getMessage()


def test_shortened_expiration_batch_warning_in_file(monkeypatch, tmp_path):
    from test_upload_stage_logging import group_reply, run_chain, SUCCESS_UPLOAD

    path = tmp_path / 'stage.log'
    logger = logging.getLogger('modules.operation_trace')
    previous = logger.level
    handler = logging.FileHandler(path, encoding='utf-8')
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        run = run_chain(monkeypatch, tmp_path,
                        [{'provider': 'boltp', 'token': 'synthetic', 'expiration': '1d'}],
                        replies=[group_reply(expire=60), SUCCESS_UPLOAD])
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        handler.close()
    text = path.read_text(encoding='utf-8')
    assert run.results[0].success
    assert run.results[0].warnings == ('保存期限已按图床上限缩短',)
    assert 'status=completed reason_code=recoverable_warning' in text
    assert text.count(' summary ') == 1
    assert 'synthetic' not in text
