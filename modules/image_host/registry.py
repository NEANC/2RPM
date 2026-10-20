#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""提供静态适配器注册表和每张图片独立的顺序故障转移。"""

from collections.abc import Mapping
from copy import deepcopy
import sys

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
from modules.log_safety import extend_secrets
from modules.operation_trace import trace_operation
from modules.operation_trace import trace_fallback
from modules.operation_trace import trace_skipped
from modules.operation_trace import trace_slot
from modules.operation_trace import trace_warning


# 仅在具体站点契约核验并实现后添加适配器，不预注册空壳。
UPLOADERS = {
    'catbox': upload_catbox, 'wmimg': upload_wmimg, 'beeimg': upload_beeimg,
    'beeimg_cn': upload_beeimg_cn, 'boltp': upload_boltp,
    'superbed': upload_superbed,
}

_FALLBACK_ACTIONS = {
    'preupload_refresh': 'refresh', 'postupload_recovery': 'recover',
    'boltp_candidate': 'switch', 'host_failover': 'failover',
}


def _record_fallback(fallback_kind, reason_code):
    """记录一次已执行的回退事实，计数只在实际收尾时增加。"""
    with trace_operation('fallback', _FALLBACK_ACTIONS[fallback_kind],
                         fallback_kind=fallback_kind) as operation:
        operation.finish('succeeded', reason_code=reason_code)


def _skip_fallback(fallback_kind, reason_code):
    """记录一次计划但未执行的回退，不伪造开始与耗时。"""
    trace_skipped('fallback', reason_code=reason_code,
                  fallback_kind=fallback_kind)


def _normalize_provider(host, index):
    """解析配置项白名单站点名，失败时只返回安全标签与固定错误。"""
    label = f'第{index + 1}项'
    if not isinstance(host, Mapping):
        return label, ImageHostError('invalid_host', '')
    raw = host.get('provider')
    if not isinstance(raw, str):
        return label, ImageHostError('invalid_provider', '')
    normalized = raw.strip().lower()
    if not normalized or _has_control(normalized):
        return label, ImageHostError('invalid_provider', '')
    if normalized not in UPLOADERS:
        return normalized, ImageHostError('unknown_provider', '')
    return normalized, None


def _slot_reason(failures):
    """给出槽位失败终态的固定原因分类。"""
    return failures[-1].code if failures else 'upload_failed'


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


def _valid_input(image_bytes, filename):
    """要求非空字节及安全的单一文件名，不在上传层解码图片。"""
    return (
        isinstance(image_bytes, bytes) and bool(image_bytes)
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
        trace_warning()
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
                warnings, *, transmission=0):
    """两站统一选择存储并计算期限，绝不在此执行上传请求。

    首次合法手填只登记实际选择及发送前代次，用组上限 None 计算期限，
    不查询组或资料、不读写存储缓存；其余情况（未填、非法手填或恢复）
    先按组顺序自动选择，再按元数据组期限计算。transmission 为当前上传
    代次，第二代为恢复，即使 options 仍保留手填也只走自动选择。
    """
    manual = options['storage_id'] if 'storage_id' in options else None
    if transmission == 0 and type(manual) is int and manual > 0:
        selected = context.record_manual_selection(
            image, index, provider, token, manual)
        state = context.storage_state(image, index, provider, token)
        decision = compute_expiration(
            seconds, image.started_at, None, state.previous_deadline,
            context.now(), previous_shortened=state.previous_shortened)
        context.record_expiration(image, index, provider, token, decision)
        if decision.shortened:
            _add_warnings(warnings, (_WARN_SHORTENED,))
        prepared = PreparedV2Options(deepcopy(options),
                                     expired_at=decision.expired_at)
        prepared['storage_id'] = selected.storage_id
        return prepared
    try:
        selected = context.prepare_storage(
            image, index, provider, token,
            manual_present=transmission == 0 and 'storage_id' in options,
            manual_value=manual)
    finally:
        _add_warnings(warnings, context.take_warnings())
    if selected.replaced_manual:
        _add_warnings(warnings, (_WARN_REPLACED,))
    metadata = context.get_metadata(provider, token, record_reuse=False)
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


def _upload_chain(image_bytes, filename, hosts, context, image):
    """按配置槽位顺序上传，仅两站的精确存储拒绝允许一次恢复。"""
    attempts = []
    failures = []
    warnings = []
    _add_warnings(warnings, context.take_warnings())
    source_slot_id = None
    for index, host in enumerate(hosts):
        provider, shape_error = _normalize_provider(host, index)
        slot = trace_slot(provider, index)
        url = None
        recovery_scope = None
        slot_scope = slot.scope()
        slot_scope.__enter__()
        failover_scope = None
        try:
            if index:
                failover_scope = trace_fallback(
                    'host_failover', 'failover', reason_code='slot_failed',
                    provider=provider, source_operation_id=source_slot_id)
                failover = failover_scope.__enter__()
            if shape_error is not None:
                raise shape_error
            token = context.resolve_credential(index, host)
            options = _copy_options(host)
            seconds = None
            if 'expiration' in host:
                if 'expired_at' in options:
                    raise ImageHostError('config_error', '', stage='expiration')
                seconds = parse_expiration(host['expiration'])
            is_v2 = provider in _V2_PROVIDERS
            if is_v2:
                validate_v2_options(options, require_storage=False)
            elif seconds is not None and provider != 'wmimg':
                _add_warnings(warnings, (_WARN_UNSUPPORTED,))
            for transmission in range(2 if is_v2 else 1):
                prepared = (_prepare_v2(
                    context, image, index, provider, token, options,
                    seconds, warnings, transmission=transmission)
                    if is_v2 else deepcopy(options))
                if recovery_scope is not None:
                    recovery.finish('succeeded', reason_code='storage_rejected')
                    recovery_scope.__exit__(None, None, None)
                    recovery_scope = None
                if provider == 'wmimg' and seconds is not None:
                    decision = compute_expiration(
                        seconds, image.started_at, None, None, context.now())
                    prepared = PreparedV2Options(
                        prepared, expired_at=decision.expired_at)
                failure = None
                try:
                    candidate = UPLOADERS[provider](
                        image_bytes, filename, token, prepared)
                    url = validate_image_url(candidate)
                except ImageHostError as error:
                    failure = _failure(provider, error.code, error=error)
                except Exception:
                    failure = _failure(provider, 'upload_failed')
                if failure is None:
                    break
                failures.append(failure)
                precise_rejection = (
                    failure.code == 'storage_unavailable'
                    and failure.stage == 'upload'
                    and failure.http_status == 200)
                if is_v2 and transmission == 0 and precise_rejection:
                    state = context.storage_state(image, index, provider, token)
                    if not state.correction_used and state.selection is not None:
                        recovery_scope = trace_operation(
                            'fallback', 'recover',
                            fallback_kind='postupload_recovery')
                        recovery = recovery_scope.__enter__()
                recovery_allowed = (
                    is_v2 and transmission == 0 and precise_rejection
                    and context.begin_recovery(
                        image, index, provider, token))
                if is_v2 and precise_rejection:
                    if transmission == 0:
                        if not recovery_allowed:
                            _skip_fallback('postupload_recovery', 'budget_used')
                    else:
                        # 第二次精确拒绝只按实际发送版本失效，不再查询或上传。
                        state = context.storage_state(
                            image, index, provider, token)
                        context.invalidate(
                            provider, token, version=state.metadata_version,
                            disk_only=True)
                if not recovery_allowed:
                    break
        except ImageHostError as error:
            if recovery_scope is not None:
                recovery.finish('failed', reason_code=error.code)
            failures.append(_failure(provider, error.code, error=error))
        except Exception:
            if recovery_scope is not None:
                recovery.finish('failed', reason_code='operation_failed')
            failures.append(_failure(provider, 'upload_failed'))
        except (KeyboardInterrupt, SystemExit):
            slot.finish('cancelled', reason_code='batch_cancelled')
            raise
        finally:
            try:
                if recovery_scope is not None:
                    recovery_scope.__exit__(*sys.exc_info())
                _add_warnings(warnings, context.take_warnings())
            finally:
                try:
                    if failover_scope is not None:
                        if sys.exc_info()[0] is None:
                            failover.finish('succeeded' if url else 'failed',
                                            reason_code='slot_failed')
                        failover_scope.__exit__(*sys.exc_info())
                finally:
                    slot_scope.__exit__(*sys.exc_info())
        attempts.append(provider)
        if url is not None:
            slot.finish('succeeded')
            extend_secrets((url,))
            return UploadResult(True, provider, url, tuple(attempts),
                                tuple(failures), tuple(warnings))
        slot.finish('failed', reason_code=_slot_reason(failures))
        source_slot_id = slot.operation_id
    return UploadResult(False, None, None, tuple(attempts), tuple(failures),
                        tuple(warnings))


def upload_with_fallback(image_bytes, filename, hosts, *,
                         context=None) -> UploadResult:
    """首个有效直链即停止，复用外部事件或确定性关闭自有事件。

    保留原三个位置参数及适配器四参数调用。共享输入提前失败不执行
    缓存或网络 IO；普通错误仅保存安全字段，控制信号原对象传播。
    """
    if not _valid_input(image_bytes, filename):
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
                    image_bytes, filename, hosts, context, image)
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
