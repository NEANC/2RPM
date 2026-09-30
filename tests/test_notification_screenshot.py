#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notification 公共链路接入截图的端到端测试，全部边界由本地替身实现。"""

import logging
import os
import sys
from copy import deepcopy
from importlib import import_module
from unittest.mock import MagicMock, Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import notification as notif
from modules.image_host.core import UploadResult
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
