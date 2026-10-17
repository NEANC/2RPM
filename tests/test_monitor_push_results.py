#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""monitor._render_push_results 的 SMTP 结果措辞与聚合测试。"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.monitor import _render_push_results


def test_smtp_result_words():
    """SMTP 成功与失败分别使用至少部分接受与未确认接受文案。"""
    sp = MagicMock()
    assert _render_push_results([('smtp', True)], sp) is False
    sp.write_done.assert_called_once_with(
        '通道 [smtp] SMTP 服务器至少部分接受（不代表最终到达）')
    sp.write_fail.assert_not_called()

    other_sp = MagicMock()
    assert _render_push_results([('smtp', False)], other_sp) is True
    other_sp.write_fail.assert_called_once_with('通道 [smtp] SMTP 未确认接受')
    other_sp.write_done.assert_not_called()

    empty_sp = MagicMock()
    assert _render_push_results([], empty_sp) is False
    empty_sp.write_done.assert_not_called()
    empty_sp.write_fail.assert_not_called()


def test_mixed_result_aggregation():
    """混合结果中 SMTP 与非 SMTP 文案独立，布尔聚合保持不变。"""
    mixed_sp = MagicMock()
    assert _render_push_results(
        [('smtp', True), ('qmsg', False)], mixed_sp) is False
    mixed_sp.write_done.assert_called_once_with(
        '通道 [smtp] SMTP 服务器至少部分接受（不代表最终到达）')
    mixed_sp.write_fail.assert_called_once_with('通道 [qmsg] 推送失败')
