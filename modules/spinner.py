#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import logging
from contextlib import contextmanager

import colorama

LOGGER = logging.getLogger(__name__)

# 完成 / 失败 / 致命的定格图标（取自 pixi_alas_install.sh#L47-49）
_ICON_DONE = "\u2714\ufe0f"  # ✔️
_ICON_FAIL = "\u274c"        # ❌

# spinner 帧间隔（毫秒）。yaspin 默认约 80ms 会闪屏，
# 放慢到 200ms 对齐 pixi_alas_install.sh 的 sleep 0.20。
_SPINNER_INTERVAL_MS = 200


def _make_yaspin(text):
    """创建一个 yaspin spinner 实例。

    单独抽出便于测试替身注入；仅在 TTY 路径下被调用，
    因此 yaspin 仅在交互式环境真正导入。使用自定义 200ms
    帧间隔降低刷新频率，旋转帧着黄色，文案另由调用方包裹颜色。

    Args:
        text (str): spinner 初始文案（已包裹黄色）。

    Returns:
        yaspin.Yaspin: 已配置 dots 动画的 spinner 实例。
    """
    from yaspin import yaspin
    from yaspin.spinners import Spinners
    from yaspin.core import Spinner
    base = Spinners.dots
    slow = Spinner(base.frames, _SPINNER_INTERVAL_MS)
    return yaspin(slow, text=text, color="yellow")


def _wrap_running(message):
    """将旋转期间的文案包裹为黄色。

    Args:
        message (str): 原始文案。

    Returns:
        str: 包裹 Fore.YELLOW 的文案。
    """
    return colorama.Fore.YELLOW + message + colorama.Style.RESET_ALL


class _SpinnerWriteHandler(logging.Handler):
    """临时日志处理器：将 CRITICAL 日志经 spinner.write() 干净打印。

    spinner 旋转期间，控台原 handler 被提级到 CRITICAL+1 彻底静音，
    CRITICAL 日志改由本处理器接管，借 yaspin.write() 实现
    停转→清行→换行打印→重启旋转，避免日志黏在 spinner 行尾。
    """

    def __init__(self, spinner):
        """记录底层 spinner 引用，并将级别固定为 CRITICAL。

        Args:
            spinner: yaspin spinner 实例。
        """
        super().__init__(level=logging.CRITICAL)
        self._spinner = spinner

    def emit(self, record):
        """以红色 ❌ 前缀干净打印 CRITICAL 消息（无 levelname/时间前缀）。

        Args:
            record (logging.LogRecord): 日志记录。
        """
        message = record.getMessage()
        text = colorama.Fore.RED + f"{_ICON_FAIL} {message}" + colorama.Style.RESET_ALL
        self._spinner.write(text)


def _find_console_handler():
    """定位根 logger 的控制台处理器。

    RotatingFileHandler 是 FileHandler 子类，需排除以定位控制台处理器。

    Returns:
        logging.Handler | None: 控制台处理器，不存在时返回 None。
    """
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and \
                not isinstance(handler, logging.FileHandler):
            return handler
    return None


class _TtySpinner:
    """TTY 环境下的 spinner 句柄，包装 yaspin 实例。"""

    def __init__(self, spinner):
        """记录底层 yaspin 实例。

        Args:
            spinner: yaspin spinner 实例。
        """
        self._spinner = spinner
        self._closed = False

    def text(self, message):
        """更新 spinner 行内文案（黄色）。

        定格（done/fail）后再调用将被忽略，避免写入已停止的 spinner。

        Args:
            message (str): 新文案。
        """
        if self._closed:
            return
        self._spinner.text = _wrap_running(message)

    def done(self, message):
        """以绿色成功图标 ✔️ 定格当前行。

        yaspin 3.4.0 的 _compose_out 取 self._text（旋转期旧文案）
        而非 ok() 参数作为定格文案，需先清空避免残留。

        Args:
            message (str): 收尾文案。
        """
        if self._closed:
            return
        self._spinner.text = ""
        self._spinner.ok(
            colorama.Fore.GREEN + f"{_ICON_DONE} {message}"
            + colorama.Style.RESET_ALL
        )
        self._closed = True

    def fail(self, message):
        """以红色失败图标 ❌ 定格当前行。

        Args:
            message (str): 收尾文案。
        """
        if self._closed:
            return
        self._spinner.text = ""
        self._spinner.fail(
            colorama.Fore.RED + f"{_ICON_FAIL} {message}"
            + colorama.Style.RESET_ALL
        )
        self._closed = True

    def write(self, message):
        """停转→清行→换行打印文本→重启旋转，用于内联输出定格信息。

        Args:
            message (str): 要打印的文本。
        """
        if self._closed:
            return
        self._spinner.write(message)


class _LogSpinner:
    """非 TTY 环境下的降级句柄，所有反馈写入日志。"""

    def text(self, message):
        """以 INFO 级别记录文案。

        Args:
            message (str): 文案。
        """
        LOGGER.info(message)

    def done(self, message):
        """记录成功收尾文案。

        Args:
            message (str): 收尾文案。
        """
        LOGGER.info(f"{_ICON_DONE} {message}")

    def fail(self, message):
        """记录失败收尾文案。

        Args:
            message (str): 收尾文案。
        """
        LOGGER.info(f"{_ICON_FAIL} {message}")

    def write(self, message):
        """以 INFO 级别记录文本。

        Args:
            message (str): 要记录的文本。
        """
        LOGGER.info(message)


@contextmanager
def spinner_phase(text):
    """spinner 阶段上下文管理器。

    TTY 环境下显示 yaspin 旋转动画并静音控台日志；非 TTY 环境
    降级为逐行日志输出。无论正常结束还是异常，都会停止 spinner、
    移除临时 CRITICAL 改道处理器并还原控台日志级别。

    Args:
        text (str): spinner 初始文案。

    Yields:
        _TtySpinner | _LogSpinner: 提供 text/done/fail 的句柄。
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

    spinner = _make_yaspin(_wrap_running(text))
    spinner.start()
    handle = _TtySpinner(spinner)
    # 临时挂载 CRITICAL 改道处理器，借 spinner.write() 干净打印
    root_logger = logging.getLogger()
    write_handler = _SpinnerWriteHandler(spinner)
    root_logger.addHandler(write_handler)
    try:
        yield handle
    finally:
        root_logger.removeHandler(write_handler)
        spinner.stop()
        if console_handler is not None:
            console_handler.setLevel(saved_level)
