#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notification.py 与 utils.run_external_program 的失败判定测试。"""

import os
import sys
import logging
import smtplib
from collections import UserDict
from json import JSONDecodeError
import unittest
from unittest.mock import patch, MagicMock, Mock, call

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import notification as notif
from modules.notification_rendering import ScreenshotRenderContext
from modules.screenshot.pipeline import ScreenshotBatch
from modules.utils import run_external_program


def _make_response(status_code=200, json_body=None, text=''):
    """构造一个伪 requests.Response 对象用于判定测试。

    Args:
        status_code (int): HTTP 状态码。
        json_body: response.json() 的返回值；为 _RAISE 时模拟解析异常。
        text (str): response.text 内容。

    Returns:
        MagicMock: 具备 status_code/text/json 的伪响应对象。
    """
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_body is _RAISE:
        resp.json.side_effect = JSONDecodeError("not json", "", 0)
    else:
        resp.json.return_value = json_body
    return resp


_RAISE = object()


class TestIsPushSuccessful(unittest.TestCase):
    """测试 _is_push_successful 对各类响应的成败判定。"""

    def test_none_response_is_failure(self):
        """请求异常返回 None：判为失败。"""
        ok, reason = notif._is_push_successful(None)
        self.assertFalse(ok)
        self.assertIn("未收到响应", reason)

    def test_non_2xx_status_is_failure(self):
        """HTTP 400：判为失败。"""
        resp = _make_response(status_code=400, json_body=_RAISE, text='bad')
        ok, reason = notif._is_push_successful(resp)
        self.assertFalse(ok)
        self.assertIn("HTTP 400", reason)

    def test_2xx_unparsable_body_is_success(self):
        """HTTP 200 且响应体无法解析：仅凭状态码判为成功。"""
        resp = _make_response(status_code=200, json_body=_RAISE)
        ok, _ = notif._is_push_successful(resp)
        self.assertTrue(ok)

    def test_errcode_nonzero_is_failure(self):
        """钉钉限流 errcode=660026：判为失败。"""
        resp = _make_response(
            status_code=200,
            json_body={"errcode": 660026,
                       "errmsg": "sending too many messages per minute"})
        ok, reason = notif._is_push_successful(resp)
        self.assertFalse(ok)
        self.assertEqual("业务拒绝", reason)

    def test_code_nonzero_is_failure(self):
        """Qmsg code=500 KEY不存在：判为失败。"""
        resp = _make_response(
            status_code=200,
            json_body={"success": False, "reason": "KEY不存在", "code": 500})
        ok, reason = notif._is_push_successful(resp)
        self.assertFalse(ok)
        self.assertEqual("业务拒绝", reason)

    def test_success_false_is_failure(self):
        """显式 success=false：判为失败。"""
        resp = _make_response(
            status_code=200, json_body={"success": False, "reason": "x"})
        ok, _ = notif._is_push_successful(resp)
        self.assertFalse(ok)

    def test_code_200_is_success(self):
        """code=200 视为成功。"""
        resp = _make_response(status_code=200, json_body={"code": 200})
        ok, _ = notif._is_push_successful(resp)
        self.assertTrue(ok)

    def test_errcode_zero_is_success(self):
        """errcode=0 视为成功。"""
        resp = _make_response(status_code=200, json_body={"errcode": 0})
        ok, _ = notif._is_push_successful(resp)
        self.assertTrue(ok)


class TestNotifySingleChannel(unittest.TestCase):
    """测试 _notify_single_channel 在响应失败时不误报成功。"""

    def test_business_error_reports_failure(self):
        """服务端业务错误（notify 不抛异常）：应判失败并重试至上限返回 False。"""
        resp = _make_response(
            status_code=400, json_body={"success": False}, text='bad')
        fake_notifier = MagicMock()
        fake_notifier.notify.return_value = resp
        with patch.object(notif, 'get_notifier', return_value=fake_notifier), \
                patch.object(notif.time, 'sleep'):
            result = notif._notify_single_channel(
                {'provider': 'qmsg'}, 't', 'c', 0, 2)
        self.assertFalse(result)
        self.assertEqual(fake_notifier.notify.call_count, 2)

    def test_success_response_returns_true(self):
        """正常成功响应：返回 True 且只调用一次。"""
        resp = _make_response(status_code=200, json_body={"code": 0})
        fake_notifier = MagicMock()
        fake_notifier.notify.return_value = resp
        with patch.object(notif, 'get_notifier', return_value=fake_notifier):
            result = notif._notify_single_channel(
                {'provider': 'x'}, 't', 'c', 0, 3)
        self.assertTrue(result)
        self.assertEqual(fake_notifier.notify.call_count, 1)

    def test_client_exception_retries(self):
        """客户端异常（notify 抛错）：捕获并重试至上限返回 False。"""
        fake_notifier = MagicMock()
        fake_notifier.notify.side_effect = TypeError("missing arg")
        with patch.object(notif, 'get_notifier', return_value=fake_notifier), \
                patch.object(notif.time, 'sleep'):
            result = notif._notify_single_channel(
                {'provider': 'lark'}, 't', 'c', 0, 3)
        self.assertFalse(result)
        self.assertEqual(fake_notifier.notify.call_count, 3)


class TestRunExternalProgramGuard(unittest.TestCase):
    """测试 run_external_program 的路径守卫。"""

    def test_empty_path_raises(self):
        """空路径：抛 FileNotFoundError。"""
        with self.assertRaises(FileNotFoundError):
            run_external_program('')

    def test_missing_file_raises(self):
        """文件不存在：抛 FileNotFoundError，不触发 subprocess。"""
        with self.assertRaises(FileNotFoundError):
            run_external_program(r'C:\path\to\not_exist_script.bat')


def test_notify_single_channel_success_log_omits_title(caplog):
    """成功日志只报告通道结果，不再额外输出原始标题。"""
    resp = _make_response(status_code=200, json_body={"code": 0})
    fake_notifier = MagicMock()
    fake_notifier.notify.return_value = resp
    with patch.object(notif, 'get_notifier', return_value=fake_notifier):
        with caplog.at_level(logging.INFO, logger='modules.notification'):
            assert notif._notify_single_channel(
                {'provider': 'qmsg'}, 'PRIVATE_TITLE_9090', '正文', 0, 1)
    assert [record.getMessage() for record in caplog.records] == [
        '通知发送成功 [qmsg]']
    assert 'PRIVATE_TITLE_9090' not in caplog.text


SMTP_ALL_ACCEPTED = 'SMTP 服务器已接受全部收件人（不代表最终到达）'
SMTP_PARTIAL = 'SMTP 部分收件人被拒收，至少部分已接受；已终止重试'
SMTP_VERDICT_FAILED = 'SMTP 响应判定失败'


def _smtp_notifier(response=None, side_effect=None):
    """构造仅返回或抛出指定结果的 notifier 替身。"""
    fake_notifier = MagicMock()
    if side_effect is not None:
        fake_notifier.notify.side_effect = side_effect
    else:
        fake_notifier.notify.return_value = response
    return fake_notifier


@pytest.mark.parametrize(
    'response',
    ({}, {'private@example.test': (550, b'SECRET')}),
    ids=('empty', 'partial'))
def test_smtp_dict_acceptance(response, caplog):
    """SMTP 正常返回 dict：空与非空均立即判成功，不调用 HTTP 判定。"""
    fake_notifier = _smtp_notifier(response=response)
    http_check = Mock()
    failure_helper = Mock()
    sleep = Mock()
    with patch.object(notif, 'get_notifier', return_value=fake_notifier), \
            patch.object(notif, '_is_push_successful', http_check), \
            patch.object(notif, '_handle_attempt_failure', failure_helper), \
            patch.object(notif.time, 'sleep', sleep), \
            caplog.at_level(logging.INFO, logger='modules.notification'):
        result = notif._notify_single_channel(
            {'provider': 'smtp'}, 't', 'c', 2, 3)

    assert result is True
    assert fake_notifier.notify.call_count == 1
    sleep.assert_not_called()
    http_check.assert_not_called()
    failure_helper.assert_not_called()
    messages = [record.getMessage() for record in caplog.records]
    if response:
        assert messages == [SMTP_PARTIAL]
        assert '通知发送成功' not in caplog.text
        assert 'private@example.test' not in caplog.text
        assert 'SECRET' not in caplog.text
    else:
        assert messages == [SMTP_ALL_ACCEPTED]


@pytest.mark.parametrize(
    'response',
    (
        None, True, False, 0, 1, '', 'x', [], [1], UserDict(),
        _make_response(status_code=200, json_body={'code': 0}),
    ),
    ids=(
        'none', 'true', 'false', 'zero', 'one', 'empty_str', 'text',
        'empty_list', 'list', 'mapping', 'http200',
    ))
def test_smtp_unknown_response(response, caplog):
    """SMTP 未知返回类型固定失败，沿用有界重试且不回退 HTTP 判定。"""
    fake_notifier = _smtp_notifier(response=response)
    http_check = Mock()
    sleep = Mock()
    with patch.object(notif, 'get_notifier', return_value=fake_notifier), \
            patch.object(notif, '_is_push_successful', http_check), \
            patch.object(notif.time, 'sleep', sleep), \
            caplog.at_level(logging.INFO, logger='modules.notification'):
        result = notif._notify_single_channel(
            {'provider': 'smtp'}, 't', 'c', 2, 3)

    assert result is False
    assert fake_notifier.notify.call_count == 3
    assert sleep.call_args_list == [call(2), call(2)]
    assert SMTP_VERDICT_FAILED in caplog.text
    http_check.assert_not_called()


class _TruthinessBomb:
    """访问真假值或 repr 即计数并抛错的未知返回对象。"""

    def __init__(self):
        self.bool_calls = 0
        self.repr_calls = 0

    def __bool__(self):
        self.bool_calls += 1
        raise RuntimeError('SECRET')

    def __repr__(self):
        self.repr_calls += 1
        raise RuntimeError('SECRET')


def test_smtp_no_truthiness():
    """SMTP 判定不读取未知对象的真假值或 repr。"""
    response = _TruthinessBomb()
    fake_notifier = _smtp_notifier(response=response)
    sleep = Mock()
    with patch.object(notif, 'get_notifier', return_value=fake_notifier), \
            patch.object(notif.time, 'sleep', sleep):
        result = notif._notify_single_channel(
            {'provider': 'smtp'}, 't', 'c', 2, 3)

    assert result is False
    assert response.bool_calls == response.repr_calls == 0
    assert fake_notifier.notify.call_count == 3


class _ExplosiveLength(dict):
    """长度判定抛普通异常的 dict 子类。"""

    def __len__(self):
        raise RuntimeError('SECRET')


def test_smtp_dict_length_failure(caplog):
    """dict 长度判定抛普通异常：固定原因有界重试且不泄露异常正文。"""
    fake_notifier = _smtp_notifier(
        response=_ExplosiveLength({'private@example.test': (550, b'SECRET')}))
    sleep = Mock()
    with patch.object(notif, 'get_notifier', return_value=fake_notifier), \
            patch.object(notif.time, 'sleep', sleep), \
            caplog.at_level(logging.INFO, logger='modules.notification'):
        result = notif._notify_single_channel(
            {'provider': 'smtp'}, 't', 'c', 2, 3)

    assert result is False
    assert fake_notifier.notify.call_count == 3
    assert SMTP_VERDICT_FAILED in caplog.text
    assert 'SECRET' not in caplog.text


@pytest.mark.parametrize(
    'exception',
    (
        smtplib.SMTPRecipientsRefused(
            {'private@example.test': (550, b'SECRET')}),
        RuntimeError('SECRET'),
    ),
    ids=('recipients_refused', 'generic'))
def test_smtp_exception_retry(exception, caplog):
    """SMTP 发送异常沿用既有固定原因与有界重试。"""
    fake_notifier = _smtp_notifier(side_effect=exception)
    sleep = Mock()
    with patch.object(notif, 'get_notifier', return_value=fake_notifier), \
            patch.object(notif.time, 'sleep', sleep), \
            caplog.at_level(logging.INFO, logger='modules.notification'):
        result = notif._notify_single_channel(
            {'provider': 'smtp'}, 't', 'c', 2, 3)

    assert result is False
    assert fake_notifier.notify.call_count == 3
    assert sleep.call_count == 2
    assert '客户端发送失败' in caplog.text
    assert 'SECRET' not in caplog.text
    assert 'private@example.test' not in caplog.text


def test_smtp_quit_failure_then_acceptance(caplog):
    """退出阶段异常后重试得到 dict：仅重试一次即判接受。"""
    fake_notifier = _smtp_notifier(side_effect=[
        smtplib.SMTPResponseException(451, b'quit failed'),
        {'private@example.test': (550, b'SECRET')},
    ])
    sleep = Mock()
    with patch.object(notif, 'get_notifier', return_value=fake_notifier), \
            patch.object(notif.time, 'sleep', sleep), \
            caplog.at_level(logging.INFO, logger='modules.notification'):
        result = notif._notify_single_channel(
            {'provider': 'smtp'}, 't', 'c', 2, 3)

    assert result is True
    assert fake_notifier.notify.call_count == 2
    sleep.assert_called_once_with(2)


def test_smtp_final_attempt_acceptance(caplog):
    """最后一次尝试得到非空 dict：终止重试并判为至少部分接受。"""
    fake_notifier = _smtp_notifier(side_effect=[
        RuntimeError('SECRET'),
        RuntimeError('SECRET'),
        {'private@example.test': (550, b'SECRET')},
    ])
    sleep = Mock()
    with patch.object(notif, 'get_notifier', return_value=fake_notifier), \
            patch.object(notif.time, 'sleep', sleep), \
            caplog.at_level(logging.INFO, logger='modules.notification'):
        result = notif._notify_single_channel(
            {'provider': 'smtp'}, 't', 'c', 2, 3)

    assert result is True
    assert fake_notifier.notify.call_count == 3
    assert sleep.call_count == 2
    messages = [record.getMessage() for record in caplog.records]
    assert messages[-1] == SMTP_PARTIAL
    assert '通知发送成功' not in caplog.text


@pytest.mark.parametrize(
    'fake_notifier',
    (
        _smtp_notifier(side_effect=RuntimeError('SECRET')),
        _smtp_notifier(response=None),
    ),
    ids=('exception', 'unknown'))
def test_smtp_single_attempt(fake_notifier):
    """max_count=1 时异常或未知返回均单次失败且不等待。"""
    sleep = Mock()
    with patch.object(notif, 'get_notifier', return_value=fake_notifier), \
            patch.object(notif.time, 'sleep', sleep):
        result = notif._notify_single_channel(
            {'provider': 'smtp'}, 't', 'c', 2, 1)

    assert result is False
    assert fake_notifier.notify.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize(
    'response, expected, expected_calls',
    (
        ({}, False, 3),
        (_make_response(status_code=200, json_body={'code': 0}), True, 1),
        (_make_response(status_code=503, json_body=None), False, 3),
    ),
    ids=('dict', 'http200', 'http503'))
def test_non_smtp_keeps_http_contract(response, expected, expected_calls):
    """非 SMTP 通道继续使用既有 HTTP 判定与重试次数。"""
    fake_notifier = _smtp_notifier(response=response)
    sleep = Mock()
    with patch.object(notif, 'get_notifier', return_value=fake_notifier), \
            patch.object(notif.time, 'sleep', sleep):
        result = notif._notify_single_channel(
            {'provider': 'qmsg'}, 't', 'c', 2, 3)

    assert result is expected
    assert fake_notifier.notify.call_count == expected_calls


@pytest.mark.parametrize(
    'response',
    ({}, {'private@example.test': (550, b'SECRET')}),
    ids=('empty', 'partial'))
def test_smtp_accepted_logging_failure(response):
    """已确认接受后日志抛普通异常仍返回 True，不重发也不调用失败 helper。"""
    fake_notifier = _smtp_notifier(response=response)
    failure_helper = Mock()
    sleep = Mock()
    with patch.object(notif, 'get_notifier', return_value=fake_notifier), \
            patch.object(notif, '_handle_attempt_failure', failure_helper), \
            patch.object(notif.time, 'sleep', sleep), \
            patch.object(notif.LOGGER, 'info',
                         Mock(side_effect=RuntimeError('SECRET'))), \
            patch.object(notif.LOGGER, 'warning',
                         Mock(side_effect=RuntimeError('SECRET'))):
        result = notif._notify_single_channel(
            {'provider': 'smtp'}, 't', 'c', 2, 3)

    assert result is True
    assert fake_notifier.notify.call_count == 1
    failure_helper.assert_not_called()
    sleep.assert_not_called()


@pytest.mark.parametrize('signal_type', (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize('location', ('notify', 'length', 'logging'))
def test_smtp_control_signals_propagate(signal_type, location):
    """发送、长度判定与接受日志中的控制信号保持原对象传播。"""
    signal = signal_type('smtp-stop')
    fake_notifier = MagicMock()
    patches = [
        patch.object(notif, 'get_notifier', return_value=fake_notifier),
        patch.object(notif, '_handle_attempt_failure', Mock()),
        patch.object(notif.time, 'sleep', Mock()),
    ]

    if location == 'notify':
        fake_notifier.notify.side_effect = signal
    elif location == 'length':
        def length_signal():
            """构造长度判定时抛出控制信号的 dict 子类。"""
            class _SignalLength(dict):
                """长度访问抛出指定控制信号。"""

                def __len__(self):
                    raise signal

            return _SignalLength()

        fake_notifier.notify.return_value = length_signal()
    else:
        fake_notifier.notify.return_value = {}
        patches.append(
            patch.object(notif.LOGGER, 'info', Mock(side_effect=signal)))

    for item in patches:
        item.start()
    try:
        with pytest.raises(signal_type) as caught:
            notif._notify_single_channel(
                {'provider': 'smtp'}, 't', 'c', 2, 3)
    finally:
        for item in reversed(patches):
            item.stop()

    assert caught.value is signal
    assert fake_notifier.notify.call_count == 1


@pytest.mark.parametrize(
    'case', ('no_context', 'explicit_msg', 'disabled', 'failed', 'normal'))
def test_smtp_acceptance_independent_of_rendering(case):
    """SMTP 接受判定与截图渲染状态无关，显式 msg 对象身份保持不变。"""
    values = {'process_name': 'demo'}
    batch_values = {}
    if case in ('disabled', 'failed', 'normal'):
        if case == 'disabled':
            aggregate = ''
        elif case == 'failed':
            aggregate = '截图失败：合成失败'
        else:
            aggregate = '![shot](https://cdn.example.test/a.png)'
            batch_values['shot'] = aggregate
        batch_values['screenshot'] = aggregate
        values['screenshot'] = aggregate
    context = None
    if case != 'no_context':
        context = ScreenshotRenderContext(
            'T', '{screenshot}', values,
            ScreenshotBatch(batch_values, (), ()), True)

    channel = {'provider': 'smtp'}
    explicit = object()
    if case == 'explicit_msg':
        channel['msg'] = explicit

    fake_notifier = _smtp_notifier(
        response={'private@example.test': (550, b'SECRET')})
    http_check = Mock()
    with patch.object(notif, 'get_notifier', return_value=fake_notifier), \
            patch.object(notif, '_is_push_successful', http_check):
        result = notif._notify_single_channel(
            channel, 'T', '{screenshot}', 2, 3, 0, render_context=context)

    assert result is True
    assert fake_notifier.notify.call_count == 1
    http_check.assert_not_called()
    if case == 'explicit_msg':
        assert fake_notifier.notify.call_args.kwargs['msg'] is explicit


if __name__ == '__main__':
    unittest.main()
