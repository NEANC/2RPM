#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spinner.py 单元测试"""

import os
import sys
import logging
import unittest
from unittest.mock import patch, MagicMock

import colorama

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import spinner as spinner_mod
from modules.spinner import spinner_phase


def _make_console_handler(level=logging.INFO):
    """构造一个独立的控制台 StreamHandler 用于级别断言。

    Returns:
        logging.StreamHandler: 设定好级别、挂在临时 logger 上的处理器。
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    return handler


class TestSpinnerTTY(unittest.TestCase):
    """测试 TTY 为真时启用 yaspin 动画路径。"""

    def test_tty_uses_yaspin_and_done(self):
        """TTY 为真：进入退出不抛错，done 调用 ok 传入图标+空格+消息。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_make_yaspin', return_value=fake_spinner):
            with spinner_phase("等待中...") as sp:
                sp.text("运行中...")
                sp.done("完成")
        fake_spinner.start.assert_called_once()
        fake_spinner.ok.assert_called_once_with(
            colorama.Fore.GREEN + spinner_mod._ICON_DONE + " 完成"
            + colorama.Style.RESET_ALL)
        fake_spinner.stop.assert_called()

    def test_tty_fail_uses_fail_icon(self):
        """TTY 为真：fail 调用 yaspin.fail 传入红色图标+空格+消息。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_make_yaspin', return_value=fake_spinner):
            with spinner_phase("等待中...") as sp:
                sp.fail("失败")
        fake_spinner.fail.assert_called_once_with(
            colorama.Fore.RED + spinner_mod._ICON_FAIL + " 失败"
            + colorama.Style.RESET_ALL)


class TestSpinnerNonTTY(unittest.TestCase):
    """测试非 TTY 时降级为逐行日志路径。"""

    def test_non_tty_falls_back_to_logging(self):
        """非 TTY：不调用 yaspin，text/done 走 logger.info。"""
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=False), \
                patch.object(spinner_mod, '_make_yaspin') as mk, \
                patch.object(spinner_mod.LOGGER, 'info') as log_info:
            with spinner_phase("等待中...") as sp:
                sp.text("运行中...")
                sp.done("完成")
        mk.assert_not_called()
        self.assertTrue(log_info.called)

    def test_non_tty_does_not_touch_console_level(self):
        """非 TTY：不应改动控台处理器级别（进入与退出均保持不变）。"""
        root = logging.getLogger()
        saved = root.handlers[:]
        root.handlers = []
        console = _make_console_handler(logging.WARNING)
        root.addHandler(console)
        try:
            with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=False), \
                    patch.object(spinner_mod, '_make_yaspin'):
                with spinner_phase("等待中..."):
                    self.assertEqual(console.level, logging.WARNING)
            self.assertEqual(console.level, logging.WARNING)
        finally:
            root.handlers = saved


class TestSpinnerConsoleMute(unittest.TestCase):
    """测试控台日志静音与还原。"""

    def setUp(self):
        """搭建含一个 INFO 控制台处理器与一个 DEBUG 文件处理器的根 logger 现场。"""
        self.root = logging.getLogger()
        self.saved = self.root.handlers[:]
        self.root.handlers = []
        self.console = _make_console_handler(logging.INFO)
        self.root.addHandler(self.console)
        self.file_handler = logging.FileHandler(os.devnull)
        self.file_handler.setLevel(logging.DEBUG)
        self.root.addHandler(self.file_handler)

    def tearDown(self):
        """还原根 logger 的处理器并关闭文件处理器。"""
        self.file_handler.close()
        self.root.handlers = self.saved

    def test_console_muted_then_restored(self):
        """进入时控台提级到 CRITICAL+1，退出后还原 INFO。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_make_yaspin', return_value=fake_spinner):
            with spinner_phase("等待中...") as sp:
                self.assertEqual(self.console.level, logging.CRITICAL + 1)
                sp.done("完成")
        self.assertEqual(self.console.level, logging.INFO)

    def test_file_handler_stays_debug_while_muted(self):
        """静音期间文件处理器级别不受影响，始终保持 DEBUG。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_make_yaspin', return_value=fake_spinner):
            with spinner_phase("等待中...") as sp:
                self.assertEqual(self.file_handler.level, logging.DEBUG)
                sp.done("完成")
        self.assertEqual(self.file_handler.level, logging.DEBUG)

    def test_console_restored_on_exception(self):
        """块内异常时控台级别仍被还原，异常向上抛出。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_make_yaspin', return_value=fake_spinner):
            with self.assertRaises(ValueError):
                with spinner_phase("等待中...") as sp:
                    raise ValueError("boom")
        self.assertEqual(self.console.level, logging.INFO)
        fake_spinner.stop.assert_called()


class TestSpinnerWriteHandler(unittest.TestCase):
    """测试 CRITICAL 改道处理器的挂载、移除与 emit 行为。"""

    def setUp(self):
        """搭建仅含一个 INFO 控制台处理器的根 logger 现场。"""
        self.root = logging.getLogger()
        self.saved = self.root.handlers[:]
        self.root.handlers = []
        self.console = _make_console_handler(logging.INFO)
        self.root.addHandler(self.console)

    def tearDown(self):
        """还原根 logger 的处理器。"""
        self.root.handlers = self.saved

    def _count_write_handlers(self):
        """统计根 logger 上挂载的改道处理器数量。

        Returns:
            int: _SpinnerWriteHandler 实例数量。
        """
        return sum(
            1 for h in self.root.handlers
            if isinstance(h, spinner_mod._SpinnerWriteHandler)
        )

    def test_write_handler_mounted_then_removed(self):
        """进入挂载改道处理器，正常退出后移除。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_make_yaspin', return_value=fake_spinner):
            with spinner_phase("等待中...") as sp:
                self.assertEqual(self._count_write_handlers(), 1)
                sp.done("完成")
        self.assertEqual(self._count_write_handlers(), 0)

    def test_write_handler_removed_on_exception(self):
        """块内异常时改道处理器仍被移除。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_make_yaspin', return_value=fake_spinner):
            with self.assertRaises(ValueError):
                with spinner_phase("等待中..."):
                    raise ValueError("boom")
        self.assertEqual(self._count_write_handlers(), 0)

    def test_emit_routes_critical_to_spinner_write(self):
        """CRITICAL 记录经 emit 调用 spinner.write，文本含红色 ❌ 前缀。"""
        fake_spinner = MagicMock()
        handler = spinner_mod._SpinnerWriteHandler(fake_spinner)
        record = logging.LogRecord(
            name="t", level=logging.CRITICAL, pathname="", lineno=0,
            msg="致命错误", args=(), exc_info=None,
        )
        handler.emit(record)
        fake_spinner.write.assert_called_once()
        written = fake_spinner.write.call_args[0][0]
        self.assertIn(spinner_mod._ICON_FAIL, written)
        self.assertIn("致命错误", written)
        self.assertIn(colorama.Fore.RED, written)


if __name__ == '__main__':
    unittest.main()
