#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notification 公共链路接入截图的端到端测试，全部边界由本地替身实现。"""

import json
import logging
import netrc
import os
import smtplib
import socket
import sys
import threading
from collections import Counter
from copy import deepcopy
from importlib import import_module
from io import BytesIO
from unittest.mock import MagicMock, Mock, PropertyMock, call

import pytest
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import config as config_module
from modules import notification as notif
from modules.image_host.context import UploadContext
from modules.image_host.core import UploadFailure
from modules.image_host.core import UploadResult
from modules.image_host.diagnostics import safe_current_message
from modules.image_host.storage_cache import StorageCache
from modules.notification_rendering import prepare_channel
from modules.screenshot.models import CaptureError
from modules.screenshot.models import CaptureResult


URL = 'https://cdn.example.com/image.png?sig=a%2Fb%3D&token=FAKE_SIGNED_KEY'
SECRET = 'FAKE_EXCEPTION_KEY_8421'
TEMPLATE_KEYS = ('on_end', 'on_timeout', 'on_wait_timeout', 'on_external')
CHANNELS = [{'provider': 'serverchan', 'sckey': 's1'}]
THREE_CHANNELS = [
    {'provider': 'serverchan', 'sckey': 's1'},
    {'provider': 'dingtalk', 'token': 't1'},
    {'provider': 'lark', 'webhook': 'w1'},
]
WINDOW_TARGET = {'provider': 'window', 'target': '窗口'}
PUSH_BUSINESS_TEXT = '合成服务端业务原文_8421'
PUSH_FAILURE_TEXT = f'{SECRET} {URL} {PUSH_BUSINESS_TEXT}'


@pytest.fixture(autouse=True)
def isolate_notification_uploads(monkeypatch, tmp_path):
    """将全部截图通知测试限制在临时缓存与本地合成边界内。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))

    def forbidden(*args, **kwargs):
        """禁止真实网络及 netrc 访问，避免测试误读本机凭证。"""
        pytest.fail('禁止真实网络或 netrc 访问')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', forbidden)
    monkeypatch.setattr(requests.sessions, 'get_netrc_auth', forbidden)
    monkeypatch.setattr(requests.utils, 'get_netrc_auth', forbidden)
    monkeypatch.setattr(netrc, 'netrc', forbidden)


def _pipeline():
    """加载真实截图编排模块，缺失功能时直接断言失败。"""
    return import_module('modules.screenshot.pipeline')


def _response(status=200):
    """构造仅具备成败判定所需字段的伪响应对象。"""
    resp = MagicMock()
    resp.status_code = status
    resp.text = 'body'
    resp.json.return_value = {'code': 0}
    return resp


def _install_boundaries(monkeypatch, capture_side_effect=None, upload_result=None):
    """只替换截图与上传边界，保留真实编排、目标分配与通知逻辑。"""
    module = _pipeline()
    capture = Mock(side_effect=capture_side_effect or (
        lambda source, name, **kwargs: CaptureResult(
            b'png-' + name.encode(), source, name, 2, 3)))
    upload = Mock(return_value=upload_result or UploadResult(
        True, 'catbox', URL, ('catbox',), ()))
    monkeypatch.setattr(module, 'capture', capture)
    monkeypatch.setattr(module, 'upload_with_fallback', upload)
    return capture, upload


def _install_onepush(monkeypatch, notify_impl=None):
    """替换 OnePush 发送边界，并记录每次发送的标题与正文。"""
    sent = []
    notifier = MagicMock()

    def default_notify(title=None, content=None, **params):
        """返回成功响应，不发起任何网络请求；SMTP 按标准库契约返回空 dict。"""
        if 'host' in params:
            return {}
        return _response(200)

    impl = notify_impl or default_notify

    def recording_notify(title=None, content=None, **params):
        """记录正文后交由本次使用的发送实现处理。"""
        sent.append((title, content))
        return impl(title=title, content=content, **params)

    notifier.notify.side_effect = recording_notify
    getter = Mock(return_value=notifier)
    monkeypatch.setattr(notif, 'get_notifier', getter)
    return sent, notifier, getter


def _base_config(channels=CHANNELS, targets=(WINDOW_TARGET,), image_host=None,
                 retry=None):
    """构造含四类既有模板、截图目标与通道的最小推送配置。"""
    return {
        'push': {
            'screenshot': {
                'targets': deepcopy(list(targets)),
                'image_host': (image_host if image_host is not None
                               else [{'provider': 'catbox'}]),
            },
            'templates': {
                key: {
                    'enable': True,
                    'capture_screenshot': True,
                    'title': '标题 {process_name}',
                    'content': '正文 {process_name}\n\n{screenshot}',
                }
                for key in TEMPLATE_KEYS
            },
            'push_channel_settings': {'channels': channels},
            'retry': retry or {'interval': '0s', 'max_count': 1},
        },
    }


def test_prepare_batch_forwards_runtime_and_event(monkeypatch):
    """包装层把 runtime 与事件键透传给编排，且不吞掉控制信号取值。"""
    seen = {}

    def fake(section, enabled, reserved, *, runtime=None, event='event'):
        """记录入参并返回空批次。"""
        seen.update(section=section, enabled=enabled, reserved=reserved,
                    runtime=runtime, event=event)
        return notif.ScreenshotBatch({'screenshot': ''}, (), ())

    monkeypatch.setattr(notif, 'prepare_screenshots', fake)
    reserved = frozenset({'process_name'})
    batch, internal_error = notif._prepare_screenshot_batch(
        {'targets': []}, True, reserved,
        runtime={'program_dir': 'P', 'config_stem': 'c'}, event='on_end')
    assert internal_error is False
    assert seen == {
        'section': {'targets': []}, 'enabled': True, 'reserved': reserved,
        'runtime': {'program_dir': 'P', 'config_stem': 'c'}, 'event': 'on_end',
    }
    assert batch.values == {'screenshot': ''}


def test_logical_event_identity_spans_batch_template_and_retries(monkeypatch, caplog):
    from modules import operation_trace

    events = []
    original = operation_trace.BatchTrace._record_event

    def record(self, event, summary=None):
        events.append(event)
        return original(self, event, summary)

    monkeypatch.setattr(operation_trace.BatchTrace, '_record_event', record)
    _install_boundaries(monkeypatch)
    attempts = []

    def notify(**kwargs):
        attempts.append(len(attempts))
        return _response(500 if len(attempts) % 2 else 200)

    _install_onepush(monkeypatch, notify)
    caplog.set_level(logging.INFO)
    config = _base_config(retry={'interval': '0s', 'max_count': 2})
    for _ in range(2):
        assert notif.send_notification(config, 'on_end', process_name='demo')
    batches = {event.batch_id for event in events}
    identities = [{event.event_id for event in events if event.batch_id == batch}
                  for batch in batches]
    assert len(identities) == 2
    assert all(len(identity) == 1 for identity in identities)
    assert identities[0].isdisjoint(identities[1])
    for identity in identities:
        event_id = next(iter(identity))
        messages = [record.getMessage() for record in caplog.records
                    if f'event_id={event_id}' in record.getMessage()]
        assert any('通知标题:' in message for message in messages)
        assert any('通知发送失败' in message for message in messages)
        assert any('通知发送成功' in message for message in messages)


def test_send_notification_forwards_config_runtime_and_template_key(monkeypatch):
    """send_notification 使用 config['_runtime'] 与真实模板键作为事件。"""
    seen = {}

    def fake(section, enabled, reserved, *, runtime=None, event='event'):
        """记录 runtime 与事件键并返回空批次。"""
        seen['runtime'] = runtime
        seen['event'] = event
        return notif.ScreenshotBatch({'screenshot': ''}, (), ())

    monkeypatch.setattr(notif, 'prepare_screenshots', fake)
    _install_onepush(monkeypatch)
    config = _base_config()
    config['_runtime'] = {'program_dir': 'P', 'config_stem': 'stem'}
    notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert seen == {
        'runtime': {'program_dir': 'P', 'config_stem': 'stem'},
        'event': 'on_end',
    }


def test_runtime_context_never_reaches_rendered_content(monkeypatch):
    """运行上下文不进入模板渲染结果或通知正文。"""
    sent, _, _ = _install_onepush(monkeypatch)
    _install_boundaries(monkeypatch)
    config = _base_config()
    config['_runtime'] = {
        'program_dir': 'FAKE_RUNTIME_DIR', 'config_stem': 'FAKE_RUNTIME_STEM'}
    notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert sent
    for title, content in sent:
        assert 'FAKE_RUNTIME' not in title
        assert 'FAKE_RUNTIME' not in content


@pytest.mark.parametrize('template_key', TEMPLATE_KEYS)
def test_all_templates_share_single_screenshot_entry(monkeypatch, template_key):
    """四类既有模板均通过同一入口注入截图结果。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    results = notif.send_notification(
        _base_config(), template_key, process_name='demo.exe')
    assert results == [('serverchan', True)]
    assert capture.call_count == upload.call_count == 1
    assert len(sent) == 1
    assert URL in sent[0][1] and 'demo.exe' in sent[0][1]


def test_on_external_default_enable_is_preserved(monkeypatch):
    """on_external 的 enable 语义不变，关闭时不截图不发送。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, getter = _install_onepush(monkeypatch)
    config = _base_config()
    config['push']['templates']['on_external']['enable'] = False
    assert notif.send_notification(config, 'on_external', process_name='demo.exe') == []
    capture.assert_not_called()
    upload.assert_not_called()
    getter.assert_not_called()


def test_capture_screenshot_defaults_true(monkeypatch):
    """未提供 capture_screenshot 时按启用处理。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config()
    del config['push']['templates']['on_end']['capture_screenshot']
    assert notif.send_notification(config, 'on_end', process_name='demo.exe') == [
        ('serverchan', True)]
    assert capture.call_count == upload.call_count == 1
    assert URL in sent[0][1]


def test_explicit_false_is_respected(monkeypatch):
    """显式 false 不被默认值覆盖，截图变量正常填空格且仍通知。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config()
    config['push']['templates']['on_end']['capture_screenshot'] = False
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    capture.assert_not_called()
    upload.assert_not_called()
    assert sent[0][1] == '正文 demo.exe\n\n'


def test_invalid_capture_type_disables_without_upload(monkeypatch, caplog):
    """错误类型不得被当作启用，诊断安全且不发生上传。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config()
    config['push']['templates']['on_end']['capture_screenshot'] = 'false'
    with caplog.at_level(logging.WARNING, logger='modules.notification'):
        results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    capture.assert_not_called()
    upload.assert_not_called()
    assert 'capture_screenshot' in caplog.text
    assert URL not in caplog.text


def test_capture_flag_reads_template_not_kwarg(monkeypatch):
    """截图开关只取模板配置，同名 kwarg 仅作普通变量不改变是否截图。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config()
    config['push']['templates']['on_end']['content'] = (
        '开关 {capture_screenshot}\n\n{screenshot}')
    results = notif.send_notification(
        config, 'on_end', process_name='demo.exe', capture_screenshot=False)
    assert results == [('serverchan', True)]
    assert capture.call_count == upload.call_count == 1
    assert '开关 False' in sent[0][1]
    assert URL in sent[0][1]


@pytest.mark.parametrize('mode', ['disabled', 'no_channels'])
def test_disabled_notification_or_no_channels_zero_screenshot(monkeypatch, mode):
    """通知禁用或无有效通道时不调用截图编排、后端与发送。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, getter = _install_onepush(monkeypatch)
    config = _base_config()
    if mode == 'disabled':
        config['push']['templates']['on_end']['enable'] = False
    else:
        config['push']['push_channel_settings']['channels'] = None
    assert notif.send_notification(config, 'on_end', process_name='demo.exe') == []
    capture.assert_not_called()
    upload.assert_not_called()
    getter.assert_not_called()


@pytest.mark.parametrize('content', [
    '{unknown_variable}', '{', '{x:', '{process_name} {', '}x{y',
])
def test_template_errors_fail_before_screenshot(monkeypatch, caplog, content):
    """普通缺变量与畸形 format 在截图前失败，且不产生任何副作用。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, getter = _install_onepush(monkeypatch)
    config = _base_config()
    config['push']['templates']['on_end']['content'] = content
    assert notif.send_notification(config, 'on_end', process_name='demo.exe') == []
    capture.assert_not_called()
    upload.assert_not_called()
    getter.assert_not_called()
    assert SECRET not in caplog.text


@pytest.mark.parametrize('content,extra', [
    ('{process_name!z}', {}),
    ('{process_name.foo}', {}),
    ('{extra[missing]}', {'extra': {}}),
])
def test_invalid_format_structure_fails_before_screenshot(
        monkeypatch, caplog, content, extra):
    """非法转换符与失败的属性、下标访问须在截图前失败，零副作用。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, getter = _install_onepush(monkeypatch)
    config = _base_config()
    config['push']['templates']['on_end']['content'] = content
    results = notif.send_notification(
        config, 'on_end', process_name='v', **extra)
    assert results == []
    capture.assert_not_called()
    upload.assert_not_called()
    getter.assert_not_called()
    assert SECRET not in caplog.text


def test_escaped_braces_and_nested_spec_fields(monkeypatch):
    """转义花括号不是引用，嵌套 format spec 字段照常参与变量合并。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config()
    config['push']['templates']['on_end']['content'] = (
        '{{字面}} {process_name:{width}}\n\n{screenshot}')
    results = notif.send_notification(
        config, 'on_end', process_name='ab', width=4)
    assert results == [('serverchan', True)]
    assert capture.call_count == 1
    body = sent[0][1]
    assert body.startswith('{字面} ab  ')
    assert URL in body


def test_configured_custom_out_is_available(monkeypatch):
    """已配置的自定义 out 允许作为截图占位变量参与渲染。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config(targets=[{**WINDOW_TARGET, 'out': 'custom'}])
    config['push']['templates']['on_end']['content'] = '截图: {custom}'
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    assert capture.call_count == upload.call_count == 1
    assert sent[0][1] == f'截图: ![custom]({URL})'


@pytest.mark.parametrize('content,targets', [
    ('{screenshot.attr}', (WINDOW_TARGET,)),
    ('{screenshot[99]}', (WINDOW_TARGET,)),
    ('{custom.attr}', ({**WINDOW_TARGET, 'out': 'custom'},)),
])
def test_placeholder_field_access_fails_before_screenshot(
        monkeypatch, caplog, content, targets):
    """占位变量的属性或下标访问必为模板笔误，须在截图前失败且零副作用。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, getter = _install_onepush(monkeypatch)
    config = _base_config(targets=targets)
    config['push']['templates']['on_end']['content'] = content
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == []
    capture.assert_not_called()
    upload.assert_not_called()
    getter.assert_not_called()
    assert URL not in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.parametrize('content', ['{screenshot}', '{screenshot_1}'])
def test_placeholder_plain_reference_still_captures(monkeypatch, content):
    """白名单占位变量的普通引用不因字段访问拦截而回归。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config()
    config['push']['templates']['on_end']['content'] = content
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    assert capture.call_count == upload.call_count == 1
    assert URL in sent[0][1]


def test_partial_capture_failure_still_notifies(monkeypatch, caplog):
    """单目标截图失败不影响其他目标与本次通知。"""
    capture, upload = _install_boundaries(monkeypatch, capture_side_effect=[
        CaptureError('window_not_found', SECRET),
        CaptureResult(b'png-second', 'adb', '第二', 2, 3),
    ])
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config(targets=[
        WINDOW_TARGET, {'provider': 'adb', 'target': '第二'}])
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    assert capture.call_count == 2
    assert upload.call_count == 1
    body = sent[0][1]
    assert '截图失败' in body and URL in body
    assert SECRET not in body and SECRET not in caplog.text


def test_all_upload_failure_still_notifies(monkeypatch, caplog):
    """整批图床失败仍按安全提示继续通知。"""
    failure = UploadResult(False, None, None, ('catbox', SECRET), ())
    capture, upload = _install_boundaries(monkeypatch, upload_result=failure)
    sent, _, _ = _install_onepush(monkeypatch)
    results = notif.send_notification(
        _base_config(), 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    assert capture.call_count == upload.call_count == 1
    assert '截图上传失败' in sent[0][1]
    assert SECRET not in sent[0][1] and SECRET not in caplog.text


def test_injected_secret_exception_never_reaches_diagnostics(monkeypatch, caplog):
    """未知普通异常在通知边界安全降级，异常原文不进入正文或日志。"""
    capture, upload = _install_boundaries(
        monkeypatch, capture_side_effect=RuntimeError(SECRET))
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config(
        image_host=[{'provider': 'catbox', 'token': SECRET}])
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    assert '截图失败' in sent[0][1]
    assert SECRET not in sent[0][1]
    assert SECRET not in caplog.text


def test_internal_error_hint_differs_from_unconfigured(monkeypatch, caplog):
    """截图编排未知异常时给出不误导的固定提示，且不泄露异常原文。"""
    _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    monkeypatch.setattr(
        notif, 'prepare_screenshots', Mock(side_effect=RuntimeError(SECRET)))
    config = _base_config()
    config['push']['templates']['on_end']['content'] = '结果 {screenshot}'
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    body = sent[0][1]
    assert '内部错误' in body
    assert '未配置' not in body
    assert SECRET not in body and SECRET not in caplog.text


@pytest.mark.parametrize('exc_type', [RuntimeError, ValueError, KeyError])
def test_internal_degrade_logs_safe_exception_type(
        monkeypatch, caplog, exc_type):
    """内部降级日志带安全异常类型名，不泄露异常原文，提示仍为固定文案。"""
    _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    monkeypatch.setattr(
        notif, 'prepare_screenshots', Mock(side_effect=exc_type(SECRET)))
    config = _base_config()
    config['push']['templates']['on_end']['content'] = '结果 {screenshot}'
    with caplog.at_level(logging.ERROR, logger='modules.notification'):
        results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    assert exc_type.__name__ in caplog.text
    assert SECRET not in caplog.text
    assert '内部错误' in sent[0][1]
    assert SECRET not in sent[0][1]


def test_configured_empty_target_keeps_original_hint(monkeypatch):
    """已配置截图但目标为空时仍使用既有未配置提示，不退化为内部错误。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config(targets=[])
    config['push']['templates']['on_end']['content'] = '结果 {screenshot}'
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    assert '未配置有效截图目标' in sent[0][1]
    assert '内部错误' not in sent[0][1]
    capture.assert_not_called()
    upload.assert_not_called()


def test_unreferenced_screenshot_skips_orchestration_without_warning(
        monkeypatch, caplog):
    """模板未引用截图变量且无目标时不执行编排，也不产生目标缺失告警。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config(targets=[])
    config['push']['templates']['on_end']['content'] = '正文 {process_name}'
    with caplog.at_level(logging.WARNING, logger='modules.screenshot.pipeline'):
        results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    assert sent[0][1] == '正文 demo.exe'
    capture.assert_not_called()
    upload.assert_not_called()
    assert '未配置有效截图目标' not in caplog.text


def test_referenced_screenshot_keeps_missing_target_warning(monkeypatch, caplog):
    """模板引用截图变量且无目标时仍按安全失败处理并保留目标缺失告警。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config(targets=[])
    config['push']['templates']['on_end']['content'] = '结果 {screenshot}'
    with caplog.at_level(logging.WARNING, logger='modules.screenshot.pipeline'):
        results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    assert '未配置有效截图目标' in sent[0][1]
    assert '未配置有效截图目标' in caplog.text
    capture.assert_not_called()
    upload.assert_not_called()


def test_default_templates_do_not_add_missing_target_warning(monkeypatch, caplog):
    """默认配置的既有模板未引用截图变量且无目标，不产生目标缺失告警。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = config_module.get_default_config()
    config['push']['push_channel_settings']['channels'] = deepcopy(CHANNELS)
    config['push']['retry'] = {'interval': '0s', 'max_count': 1}
    assert config['push']['screenshot']['targets'] == []
    assert config['push']['templates']['on_end']['capture_screenshot'] is True
    with caplog.at_level(logging.WARNING, logger='modules.screenshot.pipeline'):
        results = notif.send_notification(
            config, 'on_end', process_name='demo.exe',
            process_pid=4242, process_run_time='1s')
    assert results == [('serverchan', True)]
    assert capture.call_count == upload.call_count == 0
    assert '未配置有效截图目标' not in caplog.text
    assert '未配置有效截图目标' not in sent[0][1]


def test_conflicting_out_is_renamed_without_appending(monkeypatch):
    """out 不得覆盖普通变量；无截图引用时不补附改名结果。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config(targets=[{**WINDOW_TARGET, 'out': 'process_name'}])
    config['push']['templates']['on_end']['content'] = '正文 {process_name}'
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    assert sent == [('标题 demo.exe', '正文 demo.exe')]
    capture.assert_not_called()
    upload.assert_not_called()


@pytest.mark.parametrize('capture', [True, False])
def test_unconfigured_screenshot_index_placeholder(monkeypatch, capture):
    """未配置的 screenshot_N 启用时给固定提示，禁用时为空。"""
    capture_mock, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config(targets=[])
    config['push']['templates']['on_end']['content'] = '{screenshot_9}'
    config['push']['templates']['on_end']['capture_screenshot'] = capture
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    capture_mock.assert_not_called()
    upload.assert_not_called()
    if capture:
        assert '未配置该截图目标' in sent[0][1]
    else:
        assert sent[0][1] == ''


@pytest.mark.parametrize('targets', [
    [None], [{'provider': 'unknown-secret', 'target': 'x'}], ['bad-secret'],
])
def test_invalid_target_config_does_not_block_notification(
        monkeypatch, caplog, targets):
    """目标配置错误只给出安全提示，不阻断普通通知。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config(targets=targets)
    config['push']['templates']['on_end']['content'] = '{screenshot}'
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    capture.assert_not_called()
    upload.assert_not_called()
    assert '配置' in sent[0][1]
    assert 'secret' not in sent[0][1].lower()
    assert 'secret' not in caplog.text.lower()


def test_inputs_are_not_mutated(monkeypatch):
    """配置、模板与调用方 kwargs 在通知后保持不变。"""
    _install_boundaries(monkeypatch)
    _install_onepush(monkeypatch)
    config = _base_config(
        targets=[{**WINDOW_TARGET, 'out': 'custom'}], retry={'interval': '0s', 'max_count': 2})
    before = deepcopy(config)
    kwargs = {'process_name': 'demo.exe'}
    notif.send_notification(config, 'on_end', **kwargs)
    assert config == before
    assert kwargs == {'process_name': 'demo.exe'}


@pytest.mark.parametrize('marker', ['@{version}', '@{VERSION}', '@{Version}'])
def test_notification_version_targets_reuse_capture_batch(monkeypatch, marker):
    capture, upload = _install_boundaries(monkeypatch)
    attempts = Counter()

    def notify(title=None, content=None, **params):
        key = params.get('sckey') or params.get('token') or params.get('webhook')
        attempts[key] += 1
        return _response(500 if attempts[key] == 1 else 200)

    sent, notifier, _ = _install_onepush(monkeypatch, notify)
    config = _base_config(
        channels=deepcopy(THREE_CHANNELS),
        targets=[
            {'provider': 'window', 'target': 'App' + marker, 'out': 'first'},
            {'provider': 'window', 'target': 'App@{vErSiOn}'},
            {'provider': 'adb', 'target': 'serial@{version}'},
            {'provider': 'window', 'target': 'Straße' + marker, 'out': 'last'},
        ],
        retry={'interval': '0s', 'max_count': 2})
    config['push']['templates']['on_end']['content'] = '{screenshot}'
    before = deepcopy(config)
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert config == before
    assert results == [(channel['provider'], True) for channel in THREE_CHANNELS]
    assert attempts == {'s1': 2, 't1': 2, 'w1': 2}
    assert notifier.notify.call_count == len(sent) == 6
    assert len(set(sent)) == 1
    parts = sent[0][1].split('\n\n')
    assert parts == [
        f'![first]({URL})', '截图配置失败：截图目标配置无效',
        f'![last]({URL})']
    assert capture.call_count == upload.call_count == 2
    contexts = []
    for capture_call, upload_call, expression, name in zip(
            capture.call_args_list, upload.call_args_list,
            ('App' + marker, 'Straße' + marker), ('first', 'last')):
        assert capture_call.args == ('window', expression)
        assert capture_call.kwargs == {
            'image_format': 'jpeg', 'purpose': 'automatic'}
        assert upload_call.args == (
            b'png-' + expression.encode(), name + '.jpg',
            before['push']['screenshot']['image_host'])
        assert set(upload_call.kwargs) == {'context'}
        contexts.append(upload_call.kwargs['context'])
    assert isinstance(contexts[0], UploadContext)
    assert contexts[0] is contexts[1]


def test_channels_and_retries_reuse_one_batch(monkeypatch):
    """三通道及各自重试复用同一批次，截图与上传次数只由目标数决定。"""
    capture, upload = _install_boundaries(monkeypatch)
    seen = set()

    def notify(title=None, content=None, **params):
        """首次调用失败，重试成功，用以验证重试复用同一正文。"""
        key = params.get('sckey') or params.get('token') or params.get('webhook')
        if key not in seen:
            seen.add(key)
            return _response(500)
        return _response(200)

    sent, notifier, _ = _install_onepush(monkeypatch, notify)
    config = _base_config(
        channels=THREE_CHANNELS,
        targets=[WINDOW_TARGET, {'provider': 'adb', 'target': '第二'}],
        retry={'interval': '0s', 'max_count': 3})
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert [provider for provider, _ in results] == [
        'serverchan', 'dingtalk', 'lark']
    assert all(success for _, success in results)
    assert notifier.notify.call_count == 6
    assert capture.call_count == 2
    assert upload.call_count == 2
    assert len({content for _, content in sent}) == 1


def test_single_image_upload_follows_host_chain(monkeypatch, caplog):
    """每张图片按图床链顺序尝试，跨通道不重复上传同一批次。"""
    module = _pipeline()
    registry = import_module('modules.image_host.registry')
    calls = []
    monkeypatch.setattr(module, 'capture', Mock(
        side_effect=lambda source, name, **kwargs: (
            CaptureResult(b'png-' + name.encode(), source, name, 2, 3))))

    def failing(image, filename, token, options):
        """首个图床失败，不返回链接。"""
        calls.append('first')
        raise RuntimeError(SECRET)

    def succeeding(image, filename, token, options):
        """第二个图床返回完整签名直链。"""
        calls.append('second')
        return URL

    monkeypatch.setitem(registry.UPLOADERS, 'first', failing)
    monkeypatch.setitem(registry.UPLOADERS, 'second', succeeding)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config(
        channels=THREE_CHANNELS,
        targets=[WINDOW_TARGET, {'provider': 'adb', 'target': '第二'}],
        image_host=[{'provider': 'first'}, {'provider': 'second'}])
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert all(success for _, success in results)
    assert calls == ['first', 'second', 'first', 'second']
    assert URL in sent[0][1]
    assert SECRET not in caplog.text


def test_repeated_reference_and_two_events(monkeypatch):
    """同事件重复引用只执行一次，两个事件分别重新执行。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config()
    config['push']['templates']['on_end']['content'] = '{screenshot}\n\n{screenshot}'
    notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert capture.call_count == upload.call_count == 1
    assert sent[0][1].count(URL) == 2
    notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert capture.call_count == upload.call_count == 2


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_control_signal_propagates_unchanged(monkeypatch, signal_type):
    """截图边界的控制信号以原对象向上传播，不被吞掉或包装。"""
    signal = signal_type(SECRET)
    capture, upload = _install_boundaries(monkeypatch, capture_side_effect=signal)
    sent, _, getter = _install_onepush(monkeypatch)
    with pytest.raises(signal_type) as caught:
        notif.send_notification(_base_config(), 'on_end', process_name='demo.exe')
    assert caught.value is signal
    upload.assert_not_called()
    getter.assert_not_called()


def test_signed_url_stays_in_content_and_out_of_logs(monkeypatch, caplog):
    """签名直链完整发送给通道，但不进入任何日志文本。"""
    _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config(
        image_host=[{'provider': 'catbox', 'token': 'FAKE_HOST_TOKEN'}])
    with caplog.at_level(logging.DEBUG):
        results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    assert URL in sent[0][1]
    assert 'FAKE_SIGNED_KEY' in sent[0][1]
    assert 'FAKE_SIGNED_KEY' not in caplog.text
    assert 'FAKE_HOST_TOKEN' not in caplog.text
    assert URL not in caplog.text


def test_screenshot_title_not_leaked_in_success_log(monkeypatch, caplog):
    """标题引用截图结果时成功日志改为安全摘要，不泄露签名直链。"""
    _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config()
    config['push']['templates']['on_end']['title'] = '标题 {screenshot}'
    config['push']['templates']['on_end']['content'] = '{screenshot}'
    with caplog.at_level(logging.DEBUG, logger='modules.notification'):
        results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    assert URL in sent[0][0] and 'FAKE_SIGNED_KEY' in sent[0][0]
    assert URL in sent[0][1]
    assert URL not in caplog.text
    assert 'FAKE_SIGNED_KEY' not in caplog.text


def test_success_log_omits_title_without_screenshot(monkeypatch, caplog):
    """未使用截图结果时成功日志只报告通道结果，不再额外输出原始标题。"""
    _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config()
    config['push']['templates']['on_end']['content'] = '正文 {process_name}'
    with caplog.at_level(logging.INFO, logger='modules.notification'):
        results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    successes = [record.getMessage().split(' ', 1)[1] for record in caplog.records
                 if record.name == 'modules.notification'
                 and ' 通知发送成功' in record.getMessage()]
    assert successes == ['通知发送成功 [serverchan]']


class NotificationRaw(BytesIO):
    """提供真实响应所需的内存流与连接释放接口。"""

    def release_conn(self):
        """响应消费完成后释放合成连接对应的内存流。"""
        self.close()


@pytest.fixture
def notification_http(monkeypatch):
    """仅替换截图和 HTTP 适配器，观察真实事件、查询和通知链路。"""
    state = {'requests': [], 'contexts': [], 'closed': [], 'images': [],
             'replies': [], 'responses': [], 'clock': [1700000000.0]}
    initialize = UploadContext.__init__
    close = UploadContext.close
    start_image = UploadContext.start_image

    def observed_init(self, **kwargs):
        """保留真实缓存，仅注入可控时钟并记录事件实例。"""
        assert kwargs.get('diagnostics') is False
        initialize(self, **kwargs, clock=lambda: state['clock'][0])
        state['contexts'].append(self)

    def observed_close(self):
        """退出真实事件时记录实例，不替代其状态释放。"""
        state['closed'].append(self)
        close(self)

    def observed_start(self):
        """记录每图独立句柄与期限起点，不改变注册表行为。"""
        image = start_image(self)
        state['images'].append((self, image))
        return image

    def send(adapter, request, **kwargs):
        """向真实 Requests 会话返回有界合成响应，禁止队列外调用。"""
        assert safe_current_message(SECRET) is None
        assert kwargs['stream'] is True
        assert kwargs['verify'] is True
        state['requests'].append(request)
        assert state['replies'], '发生未批准的额外 HTTP 请求'
        reply = state['replies'].pop(0)
        if callable(reply):
            reply = reply()
        if isinstance(reply, BaseException):
            raise reply
        status, payload = reply
        response = requests.Response()
        response.status_code = status
        response.url = request.url
        response.request = request
        response.raw = NotificationRaw(json.dumps(payload).encode('utf-8'))
        state['responses'].append(response)
        return response

    capture = Mock(side_effect=lambda source, name, **kwargs: CaptureResult(
        b'png-' + name.encode(), source, name, 2, 3))
    monkeypatch.setattr(_pipeline(), 'capture', capture)
    monkeypatch.setattr(UploadContext, '__init__', observed_init)
    monkeypatch.setattr(UploadContext, 'close', observed_close)
    monkeypatch.setattr(UploadContext, 'start_image', observed_start)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    state['capture'] = capture
    yield state
    assert all(response.raw.closed for response in state['responses'])
    assert state['closed'] == state['contexts']
    assert all(context._closed for context in state['contexts'])


def _group_response(ids=(13, 14), retention=None):
    """构造组元数据合成响应，不包含真实账号数据。"""
    return 200, {'status': 'success', 'data': {
        'group': {'options': {'file_expire_seconds': retention}},
        'storages': [{'id': value} for value in ids],
    }}


def _upload_response(url=URL):
    """提供包含完整签名链接的业务成功响应。"""
    return 200, {'status': 'success', 'message': SECRET, 'data': {
        'public_url': url,
    }}


def _http_config(**kwargs):
    """构造经真实 v2 注册表处理的双目标通知配置。"""
    return _base_config(
        targets=[WINDOW_TARGET, {'provider': 'adb', 'target': '第二'}],
        image_host=[{'provider': 'beeimg_cn', 'token': SECRET}], **kwargs)


@pytest.mark.parametrize('persist', [True, False])
def test_http_batch_reuses_context_queries_and_channel_retries(
        notification_http, monkeypatch, caplog, persist):
    """冷缓存双图三通道重试只查询一次，写缓存失败也能事件内复用。"""
    state = notification_http
    if not persist:
        def denied(*args, **kwargs):
            """模拟临时缓存原子写入失败而不替代查询流程。"""
            raise OSError(SECRET)

        monkeypatch.setattr(StorageCache, '_write_atomic', denied)
    state['replies'] = [
        _group_response(), _upload_response(), _upload_response(),
    ]
    seen = Counter()

    def notify(title=None, content=None, **params):
        """每通道第一次失败，第二次成功，保持真实并发重试规则。"""
        key = params.get('sckey') or params.get('token') or params.get('webhook')
        seen[key] += 1
        return _response(500 if seen[key] == 1 else 200)

    sent, notifier, _ = _install_onepush(monkeypatch, notify)
    config = _http_config(channels=THREE_CHANNELS,
                          retry={'interval': '0s', 'max_count': 3})
    before = deepcopy(config)
    with caplog.at_level(logging.DEBUG):
        result = notif.send_notification(config, 'on_end', process_name='demo')
    assert result == [('serverchan', True), ('dingtalk', True), ('lark', True)]
    assert state['capture'].call_count == 2
    assert notifier.notify.call_count == 6
    assert len(state['contexts']) == 1
    assert [request.method for request in state['requests']] == [
        'GET', 'POST', 'POST']
    assert [request.url.split('/api/v2')[1]
            for request in state['requests']] == [
                '/group', '/upload', '/upload']
    assert len(state['images']) == 2
    assert all(context is state['contexts'][0]
               for context, _ in state['images'])
    assert state['images'][0][1] is not state['images'][1][1]
    assert len({body for _, body in sent}) == 1
    assert sent[0][1].count(URL) == 2
    assert config == before
    assert SECRET not in str(sent) + caplog.text
    assert URL not in caplog.text
    if not persist:
        warning = '存储元数据缓存保存失败'
        assert sent[0][1].count(warning) == 1
        assert sent[0][1].count('截图配置提示') == 1
        assert caplog.text.count(warning) == 2
    assert not state['replies']


def test_http_next_event_uses_new_context_and_only_metadata_disk_cache(
        notification_http, monkeypatch):
    """新事件重新截图上传，允许复用有效磁盘元数据而不复用图片结果。"""
    state = notification_http
    sent, _, _ = _install_onepush(monkeypatch)
    config = _http_config()
    state['replies'] = [_group_response(),
                        _upload_response(), _upload_response()]
    assert notif.send_notification(config, 'on_end', process_name='demo')
    state['clock'][0] += 10
    next_url = URL + '&event=2'
    state['replies'] = [_upload_response(next_url), _upload_response(next_url)]
    assert notif.send_notification(config, 'on_end', process_name='demo')
    assert state['capture'].call_count == 4
    assert len(state['contexts']) == 2
    assert state['contexts'][0] is not state['contexts'][1]
    assert [request.method for request in state['requests']] == [
        'GET', 'POST', 'POST', 'POST', 'POST']
    assert [request.url.split('/api/v2')[1]
            for request in state['requests']] == [
                '/group', '/upload', '/upload', '/upload', '/upload']
    assert next_url not in sent[0][1]
    assert sent[1][1].count(next_url) == 2
    assert [image.started_at for _, image in state['images']] == [
        1700000000.0, 1700000000.0, 1700000010.0, 1700000010.0]


@pytest.mark.parametrize('success', [True, False])
def test_http_registry_warnings_reach_notification_once(
        notification_http, monkeypatch, caplog, success):
    """无效手填与期限缩短由真实注册表产生，两种结局均集中提示一次。"""
    state = notification_http
    reply = (_upload_response() if success else
             (200, {'status': 'error', 'message': SECRET}))
    state['replies'] = [
        _group_response(retention=5), reply, reply,
    ]
    config = _http_config()
    config['push']['screenshot']['image_host'][0].update(
        expiration='20s', options={'storage_id': 0})
    sent, _, _ = _install_onepush(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        assert notif.send_notification(config, 'on_end', process_name='demo')
    body = sent[0][1]
    warnings = ('手填储存驱动不可用，已自动替代', '保存期限已按图床上限缩短')
    assert body.count('截图配置提示') == 1
    assert body.index(warnings[0]) < body.index(warnings[1])
    for warning in warnings:
        assert body.count(warning) == 1
        assert caplog.text.count(warning) == 2
    assert ('截图上传失败：通道 BeeIMG.CN 图床拒绝上传。' in body) is not success
    assert (URL in body) is success
    assert len(state['requests']) == 3
    assert SECRET not in str(sent) + caplog.text
    assert URL not in caplog.text


@pytest.mark.parametrize('provider, token', [
    ('beeimg_cn', 'FAKE_OTHER_BEEIMG_CREDENTIAL'),
    ('boltp', 'FAKE_OTHER_CREDENTIAL'),
])
def test_http_fallback_isolates_identity_and_preserves_success(
        notification_http, monkeypatch, caplog, provider, token):
    """前站失败后备用成功，各身份只查询一次且历史失败不污染图片。"""
    state = notification_http
    rejected = (200, {'status': 'error', 'message': SECRET})
    state['replies'] = [
        _group_response(), rejected, _group_response(), _upload_response(),
        rejected, _upload_response(),
    ]
    config = _http_config()
    config['push']['screenshot']['image_host'].append(
        {'provider': provider, 'token': token})
    config['push']['templates']['on_end']['title'] = '标题 {screenshot}'
    before = deepcopy(config)
    sent, _, _ = _install_onepush(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        assert notif.send_notification(config, 'on_end', process_name='demo')
    assert config == before
    assert len(state['contexts']) == 1
    requests_seen = state['requests']
    assert [request.method for request in requests_seen] == [
        'GET', 'POST', 'GET', 'POST', 'POST', 'POST']
    assert requests_seen[0].headers['Authorization'] == 'Bearer ' + SECRET
    assert requests_seen[2].headers['Authorization'] == 'Bearer ' + token
    assert ('beeimg.cn' in requests_seen[2].url) is (provider == 'beeimg_cn')
    for title, body in sent:
        assert title.count(URL) == body.count(URL) == 2
        assert '截图上传失败' not in title + body
        assert SECRET not in title + body
    assert SECRET not in caplog.text and token not in caplog.text
    assert URL not in caplog.text
    assert not state['replies']


def test_http_boltp_zero_uses_group_first_per_image(
        notification_http, monkeypatch):
    """Boltp 手填 0 走自动选择：组查询在事件内复用，每图上传组首项。"""
    state = notification_http
    state['replies'] = [_group_response(ids=(13,)), _upload_response(),
                        _upload_response()]
    sent, _, _ = _install_onepush(monkeypatch)
    config = _http_config()
    config['push']['screenshot']['image_host'] = [
        {'provider': 'boltp', 'token': SECRET, 'options': {'storage_id': 0}}]
    assert notif.send_notification(config, 'on_end', process_name='demo')
    assert [request.method for request in state['requests']] == [
        'GET', 'POST', 'POST']
    for request in state['requests'][1:]:
        assert b'name="storage_id"\r\n\r\n13\r\n' in request.body
    assert len(state['contexts']) == 1
    assert sent[0][1].count(URL) == 2
    assert not state['replies']


def test_http_recovery_budget_and_deadline_are_per_image(
        notification_http, monkeypatch, caplog):
    """两张图各允许一次恢复，期限各自起算而同图重传不延长。"""
    state = notification_http
    rejected = (200, {'status': 'error', 'message': '不存在的储存驱动'})

    def next_image():
        """在第一图成功响应时推进时钟，区分后续图片的独立起点。"""
        state['clock'][0] += 10
        return _upload_response()

    state['replies'] = [
        _group_response(), rejected, _group_response(), next_image,
        rejected, _group_response(), _upload_response(),
    ]
    config = _http_config()
    config['push']['screenshot']['image_host'][0]['expiration'] = '30s'
    sent, _, _ = _install_onepush(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        assert notif.send_notification(config, 'on_end', process_name='demo')
    posts = [request for request in state['requests'] if request.method == 'POST']
    assert len(posts) == 4
    assert [request.method for request in state['requests']] == [
        'GET', 'POST', 'GET', 'POST', 'POST', 'GET', 'POST']
    assert len(state['requests']) == 7
    assert len(state['contexts']) == 1
    assert [image.started_at for _, image in state['images']] == [
        1700000000.0, 1700000010.0]
    deadlines = []
    for request in posts:
        part = request.body.split(b'name="expired_at"\r\n\r\n')[1]
        deadlines.append(part.split(b'\r\n')[0])
    assert deadlines[0] == deadlines[1]
    assert deadlines[2] == deadlines[3]
    assert deadlines[0] != deadlines[2]
    assert sent[0][1].count(URL) == 2
    assert '截图上传失败' not in sent[0][1]
    assert '不存在的储存驱动' not in str(sent) + caplog.text
    assert SECRET not in str(sent) + caplog.text
    assert URL not in caplog.text
    assert not state['replies']


@pytest.mark.parametrize('case, expected, posts', [
    ('transport', '图床网络传输失败', 2),
    ('http', '图床 HTTP 请求失败', 2),
    ('business', '图床拒绝上传', 2),
    ('response', '图床响应无效', 2),
    ('lookup', '储存驱动查询失败', 0),
    ('unavailable', '储存驱动不可用', 0),
    ('config', '图床配置无效', 0),
])
def test_http_failure_categories_visible_without_server_diagnostics(
        notification_http, monkeypatch, caplog, case, expected, posts):
    """真实 HTTP 分类进入通知固定摘要，失败查询事件内复用且不泄露原文。"""
    state = notification_http
    config = _http_config()
    config['push']['templates']['on_end']['title'] = '标题 {screenshot}'
    if case == 'lookup':
        state['replies'] = [(200, {'status': 'success', 'data': SECRET})]
    elif case == 'unavailable':
        state['replies'] = [_group_response(())]
    elif case == 'config':
        config['push']['screenshot']['image_host'][0]['expiration'] = SECRET
    else:
        replies = {
            'transport': requests.exceptions.ReadTimeout(SECRET),
            'http': (503, {'message': SECRET}),
            'business': (200, {'status': 'error', 'message': SECRET}),
            'response': (200, {'message': SECRET}),
        }
        state['replies'] = [_group_response(), replies[case], replies[case]]
    sent, _, _ = _install_onepush(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        result = notif.send_notification(config, 'on_end', process_name='demo')
    assert result == [('serverchan', True)]
    assert state['capture'].call_count == 2
    assert len(state['contexts']) == 1
    assert sum(request.method == 'POST' for request in state['requests']) == posts
    if case in ('lookup', 'unavailable'):
        assert len(state['requests']) == 1
    if case == 'config':
        assert state['requests'] == []
    summary = '截图上传失败：通道 BeeIMG.CN ' + expected + '。'
    assert sent[0][0].count(summary) == sent[0][1].count(summary) == 1
    assert SECRET not in str(sent) + caplog.text
    assert not any(record.exc_info for record in caplog.records)
    assert not state['replies']


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('stage', ['capture', 'http'])
def test_http_control_signal_closes_after_diagnostic_scope(
        notification_http, monkeypatch, signal_type, stage):
    """真实通知中控制信号原对象传播，批次关闭晚于内层诊断作用域退出。"""
    state = notification_http
    signal = signal_type(SECRET)
    state['replies'] = [_group_response(), _upload_response()]
    if stage == 'capture':
        state['capture'].side_effect = [
            CaptureResult(b'png-first', 'window', '窗口', 2, 3), signal]
    else:
        state['replies'].append(signal)
    close = UploadContext.close
    observed = []

    def closing(self):
        """关闭时应已恢复调用方外层作用域，不再持有通知内层诊断状态。"""
        assert safe_current_message(SECRET) == '[已隐藏]'
        observed.append(self)
        close(self)

    monkeypatch.setattr(UploadContext, 'close', closing)
    sent, _, getter = _install_onepush(monkeypatch)
    diagnostics = import_module('modules.image_host.diagnostics')
    with diagnostics.diagnostic_scope((SECRET,)):
        with pytest.raises(signal_type) as caught:
            notif.send_notification(_http_config(), 'on_end', process_name='demo')
        assert caught.value is signal
    assert len(observed) == 1
    assert observed == state['contexts']
    assert safe_current_message(SECRET) is None
    assert sent == []
    getter.assert_not_called()


@pytest.mark.parametrize('mode', [
    'disabled', 'capture_disabled', 'no_channels', 'bad_template',
    'empty', 'invalid',
])
def test_notification_early_paths_have_no_context_or_cache(
        notification_http, monkeypatch, tmp_path, mode):
    """真实通知提前返回或无有效目标时，不触发上下文、凭证及缓存查询。"""
    state = notification_http
    config = _http_config()
    template = config['push']['templates']['on_end']
    if mode == 'disabled':
        template['enable'] = False
    elif mode == 'capture_disabled':
        template['capture_screenshot'] = False
    elif mode == 'no_channels':
        config['push']['push_channel_settings']['channels'] = []
    elif mode == 'bad_template':
        template['content'] = '{unknown_variable}'
    else:
        config['push']['screenshot']['targets'] = [] if mode == 'empty' else [None]

    def forbidden(*args, **kwargs):
        """所有上传前置副作用都必须保持未调用。"""
        pytest.fail('提前返回路径触发上传副作用')

    context_module = import_module('modules.image_host.context')
    monkeypatch.setattr(context_module, 'resolve_token', forbidden)
    monkeypatch.setattr(StorageCache, 'identity', forbidden)
    monkeypatch.setattr(StorageCache, 'load', forbidden)
    monkeypatch.setattr(StorageCache, 'save', forbidden)
    _install_onepush(monkeypatch)
    notif.send_notification(config, 'on_end', process_name='demo')
    assert state['contexts'] == state['requests'] == []
    state['capture'].assert_not_called()
    assert not (tmp_path / 'local').exists()


def test_notification_keeps_backup_credentials_lazy(notification_http, monkeypatch):
    """首站成功时不提前解析备用项凭证，双图复用首站凭证快照。"""
    state = notification_http
    state['replies'] = [_group_response(),
                        _upload_response(), _upload_response()]
    config = _http_config()
    config['push']['screenshot']['image_host'].append({
        'provider': 'beeimg_cn', 'token': '${UNUSED_SYNTHETIC_BACKUP}'})
    module = import_module('modules.image_host.context')
    resolve = module.resolve_token
    values = []

    def observed(value):
        """记录实际凭证解析并拒绝任何未尝试项的访问。"""
        assert value == SECRET
        values.append(value)
        return resolve(value)

    monkeypatch.setattr(module, 'resolve_token', observed)
    snapshots = []

    def notify(**kwargs):
        snapshots.append(import_module('modules.log_safety').secret_snapshot())
        return _response()

    _install_onepush(monkeypatch, notify)
    assert notif.send_notification(config, 'on_end', process_name='demo')
    assert snapshots and all(SECRET in snapshot for snapshot in snapshots)
    assert values == [SECRET]
    assert len(state['requests']) == 3


@pytest.mark.parametrize('ordinary', [False, True])
def test_notification_untrusted_failure_fields_and_unknown_exception(
        monkeypatch, caplog, ordinary):
    """真实通知入口仅展示重建摘要，伪造原文和普通异常都不可进入日志。"""
    failure = UploadResult(False, None, None, (SECRET,), (
        UploadFailure(SECRET, 'http_failed', SECRET, diagnostic=SECRET),))
    capture, upload = _install_boundaries(monkeypatch, upload_result=failure)
    if ordinary:
        upload.side_effect = RuntimeError(SECRET)
    config = _base_config()
    config['push']['templates']['on_end']['title'] = '标题 {screenshot}'
    sent, _, _ = _install_onepush(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        assert notif.send_notification(config, 'on_end', process_name='demo')
    expected = ('截图上传失败：通道 未知图床 图床上传失败。' if ordinary
                else '截图上传失败：通道 未知图床 图床 HTTP 请求失败。')
    assert sent == [('标题 ' + expected, '正文 demo\n\n' + expected)]
    assert SECRET not in str(sent) + caplog.text
    assert capture.call_count == upload.call_count == 1


def _push_response(status=200, body=None):
    """构造仅在内存中解析的真实响应，所有业务原文均为合成数据。"""
    response = requests.Response()
    response.status_code = status
    response.encoding = 'utf-8'
    response._content = json.dumps(body, ensure_ascii=False).encode('utf-8')
    return response


def _assert_push_logs_safe(caplog):
    """检查项目日志的消息、原始参数及异常附件均不携带合成秘密。"""
    records = [record for record in caplog.records
               if record.name.startswith('modules.')]
    assert records
    for record in records:
        assert record.exc_info is None
        assert not record.exc_text
        text = record.getMessage() + str(record.msg) + repr(record.args)
        for secret in (SECRET, URL, 'FAKE_SIGNED_KEY', PUSH_BUSINESS_TEXT):
            assert secret not in text


@pytest.mark.parametrize('case, category', [
    ('exception', '客户端发送失败'),
    ('http', 'HTTP 请求失败（HTTP 503）'),
    ('business', '业务拒绝'),
])
def test_screenshot_send_failure_redacts_original_diagnostics(
        monkeypatch, caplog, case, category):
    """独立复现异常、HTTP 正文和业务消息泄露，要求固定失败摘要。"""
    _, notifier, _ = _install_onepush(monkeypatch)
    if case == 'exception':
        notifier.notify.side_effect = RuntimeError(PUSH_FAILURE_TEXT)
    else:
        notifier.notify.side_effect = None
        notifier.notify.return_value = _push_response(
            503 if case == 'http' else 200,
            {'code': 1, 'message': PUSH_FAILURE_TEXT})
    channel = {'provider': 'serverchan', 'sckey': SECRET}
    before = deepcopy(channel)
    with caplog.at_level(logging.DEBUG):
        result = notif._notify_single_channel(
            channel, PUSH_FAILURE_TEXT, PUSH_FAILURE_TEXT, 0, 1,
            screenshot_count=1)
    assert result is False
    assert channel == before
    assert notifier.notify.call_count == 1
    _assert_push_logs_safe(caplog)
    assert category in caplog.text


@pytest.mark.parametrize('body', [
    {'errcode': 1, 'errmsg': PUSH_FAILURE_TEXT},
    {'errcode': PUSH_FAILURE_TEXT, 'errmsg': PUSH_FAILURE_TEXT},
    {'code': 1, 'message': PUSH_FAILURE_TEXT},
    {'code': 1, 'reason': PUSH_FAILURE_TEXT},
    {'code': 1, 'info': PUSH_FAILURE_TEXT},
    {'code': PUSH_FAILURE_TEXT, 'message': PUSH_FAILURE_TEXT},
    {'success': False, 'message': PUSH_FAILURE_TEXT},
    {'success': False, 'reason': PUSH_FAILURE_TEXT},
    {'success': False, 'info': PUSH_FAILURE_TEXT},
])
def test_screenshot_business_rejection_has_fixed_summary(
        monkeypatch, caplog, body):
    """所有旧业务拒绝分支只记录固定类别，不输出消息或动态业务码。"""
    _, notifier, _ = _install_onepush(monkeypatch)
    notifier.notify.side_effect = None
    notifier.notify.return_value = _push_response(body=body)
    assert notif._notify_single_channel(
        CHANNELS[0], '标题', URL, 0, 1, screenshot_count=1) is False
    _assert_push_logs_safe(caplog)
    failures = [record.getMessage() for record in caplog.records
                if record.levelno == logging.ERROR]
    assert failures == [
        '通道 [serverchan] 通知发送失败 (尝试 1/1): 业务拒绝']


@pytest.mark.parametrize('stage', ['get_notifier', 'notify'])
def test_screenshot_client_failure_never_formats_exception(
        monkeypatch, caplog, stage):
    """截图路径不得为失败日志调用异常的 str 或 repr。"""
    formatted = []

    class SensitiveError(RuntimeError):
        """记录任何异常格式化尝试。"""

        def __str__(self):
            """记录字符串转换并提供合成敏感原文。"""
            formatted.append('str')
            return PUSH_FAILURE_TEXT

        def __repr__(self):
            """记录表示转换并提供合成敏感原文。"""
            formatted.append('repr')
            return PUSH_FAILURE_TEXT

    _, notifier, getter = _install_onepush(monkeypatch)
    boundary = getter if stage == 'get_notifier' else notifier.notify
    boundary.side_effect = SensitiveError()
    assert notif._notify_single_channel(
        CHANNELS[0], '标题', URL, 0, 1, screenshot_count=1) is False
    assert formatted == []
    _assert_push_logs_safe(caplog)
    assert '客户端发送失败' in caplog.text


def test_screenshot_http_failure_does_not_read_text(monkeypatch, caplog):
    """非 2xx 安全判定不读取响应正文，正文属性异常不能影响重试结果。"""
    reads = []

    class GuardedResponse(requests.Response):
        """禁止读取正文的合成 HTTP 失败响应。"""

        @property
        def text(self):
            """任何正文访问都记录并抛出含合成秘密的普通异常。"""
            reads.append('text')
            raise RuntimeError(PUSH_FAILURE_TEXT)

    response = GuardedResponse()
    response.status_code = 503
    _, notifier, _ = _install_onepush(monkeypatch)
    notifier.notify.side_effect = None
    notifier.notify.return_value = response
    assert notif._notify_single_channel(
        CHANNELS[0], '标题', URL, 0, 1, screenshot_count=1) is False
    assert reads == []
    _assert_push_logs_safe(caplog)
    assert 'HTTP 请求失败（HTTP 503）' in caplog.text


@pytest.mark.parametrize('status', [None, '503', SECRET, True, 503.0, 99, 600])
def test_screenshot_invalid_http_status_fails_safely(
        monkeypatch, caplog, status):
    """只接受范围内的原生整数状态码，非法状态安全失败且不动态回显。"""
    _, notifier, _ = _install_onepush(monkeypatch)
    notifier.notify.side_effect = None
    notifier.notify.return_value = _push_response(status, {'code': 0})
    assert notif._notify_single_channel(
        CHANNELS[0], '标题', URL, 0, 1, screenshot_count=1) is False
    _assert_push_logs_safe(caplog)
    assert '响应判定失败' in caplog.text


@pytest.mark.parametrize('status', [100, 199, 300, 503, 599])
def test_screenshot_valid_http_failure_status_is_visible(
        monkeypatch, caplog, status):
    """有效非 2xx 状态码保留诊断价值，但响应正文不可进入日志。"""
    _, notifier, _ = _install_onepush(monkeypatch)
    notifier.notify.side_effect = None
    notifier.notify.return_value = _push_response(status, PUSH_FAILURE_TEXT)
    assert notif._notify_single_channel(
        CHANNELS[0], '标题', URL, 0, 1, screenshot_count=1) is False
    _assert_push_logs_safe(caplog)
    assert f'HTTP 请求失败（HTTP {status}）' in caplog.text


@pytest.mark.parametrize('case', ['none', 'unknown', 'status_error', 'json_error'])
def test_screenshot_unknown_response_fails_safely(monkeypatch, caplog, case):
    """未知对象和响应判定普通异常必须在通道内安全失败，不能上抛泄密。"""
    response = _response()
    if case == 'none':
        response = None
    elif case == 'unknown':
        response = object()
    elif case == 'status_error':
        type(response).status_code = PropertyMock(
            side_effect=RuntimeError(PUSH_FAILURE_TEXT))
    else:
        response.json.side_effect = RuntimeError(PUSH_FAILURE_TEXT)
    _, notifier, _ = _install_onepush(monkeypatch)
    notifier.notify.side_effect = None
    notifier.notify.return_value = response
    assert notif._notify_single_channel(
        CHANNELS[0], '标题', URL, 0, 1, screenshot_count=1) is False
    _assert_push_logs_safe(caplog)
    assert ('未收到响应' if case == 'none' else '响应判定失败') in caplog.text


@pytest.mark.parametrize('status, body', [
    (200, {'errcode': 0}), (200, {'code': 0}), (200, {'code': 200}),
    (200, {'success': True}), (200, {'success': 0}),
    (204, None), (299, []),
])
def test_screenshot_success_retains_legacy_response_semantics(
        monkeypatch, caplog, status, body):
    """合法 2xx 及原业务成功规则保持不变，成功日志继续省略标题。"""
    response = _push_response(status, body)
    assert notif._is_push_successful(response) == (True, '')
    _, notifier, _ = _install_onepush(monkeypatch)
    notifier.notify.side_effect = None
    notifier.notify.return_value = response
    with caplog.at_level(logging.INFO):
        assert notif._notify_single_channel(
            CHANNELS[0], URL, URL, 0, 1, screenshot_count=1) is True
    _assert_push_logs_safe(caplog)
    assert '通知发送成功 [serverchan]（含截图结果，截图项 1）' in caplog.text


def test_screenshot_non_json_2xx_retains_success(monkeypatch):
    """合法 2xx 的非 JSON 响应仍按旧规则成功，不混同判定内部错误。"""
    response = _push_response()
    response._content = b'not-json'
    assert notif._is_push_successful(response) == (True, '')
    _, notifier, _ = _install_onepush(monkeypatch)
    notifier.notify.side_effect = None
    notifier.notify.return_value = response
    assert notif._notify_single_channel(
        CHANNELS[0], '标题', URL, 0, 1, screenshot_count=1) is True


@pytest.mark.parametrize('case, expected', [
    ('exception', '客户端发送失败'),
    ('http', 'HTTP 请求失败（HTTP 503）'),
    ('errcode', '业务拒绝'),
    ('code', '业务拒绝'),
    ('success', '业务拒绝'),
])
def test_non_screenshot_failure_uses_safe_reason_and_log(
        monkeypatch, caplog, case, expected):
    """纯文本通知同样仅记录固定失败摘要。"""
    bodies = {
        'errcode': {'errcode': 1, 'errmsg': PUSH_FAILURE_TEXT},
        'code': {'code': 1, 'message': PUSH_FAILURE_TEXT},
        'success': {'success': False, 'reason': PUSH_FAILURE_TEXT},
    }
    _, notifier, _ = _install_onepush(monkeypatch)
    if case == 'exception':
        notifier.notify.side_effect = RuntimeError(PUSH_FAILURE_TEXT)
    else:
        response = _push_response(
            503 if case == 'http' else 200,
            bodies.get(case, PUSH_FAILURE_TEXT))
        assert notif._is_push_successful(response) == (False, expected)
        notifier.notify.side_effect = None
        notifier.notify.return_value = response
    assert notif._notify_single_channel(
        CHANNELS[0], '普通标题', '普通正文', 0, 1) is False
    assert [record.getMessage() for record in caplog.records] == [
        '通道 [serverchan] 通知发送失败 (尝试 1/1): ' + expected,
        '通道 [serverchan] 通知发送失败，已超过最大重试次数',
    ]


@pytest.mark.parametrize('recover', [True, False])
@pytest.mark.parametrize('case', ['exception', 'http', 'business', 'response'])
def test_http_screenshot_notification_failures_reuse_batch_safely(
        notification_http, monkeypatch, caplog, recover, case):
    """真实编排与 HTTP 上传后发送失败，重试复用批次且不泄露业务原文。"""
    state = notification_http
    state['replies'] = [_group_response(),
                        _upload_response(), _upload_response()]
    attempts = Counter()

    def notify(title=None, content=None, **params):
        """仅替换 OnePush 边界，各通道首次失败后恢复或持续失败。"""
        key = params.get('sckey') or params.get('token') or params.get('webhook')
        attempts[key] += 1
        assert URL in title and URL in content
        if recover and attempts[key] == 2:
            return _push_response(body={'code': 0})
        if case == 'exception':
            raise RuntimeError(PUSH_FAILURE_TEXT)
        if case == 'response':
            response = _response()
            type(response).status_code = PropertyMock(
                side_effect=RuntimeError(PUSH_FAILURE_TEXT))
            return response
        return _push_response(503 if case == 'http' else 200,
                              {'code': 1, 'message': PUSH_FAILURE_TEXT})

    sent, notifier, _ = _install_onepush(monkeypatch, notify)
    sleep = Mock()
    monkeypatch.setattr(notif.time, 'sleep', sleep)
    config = _http_config(channels=deepcopy(THREE_CHANNELS),
                          retry={'interval': '2s', 'max_count': 3})
    config['push']['templates']['on_end']['title'] = '标题 {screenshot}'
    before = deepcopy(config)
    with caplog.at_level(logging.DEBUG):
        results = notif.send_notification(config, 'on_end', process_name='demo')
    assert results == [(item['provider'], recover) for item in THREE_CHANNELS]
    count = 2 if recover else 3
    assert dict(attempts) == {'s1': count, 't1': count, 'w1': count}
    assert notifier.notify.call_count == 3 * count
    assert sleep.call_count == 3 * (count - 1)
    assert all(call.args == (2,) for call in sleep.call_args_list)
    assert state['capture'].call_count == 2
    assert [request.method for request in state['requests']] == [
        'GET', 'POST', 'POST']
    assert [request.url.split('/api/v2')[1]
            for request in state['requests']] == [
                '/group', '/upload', '/upload']
    assert len(state['contexts']) == 1
    assert not state['replies']
    assert len(set(sent)) == 1
    assert all(title.count(URL) == content.count(URL) == 2
               for title, content in sent)
    assert config == before
    _assert_push_logs_safe(caplog)
    category = {
        'exception': '客户端发送失败', 'http': 'HTTP 请求失败（HTTP 503）',
        'business': '业务拒绝', 'response': '响应判定失败',
    }[case]
    assert category in caplog.text
    assert ('已超过最大重试次数' in caplog.text) is not recover


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('stage', ['get_notifier', 'notify', 'status', 'json'])
def test_screenshot_send_control_signals_propagate_identically(
        monkeypatch, caplog, signal_type, stage):
    """真实通知线程中客户端和响应判定的控制信号均原对象传播。"""
    capture, upload = _install_boundaries(monkeypatch)
    _, notifier, getter = _install_onepush(monkeypatch)
    signal = signal_type(PUSH_FAILURE_TEXT)
    if stage in ('get_notifier', 'notify'):
        boundary = getter if stage == 'get_notifier' else notifier.notify
        boundary.side_effect = signal
    else:
        response = _response()
        if stage == 'status':
            type(response).status_code = PropertyMock(side_effect=signal)
        else:
            response.json.side_effect = signal
        notifier.notify.side_effect = None
        notifier.notify.return_value = response
    sleep = Mock()
    monkeypatch.setattr(notif.time, 'sleep', sleep)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(signal_type) as caught:
            notif.send_notification(_base_config(), 'on_end', process_name='demo')
    assert caught.value is signal
    assert capture.call_count == upload.call_count == 1
    assert getter.call_count == 1
    assert notifier.notify.call_count == (stage != 'get_notifier')
    sleep.assert_not_called()
    _assert_push_logs_safe(caplog)


SAFE_SIGNED_URL = (
    'https://cdn.example.com/screenshot.png?sig=abcdef&size=large')


@pytest.mark.parametrize('use_screenshot', [False, True])
def test_complete_template_is_logged_once_without_mutation(
        monkeypatch, caplog, use_screenshot):
    """模板只在渲染后记录一次脱敏标题正文，真实发送值与各输入对象不变。"""
    capture, upload = _install_boundaries(
        monkeypatch, upload_result=UploadResult(
            True, 'catbox', SAFE_SIGNED_URL, ('catbox',), ()))
    attempts = Counter()

    def notify(title=None, content=None, **params):
        """各通道首次失败再次成功，覆盖并发重试不重复生成模板日志。"""
        key = params.get('sckey') or params.get('token') or params.get('webhook')
        attempts[key] += 1
        return _response(500 if attempts[key] == 1 else 200)

    sent, _, _ = _install_onepush(monkeypatch, notify)
    config = _base_config(
        channels=deepcopy(THREE_CHANNELS),
        targets=([{'provider': 'window', 'target': '窗口', 'out': 'screenshot'}]
                 if use_screenshot else []),
        retry={'interval': '0s', 'max_count': 2})
    template = config['push']['templates']['on_end']
    if use_screenshot:
        template['title'] = '标题 {process_name:>{width}} {screenshot_1!r}'
        template['content'] = (
            '正文 {process_name} {process_name!a}\n\n'
            '{screenshot_1}\n\n{screenshot}\n\n'
            '{screenshot_1}{screenshot_1}' + '长正文内容' * 40)
        markdown = f'![screenshot_1]({SAFE_SIGNED_URL})'
        original_title = f'标题 demo.exe {markdown!r}'
        original_content = (
            f"正文 demo.exe 'demo.exe'\n\n{markdown}\n\n{markdown}\n\n"
            f'{markdown}{markdown}' + '长正文内容' * 40
            + '\n\n截图配置提示\n'
            '截图目标 1：输出名“screenshot”为保留名，已改为“screenshot_1”。')
    else:
        template['title'] = '纯文本标题 {process_name:>{width}}'
        template['content'] = (
            '纯文本正文 {process_name} {process_name!a}' + '长正文内容' * 40)
        original_title = '纯文本标题 demo.exe'
        original_content = (
            "纯文本正文 demo.exe 'demo.exe'" + '长正文内容' * 40)
    before = deepcopy(config)
    captured = []
    batch_before = []
    original_log = notif._log_rendered_notification

    def spy_log(template_key, title, content, batch):
        """记录日志入参并在发送前冻结批次副本。"""
        captured.append((template_key, title, content, batch))
        batch_before.append(deepcopy(batch))
        return original_log(template_key, title, content, batch)

    monkeypatch.setattr(notif, '_log_rendered_notification', spy_log)
    with caplog.at_level(logging.INFO, logger='modules.notification'):
        results = notif.send_notification(
            config, 'on_end', process_name='demo.exe', width=8)
    assert results == [(channel['provider'], True) for channel in THREE_CHANNELS]
    assert attempts == {'s1': 2, 't1': 2, 'w1': 2}
    assert len(sent) == 6 and len(set(sent)) == 1
    assert sent[0] == (original_title, original_content)
    assert config == before
    assert len(captured) == len(batch_before) == 1
    _, captured_title, captured_content, batch = captured[0]
    assert captured_title == original_title
    assert captured_content == original_content
    assert batch == batch_before[0]
    expected_calls = 1 if use_screenshot else 0
    assert capture.call_count == upload.call_count == expected_calls
    logs = [record.getMessage().split(' ', 1)[1] for record in caplog.records
            if record.name == 'modules.notification'
            and ' 通知标题:' in record.getMessage()]
    assert len(logs) == 1
    if use_screenshot:
        assert '![screenshot_1]([链接已隐藏])' in logs[0]
        assert SAFE_SIGNED_URL not in logs[0]
        assert 'cdn.example.com' not in logs[0]
        assert 'sig=abcdef' not in logs[0]
    else:
        assert logs[0] == (f'通知标题: {original_title}\r\n'
                           f'通知内容: {original_content}')


def test_parallel_notifications_keep_secret_scopes_separate(monkeypatch):
    """并发事件的发送线程各自持有本事件秘密，互不串号且退出后释放。"""
    log_safety = import_module('modules.log_safety')
    _install_boundaries(monkeypatch)
    _install_onepush(monkeypatch)
    first = 'FAKE_PARALLEL_SECRET_ALPHA_5501'
    second = 'FAKE_PARALLEL_SECRET_BETA_5502'
    observed = {}
    barrier = threading.Barrier(2, timeout=20)
    real = notif._notify_single_channel

    def observed_notify(channel, title, content, retry_interval, max_count,
                        screenshot_count=0, *, render_context=None):
        """在发送线程内读取本事件秘密快照，并强制两事件同时处于作用域。"""
        barrier.wait()
        observed.setdefault(channel['sckey'], []).append(
            log_safety.secret_snapshot())
        return real(channel, title, content, retry_interval, max_count,
                    screenshot_count, render_context=render_context)

    monkeypatch.setattr(notif, '_notify_single_channel', observed_notify)
    results = {}
    failures = []

    def run(key, sckey):
        """线程内发送一次纯文本通知，异常统一收集避免线程静默失败。"""
        try:
            config = _base_config(
                channels=[{'provider': 'serverchan', 'sckey': sckey}],
                targets=[])
            template = config['push']['templates'][key]
            template['title'] = '标题 {process_name}'
            template['content'] = '正文 {process_name}'
            results[key] = notif.send_notification(
                config, key, process_name='demo')
        except BaseException as exc:
            failures.append(exc)

    workers = [threading.Thread(target=run, args=('on_end', first)),
               threading.Thread(target=run, args=('on_timeout', second))]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)
    assert not failures
    assert results == {'on_end': [('serverchan', True)],
                       'on_timeout': [('serverchan', True)]}
    assert observed == {first: [(first,)], second: [(second,)]}
    assert log_safety.secret_snapshot() == ()


def test_disabled_diagnostics_keeps_lazy_backup_token(notification_http, monkeypatch):
    """首站成功时不求值备用环境引用，仅注册真正解析的凭证且退出后释放。"""
    state = notification_http
    state['replies'] = [_group_response(),
                        _upload_response(), _upload_response()]
    config = _http_config()
    config['push']['screenshot']['image_host'].append({
        'provider': 'catbox', 'token': '${UNUSED_SYNTHETIC_BACKUP}'})
    context_module = import_module('modules.image_host.context')
    resolve = context_module.resolve_token
    resolved = []
    backup_environment_reads = 0

    def observed(value):
        """统计实际请求解析的凭证项，不读取或展开未尝试的环境引用。"""
        nonlocal backup_environment_reads
        if isinstance(value, str) and value.startswith('${'):
            backup_environment_reads += 1
        resolved.append(value)
        return resolve(value)

    monkeypatch.setattr(context_module, 'resolve_token', observed)
    _install_onepush(monkeypatch)
    assert notif.send_notification(config, 'on_end', process_name='demo')
    assert resolved == [SECRET]
    assert backup_environment_reads == 0
    assert len(state['requests']) == 3
    assert import_module('modules.log_safety').secret_snapshot() == ()


def test_channel_retries_display_upload_status_once(
        notification_http, monkeypatch, capsys):
    """同一截图批次的终端状态不随通知通道数量与重试重复输出。"""
    state = notification_http
    state['replies'] = [_group_response(),
                        _upload_response(), _upload_response()]
    seen = Counter()

    def notify(title=None, content=None, **params):
        """各通道首次失败后重试成功，保持既有并发重试规则。"""
        key = params.get('sckey') or params.get('token') or params.get('webhook')
        seen[key] += 1
        return _response(500 if seen[key] == 1 else 200)

    sent, notifier, _ = _install_onepush(monkeypatch, notify)
    config = _http_config(channels=THREE_CHANNELS,
                          retry={'interval': '0s', 'max_count': 3})
    results = notif.send_notification(config, 'on_end', process_name='demo')
    assert results == [('serverchan', True), ('dingtalk', True), ('lark', True)]
    assert notifier.notify.call_count == 6
    assert len({body for _, body in sent}) == 1
    status_text = capsys.readouterr().out
    assert status_text.count('✔️  通道 [BeeIMG.CN] 上传成功') == 2
    assert status_text.count('正在上传截图') == 2
    assert '准备上传' not in status_text
    assert '\x1b' not in status_text
    assert URL not in status_text


def _smtp_channel(**overrides):
    """构造最小 SMTP 通道，仅用于本次通知接入测试。"""
    channel = {
        'provider': 'smtp',
        'subject': '标题',
        'From': 'sender@example.test',
        'To': 'recipient@example.test',
        'user': 'user@example.test',
        'host': 'smtp.example.test',
        'password': 'secret',
    }
    channel.update(overrides)
    return channel


def test_channel_preparation_is_once_per_channel(monkeypatch):
    """每个通道只准备一次，重试复用同一份 MIME 与渲染结果。"""
    capture, upload = _install_boundaries(monkeypatch)
    attempts = Counter()
    sent = {'discord': [], 'smtp': []}

    def notify(title=None, content=None, **params):
        """记录每次发送内容，SMTP 首次异常后返回空 dict，discord 首次 503 后成功。"""
        provider = 'smtp' if 'host' in params else 'discord'
        sent[provider].append((title, content, params))
        attempts[provider] += 1
        if provider == 'smtp':
            if attempts[provider] == 1:
                raise smtplib.SMTPRecipientsRefused(
                    {'recipient@example.test': (550, b'rejected')})
            return {}
        return _response(503 if attempts[provider] == 1 else 200)

    _install_onepush(monkeypatch, notify)
    config = _base_config(
        channels=[{'provider': 'discord'}, _smtp_channel()],
        retry={'interval': '2s', 'max_count': 3})
    before_config = deepcopy(config)
    previews = []
    original_log = notif._log_rendered_notification

    def spy_log(template_key, title, content, batch):
        """记录基准日志入参，并在发送前冻结批次内容副本。"""
        previews.append((deepcopy(batch.values), batch))
        return original_log(template_key, title, content, batch)

    monkeypatch.setattr(notif, '_log_rendered_notification', spy_log)
    prepare_spy = Mock(wraps=prepare_channel)
    monkeypatch.setattr(notif, 'prepare_channel', prepare_spy)
    sleep = Mock()
    monkeypatch.setattr(notif.time, 'sleep', sleep)

    results = notif.send_notification(config, 'on_end', process_name='demo')

    assert results == [('discord', True), ('smtp', True)]
    assert capture.call_count == upload.call_count == 1
    assert prepare_spy.call_count == 2
    assert attempts == Counter({'discord': 2, 'smtp': 2})
    assert sent['smtp'][0][2]['msg'] is sent['smtp'][1][2]['msg']
    assert sent['discord'][0][:2] == sent['discord'][1][:2]
    assert sleep.call_args_list == [call(2), call(2)]
    assert config == before_config
    assert len(previews) == 1
    before_values, batch = previews[0]
    assert batch.values == before_values


@pytest.mark.parametrize('mode', ('exception', 'invalid_header'))
def test_preparation_failure_is_local(monkeypatch, caplog, mode):
    """单通道准备失败只返回 False，零发送、零等待且不泄露异常原文。"""
    _install_boundaries(monkeypatch)
    smtp = _smtp_channel()
    if mode == 'invalid_header':
        smtp['subject'] = 'bad\r' + SECRET
    _, notifier, getter = _install_onepush(monkeypatch)
    if mode == 'exception':
        def failing_prepare(channel, title, content, context):
            """仅让 SMTP 通道抛出携带合成秘密的准备异常。"""
            if channel.get('provider') == 'smtp':
                raise ValueError(SECRET)
            return prepare_channel(channel, title, content, context)

        monkeypatch.setattr(notif, 'prepare_channel', failing_prepare)
    sleep = Mock()
    monkeypatch.setattr(notif.time, 'sleep', sleep)
    config = _base_config(
        channels=[smtp, {'provider': 'discord'}],
        retry={'interval': '2s', 'max_count': 3})

    with caplog.at_level(logging.DEBUG):
        results = notif.send_notification(config, 'on_end', process_name='demo')

    assert results == [('smtp', False), ('discord', True)]
    assert notifier.notify.call_count == 1
    assert getter.call_count == 1
    sleep.assert_not_called()
    assert '通道消息准备失败' in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.parametrize('signal_type', (KeyboardInterrupt, SystemExit))
def test_preparation_control_signal_propagates(monkeypatch, signal_type):
    """准备阶段控制信号保持原对象，由 Future.result 向上传播。"""
    _install_boundaries(monkeypatch)
    signal = signal_type('prepare-stop')

    def interrupting_prepare(channel, title, content, context):
        """在通道准备阶段抛出同一控制信号对象。"""
        raise signal

    monkeypatch.setattr(notif, 'prepare_channel', interrupting_prepare)
    _, notifier, getter = _install_onepush(monkeypatch)
    sleep = Mock()
    monkeypatch.setattr(notif.time, 'sleep', sleep)

    with pytest.raises(signal_type) as caught:
        notif.send_notification(_base_config(), 'on_end', process_name='demo')

    assert caught.value is signal
    assert notifier.notify.call_count == 0
    assert getter.call_count == 0
    sleep.assert_not_called()


LEGACY_CONFLICT_CASES = (
    ({'provider': 'dingtalk', 'markdown': False,
      'title': 'dup', 'content': 'dup'}, False),
    ({'provider': 'wechatworkapp', 'media_id': 'id',
      'title': 'dup', 'content': 'dup'}, True),
    ({'provider': 'smtp', 'msg': object(),
      'title': 'dup', 'content': 'dup'}, True),
    ({'provider': 'bark', 'ciphertext': 'sealed',
      'title': 'dup', 'content': 'dup'}, True),
)


@pytest.mark.parametrize('channel, reference', LEGACY_CONFLICT_CASES)
def test_legacy_conflicts_remain(monkeypatch, caplog, channel, reference):
    """未触发或保留分支保留 title/content 重复键，仍走旧发送异常重试。"""
    _install_boundaries(monkeypatch)
    _, notifier, getter = _install_onepush(monkeypatch)
    config = _base_config(
        channels=[deepcopy(channel)],
        retry={'interval': '2s', 'max_count': 3})
    template = config['push']['templates']['on_end']
    if not reference:
        template['title'] = '标题 {process_name}'
        template['content'] = '正文 {process_name}'
    sleep = Mock()
    monkeypatch.setattr(notif.time, 'sleep', sleep)

    with caplog.at_level(logging.DEBUG):
        result = notif.send_notification(config, 'on_end', process_name='demo')

    assert result == [(channel['provider'], False)]
    assert getter.call_count == 3
    assert notifier.notify.call_count == 0
    assert sleep.call_count == 2
    assert sleep.call_args_list == [call(2), call(2)]
    assert '通道消息准备失败' not in caplog.text
    assert '客户端发送失败' in caplog.text


def test_render_context_trigger_independence(monkeypatch):
    """触发只由原始模板直接引用决定，与截图结果及变量来源无关。"""
    for case in ('disabled', 'no_targets', 'all_failed'):
        if case == 'all_failed':
            _install_boundaries(
                monkeypatch,
                upload_result=UploadResult(False, None, None, ('catbox',), ()))
        else:
            _install_boundaries(monkeypatch)
        _install_onepush(monkeypatch)
        config = _base_config(
            channels=[{'provider': 'dingtalk', 'markdown': False}])
        if case == 'disabled':
            config['push']['templates']['on_end']['capture_screenshot'] = False
        if case == 'no_targets':
            config['push']['screenshot']['targets'] = []
        results = notif.send_notification(config, 'on_end', process_name='demo')
        assert results == [('dingtalk', True)]
        assert notif.get_notifier.return_value.notify.call_args.kwargs[
            'markdown'] is True

    _install_boundaries(monkeypatch)
    sent, notifier, _ = _install_onepush(monkeypatch)
    config = _base_config(channels=[{'provider': 'dingtalk', 'markdown': False}])
    results = notif.send_notification(
        config, 'on_end', process_name='demo', screenshot='ordinary')
    assert results == [('dingtalk', True)]
    assert sent == [('标题 demo', '正文 demo\n\nordinary')]
    assert notifier.notify.call_args.kwargs['markdown'] is False

    capture, upload = _install_boundaries(monkeypatch)
    sent, notifier, _ = _install_onepush(monkeypatch)
    config = _base_config(channels=[{'provider': 'dingtalk', 'markdown': False}])
    template = config['push']['templates']['on_end']
    template['title'] = '纯标题 {process_name}'
    template['content'] = '纯正文 {process_name}'
    results = notif.send_notification(config, 'on_end', process_name='demo')
    assert results == [('dingtalk', True)]
    assert capture.call_count == upload.call_count == 0
    assert sent == [('纯标题 demo', '纯正文 demo')]
    assert notifier.notify.call_args.kwargs['markdown'] is False

    variants = (
        ('标题 {process_name} {screenshot}', '正文 {process_name}'),
        ('标题 {process_name}', '正文 {process_name}\n\n{screenshot}'),
        ('标题 {screenshot} {screenshot}',
         '正文 {process_name}\n\n{screenshot}\n{screenshot}'),
    )
    for title_template, content_template in variants:
        capture, upload = _install_boundaries(monkeypatch)
        _install_onepush(monkeypatch)
        config = _base_config(
            channels=[{'provider': 'dingtalk', 'markdown': False}])
        template = config['push']['templates']['on_end']
        template['title'] = title_template
        template['content'] = content_template
        results = notif.send_notification(config, 'on_end', process_name='demo')
        assert results == [('dingtalk', True)]
        assert capture.call_count == 1
        assert upload.call_count == 1

    _install_boundaries(monkeypatch)
    _install_onepush(monkeypatch)
    config = _base_config(channels=[_smtp_channel()])
    captured = []
    original_log = notif._log_rendered_notification

    def spy_log(template_key, title, content, batch):
        """记录基准日志入参供派生内容隔离断言。"""
        captured.append((title, content))
        return original_log(template_key, title, content, batch)

    monkeypatch.setattr(notif, '_log_rendered_notification', spy_log)
    results = notif.send_notification(config, 'on_end', process_name='demo')
    assert results == [('smtp', True)]
    assert len(captured) == 1
    logged_title, logged_content = captured[0]
    assert logged_title == '标题 demo'
    assert '![screenshot_1](' in logged_content
    assert 'screenshot_1: ' not in logged_content
    assert 'multipart/alternative' not in logged_content


def _multi_window_targets(count):
    """构造不同窗口与输出名的多目标配置，保持分配顺序。"""
    return [
        {**WINDOW_TARGET, 'target': f'窗口{index}', 'out': f'shot_{index}'}
        for index in range(1, count + 1)
    ]


@pytest.mark.parametrize('count', (2, 3))
@pytest.mark.parametrize('subject', (None, ''))
def test_disabled_multi_target_smtp_title_entry(monkeypatch, count, subject):
    """禁用截图多目标时真实入口仍构造合法 SMTP 标题并发送成功。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, notifier, getter = _install_onepush(monkeypatch)
    sleep = Mock()
    monkeypatch.setattr(notif.time, 'sleep', sleep)
    config = _base_config(
        channels=[_smtp_channel(subject=subject)],
        targets=_multi_window_targets(count),
        retry={'interval': '2s', 'max_count': 3})
    template = config['push']['templates']['on_end']
    template['capture_screenshot'] = False
    template['title'] = '通知{screenshot}结束'
    template['content'] = 'BODY'
    before_config = deepcopy(config)

    results = notif.send_notification(config, 'on_end', process_name='demo')

    assert results == [('smtp', True)]
    assert sent == [('通知结束', 'BODY')]
    assert notifier.notify.call_count == 1
    getter.assert_called_once_with('smtp')
    capture.assert_not_called()
    upload.assert_not_called()
    sleep.assert_not_called()
    params = notifier.notify.call_args.kwargs
    msg = params['msg']
    assert params['title'] == '通知结束'
    assert params['content'] == 'BODY'
    assert str(msg['Subject']) == '通知结束'
    assert msg.get_content_type() == 'multipart/alternative'
    assert msg.get_body(preferencelist=('plain',)).get_content() == 'BODY\n'
    assert msg.get_body(preferencelist=('html',)).get_content() == 'BODY\n'
    assert config == before_config


@pytest.mark.parametrize('provider', ('dingtalk', 'discord'))
def test_disabled_multi_target_channel_content_entry(monkeypatch, provider):
    """禁用截图多目标时 dingtalk/discord 正文不产生多余分隔。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, notifier, getter = _install_onepush(monkeypatch)
    sleep = Mock()
    monkeypatch.setattr(notif.time, 'sleep', sleep)
    config = _base_config(
        channels=[{'provider': provider, 'token': 't1'}],
        targets=_multi_window_targets(2),
        retry={'interval': '2s', 'max_count': 3})
    template = config['push']['templates']['on_end']
    template['capture_screenshot'] = False
    template['title'] = 'T'
    template['content'] = 'A{screenshot}B'
    before_config = deepcopy(config)

    results = notif.send_notification(config, 'on_end', process_name='demo')

    assert results == [(provider, True)]
    assert sent == [('T', 'AB')]
    assert notifier.notify.call_count == 1
    getter.assert_called_once_with(provider)
    capture.assert_not_called()
    upload.assert_not_called()
    sleep.assert_not_called()
    assert config == before_config
    if provider == 'dingtalk':
        assert notifier.notify.call_args.kwargs['markdown'] is True


def _install_side_effect_probes(monkeypatch):
    """替换保存与上传上下文的构造点，用于零副作用断言。"""
    module = _pipeline()
    save = Mock(return_value=Mock(warnings=()))
    context = MagicMock()
    monkeypatch.setattr(module, 'save_automatic', save)
    monkeypatch.setattr(module, 'UploadContext', context)
    return save, context


REFERENCE_GATE_CASES = (
    ('no_reference', '标题 {process_name}', '正文 {process_name}', None, ()),
    ('escaped_reference', '标题 {{screenshot}}', '正文 {process_name}', None, ()),
    ('kwargs_value_mentions', '标题 {process_name}', '正文 {note}',
     {'note': '看图 {screenshot}'}, ()),
    ('kwargs_overrides_aggregate', '标题 {process_name}', '正文 {screenshot}',
     {'screenshot': 'ordinary'}, ()),
    ('kwargs_overrides_alias', '标题 {process_name}', '正文 {shot}',
     {'shot': 'ordinary'}, ('shot',)),
    ('kwargs_overrides_index', '标题 {process_name}', '正文 {screenshot_1}',
     {'screenshot_1': 'ordinary'}, ()),
)


@pytest.mark.parametrize('case', REFERENCE_GATE_CASES)
def test_capture_requires_owned_reference(monkeypatch, case):
    """有效目标但模板没有自有截图引用时，截图链路完全不启动。"""
    _, title_template, content_template, extra_kwargs, alias = case
    capture, upload = _install_boundaries(monkeypatch)
    save, context = _install_side_effect_probes(monkeypatch)
    sent, notifier, _ = _install_onepush(monkeypatch)
    target = WINDOW_TARGET if not alias else {**WINDOW_TARGET, 'out': alias[0]}
    config = _base_config(targets=(target,))
    template = config['push']['templates']['on_end']
    template['title'] = title_template
    template['content'] = content_template
    before_config = deepcopy(config)

    results = notif.send_notification(
        config, 'on_end', process_name='demo', **(extra_kwargs or {}))

    assert results == [('serverchan', True)]
    assert len(sent) == 1
    capture.assert_not_called()
    upload.assert_not_called()
    save.assert_not_called()
    context.assert_not_called()
    assert config == before_config
    assert notifier.notify.call_count == 1


def test_plain_text_reference_gate_keeps_formatting(monkeypatch):
    """无引用时正文与该模板的普通格式化结果逐字一致。"""
    _install_boundaries(monkeypatch)
    _install_side_effect_probes(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config()
    template = config['push']['templates']['on_end']
    template['title'] = '标题 {{screenshot}} {process_name}'
    template['content'] = '正文 {process_name}\n\n无截图\n\n'

    notif.send_notification(config, 'on_end', process_name='demo')

    assert sent == [('标题 {screenshot} demo', '正文 demo\n\n无截图\n\n')]


@pytest.mark.parametrize('variant', ('aggregate_title', 'alias_body', 'index_body'))
def test_owned_reference_triggers_one_batch(monkeypatch, variant):
    """聚合、实际别名与编号引用各自只触发一次批次，重试不重复捕获。"""
    capture, upload = _install_boundaries(monkeypatch)
    attempts = []

    def failing_notify(title=None, content=None, **params):
        """前两次失败、第三次成功，用于覆盖多通道重试复用同一批次。"""
        attempts.append((title, content))
        return _response(500 if len(attempts) < 3 else 200)

    sent = attempts
    notifier = MagicMock()
    notifier.notify.side_effect = failing_notify
    monkeypatch.setattr(notif, 'get_notifier', Mock(return_value=notifier))
    targets = _multi_window_targets(2)
    config = _base_config(
        channels=[{'provider': 'serverchan', 'sckey': 's1'}],
        targets=targets,
        retry={'interval': '2s', 'max_count': 3})
    template = config['push']['templates']['on_end']
    if variant == 'aggregate_title':
        template['title'] = '标题 {screenshot} {screenshot}'
        template['content'] = '正文 {process_name}'
    elif variant == 'alias_body':
        template['title'] = '标题 {process_name}'
        template['content'] = '正文 {shot_1}{shot_1}'
    else:
        template['title'] = '标题 {process_name}'
        template['content'] = '正文 {screenshot_2}{screenshot_2}'

    results = notif.send_notification(config, 'on_end', process_name='demo')

    assert results == [('serverchan', True)]
    assert capture.call_count == len(targets)
    assert upload.call_count == len(targets)
    assert len(sent) == 3
    assert sent[0] == sent[1] == sent[2]


def test_capture_reference_early_exit_boundaries(monkeypatch, caplog):
    """禁用、缺通道、模板错误与非法开关都在截图前退出，不给假提示。"""
    cases = []

    config = _base_config()
    config['push']['templates']['on_end']['capture_screenshot'] = False
    cases.append(('disabled', config))

    config = _base_config(channels=[])
    cases.append(('no_channel', config))

    config = _base_config()
    config['push']['templates']['on_end']['content'] = '正文 {missing}'
    cases.append(('missing_variable', config))

    config = _base_config()
    config['push']['templates']['on_end']['title'] = '标题 {screenshot'
    cases.append(('broken_format', config))

    config = _base_config()
    config['push']['templates']['on_end']['content'] = '正文 {screenshot.name}'
    cases.append(('placeholder_attribute', config))

    config = _base_config()
    config['push']['templates']['on_end']['capture_screenshot'] = 'yes'
    cases.append(('non_boolean_flag', config))

    for name, config in cases:
        capture, upload = _install_boundaries(monkeypatch)
        save, context = _install_side_effect_probes(monkeypatch)
        sent, _, _ = _install_onepush(monkeypatch)
        before_config = deepcopy(config)
        with caplog.at_level(logging.DEBUG):
            results = notif.send_notification(config, 'on_end', process_name='demo')
        if name in ('disabled', 'non_boolean_flag'):
            assert results == [('serverchan', True)]
            assert sent == [('标题 demo', '正文 demo\n\n')], name
        else:
            assert results == [], name
            assert sent == [], name
        capture.assert_not_called()
        upload.assert_not_called()
        save.assert_not_called()
        context.assert_not_called()
        assert config == before_config


def test_no_target_reference_shows_unconfigured_hint(monkeypatch):
    """无有效目标时引用聚合截图变量只显示固定配置失败提示，不启动上传。"""
    capture, upload = _install_boundaries(monkeypatch)
    save, context = _install_side_effect_probes(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config(targets=())
    template = config['push']['templates']['on_end']
    template['title'] = '标题 {process_name}'
    template['content'] = '正文 {screenshot}'

    results = notif.send_notification(config, 'on_end', process_name='demo')

    assert results == [('serverchan', True)]
    assert sent == [('标题 demo', '正文 截图配置失败：未配置有效截图目标')]
    capture.assert_not_called()
    upload.assert_not_called()
    save.assert_not_called()
    context.assert_not_called()


def test_unconfigured_index_shows_unconfigured_hint(monkeypatch):
    """引用未配置的编号只显示固定缺失提示，仍按整批策略捕获已配置目标。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config()
    template = config['push']['templates']['on_end']
    template['title'] = '标题 {process_name}'
    template['content'] = '正文 {screenshot_9}'

    results = notif.send_notification(config, 'on_end', process_name='demo')

    assert results == [('serverchan', True)]
    assert sent == [('标题 demo', '正文 截图失败：未配置该截图目标')]
    assert capture.call_count == upload.call_count == 1
