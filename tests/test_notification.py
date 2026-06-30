#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notification.py 与 utils.run_external_program 的失败判定测试。"""

import os
import sys
import logging
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import notification as notif
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
        resp.json.side_effect = ValueError("not json")
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
        self.assertIn("660026", reason)

    def test_code_nonzero_is_failure(self):
        """Qmsg code=500 KEY不存在：判为失败。"""
        resp = _make_response(
            status_code=200,
            json_body={"success": False, "reason": "KEY不存在", "code": 500})
        ok, reason = notif._is_push_successful(resp)
        self.assertFalse(ok)
        self.assertIn("500", reason)

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


if __name__ == '__main__':
    unittest.main()
