#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import sys
import threading
from contextlib import contextmanager

import colorama

LOGGER = logging.getLogger(__name__)
_ICON_DONE = "\u2714\ufe0f"
_ICON_FAIL = "\u274c"
_SPINNER_INTERVAL = 200
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_CLEAR_LINE = "\r\033[2K"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"


def _format_done(message):
    return colorama.Fore.GREEN + f"{_ICON_DONE}  {message}" + colorama.Style.RESET_ALL


def _format_fail(message):
    return colorama.Fore.RED + f"{_ICON_FAIL} {message}" + colorama.Style.RESET_ALL


class _SimpleTTYSpinner:
    def __init__(self, message, output=None):
        self._message = message
        self._output = output or sys.stdout
        self._frames = _SPINNER_FRAMES
        self._frame_index = 0
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._closed = False

    def start(self):
        self._output.write(_HIDE_CURSOR)
        with self._lock:
            self._write_full_line()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop_event.wait(_SPINNER_INTERVAL / 1000):
            self._render_next_frame()

    def _render_next_frame(self):
        with self._lock:
            if self._closed:
                return
            self._frame_index = (self._frame_index + 1) % len(self._frames)
            self._output.write(
                "\r"
                + colorama.Fore.YELLOW + self._frames[self._frame_index]
                + colorama.Style.RESET_ALL
            )
            self._output.flush()

    def _write_full_line(self):
        self._output.write(
            "\r"
            + colorama.Fore.YELLOW + self._frames[self._frame_index]
            + colorama.Style.RESET_ALL + "  "
            + colorama.Fore.YELLOW + self._message
            + colorama.Style.RESET_ALL
        )
        self._output.flush()

    def set_text(self, message):
        with self._lock:
            if self._closed:
                return
            self._message = message
            self._output.write(_CLEAR_LINE)
            self._write_full_line()

    def write(self, message):
        with self._lock:
            if self._closed:
                return
            self._output.write(_CLEAR_LINE + message + "\n")
            self._write_full_line()

    def done(self, message):
        self.stop(clear_line=True)
        self._output.write(message + "\n")
        self._output.flush()

    def fail(self, message):
        self.stop(clear_line=True)
        self._output.write(message + "\n")
        self._output.flush()

    def stop(self, clear_line=True):
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


class _TtySpinner:
    def __init__(self, spinner):
        self._spinner = spinner
        self._closed = False

    def text(self, message):
        if not self._closed:
            self._spinner.set_text(message)

    def done(self, message):
        if not self._closed:
            self._spinner.done(_format_done(message))
            self._closed = True

    def fail(self, message):
        if not self._closed:
            self._spinner.fail(_format_fail(message))
            self._closed = True

    def write(self, message):
        if not self._closed:
            self._spinner.write(message)

    def write_done(self, message):
        if not self._closed:
            self._spinner.write(_format_done(message))

    def write_fail(self, message):
        if not self._closed:
            self._spinner.write(_format_fail(message))


class _LogSpinner:
    def text(self, message):
        LOGGER.info(message)

    def done(self, message):
        LOGGER.info(f"{_ICON_DONE}  {message}")

    def fail(self, message):
        LOGGER.error(f"{_ICON_FAIL}  {message}")

    def write(self, message):
        LOGGER.info(message)

    def write_done(self, message):
        LOGGER.info(f"{_ICON_DONE}  {message}")

    def write_fail(self, message):
        LOGGER.error(f"{_ICON_FAIL}  {message}")


class _SpinnerWriteHandler(logging.Handler):
    def __init__(self, spinner):
        super().__init__(level=logging.CRITICAL)
        self._spinner = spinner

    def emit(self, record):
        self._spinner.write(_format_fail(record.getMessage()))


def _find_console_handlers():
    handlers = []
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            handlers.append(handler)
    return handlers


def _finish_for_exception(handle, exc):
    if isinstance(exc, SystemExit):
        if exc.code == 0:
            handle.done("程序已退出")
        else:
            handle.fail(f"程序异常退出 (code {exc.code})")
    else:
        handle.fail("程序已退出")


@contextmanager
def spinner_phase(text):
    if not sys.stdout.isatty():
        LOGGER.info(text)
        handle = _LogSpinner()
        try:
            yield handle
        except BaseException as exc:
            _finish_for_exception(handle, exc)
            raise
        return

    colorama.just_fix_windows_console()
    console_handlers = _find_console_handlers()
    saved_levels = [(handler, handler.level) for handler in console_handlers]
    root_logger = logging.getLogger()
    spinner = None
    handle = None
    write_handler = None
    try:
        for handler in console_handlers:
            handler.setLevel(logging.CRITICAL + 1)
        spinner = _SimpleTTYSpinner(text)
        spinner.start()
        handle = _TtySpinner(spinner)
        write_handler = _SpinnerWriteHandler(spinner)
        root_logger.addHandler(write_handler)
        yield handle
    except BaseException as exc:
        if handle is not None and not handle._closed:
            _finish_for_exception(handle, exc)
        raise
    finally:
        if write_handler is not None:
            root_logger.removeHandler(write_handler)
        if spinner is not None:
            spinner.stop(clear_line=False)
        for handler, level in saved_levels:
            handler.setLevel(level)


def notify_fail(message, exc_info=False):
    if not sys.stdout.isatty():
        LOGGER.critical(f"{_ICON_FAIL}  {message}", exc_info=exc_info)
        return

    colorama.just_fix_windows_console()
    console_handlers = _find_console_handlers()
    saved_levels = [(handler, handler.level) for handler in console_handlers]
    for handler in console_handlers:
        handler.setLevel(logging.CRITICAL + 1)
    try:
        LOGGER.critical(message, exc_info=exc_info)
    finally:
        for handler, level in saved_levels:
            handler.setLevel(level)
    print(_format_fail(message))
