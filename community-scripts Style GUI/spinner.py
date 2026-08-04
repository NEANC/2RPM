#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
from contextlib import contextmanager

import colorama

LOGGER = logging.getLogger(__name__)
_ICON_DONE = "\u2714\ufe0f"
_ICON_FAIL = "\u274c"


def _format_done(message):
    return colorama.Fore.GREEN + f"{_ICON_DONE}  {message}" + colorama.Style.RESET_ALL


def _format_fail(message):
    return colorama.Fore.RED + f"{_ICON_FAIL}  {message}" + colorama.Style.RESET_ALL


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


@contextmanager
def spinner_phase(text):
    LOGGER.info(text)
    sp = _LogSpinner()
    try:
        yield sp
    except SystemExit as e:
        if e.code == 0:
            sp.done("程序已退出")
        else:
            sp.fail(f"程序异常退出 (code {e.code})")
        raise
    except BaseException:
        sp.fail("程序已退出")
        raise


def notify_fail(message, exc_info=False):
    LOGGER.critical(f"{_ICON_FAIL}  {message}", exc_info=exc_info)
