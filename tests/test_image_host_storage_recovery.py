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
from ruamel.yaml import YAML

from modules.image_host.core import ImageHostError
from modules.image_host.diagnostics import safe_current_message
from modules.image_host.expiration import ExpirationDecision
from modules.image_host.storage import StorageMetadata
from modules.image_host.storage_cache import StorageCache
from modules.screenshot.inline_upload import parse_inline_hosts
from modules.screenshot.inline_upload import prepare_cli_host
from modules.yaml_scalar_compat import normalize_config_scalars


SECRET = 'FAKE_EVENT_PRIMARY_7319'
BACKUP = 'FAKE_EVENT_BACKUP_8420'
ENV_NAME = 'SYNTHETIC_EVENT_CREDENTIAL'
PROVIDER = 'beeimg_cn'
TOKEN = '123|offline-token'
TOKEN_FORMS = ('plain', 'single', 'double', 'backtick', 'brace', 'bracket')
HOSTS_HEAD = 'push:\n  screenshot:\n    image_host:\n'


class _NoIoCache:
    """在任何缓存访问上失败的事件替身，确保身份测试不触碰磁盘。"""

    def __getattr__(self, name):
        """禁止意外的缓存调用。"""
        raise AssertionError('身份测试不应访问缓存')


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


def parsed_config_token(form, text=TOKEN):
    """经真实词法预处理与 YAML 往返解析取得 token 标量。"""
    document = (HOSTS_HEAD + '      - {provider: catbox, token: '
                + token_source(form, text) + '}\n')
    parser = YAML()
    parser.preserve_quotes = True
    return parser.load(normalize_config_scalars(document))[
        'push']['screenshot']['image_host'][0]['token']


def context_module():
    """缺少实现时产生功能断言失败，而不是测试收集错误。"""
    name = 'modules.image_host.context'
    assert find_spec(name) is not None, '缺少事件级 UploadContext'
    return import_module(name)


def metadata(ids=(13, 14), default=14, now=1000):
    """构造既有冻结模型，不包含账号响应或上传结果。"""
    return StorageMetadata(ids, default, 3600, now)


def forbidden_metadata(ids=(13, 14), now=1000):
    """构造事件内 forbidden 查询事实，不携带账号默认值。"""
    return StorageMetadata(ids, None, 3600, now, 'forbidden')


def event_queries(calls, provider):
    """统计指定站点在本次事件中真实发生的元数据查询次数。"""
    return sum(1 for called, _, _ in calls if called == provider)


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
def rig_factory(monkeypatch, tmp_path):
    """按站点构建真实缓存及查询边界替身，供两站参数化用例复用。"""
    def build(provider=PROVIDER):
        """在测试执行阶段断言实现存在，避免夹具初始化错误。"""
        module = context_module()
        now = [1000.0]
        root = tmp_path / 'cache'
        cache = StorageCache(root, clock=lambda: now[0])
        calls = []
        replies = []

        def fetch(received_provider, token, *, now):
            """记录查询时点，按队列返回模型或抛出合成异常。"""
            calls.append((received_provider, token, now))
            reply = replies.pop(0) if replies else metadata(now=now)
            if isinstance(reply, BaseException):
                raise reply
            return reply

        monkeypatch.setattr(module, 'fetch_storage_metadata', fetch)
        context = module.UploadContext(cache=cache, clock=lambda: now[0])
        return provider, module, context, cache, calls, replies, now, root

    return build


@pytest.fixture
def rig(rig_factory):
    """默认 BeeIMG.CN 站点的事件夹具，保持既有用例的展开形状。"""
    provider, module, context, cache, calls, replies, now, _ = rig_factory()
    assert provider == PROVIDER
    return module, context, cache, calls, replies, now


def assert_safe(error, code):
    """检查固定异常展示及无底层异常链。"""
    assert type(error) is ImageHostError
    assert error.code == code
    assert error.__cause__ is None
    assert error.__context__ is None
    for secret in (SECRET, BACKUP):
        assert secret not in str(error) + repr(error) + repr(error.args)


def test_resolved_token_snapshot_is_used_once_and_cleared():
    """可信内部凭证快照不再二次解析，关闭后清空。"""
    module = context_module()
    context = module.UploadContext(resolved_tokens={0: '${LITERAL}'})
    host = {'provider': 'catbox', 'token': '${MISSING_TOKEN}'}
    assert context.resolve_credential(0, host) == '${LITERAL}'
    context.close()
    assert context._resolved_tokens == {}


def test_resolved_scalar_token_is_not_reparsed(monkeypatch):
    """注入的解析器字符串子类归一为内置 str，且不触发二次解析。"""
    module = context_module()
    calls = []
    resolve = module.resolve_token

    def observed(value):
        """记录解析器调用并保持既有解析行为。"""
        calls.append(value)
        return resolve(value)

    monkeypatch.setattr(module, 'resolve_token', observed)
    token = parsed_config_token('single', '${LITERAL}')
    assert type(token) is not str
    context = module.UploadContext(resolved_tokens={0: token})
    credential = context.resolve_credential(
        0, {'provider': 'catbox', 'token': '${MISSING_TOKEN}'})
    assert type(credential) is str
    assert credential == '${LITERAL}'
    assert calls == []
    context.close()
    assert context._resolved_tokens == {}


@pytest.mark.parametrize('provider', ['beeimg_cn', 'boltp'])
@pytest.mark.parametrize('form', TOKEN_FORMS)
def test_quoted_tokens_reach_storage_identity(provider, form):
    """真实解析得到的引号凭证冻结后进入事件身份，与内置等值相同。"""
    module = context_module()
    context = module.UploadContext(cache=_NoIoCache())
    token = parsed_config_token(form)
    credential = context.resolve_credential(
        0, {'provider': provider, 'token': token})
    assert type(credential) is str
    assert credential == TOKEN
    identity = context._identity(provider, credential)
    assert type(identity) is bytes
    assert identity == context._identity(provider, TOKEN)
    context.close()


@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('form', TOKEN_FORMS)
def test_cli_quoted_token_snapshot(wrapped, form):
    """内联整项解析到 CLI 快照注入全程保持内置 str 且不被二次解释。"""
    body = ('provider: beeimg_cn, token: ' + token_source(form, TOKEN)
            + ', options: {storage_id: 1}')
    host = parse_inline_hosts('{' + body + '}' if wrapped else body)[0]
    prepared, error = prepare_cli_host(host)
    assert error is None
    assert type(prepared['token']) is str
    assert prepared['token'] == TOKEN
    context = context_module().UploadContext(
        resolved_tokens={0: prepared['token']})
    credential = context.resolve_credential(0, host)
    assert type(credential) is str
    assert credential == TOKEN
    context.close()


@pytest.mark.parametrize('env_value', ['${LITERAL}', '{literal}', '[literal]'])
def test_cli_environment_value_is_not_reexpanded(monkeypatch, env_value):
    """环境值经内联快照注入后不再被解释为引用或外壳。"""
    monkeypatch.setenv(ENV_NAME, env_value)
    host = parse_inline_hosts(
        'provider: catbox, token: ${{{name}}}'.format(name=ENV_NAME))[0]
    prepared, error = prepare_cli_host(host)
    assert error is None
    assert type(prepared['token']) is str
    assert prepared['token'] == env_value
    context = context_module().UploadContext(
        resolved_tokens={0: prepared['token']})
    assert context.resolve_credential(0, host) == env_value
    context.close()


def test_constructor_contract_has_no_io(monkeypatch, tmp_path):
    """构造及读取空告警不访问磁盘、随机源或时钟。"""
    module = context_module()
    params = signature(module.UploadContext).parameters
    assert list(params) == ['diagnostics', 'cache', 'clock', 'resolved_tokens']
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
    """下一事件命中真实磁盘缓存，BeeIMG.CN 读取时归一无账号默认值。"""
    module, ctx, cache, calls, _, now = rig
    first = ctx.get_metadata(PROVIDER, SECRET)
    assert first.storage_ids == (13, 14)
    ctx.close()
    second = module.UploadContext(cache=cache, clock=lambda: now[0])
    cached = second.get_metadata(PROVIDER, SECRET)
    assert cached.storage_ids == first.storage_ids
    assert cached.default_storage_id is None
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


@pytest.mark.parametrize('provider', ['beeimg_cn', 'boltp'])
@pytest.mark.parametrize('disk', [False, True])
@pytest.mark.parametrize('present, manual', [
    (False, None), (True, 13), (True, 99),
    (True, None), (True, {'invalid': SECRET}),
])
def test_selection_budget_contract(rig_factory, provider, disk, present, manual):
    """两站组列表首项选择与手填无关，缓存旧列表直接选中且不消费预算。"""
    _, _, ctx, cache, calls, _, _, _ = rig_factory(provider)
    if disk:
        assert cache.save(cache.identity(provider, SECRET), metadata())
    original = deepcopy(manual)
    image = ctx.start_image()
    selected = ctx.prepare_storage(
        image, 0, provider, SECRET,
        manual_present=present, manual_value=manual)
    state = ctx.storage_state(image, 0, provider, SECRET)
    assert selected.storage_id == 13
    assert state.selection == selected
    assert state.correction_used is False
    assert ctx._corrected == set()
    assert len(calls) == (0 if disk else 1)
    assert manual == original
    for _ in range(3):
        following = ctx.start_image()
        assert ctx.prepare_storage(
            following, 0, provider, SECRET,
            manual_present=present, manual_value=manual) == selected
        assert ctx.storage_state(
            following, 0, provider, SECRET).correction_used is False
    assert len(calls) == (0 if disk else 1)


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


def test_same_identity_slots_share_version_but_not_recovery_budget(rig):
    """同身份槽位共享元数据版本推进，但恢复预算分别消费。"""
    _, ctx, _, calls, _, _ = rig
    first, second = ctx.start_image(), ctx.start_image()
    for image in (first, second):
        ctx.prepare_storage(image, 0, PROVIDER, SECRET)
    first_state = ctx.storage_state(first, 0, PROVIDER, SECRET)
    second_state = ctx.storage_state(second, 0, PROVIDER, SECRET)
    assert (first_state.metadata_version, second_state.metadata_version) == (1, 1)

    assert ctx.begin_recovery(first, 0, PROVIDER, SECRET)
    first_state = ctx.storage_state(first, 0, PROVIDER, SECRET)
    second_state = ctx.storage_state(second, 0, PROVIDER, SECRET)
    assert first_state.correction_used
    assert not second_state.correction_used
    assert (first_state.metadata_version, second_state.metadata_version) == (1, 1)

    assert ctx.begin_recovery(second, 0, PROVIDER, SECRET)
    assert ctx.storage_state(second, 0, PROVIDER, SECRET).correction_used
    assert len(calls) == 1


def test_corrected_selection_revalidated_on_metadata_version_change(rig):
    """新元数据必须重新按组首项选择，手填值不参与优先级。"""
    _, ctx, _, calls, replies, _ = rig
    first = ctx.start_image()
    selected = ctx.prepare_storage(
        first, 0, PROVIDER, SECRET, manual_present=True, manual_value=[])
    assert selected.storage_id == 13
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
    assert state.correction_used is False
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
    """查询失败快照在同事件后续图片复用，恢复预算不被错误消费。"""
    _, ctx, _, calls, replies, _ = rig
    replies.append(RuntimeError(SECRET))
    for _ in range(3):
        image = ctx.start_image()
        with pytest.raises(ImageHostError):
            ctx.prepare_storage(image, 0, PROVIDER, SECRET,
                                manual_present=True, manual_value=99)
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
    """磁盘旧列表按新语义刷新，恢复后重新选择新列表首项且不额外查询。"""
    _, ctx, cache, calls, replies, _ = rig
    assert cache.save(cache.identity(PROVIDER, SECRET), metadata())
    replies.append(metadata((99, 21), 21))
    image = ctx.start_image()
    assert ctx.prepare_storage(
        image, 0, PROVIDER, SECRET,
        manual_present=True, manual_value=99).storage_id == 13
    assert ctx.begin_recovery(image, 0, PROVIDER, SECRET)
    assert not ctx.begin_recovery(image, 0, PROVIDER, SECRET)
    assert ctx.prepare_storage(image, 0, PROVIDER, SECRET).storage_id == 99
    assert len(calls) == 1


def test_distinct_configurations_share_metadata_not_manual_budget(rig):
    """同身份不同配置项共享组元数据，但恢复预算分别消费。"""
    _, ctx, _, calls, _, _ = rig
    image = ctx.start_image()
    ctx.prepare_storage(image, 0, PROVIDER, SECRET,
                        manual_present=True, manual_value=[])
    ctx.prepare_storage(image, 1, PROVIDER, SECRET)
    assert not ctx.storage_state(image, 0, PROVIDER, SECRET).correction_used
    assert not ctx.storage_state(image, 1, PROVIDER, SECRET).correction_used
    assert len(calls) == 1
    assert ctx.begin_recovery(image, 0, PROVIDER, SECRET)
    assert ctx.begin_recovery(image, 1, PROVIDER, SECRET)
    assert ctx.prepare_storage(image, 1, PROVIDER, SECRET)
    assert len(calls) == 2


@pytest.mark.parametrize('reverse', [False, True])
def test_beeimg_forbidden_selection_is_slot_local(rig, reverse):
    """forbidden 仅作查询结果复用，BeeIMG.CN 忽略资料状态并按组首项选择。"""
    _, ctx, _, calls, replies, _ = rig
    replies.append(forbidden_metadata())
    order = ([(True, 13), (False, None)] if reverse
             else [(False, None), (True, 13)])
    image = ctx.start_image()
    for index, (present, manual) in enumerate(order):
        selection = ctx.prepare_storage(
            image, index, PROVIDER, SECRET,
            manual_present=present, manual_value=manual)
        assert selection.storage_id == 13
    assert len(calls) == 1


def test_forbidden_fact_shared_across_images_not_manual_selection(rig):
    """同身份跨图跨槽位共享 forbidden 查询事实，手填不改变组首项选择。"""
    _, ctx, _, calls, replies, _ = rig
    replies.append(forbidden_metadata())
    first, second = ctx.start_image(), ctx.start_image()
    for image in (first, second):
        for index in (0, 1):
            selection = ctx.prepare_storage(image, index, PROVIDER, SECRET)
            assert selection.storage_id == 13
    assert len(calls) == 1
    selected = ctx.prepare_storage(
        first, 0, PROVIDER, SECRET, manual_present=True, manual_value=14)
    assert selected.storage_id == 13
    replies.append(forbidden_metadata((21, 22)))
    other = ctx.prepare_storage(
        first, 0, PROVIDER, BACKUP, manual_present=True, manual_value=21)
    assert other.storage_id == 21
    assert len(calls) == 2


@pytest.mark.parametrize('manual', [99, None, 0, -1, '13', [], {}])
def test_forbidden_invalid_manual_never_selects_first(rig, manual):
    """forbidden 下非法手填仍按组首项选择，不生成资料权限错误。"""
    _, ctx, _, calls, replies, _ = rig
    replies.append(forbidden_metadata())
    image = ctx.start_image()
    selection = ctx.prepare_storage(
        image, 0, PROVIDER, SECRET,
        manual_present=True, manual_value=manual)
    assert selection.storage_id == 13
    assert ctx.storage_state(image, 0, PROVIDER, SECRET).selection == selection
    assert len(calls) == 1


def test_forbidden_result_not_reused_across_events(rig):
    """forbidden 查询事实只影响当前选择，不写盘，下一事件重新按组首项选择。"""
    module, ctx, cache, calls, replies, now = rig
    replies.append(forbidden_metadata())
    image = ctx.start_image()
    selected = ctx.prepare_storage(
        image, 0, PROVIDER, SECRET, manual_present=True, manual_value=99)
    assert selected.storage_id == 13
    assert cache.load(cache.identity(PROVIDER, SECRET)) is None
    ctx.close()
    replies.append(metadata((21, 22)))
    second = module.UploadContext(cache=cache, clock=lambda: now[0])
    selected = second.prepare_storage(
        second.start_image(), 0, PROVIDER, SECRET)
    assert selected.storage_id == 21
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
    """事件选择不覆盖查询结果；磁盘读取归一无账号默认值。"""
    _, ctx, cache, _, _, _ = rig
    image = ctx.start_image()
    selected = ctx.prepare_storage(
        image, 0, PROVIDER, SECRET, manual_present=True, manual_value=13)
    assert selected.storage_id == 13
    stored = cache.load(cache.identity(PROVIDER, SECRET))
    assert stored.storage_ids == (13, 14)
    assert stored.default_storage_id is None
    assert stored.file_expire_seconds == metadata().file_expire_seconds


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


def test_manual_selection_without_metadata_io(monkeypatch):
    """合法手填登记只用事件身份：不查询、不建磁盘身份、不触发缓存 IO。"""
    module = context_module()
    cache_calls = []

    class SpyCache:
        """记录缓存入口调用；普通异常会被缓存包装吞掉，故必须显式记录。"""

        def __getattr__(self, name):
            """把任何缓存访问记为越界调用，不抛出会被吞掉的异常。"""
            def recorded(*args, **kwargs):
                """记录一次未预期的缓存访问。"""
                cache_calls.append(name)

            return recorded

    def forbidden(*args, **kwargs):
        """手填登记期间禁止任何元数据查询。"""
        pytest.fail('手填登记不应查询元数据')

    monkeypatch.setattr(module, 'fetch_storage_metadata', forbidden)
    context = module.UploadContext(cache=SpyCache(), clock=lambda: 1000)
    image = context.start_image()
    token = TOKEN
    selected = context.record_manual_selection(image, 0, 'beeimg_cn', token, 13)
    state = context.storage_state(image, 0, 'beeimg_cn', token)
    assert selected.storage_id == 13
    assert selected.replaced_manual is False
    assert state.selection == selected
    assert state.selection_source == 'manual'
    assert state.metadata_version == 0
    assert state.rejected_storage_id is None
    assert context._entries == {}
    assert cache_calls == []
    context.close()


@pytest.mark.parametrize('provider', ['beeimg_cn', 'boltp'])
@pytest.mark.parametrize('row', [
    'manual_without_entry', 'same_generation_success', 'same_generation_failure',
    'newer_network_success', 'newer_network_failure', 'newer_disk_result',
])
def test_manual_recovery_generation_table(rig_factory, monkeypatch, provider,
                                          row):
    """两站逐行核对代次表：手填基线、同代推进、新代复用与磁盘代刷新。"""
    _, _, ctx, cache, calls, replies, _, _ = rig_factory(provider)
    loads = []
    load = cache.load

    def tracked(identity):
        """记录真实磁盘读取次数，不替代读取行为。"""
        loads.append(identity)
        return load(identity)

    monkeypatch.setattr(cache, 'load', tracked)
    identity = ctx._identity(provider, SECRET)
    if row == 'manual_without_entry':
        image = ctx.start_image()
        ctx.record_manual_selection(image, 0, provider, SECRET, 13)
        assert ctx._entries.get(identity) is None
        assert ctx.begin_recovery(image, 0, provider, SECRET) is True
        entry = ctx._entries[identity]
        assert (entry.version, entry.invalidated) == (1, True)
        assert entry.metadata is None
        assert calls == [] and loads == []
        assert ctx.prepare_storage(image, 0, provider, SECRET).storage_id == 14
        assert len(calls) == 1
        assert loads == []
        assert ctx._entries[identity].metadata.storage_ids == (13, 14)
    elif row == 'same_generation_success':
        image = ctx.start_image()
        assert ctx.prepare_storage(image, 0, provider, SECRET).storage_id == 13
        queries = len(calls)
        ctx.record_manual_selection(image, 0, provider, SECRET, 13)
        assert ctx.storage_state(image, 0, provider, SECRET).metadata_version == 1
        assert ctx.begin_recovery(image, 0, provider, SECRET) is True
        entry = ctx._entries[identity]
        assert (entry.version, entry.metadata, entry.invalidated) == (
            2, None, True)
        assert len(calls) == queries
        assert ctx.prepare_storage(image, 0, provider, SECRET).storage_id == 14
        assert ctx._entries[identity].version == 2
        assert len(calls) == queries + 1
    elif row == 'same_generation_failure':
        image = ctx.start_image()
        replies.append(RuntimeError(SECRET))
        with pytest.raises(ImageHostError) as caught:
            ctx.get_metadata(provider, SECRET)
        old_error = caught.value
        assert ctx._entries[identity].version == 1
        ctx.record_manual_selection(image, 0, provider, SECRET, 13)
        assert ctx.storage_state(image, 0, provider, SECRET).metadata_version == 1
        queries = len(calls)
        assert ctx.begin_recovery(image, 0, provider, SECRET) is True
        assert ctx._entries[identity].failure is None
        assert ctx._entries[identity].version == 2
        assert len(calls) == queries
        replies.append(RuntimeError(SECRET))
        with pytest.raises(ImageHostError) as again:
            ctx.prepare_storage(image, 0, provider, SECRET)
        assert again.value is not old_error
        assert ctx._entries[identity].version == 2
        assert ctx._entries[identity].failure is not None
        assert len(calls) == queries + 1
    elif row == 'newer_network_success':
        image = ctx.start_image()
        ctx.record_manual_selection(image, 0, provider, SECRET, 13)
        assert ctx.prepare_storage(
            ctx.start_image(), 0, provider, SECRET).storage_id == 13
        entry = ctx._entries[identity]
        assert (entry.version, entry.from_disk) == (1, False)
        queries = len(calls)
        assert ctx.begin_recovery(image, 0, provider, SECRET) is True
        assert ctx._entries[identity] is entry
        assert (entry.version, entry.invalidated) == (1, False)
        assert len(calls) == queries
        assert ctx.prepare_storage(image, 0, provider, SECRET).storage_id == 14
        assert len(calls) == queries
        assert ctx._entries[identity].metadata.storage_ids == (13, 14)
    elif row == 'newer_network_failure':
        image = ctx.start_image()
        ctx.record_manual_selection(image, 0, provider, SECRET, 13)
        other = ctx.start_image()
        replies.append(RuntimeError(SECRET))
        with pytest.raises(ImageHostError):
            ctx.prepare_storage(other, 0, provider, SECRET)
        entry = ctx._entries[identity]
        failure = entry.failure
        assert (entry.version, entry.from_disk) == (1, False)
        queries = len(calls)
        assert ctx.begin_recovery(image, 0, provider, SECRET) is True
        assert ctx._entries[identity] is entry
        assert entry.version == 1
        assert entry.failure is failure
        assert len(calls) == queries
        with pytest.raises(ImageHostError) as caught:
            ctx.prepare_storage(image, 0, provider, SECRET)
        assert caught.value.code == failure.code
        assert ctx._entries[identity].failure is failure
        assert len(calls) == queries
    else:
        image = ctx.start_image()
        ctx.record_manual_selection(image, 0, provider, SECRET, 13)
        assert cache.save(cache.identity(provider, SECRET), metadata())
        assert ctx.prepare_storage(
            ctx.start_image(), 0, provider, SECRET).storage_id == 13
        entry = ctx._entries[identity]
        assert (entry.version, entry.from_disk) == (1, True)
        assert loads == [cache.identity(provider, SECRET)]
        queries = len(calls)
        assert ctx.begin_recovery(image, 0, provider, SECRET) is True
        assert ctx._entries[identity].version == 2
        assert ctx._entries[identity].invalidated is True
        assert len(calls) == queries
        assert ctx.prepare_storage(image, 0, provider, SECRET).storage_id == 14
        assert len(calls) == queries + 1
        assert ctx._entries[identity].version == 2


@pytest.mark.parametrize('provider', ['beeimg_cn', 'boltp'])
def test_old_rejection_cannot_delete_new_generation(
        rig_factory, monkeypatch, provider):
    """两站晚到的旧代失效请求不得删除新代内存结果或磁盘记录。"""
    _, _, ctx, cache, calls, replies, _, _ = rig_factory(provider)
    deletes = []
    invalidate = cache.invalidate

    def tracked(identity):
        """记录真实磁盘失效次数，不替代删除行为。"""
        deletes.append(identity)
        return invalidate(identity)

    monkeypatch.setattr(cache, 'invalidate', tracked)
    first, second = ctx.start_image(), ctx.start_image()
    for image in (first, second):
        ctx.record_manual_selection(image, 0, provider, SECRET, 13)
    assert ctx.storage_state(first, 0, provider, SECRET).metadata_version == 0
    assert ctx.begin_recovery(first, 0, provider, SECRET) is True
    assert ctx.prepare_storage(first, 0, provider, SECRET).storage_id == 14
    identity = cache.identity(provider, SECRET)
    entry = ctx._entries[ctx._identity(provider, SECRET)]
    assert (entry.version, len(calls)) == (1, 1)
    assert cache.load(identity) is not None
    deletes_before = len(deletes)
    assert ctx.begin_recovery(second, 0, provider, SECRET) is True
    assert entry.version == 1
    ctx.invalidate(provider, SECRET, version=0, disk_only=True)
    assert entry.version == 1
    assert entry.metadata is not None
    assert len(deletes) == deletes_before
    assert cache.load(identity) is not None
    assert len(calls) == 1
    assert ctx.storage_state(second, 0, provider, SECRET).selection is not None


def test_late_rejection_cannot_delete_regenerated_result(rig, monkeypatch):
    """清除结束当前代次，晚到同代拒绝不得删除重新查询产生的新结果。"""
    _, ctx, cache, calls, replies, _ = rig
    deletes = []
    invalidate = cache.invalidate

    def tracked(identity):
        """记录真实磁盘失效次数，不替代删除行为。"""
        deletes.append(identity)
        return invalidate(identity)

    monkeypatch.setattr(cache, 'invalidate', tracked)
    first, second = ctx.start_image(), ctx.start_image()
    for image in (first, second):
        ctx.record_manual_selection(image, 0, PROVIDER, SECRET, 13)
    assert ctx.begin_recovery(first, 0, PROVIDER, SECRET) is True
    assert ctx.prepare_storage(first, 0, PROVIDER, SECRET).storage_id == 14
    identity = ctx._identity(PROVIDER, SECRET)
    entry = ctx._entries[identity]
    assert (entry.version, len(calls)) == (1, 1)
    deletes_before = len(deletes)
    ctx.invalidate(PROVIDER, SECRET, version=1, disk_only=True)
    assert entry.version == 2
    assert entry.metadata is None
    assert len(deletes) == deletes_before + 1
    third = ctx.start_image()
    assert ctx.prepare_storage(third, 0, PROVIDER, SECRET).storage_id == 13
    assert len(calls) == 2
    assert entry.version == 2
    deletes_after = len(deletes)
    ctx.invalidate(PROVIDER, SECRET, version=1, disk_only=True)
    identity = cache.identity(PROVIDER, SECRET)
    entry = ctx._entries[ctx._identity(PROVIDER, SECRET)]
    assert entry.version == 2
    assert entry.metadata is not None
    assert cache.load(identity) is not None
    assert len(deletes) == deletes_after
    assert len(calls) == 2
    ctx.invalidate(PROVIDER, SECRET, version=2, disk_only=True)
    assert entry.metadata is None


@pytest.mark.parametrize('provider', ['beeimg_cn', 'boltp'])
@pytest.mark.parametrize('outcome', ['success', 'failure'])
@pytest.mark.parametrize('disk_only', [False, True])
def test_v2_late_invalidation_preserves_generation(
        rig_factory, monkeypatch, provider, outcome, disk_only):
    """晚到旧代失效不改变新代成功/失败结果、版本或磁盘原始字节。"""
    _, _, ctx, cache, calls, replies, _, root = rig_factory(provider)
    deletes = []
    invalidate = cache.invalidate

    def tracked(identity):
        """记录真实磁盘失效次数，不替代删除行为。"""
        deletes.append(identity)
        return invalidate(identity)

    monkeypatch.setattr(cache, 'invalidate', tracked)
    first, second = ctx.start_image(), ctx.start_image()
    for image in (first, second):
        assert ctx.prepare_storage(image, 0, provider, SECRET).storage_id == 13
    assert ctx.begin_recovery(first, 0, provider, SECRET) is True
    if outcome == 'success':
        replies.append(metadata((21,), 21))
        assert ctx.prepare_storage(first, 0, provider, SECRET).storage_id == 21
    else:
        replies.append(RuntimeError(SECRET))
        with pytest.raises(ImageHostError):
            ctx.prepare_storage(first, 0, provider, SECRET)
    identity = ctx._identity(provider, SECRET)
    entry = ctx._entries[identity]
    path = root / (cache.identity(provider, SECRET) + '.json')
    disk_before = path.read_bytes() if path.exists() else None
    metadata_before, failure_before = entry.metadata, entry.failure
    version_before, queries_before = entry.version, len(calls)
    deletes_before = len(deletes)
    assert version_before == 2
    if outcome == 'success':
        assert metadata_before is not None and disk_before is not None
    else:
        assert failure_before is not None and disk_before is None

    ctx.invalidate(provider, SECRET, version=1, disk_only=disk_only)

    assert entry.version == version_before
    assert entry.metadata is metadata_before
    assert entry.failure is failure_before
    assert len(calls) == queries_before
    assert len(deletes) == deletes_before
    assert (path.read_bytes() if path.exists() else None) == disk_before


@pytest.mark.parametrize('disk_only', [False, True])
@pytest.mark.parametrize('a_provider', ['beeimg_cn', 'boltp'])
def test_v2_cross_provider_success_bytes(rig_factory, a_provider, disk_only):
    """同 token 下 A 站刷新与旧代失效不触碰 B 站对象、版本、查询与原始字节。"""
    b_provider = 'boltp' if a_provider == 'beeimg_cn' else 'beeimg_cn'
    _, _, ctx, cache, calls, replies, now, root = rig_factory(a_provider)
    a_identity = cache.identity(a_provider, SECRET)
    b_identity = cache.identity(b_provider, SECRET)
    assert a_identity != b_identity
    assert ctx._identity(a_provider, SECRET) != ctx._identity(b_provider, SECRET)
    replies.extend([metadata((13,), now=now[0]), metadata((21,), now=now[0])])
    a_metadata = ctx.get_metadata(a_provider, SECRET)
    b_metadata = ctx.get_metadata(b_provider, SECRET)
    a_entry = ctx._entries[ctx._identity(a_provider, SECRET)]
    b_entry = ctx._entries[ctx._identity(b_provider, SECRET)]
    assert (a_metadata.storage_ids, b_metadata.storage_ids) == ((13,), (21,))
    assert (a_entry.version, b_entry.version) == (1, 1)
    b_path = root / (b_identity + '.json')
    b_bytes = b_path.read_bytes()
    b_version = b_entry.version
    b_queries = event_queries(calls, b_provider)

    def assert_b_untouched():
        """B 站内存对象、版本、查询计数和磁盘原始字节逐项不变。"""
        assert b_entry.metadata is b_metadata
        assert b_entry.failure is None
        assert b_entry.version == b_version
        assert event_queries(calls, b_provider) == b_queries
        assert b_path.read_bytes() == b_bytes

    ctx.invalidate(a_provider, SECRET, version=1)
    replies.append(RuntimeError(SECRET))
    with pytest.raises(ImageHostError):
        ctx.get_metadata(a_provider, SECRET)
    assert (a_entry.version, a_entry.metadata) == (2, None)
    assert a_entry.failure is not None
    assert_b_untouched()

    ctx.invalidate(a_provider, SECRET, version=a_entry.version)
    replies.append(metadata((31,), now=now[0]))
    refreshed = ctx.get_metadata(a_provider, SECRET)
    assert refreshed.storage_ids == (31,)
    assert (a_entry.version, a_entry.metadata) == (3, refreshed)
    assert_b_untouched()

    for stale_version in (1, 2):
        ctx.invalidate(a_provider, SECRET, version=stale_version,
                       disk_only=disk_only)
        assert (a_entry.version, a_entry.metadata) == (3, refreshed)
        assert_b_untouched()


@pytest.mark.parametrize('a_provider', ['beeimg_cn', 'boltp'])
def test_v2_cross_provider_failure_snapshot(rig_factory, a_provider):
    """A 站失败快照不被 B 站刷新与旧代失效改写，重建异常但不重复查询。"""
    b_provider = 'boltp' if a_provider == 'beeimg_cn' else 'beeimg_cn'
    _, _, ctx, cache, calls, replies, now, root = rig_factory(a_provider)
    replies.append(RuntimeError(SECRET))
    with pytest.raises(ImageHostError) as first:
        ctx.get_metadata(a_provider, SECRET)
    first_error = first.value
    a_entry = ctx._entries[ctx._identity(a_provider, SECRET)]
    saved_failure = a_entry.failure
    saved_version = a_entry.version
    saved_queries = event_queries(calls, a_provider)
    a_path = root / (cache.identity(a_provider, SECRET) + '.json')
    saved_disk = a_path.read_bytes() if a_path.exists() else None

    replies.append(metadata((21,), now=now[0]))
    b_metadata = ctx.get_metadata(b_provider, SECRET)
    b_entry = ctx._entries[ctx._identity(b_provider, SECRET)]
    ctx.invalidate(b_provider, SECRET, version=b_entry.version)
    replies.append(metadata((31,), now=now[0]))
    refreshed = ctx.get_metadata(b_provider, SECRET)
    assert refreshed.storage_ids == (31,)
    ctx.invalidate(b_provider, SECRET, version=1, disk_only=True)
    assert b_entry.metadata is refreshed
    assert b_metadata.storage_ids == (21,)
    assert cache.load(cache.identity(b_provider, SECRET)).storage_ids == (31,)

    assert saved_failure is not None
    assert a_entry.failure is saved_failure
    assert a_entry.version == saved_version
    assert event_queries(calls, a_provider) == saved_queries
    assert (a_path.read_bytes() if a_path.exists() else None) == saved_disk

    with pytest.raises(ImageHostError) as second:
        ctx.get_metadata(a_provider, SECRET)
    second_error = second.value
    assert second_error is not first_error
    assert (second_error.code, second_error.stage) == (
        first_error.code, first_error.stage)
    assert a_entry.failure is saved_failure
    assert a_entry.version == saved_version
    assert event_queries(calls, a_provider) == saved_queries


@pytest.mark.parametrize('provider', ['beeimg_cn', 'boltp'])
def test_local_exclusion_and_slot_budgets(rig_factory, provider):
    """两站恢复排除只作用于本图本槽，非法手填不消费预算也不写纠错事实。"""
    _, _, ctx, cache, calls, replies, _, _ = rig_factory(provider)
    first = ctx.start_image()
    assert ctx.prepare_storage(
        first, 0, provider, SECRET,
        manual_present=True, manual_value=[]).storage_id == 13
    state = ctx.storage_state(first, 0, provider, SECRET)
    assert state.correction_used is False
    assert ctx._corrected == set()
    shared = ctx.get_metadata(provider, SECRET)
    assert shared.storage_ids == (13, 14)
    second = ctx.start_image()
    for index in (0, 1):
        assert ctx.prepare_storage(second, index, provider, SECRET).storage_id == 13
    assert ctx.begin_recovery(second, 0, provider, SECRET) is True
    assert ctx.begin_recovery(second, 0, provider, SECRET) is False
    assert ctx.begin_recovery(second, 1, provider, SECRET) is True
    for index in (0, 1):
        assert ctx.prepare_storage(
            second, index, provider, SECRET).storage_id == 14
    assert ctx.prepare_storage(
        ctx.start_image(), 0, provider, SECRET).storage_id == 13
    assert shared.storage_ids == (13, 14)
    third, fourth = ctx.start_image(), ctx.start_image()
    for image in (third, fourth):
        assert ctx.prepare_storage(image, 0, provider, SECRET).storage_id == 13
    for image in (third, fourth):
        assert ctx.begin_recovery(image, 0, provider, SECRET) is True
        assert ctx.begin_recovery(image, 0, provider, SECRET) is False
    assert replies == []


def test_manual_after_failed_lookup(rig):
    """自动失败后登记手填不查询、不改快照，按新基线恢复并允许新事件重查。"""
    module, ctx, cache, calls, replies, now = rig
    image = ctx.start_image()
    replies.append(RuntimeError(SECRET))
    with pytest.raises(ImageHostError):
        ctx.get_metadata(PROVIDER, SECRET)
    identity = ctx._identity(PROVIDER, SECRET)
    failure = ctx._entries[identity].failure
    assert (ctx._entries[identity].version, len(calls)) == (1, 1)
    recorded = ctx.record_manual_selection(image, 0, PROVIDER, SECRET, 13)
    state = ctx.storage_state(image, 0, PROVIDER, SECRET)
    assert state.selection == recorded
    assert state.selection_source == 'manual'
    assert state.metadata_version == 1
    assert ctx._entries[identity].failure is failure
    assert len(calls) == 1
    with pytest.raises(ImageHostError):
        ctx.get_metadata(PROVIDER, SECRET)
    assert ctx._entries[identity].failure is failure
    assert len(calls) == 1
    fresh = module.UploadContext(cache=cache, clock=lambda: now[0])
    assert fresh.get_metadata(PROVIDER, SECRET).storage_ids == (13, 14)
    assert len(calls) == 2
    fresh.close()
    assert ctx.begin_recovery(image, 0, PROVIDER, SECRET) is True
    assert ctx._entries[identity].failure is None
    assert ctx._entries[identity].version == 2
    assert ctx.prepare_storage(image, 0, PROVIDER, SECRET).storage_id == 14
    assert ctx._entries[identity].version == 2
    assert len(calls) == 3
    assert ctx.storage_state(image, 0, PROVIDER, SECRET).selection_source == (
        'metadata')
    third = ctx.start_image()
    assert ctx.prepare_storage(third, 0, PROVIDER, SECRET).storage_id == 13
    ctx.record_manual_selection(third, 0, PROVIDER, SECRET, 15)
    assert ctx.storage_state(
        third, 0, PROVIDER, SECRET).metadata_version == 2


def test_beeimg_failure_and_refresh_leave_boltp_metadata_untouched(rig):
    """同凭证下 BeeIMG.CN 的失败快照与代次刷新不污染 Boltp 的内存与磁盘记录。"""
    _, ctx, cache, calls, replies, now = rig
    beeimg_identity = cache.identity('beeimg_cn', SECRET)
    boltp_identity = cache.identity('boltp', SECRET)
    assert beeimg_identity != boltp_identity

    replies[:] = [metadata(ids=(13,), now=now[0])]
    beeimg_metadata = ctx.get_metadata('beeimg_cn', SECRET)
    replies[:] = [metadata(ids=(21,), now=now[0])]
    boltp_metadata = ctx.get_metadata('boltp', SECRET)
    beeimg_entry = ctx._entries[ctx._identity('beeimg_cn', SECRET)]
    boltp_entry = ctx._entries[ctx._identity('boltp', SECRET)]
    beeimg_version, boltp_version = beeimg_entry.version, boltp_entry.version
    assert (beeimg_version, boltp_version) == (1, 1)
    assert beeimg_entry.metadata is beeimg_metadata
    assert boltp_entry.metadata is boltp_metadata
    assert cache.load(beeimg_identity).storage_ids == (13,)
    assert cache.load(boltp_identity).storage_ids == (21,)
    assert (event_queries(calls, 'beeimg_cn'),
            event_queries(calls, 'boltp')) == (1, 1)

    replies[:] = [ImageHostError('storage_lookup_failed', '', stage='group')]
    with pytest.raises(ImageHostError) as caught:
        ctx.get_metadata('beeimg_cn', SECRET, correction=True)
    assert_safe(caught.value, 'storage_lookup_failed')
    assert beeimg_entry.failure is not None
    assert beeimg_entry.metadata is None
    assert cache.load(beeimg_identity) is None
    assert boltp_entry.metadata is boltp_metadata
    assert boltp_entry.failure is None
    assert boltp_entry.version == boltp_version
    assert cache.load(boltp_identity) is not None
    assert (event_queries(calls, 'beeimg_cn'),
            event_queries(calls, 'boltp')) == (2, 1)

    beeimg_failed_version = beeimg_entry.version
    ctx.invalidate('beeimg_cn', SECRET, version=beeimg_failed_version)
    assert beeimg_entry.version == beeimg_failed_version + 1
    assert beeimg_entry.metadata is None
    assert boltp_entry.metadata is boltp_metadata
    assert boltp_entry.failure is None
    assert boltp_entry.version == boltp_version
    assert cache.load(boltp_identity) is not None
    assert (event_queries(calls, 'beeimg_cn'),
            event_queries(calls, 'boltp')) == (2, 1)

    replies[:] = [metadata(ids=(31,), now=now[0])]
    refreshed = ctx.get_metadata('beeimg_cn', SECRET)
    assert refreshed.storage_ids == (31,)
    assert beeimg_entry.metadata is refreshed
    assert beeimg_entry.version == beeimg_failed_version + 1
    assert cache.load(beeimg_identity).storage_ids == (31,)
    assert boltp_entry.metadata is boltp_metadata
    assert boltp_entry.failure is None
    assert boltp_entry.version == boltp_version
    assert cache.load(boltp_identity) is not None
    assert (event_queries(calls, 'beeimg_cn'),
            event_queries(calls, 'boltp')) == (3, 1)

    beeimg_newer_version = beeimg_entry.version
    ctx.invalidate('beeimg_cn', SECRET, version=beeimg_version, disk_only=True)
    assert beeimg_entry.metadata is refreshed
    assert beeimg_entry.metadata.storage_ids == (31,)
    assert beeimg_entry.version == beeimg_newer_version
    assert cache.load(beeimg_identity).storage_ids == (31,)
    assert boltp_entry.metadata is boltp_metadata
    assert boltp_entry.failure is None
    assert boltp_entry.version == boltp_version
    assert cache.load(boltp_identity) is not None
    assert (event_queries(calls, 'beeimg_cn'),
            event_queries(calls, 'boltp')) == (3, 1)


def test_boltp_failure_and_refresh_leave_beeimg_metadata_untouched(rig):
    """同凭证下 Boltp 的失败快照与代次刷新不污染 BeeIMG.CN 的内存与磁盘记录。"""
    _, ctx, cache, calls, replies, now = rig
    beeimg_identity = cache.identity('beeimg_cn', SECRET)
    boltp_identity = cache.identity('boltp', SECRET)
    assert beeimg_identity != boltp_identity

    replies[:] = [metadata(ids=(13,), now=now[0])]
    beeimg_metadata = ctx.get_metadata('beeimg_cn', SECRET)
    replies[:] = [metadata(ids=(21,), now=now[0])]
    boltp_metadata = ctx.get_metadata('boltp', SECRET)
    beeimg_entry = ctx._entries[ctx._identity('beeimg_cn', SECRET)]
    boltp_entry = ctx._entries[ctx._identity('boltp', SECRET)]
    beeimg_version, boltp_version = beeimg_entry.version, boltp_entry.version
    assert (beeimg_version, boltp_version) == (1, 1)
    assert beeimg_entry.metadata is beeimg_metadata
    assert boltp_entry.metadata is boltp_metadata
    assert cache.load(beeimg_identity).storage_ids == (13,)
    assert cache.load(boltp_identity).storage_ids == (21,)
    assert (event_queries(calls, 'beeimg_cn'),
            event_queries(calls, 'boltp')) == (1, 1)

    replies[:] = [ImageHostError('storage_lookup_failed', '', stage='group')]
    with pytest.raises(ImageHostError) as caught:
        ctx.get_metadata('boltp', SECRET, correction=True)
    assert_safe(caught.value, 'storage_lookup_failed')
    assert boltp_entry.failure is not None
    assert boltp_entry.metadata is None
    assert cache.load(boltp_identity) is None
    assert beeimg_entry.metadata is beeimg_metadata
    assert beeimg_entry.failure is None
    assert beeimg_entry.version == beeimg_version
    assert cache.load(beeimg_identity) is not None
    assert (event_queries(calls, 'beeimg_cn'),
            event_queries(calls, 'boltp')) == (1, 2)

    boltp_failed_version = boltp_entry.version
    ctx.invalidate('boltp', SECRET, version=boltp_failed_version)
    assert boltp_entry.version == boltp_failed_version + 1
    assert boltp_entry.metadata is None
    assert beeimg_entry.metadata is beeimg_metadata
    assert beeimg_entry.failure is None
    assert beeimg_entry.version == beeimg_version
    assert cache.load(beeimg_identity) is not None
    assert (event_queries(calls, 'beeimg_cn'),
            event_queries(calls, 'boltp')) == (1, 2)

    replies[:] = [metadata(ids=(32,), now=now[0])]
    refreshed = ctx.get_metadata('boltp', SECRET)
    assert refreshed.storage_ids == (32,)
    assert boltp_entry.metadata is refreshed
    assert boltp_entry.version == boltp_failed_version + 1
    assert cache.load(boltp_identity).storage_ids == (32,)
    assert beeimg_entry.metadata is beeimg_metadata
    assert beeimg_entry.failure is None
    assert beeimg_entry.version == beeimg_version
    assert cache.load(beeimg_identity) is not None
    assert (event_queries(calls, 'beeimg_cn'),
            event_queries(calls, 'boltp')) == (1, 3)

    boltp_newer_version = boltp_entry.version
    ctx.invalidate('boltp', SECRET, version=boltp_version, disk_only=True)
    assert boltp_entry.metadata is refreshed
    assert boltp_entry.metadata.storage_ids == (32,)
    assert boltp_entry.version == boltp_newer_version
    assert cache.load(boltp_identity).storage_ids == (32,)
    assert beeimg_entry.metadata is beeimg_metadata
    assert beeimg_entry.failure is None
    assert beeimg_entry.version == beeimg_version
    assert cache.load(beeimg_identity) is not None
    assert (event_queries(calls, 'beeimg_cn'),
            event_queries(calls, 'boltp')) == (1, 3)


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

        def prepare_request(self, request):
            """保留显式请求对象供发送边界使用。"""
            return request

        def merge_environment_settings(
                self, url, proxies, stream, verify, cert):
            """返回显式传输设置并隔离真实环境。"""
            return {'proxies': proxies, 'stream': stream, 'verify': verify,
                    'cert': cert}

        def send(self, request, **kwargs):
            """只在 HTTP 边界注入响应、故障或可控时钟推进。"""
            recorded = {'headers': request.headers,
                        'timeout': kwargs['timeout'],
                        'verify': kwargs['verify'],
                        'allow_redirects': kwargs['allow_redirects'],
                        'stream': kwargs['stream']}
            if request.method == 'POST':
                recorded.update(data=request.data, files=request.files)
            state['calls'].append(
                (request.method, request.url, deepcopy(recorded)))
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
    """冷缓存只查组，按组首项上传且保持认证与直链。"""
    state = integration
    state['replies'] = [group_reply(), upload_reply()]
    result = registry_upload(state, [{'provider': provider, 'token': token}],
                             owned=True)
    assert result.success
    assert result.url == upload_reply()[1]['data']['public_url']
    assert result.attempts == (provider,)
    assert result.failures == ()
    expected = ['/group', '/upload']
    assert [url.split('/api/v2')[1] for _, url, _ in state['calls']] == expected
    assert [method for method, _, _ in state['calls']] == ['GET', 'POST']
    for _, _, kwargs in state['calls']:
        assert kwargs['headers'] == ({'Accept': 'application/json',
                                     'Authorization': 'Bearer ' + token}
                                    if token else {'Accept': 'application/json'})
        assert kwargs['allow_redirects'] is False
        assert kwargs['verify'] is True
    assert post_data(state) == [{'storage_id': 13, 'is_public': '1'}]


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
    """外部事件跨图复用组查询但每图只创建一次起点。"""
    state = integration
    state['replies'] = [group_reply(), upload_reply(), upload_reply()]
    first = registry_upload(state)
    state['clock'][0] += 10
    second = registry_upload(state)
    assert first.success and second.success
    assert [call[0] for call in state['calls']] == ['GET', 'POST', 'POST']
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


@pytest.mark.parametrize('manual, expected_id, replaced', [
    (None, 13, True), (True, 13, True), (0, 13, True), (-1, 13, True),
    ('13', 13, True), ([], 13, True), ({}, 13, True), (99, 99, False),
])
def test_registry_manual_validity_and_group_first_with_disk_cache(
        integration, manual, expected_id, replaced):
    """合法手填直传自身编号，非法手填按磁盘命中的组首项静默替换且不改输入。"""
    state = integration
    cache = state['cache']
    assert cache.save(cache.identity(PROVIDER, SECRET), StorageMetadata(
        (13,), 13, None, state['clock'][0]))
    hosts = [{'provider': PROVIDER, 'token': SECRET,
              'options': {'storage_id': manual}}]
    before = deepcopy(hosts)
    state['replies'] = [upload_reply(), upload_reply()]
    first = registry_upload(state, hosts)
    second = registry_upload(state, hosts)
    assert first.success and second.success
    assert hosts == before
    assert [data['storage_id'] for data in post_data(state)] == [
        expected_id, expected_id]
    assert [call[0] for call in state['calls']] == ['POST', 'POST']
    expected_warnings = (('手填储存驱动不可用，已自动替代',) if replaced else ())
    assert first.warnings == second.warnings == expected_warnings
    assert not any(state['context'].storage_state(
        image, 0, PROVIDER, SECRET).correction_used
        for image in state['context']._images)


@pytest.mark.parametrize('reply', [(500, {}), group_reply(())])
def test_registry_lookup_failure_never_posts(integration, reply):
    """查询失败或空组列表后不发送任何 POST，后续图片复用失败快照。"""
    state = integration
    state['replies'] = [reply]
    hosts = [{'provider': PROVIDER, 'token': SECRET}]
    assert not registry_upload(state, hosts).success
    assert not registry_upload(state, hosts).success
    assert len(state['calls']) == 1
    assert not post_data(state)


@pytest.mark.parametrize('provider', ['beeimg_cn'])
def test_registry_precise_rejection_refreshes_and_reposts_once(
        integration, provider):
    """精确拒绝只允许一次刷新重传且不增加配置槽位。"""
    state = integration
    state['replies'] = [group_reply(), rejection_reply(), group_reply((21,)),
                        upload_reply()]
    result = registry_upload(state, [{'provider': provider, 'token': SECRET}])
    assert result.success
    assert result.attempts == (provider,)
    assert [data['storage_id'] for data in post_data(state)] == [13, 21]
    assert [call[0] for call in state['calls']] == [
        'GET', 'POST', 'GET', 'POST']
    assert not state['replies']
    assert all(failure.code == 'storage_unavailable' for failure in result.failures)


@pytest.mark.parametrize('second', ['reject', 'query_fail', 'empty'])
def test_registry_recovery_stops_on_second_failure(integration, second):
    """刷新失败或第二次拒绝终止本站，不遍历存储也不无限重试。"""
    state = integration
    state['replies'] = [group_reply(), rejection_reply()]
    if second == 'reject':
        state['replies'] += [group_reply(), rejection_reply()]
    else:
        state['replies'] += [(500, {}) if second == 'query_fail'
                             else group_reply(())]
    result = registry_upload(state)
    assert not result.success
    assert result.attempts == (PROVIDER,)
    assert len(post_data(state)) == (2 if second == 'reject' else 1)
    assert not state['replies']


def test_registry_invalid_manual_keeps_recovery_budget(integration):
    """非法手填转自动选择不消费预算，精确拒绝后仍有一次恢复。"""
    state = integration
    state['replies'] = [group_reply(), rejection_reply(), group_reply((21,)),
                        upload_reply()]
    result = registry_upload(state, [{'provider': PROVIDER, 'token': SECRET,
                                      'options': {'storage_id': 0}}])
    assert result.success
    assert [data['storage_id'] for data in post_data(state)] == [13, 21]
    assert [call[0] for call in state['calls']] == [
        'GET', 'POST', 'GET', 'POST']
    image, = state['context']._images
    assert state['context'].storage_state(
        image, 0, PROVIDER, SECRET).correction_used is True
    assert result.warnings == ('手填储存驱动不可用，已自动替代',)


def test_registry_rejection_invalidates_disk_cache(integration):
    """首次拒绝删除旧磁盘记录，重新查询落盘后第二次拒绝再删除它。"""
    state = integration
    identity = state['cache'].identity(PROVIDER, SECRET)
    assert state['cache'].save(identity, StorageMetadata(
        (13, 14), None, None, state['clock'][0]))
    state['replies'] = [rejection_reply(), group_reply((21,)),
                        rejection_reply()]
    result = registry_upload(state)
    assert not result.success
    assert [call[0] for call in state['calls']] == ['POST', 'GET', 'POST']
    assert [data['storage_id'] for data in post_data(state)] == [13, 21]
    assert state['cache'].load(identity) is None


def test_registry_exhausted_slot_does_not_reuse_stale_metadata_in_next_slot(
        integration):
    """槽位 A 预算耗尽后，槽位 B 同身份重新查询且不额外重传。"""
    state = integration
    state['replies'] = [
        group_reply(), rejection_reply(), group_reply((21,)),
        rejection_reply(), group_reply((22,)), upload_reply(),
    ]
    hosts = [
        {'provider': PROVIDER, 'token': SECRET,
         'options': {'storage_id': 0}},
        {'provider': PROVIDER, 'token': SECRET},
    ]

    result = registry_upload(state, hosts)

    assert result.success
    assert result.attempts == (PROVIDER, PROVIDER)
    assert [call[0] for call in state['calls']] == [
        'GET', 'POST', 'GET', 'POST', 'GET', 'POST']
    assert [data['storage_id'] for data in post_data(state)] == [13, 21, 22]
    image, = state['context']._images
    assert state['context'].storage_state(
        image, 0, PROVIDER, SECRET).correction_used
    assert state['context'].storage_state(
        image, 1, PROVIDER, SECRET).correction_used is False
    assert not state['replies']


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
    state['replies'] = [group_reply(), reply]
    result = registry_upload(state)
    assert not result.success
    assert result.failures[-1].code == code
    assert len(post_data(state)) == 1
    assert len(state['calls']) == 2


@pytest.mark.parametrize('first_limit, second_limit, duration, shortened', [
    (None, None, 10, False), (0, 0, 10, False), (5, 20, 5, True),
    (20, 5, 5, True), (5, None, 5, True),
])
def test_registry_retry_preserves_deadline_and_shortening_pair(
        integration, first_limit, second_limit, duration, shortened):
    """重传成对继承历史，亚秒取整不误报且刷新只能缩短。"""
    state = integration
    start = state['clock'][0]
    state['replies'] = [group_reply(retention=first_limit), rejection_reply(),
                        group_reply(retention=second_limit), upload_reply()]
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

    state['replies'] = [group_reply(), rejection_reply(), advance]
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


@pytest.mark.parametrize('provider', ['catbox', 'beeimg', 'superbed'])
def test_registry_other_sites_ignore_valid_expiration_without_cache(
        integration, monkeypatch, provider):
    """三站期限仅告警，仍按原四参数单次调用且不触碰缓存。"""
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
        group_reply(), rejection_reply(), group_reply((21,)),
        rejection_reply(), group_reply((21,)), rejection_reply(),
        group_reply((22,)), upload_reply(),
    ]
    hosts = [{'provider': PROVIDER, 'token': SECRET}] * 2
    result = registry_upload(state, hosts)
    assert result.success
    assert result.attempts == (PROVIDER, PROVIDER)
    assert result.failures
    assert all(f.code == 'storage_unavailable' for f in result.failures)
    assert [d['storage_id'] for d in post_data(state)] == [13, 21, 21, 22]
    assert len(state['calls']) == 8
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
    state['replies'] = [group_reply(), failure, group_reply(),
                        upload_reply(), upload_reply()]
    hosts = [{'provider': PROVIDER, 'token': SECRET},
             {'provider': second_provider, 'token': second_token}]
    first = registry_upload(state, hosts)
    second = registry_upload(state, hosts)
    assert first.success and first.provider == second_provider
    assert first.attempts == (PROVIDER, second_provider)
    assert len(first.failures) == 1
    assert second.success and second.provider == PROVIDER
    assert second.attempts == (PROVIDER,)
    assert second.failures == ()
    headers = [kwargs['headers'] for method, _, kwargs in state['calls']
               if method == 'GET']
    expected_second = ({'Accept': 'application/json',
                        'Authorization': 'Bearer ' + second_token}
                       if second_token else {'Accept': 'application/json'})
    assert headers == [{'Accept': 'application/json',
                        'Authorization': 'Bearer ' + SECRET}, expected_second]
    assert [call[0] for call in state['calls']] == [
        'GET', 'POST', 'GET', 'POST', 'POST']
    assert [d['storage_id'] for d in post_data(state)] == [13, 13, 13]
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
    state['replies'] = [group_reply(retention=5), rejection_reply(),
                        group_reply(retention=10), upload_reply()]
    result = registry_upload(state, [{'provider': PROVIDER, 'token': SECRET,
                                      'expiration': '20s'}])
    assert result.success
    assert result.warnings == (
        '存储元数据缓存保存失败', '保存期限已按图床上限缩短',
        '存储元数据缓存失效失败')
    assert state['context'].take_warnings() == ()
    assert SECRET not in repr(result) and BACKUP not in repr(result)


@pytest.mark.parametrize('present, manual, manual_valid, replaced, expected_id', [
    (False, None, False, False, 7),
    (True, 7, True, False, 7),
    (True, 99, True, False, 99),
    (True, '7', False, True, 7),
])
def test_registry_manual_validity_and_group_first_value(
        integration, present, manual, manual_valid, replaced, expected_id):
    """合法手填直传自身编号，非法手填按组首项自动选择并给出替代提示。"""
    state = integration
    options = {'storage_id': manual} if present else {}
    state['replies'] = ([upload_reply()] if manual_valid
                        else [group_reply((7,)), upload_reply()])
    result = registry_upload(state, [{'provider': PROVIDER, 'token': SECRET,
                                      'options': options}])
    assert result.success
    expected_warnings = (('手填储存驱动不可用，已自动替代',) if replaced else ())
    assert result.warnings == expected_warnings
    methods = [method for method, _, _ in state['calls']]
    assert methods == (['POST'] if manual_valid else ['GET', 'POST'])
    assert len(post_data(state)) == 1
    assert [data['storage_id'] for data in post_data(state)] == [expected_id]


def test_registry_never_requests_profile(integration):
    """BeeIMG.CN 主链只请求组与上传，绝不请求账号资料。"""
    state = integration
    state['replies'] = [group_reply((7,)), upload_reply()]
    result = registry_upload(state, [{'provider': PROVIDER, 'token': SECRET}])
    assert result.success
    paths = [url.split('/api/v2')[1] for _, url, _ in state['calls']]
    assert paths == ['/group', '/upload']
    assert not state['replies']


def test_registry_replaced_manual_warning_is_deduplicated(integration):
    """非法手填被组内自动选择替代时给出固定去重提示，并继续一次 POST。"""
    state = integration
    state['replies'] = [group_reply((7,)), upload_reply(), upload_reply()]
    hosts = [{'provider': PROVIDER, 'token': SECRET,
              'options': {'storage_id': '7'}}] * 2
    first = registry_upload(state, hosts)
    second = registry_upload(state, hosts)
    assert first.success and second.success
    assert [method for method, _, _ in state['calls']] == [
        'GET', 'POST', 'POST']
    assert first.warnings == second.warnings == (
        '手填储存驱动不可用，已自动替代',)


def test_registry_recovery_budget_exhausted_after_second_rejection(
        integration):
    """两次精确拒绝后预算耗尽，不再查询也不授权第三次上传。"""
    state = integration
    state['replies'] = [group_reply(), rejection_reply(), group_reply((21,)),
                        rejection_reply()]
    hosts = [{'provider': PROVIDER, 'token': SECRET}]
    result = registry_upload(state, hosts)
    assert not result.success
    assert [method for method, _, _ in state['calls']] == [
        'GET', 'POST', 'GET', 'POST']
    assert [data['storage_id'] for data in post_data(state)] == [13, 21]
    assert result.failures[-1].code == 'storage_unavailable'
    image, = state['context']._images
    assert state['context'].storage_state(
        image, 0, PROVIDER, SECRET).correction_used is True
    assert state['context'].begin_recovery(image, 0, PROVIDER, SECRET) is False


def test_registry_second_rejection_uses_sent_version(
        integration, monkeypatch):
    """第二次精确拒绝只以实际发送代次调用磁盘失效，不改写为无条件清除。"""
    state = integration
    state['replies'] = [group_reply(), rejection_reply(),
                        group_reply((21,)), rejection_reply()]
    context = state['context']
    calls = []
    real = context.invalidate

    def spy(provider, token, *, version=None, disk_only=False):
        """记录真实失效调用的形参后委托实现，不替代失效行为。"""
        calls.append((version, disk_only))
        return real(provider, token, version=version, disk_only=disk_only)

    monkeypatch.setattr(context, 'invalidate', spy)
    result = registry_upload(state, [{'provider': PROVIDER, 'token': SECRET}])
    entry = context._entries[context._identity(PROVIDER, SECRET)]

    assert not result.success
    assert calls[-1][1] is True                 # 最后一次是第二次拒绝的磁盘失效
    assert calls[-1][0] == 2                    # 实测：第二次发送处于事件代次 2
    assert calls[-1][0] == entry.version - 1    # 等于发送当时所处的代次
    assert sum(1 for _, disk_only in calls if disk_only) == 1


def test_registry_recovery_records_two_real_attempts(integration):
    """精确拒绝后的恢复记两次真实尝试与一次回退，槽位只有一次成功。"""
    from modules.operation_trace import trace_item
    from modules.operation_trace import trace_scope

    state = integration
    state['replies'] = [group_reply(), rejection_reply(), group_reply(),
                        upload_reply()]
    events = []
    with trace_scope(
            mode='cli', images_planned=1, slots_planned=1,
            on_status=events.append) as trace:
        with trace_item(image_index=1, slot_index=1):
            result = registry_upload(state, [
                {'provider': PROVIDER, 'token': SECRET}])
        summary = trace.summary()

    assert result.success
    assert [method for method, _, _ in state['calls']] == [
        'GET', 'POST', 'GET', 'POST']
    assert summary['upload_post_attempts'] == 2
    assert summary['postupload_recoveries'] == 1
    assert (summary['group_get_attempts'],
            summary['profile_get_attempts']) == (2, 0)
    attempts = [event for event in events if event.stage == 'upload_attempt']
    assert [event.status for event in attempts] == [
        'started', 'failed', 'started', 'succeeded']
    assert attempts[1].request_id != attempts[3].request_id
    assert (attempts[1].image_index, attempts[1].slot_index) == (1, 1)
    slot = [event for event in events if event.stage == 'slot']
    assert [event.status for event in slot] == ['started', 'succeeded']
    assert summary['slots_succeeded'] == 1
    assert summary['slots_failed'] == 0
    assert not state['replies']
