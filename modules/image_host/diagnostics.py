#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""提供显式开启、仅在内存中处理的安全诊断文本与上下文。"""

from contextlib import contextmanager
from contextvars import ContextVar
import re

from modules.log_safety import clean_controls, normalize_secrets, redact_secrets


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
# 检测视图仅留下这些可删除控制空白；只在凭证内部跨越它们。
_CONTROL_GAP = r'[\x09-\x0d\x1c-\x1f\x85]*'
_SPLIT_CREDENTIAL = re.compile(
    r'(?a:\b)(?:'
    + '|'.join(_CONTROL_GAP.join(map(re.escape, name)) for name in (
        'authorization', 'proxy-authorization', 'bearer',
    ))
    + r')(?a:\b)|(?a:\b)(?:'
    + '|'.join(_CONTROL_GAP.join(map(re.escape, name)) for name in (
        'token', 'access_token', 'refresh_token', 'apikey', 'api_key',
        'api-key', 'secret', 'password', 'passwd', 'cookie', 'set-cookie',
    ))
    + r')(?a:\b)' + _CONTROL_GAP + r'[\"\']?\s*[:=]'
    + r'|(?a:\b)e' + _CONTROL_GAP + 'y' + _CONTROL_GAP + 'J'
    + '(?:' + _CONTROL_GAP + r'[A-Za-z0-9_-])*'
    + _CONTROL_GAP + r'\.'
    + '(?:' + _CONTROL_GAP + r'[A-Za-z0-9_-])+'
    + _CONTROL_GAP + r'\.'
    + '(?:' + _CONTROL_GAP + r'[A-Za-z0-9_-])+',
    re.IGNORECASE,
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
    detection_view = clean_controls(message, preserve_whitespace=True)
    if detection_view is None:
        return None
    detection_view = _URL.sub(_REDACTED, detection_view)
    if (_UNSAFE_CONTENT.search(detection_view) is not None
            or _SPLIT_CREDENTIAL.search(detection_view) is not None):
        return None
    known_secrets = normalize_secrets(secrets)
    cleaned = clean_controls(message)
    if cleaned is None:
        return None
    known_secrets = normalize_secrets(
        clean_controls(secret) for secret in known_secrets)
    cleaned = redact_secrets(cleaned, known_secrets)
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
    snapshot = None if secrets is None else normalize_secrets(secrets)
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
