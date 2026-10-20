#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""在真实 CLI 与通知入口验证 BeeIMG.CN 手填优先的隔离、取消与脱敏。"""

from copy import deepcopy
from email.parser import BytesParser
from email.policy import default as email_policy
from importlib import import_module
import json
import logging
import netrc
from pathlib import Path
import socket
import sys

from PIL import Image
import pytest
import requests

import modules.image_host.context as context_module
from modules import notification as notification_module
from modules.image_host.context import UploadContext
from modules.image_host.core import ImageHostError
from modules.image_host.storage_cache import StorageCache
from modules.operation_trace import trace_item
from modules.operation_trace import trace_scope
from modules.screenshot import cli
from modules.screenshot import pipeline
from modules.screenshot.models import CaptureResult
from modules.upload_status import host_providers
from modules.upload_status import make_status_consumer
from modules.upload_status import upload_feedback_scope


# 全部为测试内部合成常量，不含真实凭证或真实服务端内容。
URL = 'https://cdn.example.invalid/acceptance.jpg'
SYNTHETIC_TOKEN = 'SYNTHETIC_ACCEPTANCE_TOKEN_4471'
LEAK_MARKER = 'SYNTHETIC_LEAK_BODY_8823'
CONTAINER_MARKER = 'SYNTHETIC_CONTAINER_9931'
REJECTION = '不存在的储存驱动'
SYNTHETIC_WINDOW = 'SyntheticWindow'
TEMPLATE_KEY = 'on_end'
BEEIMG_BASE = 'https://www.beeimg.cn/api/v2'
BOLTP_BASE = 'https://www.boltp.com/api/v2'
MANUAL_STORAGE_ID = 13
RECOVERED_STORAGE_ID = 14
TRACE_LOGGER = 'modules.operation_trace'


def _png_bytes():
    """构造一张最小合成 PNG，仅供内存上传使用。"""
    import io

    buffer = io.BytesIO()
    Image.new('RGB', (2, 2), (1, 2, 3)).save(buffer, format='PNG')
    return buffer.getvalue()


def _body(payload):
    """把合成响应编码为 JSON 字节，不含真实服务端内容。"""
    return json.dumps(payload).encode('utf-8')


def _success_upload():
    """构造一次成功上传响应，携带经校验的合成直链。"""
    return (200, _body({'status': 'success', 'data': {'public_url': URL}}))


def _rejected_upload():
    """构造一次精确存储拒绝响应。"""
    return (200, _body({'status': 'error', 'message': REJECTION}))


def _group_reply(ids=(MANUAL_STORAGE_ID, RECOVERED_STORAGE_ID), expire=None):
    """构造一次成功的组查询响应，可选固定期限。"""
    payload = {'status': 'success', 'data': {
        'group': {'options': {'file_expire_seconds': expire}},
        'storages': [{'id': value} for value in ids]}}
    return (200, _body(payload))


def _ok_notify_response():
    """构造仅含成败判定所需字段的推送响应，不访问真实推送服务。"""
    response = requests.Response()
    response.status_code = 200
    response.encoding = 'utf-8'
    response._content = b'{"code":0}'
    response._content_consumed = True
    response.url = 'https://push.example.invalid/synthetic'
    return response


def _route_key(request):
    """按站点与阶段归一真实请求的离线路由键。"""
    if request.url.startswith(BEEIMG_BASE):
        provider, base = 'beeimg_cn', BEEIMG_BASE
    elif request.url.startswith(BOLTP_BASE):
        provider, base = 'boltp', BOLTP_BASE
    else:
        return (None, request.method)
    if request.method == 'POST':
        return (provider, 'upload')
    return (provider, request.url[len(base):])


class _EntryTransport:
    """替换 HTTPAdapter.send，按 (站点, 阶段) 路由合成响应并记录请求。"""

    def __init__(self, monkeypatch, queues):
        """保存有序响应队列并登记真实发送边界。"""
        self.sent = []
        self.queues = {key: list(value) for key, value in queues.items()}
        self.unexpected = []

        def send(adapter, request, **kwargs):
            """记录一次真实请求并按路由返回预设响应。"""
            self.sent.append(request)
            key = _route_key(request)
            queue = self.queues.get(key)
            if not queue:
                self.unexpected.append(key)
                return _response(503, b'')
            reply = queue.pop(0)
            if isinstance(reply, BaseException):
                raise reply
            status, content = reply
            return _response(status, content, request)

        monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)

    def requests_for(self, provider, stage):
        """返回指定站点与阶段的真实请求，保持发生顺序。"""
        return [item for item in self.sent if _route_key(item) == (provider, stage)]

    def posts(self):
        """返回全部真实 POST 请求。"""
        return [item for item in self.sent if item.method == 'POST']

    def drained(self):
        """返回所有仍未被消费的队列项，用于核对请求恰好用尽队列。"""
        return {key: tuple(value) for key, value in self.queues.items() if value}


def _response(status, content, request=None):
    """构造离线合成响应对象。"""
    response = requests.Response()
    response.request = request
    response.url = request.url if request is not None else 'https://offline.invalid'
    response.status_code = status
    response._content = content
    response._content_consumed = True
    return response


def _storage_ids(transport, provider):
    """从 multipart 请求体读取真实发送的存储编号，保持发送顺序。"""
    values = []
    for request in transport.requests_for(provider, 'upload'):
        content_type = request.headers.get('Content-Type', '')
        message = BytesParser(policy=email_policy).parsebytes(
            f'Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n'.encode()
            + request.body)
        part = next(
            (item for item in message.iter_parts()
             if item.get_param('name', header='content-disposition')
             == 'storage_id'), None)
        values.append(None if part is None else int(part.get_payload(decode=True)))
    return values


def _stage_events(caplog):
    """返回本轮捕获的全部安全阶段事件。"""
    events = []
    for record in caplog.records:
        event = getattr(record, 'stage_event', None)
        if event is not None:
            events.append(event)
    return events


def _batch_summary(caplog):
    """返回本轮唯一批次终态的计数摘要。"""
    for record in caplog.records:
        summary = getattr(record, 'stage_summary', None)
        if summary and 'upload_post_attempts' in summary:
            return summary
    return None


def _cache_root(tmp_path):
    """返回隔离后的默认存储缓存根。"""
    return tmp_path / 'local' / '2RPM' / 'image_host'


class _RecordingCache(StorageCache):
    """记录 identity/load/save/invalidate 调用名，行为继承真实缓存。"""

    instances = []

    def __init__(self, *args, **kwargs):
        """继承真实缓存初始化并登记本实例，不执行任何外部操作。"""
        super().__init__(*args, **kwargs)
        self.calls = []
        _RecordingCache.instances.append(self)

    def identity(self, *args, **kwargs):
        """记录一次身份读取调用后委托真实实现。"""
        self.calls.append('identity')
        return super().identity(*args, **kwargs)

    def load(self, *args, **kwargs):
        """记录一次磁盘读取调用后委托真实实现。"""
        self.calls.append('load')
        return super().load(*args, **kwargs)

    def save(self, *args, **kwargs):
        """记录一次磁盘保存调用后委托真实实现。"""
        self.calls.append('save')
        return super().save(*args, **kwargs)

    def invalidate(self, *args, **kwargs):
        """记录一次磁盘失效调用后委托真实实现。"""
        self.calls.append('invalidate')
        return super().invalidate(*args, **kwargs)


def _inline_upload_text(expiration='1h'):
    """构造一项手填 13 的 BeeIMG.CN 内联上传参数。"""
    text = (f'provider: beeimg_cn, token: "{SYNTHETIC_TOKEN}", '
            f'options: {{storage_id: {MANUAL_STORAGE_ID}}}')
    if expiration is not None:
        text += f', expiration: {expiration}'
    return text


def _notification_config(image_host, tmp_path):
    """构造含单通道、单目标与手填图床的最小推送配置。"""
    return {
        'push': {
            'screenshot': {
                'targets': [{'provider': 'window', 'target': SYNTHETIC_WINDOW}],
                'image_host': deepcopy(list(image_host)),
            },
            'templates': {TEMPLATE_KEY: {
                'enable': True,
                'capture_screenshot': True,
                'title': '标题 {process_name}',
                'content': '正文 {process_name}\n\n{screenshot}',
            }},
            'push_channel_settings': {
                'channels': [{'provider': 'serverchan', 'sckey': 's1'}]},
            'retry': {'interval': '0s', 'max_count': 1},
        },
        '_runtime': {'program_dir': str(tmp_path), 'config_stem': 'SYNTHETIC_STEM'},
    }


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """隔离缓存根并禁止任何未替身化的真实网络或账号访问。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))

    def forbidden(*args, **kwargs):
        """未替身化网络或账号访问立即令测试失败。"""
        pytest.fail('入口验收测试禁止真实网络或账号访问')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(netrc, 'netrc', forbidden)
    monkeypatch.setattr(requests.sessions, 'get_netrc_auth', lambda url: None)


@pytest.mark.parametrize('entry', ['cli', 'notification'])
def test_cli_and_notification_manual_first(entry, monkeypatch, tmp_path, caplog):
    """手填 13 经 CLI 与通知入口均零查询直传，计数与实际调用一致。"""
    transport = _EntryTransport(monkeypatch, {
        ('beeimg_cn', 'upload'): [_success_upload()],
    })
    image = CaptureResult(_png_bytes(), 'window', SYNTHETIC_WINDOW,
                          2, 2, image_format='png')

    _RecordingCache.instances.clear()
    monkeypatch.setattr(context_module, 'StorageCache', _RecordingCache)

    with caplog.at_level(logging.DEBUG, logger=TRACE_LOGGER):
        if entry == 'cli':
            monkeypatch.setattr(cli, 'capture', lambda *args, **kwargs: image)
            args = cli.parse_screenshot_args([
                '--source', f'window:{SYNTHETIC_WINDOW}',
                '--upload', _inline_upload_text()])
            outcome = cli.run_screenshot_cli(args, str(tmp_path))
            assert outcome == 0
        else:
            monkeypatch.setattr(pipeline, 'capture', lambda *args, **kwargs: image)
            notifier = type('Notifier', (), {})()
            notifier.notify = lambda **fields: _ok_notify_response()
            monkeypatch.setattr(
                notification_module, 'get_notifier', lambda provider: notifier)
            config = _notification_config(
                [{'provider': 'beeimg_cn', 'token': SYNTHETIC_TOKEN,
                  'options': {'storage_id': MANUAL_STORAGE_ID},
                  'expiration': '1h'}], tmp_path)
            outcome = notification_module.send_notification(
                config, TEMPLATE_KEY, process_name='demo.exe')
            assert outcome == [('serverchan', True)]

    events = _stage_events(caplog)
    summary = _batch_summary(caplog)
    assert transport.unexpected == []
    assert transport.drained() == {}
    assert len(transport.posts()) == 1
    assert transport.requests_for('beeimg_cn', '/group') == []
    assert transport.requests_for('beeimg_cn', '/user/profile') == []
    assert _storage_ids(transport, 'beeimg_cn') == [MANUAL_STORAGE_ID]
    assert [event for event in events if event.stage == 'metadata_cache'] == []
    assert not _cache_root(tmp_path).exists()
    assert _RecordingCache.instances
    assert [cache.calls for cache in _RecordingCache.instances] == [[]]
    assert summary['upload_post_attempts'] == 1 == len(transport.posts())
    assert summary['upload_post_succeeded'] == 1
    assert summary['upload_post_failed'] == 0
    assert summary['group_get_attempts'] == 0
    assert summary['profile_get_attempts'] == 0
    assert summary['slots_succeeded'] == 1
    assert summary['slots_failed'] == 0


def _patch_business_failure(monkeypatch, victim):
    """在业务层把指定站点的查询函数打桩为固定失败。"""
    module = import_module(f'modules.image_host.storage_{victim}')

    def fail_metadata(*args, **kwargs):
        """不查询任何端点而直接抛出固定查询失败。"""
        raise ImageHostError('storage_lookup_failed', '', stage='group')

    monkeypatch.setattr(module, 'fetch_metadata', fail_metadata)
    if victim == 'boltp':
        def fail_retention(*args, **kwargs):
            """不查询组端点而直接抛出固定查询失败。"""
            raise ImageHostError('storage_lookup_failed', '', stage='group')

        monkeypatch.setattr(module, 'fetch_retention', fail_retention)


def _run_event(monkeypatch, tmp_path, hosts, queues):
    """在共享事件上经真实注册表执行一次两站上传并返回真实事实。"""
    registry = import_module('modules.image_host.registry')
    cache = StorageCache(tmp_path / 'cache', clock=lambda: 1000.0)
    context = UploadContext(cache=cache, clock=lambda: 1000.0,
                            resolved_tokens={index: SYNTHETIC_TOKEN
                                             for index in range(len(hosts))})
    transport = _EntryTransport(monkeypatch, queues)
    events = []
    with trace_scope(mode='cli', images_planned=1, slots_planned=len(hosts),
                     on_status=events.append) as trace:
        with trace_item(image_index=1):
            result = registry.upload_with_fallback(
                b'image', 'image.png', hosts, context=context)
        summary = trace.summary()
    context.close()
    return transport, events, summary, result


@pytest.mark.parametrize('victim', ['beeimg_cn', 'boltp'])
@pytest.mark.parametrize('layer', ['http', 'business'])
@pytest.mark.parametrize('expiration', [None, '1h'])
def test_cross_provider_event_isolation(victim, layer, expiration,
                                        monkeypatch, tmp_path):
    """同凭证下两站的失败与代次互不污染，幸存站计数等于真实请求。"""
    if victim == 'beeimg_cn':
        hosts = [{'provider': 'beeimg_cn', 'token': SYNTHETIC_TOKEN},
                 {'provider': 'boltp', 'token': SYNTHETIC_TOKEN}]
        if expiration is not None:
            hosts[1]['expiration'] = expiration
        queues = {
            ('boltp', 'upload'): [_success_upload()],
            ('boltp', '/group'): [_group_reply(ids=(2,), expire=60)],
        }
    else:
        hosts = [{'provider': 'boltp', 'token': SYNTHETIC_TOKEN,
                  'expiration': '1h'},
                 {'provider': 'beeimg_cn', 'token': SYNTHETIC_TOKEN,
                  'options': {'storage_id': MANUAL_STORAGE_ID}}]
        if expiration is not None:
            hosts[1]['expiration'] = expiration
        queues = {
            ('beeimg_cn', 'upload'): [_rejected_upload(), _success_upload()],
            ('beeimg_cn', '/group'): [_group_reply()],
        }
    if layer == 'http':
        queues[(victim, '/group')] = [(503, b'')]
    else:
        _patch_business_failure(monkeypatch, victim)

    transport, events, summary, result = _run_event(
        monkeypatch, tmp_path, hosts, queues)

    assert result.success is True
    assert transport.unexpected == []
    assert transport.drained() == {}
    assert len(transport.requests_for('beeimg_cn', '/user/profile')) == 0
    assert len(transport.requests_for('boltp', '/user/profile')) == 0
    assert summary['profile_get_attempts'] == 0
    assert summary['group_get_attempts'] == (
        len(transport.requests_for('beeimg_cn', '/group'))
        + len(transport.requests_for('boltp', '/group')))
    assert summary['upload_post_attempts'] == len(transport.posts())

    if layer == 'http':
        http_failed = [failure for failure in result.failures
                       if failure.code == 'http_failed']
        assert http_failed
        assert http_failed[-1].stage == 'group'
        assert http_failed[-1].http_status == 503

    if victim == 'beeimg_cn':
        assert len(transport.requests_for('beeimg_cn', '/group')) == (
            1 if layer == 'http' else 0)
        assert len(transport.requests_for('boltp', '/group')) == 1
        assert _storage_ids(transport, 'boltp') == [2]
        assert summary['postupload_recoveries'] == 0
    else:
        assert summary['postupload_recoveries'] == 1
        assert summary['group_get_attempts'] == (
            1 + len(transport.requests_for('boltp', '/group')))
        assert _storage_ids(transport, 'beeimg_cn') == [
            MANUAL_STORAGE_ID, RECOVERED_STORAGE_ID]
        assert len(transport.requests_for('boltp', '/group')) == (
            1 if layer == 'http' else 0)


CANCEL_SLOTS = [
    {'provider': 'beeimg_cn', 'token': SYNTHETIC_TOKEN,
     'options': {'storage_id': MANUAL_STORAGE_ID}},
    {'provider': 'catbox'},
]


@pytest.mark.parametrize('entry', ['cli', 'notification'])
@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_cancel_during_recovery_propagates(entry, signal_type, monkeypatch,
                                           tmp_path, caplog):
    """恢复查询抛出的同一控制信号经真实入口原样传播，且后续槽不再尝试。"""
    signal_object = signal_type('synthetic-stop')
    image = CaptureResult(_png_bytes(), 'window', SYNTHETIC_WINDOW,
                          2, 2, image_format='png')
    transport = _EntryTransport(monkeypatch, {
        ('beeimg_cn', 'upload'): [_rejected_upload()],
        ('beeimg_cn', '/group'): [signal_object],
    })

    with caplog.at_level(logging.DEBUG, logger=TRACE_LOGGER):
        with pytest.raises(signal_type) as caught:
            if entry == 'cli':
                monkeypatch.setattr(cli, 'capture',
                                    lambda *args, **kwargs: image)
                args = cli.parse_screenshot_args([
                    '--source', f'window:{SYNTHETIC_WINDOW}',
                    '--upload', _inline_upload_text() + '; provider: catbox'])
                cli.run_screenshot_cli(args, str(tmp_path))
            else:
                monkeypatch.setattr(pipeline, 'capture',
                                    lambda *args, **kwargs: image)
                notifier = type('Notifier', (), {})()
                notifier.notify = lambda **fields: _ok_notify_response()
                monkeypatch.setattr(
                    notification_module, 'get_notifier',
                    lambda provider: notifier)
                notification_module.send_notification(
                    _notification_config(CANCEL_SLOTS, tmp_path),
                    TEMPLATE_KEY, process_name='demo.exe')

    events = _stage_events(caplog)
    summary = _batch_summary(caplog)

    assert caught.value is signal_object
    assert transport.unexpected == []
    assert len(transport.posts()) == 1
    assert transport.drained() == {}
    assert not [request for request in transport.sent
                if 'catbox' in request.url]
    assert len(transport.requests_for('beeimg_cn', '/group')) == 1

    slots = [event for event in events if event.stage == 'slot']
    assert [event.status for event in slots[:2]] == ['started', 'cancelled']
    skipped = [event for event in slots
               if event.slot_index == 1 and event.status == 'skipped']
    assert len(skipped) == 1
    assert skipped[0].reason_code == 'batch_cancelled'

    queries = [event for event in events if event.stage == 'metadata_query']
    assert [(event.action, event.status) for event in queries] == [
        ('group', 'started'), ('group', 'cancelled')]

    assert summary['upload_post_attempts'] == 1
    assert summary['postupload_recoveries'] == 1
    assert summary['group_get_cancelled'] == 1


@pytest.mark.parametrize('diagnostics', [False, True])
def test_diagnostics_never_expose_synthetic_secrets(diagnostics, monkeypatch,
                                                    tmp_path, caplog, capsys):
    """两种诊断状态下，日志、异常与反馈均不含凭证、正文或容器内容。"""
    message = (f'{SYNTHETIC_TOKEN} '
               f'https://cdn.example.invalid/{LEAK_MARKER}')
    hosts = [{'provider': 'beeimg_cn', 'token': SYNTHETIC_TOKEN,
              'options': {'storage_id': [CONTAINER_MARKER]}}]
    transport = _EntryTransport(monkeypatch, {
        ('beeimg_cn', '/group'): [
            (200, _body({'status': 'error', 'message': message}))],
    })
    registry = import_module('modules.image_host.registry')
    cache = StorageCache(tmp_path / 'cache', clock=lambda: 1000.0)
    context = UploadContext(diagnostics=diagnostics, cache=cache,
                            clock=lambda: 1000.0,
                            resolved_tokens={0: SYNTHETIC_TOKEN})
    consumer = make_status_consumer(
        stream=sys.stderr, images_planned=1, slot_providers=host_providers(hosts))
    with caplog.at_level(logging.DEBUG, logger=TRACE_LOGGER):
        with trace_scope(mode='cli', images_planned=1, slots_planned=1,
                         on_status=consumer):
            with trace_item(image_index=1):
                with upload_feedback_scope(consumer):
                    result = registry.upload_with_fallback(
                        b'image', 'image.png', hosts, context=context)
    output = capsys.readouterr()
    context.close()

    assert result.success is False
    assert transport.drained() == {}
    assert transport.unexpected == []
    assert len(transport.requests_for('beeimg_cn', '/group')) == 1
    assert transport.posts() == []
    failure = result.failures[-1]
    assert failure.code == 'business_rejected'
    assert failure.stage == 'group'
    if diagnostics is False:
        assert failure.diagnostic is None
    surfaced = ''.join((
        caplog.text, output.out, output.err,
        str(failure.message), str(failure.diagnostic), str(failure.code),
        *result.warnings))
    assert SYNTHETIC_TOKEN not in surfaced
    assert LEAK_MARKER not in surfaced
    assert CONTAINER_MARKER not in surfaced
    assert URL not in surfaced
    assert json.dumps({'status': 'error', 'message': message}) not in surfaced


def test_manual_first_documentation_contract():
    """三个说明文件都以纯文本逐字包含手填优先的三句固定文案。"""
    root = Path(__file__).resolve().parents[1]
    for relative in ('modules/config.py', 'README.md', 'docs/开发者文档.md'):
        text = (root / relative).read_text(encoding='utf-8')
        assert '手填优先' in text, relative
        assert '自动选择组内首项' in text, relative
        assert '首次手填不按组上限预裁剪' in text, relative
