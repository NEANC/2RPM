#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""提供两站固定端点的单次 JSON 传输，不查询缓存或自动重传。"""

from collections.abc import Mapping
import json

import requests

from .core import ImageHostError
from .core import validate_image_url
from .diagnostics import safe_current_message
from modules.log_safety import guard_dependency_logging
from modules.operation_trace import trace_request


_BASES = {
    'beeimg_cn': 'https://www.beeimg.cn/api/v2',
    'boltp': 'https://www.boltp.com/api/v2',
}
_PATHS = {'group': '/group', 'profile': '/user/profile', 'upload': '/upload'}
_BODY_LIMIT = 1024 * 1024
_CHUNK_SIZE = 64 * 1024
_TIMEOUT = (5, 15)
_GUARDED_LOGGERS = ('requests', 'urllib3')


class _ExplicitAuth(requests.auth.AuthBase):
    """仅保留调用方认证头，阻止隐式读取其他账号凭证。"""

    def __call__(self, request):
        """原样返回已准备的请求，不添加或覆盖认证信息。"""
        return request


class _V2Session(requests.sessions.Session):
    """两站专用会话，不跟随或预先准备任何重定向请求。"""

    def resolve_redirects(self, response, request, **kwargs):
        """返回空迭代器，避免 Requests 为准备下一请求预读正文。"""
        return iter(())


def is_storage_rejection(provider, status_code, payload) -> bool:
    """精确匹配存储拒绝；Boltp 仅采用批准的兼容策略。

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


def _read_body(response, operation):
    """累计实际解压字节，并在读取返回或抛出时立即冻结请求耗时。"""
    body = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
            if not chunk:
                continue
            if len(body) + len(chunk) > _BODY_LIMIT:
                return None
            body.extend(chunk)
    finally:
        operation.mark_end()
    return bytes(body)


def _read_payload(response, operation):
    """在正文读取耗时冻结后按固定 UTF-8 BOM 兼容语义解析 JSON。"""
    body = _read_body(response, operation)
    if body is None:
        return None
    try:
        return json.loads(body.decode('utf-8-sig'))
    except (ValueError, RecursionError):
        return None


def _validate_payload(provider, stage, payload):
    """按站点与阶段延迟导入对应解析器，避免跨站与循环依赖。"""
    if stage == 'upload':
        data = payload.get('data')
        if not isinstance(data, Mapping):
            raise ImageHostError('invalid_response', '', stage=stage)
        try:
            validate_image_url(data.get('public_url'))
        except ImageHostError:
            raise ImageHostError('invalid_response', '', stage=stage) from None
    elif stage == 'profile':
        from . import storage_boltp
        storage_boltp.parse_profile(payload)
    elif provider == 'boltp':
        from . import storage_boltp
        storage_boltp.parse_retention(payload)
    else:
        from . import storage_beeimg_cn
        storage_beeimg_cn.parse_group(payload)


def _send(provider, stage, kwargs):
    """在独立会话中执行一次真实请求，返回状态、映射与固定分类。"""
    status_code = None
    payload = None
    code = 'transport_failed'
    diagnostic = None
    try:
        with _V2Session() as session:
            session.auth = _ExplicitAuth()
            method = 'POST' if stage == 'upload' else 'GET'
            url = _BASES[provider] + _PATHS[stage]
            request = requests.Request(
                method, url, headers=kwargs['headers'],
                data=kwargs.get('data'), files=kwargs.get('files'))
            prepared = session.prepare_request(request)
            settings = session.merge_environment_settings(
                prepared.url, {}, kwargs['stream'], kwargs['verify'], None)
            send_kwargs = dict(
                settings, timeout=kwargs['timeout'],
                allow_redirects=kwargs['allow_redirects'])
            kind = 'upload_attempt' if stage == 'upload' else stage
            with trace_request(provider, kind) as operation:
                try:
                    operation.mark_transport_started()
                    with session.send(prepared, **send_kwargs) as response:
                        status_code = response.status_code
                        if not 200 <= status_code < 300:
                            operation.mark_end()
                            code = 'http_failed'
                        else:
                            payload = _read_payload(response, operation)
                            code = 'invalid_response'
                except (KeyboardInterrupt, SystemExit):
                    operation.mark_end()
                    raise
                except (json.JSONDecodeError,
                        requests.exceptions.JSONDecodeError):
                    operation.mark_end()
                    code = 'invalid_response'
                except Exception:
                    operation.mark_end()
                    code = 'transport_failed'
                operation.mark_end()
                if code == 'invalid_response' and isinstance(payload, Mapping):
                    status = payload.get('status')
                    if isinstance(status, str) and status == 'success':
                        try:
                            _validate_payload(provider, stage, payload)
                        except ImageHostError as error:
                            operation.finish('failed', reason_code=error.code,
                                             http_status=status_code)
                            return status_code, payload, 'success', diagnostic
                        else:
                            operation.finish('succeeded', http_status=status_code)
                            return status_code, payload, 'success', diagnostic
                    if isinstance(status, str) and status == 'error':
                        diagnostic = safe_current_message(payload.get('message'))
                        code = (
                            'storage_unavailable'
                            if stage == 'upload'
                            and is_storage_rejection(
                                provider, status_code, payload)
                            else 'business_rejected'
                        )
                operation.finish('failed', reason_code=code,
                                 http_status=status_code)
    except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
        code = 'invalid_response'
    except Exception:
        code = 'transport_failed'
    return status_code, payload, code, diagnostic


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
    with guard_dependency_logging(_GUARDED_LOGGERS):
        status_code, payload, code, diagnostic = _send(
            provider, stage, kwargs)
        if code == 'success':
            return payload
        # 离开底层异常处理器后构造错误，不保存敏感原始异常链。
        raise ImageHostError(code, '', stage=stage,
                             http_status=status_code,
                             diagnostic=diagnostic)
