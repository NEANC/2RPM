#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""隔离持久化最小存储元数据，不查询网络或参与上传流程。"""

from collections.abc import Mapping
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

from .storage import StorageMetadata


_VERSION = 1
_TTL_SECONDS = 86400
_MAX_BYTES = 64 * 1024
_KEY_BYTES = 32
_KEY_NAME = '.storage-key'
_PROVIDERS = frozenset({'beeimg_cn', 'boltp'})
_IDENTITY = re.compile(r'(beeimg_cn|boltp)-[0-9a-f]{64}')
_FIELDS = frozenset({
    'version', 'provider', 'fetched_at', 'storage_ids',
    'default_storage_id', 'file_expire_seconds',
})
_WARN_UNAVAILABLE = '存储元数据缓存不可用'
_WARN_READ = '存储元数据缓存读取失败'
_WARN_SAVE = '存储元数据缓存保存失败'
_WARN_INVALIDATE = '存储元数据缓存失效失败'
_DISABLED_IDENTITIES = set()


def _positive_integer(value):
    """只接受非布尔正整数，不转换原始字段。"""
    return type(value) is int and value > 0


def _timestamp(value):
    """验证有限非负时刻，转换失败由公开边界安全处理。"""
    if type(value) not in (int, float) or value < 0:
        raise ValueError
    result = float(value)
    if not math.isfinite(result):
        raise ValueError
    return result


def _provider(identity):
    """从严格白名单身份提取站点，拒绝任意路径及非字符串。"""
    if type(identity) is not str:
        return None
    match = _IDENTITY.fullmatch(identity)
    return match.group(1) if match is not None else None


def _metadata(payload, provider):
    """重新验证完整磁盘结构，仅构造最小冻结快照并保序去重。"""
    if not isinstance(payload, Mapping) or set(payload) != _FIELDS:
        raise ValueError
    if type(payload['version']) is not int or payload['version'] != _VERSION:
        raise ValueError
    if payload['provider'] != provider:
        raise ValueError
    storage_ids = payload['storage_ids']
    if (type(storage_ids) is not list or not storage_ids
            or not all(_positive_integer(value) for value in storage_ids)):
        raise ValueError
    default = payload['default_storage_id']
    if default is not None and not _positive_integer(default):
        raise ValueError
    expire = payload['file_expire_seconds']
    if expire is not None and (type(expire) is not int or expire < 0):
        raise ValueError
    fetched_at = _timestamp(payload['fetched_at'])
    return StorageMetadata(tuple(dict.fromkeys(storage_ids)), default,
                           expire, fetched_at)


def _remove_owned(path):
    """尽力清理本次确实创建的文件，不保留普通清理异常。"""
    try:
        path.unlink()
    except Exception:
        pass


class StorageCache:
    """通过本机密钥隔离站点与凭证，缓存查询时刻起二十四小时。"""

    def __init__(self, root=None, *, clock=time.time):
        """只保存参数与内存状态，不解析目录或执行任何外部操作。"""
        self._root_input = root
        self._clock = clock
        self._root = None
        self._key = None
        self._unavailable = False
        self._warnings = []

    def _warn(self, message):
        """按首次出现顺序保存固定中文告警，不保存异常对象。"""
        if message not in self._warnings:
            self._warnings.append(message)

    def take_warnings(self) -> tuple[str, ...]:
        """返回并清空保序去重的固定告警。"""
        warnings = tuple(self._warnings)
        self._warnings.clear()
        return warnings

    def _prepare(self):
        """惰性确定规范绝对根并准备持久密钥，失败后禁用本实例。"""
        if self._unavailable:
            return False
        if self._key is not None:
            return True
        try:
            root = self._root_input
            if root is None:
                local = os.environ.get('LOCALAPPDATA')
                if not local or not Path(local).is_absolute():
                    raise ValueError
                root = Path(local) / '2RPM' / 'image_host'
            self._root = Path(os.path.normcase(os.path.abspath(root)))
            self._root.mkdir(parents=True, exist_ok=True)
            self._key = self._prepare_key()
            return True
        except Exception:
            self._unavailable = True
            self._warn(_WARN_UNAVAILABLE)
            return False

    def _prepare_key(self):
        """排他创建并同步密钥，竞争失败只读取获胜者一次。"""
        path = self._root / _KEY_NAME
        try:
            stream = open(path, 'xb')
        except FileExistsError:
            with open(path, 'rb') as existing:
                key = existing.read(_KEY_BYTES + 1)
            if len(key) != _KEY_BYTES:
                raise ValueError
            return key
        complete = False
        try:
            with stream:
                key = os.urandom(_KEY_BYTES)
                if len(key) != _KEY_BYTES or stream.write(key) != _KEY_BYTES:
                    raise ValueError
                stream.flush()
                os.fsync(stream.fileno())
            complete = True
            return key
        finally:
            if not complete:
                _remove_owned(path)

    def identity(self, provider, token) -> str | None:
        """仅对原始字符串计算带站点前缀的持久 HMAC 身份。"""
        try:
            if (type(provider) is not str or provider not in _PROVIDERS
                    or type(token) is not str):
                raise ValueError
            message = provider.encode('ascii') + b'\0' + token.encode('utf-8')
            if not self._prepare():
                return None
            digest = hmac.new(self._key, message, hashlib.sha256).hexdigest()
            return provider + '-' + digest
        except Exception:
            self._warn(_WARN_UNAVAILABLE)
            return None

    def load(self, identity) -> StorageMetadata | None:
        """有界读取并重新校验缓存，过期、未来或进程内禁用均 miss。"""
        if identity is None:
            return None
        provider = _provider(identity)
        if provider is None:
            self._warn(_WARN_READ)
            return None
        if not self._prepare():
            return None
        if (str(self._root), identity) in _DISABLED_IDENTITIES:
            return None
        try:
            now = _timestamp(self._clock())
            try:
                stream = open(self._root / (identity + '.json'), 'rb')
            except FileNotFoundError:
                return None
            with stream:
                body = stream.read(_MAX_BYTES + 1)
            if len(body) > _MAX_BYTES:
                raise ValueError
            metadata = _metadata(json.loads(body), provider)
            age = now - metadata.fetched_at
            if age < 0 or age >= _TTL_SECONDS:
                return None
            return metadata
        except Exception:
            self._warn(_WARN_READ)
            return None

    def save(self, identity, metadata) -> bool:
        """先校验并限制序列化字节，再同步临时文件并原子替换记录。"""
        if identity is None:
            return False
        provider = _provider(identity)
        if provider is None:
            self._warn(_WARN_SAVE)
            return False
        try:
            if (not isinstance(metadata, StorageMetadata)
                    or type(metadata.storage_ids) is not tuple):
                raise ValueError
            payload = {
                'version': _VERSION,
                'provider': provider,
                'fetched_at': metadata.fetched_at,
                'storage_ids': list(metadata.storage_ids),
                'default_storage_id': metadata.default_storage_id,
                'file_expire_seconds': metadata.file_expire_seconds,
            }
            validated = _metadata(payload, provider)
            payload['storage_ids'] = list(validated.storage_ids)
            payload['fetched_at'] = validated.fetched_at
            body = json.dumps(payload, separators=(',', ':'),
                              allow_nan=False).encode('utf-8')
            if len(body) > _MAX_BYTES:
                raise ValueError
            _timestamp(self._clock())
            if not self._prepare():
                return False
            self._write_atomic(self._root / (identity + '.json'), body)
            return True
        except Exception:
            self._warn(_WARN_SAVE)
            return False

    def _write_atomic(self, destination, body):
        """只管理本次临时路径与句柄，任何失败都保留原完整记录。"""
        descriptor = None
        temporary = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix='.storage-', suffix='.tmp', dir=self._root)
            temporary = Path(name)
            stream = os.fdopen(descriptor, 'wb')
            descriptor = None
            with stream:
                if stream.write(body) != len(body):
                    raise ValueError
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            temporary = None
        finally:
            try:
                if descriptor is not None:
                    os.close(descriptor)
            finally:
                if temporary is not None:
                    _remove_owned(temporary)

    def invalidate(self, identity) -> bool:
        """删除单份记录；删除失败则跨实例禁止本进程读取该身份。"""
        if identity is None:
            return True
        if _provider(identity) is None:
            self._warn(_WARN_INVALIDATE)
            return False
        if not self._prepare():
            if self._root is not None:
                _DISABLED_IDENTITIES.add((str(self._root), identity))
            self._warn(_WARN_INVALIDATE)
            return False
        try:
            try:
                (self._root / (identity + '.json')).unlink()
            except FileNotFoundError:
                pass
            return True
        except Exception:
            _DISABLED_IDENTITIES.add((str(self._root), identity))
            self._warn(_WARN_INVALIDATE)
            return False
