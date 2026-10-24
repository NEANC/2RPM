from io import StringIO
import logging

import pytest

from modules import operation_trace as tracing
from modules.upload_status import make_status_consumer


def test_batch_started_and_unique_final_summary():
    output = StringIO()
    handler = logging.StreamHandler(output)
    logger = tracing.LOGGER
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    events = []
    try:
        with tracing.trace_scope(mode='automatic', images_planned=2,
                                 slots_planned=4, on_status=events.append) as trace:
            trace.finish_image(1, 'succeeded')
            trace.finish_image(2, 'succeeded')
        trace._finalize()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
    batches = [event for event in events if event.stage == 'batch']
    assert [event.status for event in batches] == ['started', 'completed']
    assert batches[0].batch_id == batches[1].batch_id
    assert batches[0].duration_ms is None
    lines = output.getvalue().splitlines()
    assert len(lines) == 2
    assert '计划图片 2' in lines[0] and '计划槽位 4' in lines[0]
    assert sum('生成成功' in line for line in lines) == 1
    stream = StringIO()
    consumer = make_status_consumer(stream=stream)
    for event in batches:
        consumer(event)
    assert stream.getvalue() == ''


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('phase', ['started', 'completed'])
def test_batch_callback_cancel_restores_nested_context(signal_type, phase):
    signal = signal_type('stop')
    events = []

    def consume(event):
        events.append(event)
        if event.stage == 'batch' and event.status == phase:
            raise signal

    with tracing.trace_scope(mode='automatic') as outer:
        with tracing.trace_item(image_index=7, slot_index=2):
            with tracing.trace_request('catbox', 'upload_attempt'):
                request = tracing.current_request_id()
            with pytest.raises(signal_type) as caught:
                with tracing.trace_scope(mode='cli', on_status=consume):
                    pass
            assert caught.value is signal
            assert tracing._TRACE.get() is outer
            assert tracing._ITEM.get() == (7, 2)
            assert tracing.current_request_id() == request
    assert tracing._TRACE.get() is None
    assert tracing._ITEM.get() is None
    assert tracing.current_request_id() is None
    assert [event.status for event in events] == [
        'started', 'cancelled' if phase == 'started' else 'completed']
