#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import io
import logging
import os
from importlib import import_module
from types import SimpleNamespace

import pytest

from modules import log_safety
from modules import logger as logger_mod
from modules import spinner as spinner_mod


pytest_plugins = ('notification_diagnostics_support',)

SYNTHETIC_SECRET = 'SYNTHETIC_SECRET_TOKEN_9317'
SYNTHETIC_URL = 'https://example.invalid/result'
SYNTHETIC_EVENT = 'safe-event-once'


def _record(level=logging.INFO):
    return logging.LogRecord(
        name='diagnostics',
        level=level,
        pathname=__file__,
        lineno=1,
        msg=f'{SYNTHETIC_EVENT} 携带 {SYNTHETIC_SECRET} 与 {SYNTHETIC_URL}',
        args=(),
        exc_info=None,
    )


def _emit_file(handler, record):
    before = dict(record.__dict__)
    with log_safety.safety_scope((SYNTHETIC_SECRET,)):
        handler.handle(record)
    assert record.__dict__ == before
    path = handler.baseFilename
    handler.close()
    with open(path, 'rb') as stream:
        return stream.read()


def _emit_stream(handler, stream, record):
    before = dict(record.__dict__)
    with log_safety.safety_scope((SYNTHETIC_SECRET,)):
        handler.handle(record)
    assert record.__dict__ == before
    return stream.getvalue()


def _emit_spinner(record):
    written = []
    handler = spinner_mod._SpinnerWriteHandler(SimpleNamespace(write=written.append))
    before = dict(record.__dict__)
    with log_safety.safety_scope((SYNTHETIC_SECRET,)):
        handler.emit(record)
    assert record.__dict__ == before
    return ''.join(written)


@pytest.mark.parametrize('sink', ['startup', 'running', 'reopen', 'temp'])
def test_startup_reopen_and_spinner_are_safe(sink, tmp_path, capsys):
    record = _record()
    if sink == 'startup':
        handler = logger_mod._create_file_handler(str(tmp_path), 'app')
        assert isinstance(handler, log_safety.SafeRotatingFileHandler)
        diagnostic_text = _emit_file(handler, record).decode('utf-8')
    elif sink == 'reopen':
        handler = logger_mod._reopen_file_handler(
            os.path.join(str(tmp_path), 'reopen.log'), 0)
        assert isinstance(handler, log_safety.SafeRotatingFileHandler)
        diagnostic_text = _emit_file(handler, record).decode('utf-8')
    elif sink == 'running':
        stream = io.StringIO()
        handler = log_safety.SafeStreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logger_mod.ColoredConsoleFormatter(
            logger_mod._LOG_CONSOLE_FORMAT,
            datefmt=logger_mod._LOG_CONSOLE_DATEFMT))
        diagnostic_text = _emit_stream(handler, stream, record)
    else:
        diagnostic_text = _emit_spinner(record)

    stderr = capsys.readouterr().err
    assert SYNTHETIC_SECRET not in diagnostic_text + stderr
    assert SYNTHETIC_URL not in diagnostic_text
    assert log_safety.LINK_REDACTED in diagnostic_text
    assert diagnostic_text.count(SYNTHETIC_EVENT) == 1


def test_handle_error_does_not_dump_record(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(logging, 'raiseExceptions', True)
    record = _record()

    stream = io.StringIO()
    stream_handler = log_safety.SafeStreamHandler(stream)
    stream_handler.format = lambda _record: (_ for _ in ()).throw(OSError('emit'))
    with log_safety.safety_scope((SYNTHETIC_SECRET,)):
        stream_handler.emit(record)

    file_handler = log_safety.SafeRotatingFileHandler(
        os.path.join(str(tmp_path), 'io-error.log'), encoding='utf-8')
    file_handler.stream.close()
    with log_safety.safety_scope((SYNTHETIC_SECRET,)):
        file_handler.emit(record)

    captured = capsys.readouterr()
    assert captured.err == ''
    assert captured.out == ''
    assert stream.getvalue() == ''
    assert SYNTHETIC_SECRET not in captured.err


@pytest.mark.parametrize('propagate', [False, True])
def test_dependency_handlers_are_controlled(propagate, capsys):
    stream = io.StringIO()
    owner = logging.getLogger('synthetic_dependency')
    child = logging.getLogger('synthetic_dependency.transport')
    owner.handlers = []
    child.handlers = []
    owner.setLevel(logging.DEBUG)
    child.setLevel(logging.DEBUG)
    owner.propagate = propagate
    own_handler = logging.StreamHandler(stream)
    own_handler.setLevel(logging.DEBUG)
    owner.addHandler(own_handler)
    extra_handler = None
    try:
        with log_safety.guard_dependency_logging(('synthetic_dependency',)):
            extra_handler = logging.NullHandler()
            owner.addHandler(extra_handler)
            with log_safety.safety_scope((SYNTHETIC_SECRET,)):
                owner.error('依赖失败 %s', SYNTHETIC_SECRET)
                child.error('子通道 %s', SYNTHETIC_SECRET)
        assert stream.getvalue() == ''
        assert extra_handler in owner.handlers
        assert SYNTHETIC_SECRET not in capsys.readouterr().err
        owner.error('恢复后仍可输出')
        assert '恢复后仍可输出' in stream.getvalue()
    finally:
        owner.handlers = []
        child.handlers = []
        owner.setLevel(logging.NOTSET)
        child.setLevel(logging.NOTSET)
        owner.propagate = True
        own_handler.close()


def test_urllib3_debug_output_is_suppressed():
    stream = io.StringIO()
    dependency = logging.getLogger('urllib3')
    saved_handlers = dependency.handlers[:]
    saved_level = dependency.level
    saved_propagate = dependency.propagate
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    dependency.handlers = [handler]
    dependency.setLevel(logging.DEBUG)
    dependency.propagate = False
    try:
        with log_safety.guard_dependency_logging(('urllib3',)):
            dependency.debug('wire 转储 %s', SYNTHETIC_SECRET)
        assert stream.getvalue() == ''
    finally:
        dependency.handlers = saved_handlers
        dependency.setLevel(saved_level)
        dependency.propagate = saved_propagate
        handler.close()


def test_cli_result_is_not_diagnostic(monkeypatch, capsys):
    cli = import_module('modules.screenshot.cli')
    prepared = {'provider': 'catbox', 'token': 'SYNTHETIC_TOKEN', 'options': {}}
    monkeypatch.setattr(cli, 'prepare_cli_host', lambda host: (prepared, None))
    monkeypatch.setattr(
        cli, 'upload_with_fallback',
        lambda *args, **kwargs: SimpleNamespace(success=True, url=SYNTHETIC_URL))

    stream = io.StringIO()
    handler = log_safety.SafeStreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(log_safety.SafeFormatter('%(message)s'))
    logger = logging.getLogger('cli-diagnostic-synthetic')
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        with log_safety.safety_scope((SYNTHETIC_SECRET,)):
            logger.info('诊断：上传结果链接 %s', SYNTHETIC_URL)
            outcome = cli._upload_all(
                b'data', 'shot.png', [{'provider': 'catbox'}])
    finally:
        logger.handlers = []
        logger.setLevel(logging.NOTSET)
        handler.close()

    stdout = capsys.readouterr().out
    assert outcome is True
    assert stdout == f'catbox：上传成功 {SYNTHETIC_URL}\n'
    diagnostic_text = stream.getvalue()
    assert SYNTHETIC_URL not in diagnostic_text
    assert log_safety.LINK_REDACTED in diagnostic_text
