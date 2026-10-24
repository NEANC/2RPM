from argparse import Namespace
from importlib import import_module
import netrc
import socket
from unittest.mock import Mock

import pytest
import requests

from modules.screenshot.models import CaptureResult
from modules.screenshot.retention import SaveOutcome
from test_screenshot_stage_logging import spy_trace_scope


@pytest.fixture(autouse=True)
def forbidden_external_io(monkeypatch):
    """阻断真实网络与 netrc，取消传播失效时立即失败而非触网。"""

    def forbidden(*args, **kwargs):
        """任何未替身化的外部访问都直接令测试失败。"""
        pytest.fail('取消核算测试禁止真实网络或 netrc 访问')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', forbidden)
    monkeypatch.setattr(requests.sessions, 'get_netrc_auth', forbidden)
    monkeypatch.setattr(requests.utils, 'get_netrc_auth', forbidden)
    monkeypatch.setattr(netrc, 'netrc', forbidden)


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


@pytest.mark.parametrize('entry', ['automatic', 'cli'])
def test_upload_cancel_keeps_accounting(monkeypatch, tmp_path, entry):
    """上传控制异常后计划槽位仍等于四类终态之和，未开始项记为跳过非失败。"""
    signal = KeyboardInterrupt('stop')
    registry = import_module('modules.image_host.registry')
    module = import_module('modules.screenshot.pipeline') if entry == 'automatic' \
        else import_module('modules.screenshot.cli')

    def interrupted(image, filename, token, options):
        """在上传边界触发控制信号。"""
        raise signal

    monkeypatch.setitem(registry.UPLOADERS, 'catbox', interrupted)
    monkeypatch.setattr(module, 'capture', Mock(return_value=CaptureResult(
        b'image', 'window', 'synthetic', 2, 3)))
    hosts = [{'provider': 'catbox', 'token': 'synthetic'},
             {'provider': 'wmimg', 'token': 'synthetic'}]
    events, traces = [], []
    spy_trace_scope(monkeypatch, module, events, traces)
    with pytest.raises(KeyboardInterrupt) as caught:
        if entry == 'automatic':
            monkeypatch.setattr(module, 'save_automatic', Mock(
                return_value=SaveOutcome(None, 'shot.jpg')))
            module.prepare_screenshots(
                {'targets': [{'provider': 'window', 'target': 'synthetic'}],
                 'image_host': hosts}, True, (),
                runtime={'program_dir': str(tmp_path), 'config_stem': 'synthetic'})
        else:
            module.run_screenshot_cli(
                Namespace(source='window', target='synthetic', output=None,
                          hosts=hosts), str(tmp_path))
    assert caught.value is signal
    summary = traces[0].summary()
    assert summary['slots_planned'] == 2
    assert summary['slots_cancelled'] == 1
    assert summary['slots_skipped'] == 1
    assert summary['slots_failed'] == 0
    assert summary['slots_planned'] == sum(
        summary['slots_' + state]
        for state in ('succeeded', 'failed', 'cancelled', 'skipped'))
    assert not [event for event in events
                if event.stage == 'slot'
                and event.status in ('succeeded', 'failed')]
