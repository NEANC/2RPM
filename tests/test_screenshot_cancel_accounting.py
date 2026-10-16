from importlib import import_module
from unittest.mock import Mock

import pytest

from modules.screenshot.models import CaptureResult
from test_screenshot_stage_logging import spy_trace_scope


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('completed', [0, 1])
def test_automatic_capture_cancel_closes_images_save_and_slots(
        monkeypatch, tmp_path, signal_type, completed):
    pipeline = import_module('modules.screenshot.pipeline')
    registry = import_module('modules.image_host.registry')
    signal = signal_type('stop')
    captured = CaptureResult(b'image', 'window', 'synthetic', 2, 3)
    capture = Mock(side_effect=[captured] * completed + [signal])
    monkeypatch.setattr(pipeline, 'capture', capture)
    monkeypatch.setitem(registry.UPLOADERS, 'catbox', Mock(
        return_value='https://example.invalid/result'))
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)
    with pytest.raises(signal_type) as caught:
        pipeline.prepare_screenshots(
            {'targets': [{'provider': 'window', 'target': 'synthetic'}] * 2,
             'image_host': [{'provider': 'catbox', 'token': 'synthetic'},
                            {'provider': 'wmimg', 'token': 'synthetic'}]},
            True, (),
            runtime={'program_dir': str(tmp_path), 'config_stem': 'config'})
    assert caught.value is signal
    trace = traces[0]
    assert trace._images == ({1: 'cancelled', 2: 'skipped'} if not completed
                             else {1: 'succeeded', 2: 'cancelled'})
    summary = trace.summary()
    assert summary['status'] == 'cancelled'
    assert summary['images_processed'] == completed + 1
    assert summary['slots_planned'] == sum(
        summary['slots_' + state]
        for state in ('succeeded', 'failed', 'cancelled', 'skipped')) == 4
    saves = [(event.image_index, event.reason_code) for event in events
             if event.stage == 'save' and event.status == 'skipped']
    cancelled_saves = [(index, reason) for index, reason in saves
                       if reason == 'batch_cancelled']
    assert cancelled_saves == [(index, 'batch_cancelled')
                               for index in range(completed + 1, 3)]
    assert capture.call_count == completed + 1
