#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notification 公共链路接入截图的端到端测试，全部边界由本地替身实现。"""

import json
import logging
import netrc
import os
import socket
import sys
from collections import Counter
from copy import deepcopy
from importlib import import_module
from io import BytesIO
from unittest.mock import MagicMock, Mock

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
        lambda source, name: CaptureResult(
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
        """返回成功响应，不发起任何网络请求。"""
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


def test_conflicting_out_is_renamed_and_appended(monkeypatch):
    """out 不得覆盖普通变量，改名结果按既有规则补附到正文末尾。"""
    capture, upload = _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config(targets=[{**WINDOW_TARGET, 'out': 'process_name'}])
    config['push']['templates']['on_end']['content'] = '正文 {process_name}'
    results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    body = sent[0][1]
    assert body.startswith('正文 demo.exe')
    assert body.count(URL) == 1
    assert '![screenshot_1](' in body


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
    monkeypatch.setattr(module, 'capture', Mock(side_effect=lambda source, name: (
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


def test_success_log_unchanged_without_screenshot(monkeypatch, caplog):
    """未使用截图结果时成功日志保持原有逐字格式。"""
    _install_boundaries(monkeypatch)
    sent, _, _ = _install_onepush(monkeypatch)
    config = _base_config()
    config['push']['templates']['on_end']['content'] = '正文 {process_name}'
    with caplog.at_level(logging.INFO, logger='modules.notification'):
        results = notif.send_notification(config, 'on_end', process_name='demo.exe')
    assert results == [('serverchan', True)]
    assert '通知发送成功 [serverchan]: 标题 demo.exe' in caplog.text


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

    capture = Mock(side_effect=lambda source, name: CaptureResult(
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


def _profile_response(default=14):
    """构造账号默认存储合成响应。"""
    return 200, {'status': 'success', 'data': {
        'options': {'default_storage_id': default},
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
        image_host=[{'provider': 'boltp', 'token': SECRET}], **kwargs)


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
    state['replies'] = [_group_response(), _profile_response(),
                        _upload_response(), _upload_response()]
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
        'GET', 'GET', 'POST', 'POST']
    assert [request.url.split('/api/v2')[1]
            for request in state['requests']] == [
                '/group', '/user/profile', '/upload', '/upload']
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
        assert caplog.text.count(warning) == 1
    assert not state['replies']


def test_http_next_event_uses_new_context_and_only_metadata_disk_cache(
        notification_http, monkeypatch):
    """新事件重新截图上传，允许复用有效磁盘元数据而不复用图片结果。"""
    state = notification_http
    sent, _, _ = _install_onepush(monkeypatch)
    config = _http_config()
    state['replies'] = [_group_response(), _profile_response(),
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
        'GET', 'GET', 'POST', 'POST', 'POST', 'POST']
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
    state['replies'] = [_group_response(retention=5), _profile_response(),
                        reply, reply]
    config = _http_config()
    config['push']['screenshot']['image_host'][0].update(
        expiration='20s', options={'storage_id': 99})
    sent, _, _ = _install_onepush(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        assert notif.send_notification(config, 'on_end', process_name='demo')
    body = sent[0][1]
    warnings = ('手填储存驱动不可用，已自动替代', '保存期限已按图床上限缩短')
    assert body.count('截图配置提示') == 1
    assert body.index(warnings[0]) < body.index(warnings[1])
    for warning in warnings:
        assert body.count(warning) == caplog.text.count(warning) == 1
    assert ('截图上传失败：图床拒绝上传' in body) is not success
    assert (URL in body) is success
    assert len(state['requests']) == 4
    assert SECRET not in str(sent) + caplog.text
    assert URL not in caplog.text


@pytest.mark.parametrize('provider, token', [
    ('beeimg_cn', SECRET), ('boltp', 'FAKE_OTHER_CREDENTIAL'),
])
def test_http_fallback_isolates_identity_and_preserves_success(
        notification_http, monkeypatch, caplog, provider, token):
    """前站失败后备用成功，各身份只查询一次且历史失败不污染图片。"""
    state = notification_http
    rejected = (200, {'status': 'error', 'message': SECRET})
    state['replies'] = [
        _group_response(), _profile_response(), rejected,
        _group_response((21,)), _profile_response(21), _upload_response(),
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
        'GET', 'GET', 'POST', 'GET', 'GET', 'POST', 'POST', 'POST']
    assert requests_seen[0].headers['Authorization'] == 'Bearer ' + SECRET
    assert requests_seen[3].headers['Authorization'] == 'Bearer ' + token
    assert ('beeimg.cn' in requests_seen[3].url) is (provider == 'beeimg_cn')
    for title, body in sent:
        assert title.count(URL) == body.count(URL) == 2
        assert '截图上传失败' not in title + body
        assert SECRET not in title + body
    assert SECRET not in caplog.text and token not in caplog.text
    assert URL not in caplog.text
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
        _group_response(), _profile_response(), rejected,
        _group_response((21,)), _profile_response(21), next_image,
        rejected, _group_response((22,)), _profile_response(22),
        _upload_response(),
    ]
    config = _http_config()
    config['push']['screenshot']['image_host'][0]['expiration'] = '30s'
    sent, _, _ = _install_onepush(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        assert notif.send_notification(config, 'on_end', process_name='demo')
    posts = [request for request in state['requests'] if request.method == 'POST']
    assert len(posts) == 4
    assert len(state['requests']) == 10
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
        state['replies'] = [_group_response(), _profile_response(),
                            replies[case], replies[case]]
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
    summary = '截图上传失败：' + expected
    assert sent[0][0].count(summary) == sent[0][1].count(summary) == 2
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
    state['replies'] = [_group_response(), _profile_response(), _upload_response()]
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
    state['replies'] = [_group_response(), _profile_response(),
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
    _install_onepush(monkeypatch)
    assert notif.send_notification(config, 'on_end', process_name='demo')
    assert values == [SECRET]
    assert len(state['requests']) == 4


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
    expected = '截图上传失败' if ordinary else '截图上传失败：图床 HTTP 请求失败'
    assert sent == [('标题 ' + expected, '正文 demo\n\n' + expected)]
    assert SECRET not in str(sent) + caplog.text
    assert capture.call_count == upload.call_count == 1
