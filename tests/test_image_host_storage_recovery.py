#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证事件内协调合同，仅使用合成凭证、临时缓存和查询边界替身。"""

from copy import deepcopy
from datetime import datetime
from importlib import import_module
from importlib.util import find_spec
from inspect import signature
import json
import os
from pathlib import Path
import socket
import time

import pytest
import requests

from modules.image_host.core import ImageHostError
from modules.image_host.diagnostics import safe_current_message
from modules.image_host.expiration import ExpirationDecision
from modules.image_host.storage import StorageMetadata
from modules.image_host.storage_cache import StorageCache


SECRET = 'FAKE_EVENT_PRIMARY_7319'
BACKUP = 'FAKE_EVENT_BACKUP_8420'
ENV_NAME = 'SYNTHETIC_EVENT_CREDENTIAL'
PROVIDER = 'beeimg_cn'


def context_module():
    """缺少实现时产生功能断言失败，而不是测试收集错误。"""
    name = 'modules.image_host.context'
    assert find_spec(name) is not None, '缺少事件级 UploadContext'
    return import_module(name)


def metadata(ids=(13, 14), default=14, now=1000):
    """构造既有冻结模型，不包含账号响应或上传结果。"""
    return StorageMetadata(ids, default, 3600, now)


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """隔离默认缓存根并禁止任何真实请求及 POST。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))

    def forbidden(*args, **kwargs):
        """意外网络访问立即令测试失败。"""
        pytest.fail('事件上下文测试禁止真实请求和 POST')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """延迟到测试解包时连接真实缓存及查询边界替身。"""
    def build():
        """在测试执行阶段断言实现存在，避免夹具初始化错误。"""
        module = context_module()
        now = [1000.0]
        cache = StorageCache(tmp_path / 'cache', clock=lambda: now[0])
        calls = []
        replies = []

        def fetch(provider, token, *, now):
            """记录查询时点，按队列返回模型或抛出合成异常。"""
            calls.append((provider, token, now))
            reply = replies.pop(0) if replies else metadata(now=now)
            if isinstance(reply, BaseException):
                raise reply
            return reply

        monkeypatch.setattr(module, 'fetch_storage_metadata', fetch)
        context = module.UploadContext(cache=cache, clock=lambda: now[0])
        yield from (module, context, cache, calls, replies, now)

    return build()


def assert_safe(error, code):
    """检查固定异常展示及无底层异常链。"""
    assert type(error) is ImageHostError
    assert error.code == code
    assert error.__cause__ is None
    assert error.__context__ is None
    for secret in (SECRET, BACKUP):
        assert secret not in str(error) + repr(error) + repr(error.args)


def test_constructor_contract_has_no_io(monkeypatch, tmp_path):
    """构造及读取空告警不访问磁盘、随机源或时钟。"""
    module = context_module()
    params = signature(module.UploadContext).parameters
    assert list(params) == ['diagnostics', 'cache', 'clock']
    assert all(p.kind is p.KEYWORD_ONLY for p in params.values())
    assert params['diagnostics'].default is False
    assert params['cache'].default is None
    assert params['clock'].default is time.time

    def forbidden(*args, **kwargs):
        """惰性初始化前不允许发生外部操作。"""
        pytest.fail('构造发生外部操作')

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'mkdir', forbidden)
        patch.setattr(os, 'urandom', forbidden)
        patch.setattr(StorageCache, 'identity', forbidden)
        ctx = module.UploadContext(clock=forbidden)
        assert ctx.take_warnings() == ()
        ctx.close()
    assert list(tmp_path.iterdir()) == []


def test_now_reads_current_clock_once_without_io(monkeypatch, tmp_path):
    """公开读钟逐次返回浮点值，不初始化状态或访问外部资源。"""
    module = context_module()
    current = [0]
    calls = []

    def clock():
        """记录每次注入时钟读取并返回可变时刻。"""
        calls.append(current[0])
        return current[0]

    def forbidden(*args, **kwargs):
        """禁止公开读钟触发缓存、查询、随机源或目录操作。"""
        pytest.fail('公开读钟发生外部操作')

    class RejectCache:
        """拒绝任何缓存接口访问。"""

        def __getattr__(self, name):
            """将意外缓存访问直接呈现为测试失败。"""
            forbidden()

    cache = RejectCache()
    ctx = module.UploadContext(cache=cache, clock=clock)
    with monkeypatch.context() as patch:
        patch.setattr(module, 'fetch_storage_metadata', forbidden)
        patch.setattr(module, 'resolve_token', forbidden)
        patch.setattr(time, 'time', forbidden)
        patch.setattr(os, 'urandom', forbidden)
        patch.setattr(os, 'mkdir', forbidden)
        patch.setattr(os, 'makedirs', forbidden)
        patch.setattr(os, 'listdir', forbidden)
        patch.setattr(os, 'scandir', forbidden)
        patch.setattr(Path, 'mkdir', forbidden)
        patch.setattr(Path, 'open', forbidden)
        patch.setattr(Path, 'stat', forbidden)
        patch.setattr(Path, 'iterdir', forbidden)
        for index, value in enumerate((0, 1000, 1001.25), start=1):
            current[0] = value
            result = ctx.now()
            assert type(result) is float
            assert result == value
            assert len(calls) == index
            assert calls[-1] == value
            assert ctx._images == ctx._entries == ctx._credentials == {}
            assert ctx._corrected == set()
            assert ctx._key is None
            assert ctx._secrets == ()
            assert ctx._warnings == []
            assert ctx._collected is False
            assert ctx._closed is False
            assert ctx._cache is cache
            assert ctx._clock is clock
    assert list(tmp_path.iterdir()) == []
    ctx.close()


def test_now_shares_image_clock_without_changing_existing_state(rig):
    """读钟与图片起点同源，且不改变已有元数据、纠错及期限状态。"""
    _, ctx, _, calls, _, now = rig
    started_at = ctx.now()
    assert ctx._images == {}
    image = ctx.start_image()
    assert image.started_at == started_at == now[0]
    ctx.prepare_storage(image, 0, PROVIDER, SECRET,
                        manual_present=True, manual_value=99)
    ctx.record_expiration(image, 0, PROVIDER, SECRET,
                          ExpirationDecision(1100, '合成显示', True))
    images = {key: dict(value) for key, value in ctx._images.items()}
    entries = deepcopy(ctx._entries)
    corrected = ctx._corrected.copy()
    key = ctx._key
    query_calls = list(calls)
    now[0] = 1020.5
    assert ctx.now() == now[0]
    assert image.started_at == started_at
    assert ctx._images == images
    assert ctx._entries == entries
    assert ctx._corrected == corrected
    assert ctx._key is key
    assert calls == query_calls
    assert ctx._closed is False


@pytest.mark.parametrize('value', [
    None, False, True, -1, float('nan'), float('inf'), float('-inf'), SECRET,
])
def test_now_rejects_invalid_clock_values_safely(rig, value):
    """非法时刻复用固定存储阶段错误，不泄漏假秘密或异常链。"""
    module, _, cache, calls, _, _ = rig
    ctx = module.UploadContext(cache=cache, clock=lambda: value)
    with pytest.raises(ImageHostError) as caught:
        ctx.now()
    assert_safe(caught.value, 'config_error')
    assert caught.value.stage == 'storage'
    assert caught.value.diagnostic is None
    assert caught.value.http_status is None
    assert ctx._images == ctx._entries == {}
    assert calls == []


@pytest.mark.parametrize('error_type', [RuntimeError, OSError])
def test_now_converts_clock_exceptions_without_leaking(rig, error_type):
    """普通时钟异常在处理器外转换，不保留底层异常链。"""
    module, _, cache, calls, _, _ = rig
    original = error_type(SECRET + BACKUP)

    def clock():
        """抛出包含合成秘密的普通时钟故障。"""
        raise original

    ctx = module.UploadContext(cache=cache, clock=clock)
    with pytest.raises(ImageHostError) as caught:
        ctx.now()
    assert_safe(caught.value, 'config_error')
    assert caught.value.stage == 'storage'
    assert caught.value.diagnostic is None
    assert caught.value.http_status is None
    assert calls == []


def test_now_rejects_closed_context_before_reading_clock(rig):
    """正常读钟不关闭事件，关闭后固定拒绝且不再读取注入时钟。"""
    module, _, cache, _, _, _ = rig
    calls = []

    def clock():
        """记录关闭前后实际发生的时钟读取。"""
        calls.append(1000)
        return 1000

    ctx = module.UploadContext(cache=cache, clock=clock)
    assert ctx.now() == 1000.0
    assert ctx._closed is False
    ctx.close()
    expected = ImageHostError('config_error', '')
    for _ in range(2):
        with pytest.raises(ImageHostError) as caught:
            ctx.now()
        assert_safe(caught.value, 'config_error')
        assert caught.value.stage == expected.stage
        assert str(caught.value) == str(expected)
    assert calls == [1000]


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_now_propagates_clock_control_signals_unchanged(rig, signal_type):
    """公开读钟保留控制信号原对象，不转换为业务错误。"""
    module, _, cache, calls, _, _ = rig
    signal = signal_type(SECRET)

    def clock():
        """从真实注入时钟边界抛出控制信号。"""
        raise signal

    ctx = module.UploadContext(cache=cache, clock=clock)
    with pytest.raises(signal_type) as caught:
        ctx.now()
    assert caught.value is signal
    assert ctx._closed is False
    assert ctx._images == ctx._entries == {}
    assert calls == []


def test_event_success_reused_and_identity_isolated(rig, caplog, capsys):
    """同身份跨图片复用，站点、匿名及不同凭证分别查询。"""
    _, ctx, _, calls, _, _ = rig
    identities = [(PROVIDER, SECRET), (PROVIDER, BACKUP),
                  (PROVIDER, ''), ('boltp', SECRET)]
    for provider, token in identities:
        first = ctx.get_metadata(provider, token)
        assert ctx.get_metadata(provider, token) is first
    assert len(calls) == 4
    assert SECRET not in repr(ctx)
    assert BACKUP not in repr(ctx)
    assert not caplog.records
    assert capsys.readouterr() == ('', '')


def test_disk_reuse_across_events_and_expiration(rig):
    """下一事件命中真实磁盘缓存，到期查询使用注入时刻。"""
    module, ctx, cache, calls, _, now = rig
    first = ctx.get_metadata(PROVIDER, SECRET)
    ctx.close()
    second = module.UploadContext(cache=cache, clock=lambda: now[0])
    assert second.get_metadata(PROVIDER, SECRET) == first
    assert len(calls) == 1
    now[0] += 86400
    third = module.UploadContext(cache=cache, clock=lambda: now[0])
    assert third.get_metadata(PROVIDER, SECRET).fetched_at == now[0]
    assert calls[-1][2] == now[0]
    assert len(calls) == 2


@pytest.mark.parametrize('phase', ['identity', 'save'])
def test_cache_failure_does_not_prevent_event_success(rig, monkeypatch, phase):
    """无法持久化仍可查询及事件内复用，并只产生固定告警。"""
    _, ctx, cache, calls, _, _ = rig

    def fail(*args, **kwargs):
        """模拟真实缓存内部文件边界普通故障。"""
        raise OSError(SECRET)

    if phase == 'identity':
        monkeypatch.setattr(cache, '_prepare_key', fail)
    else:
        monkeypatch.setattr(cache, '_write_atomic', fail)
    result = ctx.get_metadata(PROVIDER, SECRET)
    assert ctx.get_metadata(PROVIDER, SECRET) is result
    assert len(calls) == 1
    expected = ('存储元数据缓存不可用' if phase == 'identity'
                else '存储元数据缓存保存失败')
    assert ctx.take_warnings() == (expected,)
    assert ctx.take_warnings() == ()


@pytest.mark.parametrize('known', [False, True])
def test_failure_snapshot_reused_but_next_event_requeries(rig, known):
    """事件失败保存安全字段而非异常，下个事件允许重新查询。"""
    module, ctx, cache, calls, replies, _ = rig
    original = (ImageHostError('http_failed', '', stage='profile',
                               http_status=403, diagnostic=SECRET)
                if known else RuntimeError(SECRET))
    replies.append(original)
    errors = []
    for correction in (False, False, True, True):
        with pytest.raises(ImageHostError) as caught:
            ctx.get_metadata(PROVIDER, SECRET, correction=correction)
        error = caught.value
        assert_safe(error, 'http_failed' if known else 'storage_lookup_failed')
        assert error is not original
        assert error.diagnostic is None
        if known:
            assert (error.stage, error.http_status) == ('profile', 403)
        errors.append(error)
    assert len({id(error) for error in errors}) == 4
    assert len(calls) == 1
    new = module.UploadContext(cache=cache, clock=lambda: 1000)
    assert new.get_metadata(PROVIDER, SECRET) == metadata()
    assert len(calls) == 2


def test_correction_bypasses_success_and_reuses_failed_generation(rig):
    """纠错绕过旧成功；失败后重复纠错或失效不得复活旧记录。"""
    _, ctx, cache, calls, replies, _ = rig
    ctx.get_metadata(PROVIDER, SECRET)
    replies.append(RuntimeError(SECRET))
    for index in range(3):
        if index:
            ctx.invalidate(PROVIDER, SECRET)
        with pytest.raises(ImageHostError):
            ctx.get_metadata(PROVIDER, SECRET, correction=True)
    assert len(calls) == 2
    assert cache.load(cache.identity(PROVIDER, SECRET)) is None
    with pytest.raises(ImageHostError):
        ctx.get_metadata(PROVIDER, SECRET)
    assert len(calls) == 2


def test_invalidate_once_per_version_and_allow_later_real_failure(
        rig, monkeypatch):
    """重复失效不重复删除，成功后的新版本仍可再次失效刷新。"""
    _, ctx, cache, calls, replies, _ = rig
    deletes = []
    invalidate = cache.invalidate

    def tracked(identity):
        """记录实际磁盘失效次数，不替代删除行为。"""
        deletes.append(identity)
        return invalidate(identity)

    monkeypatch.setattr(cache, 'invalidate', tracked)
    ctx.get_metadata(PROVIDER, SECRET)
    ctx.invalidate(PROVIDER, SECRET)
    ctx.invalidate(PROVIDER, SECRET)
    replies.append(metadata((21,), 21))
    refreshed = ctx.get_metadata(PROVIDER, SECRET, correction=True)
    assert refreshed.storage_ids == (21,)
    assert len(deletes) == 1
    ctx.invalidate(PROVIDER, SECRET)
    replies.append(metadata((22,), 22))
    refreshed = ctx.get_metadata(PROVIDER, SECRET, correction=True)
    assert refreshed.storage_ids == (22,)
    assert len(deletes) == 2
    assert len(calls) == 3


def test_failed_disk_delete_blocks_next_context(rig, monkeypatch, tmp_path):
    """真实删除失败触发底层跨实例禁读，刷新失败不能回退旧数据。"""
    module, ctx, cache, calls, replies, _ = rig
    ctx.get_metadata(PROVIDER, SECRET)
    identity = cache.identity(PROVIDER, SECRET)
    target = tmp_path / 'cache' / (identity + '.json')
    unlink = Path.unlink

    def denied(path, *args, **kwargs):
        """仅拒绝目标元数据删除，不干扰其他临时文件。"""
        if path == target:
            raise PermissionError(SECRET)
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', denied)
        ctx.invalidate(PROVIDER, SECRET)
    replies.append(RuntimeError(SECRET))
    with pytest.raises(ImageHostError):
        ctx.get_metadata(PROVIDER, SECRET, correction=True)
    assert ctx.take_warnings() == ('存储元数据缓存失效失败',)
    fresh_cache = StorageCache(tmp_path / 'cache', clock=lambda: 1000)
    fresh = module.UploadContext(cache=fresh_cache, clock=lambda: 1000)
    replies.append(metadata((21,), 21))
    assert fresh.get_metadata(PROVIDER, SECRET).storage_ids == (21,)
    assert len(calls) == 3
    assert identity not in repr(ctx)


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_query_control_signals_keep_identity_and_are_not_cached(
        rig, signal_type):
    """控制信号原对象传播，不被转换为可复用业务失败。"""
    _, ctx, _, calls, replies, _ = rig
    signal = signal_type(SECRET)
    replies.append(signal)
    with pytest.raises(signal_type) as caught:
        ctx.get_metadata(PROVIDER, SECRET)
    assert caught.value is signal
    assert ctx.get_metadata(PROVIDER, SECRET) == metadata()
    assert len(calls) == 2


@pytest.mark.parametrize('disk', [False, True])
@pytest.mark.parametrize('present, manual, used', [
    (False, None, False), (True, 13, False), (True, 99, True),
    (True, None, True), (True, {'invalid': SECRET}, True),
])
def test_selection_budget_contract(rig, disk, present, manual, used):
    """旧缓存无效手填强制刷新，刚查得的列表无效手填不重复查询。"""
    _, ctx, cache, calls, _, _ = rig
    if disk:
        assert cache.save(cache.identity(PROVIDER, SECRET), metadata())
    original = deepcopy(manual)
    image = ctx.start_image()
    selected = ctx.prepare_storage(
        image, 0, PROVIDER, SECRET,
        manual_present=present, manual_value=manual)
    state = ctx.storage_state(image, 0, PROVIDER, SECRET)
    assert selected.storage_id == (13 if present and manual == 13 else 14)
    assert state.selection == selected
    assert state.correction_used is used
    assert len(calls) == (int(used) if disk else 1)
    assert manual == original
    for _ in range(3):
        following = ctx.start_image()
        assert ctx.prepare_storage(
            following, 0, PROVIDER, SECRET,
            manual_present=present, manual_value=manual) == selected
        assert ctx.storage_state(
            following, 0, PROVIDER, SECRET).correction_used is used
    assert len(calls) == (int(used) if disk else 1)


def test_budget_and_expiration_are_per_image_and_configuration(rig):
    """同身份共享元数据而不混用图片或配置项的预算和期限历史。"""
    _, ctx, _, calls, _, now = rig
    first = ctx.start_image()
    now[0] = 1010
    second = ctx.start_image()
    assert (first.started_at, second.started_at) == (1000, 1010)
    for image in (first, second):
        for index in (0, 1):
            ctx.prepare_storage(image, index, PROVIDER, SECRET)
    decision = ExpirationDecision(1100, '合成显示', True)
    ctx.record_expiration(first, 0, PROVIDER, SECRET, decision)
    state = ctx.storage_state(first, 0, PROVIDER, SECRET)
    assert (state.previous_deadline, state.previous_shortened) == (1100, True)
    assert ctx.begin_recovery(first, 0, PROVIDER, SECRET) is True
    assert ctx.begin_recovery(first, 0, PROVIDER, SECRET) is False
    for image, index in ((first, 1), (second, 0), (second, 1)):
        other = ctx.storage_state(image, index, PROVIDER, SECRET)
        assert not other.correction_used
        assert other.previous_deadline is None
        assert other.previous_shortened is None
    ctx.prepare_storage(first, 0, PROVIDER, SECRET)
    assert len(calls) == 2
    assert ctx.storage_state(first, 0, PROVIDER, SECRET).correction_used


def test_corrected_selection_revalidated_on_metadata_version_change(rig):
    """同配置纠正事实复用，但新元数据必须重新选择可用编号。"""
    _, ctx, _, calls, replies, _ = rig
    first = ctx.start_image()
    selected = ctx.prepare_storage(
        first, 0, PROVIDER, SECRET, manual_present=True, manual_value=[])
    assert selected.storage_id == 14
    old_state = ctx.storage_state(first, 0, PROVIDER, SECRET)
    old_version = old_state.metadata_version
    ctx.invalidate(PROVIDER, SECRET)
    replies.append(metadata((21,), 21))
    ctx.get_metadata(PROVIDER, SECRET, correction=True)
    second = ctx.start_image()
    selected = ctx.prepare_storage(
        second, 0, PROVIDER, SECRET, manual_present=True, manual_value=[])
    assert selected.storage_id == 21
    state = ctx.storage_state(second, 0, PROVIDER, SECRET)
    assert state.metadata_version > old_version
    assert state.correction_used
    assert len(calls) == 2


def test_stale_image_recovery_does_not_invalidate_newer_metadata(rig):
    """另一图片已完成刷新时，旧版本失败只消费自己的预算。"""
    _, ctx, _, calls, replies, _ = rig
    first, second = ctx.start_image(), ctx.start_image()
    for image in (first, second):
        ctx.prepare_storage(image, 0, PROVIDER, SECRET)
    assert ctx.begin_recovery(first, 0, PROVIDER, SECRET)
    replies.append(metadata((21,), 21))
    ctx.prepare_storage(first, 0, PROVIDER, SECRET)
    assert ctx.begin_recovery(second, 0, PROVIDER, SECRET)
    assert ctx.prepare_storage(second, 0, PROVIDER, SECRET).storage_id == 21
    assert len(calls) == 2


def test_manual_refresh_failure_consumes_budget_and_is_reused(rig):
    """手填纠错失败仍消耗预算，同配置后续图片不重复查询。"""
    _, ctx, cache, calls, replies, _ = rig
    assert cache.save(cache.identity(PROVIDER, SECRET), metadata())
    replies.append(RuntimeError(SECRET))
    for _ in range(3):
        image = ctx.start_image()
        with pytest.raises(ImageHostError):
            ctx.prepare_storage(image, 0, PROVIDER, SECRET,
                                manual_present=True, manual_value=99)
        assert ctx.storage_state(image, 0, PROVIDER, SECRET).correction_used
        assert not ctx.begin_recovery(image, 0, PROVIDER, SECRET)
    assert len(calls) == 1


def test_cli_credentials_and_full_chain_secrets_are_one_snapshot(
        rig, monkeypatch):
    """备用站凭证提前脱敏且延迟呈现解析失败，环境改变不改发送值。"""
    module, _, cache, _, _, _ = rig
    monkeypatch.setenv(ENV_NAME, BACKUP)
    missing = 'SYNTHETIC_EVENT_MISSING'
    monkeypatch.delenv(missing, raising=False)
    hosts = [{'provider': PROVIDER, 'token': SECRET},
             {'provider': 'beeimg_cn', 'token': '${' + ENV_NAME + '}'},
             {'provider': PROVIDER, 'token': '${' + missing + '}'}]
    before = deepcopy(hosts)
    ctx = module.UploadContext(diagnostics=True, cache=cache)
    ctx.collect_secrets(hosts)
    monkeypatch.setenv(ENV_NAME, 'FAKE_LATER_VALUE')
    monkeypatch.setenv(missing, 'FAKE_LATE_AVAILABLE')
    assert ctx.resolve_credential(0, hosts[0]) == SECRET
    assert ctx.resolve_credential(1, hosts[1]) == BACKUP
    with pytest.raises(ImageHostError) as caught:
        ctx.resolve_credential(2, hosts[2])
    assert_safe(caught.value, 'missing_environment')
    with ctx.diagnostic_scope():
        message = safe_current_message('失败 ' + SECRET + ' ' + BACKUP)
        assert message == '失败 [已隐藏] [已隐藏]'
    assert hosts == before
    assert os.environ[ENV_NAME] == 'FAKE_LATER_VALUE'
    assert os.environ[missing] == 'FAKE_LATE_AVAILABLE'
    assert SECRET not in repr(ctx)
    assert BACKUP not in repr(ctx)


def test_notification_does_not_preparse_backup_credentials(rig, monkeypatch):
    """关闭诊断的收集入口不解析整链，显式尝试才惰性冻结凭证。"""
    module, ctx, _, _, _, _ = rig
    calls = []
    resolve = module.resolve_token

    def observed(value):
        """观察既有解析器调用，不改变凭证解析行为。"""
        calls.append(value)
        return resolve(value)

    monkeypatch.setattr(module, 'resolve_token', observed)
    hosts = [{'provider': PROVIDER, 'token': SECRET},
             {'provider': PROVIDER, 'token': '${' + ENV_NAME + '}'}]
    ctx.collect_secrets(hosts)
    assert calls == []
    assert ctx.resolve_credential(0, hosts[0]) == SECRET
    assert calls == [SECRET]
    monkeypatch.setenv(ENV_NAME, BACKUP)
    assert ctx.resolve_credential(1, hosts[1]) == BACKUP
    monkeypatch.setenv(ENV_NAME, 'FAKE_CHANGED')
    assert ctx.resolve_credential(1, hosts[1]) == BACKUP
    assert len(calls) == 2


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_nested_diagnostics_restore_and_control_signals_propagate(
        rig, signal_type):
    """内层关闭隔离外层，控制退出恢复 ContextVar 并保持对象身份。"""
    module, disabled, cache, _, _, _ = rig
    enabled = module.UploadContext(diagnostics=True, cache=cache)
    enabled.collect_secrets([{'token': SECRET}])
    signal = signal_type('合成中断')
    with enabled.diagnostic_scope():
        assert safe_current_message(SECRET) == '[已隐藏]'
        with pytest.raises(signal_type) as caught:
            with disabled.diagnostic_scope():
                assert safe_current_message(SECRET) is None
                raise signal
        assert caught.value is signal
        assert safe_current_message(SECRET) == '[已隐藏]'
    assert safe_current_message('普通诊断') is None


@pytest.mark.parametrize('enabled', [False, True])
def test_failure_diagnostic_is_redacted_only_when_enabled(
        rig, enabled, caplog, capsys, tmp_path):
    """失败快照仅保留启用后的安全诊断，展示及缓存不泄漏秘密。"""
    module, _, cache, calls, replies, _ = rig
    ctx = module.UploadContext(diagnostics=enabled, cache=cache,
                               clock=lambda: 1000)
    ctx.collect_secrets([{'token': SECRET}, {'token': BACKUP}])
    replies.append(ImageHostError('business_rejected', '', stage='group',
                                  http_status=200,
                                  diagnostic='失败 ' + SECRET + ' ' + BACKUP))
    for _ in range(2):
        with pytest.raises(ImageHostError) as caught:
            ctx.get_metadata(PROVIDER, SECRET)
        error = caught.value
        assert_safe(error, 'business_rejected')
        assert error.diagnostic == (
            '失败 [已隐藏] [已隐藏]' if enabled else None)
    assert len(calls) == 1
    for path in (tmp_path / 'cache').iterdir():
        data = path.read_bytes()
        assert SECRET.encode() not in data
        assert BACKUP.encode() not in data
    assert not caplog.records
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_credential_control_signals_are_not_deferred(
        rig, monkeypatch, signal_type):
    """预解析只能延迟普通配置失败，不能吞掉控制信号。"""
    module, _, cache, _, _, _ = rig
    ctx = module.UploadContext(diagnostics=True, cache=cache)
    signal = signal_type(SECRET)

    def interrupted(value):
        """从解析边界抛出指定控制信号。"""
        raise signal

    monkeypatch.setattr(module, 'resolve_token', interrupted)
    with pytest.raises(signal_type) as caught:
        ctx.collect_secrets([{'token': SECRET}])
    assert caught.value is signal


def test_close_releases_event_state_and_rejects_reuse(rig):
    """显式关闭幂等，关闭后的身份、图片与凭证入口不可继续使用。"""
    _, ctx, _, _, _, _ = rig
    image = ctx.start_image()
    ctx.resolve_credential(0, {'token': SECRET})
    ctx.prepare_storage(image, 0, PROVIDER, SECRET)
    ctx.close()
    ctx.close()
    operations = [
        lambda: ctx.get_metadata(PROVIDER, SECRET),
        lambda: ctx.invalidate(PROVIDER, SECRET),
        lambda: ctx.start_image(),
        lambda: ctx.resolve_credential(0, {'token': SECRET}),
        lambda: ctx.collect_secrets([]),
        lambda: ctx.storage_state(image, 0, PROVIDER, SECRET),
    ]
    for operation in operations:
        with pytest.raises(ImageHostError) as caught:
            operation()
        assert_safe(caught.value, 'config_error')
    with pytest.raises(ImageHostError):
        with ctx.diagnostic_scope():
            pytest.fail('关闭后仍进入诊断作用域')


def test_context_manager_closes_on_exception(rig):
    """上下文管理器退出释放事件状态，不抑制调用方异常。"""
    module, _, cache, _, _, _ = rig
    signal = KeyboardInterrupt('合成中断')
    with pytest.raises(KeyboardInterrupt) as caught:
        with module.UploadContext(cache=cache) as ctx:
            assert ctx.resolve_credential(0, {'token': SECRET}) == SECRET
            raise signal
    assert caught.value is signal
    with pytest.raises(ImageHostError):
        ctx.start_image()


def test_refresh_can_restore_manual_choice_and_still_consumes_budget(rig):
    """旧列表离表手填在新列表合法时优先使用，纠错预算仍已消费。"""
    _, ctx, cache, calls, replies, _ = rig
    assert cache.save(cache.identity(PROVIDER, SECRET), metadata())
    replies.append(metadata((99, 21), 21))
    for _ in range(2):
        image = ctx.start_image()
        selection = ctx.prepare_storage(
            image, 0, PROVIDER, SECRET,
            manual_present=True, manual_value=99)
        assert selection.storage_id == 99
        assert not selection.replaced_manual
        assert ctx.storage_state(image, 0, PROVIDER, SECRET).correction_used
        assert not ctx.begin_recovery(image, 0, PROVIDER, SECRET)
    assert len(calls) == 1


def test_distinct_configurations_share_metadata_not_manual_budget(rig):
    """同身份的无效手填纠错不消费其他自动配置项的预算。"""
    _, ctx, _, calls, _, _ = rig
    image = ctx.start_image()
    ctx.prepare_storage(image, 0, PROVIDER, SECRET,
                        manual_present=True, manual_value=[])
    ctx.prepare_storage(image, 1, PROVIDER, SECRET)
    assert ctx.storage_state(image, 0, PROVIDER, SECRET).correction_used
    assert not ctx.storage_state(image, 1, PROVIDER, SECRET).correction_used
    assert len(calls) == 1
    assert not ctx.begin_recovery(image, 0, PROVIDER, SECRET)
    assert ctx.begin_recovery(image, 1, PROVIDER, SECRET)
    ctx.prepare_storage(image, 1, PROVIDER, SECRET)
    assert len(calls) == 2


def test_failed_recovery_shared_without_stale_selection_or_extra_query(rig):
    """恢复失败对旧版本图片和新图片复用，任何入口不再返回旧选择。"""
    _, ctx, _, calls, replies, _ = rig
    first, second = ctx.start_image(), ctx.start_image()
    for image in (first, second):
        ctx.prepare_storage(image, 0, PROVIDER, SECRET)
    assert ctx.begin_recovery(first, 0, PROVIDER, SECRET)
    replies.append(RuntimeError(SECRET))
    with pytest.raises(ImageHostError):
        ctx.prepare_storage(first, 0, PROVIDER, SECRET)
    assert ctx.begin_recovery(second, 0, PROVIDER, SECRET)
    for image in (first, second, ctx.start_image()):
        with pytest.raises(ImageHostError) as caught:
            ctx.prepare_storage(image, 0, PROVIDER, SECRET)
        assert_safe(caught.value, 'storage_lookup_failed')
        assert ctx.storage_state(image, 0, PROVIDER, SECRET).selection is None
    assert len(calls) == 2


def test_new_image_can_recover_new_successful_version(rig):
    """恢复成功后的真实新失效仍可刷新，不使用全事件一次性恢复锁。"""
    _, ctx, _, calls, replies, _ = rig
    for storage_id in (21, 22):
        image = ctx.start_image()
        ctx.prepare_storage(image, 0, PROVIDER, SECRET)
        assert ctx.begin_recovery(image, 0, PROVIDER, SECRET)
        replies.append(metadata((storage_id,), storage_id))
        selected = ctx.prepare_storage(image, 0, PROVIDER, SECRET)
        assert selected.storage_id == storage_id
        assert not ctx.begin_recovery(image, 0, PROVIDER, SECRET)
    assert len(calls) == 3


def test_disk_keeps_query_metadata_not_manual_preference(rig):
    """事件选择不覆盖查询默认值或写入图片、配置和成功结果。"""
    _, ctx, cache, _, _, _ = rig
    image = ctx.start_image()
    selected = ctx.prepare_storage(
        image, 0, PROVIDER, SECRET, manual_present=True, manual_value=13)
    assert selected.storage_id == 13
    stored = cache.load(cache.identity(PROVIDER, SECRET))
    assert stored == metadata()
    assert stored.default_storage_id == 14


def test_unqueried_invalidation_defers_disk_identity(rig, monkeypatch):
    """尚未查询的身份先标记失效，真正查询时只初始化和删除一次。"""
    _, ctx, cache, calls, _, _ = rig
    events = []
    identity, invalidate = cache.identity, cache.invalidate

    def identify(provider, token):
        """记录持久身份初始化且保持真实缓存操作。"""
        events.append('identity')
        return identity(provider, token)

    def remove(value):
        """记录失效删除且保持真实文件语义。"""
        events.append('invalidate')
        return invalidate(value)

    monkeypatch.setattr(cache, 'identity', identify)
    monkeypatch.setattr(cache, 'invalidate', remove)
    ctx.invalidate(PROVIDER, SECRET)
    ctx.invalidate(PROVIDER, SECRET)
    assert events == []
    ctx.get_metadata(PROVIDER, SECRET, correction=True)
    assert events == ['identity', 'invalidate']
    assert len(calls) == 1


def test_close_drops_owned_secret_and_state_references(rig):
    """关闭实际清空所持有的事件容器，不仅设置拒绝继续使用的标记。"""
    module, _, cache, _, _, _ = rig
    ctx = module.UploadContext(diagnostics=True, cache=cache,
                               clock=lambda: 1000)
    ctx.collect_secrets([{'token': SECRET}])
    image = ctx.start_image()
    ctx.prepare_storage(image, 0, PROVIDER, SECRET,
                        manual_present=True, manual_value=[])
    ctx.close()
    assert ctx._secrets == ()
    assert ctx._entries == ctx._images == ctx._credentials == {}
    assert ctx._corrected == set()
    assert ctx._key is ctx._cache is ctx._clock is None


@pytest.mark.parametrize('value', [None, True, -1, float('nan'), float('inf')])
def test_invalid_injected_clock_fails_safely(rig, value):
    """无效注入时钟不回退系统时钟，也不触发查询或泄漏异常链。"""
    module, _, cache, calls, _, _ = rig
    ctx = module.UploadContext(cache=cache, clock=lambda: value)
    with pytest.raises(ImageHostError) as caught:
        ctx.start_image()
    assert_safe(caught.value, 'config_error')
    with pytest.raises(ImageHostError) as caught:
        ctx.get_metadata(PROVIDER, SECRET)
    assert_safe(caught.value, 'config_error')
    assert calls == []


@pytest.fixture
def integration(monkeypatch, tmp_path):
    """仅替换 HTTP 会话边界，保留真实注册表及完整协调链路。"""
    registry = import_module('modules.image_host.registry')
    clock = [1700000000.75]
    cache = StorageCache(tmp_path / 'integration', clock=lambda: clock[0])
    state = {'calls': [], 'replies': [], 'sessions': [], 'responses': [],
             'clock': clock, 'cache': cache, 'registry': registry}

    class Response:
        """按队列提供合成字节响应并记录真实所有权顺序。"""

        def __init__(self, status, body):
            """初始化响应状态及有界字节正文。"""
            self.status_code = status
            self.body = json.dumps(body).encode('utf-8')
            self.active = False
            self.closed = 0

        def __enter__(self):
            """标记响应开始被持有。"""
            self.active = True
            return self

        def __exit__(self, *args):
            """释放响应，不吞掉任何异常。"""
            self.active = False
            self.closed += 1

        def iter_content(self, chunk_size):
            """模拟传输分块，不替代 JSON 解码或错误分类。"""
            assert self.active
            assert chunk_size > 0
            yield self.body

    class Session:
        """模拟单次 HTTP 会话，禁止队列外请求。"""

        def __init__(self):
            """为每次真实传输记录独立会话。"""
            self.closed = 0
            self.response = None
            state['sessions'].append(self)

        def __enter__(self):
            """返回本次会话。"""
            return self

        def __exit__(self, *args):
            """确保响应先于会话释放。"""
            if self.response is not None:
                assert not self.response.active
            self.closed += 1

        def get(self, url, **kwargs):
            """记录 GET 并消费指定响应。"""
            return self.send('GET', url, kwargs)

        def post(self, url, **kwargs):
            """记录 POST 并消费指定响应。"""
            return self.send('POST', url, kwargs)

        def send(self, method, url, kwargs):
            """只在 HTTP 边界注入响应、故障或可控时钟推进。"""
            state['calls'].append((method, url, deepcopy(kwargs)))
            assert state['replies'], '发生队列外请求'
            reply = state['replies'].pop(0)
            if callable(reply):
                reply = reply()
            if isinstance(reply, BaseException):
                raise reply
            status, body = reply
            self.response = Response(status, body)
            state['responses'].append(self.response)
            return self.response

    monkeypatch.setattr('modules.image_host.v2_http._V2Session', Session)
    state['context'] = context_module().UploadContext(
        cache=cache, clock=lambda: clock[0])
    yield state
    assert all(session.closed == 1 for session in state['sessions'])
    assert all(response.closed == 1 for response in state['responses'])
    state['context'].close()


def group_reply(ids=(13, 14), retention=None):
    """构造最小合法组响应，包含明确可用列表和上限。"""
    return 200, {'status': 'success', 'data': {
        'group': {'options': {'file_expire_seconds': retention}},
        'storages': [{'id': value} for value in ids],
    }}


def profile_reply(default=14):
    """构造账号默认存储响应。"""
    return 200, {'status': 'success', 'data': {
        'options': {'default_storage_id': default},
    }}


def upload_reply():
    """构造带不可改写签名的上传直链。"""
    return 200, {'status': 'success', 'data': {
        'public_url': 'https://example.test/i?sig=a%2Fb%3D&x=1+2',
    }}


def rejection_reply():
    """构造传输层批准的精确存储拒绝白名单样本。"""
    return 200, {'status': 'error', 'message': '不存在的储存驱动'}


def registry_upload(state, hosts=None, *, owned=False):
    """执行真实入口，缺少公开上下文接口时产生明确行为断言。"""
    if hosts is None:
        hosts = [{'provider': PROVIDER, 'token': SECRET}]
    function = state['registry'].upload_with_fallback
    if owned:
        return function(b'image', 'image.png', hosts)
    assert 'context' in signature(function).parameters, '缺少公开 context 接入'
    return function(b'image', 'image.png', hosts, context=state['context'])


def post_data(state):
    """只提取实际 POST 表单以检查期限和存储选择。"""
    return [kwargs['data'] for method, _, kwargs in state['calls']
            if method == 'POST']


@pytest.mark.parametrize('provider', ['beeimg_cn'])
@pytest.mark.parametrize('token', ['', SECRET])
def test_registry_cold_lookup_order_and_auth(integration, provider, token):
    """冷缓存按组、可选账号、上传顺序执行且保持认证与直链。"""
    state = integration
    state['replies'] = [group_reply()]
    if token:
        state['replies'].append(profile_reply())
    state['replies'].append(upload_reply())
    result = registry_upload(state, [{'provider': provider, 'token': token}],
                             owned=True)
    assert result.success
    assert result.url == upload_reply()[1]['data']['public_url']
    assert result.attempts == (provider,)
    assert result.failures == ()
    expected = ['/group', '/user/profile', '/upload'] if token else [
        '/group', '/upload']
    assert [url.split('/api/v2')[1] for _, url, _ in state['calls']] == expected
    assert [method for method, _, _ in state['calls']] == (
        ['GET'] * (len(expected) - 1) + ['POST'])
    for _, _, kwargs in state['calls']:
        assert kwargs['headers'] == ({'Accept': 'application/json',
                                     'Authorization': 'Bearer ' + token}
                                    if token else {'Accept': 'application/json'})
        assert kwargs['allow_redirects'] is False
        assert kwargs['verify'] is True
    assert post_data(state) == [{'storage_id': 14 if token else 13,
                                 'is_public': '1'}]


def test_registry_context_signature_and_hot_cache(integration):
    """公开参数仅追加关键字 context，热缓存不再 GET。"""
    state = integration
    params = signature(state['registry'].upload_with_fallback).parameters
    assert list(params) == ['image_bytes', 'filename', 'hosts', 'context']
    assert params['context'].kind is params['context'].KEYWORD_ONLY
    assert params['context'].default is None
    cache = state['cache']
    assert cache.save(cache.identity(PROVIDER, SECRET), StorageMetadata(
        (13, 14), 14, None, state['clock'][0]))
    state['replies'] = [upload_reply()]
    assert registry_upload(state).success
    assert [call[0] for call in state['calls']] == ['POST']


def test_registry_event_reuses_queries_across_images(integration):
    """外部事件跨图复用查询但每图只创建一次起点。"""
    state = integration
    state['replies'] = [group_reply(), profile_reply(), upload_reply(),
                        upload_reply()]
    first = registry_upload(state)
    state['clock'][0] += 10
    second = registry_upload(state)
    assert first.success and second.success
    assert [call[0] for call in state['calls']] == ['GET', 'GET', 'POST', 'POST']
    assert len(state['context']._images) == 2
    assert state['context'].now() == state['clock'][0]


@pytest.mark.parametrize('value', [None, True, 10, '', '0s', '1h1d', SECRET])
@pytest.mark.parametrize('provider', ['beeimg_cn', 'boltp', 'catbox', 'wmimg',
                                      'beeimg', 'superbed'])
def test_registry_invalid_expiration_has_no_io(integration, value, provider):
    """所有站点在请求和缓存前拒绝显式非法期限。"""
    state = integration
    result = registry_upload(state, [{'provider': provider, 'token': SECRET,
                                      'expiration': value}])
    assert not result.success
    assert result.failures[0].code == 'config_error'
    assert result.failures[0].stage == 'expiration'
    assert state['calls'] == []
    assert state['cache']._root is None
    assert SECRET not in repr(result)


@pytest.mark.parametrize('options', [
    {'permission': True}, {'permission': 2}, {'is_public': False},
    {'album_id': '1'}, {'album_id': True},
])
def test_registry_invalid_options_precede_lookup(integration, options):
    """不放宽权限和相册校验，错误发生在首次 GET 前。"""
    state = integration
    result = registry_upload(state, [{'provider': PROVIDER, 'options': options}])
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert state['calls'] == []
    assert state['cache']._root is None


def test_registry_expiration_conflict_precedes_lookup(integration):
    """顶层期限与原生到期字段冲突时零查询。"""
    state = integration
    result = registry_upload(state, [{'provider': PROVIDER, 'expiration': '1h',
                                      'options': {'expired_at': None}}])
    assert not result.success
    assert result.failures[0].code == 'config_error'
    assert state['calls'] == []
    assert state['cache']._root is None


@pytest.mark.parametrize('manual', [None, True, 0, -1, '13', [], {}, 99])
def test_registry_manual_correction_refreshes_disk_once(integration, manual):
    """无效手填不沿用旧缓存，新列表选默认且不修改输入。"""
    state = integration
    cache = state['cache']
    assert cache.save(cache.identity(PROVIDER, SECRET), StorageMetadata(
        (13,), 13, None, state['clock'][0]))
    hosts = [{'provider': PROVIDER, 'token': SECRET,
              'options': {'storage_id': manual}}]
    before = deepcopy(hosts)
    state['replies'] = [group_reply((21, 22)), profile_reply(22), upload_reply(),
                        upload_reply()]
    first = registry_upload(state, hosts)
    second = registry_upload(state, hosts)
    assert first.success and second.success
    assert hosts == before
    assert [data['storage_id'] for data in post_data(state)] == [22, 22]
    assert [call[0] for call in state['calls']] == ['GET', 'GET', 'POST', 'POST']
    assert first.warnings == second.warnings == ('手填储存驱动不可用，已自动替代',)
    assert all(state['context'].storage_state(
        image, 0, PROVIDER, SECRET).correction_used
        for image in state['context']._images)


@pytest.mark.parametrize('reply', [(500, {}), group_reply(())])
def test_registry_manual_refresh_failure_never_uses_old_cache(
        integration, reply):
    """纠错查询失败或空列表后不得发送旧编号或重复查询。"""
    state = integration
    cache = state['cache']
    identity = cache.identity(PROVIDER, SECRET)
    assert cache.save(identity, StorageMetadata(
        (13,), 13, None, state['clock'][0]))
    state['replies'] = [reply]
    hosts = [{'provider': PROVIDER, 'token': SECRET,
              'options': {'storage_id': 99}}]
    assert not registry_upload(state, hosts).success
    assert not registry_upload(state, hosts).success
    assert len(state['calls']) == 1
    assert not post_data(state)
    assert cache.load(identity) is None


@pytest.mark.parametrize('provider', ['beeimg_cn'])
def test_registry_precise_rejection_refreshes_and_reposts_once(
        integration, provider):
    """精确拒绝只允许一次刷新重传且不增加配置槽位。"""
    state = integration
    state['replies'] = [group_reply(), profile_reply(), rejection_reply(),
                        group_reply((21,)), profile_reply(21), upload_reply()]
    result = registry_upload(state, [{'provider': provider, 'token': SECRET}])
    assert result.success
    assert result.attempts == (provider,)
    assert [data['storage_id'] for data in post_data(state)] == [14, 21]
    assert [call[0] for call in state['calls']] == [
        'GET', 'GET', 'POST', 'GET', 'GET', 'POST']
    assert not state['replies']
    assert all(failure.code == 'storage_unavailable' for failure in result.failures)


@pytest.mark.parametrize('second', ['reject', 'query_fail', 'empty'])
def test_registry_recovery_stops_on_second_failure(integration, second):
    """刷新失败或第二次拒绝终止本站，不遍历存储也不无限重试。"""
    state = integration
    state['replies'] = [group_reply(), profile_reply(), rejection_reply()]
    if second == 'reject':
        state['replies'] += [group_reply(), profile_reply(), rejection_reply()]
    else:
        state['replies'] += [(500, {}) if second == 'query_fail'
                             else group_reply(())]
    result = registry_upload(state)
    assert not result.success
    assert result.attempts == (PROVIDER,)
    assert len(post_data(state)) == (2 if second == 'reject' else 1)
    assert not state['replies']


def test_registry_manual_correction_consumes_recovery_budget(integration):
    """上传前纠正后即使精确拒绝也不能再次 GET 或 POST。"""
    state = integration
    state['replies'] = [group_reply(), profile_reply(), rejection_reply()]
    result = registry_upload(state, [{'provider': PROVIDER, 'token': SECRET,
                                      'options': {'storage_id': 0}}])
    assert not result.success
    assert len(state['calls']) == 3
    assert len(post_data(state)) == 1
    assert result.failures[-1].code == 'storage_unavailable'


@pytest.mark.parametrize('reply, code', [
    ((200, {'status': 'error', 'message': '不存在的储存驱动 '}), 'business_rejected'),
    ((200, {'status': 'error', 'message': '请先绑定手机号'}), 'business_rejected'),
    ((200, {'status': 'error', 'message': '审核失败'}), 'business_rejected'),
    ((401, {}), 'http_failed'), ((429, {}), 'http_failed'),
    ((500, {}), 'http_failed'), ((200, {}), 'invalid_response'),
    (requests.exceptions.ReadTimeout(SECRET), 'transport_failed'),
])
def test_registry_other_failures_are_never_retried(integration, reply, code):
    """非白名单拒绝、鉴权、限流、超时和解析失败均单 POST。"""
    state = integration
    state['replies'] = [group_reply(), profile_reply(), reply]
    result = registry_upload(state)
    assert not result.success
    assert result.failures[-1].code == code
    assert len(post_data(state)) == 1
    assert len(state['calls']) == 3


@pytest.mark.parametrize('first_limit, second_limit, duration, shortened', [
    (None, None, 10, False), (0, 0, 10, False), (5, 20, 5, True),
    (20, 5, 5, True), (5, None, 5, True),
])
def test_registry_retry_preserves_deadline_and_shortening_pair(
        integration, first_limit, second_limit, duration, shortened):
    """重传成对继承历史，亚秒取整不误报且刷新只能缩短。"""
    state = integration
    start = state['clock'][0]
    state['replies'] = [group_reply(retention=first_limit), profile_reply(),
                        rejection_reply(), group_reply(retention=second_limit),
                        profile_reply(), upload_reply()]
    result = registry_upload(state, [{'provider': PROVIDER, 'token': SECRET,
                                      'expiration': '10s'}])
    assert result.success
    data = post_data(state)
    expected = datetime.fromtimestamp(int(start + duration)).strftime(
        '%Y-%m-%d %H:%M:%S')
    assert data[-1]['expired_at'] == expected
    image, = state['context']._images
    history = state['context'].storage_state(image, 0, PROVIDER, SECRET)
    assert history.previous_deadline == int(start + duration)
    assert history.previous_shortened is shortened
    assert result.warnings == (('保存期限已按图床上限缩短',) if shortened else ())


def test_registry_elapsed_deadline_prevents_second_post(integration):
    """查询耗时跨越原始期限时不以重传时刻重新起算。"""
    state = integration

    def advance():
        """在恢复组查询响应时推进唯一注入时钟。"""
        state['clock'][0] += 20
        return group_reply()

    state['replies'] = [group_reply(), profile_reply(), rejection_reply(),
                        advance, profile_reply()]
    result = registry_upload(state, [{'provider': PROVIDER, 'token': SECRET,
                                      'expiration': '10s'}])
    assert not result.success
    assert result.failures[-1].code == 'config_error'
    assert result.failures[-1].stage == 'expiration'
    assert len(post_data(state)) == 1


def test_registry_untrusted_prepared_attribute_is_discarded(integration):
    """用户伪造内部容器属性不能进入实际上传表单。"""
    state = integration
    prepared = import_module('modules.image_host.expiration').PreparedV2Options
    options = prepared({}, expired_at='2030-01-02 03:04:05')
    state['replies'] = [group_reply(), upload_reply()]
    result = registry_upload(state, [{'provider': PROVIDER, 'options': options}])
    assert result.success
    assert 'expired_at' not in post_data(state)[0]
    assert options.expired_at == '2030-01-02 03:04:05'


@pytest.mark.parametrize('enabled', [False, True])
def test_registry_diagnostic_scope_revalidates_adapter_fields(
        integration, monkeypatch, enabled):
    """当前作用域再次处理不可信适配器字段，关闭时强制无诊断。"""
    state = integration
    state['context'] = context_module().UploadContext(
        diagnostics=enabled, cache=state['cache'], clock=lambda: state['clock'][0])

    def adapter(*args):
        """抛出被恶意修改的合成错误，不发送网络请求。"""
        error = ImageHostError('business_rejected', '')
        error.message = SECRET
        error.stage = SECRET
        error.http_status = True
        error.diagnostic = '失败 ' + SECRET + ' ' + BACKUP
        raise error

    monkeypatch.setitem(state['registry'].UPLOADERS, 'local', adapter)
    result = registry_upload(state, [{'provider': 'local', 'token': SECRET},
                                      {'provider': 'local', 'token': BACKUP}])
    assert not result.success
    for failure in result.failures:
        assert failure.message == '图床拒绝上传'
        assert failure.stage == ''
        assert failure.http_status is None
        assert failure.diagnostic == (
            '失败 [已隐藏] [已隐藏]' if enabled else None)
    assert safe_current_message('普通内容') is None


@pytest.mark.parametrize('outcome', ['success', 'failure', 'interrupt', 'exit'])
def test_registry_owned_context_closes_after_scope(
        integration, monkeypatch, outcome):
    """自有事件在成功、失败及控制信号后关闭，且先恢复外层作用域。"""
    state = integration
    module = context_module()
    instances = []
    signal = KeyboardInterrupt('stop') if outcome == 'interrupt' else SystemExit(2)

    class ObservedContext(module.UploadContext):
        """仅观察真实事件的释放，不替代生命周期逻辑。"""

        def __init__(self, **kwargs):
            """保留默认构造行为并记录实例。"""
            super().__init__(**kwargs)
            instances.append(self)

        def close(self):
            """关闭前确认注册表已经退出内层诊断作用域。"""
            assert safe_current_message(BACKUP) == '[已隐藏]'
            super().close()

    def adapter(*args):
        """按指定结局返回或抛出原始控制对象。"""
        assert safe_current_message(BACKUP) is None
        if outcome in ('interrupt', 'exit'):
            raise signal
        if outcome == 'failure':
            raise RuntimeError(SECRET)
        return upload_reply()[1]['data']['public_url']

    assert hasattr(state['registry'], 'UploadContext'), '缺少自有事件生命周期'
    monkeypatch.setattr(state['registry'], 'UploadContext', ObservedContext)
    monkeypatch.setitem(state['registry'].UPLOADERS, 'local', adapter)
    diagnostics = import_module('modules.image_host.diagnostics')
    with diagnostics.diagnostic_scope((BACKUP,)):
        if outcome in ('interrupt', 'exit'):
            with pytest.raises(type(signal)) as caught:
                registry_upload(state, [{'provider': 'local'}], owned=True)
            assert caught.value is signal
        else:
            result = registry_upload(state, [{'provider': 'local'}], owned=True)
            assert result.success is (outcome == 'success')
    assert len(instances) == 1
    with pytest.raises(ImageHostError):
        instances[0].now()
    assert safe_current_message('普通内容') is None


@pytest.mark.parametrize('provider', ['catbox', 'wmimg', 'beeimg', 'superbed'])
def test_registry_other_sites_ignore_valid_expiration_without_cache(
        integration, monkeypatch, provider):
    """四站期限仅告警，仍按原四参数单次调用且不触碰缓存。"""
    state = integration
    calls = []

    def adapter(*args):
        """记录旧适配器边界，不改变原始输入。"""
        calls.append(args)
        return upload_reply()[1]['data']['public_url']

    monkeypatch.setitem(state['registry'].UPLOADERS, provider, adapter)
    hosts = [{'provider': provider, 'token': SECRET, 'expiration': '1h',
              'options': {'permission': 1}}]
    before = deepcopy(hosts)
    result = registry_upload(state, hosts)
    assert result.success
    assert calls == [(b'image', 'image.png', SECRET, {'permission': 1})]
    assert result.warnings == ('该图床不支持保存期限，已忽略',)
    assert hosts == before
    assert state['cache']._root is None
    assert state['calls'] == []


@pytest.mark.parametrize('image, filename, hosts, code', [
    (b'', 'image.png', [{}], 'invalid_input'),
    (b'image', '../image.png', [{}], 'invalid_input'),
    (b'image', 'image.png', None, 'not_configured'),
    (b'image', 'image.png', [], 'not_configured'),
    (b'image', 'image.png', {}, 'invalid_hosts'),
])
def test_registry_early_failure_never_starts_image_or_cache(
        integration, image, filename, hosts, code):
    """共享输入提前失败不创建图片、不解析凭证且零缓存网络 IO。"""
    state = integration
    function = state['registry'].upload_with_fallback
    assert 'context' in signature(function).parameters
    result = function(image, filename, hosts, context=state['context'])
    assert not result.success
    assert result.failures[0].code == code
    assert result.attempts == ()
    assert state['context']._images == {}
    assert state['context']._credentials == {}
    assert state['cache']._root is None
    assert state['calls'] == []


def test_registry_same_identity_slots_have_independent_budgets(integration):
    """相同身份配置项共享版本但不共享预算，失败保留且备用可成功。"""
    state = integration
    state['replies'] = [
        group_reply(), profile_reply(), rejection_reply(),
        group_reply((21,)), profile_reply(21), rejection_reply(),
        rejection_reply(), group_reply((22,)), profile_reply(22), upload_reply(),
    ]
    hosts = [{'provider': PROVIDER, 'token': SECRET}] * 2
    result = registry_upload(state, hosts)
    assert result.success
    assert result.attempts == (PROVIDER, PROVIDER)
    assert result.failures
    assert all(f.code == 'storage_unavailable' for f in result.failures)
    assert [d['storage_id'] for d in post_data(state)] == [14, 21, 21, 22]
    assert len(state['calls']) == 10
    image, = state['context']._images
    first = state['context'].storage_state(image, 0, PROVIDER, SECRET)
    second = state['context'].storage_state(image, 1, PROVIDER, SECRET)
    assert first.correction_used and second.correction_used
    assert first.metadata_version < second.metadata_version
    assert not state['replies']


@pytest.mark.parametrize('second_provider, second_token', [
    (PROVIDER, BACKUP), (PROVIDER, ''),
])
def test_registry_metadata_identity_isolated_and_reused(
        integration, second_provider, second_token):
    """BeeIMG.cn 不同凭证和匿名身份独立查询，后续图片各自复用。"""
    state = integration
    failure = (200, {'status': 'error', 'message': '普通业务拒绝'})
    state['replies'] = [group_reply(), profile_reply(), failure,
                        group_reply((21,), retention=30)]
    if second_token:
        state['replies'].append(profile_reply(21))
    state['replies'] += [upload_reply(), failure, upload_reply()]
    hosts = [{'provider': PROVIDER, 'token': SECRET},
             {'provider': second_provider, 'token': second_token}]
    for _ in range(2):
        result = registry_upload(state, hosts)
        assert result.success and result.provider == second_provider
        assert result.attempts == (PROVIDER, second_provider)
        assert len(result.failures) == 1
    assert len(state['calls']) == (8 if second_token else 7)
    assert [d['storage_id'] for d in post_data(state)] == [14, 21, 14, 21]
    assert not state['replies']


def test_registry_sites_share_start_not_duration(integration):
    """同图两站使用共同起点，但分别应用配置期限及组上限。"""
    state = integration
    start = state['clock'][0]

    def delayed_rejection():
        """首站响应耗时不能延长后站期限。"""
        state['clock'][0] += 5
        return 200, {'status': 'error', 'message': '普通拒绝'}

    state['replies'] = [group_reply(retention=30), delayed_rejection,
                        group_reply(retention=15), upload_reply()]
    result = registry_upload(state, [
        {'provider': 'boltp', 'expiration': '10s'},
        {'provider': 'beeimg_cn', 'expiration': '20s'},
    ])
    assert result.success
    assert len(state['context']._images) == 1
    assert [d['expired_at'] for d in post_data(state)] == [
        datetime.fromtimestamp(int(start + seconds)).strftime(
            '%Y-%m-%d %H:%M:%S') for seconds in (10, 15)]
    assert result.warnings == ('保存期限已按图床上限缩短',)


@pytest.mark.parametrize('backup_attempted', [False, True])
def test_registry_diagnostics_freeze_full_chain_and_defer_errors(
        integration, monkeypatch, backup_attempted):
    """整链秘密包含未尝试站点，凭证预解析失败只在实际槽位呈现。"""
    state = integration
    state['context'] = context_module().UploadContext(
        diagnostics=True, cache=state['cache'], clock=lambda: state['clock'][0])
    monkeypatch.setenv(ENV_NAME, BACKUP)
    missing = 'SYNTHETIC_REGISTRY_MISSING'
    monkeypatch.delenv(missing, raising=False)
    seen = []

    def adapter(image, filename, token, options):
        """改变环境后验证后续发送仍使用预收集快照。"""
        seen.append(token)
        assert safe_current_message(BACKUP) == '[已隐藏]'
        monkeypatch.setenv(ENV_NAME, 'FAKE_CHANGED_LATER')
        monkeypatch.setenv(missing, 'FAKE_CREATED_LATER')
        if backup_attempted and token == SECRET:
            raise ImageHostError('business_rejected', '', stage='upload',
                                 http_status=200, diagnostic='失败 ' + BACKUP)
        return upload_reply()[1]['data']['public_url']

    monkeypatch.setitem(state['registry'].UPLOADERS, 'local', adapter)
    result = registry_upload(state, [
        {'provider': 'local', 'token': SECRET},
        {'provider': 'local', 'token': '${' + missing + '}'},
        {'provider': 'local', 'token': '${' + ENV_NAME + '}'},
    ])
    assert result.success
    assert seen == ([SECRET, BACKUP] if backup_attempted else [SECRET])
    if backup_attempted:
        assert result.attempts == ('local', 'local', 'local')
        assert [f.code for f in result.failures] == [
            'business_rejected', 'missing_environment']
        assert result.failures[0].diagnostic == '失败 [已隐藏]'
    else:
        assert result.attempts == ('local',)
        assert result.failures == ()
    assert state['context'].now() == state['clock'][0]
    assert safe_current_message('普通内容') is None


def test_registry_cache_warnings_drained_without_loss(integration, monkeypatch):
    """已有和新增缓存告警保序去重，恢复时的新告警不得丢失。"""
    state = integration
    cache = state['cache']

    def save_failure(*args):
        """仅模拟真实缓存原子写入边界失败。"""
        raise OSError(SECRET)

    def delete_failure(*args):
        """模拟缓存失效操作失败，不影响网络恢复决策。"""
        raise OSError(BACKUP)

    monkeypatch.setattr(cache, '_write_atomic', save_failure)
    monkeypatch.setattr(cache, 'invalidate', delete_failure)
    state['replies'] = [group_reply(retention=5), profile_reply(),
                        rejection_reply(), group_reply(retention=10),
                        profile_reply(), upload_reply()]
    result = registry_upload(state, [{'provider': PROVIDER, 'token': SECRET,
                                      'expiration': '20s'}])
    assert result.success
    assert result.warnings == (
        '存储元数据缓存保存失败', '保存期限已按图床上限缩短',
        '存储元数据缓存失效失败')
    assert state['context'].take_warnings() == ()
    assert SECRET not in repr(result) and BACKUP not in repr(result)
