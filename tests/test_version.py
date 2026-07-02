#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""version.py banner smoke 测试"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.version import VERSION, print_info


class TestBanner(unittest.TestCase):
    """启动横幅 smoke 测试。"""

    def test_print_info_runs_and_contains_version(self):
        """print_info 不崩溃且输出包含版本号与项目副标题。"""
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_info()
        out = buf.getvalue()
        self.assertIn(VERSION, out)
        self.assertIn("Running Runtime Process Monitoring", out)
        self.assertIn("WTFPL", out)



if __name__ == '__main__':
    unittest.main()
