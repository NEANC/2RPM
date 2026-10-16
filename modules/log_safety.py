#!/usr/bin/env python3
# -_- coding: utf-8 -_-

from contextlib import contextmanager
from contextvars import ContextVar
import copy
import json
import logging
from logging.handlers import RotatingFileHandler
import re
import unicodedata
import urllib.parse


SecretSnapshot = tuple[str, ...]

REDACTED = '[已隐藏]'
LINK_REDACTED = '[链接已隐藏]'
FORMAT_FAILURE = '日志内容无法安全呈现'

_REDACTED_SENTINEL = '\x00\x01\x00'
_LINK_SENTINEL = '\x00\x02\x00'

_SECRETS = ContextVar('log_safety_secrets', default=None)

_OSC = re.compile(r'(?:\x1b\]|\x9d).*?(?:\x07|\x1b\\|\x9c)', re.DOTALL)
_CONTROL_STRING = re.compile(
    r'(?:\x1b[PX^_]|[\x90\x98\x9e\x9f]).*?(?:\x1b\\|\x9c)',
    re.DOTALL,
)
_CSI = re.compile(r'(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]')
_OTHER_ESCAPE = re.compile(r'\x1b(?![\[\]PX^_])[ -/]*[@-~]')
_UNFINISHED_ESCAPE = re.compile(r'[\x1b\x90\x98\x9b\x9d\x9e\x9f]')
_CONTROL_CATEGORIES = frozenset({'Cc', 'Cf', 'Cs'})
_TEXT_WHITESPACE = frozenset('\n\r\t')
_CREDENTIAL = re.compile(
    r'(?a:\b(?:authorization|proxy-authorization)\b)[\"\']?\s*[:=]\s*[^\r\n]+'
    r'|(?a:\b)bearer\b[\"\']?\s+[^\s,;\"\']+'
    r'|(?a:\b(?:token|access[_-]?token|refresh[_-]?token|api[_-]?key|apikey'
    r'|secret|client[_-]?secret|password|passwd|set-cookie|cookie)\b)'
    r'[\"\']?\s*[:=]\s*[\"\']?[^\s,;\"\']+',
    re.IGNORECASE,
)
_URL = re.compile(r"https?://[^\s<>\"'\x00-\x1f\x7f-\x9f]*", re.IGNORECASE)


def normalize_secrets(secrets):
    if secrets is None:
        return ()
    if type(secrets) is str:
        secrets = (secrets,)
    return tuple(sorted(
        (secret for secret in secrets if type(secret) is str and secret),
        key=len, reverse=True,
    ))


def secret_snapshot() -> SecretSnapshot:
    snapshot = _SECRETS.get()
    return () if snapshot is None else snapshot


def _merge_secrets(current, extra):
    merged = list(current)
    for secret in extra:
        if secret not in merged:
            merged.append(secret)
    return tuple(sorted(merged, key=len, reverse=True))


@contextmanager
def safety_scope(secrets: SecretSnapshot):
    token = _SECRETS.set(_merge_secrets(secret_snapshot(), normalize_secrets(secrets)))
    try:
        yield
    finally:
        _SECRETS.reset(token)


def extend_secrets(secrets: SecretSnapshot) -> None:
    current = _SECRETS.get()
    if current is None:
        return
    _SECRETS.set(_merge_secrets(current, normalize_secrets(secrets)))


def _encoded_forms(secret):
    return {
        secret,
        urllib.parse.quote(secret, safe=''),
        json.dumps(secret)[1:-1],
        repr(secret)[1:-1],
    }


def _secret_pattern(secrets):
    forms = set()
    for secret in secrets:
        forms.update(_encoded_forms(secret))
    ordered = sorted((form for form in forms if form), key=len, reverse=True)
    if not ordered:
        return None
    return re.compile('|'.join(re.escape(form) for form in ordered))


def redact_secrets(text, secrets):
    pattern = _secret_pattern(normalize_secrets(secrets))
    if pattern is None:
        return text
    return pattern.sub(REDACTED, text)


def remove_terminal_sequences(text):
    for pattern in (_OSC, _CONTROL_STRING, _CSI, _OTHER_ESCAPE):
        text = pattern.sub('', text)
    return text


def has_unfinished_escape(text):
    return _UNFINISHED_ESCAPE.search(text) is not None


def strip_control_chars(text, *, preserve_whitespace=False):
    return ''.join(
        char for char in text
        if unicodedata.category(char) not in _CONTROL_CATEGORIES
        or (preserve_whitespace and char.isspace())
    )


def strip_log_controls(text):
    return ''.join(
        char for char in text
        if char in _TEXT_WHITESPACE
        or unicodedata.category(char) not in _CONTROL_CATEGORIES
    )


def clean_controls(text, *, preserve_whitespace=False):
    text = remove_terminal_sequences(text)
    if has_unfinished_escape(text):
        return None
    return strip_control_chars(text, preserve_whitespace=preserve_whitespace)


def _redact_url(match):
    """隐藏完整链接，仅保留末尾多出的外层右括号。"""
    url = match.group()
    trailing = len(url) - len(url.rstrip(')'))
    outer = min(trailing, max(0, url.count(')') - url.count('(')))
    return LINK_REDACTED + ')' * outer


def redact_text(text: str, secrets: SecretSnapshot | None = None) -> str:
    snapshot = secret_snapshot() if secrets is None else secrets
    result = redact_secrets(text, snapshot)
    result = strip_log_controls(remove_terminal_sequences(result))
    result = _CREDENTIAL.sub(REDACTED, result)
    result = _URL.sub(_redact_url, result)
    guarded = result.replace(REDACTED, _REDACTED_SENTINEL).replace(
        LINK_REDACTED, _LINK_SENTINEL)
    guarded = redact_secrets(guarded, snapshot)
    return guarded.replace(_REDACTED_SENTINEL, REDACTED).replace(
        _LINK_SENTINEL, LINK_REDACTED)


class SafeFormatter(logging.Formatter):

    def __init__(self, fmt=None, datefmt=None, *, include_traceback=True):
        super().__init__(fmt, datefmt)
        self._include_traceback = include_traceback

    def format(self, record: logging.LogRecord) -> str:
        try:
            pending = copy.copy(record)
            pending.exc_text = None
            if not self._include_traceback:
                pending.exc_info = None
                pending.stack_info = None
            rendered = super().format(pending)
            return redact_text(rendered)
        except Exception:
            return FORMAT_FAILURE


class SafeStreamHandler(logging.StreamHandler):

    def handleError(self, record: logging.LogRecord) -> None:
        pass


class SafeRotatingFileHandler(RotatingFileHandler):

    def handleError(self, record: logging.LogRecord) -> None:
        pass


class _DependencyDropFilter(logging.Filter):

    def filter(self, record: logging.LogRecord) -> bool:
        return False


@contextmanager
def guard_dependency_logging(names: tuple[str, ...]):
    drop = _DependencyDropFilter()
    guarded = []
    for name in names:
        prefix = name + '.'
        for logger_name, logger in list(logging.Logger.manager.loggerDict.items()):
            if logger_name != name and not logger_name.startswith(prefix):
                continue
            if isinstance(logger, logging.Logger):
                logger.addFilter(drop)
                guarded.append(logger)
        direct = logging.getLogger(name)
        if direct not in guarded:
            direct.addFilter(drop)
            guarded.append(direct)
    try:
        yield
    finally:
        for logger in guarded:
            logger.removeFilter(drop)
