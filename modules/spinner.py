#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import threading
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TextIO

import colorama

from modules.log_safety import FORMAT_FAILURE
from modules.log_safety import redact_text

LOGGER = logging.getLogger(__name__)

# 当前活动的 TTY spinner 句柄，供入口复用外层实例而不新建嵌套 spinner
_CURRENT_SPINNER = ContextVar('current_spinner', default=None)

# 完成 / 失败 / 致命的定格图标（取自 pixi_alas_install.sh#L47-49）
_ICON_DONE = "\u2714\ufe0f"  # ✔️
_ICON_FAIL = "\u274c"        # ❌

# spinner 帧间隔: 200ms
_SPINNER_INTERVAL = 200

# spinner 帧列表
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
# 清行 ANSI 序列
_CLEAR_LINE = "\r\033[2K"
# 隐藏/显示光标 ANSI 序列
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"


class _SimpleTTYSpinner:
    """仅刷新行首帧字符的轻量 TTY spinner"""

    def __init__(self, message, output=None):
        """初始化轻量 spinner

        Args:
            message (str): 初始状态文案
            output: 输出流，默认使用 sys.stdout
        """
        self._message = redact_text(message)
        self._output = output or sys.stdout
        self._frames = _SPINNER_FRAMES
        self._frame_index = 0
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._closed = False

    def start(self):
        """启动 spinner，隐藏光标并输出首行"""
        self._output.write(_HIDE_CURSOR)
        with self._lock:
            self._write_full_line()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        """后台刷新 spinner 帧"""
        while not self._stop_event.wait(_SPINNER_INTERVAL / 1000):
            self._render_next_frame()

    def _render_next_frame(self):
        """只刷新当前行首的 spinner 帧字符（黄色）"""
        with self._lock:
            if self._closed:
                return
            self._frame_index = (self._frame_index + 1) % len(self._frames)
            self._output.write(
                "\r" + colorama.Fore.YELLOW + self._frames[self._frame_index]
                + colorama.Style.RESET_ALL
            )
            self._output.flush()

    def _write_full_line(self):
        """写入完整 spinner 行（帧字符黄色，文案黄色）"""
        self._output.write(
            "\r"
            + colorama.Fore.YELLOW + self._frames[self._frame_index]
            + colorama.Style.RESET_ALL + "  "
            + colorama.Fore.YELLOW + self._message
            + colorama.Style.RESET_ALL
        )
        self._output.flush()

    def set_text(self, message):
        """更新文案并重绘完整 spinner 行

        Args:
            message (str): 新文案
        """
        with self._lock:
            if self._closed:
                return
            self._message = redact_text(message)
            self._output.write(_CLEAR_LINE)
            self._write_full_line()

    def write(self, message):
        """清理 spinner 行，输出插入文本，再恢复 spinner 行

        Args:
            message (str): 插入输出内容
        """
        with self._lock:
            if self._closed:
                return
            self._output.write(_CLEAR_LINE + message + "\n")
            self._write_full_line()

    def done(self, message):
        """停止 spinner 并输出定格成功行

        Args:
            message (str): 已格式化的成功文案
        """
        self.stop(clear_line=True)
        self._output.write(message + "\n")
        self._output.flush()

    def fail(self, message):
        """停止 spinner 并输出定格失败行

        Args:
            message (str): 已格式化的失败文案
        """
        self.stop(clear_line=True)
        self._output.write(message + "\n")
        self._output.flush()

    def stop(self, clear_line=True):
        """停止后台线程

        Args:
            clear_line (bool): 是否清理当前行
        """
        if self._closed:
            return
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        with self._lock:
            self._closed = True
            if clear_line:
                self._output.write(_CLEAR_LINE)
            self._output.write(_SHOW_CURSOR)
            self._output.flush()

    @property
    def closed(self):
        """spinner 是否已停止。"""
        return self._closed


def _format_done(message):
    """构造绿色 ✔️ 前缀的收尾文案（图标后两空格，与 ❌ 行对齐）

    图标后空格及颜色属于既有输出合同，不与普通流/上传终态格式强行统一。

    Args:
        message (str): 收尾文案

    Returns:
        str: 包裹 Fore.GREEN、含 ✔️ 前缀的完整文案
    """
    return colorama.Fore.GREEN + f"{_ICON_DONE}  {message}" + colorama.Style.RESET_ALL


def _format_fail(message):
    """构造红色 ❌ 前缀的收尾文案（图标后空格，与 ✔️ 行对齐）

    图标后空格及颜色属于既有输出合同，不与普通流/上传终态格式强行统一。

    Args:
        message (str): 收尾文案

    Returns:
        str: 包裹 Fore.RED、含 ❌ 前缀的完整文案
    """
    return colorama.Fore.RED + f"{_ICON_FAIL} {message}" + colorama.Style.RESET_ALL


class _SpinnerWriteHandler(logging.Handler):
    """临时日志处理器：将 CRITICAL 日志经 spinner.write() 干净打印

    spinner 旋转期间，控台原 handler 被提级到 CRITICAL+1 彻底静音，
    CRITICAL 日志改由本处理器接管，借 yaspin.write() 实现
    停转→清行→换行打印→重启旋转，避免日志黏在 spinner 行尾
    """

    def __init__(self, spinner):
        """记录底层 spinner 引用，并将级别固定为 CRITICAL

        Args:
            spinner: yaspin spinner 实例
        """
        super().__init__(level=logging.CRITICAL)
        self._spinner = spinner

    def emit(self, record):
        """以红色 ❌ 前缀干净打印 CRITICAL 消息（无 levelname/时间前缀）

        Args:
            record (logging.LogRecord): 日志记录
        """
        try:
            message = redact_text(record.getMessage())
        except Exception:
            message = FORMAT_FAILURE
        try:
            self._spinner.write(_format_fail(message))
        except Exception:
            # 交给静默的 handleError，避免 logging 默认实现把原始记录写到 stderr
            self.handleError(record)

    def handleError(self, record):
        """静默处理写入异常，绝不复现原始 Message/Arguments。"""
        pass


def _find_console_handler():
    """定位根 logger 的控制台处理器

    RotatingFileHandler 是 FileHandler 子类，需排除以定位控制台处理器

    Returns:
        logging.Handler | None: 控制台处理器，不存在时返回 None
    """
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and \
                not isinstance(handler, logging.FileHandler):
            return handler
    return None


class _TtySpinner:
    """TTY 环境下的 spinner 句柄，包装轻量 spinner 实例"""

    def __init__(self, spinner):
        """记录底层轻量 spinner 实例

        Args:
            spinner: _SimpleTTYSpinner 实例
        """
        self._spinner = spinner
        self._closed = False
        self._owner_thread_id = threading.get_ident()

    @property
    def message(self) -> str:
        """当前 spinner 行的公开文案。"""
        return self._spinner._message

    @property
    def stream(self) -> TextIO:
        """该句柄实际写入的输出流。"""
        return self._spinner._output

    @property
    def owner_thread_id(self) -> int:
        """建立句柄的线程身份，Context 复制不会转移到其他线程。"""
        return self._owner_thread_id

    @property
    def closed(self) -> bool:
        """句柄自身或底层 spinner 是否已关闭。"""
        return self._closed or self._spinner.closed is True

    def text(self, message):
        """更新 spinner 行内文案（黄色）

        Args:
            message (str): 新文案
        """
        if self._closed:
            return
        self._spinner.set_text(message)

    def done(self, message):
        """以绿色成功图标 ✔️ 定格当前行

        Args:
            message (str): 收尾文案
        """
        if self._closed:
            return
        self._spinner.done(_format_done(redact_text(message)))
        self._closed = True

    def fail(self, message):
        """以红色失败图标 ❌ 定格当前行

        Args:
            message (str): 收尾文案
        """
        if self._closed:
            return
        self._spinner.fail(_format_fail(redact_text(message)))
        self._closed = True

    def write(self, message):
        """插入打印文本后恢复 spinner 行

        Args:
            message (str): 要打印的文本
        """
        if self._closed:
            return
        self._spinner.write(redact_text(message))

    def write_done(self, message):
        """插入打印成功文本后恢复 spinner 行

        Args:
            message (str): 收尾文案
        """
        if self._closed:
            return
        self._spinner.write(_format_done(redact_text(message)))

    def write_fail(self, message):
        """插入打印失败文本后恢复 spinner 行

        Args:
            message (str): 失败文案
        """
        if self._closed:
            return
        self._spinner.write(_format_fail(redact_text(message)))


class _LogSpinner:
    """非 TTY 环境下的降级句柄，所有反馈写入日志"""

    def text(self, message):
        """以 INFO 级别记录文案

        Args:
            message (str): 文案
        """
        LOGGER.info(message)

    def done(self, message):
        """记录成功收尾文案

        Args:
            message (str): 收尾文案
        """
        LOGGER.info(f"{_ICON_DONE}  {message}")

    def fail(self, message):
        """记录失败收尾文案

        Args:
            message (str): 收尾文案
        """
        LOGGER.error(f"{_ICON_FAIL}  {message}")

    def write(self, message):
        """以 INFO 级别记录文本

        Args:
            message (str): 要记录的文本
        """
        LOGGER.info(message)

    def write_done(self, message):
        """记录一条成功收尾信息（不定格）

        Args:
            message (str): 收尾文案
        """
        LOGGER.info(f"{_ICON_DONE}  {message}")

    def write_fail(self, message):
        """记录一条失败信息（不定格）

        Args:
            message (str): 失败文案
        """
        LOGGER.error(f"{_ICON_FAIL}  {message}")


@contextmanager
def spinner_phase(text, *, stream=None, report_exit=True):
    """spinner 阶段上下文管理器

    TTY 环境下显示旋转动画并静音控台日志；非 TTY 环境降级为逐行日志输出；
    无论正常结束还是异常，都会停止 spinner、移除临时 CRITICAL 改道处理器并还原控台日志级别。
    显式传入 stream 时，isatty 判定与实际写入均使用该流；report_exit 为假时不再输出退出提示，
    供上传自有阶段静默收尾。启动也纳入资源 try/finally，每项清理独立执行，
    普通清理异常不会覆盖在途的 KeyboardInterrupt / SystemExit。

    Args:
        text (str): spinner 初始文案
        stream (TextIO | None): 显式输出流，缺省使用 sys.stdout
        report_exit (bool): 异常退出时是否输出既有退出提示，默认 True

    Yields:
        _TtySpinner | _LogSpinner: 提供 text/done/fail 的句柄
    """
    target = sys.stdout if stream is None else stream
    # 非 TTY：降级为日志，不触碰 spinner 与控台级别
    if not target.isatty():
        LOGGER.info(text)
        yield _LogSpinner()
        return

    console_handler = _find_console_handler()
    saved_level = console_handler.level if console_handler else None
    # 提级到 CRITICAL+1（51），连 CRITICAL 也不再经原 handler 直接穿透
    if console_handler is not None:
        console_handler.setLevel(logging.CRITICAL + 1)

    spinner = _SimpleTTYSpinner(text, output=target)
    handle = _TtySpinner(spinner)
    # 临时挂载 CRITICAL 改道处理器，借 spinner.write() 干净打印
    root_logger = logging.getLogger()
    write_handler = _SpinnerWriteHandler(spinner)
    handler_mounted = False
    current_token = None
    try:
        root_logger.addHandler(write_handler)
        handler_mounted = True
        current_token = _CURRENT_SPINNER.set(handle)
        spinner.start()
        try:
            yield handle
        except SystemExit as e:
            if report_exit and not handle.closed:
                if e.code == 0:
                    handle.done("程序已退出")
                else:
                    handle.fail(f"程序异常退出 (code {e.code})")
            raise
        except BaseException:
            if report_exit and not handle.closed:
                handle.fail("程序已退出")
            raise
    finally:
        if current_token is not None:
            try:
                _CURRENT_SPINNER.reset(current_token)
            except Exception:
                pass
        if handler_mounted:
            try:
                root_logger.removeHandler(write_handler)
            except Exception:
                pass
        try:
            spinner.stop(clear_line=False)
        except Exception:
            pass
        if console_handler is not None:
            try:
                console_handler.setLevel(saved_level)
            except Exception:
                pass


@contextmanager
def temporary_text(handle, text):
    """临时替换句柄文案并在退出时恢复公开状态

    保存进入前的公开文案，设置临时文案；嵌套使用严格后进先出恢复。
    只读写句柄的公开状态，绝不调用 done/fail/stop 关闭被借用的句柄。

    Args:
        handle (_TtySpinner): 可借用文案状态的 TTY 句柄
        text (str): 作用域内使用的临时文案

    Yields:
        _TtySpinner: 传入的句柄
    """
    saved = handle.message
    handle.text(text)
    try:
        yield handle
    finally:
        if not handle.closed:
            handle.text(saved)


def current_spinner():
    """返回本线程中未关闭的 TTY spinner 句柄，否则为空。

    复制的 Context 不代表授予 UI 写权限，因此仅返回建立句柄的同线程实例。

    Returns:
        _TtySpinner | None: 供入口复用外层实例的安全写入句柄。
    """
    handle = _CURRENT_SPINNER.get()
    if handle is None or handle.closed:
        return None
    if handle.owner_thread_id != threading.get_ident():
        return None
    return handle


def notify_fail(message, exc_info=False):
    """在 spinner 块外输出一条干净的红色 ❌ 失败行

    用于致命终止、Ctrl+C 取消、程序异常等不在 spinner 上下文内的失败场景
    全量信息（含 exc_info traceback）以 CRITICAL 级别写入日志文件，
    控台仅呈现一条干净的 ❌ 双空格行（无 levelname/时间前缀），
    与 spinner 的定格风格保持一致；非 TTY 环境降级为 LOGGER 输出

    Args:
        message (str): 失败文案
        exc_info (bool): 是否在日志文件中附带异常 traceback，默认 False
    """
    # 非 TTY：交由日志系统（文件全量，含 traceback）
    if not sys.stdout.isatty():
        LOGGER.critical(f"{_ICON_FAIL}  {message}", exc_info=exc_info)
        return

    # TTY：先把完整记录（含 traceback）写入文件，控台静音以免重复
    console_handler = _find_console_handler()
    saved_level = console_handler.level if console_handler else None
    if console_handler is not None:
        console_handler.setLevel(logging.CRITICAL + 1)
    try:
        LOGGER.critical(message, exc_info=exc_info)
    finally:
        if console_handler is not None:
            console_handler.setLevel(saved_level)
    colorama.init(autoreset=True)
    print(_format_fail(redact_text(message)))
