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

from . import storage
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


_CACHE_WARNINGS = {
    'identity': '存储元数据缓存不可用',
    'load': '存储元数据缓存读取失败',
    'save': '存储元数据缓存保存失败',
    'invalidate': '存储元数据缓存失效失败',
}


@dataclass(frozen=True, eq=False)
class ImageState:
    """标识一条图片上传链，只公开独立的起始时刻，不保存图片。"""

    started_at: float


@dataclass(frozen=True)
class StorageState:
    """公开单图单配置的只读状态，不包含凭证或内部身份摘要。

    selection 为 None 表示尚未选择或此前选择的版本已经失效。
    metadata_version 是本事件、本身份的递增版本，不是磁盘版本。
    历史期限由 record_expiration 显式记录，不在图片之间共享。
    """

    selection: StorageSelection | None = None
    metadata_version: int = 0
    correction_used: bool = False
    previous_deadline: float | None = None
    previous_shortened: bool | None = None


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
        self._retentions = {}
        self._images = {}
        self._corrected = set()
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

    def invalidate(self, provider, token):
        """失效当前成功版本；重复待恢复或失败状态保持幂等。

        已查询的身份立即删除磁盘记录。尚未查询的身份仅记录失效，
        在首次 get_metadata 初始化磁盘身份后先删除再查询。
        恢复失败在本事件内终止该代查询；再次 invalidate 不清除失败。
        """
        entry = self._entry(provider, token)
        if entry.failure is not None or entry.invalidated:
            return
        entry.version += 1
        entry.metadata = None
        entry.from_disk = False
        entry.corrected = False
        entry.invalidated = True
        entry.disk_invalidated = False
        self._delete_record(entry)

    def get_metadata(self, provider, token, *, correction=False):
        """先复用事件结果，再读有效磁盘，最后进行一次元数据查询。

        correction 首次绕过旧成功和磁盘；同次已成功或失败的纠错
        不重复查询。真正的新失效必须显式 invalidate，或使用按
        已选版本判定的 begin_recovery，不能只重复设置 correction。
        """
        entry = self._entry(provider, token)
        if entry.failure is not None:
            raise entry.failure.error()
        if correction and not entry.corrected and not entry.invalidated:
            self.invalidate(provider, token)
        if entry.metadata is not None:
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
                return cached
        try:
            with self.diagnostic_scope():
                fetched = fetch_storage_metadata(
                    provider, token, now=self._now())
        except Exception as error:
            entry.failure = self._failure(
                error, fallback='storage_lookup_failed', secrets=(token,))
        else:
            entry.metadata = fetched
            entry.from_disk = False
            entry.corrected = entry.invalidated
            entry.invalidated = False
            self._cache_call('save', entry.disk_identity, fetched)
        if entry.failure is not None:
            raise entry.failure.error()
        return entry.metadata

    def get_boltp_retention(self, token):
        """按事件内 HMAC 身份缓存 Boltp 期限或安全失败快照。"""
        identity = self._identity('boltp', token)
        if identity not in self._retentions:
            try:
                value = storage.fetch_boltp_retention(token)
            except Exception as error:
                self._retentions[identity] = self._failure(
                    error, fallback='storage_lookup_failed', secrets=(token,))
            else:
                self._retentions[identity] = value
        result = self._retentions[identity]
        if isinstance(result, _Failure):
            raise result.error()
        return result

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
        """读取冻结状态；已失效的选择不再向调用方暴露为可用选择。"""
        items, key = self._item(image, index, provider, token)
        state = items[key]
        entry = self._entries.get(key[1])
        if (entry is None or entry.metadata is None
                or entry.version != state.metadata_version):
            return replace(state, selection=None)
        return state

    def prepare_storage(self, image, index, provider, token, *,
                        manual_present=False, manual_value=None
                        ) -> StorageSelection:
        """选择当前可用存储，必要时消耗该图该项的一次纠错预算。

        旧磁盘列表上的无效手填触发一次纠错查询；本事件新列表则
        直接替代。同配置同类手填问题共享已纠错事实，但每次均用
        最新版本重新选择。非法手填只记录类别，不序列化或保留原对象。
        """
        items, key = self._item(image, index, provider, token)
        manual = (manual_value if type(manual_value) is int
                  and manual_value > 0 else None)
        signature = (key, bool(manual_present), manual)
        if signature in self._corrected:
            items[key] = replace(items[key], correction_used=True)
        current = self.get_metadata(provider, token)
        entry = self._entries[key[1]]
        selected = select_storage(current, manual_present, manual)
        if selected.replaced_manual:
            used = items[key].correction_used
            items[key] = replace(items[key], correction_used=True)
            self._corrected.add(signature)
            if entry.from_disk and not used:
                current = self.get_metadata(provider, token, correction=True)
                selected = select_storage(current, manual_present, manual)
        items[key] = replace(items[key], selection=selected,
                             metadata_version=entry.version)
        return selected

    def begin_recovery(self, image, index, provider, token) -> bool:
        """消费一次预算并按本图已用版本失效，不查询或执行 POST。

        返回 True 仅表示允许继续取得恢复选择，不授权额外上传次数。
        若另一图片已使旧版本失效或完成刷新，只消费本图预算而不再
        删除新记录；后续 prepare_storage 复用新结果或同代失败快照。
        """
        items, key = self._item(image, index, provider, token)
        state = items[key]
        if state.correction_used or state.selection is None:
            return False
        items[key] = replace(state, correction_used=True)
        entry = self._entries[key[1]]
        if entry.version == state.metadata_version:
            self.invalidate(provider, token)
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
                token = (self._resolved_tokens[index]
                         if index in self._resolved_tokens
                         else resolve_token(raw))
            except Exception as error:
                failure = self._failure(error, fallback='config_error')
                self._credentials[index] = _Credential(None, failure)
            else:
                self._credentials[index] = _Credential(token, None)
                if self._diagnostics and token:
                    self._secrets = tuple(dict.fromkeys(
                        self._secrets + (token,)))
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
        with _diagnostic_scope(self._secrets if self._diagnostics else None):
            yield

    def close(self):
        """幂等释放本对象的秘密及事件引用，不承诺 Python 内存安全擦除。

        调用方应先退出已经进入的 diagnostic_scope，再关闭事件；
        已进入作用域持有的不可变快照由其自身 finally 确定性释放。
        """
        self._closed = True
        self._entries.clear()
        self._retentions.clear()
        self._images.clear()
        self._corrected.clear()
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
