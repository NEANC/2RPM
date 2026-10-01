#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""提供两站固定端点的单次 JSON 传输，不查询缓存或自动重传。"""

from collections.abc import Mapping
import json

import requests

from .core import ImageHostError
from .diagnostics import safe_current_message


_BASES = {
    'beeimg_cn': 'https://www.beeimg.cn/api/v2',
    'boltp': 'https://www.boltp.com/api/v2',
}
_PATHS = {'group': '/group', 'profile': '/user/profile', 'upload': '/upload'}
_BODY_LIMIT = 1024 * 1024
_CHUNK_SIZE = 64 * 1024
_TIMEOUT = (5, 15)


def is_storage_rejection(provider, status_code, payload) -> bool:
    """精确匹配存储拒绝；Boltp 仅采用批准的合成兼容策略。

    BeeIMG.cn 有历史样本；不将 Boltp 策略描述为官方或实测行为。
    """
    return (
        isinstance(provider, str) and provider in _BASES
        and type(status_code) is int and status_code == 200
        and isinstance(payload, Mapping)
        and isinstance(payload.get('status'), str)
        and payload['status'] == 'error'
        and isinstance(payload.get('message'), str)
        and payload['message'] == '不存在的储存驱动'
    )


def _read_payload(response):
    """累计实际解压字节，超限立即停止且不访问无界响应属性。"""
    body = bytearray()
    for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
        if not chunk:
            continue
        if len(body) + len(chunk) > _BODY_LIMIT:
            return None
        body.extend(chunk)
    try:
        return json.loads(body.decode('utf-8-sig'))
    except (ValueError, RecursionError):
        return None


def request_json(provider, stage, token, *, data=None, files=None) -> dict:
    """独立持有一次会话及响应，只返回成功映射或固定安全错误。

    非成功 HTTP 不读正文；业务诊断仅经显式上下文脱敏后保存。
    连接和读取超时并非总时限，不保证超时后服务器尚未保存图片。
    """
    if not isinstance(provider, str) or provider not in _BASES:
        raise ImageHostError('invalid_provider', '', stage=stage)
    if not isinstance(stage, str) or stage not in _PATHS:
        raise ImageHostError('invalid_options', '')
    if not isinstance(token, str):
        raise ImageHostError('invalid_token', '', stage=stage)

    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    kwargs = dict(headers=headers, timeout=_TIMEOUT, verify=True,
                  allow_redirects=False, stream=True)
    if stage == 'upload':
        kwargs.update(data=data, files=files)
    status_code = None
    payload = None
    code = 'transport_failed'
    try:
        with requests.Session() as session:
            request = session.post if stage == 'upload' else session.get
            with request(_BASES[provider] + _PATHS[stage], **kwargs) as response:
                status_code = response.status_code
                if not 200 <= status_code < 300:
                    code = 'http_failed'
                else:
                    payload = _read_payload(response)
                    code = 'invalid_response'
    except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
        code = 'invalid_response'
    except Exception:
        code = 'transport_failed'

    diagnostic = None
    if code == 'invalid_response' and isinstance(payload, Mapping):
        status = payload.get('status')
        if isinstance(status, str) and status == 'success':
            return payload
        if isinstance(status, str) and status == 'error':
            diagnostic = safe_current_message(payload.get('message'))
            code = (
                'storage_unavailable'
                if stage == 'upload'
                and is_storage_rejection(provider, status_code, payload)
                else 'business_rejected'
            )
    # 离开底层异常处理器后构造错误，不保存敏感原始异常链。
    raise ImageHostError(code, '', stage=stage, http_status=status_code,
                         diagnostic=diagnostic)
