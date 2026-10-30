#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import socket
import sys

import pytest
import requests

from modules import log_safety


pytest_plugins = ('notification_diagnostics_support',)

SECRET = 'SYNTHETIC_SECRET_TOKEN_9317'
URL = 'https://example.invalid/signed?a=1'


def test_full_redaction_preserves_text_and_source(record, original, formatted):
    assert record.__dict__ == original
    assert 'SYNTHETIC_SECRET' not in formatted
    assert 'https://example.invalid/signed?a=1' not in formatted
    assert '![screenshot_1]([链接已隐藏])' in formatted
    assert len(formatted) > 200


def test_format_failure_never_returns_raw(record_with_bad_args):
    assert log_safety.SafeFormatter().format(record_with_bad_args) == '日志内容无法安全呈现'


def test_real_external_io_guard_is_active():
    with pytest.raises(AssertionError):
        socket.create_connection(('127.0.0.1', 9), timeout=0.05)

    session = requests.Session()
    with pytest.raises(AssertionError):
        session.send(requests.Request('GET', 'http://127.0.0.1:9/').prepare())


def test_short_secret_and_encoded_forms():
    assert log_safety.redact_text('前Z后', ('Z',)) == '前[已隐藏]后'

    secret = 'a b"c\\d'
    forms = (
        secret,
        'a b\\"c\\\\d',
        'a b"c\\\\d',
        'a%20b%22c%5Cd',
    )
    for form in forms:
        text = '值 ' + form + ' 结束'
        assert log_safety.redact_text(text, (secret,)) == '值 [已隐藏] 结束'

    mixed = "a'b\"c"
    for form in (mixed, 'a\\\'b"c'):
        text = '值 ' + form + ' 结束'
        assert log_safety.redact_text(text, (mixed,)) == '值 [已隐藏] 结束'


def test_longer_secret_wins_and_fixed_marker_survives():
    short = 'FAKE_PREFIX'
    long = 'FAKE_PREFIX_PRIVATE_SUFFIX'
    result = log_safety.redact_text('值 ' + long, (short, long))
    assert result == '值 [已隐藏]'
    assert short not in result

    marked = log_safety.redact_text('甲 已 乙 SECRET 丙', ('已', 'SECRET'))
    assert marked == '甲 [已隐藏] 乙 [已隐藏] 丙'


def test_exception_cache_is_private():
    def raise_chained():
        try:
            raise KeyError('内层 ' + SECRET)
        except KeyError as exc:
            raise RuntimeError('外层 ' + SECRET) from exc

    try:
        raise_chained()
    except RuntimeError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        'notification', logging.ERROR, __file__, 1, '发送失败', (), exc_info)
    record.exc_text = 'STALE_EXC_TEXT ' + SECRET
    record.stack_info = 'in 合成栈帧 ' + SECRET
    before = dict(record.__dict__)

    with log_safety.safety_scope((SECRET,)):
        rendered = log_safety.SafeFormatter('%(message)s').format(record)

    assert record.__dict__ == before
    assert record.exc_text == 'STALE_EXC_TEXT ' + SECRET
    assert SECRET not in rendered
    assert 'STALE_EXC_TEXT' not in rendered
    assert 'KeyError' in rendered
    assert 'RuntimeError' in rendered


def test_formatter_can_omit_traceback():
    try:
        raise ValueError('合成异常')
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        'notification', logging.ERROR, __file__, 1, '失败', (), exc_info)
    with_traceback = log_safety.SafeFormatter('%(message)s').format(record)
    without_traceback = log_safety.SafeFormatter(
        '%(message)s', include_traceback=False).format(record)

    assert 'Traceback' in with_traceback
    assert 'Traceback' not in without_traceback


@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt, SystemExit])
def test_nested_scope_resets_after_exception(error_type):
    outer = ('OUTER_SECRET_TOKEN',)
    inner = ('INNER_SECRET_TOKEN',)

    assert log_safety.secret_snapshot() == ()
    with log_safety.safety_scope(outer):
        assert log_safety.secret_snapshot() == outer
        with pytest.raises(error_type):
            with log_safety.safety_scope(inner):
                assert set(log_safety.secret_snapshot()) == set(outer) | set(inner)
                raise error_type('合成异常')
        assert log_safety.secret_snapshot() == outer
        assert log_safety.redact_text(outer[0]) == '[已隐藏]'
    assert log_safety.secret_snapshot() == ()
    assert log_safety.redact_text(outer[0]) == outer[0]
    assert log_safety.redact_text(inner[0]) == inner[0]


def test_extend_secrets_only_changes_current_scope():
    log_safety.extend_secrets(('OUTSIDE_SECRET',))
    assert log_safety.secret_snapshot() == ()

    with log_safety.safety_scope(('OUTER_SECRET',)):
        log_safety.extend_secrets(('LATER_SECRET',))
        assert set(log_safety.secret_snapshot()) == {'OUTER_SECRET', 'LATER_SECRET'}
        assert log_safety.redact_text(
            'OUTER_SECRET 与 LATER_SECRET') == '[已隐藏] 与 [已隐藏]'
    assert log_safety.secret_snapshot() == ()
    assert log_safety.redact_text('LATER_SECRET') == 'LATER_SECRET'


def test_redact_text_defaults_to_current_snapshot():
    text = '令牌 ' + SECRET
    assert log_safety.redact_text(text) == text
    with log_safety.safety_scope((SECRET,)):
        assert log_safety.redact_text(text) == '令牌 [已隐藏]'
    assert log_safety.redact_text(text) == text
    assert log_safety.redact_text(text, (SECRET,)) == '令牌 [已隐藏]'


def test_plain_text_and_markdown_are_preserved():
    text = (
        '第一行\n第二行\n\n'
        '- 列表项\n**加粗** 与 `代码`\n\n'
        '![screenshot_1](' + URL + ')'
    )
    assert log_safety.redact_text(text) == (
        '第一行\n第二行\n\n'
        '- 列表项\n**加粗** 与 `代码`\n\n'
        '![screenshot_1]([链接已隐藏])'
    )


@pytest.mark.parametrize('payload, expected', [
    ('\x1b[31m危险文本\x1b[0m', '危险文本'),
    ('\x9b31m危险文本\x9b0m', '危险文本'),
    ('\x1b]0;隐藏标题\x07正文', '正文'),
    ('\x1b]8;;' + URL + '\x1b\\链接文本\x1b]8;;\x1b\\', '链接文本'),
    ('\x1b(B普通文本', '普通文本'),
    ('前\x00后\u200b文本', '前后文本'),
])
def test_terminal_sequences_are_removed(payload, expected):
    result = log_safety.redact_text(payload)
    assert result == expected
    assert '\x1b' not in result
    assert '\x07' not in result


@pytest.mark.parametrize('level', [
    logging.DEBUG,
    logging.INFO,
    logging.WARNING,
    logging.ERROR,
    logging.CRITICAL,
])
def test_all_levels_share_redaction(synthetic_log_stream, level):
    logger, stream = synthetic_log_stream
    with log_safety.safety_scope((SECRET,)):
        logger.log(level, '级别 %s 携带 %s 与 %s', level, SECRET, URL)

    output = stream.getvalue()
    assert logging.getLevelName(level) in output
    assert SECRET not in output
    assert URL not in output


@pytest.mark.parametrize('separator', ['\u200b', '\x1b[0m'])
def test_secret_split_by_controls_is_redacted_after_cleaning(separator):
    split = SECRET[:8] + separator + SECRET[8:]
    result = log_safety.redact_text('甲 ' + split + ' 乙 ' + URL, (SECRET,))
    assert SECRET not in result
    assert '甲' in result
    assert '乙' in result
    assert log_safety.REDACTED in result
    assert log_safety.LINK_REDACTED in result
