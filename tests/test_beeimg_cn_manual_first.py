#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证两站统一的手填首次零查询、精确恢复与期限接线。

仅替换 HTTPAdapter.send 与元数据缓存，经真实注册表断言实际请求序列、
缓存调用与期限数字；全部凭证、响应与时钟均为合成值，不访问真实网络。
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from email.parser import BytesParser
from email.policy import default
from importlib import import_module
import json
import logging
from unittest.mock import MagicMock, Mock
from urllib.parse import urlsplit

import pytest
import requests

from modules import notification as notif
from modules.image_host.context import UploadContext
from modules.image_host.expiration import PreparedV2Options
from modules.screenshot import pipeline
from modules.screenshot.models import CaptureResult


URL = 'https://www.beeimg.cn/20261020/synthetic.png'
REJECTION = '不存在的储存驱动'
MANUAL_ID = 13
SECOND_ID = 14
MISSING_ENV = '${SYNTHETIC_MANUAL_FIRST_MISSING_ENV}'
NOTIFICATION_ENV = 'SYNTHETIC_NOTIFICATION_SECRET_ENV'
NOTIFICATION_TOKEN = 'FAKE_NOTIFICATION_TOKEN_8421'
INVALID_MANUAL = 'FAKE_OPTIONS_8421'
STARTED_AT = 1000.0
DURATION_SECONDS = 3600
FIRST_DEADLINE = STARTED_AT + DURATION_SECONDS
GROUP_LIMIT = 600
PROVIDERS = ('beeimg_cn', 'boltp')
HOSTS = {'beeimg_cn': 'www.beeimg.cn', 'boltp': 'www.boltp.com'}


def invalid_notice(storage_id):
    """非法手填被自动选择后的完整安全提示。"""
    return (f'预设的 options.storage_id 格式无效，本次已自动选择储存编号：'
            f'{storage_id}，未修改配置文件。')


def rejected_notice(storage_id):
    """合法手填被精确拒绝后改用新编号的完整安全提示。"""
    return (f'预设的 options.storage_id 被图床拒绝，本次改用分组列表中的储存编号：'
            f'{storage_id} 重试，未修改配置文件。')


SHORTENED = '保存期限已按图床上限缩短'
UNKNOWN = {
    'beeimg_cn': 'BeeIMG.cn 存在不支持的其他选项，已忽略',
    'boltp': 'Boltp 存在不支持的其他选项，已忽略',
}
_ABSENT = object()


def body(payload):
    """把合成响应编码为 JSON 字节，不含真实服务端内容。"""
    return json.dumps(payload).encode('utf-8')


def group_reply(ids=(SECOND_ID, MANUAL_ID), expire=None, status=200,
                default=None):
    """构造一次 group 查询响应，可显式给出组上限、默认编号或 HTTP 状态。"""
    if status != 200:
        return (status, b'')
    options = {} if expire is _ABSENT else {'file_expire_seconds': expire}
    if default is not None:
        options['default_storage_id'] = default
    return (200, body({'status': 'success', 'data': {
        'group': {'options': options},
        'storages': [{'id': value} for value in ids]}}))


SUCCESS_UPLOAD = (200, body({'status': 'success',
                             'data': {'public_url': URL}}))
REJECTED_UPLOAD = (200, body({'status': 'error', 'message': REJECTION}))


class CacheSpy:
    """记录元数据缓存调用名的离线替身，不读写任何磁盘文件。"""

    def __init__(self):
        """初始化空的调用记录。"""
        self.calls = []

    def identity(self, provider, token):
        """记录一次磁盘身份请求并返回合成标识。"""
        self.calls.append('identity')
        return 'synthetic-disk-identity'

    def load(self, identity):
        """记录一次磁盘读取请求并报告未命中。"""
        self.calls.append('load')
        return None

    def save(self, identity, metadata):
        """记录一次磁盘写入请求并报告成功。"""
        self.calls.append('save')
        return True

    def invalidate(self, identity):
        """记录一次磁盘删除请求并报告成功。"""
        self.calls.append('invalidate')
        return True

    def take_warnings(self):
        """返回空告警，保持与真实缓存相同的取出语义。"""
        return ()


def _form(request):
    """从 multipart 请求体解析表单字段，非上传请求返回空映射。"""
    content_type = request.headers.get('Content-Type', '')
    if not content_type.startswith('multipart/form-data;'):
        return {}
    message = BytesParser(policy=default).parsebytes(
        f'Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n'.encode()
        + request.body)
    return {part.get_param('name', header='content-disposition'):
            part.get_payload(decode=True)
            for part in message.iter_parts()}


class Transport:
    """替换 HTTPAdapter.send，按站点校验记录请求并返回合成响应。"""

    def __init__(self, monkeypatch, provider, replies):
        """登记真实发送边界并保存有序响应队列与期望站点。"""
        self.provider = provider
        self.calls = []
        self.forms = []
        self.authorizations = []
        self.replies = list(replies)

        def send(adapter, request, **kwargs):
            """记录 (方法, 阶段, 存储编号) 与认证头，返回预设响应或抛出异常。"""
            host = urlsplit(request.url).hostname
            assert host == HOSTS[provider], '测试请求串到其他站点'
            self.authorizations.append(
                request.headers.get('Authorization'))
            self.calls.append(
                (request.method, self._stage(request.url),
                 self._storage_id(request)))
            if request.method == 'POST':
                self.forms.append(_form(request))
            reply = self.replies.pop(0)
            if callable(reply):
                reply = reply()
            if isinstance(reply, BaseException):
                raise reply
            status, content = reply
            response = requests.Response()
            response.request = request
            response.url = request.url
            response.status_code = status
            response._content = content
            response._content_consumed = True
            return response

        monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)

    @staticmethod
    def _stage(url):
        """按固定端点后缀判定请求阶段，未知端点立即失败。"""
        for stage, suffix in (('profile', '/user/profile'),
                              ('upload', '/upload'), ('group', '/group')):
            if url.endswith(suffix):
                return stage
        raise AssertionError('测试未预期的请求端点')

    @staticmethod
    def _storage_id(request):
        """从 multipart 请求体读取真实发送的存储编号，GET 返回 None。"""
        if request.method != 'POST':
            return None
        value = _form(request).get('storage_id')
        return None if value is None else int(value)


@dataclass
class Run:
    """保存一次真实上传链记录到的请求、实际缓存与业务结果。"""

    calls: list
    cache: object
    forms: list
    result: object


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    """阻断任何未替身化的真实网络访问并清理合成环境变量。"""
    def forbidden(*args, **kwargs):
        """意外网络访问立即令测试失败。"""
        pytest.fail('手填直传测试禁止真实网络')

    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    monkeypatch.delenv('SYNTHETIC_MANUAL_FIRST_MISSING_ENV', raising=False)


def run(monkeypatch, provider, hosts, replies, *, clock=None, context=None):
    """经真实注册表执行一次上传并返回记录到的真实事实。"""
    registry = import_module('modules.image_host.registry')
    moment = clock if clock is not None else [STARTED_AT]
    owned = context is None
    ctx = (UploadContext(cache=CacheSpy(), clock=lambda: moment[0])
           if owned else context)
    cache = ctx._cache
    transport = Transport(monkeypatch, provider, replies)
    try:
        result = registry.upload_with_fallback(
            b'image', 'image.png', hosts, context=ctx)
    finally:
        if owned:
            ctx.close()
    return Run(transport.calls, cache, transport.forms, result)


def expired_at(deadline):
    """按本机本地时间格式化绝对期限，与生产格式化保持一致。"""
    return datetime.fromtimestamp(deadline).strftime('%Y-%m-%d %H:%M:%S')


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('expiration', [None, '1h'])
def test_manual_first_has_no_metadata_io(monkeypatch, provider, expiration):
    """合法手填首次直传：零 group/profile 查询、零存储缓存 IO。"""
    cache = CacheSpy()
    context = UploadContext(cache=cache, clock=lambda: STARTED_AT)
    host = {'provider': provider, 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}}
    if expiration is not None:
        host['expiration'] = expiration
    try:
        run_result = run(monkeypatch, provider, [host], [SUCCESS_UPLOAD],
                         context=context)
        assert run_result.cache is context._cache
        assert run_result.cache.calls == []
    finally:
        context.close()
    assert run_result.calls == [('POST', 'upload', MANUAL_ID)]
    assert run_result.result.success is True
    form = run_result.forms[0]
    assert form['storage_id'] == b'13'
    if expiration is None:
        assert 'expired_at' not in form
    else:
        assert form['expired_at'] == expired_at(FIRST_DEADLINE).encode()
        assert b'1h' not in b''.join(form.values())


@pytest.mark.parametrize('provider', PROVIDERS)
def test_v2_prepare_has_no_post(monkeypatch, provider):
    """准备函数只返回 PreparedV2Options，绝不调用上传适配器。"""
    registry = import_module('modules.image_host.registry')
    context = UploadContext(cache=CacheSpy(), clock=lambda: STARTED_AT)
    image = context.start_image()
    replies = [group_reply()]
    transport = Transport(monkeypatch, provider, replies)
    attempts = []

    def forbidden(*args, **kwargs):
        """准备阶段一旦执行 POST 立即失败。"""
        attempts.append(args)
        pytest.fail('准备阶段禁止上传')

    monkeypatch.setitem(registry.UPLOADERS, provider, forbidden)
    warnings = []
    try:
        manual = registry._prepare_v2(
            context, image, 0, provider, 'SYNTHETIC',
            {'storage_id': MANUAL_ID}, None, warnings)
        auto = registry._prepare_v2(
            context, image, 0, provider, 'SYNTHETIC', {}, None, warnings,
            transmission=0)
    finally:
        context.close()
    assert isinstance(manual, PreparedV2Options)
    assert manual['storage_id'] == MANUAL_ID
    assert manual.expired_at is None
    assert isinstance(auto, PreparedV2Options)
    assert auto['storage_id'] == SECOND_ID
    assert attempts == []
    assert [call[0] for call in transport.calls] == ['GET']


@pytest.mark.parametrize('provider', PROVIDERS)
def test_precise_rejection_recovers_once(monkeypatch, provider):
    """首次精确拒绝只触发一次恢复：一次 group 查询后改用剩余首项。"""
    host = {'provider': provider, 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}}
    run_result = run(monkeypatch, provider, [host], [
        REJECTED_UPLOAD, group_reply(), SUCCESS_UPLOAD])
    assert run_result.calls == [
        ('POST', 'upload', MANUAL_ID), ('GET', 'group', None),
        ('POST', 'upload', SECOND_ID)]
    assert run_result.result.success is True
    assert run_result.result.warnings.count(rejected_notice(SECOND_ID)) == 1
    assert all('expired_at' not in form for form in run_result.forms)


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('value', [
    None, 0, -1, '13', True, 1.5, [], {},
], ids=lambda value: repr(value))
def test_invalid_manual_uses_group_first(monkeypatch, caplog, provider, value):
    """非法手填一律自动发现：先查组再发送组列表首项。"""
    host = {'provider': provider, 'token': 'SYNTHETIC',
            'options': {'storage_id': value}}
    with caplog.at_level(logging.WARNING):
        run_result = run(monkeypatch, provider, [host],
                         [group_reply(), SUCCESS_UPLOAD])
    assert run_result.calls == [('GET', 'group', None),
                                ('POST', 'upload', SECOND_ID)]
    assert run_result.result.success is True
    assert run_result.result.warnings == (invalid_notice(SECOND_ID),)
    assert '[' not in caplog.text and '{' not in caplog.text


@pytest.mark.parametrize('provider', PROVIDERS)
def test_absent_manual_does_not_claim_replacement(monkeypatch, provider):
    """未填编号走自动选择，但不得冒充“手填已被替代”。"""
    host = {'provider': provider, 'token': 'SYNTHETIC', 'options': {}}
    run_result = run(monkeypatch, provider, [host],
                     [group_reply(), SUCCESS_UPLOAD])
    assert run_result.calls == [('GET', 'group', None),
                                ('POST', 'upload', SECOND_ID)]
    assert run_result.result.success is True
    assert run_result.result.warnings == ()
    assert invalid_notice(SECOND_ID) not in run_result.result.warnings


@pytest.mark.parametrize('provider', PROVIDERS)
def test_group_first_ignores_account_default(monkeypatch, provider):
    """账号默认编号不参与自动发现，组列表首项 14 胜出且无资料查询。"""
    host = {'provider': provider, 'token': 'SYNTHETIC', 'options': {}}
    run_result = run(monkeypatch, provider, [host], [
        group_reply(default=MANUAL_ID), SUCCESS_UPLOAD])
    assert run_result.calls == [('GET', 'group', None),
                                ('POST', 'upload', SECOND_ID)]
    assert run_result.result.success is True


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('options', [
    {'storage_id': MANUAL_ID, 'permission': 2},
    {'storage_id': MANUAL_ID, 'permission': True},
    {'storage_id': MANUAL_ID, 'album_id': 'x'},
    {'storage_id': MANUAL_ID, 'album_id': True},
    {'storage_id': MANUAL_ID, 'is_public': 1},
])
def test_invalid_other_options_still_reject_with_zero_requests(
        monkeypatch, provider, options):
    """权限、相册与原生公开字段非法仍在请求前拒绝，且不发生任何 GET。"""
    host = {'provider': provider, 'token': 'SYNTHETIC',
            'options': options}
    run_result = run(monkeypatch, provider, [host], [])
    assert run_result.calls == []
    assert run_result.result.success is False
    assert run_result.result.failures[0].code == 'invalid_options'


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('extra', [
    {'expiration': 'bad'},
    {'expiration': None},
    {'expiration': '1h', 'options': {'storage_id': MANUAL_ID,
                                     'expired_at': None}},
], ids=['malformed', 'none', 'conflict'])
def test_invalid_expiration_rejects_with_zero_requests(
        monkeypatch, provider, extra):
    """非法或冲突的顶部期限在网络前失败，且零请求。"""
    host = {'provider': provider, 'token': 'SYNTHETIC', **extra}
    host.setdefault('options', {'storage_id': MANUAL_ID})
    run_result = run(monkeypatch, provider, [host], [])
    assert run_result.calls == []
    assert run_result.result.success is False
    failure = run_result.result.failures[0]
    assert (failure.code, failure.stage) == ('config_error', 'expiration')


@pytest.mark.parametrize('provider', PROVIDERS)
def test_unknown_option_warns_without_being_sent(monkeypatch, caplog, provider):
    """未知键按既有行为告警并忽略，上传仍成功且请求不携带该键。"""
    host = {'provider': provider, 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID, 'mystery_key': 'x'}}
    with caplog.at_level(logging.WARNING):
        run_result = run(monkeypatch, provider, [host], [SUCCESS_UPLOAD])
    assert run_result.result.success is True
    assert run_result.calls == [('POST', 'upload', MANUAL_ID)]
    assert [record.getMessage() for record in caplog.records] == [
        UNKNOWN[provider]]
    assert all(b'mystery_key' not in value
               for form in run_result.forms for value in form.values())


@pytest.mark.parametrize('provider', PROVIDERS)
def test_invalid_manual_then_recovery_keeps_budget(monkeypatch, provider):
    """非法手填转自动后遇精确拒绝仍能恢复一次，总计两次 POST。"""
    host = {'provider': provider, 'token': 'SYNTHETIC',
            'options': {'storage_id': '13'}}
    run_result = run(monkeypatch, provider, [host], [
        group_reply(), REJECTED_UPLOAD, group_reply(), SUCCESS_UPLOAD])
    assert run_result.calls == [
        ('GET', 'group', None), ('POST', 'upload', SECOND_ID),
        ('GET', 'group', None), ('POST', 'upload', MANUAL_ID)]
    assert run_result.result.success is True


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('reply', [
    (401, b''), (403, b''), (429, b''), (500, b''),
    requests.exceptions.Timeout(),
    (200, b'{invalid'),
    (400, body({'status': 'error', 'message': REJECTION})),
    (200, body({'status': 'error', 'message': REJECTION + ' '})),
    (200, body({'status': 'error', 'message': ' ' + REJECTION})),
    (200, body({'status': 'error', 'message': REJECTION + 'x'})),
], ids=['401', '403', '429', '500', 'timeout', 'malformed',
       'non-200-exact', 'trailing-space', 'leading-space', 'message-suffix'])
def test_non_precise_failure_never_recovers(monkeypatch, provider, reply):
    """401/403/429/5xx、超时、畸形 JSON 与相近 message 均只发一次 POST。"""
    host = {'provider': provider, 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}}
    run_result = run(monkeypatch, provider, [host], [reply])
    assert run_result.calls == [('POST', 'upload', MANUAL_ID)]
    assert run_result.result.success is False


@pytest.mark.parametrize('provider', PROVIDERS)
def test_second_failure_or_no_candidate_stops(monkeypatch, provider):
    """第二次精确拒绝后停止；无其他候选时也立即停止，不越权第三次请求。"""
    invalidations = []
    original = UploadContext.invalidate

    def capture(self, provider_name, token, *, version=None, disk_only=False):
        """记录失效使用的实际发送版本与 disk_only 语义。"""
        invalidations.append((version, disk_only))
        return original(self, provider_name, token, version=version,
                        disk_only=disk_only)

    monkeypatch.setattr(UploadContext, 'invalidate', capture)
    host = {'provider': provider, 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}}
    run_result = run(monkeypatch, provider, [host], [
        REJECTED_UPLOAD, group_reply(), REJECTED_UPLOAD])
    assert run_result.calls == [
        ('POST', 'upload', MANUAL_ID), ('GET', 'group', None),
        ('POST', 'upload', SECOND_ID)]
    assert run_result.result.success is False
    assert invalidations == [(1, True)]

    invalidations.clear()
    second_slot = {'provider': provider, 'token': MISSING_ENV}
    run_result = run(monkeypatch, provider, [host, second_slot],
                     [REJECTED_UPLOAD, group_reply(ids=(MANUAL_ID,))])
    assert run_result.calls == [('POST', 'upload', MANUAL_ID),
                                ('GET', 'group', None)]
    assert run_result.result.attempts == (provider, provider)
    assert run_result.result.success is False


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('expire, expected, shortened', [
    (GROUP_LIMIT, 1600.0, True),
    (0, FIRST_DEADLINE, False),
    (None, FIRST_DEADLINE, False),
    (_ABSENT, FIRST_DEADLINE, False),
], ids=['limit-600', 'limit-zero', 'limit-none', 'limit-absent'])
def test_v2_manual_expiration_recovery_matrix(
        monkeypatch, provider, expire, expected, shortened):
    """手填首次不预裁剪；恢复只按组上限缩短，不重启倒计时。"""
    registry = import_module('modules.image_host.registry')
    decisions = []
    original = registry.compute_expiration

    def capture(*args, **kwargs):
        """记录每次真实期限决策并返回同一结果。"""
        decision = original(*args, **kwargs)
        decisions.append(decision)
        return decision

    monkeypatch.setattr(registry, 'compute_expiration', capture)
    host = {'provider': provider, 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}, 'expiration': '1h'}
    run_result = run(monkeypatch, provider, [host], [
        REJECTED_UPLOAD, group_reply(expire=expire), SUCCESS_UPLOAD])
    assert len(decisions) == 2
    assert decisions[0].deadline == FIRST_DEADLINE
    assert decisions[0].shortened is False
    assert decisions[1].deadline == expected
    assert decisions[1].shortened is shortened
    assert [form['expired_at'] for form in run_result.forms] == [
        expired_at(FIRST_DEADLINE).encode(), expired_at(expected).encode()]
    assert run_result.result.success is True


@pytest.mark.parametrize('provider', PROVIDERS)
def test_v2_auto_expiration_keeps_shortening(monkeypatch, provider):
    """自动首次按组上限缩短后，恢复获得更大上限也不得延长。"""
    registry = import_module('modules.image_host.registry')
    decisions = []
    original = registry.compute_expiration

    def capture(*args, **kwargs):
        """记录每次真实期限决策并返回同一结果。"""
        decision = original(*args, **kwargs)
        decisions.append(decision)
        return decision

    monkeypatch.setattr(registry, 'compute_expiration', capture)
    host = {'provider': provider, 'token': 'SYNTHETIC', 'expiration': '1h'}
    run_result = run(monkeypatch, provider, [host], [
        group_reply(expire=GROUP_LIMIT), REJECTED_UPLOAD,
        group_reply(expire=7200), SUCCESS_UPLOAD])
    assert [decision.deadline for decision in decisions] == [1600.0, 1600.0]
    assert [decision.shortened for decision in decisions] == [True, True]
    assert [form['expired_at'] for form in run_result.forms] == [
        expired_at(1600.0).encode(), expired_at(1600.0).encode()]
    assert run_result.result.success is True


@pytest.mark.parametrize('provider', PROVIDERS)
def test_expired_during_group_has_no_second_post(monkeypatch, provider):
    """恢复查询期间已过期时不得发送第二次 POST。"""
    clock = [STARTED_AT]

    def advance():
        """返回组响应前把共享时钟推过首个期限。"""
        clock[0] = FIRST_DEADLINE
        return group_reply()

    host = {'provider': provider, 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}, 'expiration': '1h'}
    run_result = run(monkeypatch, provider, [host], [REJECTED_UPLOAD, advance],
                     clock=clock)
    assert [call for call in run_result.calls if call[0] == 'POST'] == [
        ('POST', 'upload', MANUAL_ID)]
    assert run_result.result.success is False
    assert run_result.result.failures[-1].code == 'config_error'
    assert rejected_notice(SECOND_ID) not in run_result.result.warnings


@pytest.mark.parametrize('provider', PROVIDERS)
def test_failed_auto_does_not_block_manual_upload(monkeypatch, provider):
    """同事件的自动查询失败快照不阻断随后的合法手填直传。"""
    moment = [STARTED_AT]
    cache = CacheSpy()
    context = UploadContext(cache=cache, clock=lambda: moment[0])
    try:
        first = run(monkeypatch, provider,
                    [{'provider': provider, 'token': 'SYNTHETIC'}],
                    [requests.exceptions.Timeout()],
                    clock=moment, context=context)
        assert first.result.success is False
        before = len(cache.calls)
        second = run(monkeypatch, provider, [{
            'provider': provider, 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}}],
            [SUCCESS_UPLOAD], clock=moment, context=context)
    finally:
        context.close()
    assert second.calls == [('POST', 'upload', MANUAL_ID)]
    assert len(cache.calls) == before
    assert second.result.success is True


@pytest.mark.parametrize('provider', PROVIDERS)
def test_auto_failure_then_rejected_manual_recovers_once(monkeypatch, provider):
    """自动查询失败后手填直传，被拒只重查一次并替代成功。"""
    moment = [STARTED_AT]
    cache = CacheSpy()
    context = UploadContext(cache=cache, clock=lambda: moment[0])
    invalidations = []
    original = UploadContext.invalidate

    def capture(self, provider_name, token, *, version=None, disk_only=False):
        """记录恢复失效使用的实际发送代次与 disk_only 语义。"""
        invalidations.append((version, disk_only))
        return original(self, provider_name, token, version=version,
                        disk_only=disk_only)

    monkeypatch.setattr(UploadContext, 'invalidate', capture)
    try:
        auto = run(monkeypatch, provider,
                   [{'provider': provider, 'token': 'SYNTHETIC'}],
                   [(503, b'')], clock=moment, context=context)
        manual = run(monkeypatch, provider, [{
            'provider': provider, 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}}],
            [REJECTED_UPLOAD, group_reply(), SUCCESS_UPLOAD],
            clock=moment, context=context)
    finally:
        context.close()
    assert auto.calls == [('GET', 'group', None)]
    assert auto.result.success is False
    assert auto.result.failures[0].code == 'http_failed'
    assert manual.calls == [
        ('POST', 'upload', MANUAL_ID), ('GET', 'group', None),
        ('POST', 'upload', SECOND_ID)]
    assert [call[0] for call in auto.calls + manual.calls] == [
        'GET', 'POST', 'GET', 'POST']
    assert [call for call in auto.calls + manual.calls
            if call[0] == 'POST'] == [
        ('POST', 'upload', MANUAL_ID), ('POST', 'upload', SECOND_ID)]
    assert all(call[1] != 'profile' for call in auto.calls + manual.calls)
    assert manual.result.success is True
    assert manual.result.url == URL
    assert manual.result.warnings.count(rejected_notice(SECOND_ID)) == 1
    assert invalidations == [(1, False)]
    assert cache.calls == ['identity', 'load', 'invalidate', 'save']


@pytest.mark.parametrize('provider', PROVIDERS)
def test_rejected_manual_allows_next_manual_attempt(monkeypatch, provider):
    """手填被拒绝并完成恢复后，同事件下一次合法手填仍直接发送。"""
    moment = [STARTED_AT]
    cache = CacheSpy()
    context = UploadContext(cache=cache, clock=lambda: moment[0])
    host = {'provider': provider, 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}}
    try:
        first = run(monkeypatch, provider, [host], [
            REJECTED_UPLOAD, group_reply(), requests.exceptions.Timeout()],
            clock=moment, context=context)
        assert first.calls == [
            ('POST', 'upload', MANUAL_ID), ('GET', 'group', None),
            ('POST', 'upload', SECOND_ID)]
        before = len(cache.calls)
        second = run(monkeypatch, provider, [host], [SUCCESS_UPLOAD],
                     clock=moment, context=context)
    finally:
        context.close()
    assert first.result.success is False
    assert second.calls == [('POST', 'upload', MANUAL_ID)]
    assert len(cache.calls) == before
    assert second.result.success is True


@pytest.mark.parametrize('provider', PROVIDERS)
def test_invalid_manual_notice_on_each_successful_preparation(
        monkeypatch, provider):
    """非法手填在首轮与恢复轮各给出一次该轮实际编号的提示。"""
    host = {'provider': provider, 'token': 'SYNTHETIC',
            'options': {'storage_id': 0}}
    run_result = run(monkeypatch, provider, [host], [
        group_reply((7,)), REJECTED_UPLOAD, group_reply((23,)),
        SUCCESS_UPLOAD])

    assert run_result.calls == [
        ('GET', 'group', None), ('POST', 'upload', 7),
        ('GET', 'group', None), ('POST', 'upload', 23)]
    assert run_result.result.success is True
    assert run_result.result.warnings == (
        invalid_notice(7), invalid_notice(23))


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('manual, first_notice', [
    (0, (invalid_notice(7),)), (7, ()),
])
def test_recovery_candidate_exhaustion_has_no_second_upload(
        monkeypatch, provider, manual, first_notice):
    """恢复列表只剩被拒编号时按候选耗尽失败，不再上传或新增提示。"""
    host = {'provider': provider, 'token': 'SYNTHETIC',
            'options': {'storage_id': manual}}
    replies = [REJECTED_UPLOAD, group_reply((7,))]
    if manual == 0:
        replies.insert(0, group_reply((7,)))
    run_result = run(monkeypatch, provider, [host], replies)

    posts = [call for call in run_result.calls if call[0] == 'POST']
    assert posts == [('POST', 'upload', 7)]
    assert run_result.result.success is False
    assert run_result.result.failures[-1].code == 'storage_unavailable'
    assert run_result.result.warnings == first_notice


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('phase', ('first', 'recovery'))
def test_group_403_remains_http_failed_without_profile(
        monkeypatch, provider, phase):
    """组查询 403 归网络请求失败，不请求资料也不继续该轮上传。"""
    host = {'provider': provider, 'token': 'SYNTHETIC'}
    if phase == 'recovery':
        host['options'] = {'storage_id': MANUAL_ID}
        replies = [REJECTED_UPLOAD, group_reply(status=403)]
    else:
        replies = [group_reply(status=403)]
    run_result = run(monkeypatch, provider, [host], replies)

    failure = run_result.result.failures[-1]
    assert failure.code == 'http_failed'
    assert failure.stage == 'group'
    assert failure.http_status == 403
    expected_calls = (
        [('POST', 'upload', MANUAL_ID), ('GET', 'group', None)]
        if phase == 'recovery' else [('GET', 'group', None)])
    assert run_result.calls == expected_calls
    assert [call for call in run_result.calls if call[0] == 'POST'] == (
        [('POST', 'upload', MANUAL_ID)] if phase == 'recovery' else [])
    assert all(call[1] != 'profile' for call in run_result.calls)
    assert run_result.result.warnings == ()


def test_notification_end_to_end_uses_real_recovery_chain(
        monkeypatch, caplog, tmp_path):
    """真实通知链贯通非法手填恢复：7 被拒改用 23，两条提示按序进入载荷。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.setenv(NOTIFICATION_ENV, NOTIFICATION_TOKEN)
    capture = Mock(return_value=CaptureResult(b'image', 'window', '窗口', 2, 2))
    monkeypatch.setattr(pipeline, 'capture', capture)
    transport = Transport(monkeypatch, 'beeimg_cn', [
        group_reply((7,)), REJECTED_UPLOAD, group_reply((23,)),
        SUCCESS_UPLOAD])
    sent = []
    notifier = MagicMock()

    def notify(title=None, content=None, **params):
        """记录真实发送载荷并返回固定的业务成功响应。"""
        sent.append((title, content))
        response = MagicMock()
        response.status_code = 200
        response.text = 'ok'
        response.json.return_value = {'code': 0}
        return response

    notifier.notify.side_effect = notify
    monkeypatch.setattr(notif, 'get_notifier', Mock(return_value=notifier))
    config = {
        'push': {
            'screenshot': {
                'targets': [{'provider': 'window', 'target': '窗口'}],
                'image_host': [{
                    'provider': 'beeimg_cn',
                    'token': '${' + NOTIFICATION_ENV + '}',
                    'options': {'storage_id': INVALID_MANUAL},
                }],
            },
            'templates': {
                'on_end': {
                    'enable': True,
                    'capture_screenshot': True,
                    'title': '标题 {screenshot}',
                    'content': '前文\n{screenshot}\n后文',
                },
            },
            'push_channel_settings': {
                'channels': [{'provider': 'serverchan', 'sckey': 's1'}],
            },
            'retry': {'interval': '0s', 'max_count': 1},
        },
    }
    before = deepcopy(config)

    with caplog.at_level(logging.DEBUG):
        results = notif.send_notification(
            config, 'on_end', process_name='demo')

    assert results == [('serverchan', True)]
    assert transport.calls == [
        ('GET', 'group', None), ('POST', 'upload', 7),
        ('GET', 'group', None), ('POST', 'upload', 23)]
    assert config == before
    assert len(sent) == 1
    assert set(transport.authorizations) == {'Bearer ' + NOTIFICATION_TOKEN}
    title, content = sent[0]
    first = invalid_notice(7)
    second = invalid_notice(23)
    assert first in title and second in title
    assert first in content and second in content
    assert title.count(first) == title.count(second) == 1
    assert content.count(first) == content.count(second) == 1
    assert title.index(first) < title.index(second)
    assert content.index(first) < content.index(second)
    assert URL in content
    assert REJECTION not in str(sent)
    assert NOTIFICATION_TOKEN not in str(sent)
    assert NOTIFICATION_TOKEN not in caplog.text
    assert INVALID_MANUAL not in str(sent)
    assert INVALID_MANUAL not in caplog.text
    assert capture.call_count == 1
    records = [record for record in caplog.records
               if record.name.startswith('modules.')]
    assert records
    assert all(record.exc_info is None for record in records)


@pytest.mark.parametrize('provider', PROVIDERS)
@pytest.mark.parametrize('duplicate_warnings', [False, True])
@pytest.mark.parametrize(('operation', 'notice'), [
    ('identity', '存储元数据缓存不可用'),
    ('load', '存储元数据缓存读取失败'),
    ('invalidate', '存储元数据缓存失效失败'),
])
def test_notification_cache_exception_keeps_image_and_deduplicates_notice(
        monkeypatch, caplog, tmp_path, provider, duplicate_warnings,
        operation, notice):
    """缓存异常经真实通知链安全降级，重复提示不替代图片或增加请求。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.setenv(NOTIFICATION_ENV, NOTIFICATION_TOKEN)
    secrets = (str(tmp_path / 'PRIVATE_CACHE_8421'), NOTIFICATION_ENV,
               NOTIFICATION_TOKEN, 'https://private.invalid/SECRET_CACHE_8421')
    cache = Mock(wraps=CacheSpy())
    getattr(cache, operation).side_effect = OSError(' '.join(secrets))
    cache.take_warnings.return_value = (
        (notice, notice) if duplicate_warnings else ())
    cache_factory = Mock(return_value=cache)
    monkeypatch.setattr(
        import_module('modules.image_host.context'), 'StorageCache',
        cache_factory)
    capture = Mock(return_value=CaptureResult(b'image', 'window', '窗口', 2, 2))
    monkeypatch.setattr(pipeline, 'capture', capture)
    replies = [group_reply((7,)), SUCCESS_UPLOAD]
    expected_calls = [('GET', 'group', None), ('POST', 'upload', 7)]
    cache_operations = ['identity', 'load', 'save']
    if operation == 'invalidate':
        replies = [group_reply((7,)), REJECTED_UPLOAD,
                   group_reply((23,)), SUCCESS_UPLOAD]
        expected_calls += [('GET', 'group', None), ('POST', 'upload', 23)]
        cache_operations += ['invalidate', 'save']
    transport = Transport(monkeypatch, provider, replies)
    response = Mock(status_code=200, text='ok')
    response.json.return_value = {'code': 0}
    notifier = Mock()
    notifier.notify.return_value = response
    monkeypatch.setattr(notif, 'get_notifier', Mock(return_value=notifier))
    config_module = import_module('modules.config')
    config_guards = []
    for name in ('load_config', 'create_default_config', '_make_write_yaml'):
        guard = Mock(side_effect=AssertionError('通知不得加载或写入配置'))
        monkeypatch.setattr(config_module, name, guard)
        config_guards.append(guard)
    config = {
        'push': {
            'screenshot': {
                'targets': [{'provider': 'window', 'target': '窗口'}],
                'image_host': [{'provider': provider,
                                'token': '${' + NOTIFICATION_ENV + '}'}],
            },
            'templates': {
                'on_end': {
                    'enable': True,
                    'capture_screenshot': True,
                    'title': '标题 {screenshot}',
                    'content': '前文\n{screenshot}\n后文',
                },
            },
            'push_channel_settings': {
                'channels': [{'provider': 'serverchan', 'sckey': 's1'}],
            },
            'retry': {'interval': '0s', 'max_count': 3},
        },
    }
    before = deepcopy(config)

    with caplog.at_level(logging.DEBUG):
        results = notif.send_notification(config, 'on_end', process_name='demo')

    assert results == [('serverchan', True)]
    assert config == before
    for guard in config_guards:
        guard.assert_not_called()
    cache_factory.assert_called_once()
    assert [call[0] for call in cache.mock_calls
            if call[0] != 'take_warnings'] == cache_operations
    getattr(cache, operation).assert_called_once()
    assert cache.take_warnings.call_count == len(cache_operations)
    assert transport.calls == expected_calls
    assert transport.replies == []
    assert set(transport.authorizations) == {'Bearer ' + NOTIFICATION_TOKEN}
    capture.assert_called_once()
    notifier.notify.assert_called_once()
    payload = notifier.notify.call_args.kwargs
    rendered = f'![screenshot_1]({URL})\n\n---\n\n截图配置提示: {notice}'
    assert payload['title'] == '标题 ' + rendered
    assert payload['content'] == '前文\n' + rendered + '\n后文'
    assert payload['title'].count(notice) == 1
    assert payload['content'].count(notice) == 1
    for secret in secrets:
        assert secret not in str(payload)
        assert secret not in caplog.text
    records = [record for record in caplog.records
               if record.name.startswith('modules.')]
    assert records
    assert all(record.exc_info is None for record in records)
