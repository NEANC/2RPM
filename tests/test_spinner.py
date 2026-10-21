#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spinner.py 单元测试"""

import os
import sys
import logging
import threading
import unittest
from contextvars import copy_context
from unittest.mock import patch, MagicMock

import colorama

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import spinner as spinner_mod
from modules.log_safety import redact_text, safety_scope
from modules.spinner import current_spinner, spinner_phase

# 在 patch 覆盖模块属性前捕获真实类，供替身工厂构造真实实例
_REAL_SIMPLE_SPINNER = spinner_mod._SimpleTTYSpinner


def _make_console_handler(level=logging.INFO):
    """构造一个独立的控制台 StreamHandler 用于级别断言。

    Returns:
        logging.StreamHandler: 设定好级别、挂在临时 logger 上的处理器。
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    return handler


class _FakeTTYOutput:
    """记录 TTY spinner 写入内容的假输出流。"""

    def __init__(self):
        """初始化写入记录和刷新次数。"""
        self.writes = []
        self.flush_count = 0

    def write(self, text):
        """记录写入的文本。"""
        self.writes.append(text)

    def flush(self):
        """记录刷新次数。"""
        self.flush_count += 1

    def isatty(self):
        """返回 True 表示该输出流为 TTY。"""
        return True


class _FaultStream:
    """按内容注入一次性或永久写入故障的 TTY 输出流。

    命中 ``fail_on`` 的内容片段时抛出预置异常对象，用于核验清理路径能否
    在故障后继续尝试恢复光标；``permanent`` 为真时每次写入都抛错。
    """

    def __init__(self, error, fail_on, permanent=False):
        """记录故障异常对象、触发内容与是否永久损坏。

        Args:
            error (BaseException): 命中故障条件时要抛出的异常对象。
            fail_on (str): 触发故障的写入内容片段。
            permanent (bool): 真为每次写入都抛错，否则只抛一次后恢复。
        """
        self.error = error
        self.fail_on = fail_on
        self.permanent = permanent
        self.writes = []
        self.failed_once = False

    def write(self, text):
        """命中故障条件时抛出预置异常，否则记录写入内容。"""
        if self.permanent or (self.fail_on in text and not self.failed_once):
            self.failed_once = True
            raise self.error
        self.writes.append(text)

    def flush(self):
        """空实现，保持流接口兼容。"""

    def isatty(self):
        """恒为 TTY，强制走动画路径。"""
        return True


def _make_factory(created):
    """返回记录真实 spinner 实例的替身工厂函数。

    Args:
        created (list): 收集每次构造出的 _SimpleTTYSpinner 实例的列表。

    Returns:
        Callable: 按关键字 output 构造真实实例并记录的工厂。
    """

    def factory(text, output=None):
        """构造真实轻量 spinner 并记入 created 列表。"""
        instance = _REAL_SIMPLE_SPINNER(text, output=output)
        created.append(instance)
        return instance

    return factory


class TestSpinnerTTY(unittest.TestCase):
    """测试 TTY 为真时启用 yaspin 动画路径。"""

    def test_tty_uses_simple_spinner_and_done(self):
        """TTY 为真：进入后启动轻量 spinner，done 输出成功定格行。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_SimpleTTYSpinner', return_value=fake_spinner):
            with spinner_phase("等待中...") as sp:
                sp.text("运行中...")
                sp.done("完成")
        fake_spinner.start.assert_called_once()
        fake_spinner.set_text.assert_called_once_with("运行中...")
        fake_spinner.done.assert_called_once_with(
            colorama.Fore.GREEN + spinner_mod._ICON_DONE + "  完成"
            + colorama.Style.RESET_ALL)
        fake_spinner.stop.assert_called_once_with(clear_line=False)

    def test_tty_fail_uses_fail_icon(self):
        """TTY 为真：fail 输出红色 ❌ 定格行。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_SimpleTTYSpinner', return_value=fake_spinner):
            with spinner_phase("等待中...") as sp:
                sp.fail("失败")
        fake_spinner.fail.assert_called_once_with(
            colorama.Fore.RED + spinner_mod._ICON_FAIL + " 失败"
            + colorama.Style.RESET_ALL)

    def test_tty_write_delegates_to_spinner_write(self):
        """TTY write() 委托轻量 spinner 插入输出。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_SimpleTTYSpinner', return_value=fake_spinner):
            with spinner_phase("等待中...") as sp:
                sp.write("进程已退出运行")
        fake_spinner.write.assert_called_with("进程已退出运行")

    def test_tty_write_done_uses_green_icon(self):
        """TTY write_done() 以绿色 ✔️ 前缀插入输出且不定格 spinner。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_SimpleTTYSpinner', return_value=fake_spinner):
            with spinner_phase("等待中...") as sp:
                sp.write_done("进程已退出运行")
        fake_spinner.write.assert_called_once_with(
            colorama.Fore.GREEN + spinner_mod._ICON_DONE + "  进程已退出运行"
            + colorama.Style.RESET_ALL)
        fake_spinner.done.assert_not_called()

    def test_system_exit_marks_spinner_as_failed(self):
        """TTY：SystemExit(0) 应绿色 done 定格，SystemExit(非0) 应红色 fail。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_SimpleTTYSpinner', return_value=fake_spinner):
            with self.assertRaises(SystemExit):
                with spinner_phase("程序正在初始化..."):
                    raise SystemExit(0)
        fake_spinner.done.assert_called_once_with(
            colorama.Fore.GREEN + spinner_mod._ICON_DONE + "  程序已退出"
            + colorama.Style.RESET_ALL)


class TestSimpleTTYSpinner(unittest.TestCase):
    """测试自实现轻量 TTY spinner。"""

    def test_simple_spinner_tick_only_writes_frame(self):
        """轻量 spinner 的帧刷新只写帧字符，不重复写文案。"""
        output = _FakeTTYOutput()
        spinner = spinner_mod._SimpleTTYSpinner(
            "程序正在初始化...", output=output)
        spinner.start()
        output.writes.clear()
        spinner._render_next_frame()
        spinner.stop(clear_line=False)
        joined = "".join(output.writes)
        self.assertIn("\r", joined)
        self.assertNotIn("程序正在初始化", joined)


class TestTtySpinnerResultColor(unittest.TestCase):
    """测试 _TtySpinner.write 先脱敏再按结果语义受控着色。"""

    def test_write_result_color_after_redaction(self):
        """None 无色原样、True 绿、False 红，先脱敏后着色且不新增图标。"""
        secret = 'FAKE_TTY_SECRET_9157'
        injected = '\x1b[4m劫持\x1b[9m\x07'
        message = f'{spinner_mod._ICON_FAIL} 通道 [{secret}] 上传失败{injected}'
        bottom = MagicMock()
        handle = spinner_mod._TtySpinner(bottom)
        with safety_scope((secret,)):
            safe_text = redact_text(message)
            self.assertNotIn(secret, safe_text)

            handle.write(message)
            plain = bottom.write.call_args[0][0]
            bottom.write.reset_mock()

            handle.write(message, success=True)
            green = bottom.write.call_args[0][0]
            bottom.write.reset_mock()

            handle.write(message, success=False)
            red = bottom.write.call_args[0][0]
            bottom.write.reset_mock()

        # 既有脱敏合同决定期望，不用被测 write 生成期望
        self.assertEqual(plain, safe_text)
        self.assertEqual(
            green, colorama.Fore.GREEN + safe_text + colorama.Style.RESET_ALL)
        self.assertEqual(
            red, colorama.Fore.RED + safe_text + colorama.Style.RESET_ALL)
        self.assertNotIn('\x1b', plain)
        for written in (plain, green, red):
            self.assertNotIn(secret, written)
            self.assertNotIn('\x1b[4m', written)
            self.assertNotIn('\x1b[9m', written)
            self.assertNotIn('\x07', written)
            self.assertEqual(written.count(spinner_mod._ICON_FAIL), 1)
        bottom.done.assert_not_called()
        bottom.fail.assert_not_called()
        bottom.stop.assert_not_called()

        # 关闭后不再写入
        handle.done('收尾')
        bottom.done.assert_called_once()
        bottom.write.reset_mock()
        handle.write(message, success=True)
        bottom.write.assert_not_called()


class TestSpinnerNonTTY(unittest.TestCase):
    """测试非 TTY 时降级为逐行日志路径。"""

    def test_non_tty_falls_back_to_logging(self):
        """非 TTY：不调用 yaspin，text/done 走 logger.info。"""
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=False), \
                patch.object(spinner_mod, '_SimpleTTYSpinner') as mk, \
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
                    patch.object(spinner_mod, '_SimpleTTYSpinner'):
                with spinner_phase("等待中..."):
                    self.assertEqual(console.level, logging.WARNING)
            self.assertEqual(console.level, logging.WARNING)
        finally:
            root.handlers = saved

    def test_non_tty_write_logs_info(self):
        """非 TTY write() 以 INFO 级别记录日志。"""
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=False), \
                patch.object(spinner_mod.LOGGER, 'info') as log_info:
            with spinner_phase("等待中...") as sp:
                sp.write("进程已退出运行")
            log_info.assert_any_call("进程已退出运行")

    def test_non_tty_write_done_logs_info_with_icon(self):
        """非 TTY write_done() 以 INFO 级别记录带 ✔️ 前缀的日志。"""
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=False), \
                patch.object(spinner_mod.LOGGER, 'info') as log_info:
            with spinner_phase("等待中...") as sp:
                sp.write_done("进程已退出运行")
            log_info.assert_any_call(
                spinner_mod._ICON_DONE + "  进程已退出运行")

    def test_non_tty_failure_lines_use_single_space(self):
        """非 TTY：fail 与 write_fail 的失败行均为图标后单空格。"""
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=False), \
                patch.object(spinner_mod.LOGGER, 'error') as log_error:
            with spinner_phase("等待中...") as sp:
                sp.fail("失败")
                sp.write_fail("失败")
        log_error.assert_any_call(spinner_mod._ICON_FAIL + " 失败")
        self.assertEqual(log_error.call_count, 2)


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
                patch.object(spinner_mod, '_SimpleTTYSpinner', return_value=fake_spinner):
            with spinner_phase("等待中...") as sp:
                self.assertEqual(self.console.level, logging.CRITICAL + 1)
                sp.done("完成")
        self.assertEqual(self.console.level, logging.INFO)

    def test_file_handler_stays_debug_while_muted(self):
        """静音期间文件处理器级别不受影响，始终保持 DEBUG。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_SimpleTTYSpinner', return_value=fake_spinner):
            with spinner_phase("等待中...") as sp:
                self.assertEqual(self.file_handler.level, logging.DEBUG)
                sp.done("完成")
        self.assertEqual(self.file_handler.level, logging.DEBUG)

    def test_console_restored_on_exception(self):
        """块内异常时控台级别仍被还原，异常向上抛出。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_SimpleTTYSpinner', return_value=fake_spinner):
            with self.assertRaises(ValueError):
                with spinner_phase("等待中...") as sp:
                    raise ValueError("boom")
        self.assertEqual(self.console.level, logging.INFO)


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
                patch.object(spinner_mod, '_SimpleTTYSpinner', return_value=fake_spinner):
            with spinner_phase("等待中...") as sp:
                self.assertEqual(self._count_write_handlers(), 1)
                sp.done("完成")
        self.assertEqual(self._count_write_handlers(), 0)

    def test_write_handler_removed_on_exception(self):
        """块内异常时改道处理器仍被移除。"""
        fake_spinner = MagicMock()
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, '_SimpleTTYSpinner', return_value=fake_spinner):
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


class TestNotifyFail(unittest.TestCase):
    """测试 spinner 块外的干净 ❌ 失败行输出。"""

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

    def test_tty_prints_clean_fail_line(self):
        """TTY：print 一条红色 ❌ 单空格行，全量记录写入文件。"""
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, 'print', create=True) as fake_print, \
                patch.object(spinner_mod.LOGGER, 'critical') as log_crit:
            spinner_mod.notify_fail("程序终止运行")
        fake_print.assert_called_once_with(
            colorama.Fore.RED + spinner_mod._ICON_FAIL + " 程序终止运行"
            + colorama.Style.RESET_ALL)
        log_crit.assert_called_once_with("程序终止运行", exc_info=False)

    def test_tty_mutes_then_restores_console(self):
        """TTY：写文件期间控台提级到 CRITICAL+1，结束后还原。"""
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, 'print', create=True):
            self.console.setLevel(logging.INFO)
            spinner_mod.notify_fail("程序终止运行")
        self.assertEqual(self.console.level, logging.INFO)

    def test_tty_passes_exc_info_to_file(self):
        """TTY：exc_info=True 透传给 LOGGER.critical 以保留 traceback。"""
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
                patch.object(spinner_mod, 'print', create=True), \
                patch.object(spinner_mod.LOGGER, 'critical') as log_crit:
            spinner_mod.notify_fail("程序出现异常", exc_info=True)
        log_crit.assert_called_once_with("程序出现异常", exc_info=True)

    def test_non_tty_logs_critical_with_icon(self):
        """非 TTY：降级为 LOGGER.critical，文案含 ❌ 前缀。"""
        with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=False), \
                patch.object(spinner_mod.LOGGER, 'critical') as log_crit:
            spinner_mod.notify_fail("程序终止运行", exc_info=True)
        log_crit.assert_called_once_with(
            spinner_mod._ICON_FAIL + " 程序终止运行", exc_info=True)


class TestSpinnerPhaseContract(unittest.TestCase):
    """测试显式输出流、公开句柄状态与可恢复的临时文案。"""

    def test_phase_uses_selected_stream(self):
        """显式 stream 的 isatty 与实际写入一致，stdout 的 TTY 状态不影响判定。"""
        stderr = sys.stderr
        combos = ((True, True), (True, False), (False, True), (False, False))
        for stdout_tty, stderr_tty in combos:
            with self.subTest(stdout_tty=stdout_tty, stderr_tty=stderr_tty):
                created = []
                with patch.object(spinner_mod.sys.stdout, 'isatty',
                                  return_value=stdout_tty), \
                        patch.object(spinner_mod.sys.stderr, 'isatty',
                                     return_value=stderr_tty), \
                        patch.object(spinner_mod, '_SimpleTTYSpinner',
                                     side_effect=_make_factory(created)) as factory, \
                        patch.object(spinner_mod.LOGGER, 'info') as log_info:
                    with spinner_phase("等待中", stream=stderr) as handle:
                        if stderr_tty:
                            self.assertEqual(handle.stream, stderr)
                if stderr_tty:
                    self.assertIs(factory.call_args.kwargs['output'], stderr)
                    self.assertEqual(len(created), 1)
                    self.assertFalse(log_info.called)
                else:
                    factory.assert_not_called()
                    self.assertTrue(log_info.called)

    def test_temporary_text_lifo(self):
        """temporary_text 三层嵌套严格后进先出恢复公开文案，且不关闭借用句柄。"""
        output = _FakeTTYOutput()
        texts = []
        with spinner_phase("等待中", stream=output) as handle:
            texts.append(handle.message)
            with spinner_mod.temporary_text(handle, "正在上传截图"):
                texts.append(handle.message)
                with spinner_mod.temporary_text(handle, "内层"):
                    texts.append(handle.message)
                texts.append(handle.message)
            texts.append(handle.message)
            self.assertFalse(handle.closed)
            self.assertNotIn(spinner_mod._SHOW_CURSOR, "".join(output.writes))
        self.assertEqual(
            texts, ['等待中', '正在上传截图', '内层', '正在上传截图', '等待中'])
        self.assertTrue(handle.closed)

    def test_current_handle_owner_and_closed(self):
        """current_spinner 只在本线程返回未关闭句柄，复制的 Context 不授予他线程所有权。"""
        output = _FakeTTYOutput()
        queried = {}
        with spinner_phase("等待中", stream=output) as handle:
            self.assertEqual(handle.owner_thread_id, threading.get_ident())
            self.assertIs(current_spinner(), handle)
            self.assertFalse(handle.closed)
            copied = copy_context()

            def query():
                """在另一线程按复制的 Context 读取当前句柄。"""
                queried['handle'] = current_spinner()

            worker = threading.Thread(target=lambda: copied.run(query))
            worker.start()
            worker.join()
            self.assertIsNone(queried['handle'])
        self.assertTrue(handle.closed)
        self.assertIsNone(current_spinner())


class TestSpinnerSilentCleanup(unittest.TestCase):
    """测试 report_exit=False 上传阶段在故障与信号下的资源释放与异常保真。"""

    def setUp(self):
        """安装一个 INFO 控制台处理器并保存根 logger 原处理器。"""
        self.root = logging.getLogger()
        self.saved = self.root.handlers[:]
        self.root.handlers = []
        self.console = _make_console_handler(logging.INFO)
        self.root.addHandler(self.console)

    def tearDown(self):
        """还原根 logger 的处理器。"""
        self.root.handlers = self.saved

    def _count_write_handlers(self):
        """统计根 logger 上挂载的改道处理器数量。"""
        return sum(
            1 for h in self.root.handlers
            if isinstance(h, spinner_mod._SpinnerWriteHandler)
        )

    def test_silent_phase_cleanup(self):
        """启动/写入/退出故障下静默阶段释放全部资源，并原样保留在途异常对象。"""
        cases = [
            ('启动一次性故障', spinner_mod._HIDE_CURSOR, False, 'idle', 'stream'),
            ('写入一次性故障', '注入写入', False, 'write', 'stream'),
            ('退出一次性故障-中断', spinner_mod._SHOW_CURSOR, False, 'raise', 'control'),
            ('退出一次性故障-退出', spinner_mod._SHOW_CURSOR, False, 'raise', 'control'),
            ('永久损坏流', spinner_mod._HIDE_CURSOR, True, 'idle', 'stream'),
        ]
        signals = {
            '退出一次性故障-中断': KeyboardInterrupt(),
            '退出一次性故障-退出': SystemExit(3),
        }
        for name, fail_on, permanent, body_kind, expect in cases:
            with self.subTest(case=name):
                self.console.setLevel(logging.INFO)
                error = OSError('合成输出流故障')
                stream = _FaultStream(error, fail_on, permanent=permanent)
                created = []
                signal = signals.get(name)
                expected = error if expect == 'stream' else signal
                with patch.object(spinner_mod, '_SimpleTTYSpinner',
                                  side_effect=_make_factory(created)):
                    with self.assertRaises(BaseException) as caught:
                        with spinner_phase("等待中", stream=stream,
                                           report_exit=False) as handle:
                            if body_kind == 'write':
                                handle.write('注入写入')
                            elif body_kind == 'raise':
                                raise signal
                joined = "".join(stream.writes)
                instance = created[0]
                self.assertIs(caught.exception, expected)
                self.assertNotIn("程序已退出", joined)
                self.assertEqual(self.console.level, logging.INFO)
                self.assertEqual(self._count_write_handlers(), 0)
                self.assertIsNone(current_spinner())
                self.assertTrue(instance._stop_event.is_set())
                self.assertTrue(instance._thread is None
                                or not instance._thread.is_alive())
                if fail_on == spinner_mod._HIDE_CURSOR and not permanent:
                    self.assertIn(spinner_mod._SHOW_CURSOR, joined)

    def test_report_exit_default_keeps_exit_message(self):
        """report_exit 缺省为真时，块内异常仍输出既有“程序已退出”提示。"""
        self.console.setLevel(logging.INFO)
        stream = _FakeTTYOutput()
        with patch.object(spinner_mod, '_SimpleTTYSpinner',
                          side_effect=_make_factory([])):
            with self.assertRaises(ValueError):
                with spinner_phase("等待中", stream=stream):
                    raise ValueError("boom")
        self.assertIn("程序已退出", "".join(stream.writes))


if __name__ == '__main__':
    unittest.main()
