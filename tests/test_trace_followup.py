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
