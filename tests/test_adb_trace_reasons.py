from unittest.mock import Mock

import pytest

from modules.operation_trace import trace_item
from modules.operation_trace import trace_scope
from test_screenshot_stage_logging import adb_backend
from test_screenshot_stage_logging import fail_first_saves
from test_screenshot_stage_logging import make_png
from test_screenshot_stage_logging import make_raw
from test_screenshot_stage_logging import SERIAL


@pytest.mark.parametrize('failure', ['encoding_failed', 'adb_raw_invalid',
                                     'adb_timeout', 'response_too_large'])
def test_adb_fallback_records_fixed_trigger(monkeypatch, failure):
    backend = adb_backend()
    if failure == 'encoding_failed':
        outcomes = [make_raw(), make_raw(), make_png()]
        fail_first_saves(monkeypatch, 2)
    elif failure == 'response_too_large':
        outcomes = [backend.ResponseTooLarge(), make_png()]
    else:
        outcomes = [backend._error(failure)] * 2 + [make_png()]
    monkeypatch.setattr(backend, '_read_capture', Mock(side_effect=outcomes))
    events = []
    with trace_scope(mode='automatic', on_status=events.append):
        with trace_item(image_index=1):
            backend.capture_adb(SERIAL)
    fallbacks = [event for event in events if event.stage == 'fallback'
                 and event.status == 'succeeded']
    assert [event.fallback_kind for event in fallbacks] == (
        ['capture_format'] if failure == 'response_too_large'
        else ['capture_retry', 'capture_format'])
    assert [event.reason_code for event in fallbacks] == [failure] * len(fallbacks)


@pytest.mark.parametrize('direct', [True, False])
def test_cli_png_records_reuse_without_encoding(monkeypatch, direct):
    backend = adb_backend()
    png = make_png()
    outcomes = [png] if direct else [backend._error('adb_timeout'), png]
    monkeypatch.setattr(backend, '_read_capture', Mock(side_effect=outcomes))
    encode = Mock(side_effect=AssertionError('must not encode'))
    monkeypatch.setattr(backend, 'encode_image', encode)
    events = []
    with trace_scope(mode='cli', on_status=events.append) as trace:
        result = backend.capture_adb(
            SERIAL, purpose='cli', image_format='png' if direct else 'jpeg')
    assert result.image_bytes is png
    encode.assert_not_called()
    assert trace.summary()['encode_started'] == 0
    assert trace.summary()['encode_skipped'] == 1
    assert [(event.status, event.reason_code, event.image_format)
            for event in events if event.stage == 'encode'] == [
                ('skipped', 'encoding_reused', 'png')]
