#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import threading
import logging
from contextlib import contextmanager

import colorama

LOGGER = logging.getLogger(__name__)

# 完成 / 失败 / 致命的定格图标（取自 pixi_alas_install.sh#L47-49）
_ICON_DONE = "\u2714\ufe0f"  # ✔️
_ICON_FAIL = "\u274c"        # ❌

# spinner 帧间隔（毫秒）；yaspin 默认约 80ms 会闪屏，放慢到 200ms
_SPINNER_INTERVAL_MS = 200

# spinner 帧列表
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
# 清行 ANSI 序列
_CLEAR_LINE = "\r\033[2K"
# 隐藏/显示光标 ANSI 序列
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"


def _make_yaspin(text):
    """创建一个 yaspin spinner 实例

    单独抽出便于测试替身注入；仅在 TTY 路径下被调用，
    因此 yaspin 仅在交互式环境真正导入使用内联 dots 帧，避免
    打包产物依赖 yaspin/data/spinners.json

    Args:
        text (str): spinner 初始文案（已包裹黄色）

    Returns:
        yaspin.Yaspin: 已配置 dots 动画的 spinner 实例
    """
    from yaspin import yaspin
    from yaspin.core import Spinner
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    spinner = Spinner(frames, _SPINNER_INTERVAL_MS)
    return yaspin(spinner, text=text, color="yellow")


class _SimpleTTYSpinner:
    """仅刷新行首帧字符的轻量 TTY spinner"""

    def __init__(self, message, output=None):
        """初始化轻量 spinner

        Args:
            message (str): 初始状态文案
            output: 输出流，默认使用 sys.stdout
        """
        self._message = message
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
        while not self._stop_event.wait(_SPINNER_INTERVAL_MS / 1000):
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
            self._message = message
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


def _wrap_running(message):
    """将旋转期间的文案包裹为黄色

    前导空格使渲染的「帧字符 + 单空格 + 文案」变为双空格，
    与 done/fail（图标后双空格）的对齐风格保持一致

    Args:
        message (str): 原始文案

    Returns:
        str: 前缀一个空格并包裹 Fore.YELLOW 的文案
    """
    return (" "
            + colorama.Fore.YELLOW
            + message + colorama.Style.RESET_ALL)


def _format_done(message):
    """构造绿色 ✔️ 前缀的收尾文案（图标后两空格，与 ❌ 行对齐）

    Args:
        message (str): 收尾文案

    Returns:
        str: 包裹 Fore.GREEN、含 ✔️ 前缀的完整文案
    """
    return colorama.Fore.GREEN + f"{_ICON_DONE}  {message}" + colorama.Style.RESET_ALL


def _format_fail(message):
    """构造红色 ❌ 前缀的收尾文案（图标后空格，与 ✔️ 行对齐）

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
        message = record.getMessage()
        self._spinner.write(_format_fail(message))


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
        self._spinner.done(_format_done(message))
        self._closed = True

    def fail(self, message):
        """以红色失败图标 ❌ 定格当前行

        Args:
            message (str): 收尾文案
        """
        if self._closed:
            return
        self._spinner.fail(_format_fail(message))
        self._closed = True

    def write(self, message):
        """插入打印文本后恢复 spinner 行

        Args:
            message (str): 要打印的文本
        """
        if self._closed:
            return
        self._spinner.write(message)

    def write_done(self, message):
        """插入打印成功文本后恢复 spinner 行

        Args:
            message (str): 收尾文案
        """
        if self._closed:
            return
        self._spinner.write(_format_done(message))

    def write_fail(self, message):
        """插入打印失败文本后恢复 spinner 行

        Args:
            message (str): 失败文案
        """
        if self._closed:
            return
        self._spinner.write(_format_fail(message))


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
def spinner_phase(text):
    """spinner 阶段上下文管理器

    TTY 环境下显示 yaspin 旋转动画并静音控台日志；非 TTY 环境降级为逐行日志输出；
    无论正常结束还是异常，都会停止 spinner、移除临时 CRITICAL 改道处理器并还原控台日志级别

    Args:
        text (str): spinner 初始文案

    Yields:
        _TtySpinner | _LogSpinner: 提供 text/done/fail 的句柄
    """
    # 非 TTY：降级为日志，不触碰 yaspin 与控台级别
    if not sys.stdout.isatty():
        LOGGER.info(text)
        yield _LogSpinner()
        return

    console_handler = _find_console_handler()
    saved_level = console_handler.level if console_handler else None
    # 提级到 CRITICAL+1（51），连 CRITICAL 也不再经原 handler 直接穿透
    if console_handler is not None:
        console_handler.setLevel(logging.CRITICAL + 1)

    spinner = _SimpleTTYSpinner(text)
    spinner.start()
    handle = _TtySpinner(spinner)
    # 临时挂载 CRITICAL 改道处理器，借 spinner.write() 干净打印
    root_logger = logging.getLogger()
    write_handler = _SpinnerWriteHandler(spinner)
    root_logger.addHandler(write_handler)
    try:
        yield handle
    except BaseException:
        if not handle._closed:
            handle.fail("程序已退出")
        raise
    finally:
        root_logger.removeHandler(write_handler)
        spinner.stop(clear_line=False)
        if console_handler is not None:
            console_handler.setLevel(saved_level)


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
    print(_format_fail(message))
