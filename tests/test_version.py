#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""version.py banner 与退出提示 smoke 测试"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.version import VERSION, print_info, print_exit_info


class TestBanner(unittest.TestCase):
    """启动横幅 smoke 测试。"""

    def test_print_info_runs_and_contains_version(self):
        """print_info 不崩溃且输出包含版本号与项目副标题。"""
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_info()
        out = buf.getvalue()
        self.assertIn(VERSION, out)
        self.assertIn("Running-Runtime Process Monitoring", out)
        self.assertIn("WTFPL", out)


class TestExitInfo(unittest.TestCase):
    """结束状态提示 smoke 测试。"""

    def test_exit_zero_shows_success(self):
        """退出码 0：无输出（spinner 已提供完成反馈）。"""
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_exit_info(0)
        out = buf.getvalue()
        self.assertEqual(out, "")

    def test_exit_nonzero_shows_failure(self):
        """退出码非 0：输出含该退出码。"""
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_exit_info(1)
        out = buf.getvalue()
        self.assertIn("exit 1", out)


if __name__ == '__main__':
    unittest.main()
