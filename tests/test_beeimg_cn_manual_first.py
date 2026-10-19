#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证 BeeIMG.CN 手填首次零查询、精确恢复与期限接线。

仅替换 HTTPAdapter.send 与元数据缓存，经真实注册表断言实际请求序列、
缓存调用与期限数字；全部凭证、响应与时钟均为合成值，不访问真实网络。
"""

from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from importlib import import_module
import json
import logging

import pytest
import requests

from modules.image_host.context import UploadContext


URL = 'https://www.beeimg.cn/20261020/synthetic.png'
REJECTION = '不存在的储存驱动'
MANUAL_ID = 13
SECOND_ID = 14
MISSING_ENV = '${SYNTHETIC_MANUAL_FIRST_MISSING_ENV}'
STARTED_AT = 1000.0
DURATION_SECONDS = 3600
FIRST_DEADLINE = STARTED_AT + DURATION_SECONDS
GROUP_LIMIT = 600
_ABSENT = object()


def body(payload):
    """把合成响应编码为 JSON 字节，不含真实服务端内容。"""
    return json.dumps(payload).encode('utf-8')


def group_reply(ids=(MANUAL_ID, SECOND_ID), expire=None, status=200):
    """构造一次 group 查询响应，可显式给出组上限或 HTTP 状态。"""
    if status != 200:
        return (status, b'')
    return (200, body({'status': 'success', 'data': {
        'group': {'options': {'file_expire_seconds': expire}},
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


class Transport:
    """替换 HTTPAdapter.send，记录请求并按队列返回合成响应。"""

    def __init__(self, monkeypatch, replies, clock=None):
        """登记真实发送边界并保存有序响应队列与共享时钟。"""
        self.calls = []
        self.bodies = []
        self.replies = list(replies)
        self.clock = clock

        def send(adapter, request, **kwargs):
            """记录 (方法, 阶段, 存储编号)，返回预设响应或抛出异常。"""
            self.calls.append(
                (request.method, self._stage(request.url),
                 self._storage_id(request)))
            self.bodies.append(request.body)
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
        content_type = request.headers.get('Content-Type', '')
        if not content_type.startswith('multipart/form-data;'):
            return None
        message = BytesParser(policy=default).parsebytes(
            f'Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n'.encode()
            + request.body)
        part = next(
            (item for item in message.iter_parts()
             if item.get_param('name', header='content-disposition')
             == 'storage_id'), None)
        return None if part is None else int(part.get_payload(decode=True))


@dataclass
class Run:
    """保存一次真实上传链记录到的请求序列、缓存调用与业务结果。"""

    calls: list
    cache_calls: list
    bodies: list
    result: object


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    """阻断任何未替身化的真实网络访问并清理合成环境变量。"""
    def forbidden(*args, **kwargs):
        """意外网络访问立即令测试失败。"""
        pytest.fail('手填直传测试禁止真实网络')

    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    monkeypatch.delenv('SYNTHETIC_MANUAL_FIRST_MISSING_ENV', raising=False)


def run(monkeypatch, hosts, replies, *, clock=None, context=None):
    """经真实注册表执行一次上传并返回记录到的真实事实。"""
    registry = import_module('modules.image_host.registry')
    moment = clock if clock is not None else [STARTED_AT]
    cache = CacheSpy()
    ctx = context if context is not None else UploadContext(
        cache=cache, clock=lambda: moment[0])
    transport = Transport(monkeypatch, replies, clock=moment)
    result = registry.upload_with_fallback(
        b'image', 'image.png', hosts, context=ctx)
    if context is None:
        ctx.close()
    return Run(transport.calls, cache.calls, transport.bodies, result)


def post_bodies(run_result):
    """只返回实际 POST 请求体，用于断言表单字段。"""
    return [body_ for body_, call in zip(run_result.bodies, run_result.calls)
            if call[0] == 'POST']


@pytest.mark.parametrize('expiration', [None, '1h'])
def test_manual_first_has_no_metadata_io(monkeypatch, expiration):
    """合法手填首次直传：零 group/profile 查询、零存储缓存 IO。"""
    host = {'provider': 'beeimg_cn', 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}}
    if expiration is not None:
        host['expiration'] = expiration
    run_result = run(monkeypatch, [host], [SUCCESS_UPLOAD])
    calls, cache_calls, result = (
        run_result.calls, run_result.cache_calls, run_result.result)
    assert calls == [('POST', 'upload', 13)]
    assert cache_calls == []
    assert result.success is True


def test_precise_rejection_recovers_once(monkeypatch):
    """首次精确拒绝只触发一次恢复：一次 group 查询后改用剩余首项。"""
    host = {'provider': 'beeimg_cn', 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}}
    run_result = run(monkeypatch, [host], [
        REJECTED_UPLOAD, group_reply(), SUCCESS_UPLOAD])
    calls, result = run_result.calls, run_result.result
    assert calls == [('POST', 'upload', 13), ('GET', 'group', None),
                     ('POST', 'upload', 14)]
    assert result.success is True
    assert all(b'expired_at' not in (body_ or b'')
               for body_ in post_bodies(run_result))


@pytest.mark.parametrize('value', [
    None, 0, -1, '13', True, 1.5, [], {}, _ABSENT,
], ids=lambda value: 'absent' if value is _ABSENT else repr(value))
def test_invalid_manual_uses_group_first(monkeypatch, value):
    """缺省或非法手填一律自动发现：先查组再发送组列表首项。"""
    options = {} if value is _ABSENT else {'storage_id': value}
    host = {'provider': 'beeimg_cn', 'token': 'SYNTHETIC',
            'options': options}
    run_result = run(monkeypatch, [host], [group_reply(), SUCCESS_UPLOAD])
    assert run_result.calls == [('GET', 'group', None),
                                ('POST', 'upload', MANUAL_ID)]
    assert run_result.result.success is True


def test_default_storage_id_never_overrides_group_first(monkeypatch):
    """账号默认编号不参与自动发现，组列表首项 13 胜出且无资料查询。"""
    host = {'provider': 'beeimg_cn', 'token': 'SYNTHETIC', 'options': {}}
    run_result = run(monkeypatch, [host], [group_reply(), SUCCESS_UPLOAD])
    assert run_result.calls == [('GET', 'group', None),
                                ('POST', 'upload', MANUAL_ID)]
    assert run_result.result.success is True


@pytest.mark.parametrize('options', [
    {'storage_id': MANUAL_ID, 'permission': 2},
    {'storage_id': MANUAL_ID, 'album_id': 'x'},
    {'storage_id': MANUAL_ID, 'album_id': True},
])
def test_invalid_other_options_still_reject_with_zero_requests(
        monkeypatch, options):
    """权限与相册非法仍在请求前拒绝，且不发生任何 GET。"""
    host = {'provider': 'beeimg_cn', 'token': 'SYNTHETIC',
            'options': options}
    run_result = run(monkeypatch, [host], [])
    assert run_result.calls == []
    assert run_result.result.success is False
    assert run_result.result.failures[0].code == 'invalid_options'


def test_unknown_option_warns_without_being_sent(monkeypatch, caplog):
    """未知键按既有行为告警并忽略，上传仍成功且请求不携带该键。"""
    host = {'provider': 'beeimg_cn', 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID, 'mystery_key': 'x'}}
    with caplog.at_level(logging.WARNING):
        run_result = run(monkeypatch, [host], [SUCCESS_UPLOAD])
    assert run_result.result.success is True
    assert run_result.calls == [('POST', 'upload', MANUAL_ID)]
    assert [record.getMessage() for record in caplog.records] == [
        'BeeIMG.cn 存在不支持的其他选项，已忽略']
    assert all(b'mystery_key' not in (body_ or b'')
               for body_ in post_bodies(run_result))


def test_invalid_manual_then_recovery_keeps_budget(monkeypatch):
    """非法手填转自动后遇精确拒绝仍能恢复一次，总计两次 POST。"""
    host = {'provider': 'beeimg_cn', 'token': 'SYNTHETIC',
            'options': {'storage_id': '13'}}
    run_result = run(monkeypatch, [host], [
        group_reply(), REJECTED_UPLOAD, group_reply(), SUCCESS_UPLOAD])
    assert run_result.calls == [
        ('GET', 'group', None), ('POST', 'upload', MANUAL_ID),
        ('GET', 'group', None), ('POST', 'upload', SECOND_ID)]
    assert run_result.result.success is True


@pytest.mark.parametrize('reply', [
    (401, b''), (403, b''), (429, b''), (500, b''),
    requests.exceptions.Timeout(),
    (200, b'{invalid'),
    (200, body({'status': 'error', 'message': REJECTION + ' '})),
    (200, body({'status': 'error', 'message': REJECTION + 'x'})),
], ids=['401', '403', '429', '500', 'timeout', 'malformed',
       'message-space', 'message-suffix'])
def test_non_precise_failure_never_recovers(monkeypatch, reply):
    """401/403/429/5xx、超时、畸形 JSON 与相近 message 均只发一次 POST。"""
    host = {'provider': 'beeimg_cn', 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}}
    run_result = run(monkeypatch, [host], [reply])
    assert run_result.calls == [('POST', 'upload', MANUAL_ID)]
    assert run_result.result.success is False


def test_second_failure_or_no_candidate_stops(monkeypatch):
    """第二次精确拒绝后停止；无其他候选时也立即停止，不越权第三次请求。"""
    host = {'provider': 'beeimg_cn', 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}}
    run_result = run(monkeypatch, [host], [
        REJECTED_UPLOAD, group_reply(), REJECTED_UPLOAD])
    assert run_result.calls == [
        ('POST', 'upload', MANUAL_ID), ('GET', 'group', None),
        ('POST', 'upload', SECOND_ID)]
    assert run_result.result.success is False

    second_slot = {'provider': 'beeimg_cn', 'token': MISSING_ENV}
    run_result = run(monkeypatch, [host, second_slot],
                     [REJECTED_UPLOAD, group_reply(ids=(MANUAL_ID,))])
    assert run_result.calls == [('POST', 'upload', MANUAL_ID),
                                ('GET', 'group', None)]
    assert run_result.result.attempts == ('beeimg_cn', 'beeimg_cn')
    assert run_result.result.success is False


@pytest.mark.parametrize(('expire', 'expected', 'shortened'), [
    (GROUP_LIMIT, 1600.0, True),
    (0, FIRST_DEADLINE, False),
    (None, FIRST_DEADLINE, False),
], ids=['limit-600', 'limit-zero', 'limit-absent'])
def test_recovery_deadline_never_extends(monkeypatch, expire, expected,
                                         shortened):
    """恢复沿用原起点与历史期限，只按组上限缩短，不重启倒计时。"""
    registry = import_module('modules.image_host.registry')
    decisions = []
    original = registry.compute_expiration

    def capture(*args, **kwargs):
        """记录每次真实期限决策并返回同一结果。"""
        decision = original(*args, **kwargs)
        decisions.append(decision)
        return decision

    monkeypatch.setattr(registry, 'compute_expiration', capture)
    host = {'provider': 'beeimg_cn', 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}, 'expiration': '1h'}
    run_result = run(monkeypatch, [host], [
        REJECTED_UPLOAD, group_reply(expire=expire), SUCCESS_UPLOAD])
    assert len(decisions) == 2
    assert decisions[0].deadline == FIRST_DEADLINE
    assert decisions[0].shortened is False
    assert decisions[1].deadline == expected
    assert decisions[1].shortened is shortened
    assert run_result.result.success is True


def test_expired_during_group_has_no_second_post(monkeypatch):
    """恢复查询期间已过期时不得发送第二次 POST。"""
    clock = [STARTED_AT]

    def advance():
        """返回组响应前把共享时钟推过首个期限。"""
        clock[0] = FIRST_DEADLINE + 1
        return group_reply()

    host = {'provider': 'beeimg_cn', 'token': 'SYNTHETIC',
            'options': {'storage_id': MANUAL_ID}, 'expiration': '1h'}
    run_result = run(monkeypatch, [host], [REJECTED_UPLOAD, advance],
                     clock=clock)
    assert [call for call in run_result.calls if call[0] == 'POST'] == [
        ('POST', 'upload', MANUAL_ID)]
    assert run_result.result.success is False


def test_failed_auto_does_not_block_manual_upload(monkeypatch):
    """同事件的自动查询失败快照不阻断随后的合法手填直传。"""
    moment = [STARTED_AT]
    cache = CacheSpy()
    context = UploadContext(cache=cache, clock=lambda: moment[0])
    try:
        first = run(monkeypatch,
                    [{'provider': 'beeimg_cn', 'token': 'SYNTHETIC'}],
                    [requests.exceptions.Timeout()],
                    clock=moment, context=context)
        assert first.result.success is False
        before = len(cache.calls)
        second = run(monkeypatch, [{'provider': 'beeimg_cn',
                                    'token': 'SYNTHETIC',
                                    'options': {'storage_id': MANUAL_ID}}],
                     [SUCCESS_UPLOAD], clock=moment, context=context)
    finally:
        context.close()
    assert second.calls == [('POST', 'upload', MANUAL_ID)]
    assert len(cache.calls) == before
    assert second.result.success is True
