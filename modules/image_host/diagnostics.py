#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""提供显式开启、仅在内存中处理的安全诊断文本与上下文。"""

from contextlib import contextmanager
from contextvars import ContextVar
import re
import unicodedata


_INPUT_LIMIT = 4096
_OUTPUT_LIMIT = 200
_REDACTED = '[已隐藏]'
_CURRENT_SECRETS = ContextVar('image_host_diagnostic_secrets', default=None)
_UNSAFE_CONTENT = re.compile(
    r'<|>|&(?:lt|gt|#0*60|#x0*3c);'
    r'|(?a:\b(?:authorization|proxy-authorization|bearer)\b)'
    r'|(?a:\b(?:token|access_token|refresh_token|api[_-]?key|secret|password'
    r'|passwd|cookie|set-cookie)\b)[\"\']?\s*[:=]'
    r'|-----BEGIN [A-Z ]*PRIVATE KEY-----'
    r'|(?a:\b)eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',
    re.IGNORECASE,
)
_URL = re.compile(r'https?://[^\s<>\"\']*', re.IGNORECASE)
_ENV_REFERENCE = re.compile(
    r'\$\{[^}]*\}|\$(?:env:)?[A-Za-z_][A-Za-z0-9_]*'
    r'|%[A-Za-z_][A-Za-z0-9_]*%',
    re.IGNORECASE,
)
_OSC = re.compile(r'(?:\x1b\]|\x9d).*?(?:\x07|\x1b\\|\x9c)', re.DOTALL)
_CONTROL_STRING = re.compile(
    r'(?:\x1b[PX^_]|[\x90\x98\x9e\x9f]).*?(?:\x1b\\|\x9c)',
    re.DOTALL,
)
_CSI = re.compile(r'(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]')
_OTHER_ESCAPE = re.compile(r'\x1b(?![\[\]PX^_])[ -/]*[@-~]')
_UNFINISHED_ESCAPE = re.compile(r'[\x1b\x90\x98\x9b\x9d\x9e\x9f]')
_CONTROL_CATEGORIES = frozenset({'Cc', 'Cf', 'Cs'})


def _secret_snapshot(secrets):
    """复制非空字符串秘密，按长度降序排列且不改变调用方容器。"""
    if secrets is None:
        return ()
    if type(secrets) is str:
        secrets = (secrets,)
    return tuple(sorted(
        (secret for secret in secrets if type(secret) is str and secret),
        key=len, reverse=True,
    ))


def _replace_secrets(message, secrets):
    """先替换长秘密，避免包含关系使较长凭证留下后缀。"""
    for secret in secrets:
        message = message.replace(secret, _REDACTED)
    return message


def _clean_controls(message):
    """移除完整终端指令和控制字符，舍弃无法可靠解析的残留。"""
    for pattern in (_OSC, _CONTROL_STRING, _CSI, _OTHER_ESCAPE):
        message = pattern.sub('', message)
    if _UNFINISHED_ESCAPE.search(message) is not None:
        return None
    return ''.join(
        char for char in message
        if unicodedata.category(char) not in _CONTROL_CATEGORIES
    )


def sanitize_message(message, secrets) -> str | None:
    """先脱敏和清理再截断，不推测未知秘密或任意编码。

    调用方须提供整条故障转移链的令牌；本函数不读取配置或环境。
    页面和明显凭证结构整体舍弃，正常中文诊断尽可能保留。
    """
    if type(message) is not str or len(message) > _INPUT_LIMIT:
        return None
    if _UNSAFE_CONTENT.search(_URL.sub(_REDACTED, message)) is not None:
        return None
    known_secrets = _secret_snapshot(secrets)
    cleaned = _clean_controls(message)
    if cleaned is None:
        return None
    known_secrets = _secret_snapshot(
        _clean_controls(secret) for secret in known_secrets)
    cleaned = _replace_secrets(cleaned, known_secrets)
    cleaned = _URL.sub(_REDACTED, cleaned)
    if _UNSAFE_CONTENT.search(cleaned) is not None:
        return None
    cleaned = _ENV_REFERENCE.sub(_REDACTED, cleaned).strip()
    if (not cleaned or '${' in cleaned
            or _UNSAFE_CONTENT.search(cleaned) is not None
            or _URL.search(cleaned) is not None
            or any(secret in cleaned for secret in known_secrets)):
        return None
    return cleaned[:_OUTPUT_LIMIT]


@contextmanager
def diagnostic_scope(secrets):
    """临时设置秘密快照，区分关闭与空集合开启并可靠恢复外层。"""
    snapshot = None if secrets is None else _secret_snapshot(secrets)
    token = _CURRENT_SECRETS.set(snapshot)
    try:
        yield
    finally:
        _CURRENT_SECRETS.reset(token)


def safe_current_message(message) -> str | None:
    """仅在当前上下文显式开启时脱敏，不在上下文中保存原文。"""
    secrets = _CURRENT_SECRETS.get()
    if secrets is None:
        return None
    return sanitize_message(message, secrets)
