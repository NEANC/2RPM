#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""协调单次事件的元数据、凭证和恢复预算，不执行图片上传。

调用方为每条图片上传链创建一个图片句柄，配置位置使用零基索引。
配置位置在事件中保持稳定；元数据按站点和已解析凭证共享。遇到已
确认的上传存储失效时调用 begin_recovery，再调用 prepare_storage。
是否允许 POST 重传及 HTTP 错误分类仍由未来的调用方负责。
"""

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
import hashlib
import hmac
import math
import os
import time
from typing import Literal

from .core import ImageHostError
from .core import resolve_token
from .diagnostics import diagnostic_scope as _diagnostic_scope
from .diagnostics import sanitize_message
from .expiration import ExpirationDecision
from .storage import fetch_storage_metadata
from .storage import select_storage
from .storage import StorageMetadata
from .storage import StorageSelection
from .storage_cache import StorageCache
from modules.log_safety import extend_secrets
from modules.log_safety import safety_scope
from modules.operation_trace import current_request_id
from modules.operation_trace import trace_operation
from modules.operation_trace import trace_skipped
from modules.operation_trace import trace_warning


_CACHE_WARNINGS = {
    'identity': '存储元数据缓存不可用',
    'load': '存储元数据缓存读取失败',
    'save': '存储元数据缓存保存失败',
    'invalidate': '存储元数据缓存失效失败',
}


def _reuse_metadata(reason_code, provider, entry):
    """记录一次不产生新请求的事件内元数据复用事实。"""
    trace_skipped('metadata_cache', reason_code=reason_code,
                  cache_layer='event', provider=provider,
                  source_operation_id=entry.source_operation_id)


@dataclass(frozen=True, eq=False)
class ImageState:
    """标识一条图片上传链，只公开独立的起始时刻，不保存图片。"""

    started_at: float


@dataclass(frozen=True)
class StorageState:
    """公开单图单配置的只读状态，不包含凭证或内部身份摘要。

    selection 为 None 表示尚未选择或此前选择的版本已经失效。
    metadata_version 是本事件、本身份的递增版本，不是磁盘版本；
    selection_source 为 'manual' 时该值表示发送前观察到的事件代次，
    不声明本槽位拥有元数据，也不要求元数据存在或版本匹配。
    rejected_storage_id 只记录本图本槽实际被拒绝的选择。
    历史期限由 record_expiration 显式记录，不在图片之间共享。
    """

    selection: StorageSelection | None = None
    metadata_version: int = 0
    correction_used: bool = False
    previous_deadline: float | None = None
    previous_shortened: bool | None = None
    selection_source: Literal['manual', 'metadata'] | None = None
    rejected_storage_id: int | None = None


@dataclass(frozen=True)
class _Failure:
    """仅保存重新校验后的固定字段，不持有异常、回溯或响应。"""

    code: str
    stage: str
    http_status: int | None
    diagnostic: str | None = field(default=None, repr=False)

    def error(self):
        """每次呈现重新构造异常，不复用含回溯的实例。"""
        return ImageHostError(self.code, '', stage=self.stage,
                              http_status=self.http_status,
                              diagnostic=self.diagnostic)


@dataclass(repr=False)
class _MetadataEntry:
    """保存一个事件身份的版本、最小结果及惰性磁盘状态。"""

    version: int = 1
    metadata: StorageMetadata | None = None
    failure: _Failure | None = None
    from_disk: bool = False
    corrected: bool = False
    invalidated: bool = False
    disk_ready: bool = False
    disk_identity: str | None = None
    disk_invalidated: bool = False
    source_operation_id: str | None = None


@dataclass(frozen=True, repr=False)
class _Credential:
    """保存发送凭证或延迟错误，不保留原始配置项。"""

    token: str | None
    failure: _Failure | None


class UploadContext:
    """一次通知或 CLI 操作的协调器，串行使用并在事件结束后关闭。

    构造不执行 IO。CLI 仅创建并传入诊断 context；
    registry.upload_with_fallback 统一调用 collect_secrets(hosts)，
    在诊断开启时收集整链凭证，再进入诊断作用域；后续通过
    resolve_credential 使用同一份凭证快照。直接使用 context 的调用方
    须在进入诊断作用域前调用 collect_secrets(hosts) 准备凭证快照。
    通知默认关闭诊断，仅在实际尝试配置项时惰性解析。
    配置位置不可在事件中重排。
    不保存图片、上传 URL、完整响应，也不修改调用方配置或环境。
    """

    def __init__(self, *, diagnostics=False, cache=None, clock=time.time,
                 resolved_tokens=None):
        """仅初始化内存容器；随机密钥和磁盘身份都延迟到实际使用。"""
        self._diagnostics = diagnostics is True
        self._cache = cache if cache is not None else StorageCache(clock=clock)
        self._clock = clock
        self._resolved_tokens = dict(resolved_tokens or {})
        self._key = None
        self._entries = {}
        self._images = {}
        self._credentials = {}
        self._secrets = ()
        self._collected = False
        self._warnings = []
        self._closed = False

    def _ensure_open(self):
        """关闭后的所有业务入口统一拒绝，不重新生成身份或秘密。"""
        if self._closed:
            raise ImageHostError('config_error', '')

    def _identity(self, provider, token):
        """以事件随机 HMAC 区分站点和凭证，不保存原始凭证字典键。"""
        self._ensure_open()
        if type(provider) is not str:
            raise ImageHostError('invalid_provider', '', stage='storage')
        if type(token) is not str:
            raise ImageHostError('invalid_token', '', stage='storage')
        identity = None
        try:
            if self._key is None:
                self._key = os.urandom(32)
            site = provider.encode('utf-8', errors='surrogatepass')
            message = (len(site).to_bytes(8, 'big') + site
                       + token.encode('utf-8', errors='surrogatepass'))
            identity = hmac.digest(self._key, message, hashlib.sha256)
        except Exception:
            pass
        if identity is None:
            raise ImageHostError('config_error', '', stage='storage')
        return identity

    def _entry(self, provider, token):
        """取得事件记录，不初始化持久化身份。"""
        identity = self._identity(provider, token)
        if identity not in self._entries:
            self._entries[identity] = _MetadataEntry()
        return self._entries[identity]

    def _now(self):
        """读取唯一注入时钟并安全校验，不回退系统时钟。"""
        result = None
        try:
            value = self._clock()
            if (type(value) in (int, float) and value >= 0
                    and math.isfinite(value)):
                result = float(value)
        except Exception:
            pass
        if result is None:
            raise ImageHostError('config_error', '', stage='storage')
        return result

    def _warn(self, warning):
        """保序去重固定告警，不接受动态错误文本。"""
        trace_warning()
        if warning not in self._warnings:
            self._warnings.append(warning)

    def _cache_call(self, operation, *args):
        """缓存失败不妨碍事件成功；控制信号不被转成普通缓存失败。"""
        result = False if operation in ('save', 'invalidate') else None
        try:
            result = getattr(self._cache, operation)(*args)
        except Exception:
            self._warn(_CACHE_WARNINGS[operation])
        try:
            for warning in self._cache.take_warnings():
                if warning in _CACHE_WARNINGS.values():
                    self._warn(warning)
        except Exception:
            self._warn(_CACHE_WARNINGS['identity'])
        return result

    def take_warnings(self) -> tuple[str, ...]:
        """取出并清空本事件固定缓存告警；关闭后返回空元组。"""
        warnings = tuple(self._warnings)
        self._warnings.clear()
        return warnings

    def _failure(self, error, *, fallback, secrets=()):
        """重验固定元数据，仅显式开启时二次脱敏既有诊断字段。"""
        diagnostic = None
        if isinstance(error, ImageHostError):
            if self._diagnostics:
                diagnostic = sanitize_message(
                    error.diagnostic, self._secrets + secrets)
            safe = ImageHostError(error.code, '', stage=error.stage,
                                  http_status=error.http_status,
                                  diagnostic=diagnostic)
        else:
            stage = 'storage' if fallback == 'storage_lookup_failed' else ''
            safe = ImageHostError(fallback, '', stage=stage)
        return _Failure(
            safe.code, safe.stage, safe.http_status, safe.diagnostic)

    def _delete_record(self, entry):
        """每次失效只删除一次，未初始化磁盘身份时延迟到实际查询。"""
        if entry.disk_ready and not entry.disk_invalidated:
            self._cache_call('invalidate', entry.disk_identity)
            entry.disk_invalidated = True

    def invalidate(self, provider, token, *, version=None, disk_only=False):
        """失效指定版本；版本不匹配时立即返回，不改内存也不删磁盘。

        已查询的身份立即删除磁盘记录。尚未查询的身份仅记录失效，
        在首次 get_metadata 初始化磁盘身份后先删除再查询。带版本的
        失效以调用方实际发送的代次为准：晚到的旧请求既不删除新代
        内存结果，也不删除新代磁盘记录，也不重复递增。带版本且匹配
        时同时清除该代成功与失败结果，使新代可重新查询。
        """
        entry = self._entry(provider, token)
        if version is not None and entry.version != version:
            return
        if disk_only:
            if entry.failure is not None:
                return
            entry.version += 1
            # 一次失效结束当前代次，此后重新查询产生的结果属于新代次
            entry.metadata = None
            entry.from_disk = False
            entry.invalidated = True
            entry.disk_invalidated = False
            if entry.disk_ready:
                self._cache_call('invalidate', entry.disk_identity)
                entry.disk_invalidated = True
            return
        if (version is None and (entry.failure is not None or entry.invalidated)):
            return
        entry.version += 1
        entry.metadata = None
        entry.failure = None
        entry.from_disk = False
        entry.corrected = False
        entry.invalidated = True
        entry.disk_invalidated = False
        self._delete_record(entry)

    def get_metadata(self, provider, token, *, correction=False, record_reuse=True):
        """先复用事件结果，再读有效磁盘，最后进行一次元数据查询。

        correction 首次绕过旧成功和磁盘；同次已成功或失败的纠错
        不重复查询。真正的新失效必须显式 invalidate，或使用按
        已选版本判定的 begin_recovery，不能只重复设置 correction。
        """
        entry = self._entry(provider, token)
        if entry.failure is not None:
            _reuse_metadata('reused_failure', provider, entry)
            raise entry.failure.error()
        if correction and not entry.corrected and not entry.invalidated:
            self.invalidate(provider, token)
        if entry.metadata is not None:
            if record_reuse:
                _reuse_metadata('reused_metadata', provider, entry)
            return entry.metadata
        if not entry.disk_ready:
            entry.disk_identity = self._cache_call('identity', provider, token)
            entry.disk_ready = True
        if entry.invalidated:
            self._delete_record(entry)
        else:
            cached = self._cache_call('load', entry.disk_identity)
            if cached is not None:
                entry.metadata = cached
                entry.from_disk = True
                entry.source_operation_id = None
                return cached
        try:
            with self.diagnostic_scope():
                fetched = fetch_storage_metadata(
                    provider, token, now=self._now())
        except Exception as error:
            entry.failure = self._failure(
                error, fallback='storage_lookup_failed', secrets=(token,))
            entry.source_operation_id = current_request_id()
        else:
            entry.metadata = fetched
            entry.from_disk = False
            entry.corrected = entry.invalidated
            entry.invalidated = False
            entry.source_operation_id = current_request_id()
            self._cache_call('save', entry.disk_identity, fetched)
        if entry.failure is not None:
            raise entry.failure.error()
        return entry.metadata

    def now(self) -> float:
        """用于请求前期限检查，只读注入时钟，不创建图片或元数据状态。"""
        self._ensure_open()
        return self._now()

    def start_image(self) -> ImageState:
        """创建独立图片链句柄并固定起始时刻，不初始化缓存身份。"""
        self._ensure_open()
        image = ImageState(self._now())
        self._images[image] = {}
        return image

    def _item(self, image, index, provider, token):
        """定位单图单配置状态，拒绝跨事件句柄及非法配置位置。"""
        self._ensure_open()
        if (type(image) is not ImageState or image not in self._images
                or type(index) is not int or index < 0):
            raise ImageHostError('config_error', '')
        key = (index, self._identity(provider, token))
        items = self._images[image]
        if key not in items:
            items[key] = StorageState()
        return items, key

    def storage_state(self, image, index, provider, token) -> StorageState:
        """读取冻结状态；手填登记不要求元数据存在，其余需版本匹配。

        手填登记只记录发送前观察到的事件代次，因此仍向调用方暴露
        实际选择；元数据来源的选择在失效后不再暴露为可用选择。
        """
        items, key = self._item(image, index, provider, token)
        state = items[key]
        if state.selection_source == 'manual':
            return state
        entry = self._entries.get(key[1])
        if (entry is None or entry.metadata is None
                or entry.version != state.metadata_version):
            return replace(state, selection=None)
        return state

    def record_manual_selection(self, image, index, provider, token,
                                storage_id) -> StorageSelection:
        """登记两站首次合法手填直传，只使用事件身份与代次。

        不查询、不初始化磁盘身份、不读写或失效缓存，也不改变失败
        快照：登记的是发送前观察到的事件代次，无事件记录时基线为 0。
        """
        items, key = self._item(image, index, provider, token)
        entry = self._entries.get(key[1])
        selection = StorageSelection(storage_id, False)
        items[key] = replace(
            items[key], selection=selection, selection_source='manual',
            metadata_version=(entry.version if entry is not None else 0))
        return selection

    def prepare_storage(self, image, index, provider, token, *,
                        manual_present=False) -> StorageSelection:
        """两站按同一组顺序选择当前可用存储。

        手填值不参与优先级；合法手填由调用方先登记，只有自动发现
        或恢复进入这里。manual_present 仅用于向调用方报告非法手填
        已被替代，不改变候选顺序。
        """
        items, key = self._item(image, index, provider, token)
        return self._prepare_group_storage(
            items, key, provider, token, manual_present)

    def _prepare_group_storage(self, items, key, provider, token,
                               manual_present) -> StorageSelection:
        """两站按组顺序选择，排除本图本槽实际被拒绝的候选。

        手填值不参与优先级，也不消费共享纠错预算或触发上传前刷新：
        本事件新列表直接替代，其他图或其他槽位仍可选到同一编号。
        """
        current = self.get_metadata(provider, token)
        entry = self._entries[key[1]]
        selected = self._select_storage(
            provider, current, manual_present, None,
            excluded_id=items[key].rejected_storage_id)
        items[key] = replace(items[key], selection=selected,
                             metadata_version=entry.version,
                             selection_source='metadata')
        return selected

    def _select_storage(self, provider, metadata, manual_present, manual,
                        *, excluded_id=None):
        """按统一组顺序选择，并为恢复传入本图本槽的排除候选。"""
        with trace_operation('storage_select', 'select', provider=provider,
                             candidate_count=len(metadata.storage_ids)) as operation:
            try:
                selected = select_storage(metadata, manual_present, manual,
                                          provider=provider,
                                          excluded_id=excluded_id)
            except ImageHostError as error:
                operation.finish('failed', reason_code=error.code)
                raise
            operation.finish('succeeded', selection_source='group_first',
                             storage_id=selected.storage_id)
            return selected

    def begin_recovery(self, image, index, provider, token) -> bool:
        """消费一次预算并按本图已用代次失效，不查询或执行 POST。

        返回 True 仅表示允许继续取得恢复选择，不授权额外上传次数。
        两站均按“发送时代次”推进：无记录时创建首代、同代重建
        下一代、更新的网络代直接复用、仅来自磁盘的代必须刷新。
        """
        items, key = self._item(image, index, provider, token)
        state = items[key]
        if state.correction_used:
            return False
        if state.selection is None:
            items[key] = replace(state, correction_used=True)
            return False
        items[key] = replace(state, correction_used=True,
                             rejected_storage_id=state.selection.storage_id)
        entry = self._entries.get(key[1])
        baseline = state.metadata_version
        if entry is None:
            entry = _MetadataEntry(version=1, invalidated=True)
            self._entries[key[1]] = entry
            return True
        if entry.version == baseline:
            self.invalidate(provider, token, version=baseline)
        elif entry.from_disk:
            self.invalidate(provider, token, version=entry.version)
        return True

    def record_expiration(self, image, index, provider, token, decision):
        """记录既有期限决策的历史字段，不重算期限或跨图共享。"""
        items, key = self._item(image, index, provider, token)
        if not isinstance(decision, ExpirationDecision):
            raise ImageHostError('config_error', '', stage='expiration')
        items[key] = replace(items[key], previous_deadline=decision.deadline,
                             previous_shortened=decision.shortened)

    def _credential(self, index, host):
        """首次读取配置项即冻结成功值或固定失败，不保存配置引用。"""
        self._ensure_open()
        if type(index) is not int or index < 0:
            raise ImageHostError('config_error', '')
        if index not in self._credentials:
            try:
                if not isinstance(host, Mapping):
                    raise ImageHostError('invalid_host', '')
                raw = host.get('token')
                if self._diagnostics and type(raw) is str and raw:
                    self._secrets = tuple(dict.fromkeys(
                        self._secrets + (raw,)))
                if index in self._resolved_tokens:
                    injected = self._resolved_tokens[index]
                    token = (str(injected) if isinstance(injected, str)
                             else injected)
                else:
                    token = resolve_token(raw)
            except Exception as error:
                failure = self._failure(error, fallback='config_error')
                self._credentials[index] = _Credential(None, failure)
            else:
                self._credentials[index] = _Credential(token, None)
                if token:
                    self._secrets = tuple(dict.fromkeys(
                        self._secrets + (token,)))
                if token:
                    extend_secrets((token,))
        return self._credentials[index]

    def collect_secrets(self, hosts):
        """仅诊断开启时预解析整链映射项，普通错误延迟到实际尝试。

        每个事件只收集一次；调用方提供合成或已加载的配置列表，
        本入口不读取配置文件。关闭诊断时完全不遍历或解析 hosts。
        """
        self._ensure_open()
        if not self._diagnostics or self._collected:
            return
        if not isinstance(hosts, list):
            raise ImageHostError('invalid_hosts', '')
        for index, host in enumerate(hosts):
            if isinstance(host, Mapping):
                self._credential(index, host)
        self._collected = True

    def resolve_credential(self, index, host) -> str:
        """实际尝试配置项时取得冻结凭证，或重新构造其延迟安全错误。"""
        credential = self._credential(index, host)
        if credential.failure is not None:
            raise credential.failure.error()
        return credential.token

    @contextmanager
    def diagnostic_scope(self):
        """用不可变秘密快照包装既有作用域，关闭诊断时显式传入 None。"""
        self._ensure_open()
        try:
            with safety_scope(self._secrets):
                with _diagnostic_scope(self._secrets if self._diagnostics else None):
                    yield
        finally:
            extend_secrets(self._secrets)

    def close(self):
        """幂等释放本对象的秘密及事件引用，不承诺 Python 内存安全擦除。

        调用方应先退出已经进入的 diagnostic_scope，再关闭事件；
        已进入作用域持有的不可变快照由其自身 finally 确定性释放。
        """
        self._closed = True
        self._entries.clear()
        self._images.clear()
        self._credentials.clear()
        self._resolved_tokens.clear()
        self._secrets = ()
        self._warnings.clear()
        self._key = None
        self._cache = None
        self._clock = None

    def __enter__(self):
        """进入事件生命周期，不自动开启诊断或解析配置。"""
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """退出时关闭事件，不吞掉任何异常或控制信号。"""
        self.close()
        return False
