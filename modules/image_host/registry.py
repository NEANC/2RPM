#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""提供静态适配器注册表和每张图片独立的顺序故障转移。"""

from collections.abc import Mapping
from copy import deepcopy

from .core import ImageHostError
from .core import UploadFailure
from .core import UploadResult
from .core import _has_control
from .core import resolve_token
from .core import validate_image_url
from .providers import upload_beeimg
from .providers import upload_beeimg_cn
from .providers import upload_catbox
from .providers import upload_superbed
from .providers import upload_wmimg


# 仅在具体站点契约核验并实现后添加适配器，不预注册空壳。
UPLOADERS = {
    'catbox': upload_catbox, 'wmimg': upload_wmimg, 'beeimg': upload_beeimg,
    'beeimg_cn': upload_beeimg_cn, 'superbed': upload_superbed,
}


def _failure(provider, code):
    """重新映射安全类别，绝不转发适配器的消息或动态类型名。"""
    error = ImageHostError(code, '')
    return UploadFailure(provider, error.code, error.message)


def _shared_failure(code):
    """将共享输入或顶层配置错误计为一次失败，不伪造站点尝试。"""
    return UploadResult(False, None, None, (), (_failure('配置', code),))


def _valid_input(png_bytes, filename):
    """要求非空字节及安全的单一文件名，不在上传层解码图片。"""
    return (
        isinstance(png_bytes, bytes) and bool(png_bytes)
        and isinstance(filename, str) and bool(filename.strip())
        and filename not in {'.', '..'} and not _has_control(filename)
        and not any(char in filename for char in '<>:"/\\|?*')
    )


def upload_with_fallback(png_bytes, filename, hosts) -> UploadResult:
    """依次调用图床适配器，首个有效链接即停止，不重试或缓存。

    适配器签名为 upload(image_bytes, filename, token, options)，
    返回候选直链或抛出异常。每项仅接收自己的凭证和参数深拷贝。
    普通异常转为安全结果；进程控制信号保持原对象向上传播。
    """
    if not _valid_input(png_bytes, filename):
        return _shared_failure('invalid_input')
    if hosts is None:
        return _shared_failure('not_configured')
    if not isinstance(hosts, list):
        return _shared_failure('invalid_hosts')
    if not hosts:
        return _shared_failure('not_configured')

    attempts = []
    failures = []
    for index, host in enumerate(hosts, 1):
        provider = f'第{index}项'
        try:
            if not isinstance(host, Mapping):
                raise ImageHostError('invalid_host', '')
            raw_provider = host.get('provider')
            if not isinstance(raw_provider, str):
                raise ImageHostError('invalid_provider', '')
            normalized = raw_provider.strip().lower()
            if not normalized or _has_control(normalized):
                raise ImageHostError('invalid_provider', '')
            provider = normalized
            if provider not in UPLOADERS:
                raise ImageHostError('unknown_provider', '')
            token = resolve_token(host.get('token'))
            options = host.get('options')
            if options is None:
                options = {}
            if not isinstance(options, Mapping):
                raise ImageHostError('invalid_options', '')
            copy_failed = False
            try:
                options = deepcopy(options)
            except Exception:
                copy_failed = True
            if copy_failed:
                raise ImageHostError('invalid_options', '')
            candidate = UPLOADERS[provider](
                png_bytes, filename, token, options)
            url = validate_image_url(candidate)
        except ImageHostError as error:
            failures.append(_failure(provider, error.code))
        except Exception:
            failures.append(_failure(provider, 'upload_failed'))
        else:
            attempts.append(provider)
            return UploadResult(
                True, provider, url, tuple(attempts), tuple(failures))
        attempts.append(provider)
    return UploadResult(False, None, None, tuple(attempts), tuple(failures))
