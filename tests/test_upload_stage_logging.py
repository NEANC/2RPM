#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证缓存查询、真实请求与恢复/回退按真实操作记录与计数。"""

from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from importlib import import_module
import json
import logging
import socket

import pytest
import requests

from modules.image_host.context import UploadContext
from modules.image_host.storage import StorageMetadata
from modules.image_host.storage_cache import StorageCache
from modules.operation_trace import trace_item
from modules.operation_trace import trace_scope


URL = 'https://images.example/synthetic.png'
SECRET = 'FAKE_UPLOAD_SECRET_7319'
REJECTION = '不存在的储存驱动'
MISSING_ENV = '${SYNTHETIC_UPLOAD_MISSING_ENV}'


def body(payload):
    """把合成响应编码为 JSON 字节，不含真实服务端内容。"""
    return json.dumps(payload).encode('utf-8')


def group_reply(ids=(13, 14), expire=None, status=200):
    """构造一次 group 查询响应，可选固定期限。"""
    if status != 200:
        return (status, b'')
    return (200, body({'status': 'success', 'data': {
        'group': {'options': {'file_expire_seconds': expire}},
        'storages': [{'id': value} for value in ids]}}))


def profile_reply(default=14, status=200):
    """构造一次 profile 查询响应，可显式返回 403。"""
    if status != 200:
        return (status, b'')
    return (200, body({'status': 'success', 'data': {'options': {
        'default_storage_id': default}}}))


SUCCESS_UPLOAD = (200, body({'status': 'success',
                             'data': {'public_url': URL}}))
REJECTED_UPLOAD = (200, body({'status': 'error', 'message': REJECTION}))


class Transport:
    """替换 HTTPAdapter.send，记录真实请求并按队列返回合成响应。"""

    def __init__(self, monkeypatch, replies):
        """登记真实发送边界并保存有序响应队列。"""
        self.sent = []
        self.replies = list(replies)

        def send(adapter, request, **kwargs):
            """记录一次真实请求，返回预设响应或抛出传输异常。"""
            self.sent.append(request)
            reply = self.replies.pop(0)
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

    @property
    def posts(self):
        """返回按发生顺序记录的真实 POST 请求。"""
        return [item for item in self.sent if item.method == 'POST']

    def gets(self, stage):
        """按端点路径返回真实 GET 请求，区分 group 与 profile。"""
        suffix = '/user/profile' if stage == 'profile' else '/group'
        return [item for item in self.sent
                if item.method == 'GET' and item.url.endswith(suffix)]


def storage_ids(transport):
    """从 multipart 请求体读取真实发送的存储编号，保持发送顺序。"""
    values = []
    for request in transport.posts:
        content_type = request.headers.get('Content-Type', '')
        if not content_type.startswith('multipart/form-data;'):
            values.append(None)
            continue
        message = BytesParser(policy=default).parsebytes(
            f'Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n'.encode()
            + request.body)
        part = next(
            (item for item in message.iter_parts()
             if item.get_param('name', header='content-disposition')
             == 'storage_id'), None)
        values.append(
            None if part is None else int(part.get_payload(decode=True)))
    return values


@dataclass
class Run:
    """保存一次真实上传链的传输替身、事件、汇总与业务结果。"""

    transport: Transport
    events: list
    summary: dict
    results: list
    trace: object


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """隔离缓存根并禁止任何未替身化的真实网络访问。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.delenv('SYNTHETIC_UPLOAD_MISSING_ENV', raising=False)

    def forbidden(*args, **kwargs):
        """未替身化网络立即令测试失败。"""
        pytest.fail('上传阶段测试禁止真实网络')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def build(monkeypatch, tmp_path, *, replies, clock=None):
    """建立共享缓存与事件时钟的普通上传事件。"""
    moment = clock if clock is not None else [1000.0]
    cache = StorageCache(tmp_path / 'cache', clock=lambda: moment[0])
    context = UploadContext(cache=cache, clock=lambda: moment[0])
    return context, cache, Transport(monkeypatch, replies)


def run_chain(monkeypatch, tmp_path, hosts, *, replies, images=(1,),
              clock=None, slot_index=None, slots_planned=None, mode='cli'):
    """在真实注册表上传链上执行一次上传并返回真实事实。"""
    registry = import_module('modules.image_host.registry')
    context, cache, transport = build(
        monkeypatch, tmp_path, replies=replies, clock=clock)
    events = []
    results = []
    planned = (len(hosts) * len(images) if slots_planned is None
               else slots_planned)
    with trace_scope(mode=mode, images_planned=len(images),
                     slots_planned=planned,
                     on_status=events.append) as trace:
        for image_index in images:
            with trace_item(image_index=image_index, slot_index=slot_index):
                results.append(registry.upload_with_fallback(
                    b'image', 'image.png', hosts, context=context))
        summary = trace.summary()
    context.close()
    return Run(transport, events, summary, results, trace)


def run_metadata(monkeypatch, tmp_path, *, replies, provider='beeimg_cn',
                 token='', clock=None):
    """直接经事件上下文执行一次元数据读取并返回真实事实。"""
    context, cache, transport = build(
        monkeypatch, tmp_path, replies=replies, clock=clock)
    events = []
    errors = []
    with trace_scope(mode='cli', on_status=events.append) as trace:
        with trace_item(image_index=1):
            try:
                metadata = context.get_metadata(provider, token)
            except Exception as error:
                errors.append(error)
                metadata = None
        summary = trace.summary()
    context.close()
    return transport, events, summary, metadata, errors


def of_stage(events, stage):
    """按阶段过滤事件，保持真实发生顺序。"""
    return [event for event in events if event.stage == stage]


def slots(events):
    """返回槽位阶段事件。"""
    return of_stage(events, 'slot')


def test_boltp_zero_and_candidate_switch_do_not_get(monkeypatch, tmp_path):
    """Boltp 手填 0 合法且候选切换不增加任何网络查询。"""
    run = run_chain(
        monkeypatch, tmp_path,
        [{'provider': 'boltp', 'token': '',
          'options': {'storage_id': 0}}],
        replies=[REJECTED_UPLOAD, REJECTED_UPLOAD, SUCCESS_UPLOAD])
    assert storage_ids(run.transport) == [0, 2, 3]
    assert (run.summary['group_get_attempts'],
            run.summary['profile_get_attempts']) == (0, 0)
    assert run.summary['upload_post_attempts'] == 3
    assert run.summary['boltp_candidate_switches'] == 2
    selection = of_stage(run.events, 'storage_select')
    assert selection[0].storage_id == 0
    assert selection[0].selection_source == 'manual'
    assert run.transport.gets('group') == []
    assert len(run.transport.posts) == run.summary['upload_post_attempts'] == 3
    assert run.summary['slots_succeeded'] == 1
    assert run.summary['slots_failed'] == 0


def test_boltp_expiration_get_is_shared(monkeypatch, tmp_path):
    """同凭证多图在有期限时只共享一次 group 查询且始终零 profile。"""
    run = run_chain(
        monkeypatch, tmp_path,
        [{'provider': 'boltp', 'token': '', 'expiration': '1h',
          'options': {'storage_id': 0}}],
        replies=[group_reply(expire=60), REJECTED_UPLOAD, SUCCESS_UPLOAD,
                 SUCCESS_UPLOAD],
        images=(1, 2))
    assert (run.summary['group_get_attempts'],
            run.summary['profile_get_attempts']) == (1, 0)
    assert len(run.transport.gets('group')) == 1
    assert run.transport.gets('profile') == []
    assert run.summary['upload_post_attempts'] == 3
    assert run.summary['boltp_candidate_switches'] == 1


@pytest.mark.parametrize(('manual', 'expected'), [
    (0, [0, 2, 3]), (2, [2, 3]), (3, [3, 2]), (5, [5, 2, 3]),
])
def test_boltp_candidate_order_keeps_dedup(manual, expected, monkeypatch,
                                            tmp_path):
    """候选按手填、2、3 保序去重且切换不增加 GET。"""
    run = run_chain(
        monkeypatch, tmp_path,
        [{'provider': 'boltp', 'token': '',
          'options': {'storage_id': manual}}],
        replies=[REJECTED_UPLOAD, REJECTED_UPLOAD, REJECTED_UPLOAD])
    assert storage_ids(run.transport) == expected
    assert run.summary['group_get_attempts'] == 0
    assert run.summary['upload_post_attempts'] == len(expected)


@pytest.mark.parametrize('storage_id', [True, -1, 1.5, '0', None, []])
def test_boltp_invalid_manual_stops_before_post(storage_id, monkeypatch,
                                                tmp_path):
    """非法布尔或负数手填在传输前停止且零 POST。"""
    run = run_chain(
        monkeypatch, tmp_path,
        [{'provider': 'boltp', 'token': '',
          'options': {'storage_id': storage_id}}],
        replies=[])
    assert run.transport.posts == []
    assert run.summary['upload_post_attempts'] == 0
    assert run.summary['slots_failed'] == 1


def test_preupload_refresh_and_post_recovery_have_one_budget(
        monkeypatch, tmp_path):
    """上传前刷新消费预算后，精确拒绝不再重传且只记跳过。"""
    context, cache, transport = build(
        monkeypatch, tmp_path,
        replies=[group_reply(), profile_reply(default=None), REJECTED_UPLOAD])
    identity = cache.identity('beeimg_cn', SECRET)
    assert cache.save(identity, StorageMetadata((13, 14), 14, None, 1000.0))
    events = []
    with trace_scope(mode='cli', on_status=events.append) as trace:
        with trace_item(image_index=1):
            result = import_module(
                'modules.image_host.registry').upload_with_fallback(
                    b'image', 'image.png',
                    [{'provider': 'beeimg_cn', 'token': SECRET,
                      'options': {'storage_id': 99}}], context=context)
        summary = trace.summary()
    context.close()
    assert not result.success
    assert (summary['group_get_attempts'], summary['profile_get_attempts'],
            summary['upload_post_attempts']) == (1, 1, 1)
    assert summary['preupload_refreshes'] == 1
    assert summary['postupload_recoveries'] == 0
    fallback = [event for event in of_stage(events, 'fallback')
                if event.status != 'started']
    assert [(event.fallback_kind, event.status, event.reason_code)
            for event in fallback] == [
        ('preupload_refresh', 'succeeded', 'manual_replaced'),
        ('postupload_recovery', 'skipped', 'budget_used')]
    assert len(transport.posts) == 1
    assert [event.status for event in slots(events)] == ['started', 'failed']
    assert summary['slots_failed'] == 1


def test_cached_failure_has_no_request_id(monkeypatch, tmp_path):
    """事件内共享查询失败只复用一次真实请求，不伪造请求标识。"""
    run = run_chain(
        monkeypatch, tmp_path,
        [{'provider': 'beeimg_cn', 'token': SECRET}],
        replies=[requests.exceptions.Timeout()], images=(1, 2))
    assert len(run.transport.gets('group')) == 1
    assert run.summary['group_get_attempts'] == 1
    assert run.summary['upload_post_attempts'] == 0
    queries = [event for event in of_stage(run.events, 'metadata_query')
               if event.status != 'started']
    assert len(queries) == 1 and queries[0].request_id is not None
    assert queries[0].status == 'failed'
    reuse = [event for event in of_stage(run.events, 'metadata_cache')
             if event.reason_code == 'reused_failure']
    assert reuse and all(event.request_id is None for event in reuse)
    assert run.summary['slots_failed'] == 2


def test_timeout_counts_once_without_retransmission(monkeypatch, tmp_path):
    """上传超时只记一次真实尝试且不触发任何恢复。"""
    run = run_chain(
        monkeypatch, tmp_path,
        [{'provider': 'beeimg_cn', 'token': '', 'options': {'storage_id': 13}}],
        replies=[group_reply(), requests.exceptions.Timeout()])
    assert run.summary['upload_post_attempts'] == 1
    assert len(run.transport.posts) == 1
    assert run.summary['postupload_recoveries'] == 0
    assert not [event for event in of_stage(run.events, 'fallback')]
    attempts = of_stage(run.events, 'upload_attempt')
    assert [event.status for event in attempts] == ['started', 'failed']
    assert attempts[1].reason_code == 'transport_failed'
    assert run.summary['slots_failed'] == 1


@pytest.mark.parametrize('scenario', [
    'missing_credential', 'query_failure', 'profile_forbidden',
    'selection_failure',
])
def test_preupload_failure_finishes_slot_without_post(
        scenario, monkeypatch, tmp_path):
    """前置失败恰好一个槽位失败、零 POST、无上传尝试事件。"""
    hosts = [{'provider': 'beeimg_cn', 'token': SECRET,
              'options': {'storage_id': 13}}]
    replies = []
    if scenario == 'missing_credential':
        hosts = [{'provider': 'beeimg_cn', 'token': MISSING_ENV}]
    elif scenario == 'query_failure':
        hosts = [{'provider': 'beeimg_cn', 'token': SECRET}]
        replies = [requests.exceptions.Timeout()]
    elif scenario == 'profile_forbidden':
        hosts = [{'provider': 'beeimg_cn', 'token': SECRET}]
        replies = [group_reply(), profile_reply(status=403)]
    else:
        hosts = [{'provider': 'beeimg_cn', 'token': SECRET,
                  'options': {'storage_id': True}}]
        replies = [group_reply(), profile_reply(status=403)]
    run = run_chain(monkeypatch, tmp_path, hosts, replies=replies)
    assert run.transport.posts == []
    assert run.summary['upload_post_attempts'] == 0
    assert of_stage(run.events, 'upload_attempt') == []
    assert run.summary['slots_failed'] == 1
    assert run.summary['slots_succeeded'] == 0
    slot = slots(run.events)
    assert [event.status for event in slot] == ['started', 'failed']
    assert slot[0].operation_id == slot[1].operation_id
    assert slot[0].request_id is None and slot[1].attempt_index is None
    assert not run.results[0].success


def test_recovery_success_finishes_slot_once(monkeypatch, tmp_path):
    """精确拒绝后恢复成功：两次真实请求、槽位仅一次成功终态。"""
    run = run_chain(
        monkeypatch, tmp_path,
        [{'provider': 'beeimg_cn', 'token': '', 'options': {'storage_id': 13}}],
        replies=[group_reply(), REJECTED_UPLOAD, group_reply(),
                 SUCCESS_UPLOAD], slot_index=4)
    assert run.transport.replies == []
    assert run.results[0].success
    assert run.summary['upload_post_attempts'] == 2
    assert run.summary['postupload_recoveries'] == 1
    assert run.summary['group_get_attempts'] == len(
        run.transport.gets('group')) == 2
    attempts = of_stage(run.events, 'upload_attempt')
    assert [event.status for event in attempts] == [
        'started', 'failed', 'started', 'succeeded']
    failed = attempts[1]
    succeeded = attempts[3]
    assert failed.request_id != succeeded.request_id
    assert failed.operation_id != succeeded.operation_id
    assert (failed.image_index, failed.slot_index) == (1, 4)
    assert failed.slot_index == succeeded.slot_index
    assert attempts[0].attempt_index == 1
    assert attempts[2].attempt_index == 2
    slot = slots(run.events)
    assert [event.status for event in slot] == ['started', 'succeeded']
    assert run.summary['slots_succeeded'] == 1
    assert run.summary['slots_failed'] == 0
    assert run.summary['slots_planned'] == 1


def test_slot_events_use_bound_indices(monkeypatch, tmp_path):
    """槽位事件采用编排绑定的图片与槽位序号，且不带请求身份。"""
    run = run_chain(
        monkeypatch, tmp_path,
        [{'provider': 'beeimg_cn', 'token': '',
          'options': {'storage_id': 13}}],
        replies=[group_reply(), SUCCESS_UPLOAD], slot_index=9, slots_planned=1)
    slot = slots(run.events)
    assert [event.status for event in slot] == ['started', 'succeeded']
    assert all((event.image_index, event.slot_index) == (1, 9)
               for event in slot)
    assert all(event.request_id is None and event.attempt_index is None
               for event in slot)


def test_host_failover_counts_each_real_post(monkeypatch, tmp_path):
    """跨槽位故障转移各记一次真实 POST 与一次回退。"""
    hosts = [
        {'provider': 'catbox', 'token': ''},
        {'provider': 'beeimg', 'token': ''},
        {'provider': 'wmimg', 'token': ''},
    ]
    run = run_chain(
        monkeypatch, tmp_path, hosts,
        replies=[(500, b''), (500, b''),
                 (200, body({'status': True,
                             'data': {'links': {'url': URL}}}))])
    assert len(run.transport.posts) == 3
    assert run.summary['upload_post_attempts'] == 3
    assert run.summary['host_failovers'] == 2
    assert run.summary['slots_succeeded'] == 1
    assert run.summary['slots_failed'] == 2
    assert run.results[0].success and run.results[0].attempts == (
        'catbox', 'beeimg', 'wmimg')


PROVIDER_POSTS = (
    ('catbox', {'provider': 'catbox', 'token': ''},
     [(200, URL.encode())], 1),
    ('wmimg', {'provider': 'wmimg', 'token': ''},
     [(200, body({'status': True, 'data': {'links': {'url': URL}}}))], 1),
    ('beeimg', {'provider': 'beeimg', 'token': ''},
     [(200, body({'files': {'status': 'Success', 'url': URL}}))], 1),
    ('superbed', {'provider': 'superbed', 'token': SECRET},
     [(200, body({'success': True, 'total': 1, 'succeed': 1, 'failed': 0,
                  'files': [{'url': URL}]}))], 1),
    ('beeimg_cn', {'provider': 'beeimg_cn', 'token': '',
                   'options': {'storage_id': 13}},
     [group_reply(), SUCCESS_UPLOAD], 1),
    ('boltp', {'provider': 'boltp', 'token': '',
               'options': {'storage_id': 2}}, [SUCCESS_UPLOAD], 1),
)


@pytest.mark.parametrize(('name', 'host', 'replies', 'posts'), PROVIDER_POSTS)
def test_each_provider_counts_only_real_posts(
        name, host, replies, posts, monkeypatch, tmp_path):
    """各 provider 的上传计数等于传输替身实际收到的 POST 次数。"""
    run = run_chain(monkeypatch, tmp_path, [host], replies=replies)
    assert run.results[0].success
    assert len(run.transport.posts) == posts
    assert run.summary['upload_post_attempts'] == len(run.transport.posts)
    assert run.summary['group_get_attempts'] == len(
        run.transport.gets('group'))
    assert run.summary['profile_get_attempts'] == len(
        run.transport.gets('profile'))
    assert run.summary['slots_succeeded'] == 1


def test_superbed_domain_fallback_counts_two_posts(monkeypatch, tmp_path):
    """Superbed 域名回退的两次真实 POST 分别计数并落在同一槽位。"""
    run = run_chain(
        monkeypatch, tmp_path,
        [{'provider': 'superbed', 'token': SECRET}],
        replies=[requests.exceptions.ConnectionError(),
                 (200, body({'success': True, 'total': 1, 'succeed': 1,
                             'failed': 0, 'files': [{'url': URL}]}))])
    assert run.results[0].success
    assert len(run.transport.posts) == 2
    assert run.summary['upload_post_attempts'] == 2
    assert run.summary['slots_succeeded'] == 1
    assert run.summary['slots_failed'] == 0
    assert [event.status for event in slots(run.events)] == [
        'started', 'succeeded']


def test_disk_cache_hit_reuses_without_query(monkeypatch, tmp_path):
    """磁盘命中不产生任何查询，且日志带命中类别与空请求来源。"""
    context, cache, transport = build(monkeypatch, tmp_path, replies=[])
    identity = cache.identity('beeimg_cn', SECRET)
    assert cache.save(identity, StorageMetadata((13, 14), 14, 3600, 1000.0))
    events = []
    with trace_scope(mode='cli', on_status=events.append) as trace:
        with trace_item(image_index=1):
            metadata = context.get_metadata('beeimg_cn', SECRET)
        summary = trace.summary()
    context.close()
    assert metadata.storage_ids == (13, 14)
    assert transport.sent == []
    assert (summary['group_get_attempts'],
            summary['profile_get_attempts']) == (0, 0)
    hits = [event for event in of_stage(events, 'metadata_cache')
            if event.source == 'hit']
    assert len(hits) == 1
    assert hits[0].cache_layer == 'disk' and hits[0].request_id is None


@pytest.mark.parametrize(('fetched_at', 'now', 'source'), [
    (1000.0, 1000.0 + 86400, 'expired'),
    (2000.0, 1000.0, 'future'),
])
def test_expired_and_future_cache_are_distinct_misses(
        fetched_at, now, source, monkeypatch, tmp_path):
    """过期与未来时间的缓存分别记录为不同未命中类别并重新查询。"""
    context, cache, transport = build(
        monkeypatch, tmp_path, replies=[group_reply(ids=(13,))],
        clock=[now])
    identity = cache.identity('beeimg_cn', '')
    assert cache.save(identity, StorageMetadata(
        (13,), None, None, fetched_at))
    events = []
    with trace_scope(mode='cli', on_status=events.append) as trace:
        with trace_item(image_index=1):
            context.get_metadata('beeimg_cn', '')
        summary = trace.summary()
    context.close()
    recorded = [event for event in of_stage(events, 'metadata_cache')
                if event.source == source]
    assert len(recorded) == 1
    assert summary['group_get_attempts'] == 1
    assert len(transport.gets('group')) == 1


def test_disk_cache_miss_queries_once(monkeypatch, tmp_path):
    """空缓存记为未命中并只执行一次真实查询。"""
    transport, events, summary, metadata, errors = run_metadata(
        monkeypatch, tmp_path, replies=[group_reply(ids=(13,))])
    assert not errors
    assert metadata.storage_ids == (13,)
    miss = [event for event in of_stage(events, 'metadata_cache')
            if event.source == 'miss']
    assert len(miss) == 1
    assert (summary['group_get_attempts'],
            summary['profile_get_attempts']) == (1, 0)
    assert len(transport.sent) == 1


def test_disabled_identity_is_skipped(monkeypatch, tmp_path):
    """进程内禁用身份不再读取磁盘但仍执行一次真实查询。"""
    storage_cache = import_module('modules.image_host.storage_cache')
    context, cache, transport = build(
        monkeypatch, tmp_path, replies=[group_reply(ids=(13,))])
    identity = cache.identity('beeimg_cn', '')
    assert cache.save(identity, StorageMetadata((13,), None, None, 1000.0))
    storage_cache._DISABLED_IDENTITIES.add((str(cache._root), identity))
    events = []
    with trace_scope(mode='cli', on_status=events.append) as trace:
        with trace_item(image_index=1):
            context.get_metadata('beeimg_cn', '')
        summary = trace.summary()
    context.close()
    disabled = [event for event in of_stage(events, 'metadata_cache')
                if event.reason_code == 'disabled']
    assert len(disabled) == 1
    assert disabled[0].status == 'skipped'
    assert disabled[0].duration_ms is None
    assert summary['group_get_attempts'] == 1


def test_invalid_cache_record_is_recorded(monkeypatch, tmp_path):
    """畸形磁盘记录记为无效并继续真实查询。"""
    context, cache, transport = build(
        monkeypatch, tmp_path, replies=[group_reply(ids=(13,))])
    identity = cache.identity('beeimg_cn', '')
    (cache._root / (identity + '.json')).write_bytes(b'{not-json')
    events = []
    with trace_scope(mode='cli', on_status=events.append) as trace:
        with trace_item(image_index=1):
            context.get_metadata('beeimg_cn', '')
        summary = trace.summary()
    context.close()
    invalid = [event for event in of_stage(events, 'metadata_cache')
               if event.status == 'failed']
    assert invalid and invalid[0].reason_code == 'invalid'
    assert summary['group_get_attempts'] == 1


def test_save_refuses_forbidden_partial_result(monkeypatch, tmp_path):
    """forbidden 部分结果拒绝落盘并记录固定跳过。"""
    context, cache, transport = build(
        monkeypatch, tmp_path,
        replies=[group_reply(ids=(13,)), profile_reply(status=403)])
    events = []
    with trace_scope(mode='cli', on_status=events.append) as trace:
        with trace_item(image_index=1):
            context.get_metadata('beeimg_cn', SECRET)
        trace.summary()
    context.close()
    assert (transport.gets('profile'))
    refused = [event for event in of_stage(events, 'metadata_cache')
               if event.reason_code == 'forbidden']
    assert len(refused) == 1
    assert refused[0].status == 'skipped'
    identity = cache.identity('beeimg_cn', SECRET)
    assert not (cache._root / (identity + '.json')).exists()


def test_invalidate_records_removal_and_failure(monkeypatch, tmp_path):
    """失效删除成功与删除失败分别记录固定结果。"""
    cache = StorageCache(tmp_path / 'cache', clock=lambda: 1000.0)
    identity = cache.identity('beeimg_cn', '')
    assert cache.save(identity, StorageMetadata((13,), None, None, 1000.0))
    events = []
    with trace_scope(mode='cli', on_status=events.append):
        assert cache.invalidate(identity) is True
    removed = of_stage(events, 'metadata_cache')
    assert [event.status for event in removed] == ['started', 'succeeded']

    assert cache.save(identity, StorageMetadata((13,), None, None, 1000.0))
    events.clear()

    def fail_unlink(self, *args, **kwargs):
        """模拟删除磁盘记录失败。"""
        raise OSError('delete-failure-marker')

    monkeypatch.setattr(type(cache._root), 'unlink', fail_unlink)
    with trace_scope(mode='cli', on_status=events.append):
        assert cache.invalidate(identity) is False
    failed = of_stage(events, 'metadata_cache')
    assert failed[1].status == 'failed'
    assert failed[1].reason_code == 'delete_failed'


def test_shared_query_source_is_traceable(monkeypatch, tmp_path):
    """同凭证第二次读取复用来源操作标识而不冒充新的网络查询。"""
    context, cache, transport = build(
        monkeypatch, tmp_path, replies=[group_reply(ids=(13,))])
    events = []
    with trace_scope(mode='cli', on_status=events.append) as trace:
        with trace_item(image_index=1):
            context.get_metadata('beeimg_cn', '')
        with trace_item(image_index=2):
            context.get_metadata('beeimg_cn', '')
        summary = trace.summary()
    context.close()
    query = of_stage(events, 'metadata_query')[0]
    reuse = [event for event in of_stage(events, 'metadata_cache')
             if event.reason_code == 'reused_metadata']
    assert reuse
    assert reuse[0].source_operation_id == query.operation_id
    assert reuse[0].cache_layer == 'event'
    assert summary['group_get_attempts'] == 1
    assert len(transport.sent) == 1


def test_upload_attempt_started_is_info(monkeypatch, tmp_path, caplog):
    """upload_attempt 开始为 INFO，而查询与缓存细节仍为 DEBUG。"""
    with caplog.at_level(logging.DEBUG, logger='modules.operation_trace'):
        run_chain(
            monkeypatch, tmp_path,
            [{'provider': 'beeimg_cn', 'token': '',
              'options': {'storage_id': 13}}],
            replies=[group_reply(), SUCCESS_UPLOAD])
    levels = {}
    for record in caplog.records:
        message = record.getMessage()
        if 'stage=upload_attempt' in message and 'status=started' in message:
            levels['upload'] = record.levelno
        if 'stage=metadata_query' in message and 'status=started' in message:
            levels['query'] = record.levelno
    assert levels['upload'] == logging.INFO
    assert levels['query'] == logging.DEBUG


TRANSPORT_SECONDS = 1.0
BUSINESS_SECONDS = 9.0
TRANSPORT_MS = 1000
INVALID_URL = 'not-a-valid-image-url'
REQUEST_SECONDS = 0.125
BODY_SECONDS = 0.25
PARSE_SECONDS = 5.0
CLOSE_SECONDS = 0.5
REQUEST_BODY_MS = 375
REQUEST_MS = 125
SLOW_SESSION_SECONDS = 5.0
SLOW_AUTH_SECONDS = 5.0
SLOW_REQUEST_SECONDS = 1.0
SLOW_BODY_SECONDS = 2.0
SLOW_REQUEST_BODY_MS = 3000
AUTH_REQUEST_SECONDS = 0.4
AUTH_BODY_SECONDS = 0.6
AUTH_REQUEST_BODY_MS = 1000

# 四个非 v2 适配器的成功正文与业务校验失败正文；superbed 失败会在两个
# 域名间各发一次真实请求，因此失败队列需要两条合成响应。
FREEZE_CASES = (
    ('catbox', {'provider': 'catbox', 'token': ''},
     (200, URL.encode()),
     [(200, INVALID_URL.encode())]),
    ('wmimg', {'provider': 'wmimg', 'token': ''},
     (200, body({'status': True, 'data': {'links': {'url': URL}}})),
     [(200, body({'status': True, 'data': {'links': {'url': INVALID_URL}}}))]),
    ('beeimg', {'provider': 'beeimg', 'token': ''},
     (200, body({'files': {'status': 'Success', 'url': URL}})),
     [(200, body({'files': {'status': 'Success', 'url': INVALID_URL}}))]),
    ('superbed', {'provider': 'superbed', 'token': SECRET},
     (200, body({'success': True, 'total': 1, 'succeed': 1,
                 'failed': 0, 'files': [{'url': URL}]})),
     [(200, body({'success': True, 'total': 1, 'succeed': 1,
                  'failed': 0, 'files': [{'url': INVALID_URL}]})),
      (200, body({'success': True, 'total': 1, 'succeed': 1,
                  'failed': 0, 'files': [{'url': INVALID_URL}]}))]),
)


def run_single_adapter(monkeypatch, tmp_path, host, replies, clock):
    """在可控时钟的批次作用域内经真实注册表执行一次上传。"""
    registry = import_module('modules.image_host.registry')
    cache = StorageCache(tmp_path / 'cache', clock=lambda: clock[0])
    context = UploadContext(cache=cache, clock=lambda: clock[0])
    transport = Transport(monkeypatch, replies)
    events = []
    with trace_scope(mode='cli', images_planned=1, slots_planned=1,
                     clock=lambda: clock[0], on_status=events.append):
        result = registry.upload_with_fallback(
            b'image', 'image.png', [host], context=context)
    context.close()
    return transport, events, result


@pytest.mark.parametrize(
    ('name', 'host', 'success_reply', 'failure_replies'), FREEZE_CASES)
@pytest.mark.parametrize('outcome', ['success', 'failure'])
def test_transport_end_freezes_request_duration(
        name, host, success_reply, failure_replies, outcome,
        monkeypatch, tmp_path):
    """上传耗时在读完响应正文时冻结，业务解析与校验的时间不计入。"""
    module = import_module('modules.image_host.providers')
    clock = [1000.0]
    validate = module.validate_image_url

    def slow_validate(value):
        """模拟业务链接校验耗时后再执行真实校验。"""
        clock[0] += BUSINESS_SECONDS
        return validate(value)

    monkeypatch.setattr(module, 'validate_image_url', slow_validate)
    original_request = requests.sessions.Session.request

    def timed_request(session, method, url, **kwargs):
        """模拟一次传输耗时后执行真实请求。"""
        clock[0] += TRANSPORT_SECONDS
        return original_request(session, method, url, **kwargs)

    monkeypatch.setattr(
        requests.sessions.Session, 'request', timed_request)
    replies = (
        [success_reply] if outcome == 'success' else list(failure_replies))
    transport, events, result = run_single_adapter(
        monkeypatch, tmp_path, host, replies, clock)
    assert result.success is (outcome == 'success')
    attempts = [event for event in of_stage(events, 'upload_attempt')
                if event.status != 'started']
    assert attempts
    assert all(event.duration_ms == TRANSPORT_MS for event in attempts)
    assert len(transport.posts) == len(attempts)


class TimedResponse(requests.Response):
    """在请求、正文和解析边界推进可控时钟的合成响应。"""

    def __init__(self, clock, status, content, read_error=None, headers=None):
        """保存合成正文与可选读取错误。"""
        super().__init__()
        self.clock = clock
        self.status_code = status
        self._content = content
        self._content_consumed = True
        self.read_error = read_error
        self.headers.update(headers or {})
        self.encoding = requests.utils.get_encoding_from_headers(self.headers)

    @property
    def content(self):
        """模拟完整正文读取耗时并传播指定错误。"""
        self.clock[0] += BODY_SECONDS
        if self.read_error is not None:
            raise self.read_error
        return self._content

    def iter_content(self, chunk_size=1, decode_unicode=False):
        """模拟 v2 有界正文读取耗时并传播指定错误。"""
        self.clock[0] += BODY_SECONDS
        if self.read_error is not None:
            raise self.read_error
        yield self._content

    def close(self):
        """模拟响应资源释放耗时。"""
        self.clock[0] += CLOSE_SECONDS
        super().close()


def install_timed_transport(monkeypatch, clock, replies):
    """安装按队列返回计时响应的离线传输替身。"""
    sent = []
    queue = list(replies)

    def send(adapter, request, **kwargs):
        """模拟请求传输耗时并返回下一个合成响应。"""
        sent.append(request)
        clock[0] += REQUEST_SECONDS
        reply = queue.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        status, content, *extras = reply
        error = extras[0] if extras else None
        headers = extras[1] if len(extras) > 1 else None
        response = TimedResponse(
            clock, status, content, error, headers)
        response.request = request
        response.url = request.url
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)

    original_close = requests.sessions.Session.close

    def close(session):
        """模拟会话资源释放耗时。"""
        clock[0] += CLOSE_SECONDS
        original_close(session)

    monkeypatch.setattr(requests.sessions.Session, 'close', close)
    return sent


def terminal_requests(events):
    """返回真实请求的终态事件并保持发生顺序。"""
    return [event for event in events
            if event.stage in ('metadata_query', 'upload_attempt')
            and event.status != 'started']


def run_timed_adapter(monkeypatch, tmp_path, host, clock):
    """使用已安装的计时传输执行单个真实适配器。"""
    registry = import_module('modules.image_host.registry')
    cache = StorageCache(tmp_path / 'cache', clock=lambda: clock[0])
    context = UploadContext(cache=cache, clock=lambda: clock[0])
    events = []
    with trace_scope(mode='cli', images_planned=1, slots_planned=1,
                     clock=lambda: clock[0], on_status=events.append):
        result = registry.upload_with_fallback(
            b'image', 'image.png', [host], context=context)
    context.close()
    return events, result


def test_non_v2_duration_excludes_json_parse(monkeypatch, tmp_path):
    """非 v2 请求耗时只含请求与正文读取，不含五秒 JSON 解析。"""
    clock = [0.0]
    sent = install_timed_transport(monkeypatch, clock, [(
        200, body({'status': True, 'data': {'links': {'url': URL}}}))])
    original_loads = json.loads

    def slow_loads(value, *args, **kwargs):
        """模拟远慢于网络的 JSON 解析后执行真实解析。"""
        clock[0] += PARSE_SECONDS
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(requests.models.complexjson, 'loads', slow_loads)
    events, result = run_timed_adapter(
        monkeypatch, tmp_path, {'provider': 'wmimg', 'token': ''}, clock)
    assert result.success
    assert len(sent) == 1
    assert [event.duration_ms for event in terminal_requests(events)] == [
        REQUEST_BODY_MS]


def test_v2_group_profile_upload_duration_excludes_json_parse(
        monkeypatch, tmp_path):
    """v2 组、资料和上传请求均在正文读取后冻结为三十毫秒。"""
    module = import_module('modules.image_host.v2_http')
    clock = [0.0]
    replies = [group_reply(), profile_reply(), SUCCESS_UPLOAD]
    sent = install_timed_transport(
        monkeypatch, clock, [(status, content) for status, content in replies])
    original_loads = json.loads

    def slow_loads(value, *args, **kwargs):
        """模拟五秒 JSON 解析并保留真实解码行为。"""
        clock[0] += PARSE_SECONDS
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(module.json, 'loads', slow_loads)
    events, result = run_timed_adapter(
        monkeypatch, tmp_path,
        {'provider': 'beeimg_cn', 'token': SECRET}, clock)
    assert result.success
    assert len(sent) == 3
    assert [event.duration_ms for event in terminal_requests(events)] == [
        REQUEST_BODY_MS, REQUEST_BODY_MS, REQUEST_BODY_MS]


@pytest.mark.parametrize('provider', ['wmimg', 'beeimg_cn'])
def test_request_duration_excludes_slow_session_construction(
        provider, monkeypatch, tmp_path):
    """五秒会话准备不计时，请求与正文三秒构成完整请求耗时。"""
    clock = [0.0]
    original_init = requests.sessions.Session.__init__

    def slow_init(session):
        """模拟本地会话构造耗时后执行真实初始化。"""
        clock[0] += SLOW_SESSION_SECONDS
        original_init(session)

    monkeypatch.setattr(requests.sessions.Session, '__init__', slow_init)
    monkeypatch.setattr(
        import_module(__name__), 'REQUEST_SECONDS', SLOW_REQUEST_SECONDS)
    monkeypatch.setattr(
        import_module(__name__), 'BODY_SECONDS', SLOW_BODY_SECONDS)
    replies = ([SUCCESS_UPLOAD] if provider == 'beeimg_cn' else [(
        200, body({'status': True, 'data': {'links': {'url': URL}}}))])
    sent = install_timed_transport(monkeypatch, clock, replies)
    host = ({'provider': 'wmimg', 'token': ''} if provider == 'wmimg' else
            {'provider': 'beeimg_cn', 'token': '',
             'options': {'storage_id': 13}})
    if provider == 'wmimg':
        events, result = run_timed_adapter(monkeypatch, tmp_path, host, clock)
    else:
        module = import_module('modules.image_host.v2_http')
        events = []
        with trace_scope(mode='cli', clock=lambda: clock[0],
                         on_status=events.append):
            payload = module.request_json(
                'beeimg_cn', 'upload', '', data={}, files={})
        result = type('Result', (), {'success': payload['status'] == 'success'})()
    assert result.success
    assert len(sent) == 1
    assert [event.duration_ms for event in terminal_requests(events)] == [
        SLOW_REQUEST_BODY_MS]


@pytest.mark.parametrize('stage', ['group', 'profile', 'upload'])
@pytest.mark.parametrize('signal_type', [None, KeyboardInterrupt, SystemExit])
def test_v2_request_timing_excludes_slow_auth_preparation(
        stage, signal_type, monkeypatch):
    """认证准备五秒不进入请求起点，传输与正文一秒构成完整耗时。"""
    module = import_module('modules.image_host.v2_http')
    clock = [0.0]
    original_auth = module._ExplicitAuth.__call__

    def slow_auth(auth, request):
        """模拟本地认证回调耗时后保留真实认证语义。"""
        clock[0] += SLOW_AUTH_SECONDS
        return original_auth(auth, request)

    monkeypatch.setattr(module._ExplicitAuth, '__call__', slow_auth)
    monkeypatch.setattr(
        import_module(__name__), 'REQUEST_SECONDS', AUTH_REQUEST_SECONDS)
    monkeypatch.setattr(
        import_module(__name__), 'BODY_SECONDS', AUTH_BODY_SECONDS)
    signal = None if signal_type is None else signal_type('stop-request')
    reply = {'group': group_reply(), 'profile': profile_reply(),
             'upload': SUCCESS_UPLOAD}[stage]
    sent = install_timed_transport(
        monkeypatch, clock, [(*reply, signal)])
    events = []

    def record(event):
        """记录事件及其发出时刻以核对请求起点。"""
        events.append((event, clock[0]))

    kwargs = {'data': {}, 'files': {}} if stage == 'upload' else {}
    if signal is None:
        with trace_scope(mode='cli', clock=lambda: clock[0], on_status=record):
            result = module.request_json('beeimg_cn', stage, SECRET, **kwargs)
        assert result['status'] == 'success'
    else:
        with pytest.raises(signal_type) as caught:
            with trace_scope(
                    mode='cli', clock=lambda: clock[0], on_status=record):
                module.request_json('beeimg_cn', stage, SECRET, **kwargs)
        assert caught.value is signal
    request_events = [item for item in events if item[0].stage in (
        'metadata_query', 'upload_attempt')]
    assert len(sent) == 1
    assert request_events[0][0].status == 'started'
    assert request_events[0][1] == SLOW_AUTH_SECONDS
    assert request_events[-1][0].duration_ms == AUTH_REQUEST_BODY_MS
    assert request_events[-1][0].status == (
        'succeeded' if signal is None else 'cancelled')


@pytest.mark.parametrize('provider', ['wmimg', 'beeimg_cn'])
def test_json_parse_failure_keeps_request_body_duration(
        provider, monkeypatch, tmp_path):
    """JSON 解析失败仍保持请求与正文读取边界。"""
    module_name = ('modules.image_host.providers' if provider == 'wmimg'
                   else 'modules.image_host.v2_http')
    module = import_module(module_name)
    clock = [0.0]
    sent = install_timed_transport(monkeypatch, clock, [(200, b'{invalid')])
    original_loads = json.loads

    def slow_loads(value, *args, **kwargs):
        """先推进五秒再触发真实解析错误。"""
        clock[0] += PARSE_SECONDS
        return original_loads(value, *args, **kwargs)

    if provider == 'wmimg':
        monkeypatch.setattr(requests.models.complexjson, 'loads', slow_loads)
    else:
        monkeypatch.setattr(module.json, 'loads', slow_loads)
    host = ({'provider': 'wmimg', 'token': ''} if provider == 'wmimg' else
            {'provider': 'beeimg_cn', 'token': '',
             'options': {'storage_id': 13}})
    events, result = run_timed_adapter(monkeypatch, tmp_path, host, clock)
    assert not result.success
    assert len(sent) == 1
    assert [event.duration_ms for event in terminal_requests(events)] == [
        REQUEST_BODY_MS]


@pytest.mark.parametrize('provider', ['catbox', 'beeimg_cn'])
def test_body_read_failure_freezes_duration_and_propagates_control_signal(
        provider, monkeypatch, tmp_path):
    """正文读取控制信号原样传播，终态耗时在读取边界冻结。"""
    clock = [0.0]
    signal = KeyboardInterrupt('stop-reading')
    sent = install_timed_transport(
        monkeypatch, clock, [(200, b'', signal)])
    host = ({'provider': 'catbox', 'token': ''} if provider == 'catbox' else
            {'provider': 'beeimg_cn', 'token': '',
             'options': {'storage_id': 13}})
    events = []
    registry = import_module('modules.image_host.registry')
    cache = StorageCache(tmp_path / 'cache', clock=lambda: clock[0])
    context = UploadContext(cache=cache, clock=lambda: clock[0])
    with pytest.raises(KeyboardInterrupt) as caught:
        with trace_scope(mode='cli', images_planned=1, slots_planned=1,
                         clock=lambda: clock[0], on_status=events.append):
            registry.upload_with_fallback(
                b'image', 'image.png', [host], context=context)
    context.close()
    assert caught.value is signal
    assert len(sent) == 1
    assert [event.duration_ms for event in terminal_requests(events)] == [
        REQUEST_BODY_MS]


@pytest.mark.parametrize('provider', ['wmimg', 'beeimg_cn'])
@pytest.mark.parametrize('outcome', ['http', 'request_error'])
def test_no_body_duration_excludes_resource_close(
        provider, outcome, monkeypatch, tmp_path):
    """不读正文路径在响应和会话关闭前冻结请求耗时。"""
    clock = [0.0]
    reply = ((503, b'') if outcome == 'http'
             else requests.exceptions.ConnectionError('offline'))
    sent = install_timed_transport(monkeypatch, clock, [reply])
    host = ({'provider': 'wmimg', 'token': ''} if provider == 'wmimg' else
            {'provider': 'beeimg_cn', 'token': '',
             'options': {'storage_id': 13}})
    events, result = run_timed_adapter(monkeypatch, tmp_path, host, clock)
    assert not result.success
    assert len(sent) == 1
    assert [event.duration_ms for event in terminal_requests(events)] == [
        REQUEST_MS]


@pytest.mark.parametrize('encoding', ['utf-16', 'utf-32'])
def test_non_v2_json_preserves_requests_unicode_detection(
        encoding, monkeypatch, tmp_path):
    """非 v2 JSON 对无 charset 的 UTF-16/32 正文保持 Requests 语义。"""
    clock = [0.0]
    content = json.dumps(
        {'status': True, 'data': {'links': {'url': URL}}}).encode(encoding)
    sent = install_timed_transport(monkeypatch, clock, [(200, content)])
    events, result = run_timed_adapter(
        monkeypatch, tmp_path, {'provider': 'wmimg', 'token': ''}, clock)
    assert result.success
    assert result.url == URL
    assert len(sent) == 1
    assert [event.duration_ms for event in terminal_requests(events)] == [
        REQUEST_BODY_MS]


def test_non_v2_json_unknown_charset_uses_requests_text_fallback(
        monkeypatch, tmp_path):
    """未知 charset 经 Requests text 回退解析且不改变 URL 与请求数。"""
    clock = [0.0]
    content = body({'status': True, 'data': {'links': {'url': URL}}})
    headers = {'Content-Type': 'application/json; charset=unknown-synthetic'}
    sent = install_timed_transport(
        monkeypatch, clock, [(200, content, None, headers)])
    events, result = run_timed_adapter(
        monkeypatch, tmp_path, {'provider': 'wmimg', 'token': ''}, clock)
    assert result.success
    assert result.url == URL
    assert len(sent) == 1
    assert [event.duration_ms for event in terminal_requests(events)] == [
        REQUEST_BODY_MS]


@pytest.mark.parametrize('stage', ['group', 'profile', 'upload'])
def test_v2_required_structure_precedes_request_terminal(stage, monkeypatch):
    module = import_module('modules.image_host.v2_http')
    clock = [0.0]
    sent = install_timed_transport(
        monkeypatch, clock, [(200, body({'status': 'success', 'data': {}}))])
    events = []
    with trace_scope(mode='cli', clock=lambda: clock[0],
                     on_status=events.append) as trace:
        module.request_json('beeimg_cn', stage, '')
        summary = trace.summary()
    terminal = terminal_requests(events)
    assert len(sent) == len(terminal) == 1
    assert terminal[0].status == 'failed'
    assert terminal[0].duration_ms == REQUEST_BODY_MS
    prefix = 'upload_post' if stage == 'upload' else stage + '_get'
    assert summary[prefix + '_failed'] == 1


def test_non_v2_invalid_json_raises_requests_decode_error():
    """已读取的无效 JSON 仍包装为 Requests JSONDecodeError。"""
    module = import_module('modules.image_host.providers')
    response = requests.Response()
    response._content = b'{invalid'
    response._content_consumed = True

    class Attempt:
        """记录正文读取边界。"""

        ended = False

        def mark_end(self):
            """登记正文读取结束。"""
            self.ended = True

    attempt = Attempt()
    with pytest.raises(requests.exceptions.JSONDecodeError):
        module._read_response_json(response, attempt)
    assert attempt.ended
