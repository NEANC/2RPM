#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""提供静态适配器注册表和每张图片独立的顺序故障转移。"""

from collections.abc import Mapping
from copy import deepcopy

from .context import UploadContext
from .core import ImageHostError
from .core import UploadFailure
from .core import UploadResult
from .core import _has_control
from .core import resolve_token
from .core import validate_image_url
from .diagnostics import safe_current_message
from .expiration import compute_expiration
from .expiration import parse_expiration
from .expiration import PreparedV2Options
from .providers import upload_beeimg
from .providers import upload_beeimg_cn
from .providers import upload_boltp
from .providers import upload_catbox
from .providers import upload_superbed
from .providers import upload_wmimg
from .providers import validate_v2_options


# 仅在具体站点契约核验并实现后添加适配器，不预注册空壳。
UPLOADERS = {
    'catbox': upload_catbox, 'wmimg': upload_wmimg, 'beeimg': upload_beeimg,
    'beeimg_cn': upload_beeimg_cn, 'boltp': upload_boltp,
    'superbed': upload_superbed,
}


def _failure(provider, code, *, error=None):
    """重验类别和元数据，仅在当前诊断作用域内再次脱敏。"""
    safe = ImageHostError(
        code, '', stage=error.stage if error is not None else '',
        http_status=error.http_status if error is not None else None,
        diagnostic=safe_current_message(error.diagnostic)
        if error is not None else None)
    return UploadFailure(provider, safe.code, safe.message, safe.stage,
                         safe.http_status, safe.diagnostic)


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


_V2_PROVIDERS = frozenset({'beeimg_cn', 'boltp'})
_WARN_REPLACED = '手填储存驱动不可用，已自动替代'
_WARN_SHORTENED = '保存期限已按图床上限缩短'
_WARN_UNSUPPORTED = '该图床不支持保存期限，已忽略'


def _add_warnings(warnings, values):
    """按发生顺序合并固定告警，不重复展示同一条提醒。"""
    for value in values:
        if value not in warnings:
            warnings.append(value)


def _copy_options(host):
    """深复制普通选项映射，不保留用户伪造的内部期限属性。"""
    options = host.get('options')
    if options is None:
        return {}
    if not isinstance(options, Mapping):
        raise ImageHostError('invalid_options', '')
    copied = None
    try:
        copied = deepcopy(dict(options))
    except Exception:
        pass
    if copied is None:
        raise ImageHostError('invalid_options', '')
    return copied


def _prepare_v2(context, image, index, provider, token, options, seconds,
                warnings, png_bytes=None, filename=None):
    """复用版本化存储选择；Boltp 按候选独立上传并重算期限。"""
    if provider == 'boltp':
        retention = (context.get_boltp_retention(token)
                     if seconds is not None else None)
        candidates = ([options['storage_id']] if 'storage_id' in options else [])
        candidates.extend(value for value in (2, 3) if value not in candidates)
        failure = None
        for storage_id in candidates:
            decision = compute_expiration(
                seconds, image.started_at, retention, None, context.now())
            if decision.shortened:
                _add_warnings(warnings, (_WARN_SHORTENED,))
            prepared = PreparedV2Options(
                deepcopy(options), expired_at=decision.expired_at)
            prepared['storage_id'] = storage_id
            try:
                result = UPLOADERS[provider](png_bytes, filename, token, prepared)
                return validate_image_url(result), None
            except ImageHostError as error:
                failure = _failure(provider, error.code, error=error)
            except Exception:
                failure = _failure(provider, 'upload_failed')
            if not (failure.code == 'storage_unavailable'
                    and failure.stage == 'upload'
                    and failure.http_status == 200):
                break
        return None, failure
    try:
        selected = context.prepare_storage(
            image, index, provider, token,
            manual_present='storage_id' in options,
            manual_value=options.get('storage_id'))
    finally:
        _add_warnings(warnings, context.take_warnings())
    if selected.replaced_manual:
        _add_warnings(warnings, (_WARN_REPLACED,))
    metadata = context.get_metadata(provider, token)
    state = context.storage_state(image, index, provider, token)
    decision = compute_expiration(
        seconds, image.started_at, metadata.file_expire_seconds,
        state.previous_deadline, context.now(),
        previous_shortened=state.previous_shortened)
    context.record_expiration(image, index, provider, token, decision)
    if decision.shortened:
        _add_warnings(warnings, (_WARN_SHORTENED,))
    prepared = PreparedV2Options(deepcopy(options),
                                 expired_at=decision.expired_at)
    prepared['storage_id'] = selected.storage_id
    return prepared


def _upload_chain(png_bytes, filename, hosts, context, image):
    """按配置槽位顺序上传，仅两站的精确存储拒绝允许一次恢复。"""
    attempts = []
    failures = []
    warnings = []
    _add_warnings(warnings, context.take_warnings())
    for index, host in enumerate(hosts):
        provider = f'第{index + 1}项'
        url = None
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
            token = context.resolve_credential(index, host)
            options = _copy_options(host)
            if provider == 'boltp' and 'storage_id' in options:
                storage_id = options['storage_id']
                if type(storage_id) is not int or storage_id < 0:
                    raise ImageHostError('invalid_options', '')
            seconds = None
            if 'expiration' in host:
                if 'expired_at' in options:
                    raise ImageHostError('config_error', '', stage='expiration')
                seconds = parse_expiration(host['expiration'])
            is_v2 = provider in _V2_PROVIDERS
            if is_v2:
                if provider == 'boltp' and 'storage_id' in options:
                    storage_id = options['storage_id']
                    if (type(storage_id) is not int or storage_id < 0):
                        raise ImageHostError('invalid_options', '')
                    validate_v2_options({key: value for key, value in options.items()
                                         if key != 'storage_id'},
                                        provider=provider, require_storage=False)
                elif provider == 'boltp':
                    validate_v2_options(options, provider=provider,
                                        require_storage=False)
                else:
                    validate_v2_options(options, provider=provider,
                                        require_storage=False)
            elif seconds is not None:
                _add_warnings(warnings, (_WARN_UNSUPPORTED,))
            for transmission in range(2 if is_v2 else 1):
                if provider == 'boltp':
                    url, candidate_failure = _prepare_v2(
                        context, image, index, provider, token, options,
                        seconds, warnings, png_bytes, filename)
                    if candidate_failure is not None:
                        failures.append(candidate_failure)
                    break
                prepared = (_prepare_v2(
                    context, image, index, provider, token, options,
                    seconds, warnings) if is_v2 else deepcopy(options))
                failure = None
                try:
                    candidate = UPLOADERS[provider](
                        png_bytes, filename, token, prepared)
                    url = validate_image_url(candidate)
                except ImageHostError as error:
                    failure = _failure(provider, error.code, error=error)
                except Exception:
                    failure = _failure(provider, 'upload_failed')
                if failure is None:
                    break
                failures.append(failure)
                if not (
                        is_v2 and transmission == 0
                        and failure.code == 'storage_unavailable'
                        and failure.stage == 'upload'
                        and failure.http_status == 200
                        and context.begin_recovery(
                            image, index, provider, token)):
                    break
        except ImageHostError as error:
            failures.append(_failure(provider, error.code, error=error))
        except Exception:
            failures.append(_failure(provider, 'upload_failed'))
        finally:
            _add_warnings(warnings, context.take_warnings())
        attempts.append(provider)
        if url is not None:
            return UploadResult(True, provider, url, tuple(attempts),
                                tuple(failures), tuple(warnings))
    return UploadResult(False, None, None, tuple(attempts), tuple(failures),
                        tuple(warnings))


def upload_with_fallback(png_bytes, filename, hosts, *,
                         context=None) -> UploadResult:
    """首个有效直链即停止，复用外部事件或确定性关闭自有事件。

    保留原三个位置参数及适配器四参数调用。共享输入提前失败不执行
    缓存或网络 IO；普通错误仅保存安全字段，控制信号原对象传播。
    """
    if not _valid_input(png_bytes, filename):
        return _shared_failure('invalid_input')
    if hosts is None:
        return _shared_failure('not_configured')
    if not isinstance(hosts, list):
        return _shared_failure('invalid_hosts')
    if not hosts:
        return _shared_failure('not_configured')

    owned = context is None
    if owned:
        context = UploadContext(diagnostics=False)
    try:
        context.collect_secrets(hosts)
        with context.diagnostic_scope():
            try:
                image = context.start_image()
                return _upload_chain(
                    png_bytes, filename, hosts, context, image)
            except ImageHostError as error:
                return UploadResult(False, None, None, (), (
                    _failure('配置', error.code, error=error),),
                    context.take_warnings())
            except Exception:
                return _shared_failure('upload_failed')
    except ImageHostError as error:
        return _shared_failure(error.code)
    except Exception:
        return _shared_failure('upload_failed')
    finally:
        if owned:
            context.close()
