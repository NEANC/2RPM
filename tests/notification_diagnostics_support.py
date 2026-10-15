#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import io
import logging
import socket

import pytest

from modules import log_safety


PLUGIN_NAME = 'notification_diagnostics_support'

SYNTHETIC_SECRET = 'SYNTHETIC_SECRET_TOKEN_9317'
SYNTHETIC_URL = 'https://example.invalid/signed?a=1'
SYNTHETIC_MESSAGE = (
    '标题: 截图上传通知 — ' + SYNTHETIC_SECRET + '\n'
    '\n'
    '正文：本次合成日志用于验证完整脱敏之后的模板文本，仍然保留普通换行、'
    'Markdown 结构、缩进以及常规中文标点；同时把数字 12345 与占位词 '
    'placeholder 原样保留下来，不因脱敏而被截断或改写，也不改写为错误类别。'
    '这里再补充一段用于确保脱敏结果明显超过两百个字符的说明文字，长度足够。\n'
    '\n'
    '![screenshot_1](' + SYNTHETIC_URL + ')\n'
    '\n'
    '尾部：缩进与换行保持不变，普通句号与中文标点继续可见。'
)


def _declares_plugin(module):
    declared = getattr(module, 'pytest_plugins', ()) if module is not None else ()
    if isinstance(declared, str):
        declared = (declared,)
    return PLUGIN_NAME in declared


@pytest.fixture(autouse=True)
def forbid_real_external_io(request, monkeypatch):
    if not _declares_plugin(getattr(request, 'module', None)):
        yield
        return

    def blocked(*args, **kwargs):
        raise AssertionError('测试禁止真实外部 IO 调用')

    import requests.sessions

    monkeypatch.setattr(requests.sessions.Session, 'send', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    yield


@pytest.fixture
def record():
    return logging.LogRecord(
        name='notification',
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=SYNTHETIC_MESSAGE,
        args=(),
        exc_info=None,
    )


@pytest.fixture
def original(record):
    return dict(record.__dict__)


@pytest.fixture
def formatted(record):
    with log_safety.safety_scope((SYNTHETIC_SECRET,)):
        formatter = log_safety.SafeFormatter('%(message)s')
        return formatter.format(record)


@pytest.fixture
def record_with_bad_args():
    return logging.LogRecord(
        name='notification',
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg='上传失败 %d',
        args=('不是整数',),
        exc_info=None,
    )


@pytest.fixture
def synthetic_log_stream():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(log_safety.SafeFormatter('%(levelname)s | %(message)s'))
    logger = logging.getLogger('notification-diagnostics-synthetic')
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        yield logger, stream
    finally:
        logger.handlers = []
        logger.setLevel(logging.NOTSET)
        handler.close()
