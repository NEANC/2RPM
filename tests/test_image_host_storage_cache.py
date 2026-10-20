#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""在隔离临时目录验证磁盘缓存，不读取真实配置、凭证或缓存。"""

import builtins
from dataclasses import FrozenInstanceError
import hashlib
import hmac
from importlib import import_module
from importlib.util import find_spec
from inspect import signature
import json
import os
from pathlib import Path
import socket
import time

import pytest
from ruamel.yaml import YAML

from modules.image_host import storage
from modules.image_host.core import resolve_token
from modules.image_host.storage import StorageMetadata
from modules.yaml_scalar_compat import normalize_config_scalars


SECRET = 'FAKE_CACHE_SECRET_9381'
OTHER_SECRET = 'FAKE_CACHE_OTHER_9381'
PATH_SECRET = 'FAKE_PATH_SECRET_9381'
RESPONSE_SECRET = 'FAKE_RESPONSE_SECRET_9381'
LIMIT = 64 * 1024
KEY_NAME = '.storage-key'
WARN_UNAVAILABLE = '存储元数据缓存不可用'
WARN_READ = '存储元数据缓存读取失败'
WARN_SAVE = '存储元数据缓存保存失败'
WARN_INVALIDATE = '存储元数据缓存失效失败'
WARNINGS = {WARN_UNAVAILABLE, WARN_READ, WARN_SAVE, WARN_INVALIDATE}
TOKEN = '123|offline-token'
TOKEN_FORMS = ('plain', 'single', 'double', 'backtick', 'brace', 'bracket')
HOSTS_HEAD = 'push:\n  screenshot:\n    image_host:\n'


def cache_module():
    """让缺少实现成为功能断言失败，而非测试收集错误。"""
    name = 'modules.image_host.storage_cache'
    assert find_spec(name) is not None, '缺少持久化存储元数据缓存'
    return import_module(name)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    """默认根也限定在临时目录，并阻止任何意外网络连接。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local-app-data'))

    def forbidden(*args, **kwargs):
        """网络连接不是缓存职责，任何调用立即失败。"""
        pytest.fail('磁盘缓存测试禁止网络访问')

    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


def make_cache(tmp_path, clock=None):
    """创建隔离实例、身份和合法的合成元数据。

    身份固定为 BeeIMG.CN；两站新语义均不存在账号默认存储，故默认编号为 None。
    """
    cache = cache_module().StorageCache(
        tmp_path, clock=clock if clock is not None else lambda: 1000.0)
    identity = cache.identity('beeimg_cn', SECRET)
    assert isinstance(identity, str)
    return cache, identity, StorageMetadata((13,), None, 3600, 1000.0)


def payload():
    """生成仅包含批准字段的独立磁盘记录。"""
    return {'version': 1, 'provider': 'beeimg_cn', 'fetched_at': 1000.0,
            'storage_ids': [14, 13, 14], 'default_storage_id': 99,
            'file_expire_seconds': None}


def legacy_payload(provider, fetched_at, default=14):
    """构造不含 profile_state 的原始 v1 记录，默认编号取旧版字段值。"""
    return {'version': 1, 'provider': provider, 'fetched_at': fetched_at,
            'storage_ids': [13, 14], 'default_storage_id': default,
            'file_expire_seconds': 0}


def record_path(root, identity):
    """定位测试身份的唯一 JSON 文件。"""
    return root / (identity + '.json')


def write_payload(root, identity, value):
    """只向隔离目录写入合成 JSON，用于读盘重新验证。"""
    record_path(root, identity).write_bytes(json.dumps(value).encode('utf-8'))


def token_source(form, text):
    """返回 token 值在测试配置中使用的 YAML 源片段。"""
    if form == 'single':
        return "'" + text + "'"
    if form == 'double':
        return '"' + text + '"'
    if form == 'backtick':
        return '`' + text + '`'
    if form == 'brace':
        return '{' + text + '}'
    if form == 'bracket':
        return '[' + text + ']'
    return text


def resolved_config_token(form, text=TOKEN):
    """经真实配置词法预处理、往返解析与凭证解析得到内置 str 凭证。"""
    document = (HOSTS_HEAD + '      - {provider: beeimg_cn, token: '
                + token_source(form, text) + ', options: {storage_id: 1}}\n')
    parser = YAML()
    parser.preserve_quotes = True
    host = parser.load(normalize_config_scalars(document))[
        'push']['screenshot']['image_host'][0]
    return resolve_token(host['token'])


def assert_safe(cache, caplog, capsys):
    """固定告警不得泄露路径、响应或凭证，且没有日志输出。"""
    warnings = cache.take_warnings()
    assert set(warnings) <= WARNINGS
    assert len(warnings) == len(set(warnings))
    assert cache.take_warnings() == ()
    rendered = repr(warnings) + repr(cache)
    for secret in (SECRET, OTHER_SECRET, PATH_SECRET, RESPONSE_SECRET):
        assert secret not in rendered
    assert not caplog.records
    assert capsys.readouterr() == ('', '')
    assert not any(isinstance(value, BaseException)
                   for value in vars(cache).values())
    return warnings


class ObservedFile:
    """包装真实文件，记录资源释放并在指定操作注入单个故障。"""

    def __init__(self, stream, phase=None, error=None, short=False):
        """保存真实句柄与测试注入条件。"""
        self.stream = stream
        self.phase = phase
        self.error = error
        self.short = short
        self.sizes = []

    def __enter__(self):
        """返回包装器，真实句柄仍由本上下文持有。"""
        return self

    def __exit__(self, *args):
        """总是关闭真实句柄，不吞掉原控制信号。"""
        self.stream.close()

    def read(self, size=-1):
        """记录有界读取量，并允许模拟读取失败。"""
        self.sizes.append(size)
        if self.phase == 'read':
            raise self.error
        return self.stream.read(size)

    def write(self, data):
        """可先留下真实半成品，再抛出异常或报告短写。"""
        if self.phase == 'write':
            self.stream.write(data[:3])
            raise self.error
        if self.short:
            return self.stream.write(data[:3])
        return self.stream.write(data)

    def flush(self):
        """在真实刷盘边界注入故障。"""
        if self.phase == 'flush':
            raise self.error
        self.stream.flush()

    def fileno(self):
        """提供真实文件描述符以执行同步操作。"""
        return self.stream.fileno()

    def close(self):
        """允许实现显式关闭已经取得的文件资源。"""
        self.stream.close()


def test_signature_constructor_and_none_have_no_io(tmp_path, monkeypatch):
    """构造、空身份和取告警都不触碰磁盘、时钟或随机源。"""
    module = cache_module()
    params = signature(module.StorageCache).parameters
    assert list(params) == ['root', 'clock']
    assert params['root'].default is None
    assert params['clock'].kind is params['clock'].KEYWORD_ONLY
    assert params['clock'].default is time.time

    def forbidden(*args, **kwargs):
        """禁止惰性边界之前发生外部操作。"""
        pytest.fail('构造或空身份不允许进行 IO')

    with monkeypatch.context() as patch:
        patch.setattr(module, 'open', forbidden, raising=False)
        patch.setattr(Path, 'mkdir', forbidden)
        patch.setattr(os, 'urandom', forbidden)
        cache = module.StorageCache(tmp_path / 'unused', clock=forbidden)
        default = module.StorageCache(clock=forbidden)
        for instance in (cache, default):
            assert instance.load(None) is None
            assert instance.save(None, None) is False
            assert instance.invalidate(None) is True
            assert instance.take_warnings() == ()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('value', [None, '', 'relative-cache', '.'])
def test_invalid_default_root_disables_without_fallback(
        tmp_path, monkeypatch, value):
    """缺失、空白或相对默认目录禁用，不回退工作目录。"""
    module = cache_module()
    if value is None:
        monkeypatch.delenv('LOCALAPPDATA')
    else:
        monkeypatch.setenv('LOCALAPPDATA', value)

    def forbidden(*args, **kwargs):
        """无合法默认根时禁止读写任何文件。"""
        pytest.fail('不得使用回退目录')

    with monkeypatch.context() as patch:
        patch.setattr(module, 'open', forbidden, raising=False)
        patch.setattr(Path, 'mkdir', forbidden)
        cache = module.StorageCache()
        assert cache.identity('beeimg_cn', SECRET) is None
        assert cache.identity('boltp', '') is None
        assert cache.take_warnings() == (WARN_UNAVAILABLE,)
    assert list(tmp_path.iterdir()) == []


def test_default_root_is_lazy_absolute_and_explicit_root_isolated(
        tmp_path, monkeypatch):
    """首次使用才读取默认根，显式目录不访问环境缓存。"""
    module = cache_module()
    cache = module.StorageCache(clock=lambda: 1000)
    local = tmp_path / 'late-local'
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    identity = cache.identity('boltp', '')
    root = local / '2RPM' / 'image_host'
    assert identity
    assert (root / KEY_NAME).stat().st_size == 32
    isolated = tmp_path / 'explicit'
    monkeypatch.delenv('LOCALAPPDATA')
    explicit = module.StorageCache(isolated)
    assert explicit.identity('beeimg_cn', SECRET)
    assert (isolated / KEY_NAME).stat().st_size == 32


@pytest.mark.parametrize('identity', [
    '', '../escape', '..\\escape', '/absolute', 'C:\\absolute',
    'beeimg_cn-' + 'a' * 63, 'beeimg_cn-' + 'a' * 65,
    'beeimg_cn-' + 'A' * 64, 'beeimg_cn-' + 'g' * 64,
    'other-' + 'a' * 64, 'boltp-' + 'a' * 64 + '\n', 13, [],
])
def test_invalid_identity_rejected_before_disk(
        tmp_path, monkeypatch, identity):
    """严格身份白名单在任何目录创建或读写前阻止路径穿越。"""
    module = cache_module()
    cache = module.StorageCache(tmp_path)

    def forbidden(*args, **kwargs):
        """非法身份不能访问磁盘。"""
        pytest.fail('非法身份发生磁盘操作')

    with monkeypatch.context() as patch:
        patch.setattr(module, 'open', forbidden, raising=False)
        patch.setattr(Path, 'mkdir', forbidden)
        assert cache.load(identity) is None
        assert cache.save(identity, StorageMetadata((13,), 13, 0, 0)) is False
        assert cache.invalidate(identity) is False
    assert set(cache.take_warnings()) <= WARNINGS
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('provider, token', [
    ('other', SECRET), ('BOLTP', ''), (None, ''), ([], ''),
    ('boltp', None), ('beeimg_cn', 13), ('boltp', '\ud800'),
])
def test_invalid_identity_inputs_do_not_create_key(tmp_path, provider, token):
    """不支持的站点、非字符串及无法编码的凭证安全拒绝。"""
    cache = cache_module().StorageCache(tmp_path)
    assert cache.identity(provider, token) is None
    assert list(tmp_path.iterdir()) == []


def test_identity_hmac_is_stable_isolated_and_secret_free(
        tmp_path, monkeypatch):
    """跨实例稳定且站点、原始凭证、匿名完全隔离，不保存明文。"""
    module = cache_module()
    cache = module.StorageCache(tmp_path, clock=lambda: 1000)
    tokens = ['', SECRET, OTHER_SECRET, ' ' + SECRET + ' ',
              '${FAKE_REF}', '汉字']
    monkeypatch.setenv('FAKE_REF', SECRET)
    identities = []
    for provider in ('beeimg_cn', 'boltp'):
        for token in tokens:
            identity = cache.identity(provider, token)
            identities.append(identity)
            assert (module.StorageCache(tmp_path).identity(provider, token)
                    == identity)
            assert cache.save(identity, StorageMetadata((13,), 13, 0, 1000))
    assert len(set(identities)) == len(identities)
    key = (tmp_path / KEY_NAME).read_bytes()
    assert len(key) == 32
    expected = hmac.new(key, b'beeimg_cn\0' + SECRET.encode('utf-8'),
                        hashlib.sha256).hexdigest()
    assert cache.identity('beeimg_cn', SECRET) == 'beeimg_cn-' + expected
    for path in tmp_path.iterdir():
        data = path.read_bytes()
        for secret in (SECRET, OTHER_SECRET, RESPONSE_SECRET):
            assert secret not in path.name
            assert secret.encode('utf-8') not in data
    assert expected not in repr(cache)
    assert cache.take_warnings() == ()


@pytest.mark.parametrize('provider', ['beeimg_cn', 'boltp'])
def test_quoted_tokens_share_cache_identity(tmp_path, provider):
    """解析后的六种等值写法得到同一持久身份，站点与凭证仍隔离。"""
    cache = cache_module().StorageCache(tmp_path)
    identities = []
    for form in TOKEN_FORMS:
        identity = cache.identity(provider, resolved_config_token(form))
        assert isinstance(identity, str)
        identities.append(identity)
    assert len(set(identities)) == 1
    reference = cache.identity(provider, TOKEN)
    assert identities[0] == reference
    key = (tmp_path / KEY_NAME).read_bytes()
    expected = hmac.new(key, provider.encode('ascii') + b'\0'
                        + TOKEN.encode('utf-8'), hashlib.sha256).hexdigest()
    assert reference == provider + '-' + expected
    other_provider = 'boltp' if provider == 'beeimg_cn' else 'beeimg_cn'
    assert cache.identity(other_provider, TOKEN) != reference
    assert cache.identity(provider, OTHER_SECRET) != reference
    assert cache.identity(provider, '') != reference
    for path in tmp_path.iterdir():
        data = path.read_bytes()
        assert TOKEN not in path.name
        assert TOKEN.encode('utf-8') not in data
    assert cache.take_warnings() == ()


@pytest.mark.parametrize('expire', [None, 0, 3600])
@pytest.mark.parametrize('default', [None, 13, 99])
def test_roundtrip_frozen_minimal_metadata(tmp_path, expire, default):
    """真实往返保序去重，调用方默认值落盘归一为 None。"""
    cache, identity, _ = make_cache(tmp_path)
    metadata = StorageMetadata((14, 13, 14), default, expire, 1000.0)
    assert cache.save(identity, metadata)
    result = cache.load(identity)
    assert result == StorageMetadata((14, 13), None, expire, 1000.0)
    assert metadata.storage_ids == (14, 13, 14)
    with pytest.raises(FrozenInstanceError):
        result.fetched_at = 0
    stored = json.loads(record_path(tmp_path, identity).read_bytes())
    assert set(stored) == set(payload())
    assert stored['provider'] == 'beeimg_cn'
    assert stored['version'] == 1
    assert stored['default_storage_id'] is None
    assert cache.take_warnings() == ()


@pytest.mark.parametrize('age, hit', [(0, True), (86399.999, True),
                                     (86400, False), (86401, False),
                                     (-0.001, False)])
def test_ttl_boundary_does_not_refresh_timestamp(tmp_path, age, hit):
    """年龄从查询时刻计算，读盘和保存都不刷新时间戳。"""
    now = [1000.0]
    cache, identity, metadata = make_cache(tmp_path, lambda: now[0])
    assert cache.save(identity, metadata)
    before = record_path(tmp_path, identity).read_bytes()
    now[0] += age
    assert (cache.load(identity) == metadata) is hit
    assert record_path(tmp_path, identity).read_bytes() == before


@pytest.mark.parametrize('now', [None, True, False, -1, '1000', [], {},
                               float('nan'), float('inf'), float('-inf'),
                               pytest.param(10 ** 10000, id='huge-integer')])
def test_invalid_clock_safe_for_load_and_save(tmp_path, now):
    """非法当前时刻不会读取旧记录或保存新的无效记录。"""
    clock = [1000]
    cache, identity, metadata = make_cache(tmp_path, lambda: clock[0])
    assert cache.save(identity, metadata)
    original = record_path(tmp_path, identity).read_bytes()
    clock[0] = now
    assert cache.load(identity) is None
    assert cache.save(identity, metadata) is False
    assert record_path(tmp_path, identity).read_bytes() == original


@pytest.mark.parametrize('operation', ['load', 'save'])
@pytest.mark.parametrize('error_type', [
    RuntimeError, KeyboardInterrupt, SystemExit,
])
def test_clock_exception_and_control_signal(tmp_path, operation, error_type):
    """普通时钟异常安全失败，两种控制信号原对象传播。"""
    cache, identity, metadata = make_cache(tmp_path)
    assert cache.save(identity, metadata)
    error = error_type(PATH_SECRET)

    def broken_clock():
        """只注入合成异常，不读取系统时钟。"""
        raise error

    cache = cache_module().StorageCache(tmp_path, clock=broken_clock)
    args = (identity, metadata) if operation == 'save' else (identity,)
    if isinstance(error, Exception):
        expected = False if operation == 'save' else None
        assert getattr(cache, operation)(*args) is expected
    else:
        with pytest.raises(error_type) as caught:
            getattr(cache, operation)(*args)
        assert caught.value is error


BAD_FIELDS = [
    ('version', value) for value in (True, False, 0, 2, 1.0, '1', None)
] + [
    ('provider', value) for value in ('boltp', 'other', None, [], 1)
] + [
    ('storage_ids', value) for value in (
        [], None, {}, '13', [True], [False], [0], [-1], ['13'], [1.0],
        [13, None], [[13]], [13, {}])
] + [
    ('default_storage_id', value)
    for value in (True, False, 0, -1, '13', 1.0, [], {})
] + [
    ('file_expire_seconds', value)
    for value in (True, False, -1, '0', 0.0, [], {})
] + [
    ('fetched_at', value) for value in (
        True, False, None, -1, '1000', [], {}, float('nan'),
        float('inf'), float('-inf'))
]


@pytest.mark.parametrize('field, value', BAD_FIELDS)
def test_disk_field_validation(tmp_path, field, value, caplog, capsys):
    """磁盘字段重新严格验证，不信任版本、类型或特殊数值。"""
    cache, identity, _ = make_cache(tmp_path)
    data = payload()
    data[field] = value
    write_payload(tmp_path, identity, data)
    assert cache.load(identity) is None
    assert assert_safe(cache, caplog, capsys) == (WARN_READ,)


@pytest.mark.parametrize('field', list(payload()))
def test_missing_fields_rejected(tmp_path, field):
    """即使可空字段也必须存在，避免隐式接受不完整记录。"""
    cache, identity, _ = make_cache(tmp_path)
    data = payload()
    del data[field]
    write_payload(tmp_path, identity, data)
    assert cache.load(identity) is None
    assert cache.take_warnings() == (WARN_READ,)


@pytest.mark.parametrize('body', [
    b'', b'{bad-json', b'\xff', b'[]', b'null', b'true', b'13',
    b'"FAKE_RESPONSE_SECRET_9381"', b'[' * 2000 + b'0',
    b'{"version":' + b'9' * 5000 + b'}',
])
def test_bad_json_and_top_level_safe(tmp_path, body, caplog, capsys):
    """损坏、超深结构及非映射正文均安全 miss，不泄露内容。"""
    cache, identity, _ = make_cache(tmp_path)
    record_path(tmp_path, identity).write_bytes(body)
    assert cache.load(identity) is None
    assert assert_safe(cache, caplog, capsys) == (WARN_READ,)


def test_extra_field_rejected_and_missing_file_is_quiet(tmp_path):
    """未知字段不得进入模型，文件缺失则为无告警普通 miss。"""
    cache, identity, _ = make_cache(tmp_path)
    assert cache.load(identity) is None
    assert cache.take_warnings() == ()
    data = payload()
    data['payments'] = [RESPONSE_SECRET]
    write_payload(tmp_path, identity, data)
    assert cache.load(identity) is None
    assert cache.take_warnings() == (WARN_READ,)


def test_forbidden_partial_result_is_never_persisted(tmp_path):
    """forbidden 部分结果在写入边界直接拒存且不留任何记录。"""
    cache, identity, _ = make_cache(tmp_path)
    forbidden = StorageMetadata((13, 14), None, 3600, 1000.0, 'forbidden')
    assert cache.save(identity, forbidden) is False
    assert not record_path(tmp_path, identity).exists()
    assert cache.take_warnings() == ()
    assert {path.name for path in tmp_path.iterdir()} == {KEY_NAME}


def test_extra_profile_state_disk_field_is_rejected(tmp_path):
    """带额外 profile_state 字段的磁盘记录仍按严格字段集拒读。"""
    cache, identity, _ = make_cache(tmp_path)
    data = payload()
    data['profile_state'] = 'forbidden'
    write_payload(tmp_path, identity, data)
    assert cache.load(identity) is None
    assert cache.take_warnings() == (WARN_READ,)


def test_loaded_v1_record_reports_not_requested(tmp_path):
    """合法 v1 记录还原为 not_requested，不推断权限或本次请求。"""
    cache, identity, metadata = make_cache(tmp_path)
    assert cache.save(identity, metadata)
    loaded = cache.load(identity)
    assert loaded.profile_state == 'not_requested'
    with pytest.raises(FrozenInstanceError):
        loaded.profile_state = 'forbidden'


def test_legacy_beeimg_default_is_normalized(tmp_path):
    """旧记录读取时两站默认编号均归一为 None，选择只取组首项。"""
    cache = cache_module().StorageCache(tmp_path, clock=lambda: 1000.0)
    bee_identity = cache.identity('beeimg_cn', SECRET)
    boltp_identity = cache.identity('boltp', SECRET)
    assert bee_identity != boltp_identity
    write_payload(tmp_path, bee_identity, legacy_payload('beeimg_cn', 1000.0))
    write_payload(tmp_path, boltp_identity, legacy_payload('boltp', 1000.0))
    assert (json.loads(record_path(tmp_path, bee_identity).read_bytes())
            ['default_storage_id'] == 14)
    assert (json.loads(record_path(tmp_path, boltp_identity).read_bytes())
            ['default_storage_id'] == 14)
    bee_loaded = cache.load(bee_identity)
    boltp_loaded = cache.load(boltp_identity)
    assert bee_loaded.storage_ids == (13, 14)
    assert bee_loaded.default_storage_id is None
    assert bee_loaded.profile_state == 'not_requested'
    assert boltp_loaded.default_storage_id is None
    assert storage.select_storage(
        boltp_loaded, False, None, provider='boltp').storage_id == 13
    bee_selected = storage.select_storage(
        bee_loaded, False, None, provider='beeimg_cn')
    assert bee_selected.storage_id == 13
    assert cache.take_warnings() == ()


def test_same_token_cache_isolation(tmp_path):
    """同一合成凭证在两站生成独立身份与独立记录，互不覆盖或删除。"""
    cache = cache_module().StorageCache(tmp_path, clock=lambda: 1000.0)
    bee_identity = cache.identity('beeimg_cn', SECRET)
    boltp_identity = cache.identity('boltp', SECRET)
    assert bee_identity != boltp_identity
    assert cache.identity('beeimg_cn', SECRET) == bee_identity
    write_payload(tmp_path, bee_identity, legacy_payload('beeimg_cn', 1000.0))
    write_payload(tmp_path, boltp_identity, legacy_payload('boltp', 1000.0))
    assert {path.name for path in tmp_path.iterdir()} == {
        KEY_NAME, bee_identity + '.json', boltp_identity + '.json'}
    assert cache.load(bee_identity) is not None
    boltp_before = record_path(tmp_path, boltp_identity).read_bytes()
    assert cache.invalidate(bee_identity)
    assert not record_path(tmp_path, bee_identity).exists()
    assert cache.load(boltp_identity).default_storage_id is None
    assert record_path(tmp_path, boltp_identity).read_bytes() == boltp_before
    assert cache.take_warnings() == ()


def test_cache_schema_and_ttl_unchanged(tmp_path):
    """读取归一不放宽六字段白名单，也不改变期限、时间戳与相邻记录。"""
    now = [1000.0]
    cache = cache_module().StorageCache(tmp_path, clock=lambda: now[0])
    bee_identity = cache.identity('beeimg_cn', SECRET)
    boltp_identity = cache.identity('boltp', SECRET)
    write_payload(tmp_path, bee_identity, legacy_payload('beeimg_cn', 1000.0))
    write_payload(tmp_path, boltp_identity, legacy_payload('boltp', 1000.0))
    assert cache.load(bee_identity).default_storage_id is None
    stored = json.loads(record_path(tmp_path, bee_identity).read_bytes())
    assert set(stored) == set(payload())
    assert stored['fetched_at'] == 1000.0
    assert stored['default_storage_id'] == 14
    assert json.loads(record_path(tmp_path, boltp_identity).read_bytes()) == (
        legacy_payload('boltp', 1000.0))

    for extra in ('not_requested', 'forbidden'):
        data = legacy_payload('beeimg_cn', 1000.0)
        data['profile_state'] = extra
        write_payload(tmp_path, bee_identity, data)
        assert cache.load(bee_identity) is None
        assert cache.take_warnings() == (WARN_READ,)

    for value in (0, -1, True, '14', []):
        data = legacy_payload('beeimg_cn', 1000.0)
        data['default_storage_id'] = value
        write_payload(tmp_path, bee_identity, data)
        assert cache.load(bee_identity) is None
    assert cache.take_warnings() == (WARN_READ,)

    write_payload(tmp_path, bee_identity, legacy_payload('beeimg_cn', 1000.0))
    now[0] = 1000.0 + 86400
    assert cache.load(bee_identity) is None
    now[0] = 999.999
    assert cache.load(bee_identity) is None
    assert json.loads(record_path(tmp_path, boltp_identity).read_bytes()) == (
        legacy_payload('boltp', 1000.0))

    now[0] = 1000.0
    fresh = StorageMetadata((13, 14), None, 0, 1000.0)
    assert cache.save(bee_identity, fresh)
    saved = json.loads(record_path(tmp_path, bee_identity).read_bytes())
    assert set(saved) == set(payload())
    assert saved['default_storage_id'] is None
    assert cache.take_warnings() == ()


@pytest.mark.parametrize('extra, hit', [(0, True), (1, False), (50000, False)])
def test_read_is_bounded_and_closes_real_file(
        tmp_path, monkeypatch, extra, hit):
    """实际文件精确上限可读，超限最多读取上限加一并释放句柄。"""
    cache, identity, _ = make_cache(tmp_path)
    data = json.dumps(payload()).encode('utf-8')
    path = record_path(tmp_path, identity)
    path.write_bytes(data + b' ' * (LIMIT + extra - len(data)))
    observed = []

    def observe(path, mode):
        """保留真实文件打开，仅记录调用方读取参数。"""
        wrapped = ObservedFile(builtins.open(path, mode))
        observed.append(wrapped)
        return wrapped

    monkeypatch.setattr(cache_module(), 'open', observe, raising=False)
    assert (cache.load(identity) is not None) is hit
    assert observed[-1].sizes == [LIMIT + 1]
    assert all(item.stream.closed for item in observed)


@pytest.mark.parametrize('field, value', [
    ('storage_ids', ()), ('storage_ids', (True,)), ('storage_ids', [13]),
    ('storage_ids', (0,)), ('default_storage_id', False),
    ('file_expire_seconds', -1), ('fetched_at', float('nan')),
    ('fetched_at', True), ('fetched_at', -1),
])
def test_save_validates_metadata_before_writing(tmp_path, field, value):
    """保存重新校验冻结模型的实际值，拒绝非法记录且不修改输入。"""
    cache, identity, metadata = make_cache(tmp_path)
    assert cache.save(identity, metadata)
    before = record_path(tmp_path, identity).read_bytes()
    fields = dict(vars(metadata))
    fields[field] = value
    invalid = StorageMetadata(**fields)
    original = dict(vars(invalid))
    assert cache.save(identity, invalid) is False
    assert vars(invalid) == original
    assert record_path(tmp_path, identity).read_bytes() == before
    assert cache.take_warnings() == (WARN_SAVE,)


def test_save_size_limit_precedes_temporary_file(tmp_path, monkeypatch):
    """序列化后超过字节上限，必须在创建临时文件之前拒绝。"""
    module = cache_module()
    cache, identity, metadata = make_cache(tmp_path)
    assert cache.save(identity, metadata)
    before = record_path(tmp_path, identity).read_bytes()

    def forbidden(*args, **kwargs):
        """超限记录不能创建任何临时文件。"""
        pytest.fail('超限记录创建了临时文件')

    monkeypatch.setattr(module.tempfile, 'mkstemp', forbidden)
    huge = StorageMetadata(tuple(range(1, 20001)), 13, None, 1000)
    assert cache.save(identity, huge) is False
    assert record_path(tmp_path, identity).read_bytes() == before
    assert cache.take_warnings() == (WARN_SAVE,)


def test_atomic_replace_uses_complete_same_directory_temp(
        tmp_path, monkeypatch):
    """替换前旧记录完整、临时文件已完整落盘且位于同目录。"""
    module = cache_module()
    cache, identity, metadata = make_cache(tmp_path)
    assert cache.save(identity, metadata)
    destination = record_path(tmp_path, identity)
    original = destination.read_bytes()
    updated = StorageMetadata((99,), None, 0, 1000)
    replace = os.replace
    events = []
    fsync = os.fsync

    def sync(descriptor):
        """执行真实 fsync 并记录先于替换的顺序。"""
        fsync(descriptor)
        events.append('fsync')

    def replace_checked(source, target):
        """检查完整临时内容和旧文件，再执行真实原子替换。"""
        assert Path(source).parent == tmp_path
        assert Path(target) == destination
        assert Path(source) != destination
        assert destination.read_bytes() == original
        assert json.loads(Path(source).read_bytes())['storage_ids'] == [99]
        assert events == ['fsync']
        events.append('replace')
        replace(source, target)

    monkeypatch.setattr(module.os, 'fsync', sync)
    monkeypatch.setattr(module.os, 'replace', replace_checked)
    assert cache.save(identity, updated)
    assert events == ['fsync', 'replace']
    assert cache.load(identity) == updated
    assert {p.name for p in tmp_path.iterdir()} == {KEY_NAME, destination.name}


@pytest.mark.parametrize('phase', ['mkstemp', 'fdopen', 'write', 'short',
                                   'flush', 'fsync', 'replace'])
@pytest.mark.parametrize('error_type', [
    OSError, KeyboardInterrupt, SystemExit,
])
def test_save_failure_keeps_old_record_and_cleans_owned_temp(
        tmp_path, monkeypatch, phase, error_type, caplog, capsys):
    """各写入边界失败只清理本次临时文件，控制信号保持对象身份。"""
    module = cache_module()
    cache, identity, metadata = make_cache(tmp_path)
    assert cache.save(identity, metadata)
    unrelated = tmp_path / 'unrelated-user-file'
    unrelated.write_bytes(b'keep')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    error = error_type(PATH_SECRET + RESPONSE_SECRET)
    handles = []
    descriptors = []
    fdopen = os.fdopen

    def fail(*args, **kwargs):
        """在一个普通文件边界抛出原合成异常。"""
        raise error

    def wrap(descriptor, mode):
        """用真实文件验证短写、写入或 flush 后的释放。"""
        descriptors.append(descriptor)
        if phase == 'fdopen':
            raise error
        wrapped = ObservedFile(fdopen(descriptor, mode), phase, error,
                               short=phase == 'short')
        handles.append(wrapped)
        return wrapped

    with monkeypatch.context() as patch:
        patch.setattr(module.os, 'fdopen', wrap)
        if phase == 'mkstemp':
            patch.setattr(module.tempfile, 'mkstemp', fail)
        elif phase in ('fsync', 'replace'):
            patch.setattr(module.os, phase, fail)
        updated = StorageMetadata((99,), None, None, 1000)
        if isinstance(error, Exception) or phase == 'short':
            assert cache.save(identity, updated) is False
        else:
            with pytest.raises(error_type) as caught:
                cache.save(identity, updated)
            assert caught.value is error
        assert updated == StorageMetadata((99,), None, None, 1000)
    assert all(handle.stream.closed for handle in handles)
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    warnings = assert_safe(cache, caplog, capsys)
    if isinstance(error, Exception) or phase == 'short':
        assert warnings == (WARN_SAVE,)


@pytest.mark.parametrize('size', [0, 1, 31, 33, 100])
def test_corrupt_key_is_not_replaced(tmp_path, size):
    """损坏密钥保持原样，不自动生成新密钥或可复用身份。"""
    key = tmp_path / KEY_NAME
    original = b'x' * size
    key.write_bytes(original)
    cache = cache_module().StorageCache(tmp_path)
    assert cache.identity('beeimg_cn', SECRET) is None
    assert cache.identity('boltp', '') is None
    assert key.read_bytes() == original
    assert cache.take_warnings() == (WARN_UNAVAILABLE,)


@pytest.mark.parametrize('phase', [
    'open', 'write', 'short', 'flush', 'fsync', 'random',
])
@pytest.mark.parametrize('error_type', [
    OSError, KeyboardInterrupt, SystemExit,
])
def test_key_creation_failure_cleanup_and_signal(
        tmp_path, monkeypatch, phase, error_type, caplog, capsys):
    """密钥创建失败不留下自己的半成品，控制信号和资源释放可验证。"""
    module = cache_module()
    cache = module.StorageCache(tmp_path)
    unrelated = tmp_path / 'unrelated'
    unrelated.write_bytes(b'keep')
    error = error_type(PATH_SECRET)
    handles = []

    def fail(*args, **kwargs):
        """注入单次密钥初始化故障。"""
        raise error

    def wrap(path, mode):
        """排他创建真实文件，然后在选定操作注入故障。"""
        if phase == 'open':
            raise error
        wrapped = ObservedFile(builtins.open(path, mode), phase, error,
                               short=phase == 'short')
        handles.append(wrapped)
        return wrapped

    with monkeypatch.context() as patch:
        patch.setattr(module, 'open', wrap, raising=False)
        if phase == 'fsync':
            patch.setattr(module.os, 'fsync', fail)
        elif phase == 'random':
            patch.setattr(module.os, 'urandom', fail)
        if isinstance(error, Exception) or phase == 'short':
            assert cache.identity('beeimg_cn', SECRET) is None
        else:
            with pytest.raises(error_type) as caught:
                cache.identity('beeimg_cn', SECRET)
            assert caught.value is error
    assert all(handle.stream.closed for handle in handles)
    assert {p.name for p in tmp_path.iterdir()} == {'unrelated'}
    assert unrelated.read_bytes() == b'keep'
    warnings = assert_safe(cache, caplog, capsys)
    if isinstance(error, Exception) or phase == 'short':
        assert warnings == (WARN_UNAVAILABLE,)


@pytest.mark.parametrize('winner_size', [32, 3])
def test_concurrent_key_winner_is_read_once_not_overwritten(
        tmp_path, monkeypatch, winner_size):
    """排他创建竞争只读取获胜文件，半成品安全回退而不等待或覆盖。"""
    module = cache_module()
    key_path = tmp_path / KEY_NAME
    winner = b'w' * winner_size
    modes = []

    def competing_open(path, mode):
        """在排他创建前模拟另一实例赢得密钥文件。"""
        modes.append(mode)
        if mode == 'xb':
            key_path.write_bytes(winner)
            raise FileExistsError(PATH_SECRET)
        return builtins.open(path, mode)

    monkeypatch.setattr(module, 'open', competing_open, raising=False)
    cache = module.StorageCache(tmp_path)
    identity = cache.identity('beeimg_cn', SECRET)
    assert modes == ['xb', 'rb']
    assert key_path.read_bytes() == winner
    if winner_size == 32:
        digest = hmac.new(winner, b'beeimg_cn\0' + SECRET.encode(),
                          hashlib.sha256).hexdigest()
        assert identity == 'beeimg_cn-' + digest
    else:
        assert identity is None
        assert cache.take_warnings() == (WARN_UNAVAILABLE,)


@pytest.mark.parametrize('operation', ['key', 'record'])
@pytest.mark.parametrize('error_type', [
    OSError, KeyboardInterrupt, SystemExit,
])
def test_read_failure_releases_handles_without_secret_leak(
        tmp_path, monkeypatch, operation, error_type, caplog, capsys):
    """密钥或记录读取故障关闭真实文件，普通异常不保留敏感内容。"""
    module = cache_module()
    cache, identity, metadata = make_cache(tmp_path)
    assert cache.save(identity, metadata)
    if operation == 'key':
        cache = module.StorageCache(tmp_path)
    error = error_type(PATH_SECRET + RESPONSE_SECRET)
    handles = []

    def broken_read(path, mode):
        """保留真实打开与存在性语义，只在读取时失败。"""
        stream = builtins.open(path, mode)
        wrapped = ObservedFile(stream, 'read', error)
        handles.append(wrapped)
        return wrapped

    monkeypatch.setattr(module, 'open', broken_read, raising=False)
    args = ('beeimg_cn', SECRET) if operation == 'key' else (identity,)
    method = cache.identity if operation == 'key' else cache.load
    if isinstance(error, Exception):
        assert method(*args) is None
    else:
        with pytest.raises(error_type) as caught:
            method(*args)
        assert caught.value is error
    assert handles
    assert all(handle.stream.closed for handle in handles)
    assert (tmp_path / KEY_NAME).stat().st_size == 32
    warnings = assert_safe(cache, caplog, capsys)
    if isinstance(error, Exception):
        assert warnings == ((WARN_UNAVAILABLE,) if operation == 'key'
                            else (WARN_READ,))


def test_invalidate_removes_only_target_and_missing_is_success(tmp_path):
    """失效仅删除目标，保留密钥和其他身份，重复失效仍成功。"""
    cache, identity, metadata = make_cache(tmp_path)
    other = cache.identity('boltp', OTHER_SECRET)
    assert cache.save(identity, metadata)
    assert cache.save(other, metadata)
    key = (tmp_path / KEY_NAME).read_bytes()
    assert cache.invalidate(identity)
    assert not record_path(tmp_path, identity).exists()
    assert cache.invalidate(identity)
    assert cache.load(identity) is None
    assert cache.load(other) == metadata
    assert (tmp_path / KEY_NAME).read_bytes() == key
    assert cache.take_warnings() == ()


def test_failed_invalidation_disables_across_instances_and_aliases(
        tmp_path, monkeypatch, caplog, capsys):
    """删除失败永久标记本进程的规范根与身份，保存成功也不恢复读盘。"""
    module = cache_module()
    cache, identity, metadata = make_cache(tmp_path)
    other = cache.identity('boltp', OTHER_SECRET)
    assert cache.save(identity, metadata)
    assert cache.save(other, metadata)
    destination = record_path(tmp_path, identity)
    unlink = Path.unlink

    def deny_target(path, *args, **kwargs):
        """仅拒绝目标删除，不干扰临时文件或其他身份。"""
        if path == destination:
            raise PermissionError(PATH_SECRET)
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', deny_target)
        assert cache.invalidate(identity) is False
        assert cache.invalidate(identity) is False
    assert destination.exists()
    assert cache.load(identity) is None
    assert cache.save(identity, metadata)
    assert cache.load(identity) is None
    alias = tmp_path / 'unused' / '..'
    fresh = module.StorageCache(alias, clock=lambda: 1000)
    assert fresh.load(identity) is None
    assert fresh.load(other) == metadata
    separate, separate_id, _ = make_cache(tmp_path / 'separate')
    assert separate.save(separate_id, metadata)
    assert separate.load(separate_id) == metadata
    assert assert_safe(cache, caplog, capsys) == (WARN_INVALIDATE,)
    disabled = module._DISABLED_IDENTITIES
    assert disabled
    assert all(isinstance(key, tuple) and len(key) == 2 for key in disabled)
    assert SECRET not in repr(disabled)
    assert OTHER_SECRET not in repr(disabled)


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_invalidate_control_signal_identity(
        tmp_path, monkeypatch, signal_type):
    """删除中的控制信号原样传播，既有记录不被意外改变。"""
    cache, identity, metadata = make_cache(tmp_path)
    assert cache.save(identity, metadata)
    signal = signal_type(PATH_SECRET)

    def interrupted(*args, **kwargs):
        """模拟删除被用户控制信号打断。"""
        raise signal

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', interrupted)
        with pytest.raises(signal_type) as caught:
            cache.invalidate(identity)
        assert caught.value is signal
    assert record_path(tmp_path, identity).exists()


def test_warnings_are_ordered_deduplicated_and_drained(tmp_path, monkeypatch):
    """不同失败类别按首次出现顺序保存，取出后清空。"""
    cache, identity, metadata = make_cache(tmp_path)
    record_path(tmp_path, identity).write_bytes(RESPONSE_SECRET.encode())
    assert cache.load(identity) is None
    assert cache.load(identity) is None
    invalid = StorageMetadata((), None, None, 1000)
    assert cache.save(identity, invalid) is False
    assert cache.save(identity, invalid) is False

    def denied(*args, **kwargs):
        """模拟目标记录删除被拒绝。"""
        raise PermissionError(PATH_SECRET)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', denied)
        assert cache.invalidate(identity) is False
    assert cache.take_warnings() == (WARN_READ, WARN_SAVE, WARN_INVALIDATE)
    assert cache.take_warnings() == ()
    assert metadata == StorageMetadata((13,), None, 3600, 1000.0)


@pytest.mark.parametrize('provider', ['beeimg_cn', 'boltp'])
@pytest.mark.parametrize('default', [13, 99])
def test_v2_legacy_raw_cache_normalization(tmp_path, provider, default):
    """直接写入旧原始六字段记录，验证读盘先校验再归一默认值为 None。"""
    cache = cache_module().StorageCache(tmp_path, clock=lambda: 1000.0)
    identity = cache.identity(provider, SECRET)
    data = legacy_payload(provider, 1000.0, default=default)
    original_bytes = json.dumps(data).encode('utf-8')
    record_path(tmp_path, identity).write_bytes(original_bytes)
    loaded = cache.load(identity)
    assert loaded is not None
    assert loaded.default_storage_id is None
    assert loaded.fetched_at == 1000.0
    assert loaded.storage_ids == (13, 14)
    assert record_path(tmp_path, identity).read_bytes() == original_bytes
    assert cache.take_warnings() == ()


@pytest.mark.parametrize('provider', ['beeimg_cn', 'boltp'])
@pytest.mark.parametrize('value', [0, -1, True, '13', [], {}, 13.0])
def test_v2_legacy_raw_cache_rejects_invalid_default(tmp_path, provider, value):
    """非法默认编号保持校验失败，不因为归一而放宽校验。"""
    cache = cache_module().StorageCache(tmp_path, clock=lambda: 1000.0)
    identity = cache.identity(provider, SECRET)
    data = legacy_payload(provider, 1000.0)
    data['default_storage_id'] = value
    write_payload(tmp_path, identity, data)
    assert cache.load(identity) is None
    assert cache.take_warnings() == (WARN_READ,)


def test_v2_cache_save_normalizes_default(tmp_path):
    """新保存将 validated.default_storage_id（归一后的 None）回写到 payload。"""
    cache = cache_module().StorageCache(tmp_path, clock=lambda: 1000.0)
    bee_identity = cache.identity('beeimg_cn', SECRET)
    boltp_identity = cache.identity('boltp', SECRET)

    # 调用方传入 bee 13，验证落盘后被归一为 None
    metadata_bee = StorageMetadata((13, 99), 13, 3600, 1000.0)
    assert cache.save(bee_identity, metadata_bee)
    saved_bee = json.loads(record_path(tmp_path, bee_identity).read_bytes())
    assert set(saved_bee.keys()) == {'version', 'provider', 'fetched_at',
                                     'storage_ids', 'default_storage_id',
                                     'file_expire_seconds'}
    assert saved_bee['version'] == 1
    assert saved_bee['default_storage_id'] is None
    assert saved_bee['fetched_at'] == 1000.0

    # 调用方传入 boltp 14，验证落盘后同样被归一为 None
    metadata_boltp = StorageMetadata((13, 14), 14, 0, 1000.0)
    assert cache.save(boltp_identity, metadata_boltp)
    saved_boltp = json.loads(record_path(tmp_path, boltp_identity).read_bytes())
    assert set(saved_boltp.keys()) == {'version', 'provider', 'fetched_at',
                                       'storage_ids', 'default_storage_id',
                                       'file_expire_seconds'}
    assert saved_boltp['version'] == 1
    assert saved_boltp['default_storage_id'] is None
    assert saved_boltp['fetched_at'] == 1000.0
    assert cache.take_warnings() == ()


@pytest.mark.parametrize('provider', ['beeimg_cn', 'boltp'])
@pytest.mark.parametrize('age, expect_hit', [
    (-1, False), (0, True), (86399, True), (86400, False)])
def test_v2_cache_ttl_and_fields_unchanged(tmp_path, provider, age, expect_hit):
    """TTL 边界、六字段校验保持原有行为，归一不放宽校验。"""
    now = 1000.0
    cache = cache_module().StorageCache(tmp_path, clock=lambda: now + age)
    identity = cache.identity(provider, SECRET)
    data = legacy_payload(provider, now, default=99)
    write_payload(tmp_path, identity, data)
    result = cache.load(identity)
    if expect_hit:
        assert result is not None
        assert result.default_storage_id is None
    else:
        assert result is None

    # 少字段
    for missing in ('version', 'storage_ids', 'default_storage_id',
                    'fetched_at'):
        bad = dict(data)
        del bad[missing]
        write_payload(tmp_path, identity, bad)
        assert cache.load(identity) is None

    # 多字段
    bad = dict(data)
    bad['extra'] = 'extra'
    write_payload(tmp_path, identity, bad)
    assert cache.load(identity) is None
    assert cache.take_warnings() == (WARN_READ,)
