#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""离线验收已安装 onepush 的通道载荷、Bark 加密、并发隔离与日志脱敏。"""

from copy import deepcopy
from html import escape as html_escape
from importlib import import_module
import io
import logging
import netrc
import socket
import threading
from unittest.mock import Mock

import pytest
import requests
from onepush import all_providers
from onepush import get_notifier
from onepush.exceptions import NoSuchNotifierError
from onepush.providers.smtp import SMTP

from modules import log_safety
from modules import notification as notif
from modules.image_host.core import UploadResult
from modules.log_safety import guard_dependency_logging
from modules.log_safety import safety_scope
from modules.log_safety import secret_snapshot
from modules.notification_rendering import ScreenshotRenderContext
from modules.notification_rendering import prepare_channel
from modules.operation_trace import current_event_id
from modules.screenshot.models import CaptureResult
from modules.screenshot.pipeline import ScreenshotBatch
from modules.screenshot.retention import SaveOutcome


TITLE = 'T'
URL = 'https://cdn.example/a%28b%29.png?sig=x%2Fy&k=z'
IMAGE = f'![shot]({URL})'
BATCH = ScreenshotBatch({'shot': IMAGE, 'screenshot': IMAGE}, (), ())
CONTEXT = ScreenshotRenderContext(TITLE, '{screenshot}', dict(BATCH.values),
                                  BATCH, True)

SENSITIVE_TEXT = 'synthetic marker 6001'
MAIL_LOGIN_VALUE = 'synthetic marker 6002'
ENCRYPTION_PARAM = 'synthetic marker 6003'
SMTP_CHANNEL = {
    'provider': 'smtp',
    'msg': None,
    'subject': TITLE,
    'From': 'sender@example.test',
    'To': 'recipient@example.test',
    'user': 'user@example.test',
    'host': 'smtp.example.test',
    'password': MAIL_LOGIN_VALUE,
}

PROVIDER_CASES = (
    ('dingtalk', {'provider': 'dingtalk', 'token': 'DINGTALK'}),
    ('pushplus', {'provider': 'pushplus', 'token': 'PUSHPLUS'}),
    ('wechatworkapp', {'provider': 'wechatworkapp', 'corpid': 'CORP',
                       'corpsecret': 'synthetic corp value', 'agentid': '1'}),
    ('wechatworkapp-mpnews', {'provider': 'wechatworkapp', 'corpid': 'CORP',
                              'corpsecret': 'synthetic corp value', 'agentid': '1',
                              'media_id': ''}),
    ('wechatworkbot-1', {'provider': 'wechatworkbot', 'key': 'KEY',
                         'markdown': 1}),
    ('wechatworkbot-2', {'provider': 'wechatworkbot', 'key': 'KEY',
                         'markdown': 2}),
    ('pushdeer', {'provider': 'pushdeer', 'pushkey': 'PUSHKEY',
                  'type': 'text'}),
    ('telegram', {'provider': 'telegram', 'token': 'TOKEN', 'userid': '42'}),
    ('ntfy', {'provider': 'ntfy', 'url': 'https://ntfy.invalid',
              'topic': 'topic'}),
    ('bark', {'provider': 'bark', 'key': 'DEVICE_KEY', 'body': 'old',
              'markdown': True}),
    ('discord', {'provider': 'discord', 'webhook': 'https://discord.invalid/h',
                 'username': 'bot',
                 'avatar_url': 'https://example.invalid/a.png',
                 'color': '16478873'}),
    ('smtp', SMTP_CHANNEL),
    ('gotify', {'provider': 'gotify', 'url': 'https://gotify.invalid',
                'token': 'TOKEN'}),
    ('serverchan', {'provider': 'serverchan', 'sckey': 'SKEY'}),
    ('serverchanturbo', {'provider': 'serverchanturbo', 'sctkey': 'SCTKEY',
                         'channel': 1, 'openid': 'openid'}),
    ('gocqhttp', {'provider': 'gocqhttp', 'endpoint': 'http://gocq.invalid',
                  'token': 'TOKEN', 'message_type': 'private', 'user_id': 1,
                  'group_id': 2, 'auto_escape': False}),
    ('custom', {'provider': 'custom', 'url': 'https://custom.invalid',
                'method': 'post', 'datatype': 'json', 'data': {'p': 'x'}}),
    ('qmsg', {'provider': 'qmsg', 'key': 'KEY', 'qq': '123'}),
    ('lark', {'provider': 'lark', 'webhook': 'https://lark.invalid',
              'keyword': 'KW', 'sign': ''}),
    ('wpush', {'provider': 'wpush', 'apikey': 'APIKEY'}),
)

BASELINE_CASES = frozenset((
    'gotify', 'serverchan', 'serverchanturbo', 'gocqhttp', 'custom', 'qmsg',
    'lark', 'wpush',
))


@pytest.fixture(autouse=True)
def isolate_channel_acceptance(monkeypatch, tmp_path):
    """阻断全部真实网络、隐式账号读取，并把缓存根固定在临时目录。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))

    def forbidden(*args, **kwargs):
        """任何越过合成边界的网络或 netrc 访问立即失败。"""
        pytest.fail('验收测试禁止真实网络或 netrc 访问')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', forbidden)
    monkeypatch.setattr(requests.sessions, 'get_netrc_auth', forbidden)
    monkeypatch.setattr(requests.utils, 'get_netrc_auth', forbidden)
    monkeypatch.setattr(netrc, 'netrc', forbidden)


def _baseline_params(channel):
    """返回通道去掉 provider 后的参数副本。"""
    return {key: value for key, value in channel.items() if key != 'provider'}


@pytest.mark.parametrize(
    'case_id, channel', PROVIDER_CASES,
    ids=[case[0] for case in PROVIDER_CASES])
def test_installed_provider_payloads(case_id, channel):
    """直接准备安装包 provider 的载荷，不访问 URL 或网络。"""
    original_parser = SMTP._message_parser

    # wpush 不在已安装 onepush 1.9.0 的注册表中，只验证通知层基线不变
    if channel['provider'] not in all_providers():
        assert channel['provider'] == 'wpush'
        with pytest.raises(NoSuchNotifierError):
            get_notifier(channel['provider'])
        title, content, params = prepare_channel(
            channel, TITLE, IMAGE, CONTEXT)
        assert (title, content, params) == (
            TITLE, IMAGE, _baseline_params(channel))
        return

    notifier = get_notifier(channel['provider'])
    title, content, params = prepare_channel(channel, TITLE, IMAGE, CONTEXT)
    payload = notifier._prepare_data(title=title, content=content, **params)

    # 只调用 _prepare_data，URL 与连接端点均未被准备
    assert notifier.url is None

    if case_id == 'dingtalk':
        assert payload['msgtype'] == 'markdown'
    elif case_id == 'pushplus':
        assert payload['template'] == 'markdown'
    elif case_id == 'wechatworkapp':
        assert payload['msgtype'] == 'markdown'
    elif case_id == 'wechatworkapp-mpnews':
        assert payload['msgtype'] == 'mpnews'
    elif case_id == 'wechatworkbot-1':
        assert payload['msgtype'] == 'markdown'
    elif case_id == 'wechatworkbot-2':
        assert payload['msgtype'] == 'markdown_v2'
    elif case_id == 'pushdeer':
        assert payload['type'] == 'markdown'
    elif case_id == 'telegram':
        assert payload['text'] == TITLE + '\n\n' + IMAGE
        assert payload['parse_mode'] == 'Markdown'
        assert payload['disable_web_page_preview'] is True
    elif case_id == 'ntfy':
        assert payload['markdown'] is True
    elif case_id == 'bark':
        assert payload['body'] == payload['markdown'] == IMAGE
    elif case_id == 'discord':
        embed = payload['embeds'][0]
        assert embed['description'] == URL
        assert embed['title'] == TITLE
        assert embed['color'] == channel['color']
        for key in ('username', 'avatar_url'):
            assert payload[key] == channel[key]
    elif case_id == 'smtp':
        assert payload['msg'] is params['msg']
        assert SMTP._message_parser is original_parser
    elif case_id in BASELINE_CASES:
        baseline = get_notifier(channel['provider'])._prepare_data(
            title=TITLE, content=IMAGE, **_baseline_params(channel))
        assert (title, content, params) == (
            TITLE, IMAGE, _baseline_params(channel))
        assert payload == baseline


def test_bark_encryption_receives_markdown():
    """Bark 加密前收到 markdown 正文，预制密文不被替换为明文。"""
    channel = {
        'provider': 'bark',
        'key': 'DEVICE_KEY',
        'body': 'old',
        'markdown': True,
        'cipherkey': ENCRYPTION_PARAM,
        'ciphermethod': 'cbc',
    }
    notifier = get_notifier('bark')
    encryption_calls = []
    before_encryption = {}

    def encrypt_spy(key, method):
        """记录加密入参并产出合成密文。"""
        encryption_calls.append((key, method))
        before_encryption.update(deepcopy(notifier.data))
        notifier.data = {'ciphertext': 'SYNTHETIC_CIPHERTEXT_6004'}

    notifier._encrypt_data = encrypt_spy
    title, content, params = prepare_channel(channel, TITLE, IMAGE, CONTEXT)
    payload = notifier._prepare_data(title=title, content=content, **params)

    assert encryption_calls == [(ENCRYPTION_PARAM, 'cbc')]
    assert before_encryption['markdown'] == IMAGE
    assert payload == {'ciphertext': 'SYNTHETIC_CIPHERTEXT_6004'}
    assert IMAGE not in str(payload)
    assert URL not in str(payload)

    sealed_channel = {
        'provider': 'bark',
        'key': 'DEVICE_KEY',
        'device_keys': ['DEVICE_1', 'DEVICE_2'],
        'ciphertext': 'SEALED_CIPHERTEXT_6005',
        'body': 'old',
        'markdown': True,
    }
    sealed = get_notifier('bark')
    sealed._encrypt_data = Mock(side_effect=AssertionError('不得重新加密'))
    title, content, params = prepare_channel(
        sealed_channel, TITLE, IMAGE, CONTEXT)
    sealed_payload = sealed._prepare_data(
        title=title, content=content, **params)

    sealed._encrypt_data.assert_not_called()
    assert sealed_payload == {
        'ciphertext': 'SEALED_CIPHERTEXT_6005',
        'device_key': 'DEVICE_KEY',
        'device_keys': ['DEVICE_1', 'DEVICE_2'],
    }
    assert IMAGE not in str(sealed_payload)


def test_parallel_events_and_channels_are_isolated(monkeypatch):
    """两个事件并发发送时截图批次、SMTP 消息与事件身份互不串线。"""
    module = import_module('modules.screenshot.pipeline')
    barrier = threading.Barrier(2, timeout=10)
    upload_lock = threading.Lock()
    uploaded_urls = []

    def upload(image, filename, hosts, **kwargs):
        """每个事件只上传一次并返回本事件独有的签名 URL。"""
        with upload_lock:
            index = len(uploaded_urls)
            uploaded_urls.append(
                f'https://cdn.example/event-{index + 1}.png?sig=E{index + 1}%2F1')
            url = uploaded_urls[-1]
        barrier.wait()
        return UploadResult(True, 'catbox', url, ('catbox',), ())

    capture = Mock(side_effect=lambda source, name, **kwargs: CaptureResult(
        b'png-' + name.encode(), source, name, 2, 3))
    upload_mock = Mock(side_effect=upload)
    monkeypatch.setattr(module, 'capture', capture)
    monkeypatch.setattr(module, 'upload_with_fallback', upload_mock)
    monkeypatch.setattr(module, 'save_automatic', Mock(
        return_value=SaveOutcome(None, 'shot.jpg')))

    records = []
    attempts = {}
    records_lock = threading.Lock()

    def notify(title=None, content=None, **params):
        """每个事件通道首次抛异常，重试时返回空 dict 表示全部接受。"""
        event_id = current_event_id()
        key = (event_id, params.get('host'))
        with records_lock:
            count = attempts.get(key, 0) + 1
            attempts[key] = count
            records.append((event_id, params.get('host'), params.get('msg')))
        if count == 1:
            raise RuntimeError('synthetic first attempt')
        return {}

    monkeypatch.setattr(notif, 'get_notifier', lambda provider: Mock(
        notify=Mock(side_effect=notify)))

    def config():
        """构造两通道 SMTP、单目标截图的通知配置。"""
        return {'push': {
            'screenshot': {
                'targets': [{'provider': 'window', 'target': '窗口'}],
                'image_host': [{'provider': 'catbox'}],
            },
            'templates': {'on_end': {
                'enable': True,
                'capture_screenshot': True,
                'title': '标题',
                'content': '{screenshot}',
            }},
            'push_channel_settings': {'channels': [
                {'provider': 'smtp', 'host': 'smtp-a.invalid',
                 'user': 'user@example.invalid', 'password': MAIL_LOGIN_VALUE,
                 'subject': 'A', 'From': 'from@example.invalid',
                 'To': 'to@example.invalid'},
                {'provider': 'smtp', 'host': 'smtp-b.invalid',
                 'user': 'user@example.invalid', 'password': MAIL_LOGIN_VALUE,
                 'subject': 'B', 'From': 'from@example.invalid',
                 'To': 'to@example.invalid'},
            ]},
            'retry': {'interval': '0s', 'max_count': 2},
        }}

    results = [None, None]

    def run(index):
        """在线程内执行一次完整通知，等待两个事件共同越过上传屏障。"""
        results[index] = notif.send_notification(config(), 'on_end')

    threads = [threading.Thread(target=run, args=(index,)) for index in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert all(not thread.is_alive() for thread in threads)
    assert results == [[('smtp', True), ('smtp', True)]] * 2
    assert capture.call_count == upload_mock.call_count == 2
    assert len(uploaded_urls) == 2

    messages = [message for _, _, message in records]
    assert len({id(message) for message in messages}) == 4

    grouped = {}
    for event_id, host, message in records:
        grouped.setdefault((event_id, host), []).append(message)
    assert len(grouped) == 4
    for retries in grouped.values():
        assert len(retries) == 2
        assert retries[0] is retries[1]

    event_ids = {event_id for event_id, _, _ in records}
    assert len(event_ids) == 2

    url_sets = []
    for event_id in event_ids:
        event_messages = [
            message for observed, _, message in records
            if observed == event_id
        ]
        contained = {
            url for url in uploaded_urls
            if any(url in message.as_string() for message in event_messages)
        }
        assert len(contained) == 1
        url_sets.append(contained)
    assert url_sets[0].isdisjoint(url_sets[1])


def test_dependency_logs_are_dropped():
    """保护范围内的依赖记录被丢弃，范围外与退出后仍可输出。"""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger = logging.getLogger('onepush.acceptance')
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    entity_url = html_escape(URL, quote=True)
    try:
        logger.warning('保护外标记_%s', 'OUTSIDE_6006')
        assert 'OUTSIDE_6006' in stream.getvalue()

        with guard_dependency_logging(('onepush', 'requests', 'urllib3')):
            logger.warning(
                '保护内标记_%s %s %s %s',
                'INSIDE_6007', URL, entity_url, SENSITIVE_TEXT)
        output = stream.getvalue()
        assert 'INSIDE_6007' not in output
        assert URL not in output
        assert entity_url not in output
        assert SENSITIVE_TEXT not in output

        logger.warning('保护后标记_%s', 'AFTER_6008')
        output = stream.getvalue()
        assert 'OUTSIDE_6006' in output
        assert 'AFTER_6008' in output
    finally:
        logger.handlers = []
        logger.setLevel(logging.NOTSET)
        handler.close()


def _smtp_config():
    """构造禁用截图但直接引用占位符的 SMTP 单通道配置。"""
    return {'push': {
        'templates': {'on_end': {
            'enable': True,
            'capture_screenshot': False,
            'title': '标题',
            'content': '{screenshot}',
        }},
        'push_channel_settings': {'channels': [{
            'provider': 'smtp',
            'host': 'smtp.example.test',
            'user': 'user@example.test',
            'password': MAIL_LOGIN_VALUE,
            'subject': '主题',
            'From': 'from@example.test',
            'To': 'to@example.test',
        }]},
        'retry': {'interval': '0s', 'max_count': 1},
    }}


def _run_smtp_notification(monkeypatch, response):
    """执行一次 SMTP 通知并返回真实最终处理器输出。"""
    stream = io.StringIO()
    handler = log_safety.SafeStreamHandler(stream)
    handler.setFormatter(log_safety.SafeFormatter('%(message)s'))
    previous_level = notif.LOGGER.level
    notif.LOGGER.addHandler(handler)
    notif.LOGGER.setLevel(logging.DEBUG)
    notifier = Mock()
    notifier.notify.return_value = response
    monkeypatch.setattr(notif, 'get_notifier', lambda provider: notifier)
    try:
        result = notif.send_notification(_smtp_config(), 'on_end')
    finally:
        notif.LOGGER.removeHandler(handler)
        notif.LOGGER.setLevel(previous_level)
        handler.close()
    return result, stream.getvalue()


def test_rendered_secret_variants_are_redacted(monkeypatch):
    """HTML 实体 URL 在准备上下文注册，最终日志只保留脱敏后的普通文本。"""
    signed_url = 'https://cdn.example/a%28b%29.png?sig=x%2Fy&k=z&q="v"'
    batch = ScreenshotBatch(
        {'shot': f'![shot]({signed_url})',
         'screenshot': f'![shot]({signed_url})'},
        (), ())
    context = ScreenshotRenderContext(
        TITLE, '{screenshot}', dict(batch.values), batch, True)
    html_url = html_escape(signed_url, quote=True)
    assert html_url != signed_url

    with safety_scope((SENSITIVE_TEXT,)):
        assert html_url not in secret_snapshot()

        title, content, params = prepare_channel(
            dict(SMTP_CHANNEL), TITLE, IMAGE, context)
        assert html_url in secret_snapshot()
        message = params['msg']
        plain_body = message.get_body(
            preferencelist=('plain',)).get_content()
        html_body = message.get_body(
            preferencelist=('html',)).get_content()
        assert signed_url in plain_body
        assert f'src="{html_url}"' in html_body
        assert html_url not in plain_body

        stream = io.StringIO()
        handler = log_safety.SafeStreamHandler(stream)
        handler.setFormatter(log_safety.SafeFormatter('%(message)s'))
        logger = logging.getLogger('acceptance-render-secret')
        logger.handlers = [handler]
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        try:
            logger.warning(
                '前普通标记_%s %s %s %s 后普通标记_%s',
                'BEFORE_6009', signed_url, html_url, SENSITIVE_TEXT, 'AFTER_6010')
        finally:
            logger.handlers = []
            logger.setLevel(logging.NOTSET)
            handler.close()
        output = stream.getvalue()
        assert 'BEFORE_6009' in output
        assert 'AFTER_6010' in output
        assert log_safety.REDACTED in output
        assert signed_url not in output
        assert html_url not in output
        assert SENSITIVE_TEXT not in output

        result, output = _run_smtp_notification(monkeypatch, {})
        assert result == [('smtp', True)]
        assert output.count('通知标题:') == 1
        assert 'params' not in output
        assert 'msg' not in output

        private_address = 'private.person@example.invalid'
        server_reply = 'SYNTHETIC_REJECT_REPLY_6011'
        result, output = _run_smtp_notification(
            monkeypatch, {private_address: (550, server_reply)})
        assert result == [('smtp', True)]
        assert (
            'SMTP 部分收件人被拒收，至少部分已接受；已终止重试' in output)
        assert private_address not in output
        assert server_reply not in output

    assert secret_snapshot() == ()
