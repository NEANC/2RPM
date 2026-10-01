#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证事件内协调合同，仅使用合成凭证、临时缓存和查询边界替身。"""

from copy import deepcopy
from importlib import import_module
from importlib.util import find_spec
from inspect import signature
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
PROVIDER = 'boltp'


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
                  (PROVIDER, ''), ('beeimg_cn', SECRET)]
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
