from importlib import import_module
from unittest.mock import Mock

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
