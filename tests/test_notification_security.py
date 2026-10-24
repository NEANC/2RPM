from copy import deepcopy
import io
import logging
from unittest.mock import Mock

import pytest

from modules import notification as notif
from modules import log_safety
from modules.image_host.context import UploadContext


@pytest.mark.parametrize('count', [0, 1])
@pytest.mark.parametrize('stage', ['client', 'status', 'json', 'unknown'])
def test_untrusted_failure_is_never_stringified(monkeypatch, caplog, count, stage):
    class UnsafeError(Exception):
        def __str__(self):
            pytest.fail('异常原文不得字符串化')

    class Response:
        @property
        def status_code(self):
            if stage == 'status':
                raise UnsafeError()
            return 200

        def json(self):
            raise UnsafeError()

    notifier = Mock()
    notifier.notify.return_value = object() if stage == 'unknown' else Response()
    if stage == 'client':
        notifier.notify.side_effect = UnsafeError()
    monkeypatch.setattr(notif, 'get_notifier', lambda provider: notifier)
    with caplog.at_level(logging.ERROR):
        assert notif._notify_single_channel(
            {'provider': 'qmsg'}, '标题', '正文', 0, 2, count) is False
    assert notifier.notify.call_count == 2
    reason = '客户端发送失败' if stage == 'client' else '响应判定失败'
    assert all(reason in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize('diagnostics', [False, True])
@pytest.mark.parametrize('fail', [False, True])
def test_resolved_credentials_survive_upload_scope(diagnostics, fail):
    secret = 'SYNTHETIC_RESOLVED_HOST_9311'
    with log_safety.safety_scope(('outer',)):
        with UploadContext(diagnostics=diagnostics) as context:
            for attempt in range(2):
                try:
                    with context.diagnostic_scope():
                        assert context.resolve_credential(0, {'token': secret}) == secret
                        assert secret in log_safety.secret_snapshot()
                        if fail:
                            raise RuntimeError()
                except RuntimeError:
                    pass
                assert secret in log_safety.secret_snapshot()
        assert secret in log_safety.secret_snapshot()
    assert log_safety.secret_snapshot() == ()


def test_template_log_uses_current_credentials(caplog):
    secret = 'SYNTHETIC_TEMPLATE_CREDENTIAL_9312'
    with log_safety.safety_scope((secret,)), caplog.at_level(logging.INFO):
        notif._log_rendered_notification('on_end', secret, secret, None)
    assert secret not in caplog.text
    assert log_safety.REDACTED in caplog.text


@pytest.mark.parametrize('key', [
    'token', 'access_token', 'refresh-token', 'api_key', 'API-KEY',
    'secret', 'client_secret', 'password', 'passwd', 'authorization',
    'cookie', 'sckey', 'sctkey', 'pushkey', 'key', 'device_key',
    'corpsecret', 'webhook', 'sign', 'cipherkey',
])
def test_channel_collects_only_authentication_aliases(monkeypatch, caplog, key):
    secret = 'SYNTHETIC_CHANNEL_AUTH_9313'
    ordinary = '普通参数保留9314'
    channel = {'provider': 'qmsg', key: secret, 'topic': ordinary,
               'userid': ordinary, 'agentid': ordinary, 'host': ordinary,
               'username': ordinary, 'priority': ordinary, 'mode': ordinary}
    config = {'push': {
        'templates': {'on_end': {'title': secret, 'content': ordinary}},
        'push_channel_settings': {'channels': [channel]},
        'retry': {'interval': '0s', 'max_count': 1},
    }}
    snapshots = []

    def notify(**kwargs):
        snapshots.append(log_safety.secret_snapshot())
        assert kwargs['title'] == secret
        assert kwargs['content'] == ordinary
        return Mock(status_code=200, json=Mock(return_value={'code': 0}))

    monkeypatch.setattr(notif, 'get_notifier', lambda provider: Mock(notify=notify))
    with caplog.at_level(logging.INFO):
        assert notif.send_notification(config, 'on_end') == [('qmsg', True)]
    assert snapshots == [(secret,)]
    assert secret not in caplog.text
    assert ordinary in caplog.text
    assert log_safety.secret_snapshot() == ()


@pytest.mark.parametrize('device_keys', [
    'SYNTHETIC_BARK_DEVICE_9315',
    ['SYNTHETIC_BARK_DEVICE_9315', 'SYNTHETIC_BARK_DEVICE_9316'],
])
def test_bark_secrets_reach_final_handlers_without_changing_send(
        monkeypatch, tmp_path, device_keys):
    """Bark 真实字段在最终控制台与文件隐藏，线程内发送仍保留原值。"""
    secrets = ['SYNTHETIC_BARK_CIPHER_9317', 'SYNTHETIC_BARK_PRIMARY_9318']
    secrets.extend([device_keys] if isinstance(device_keys, str) else device_keys)
    ordinary = '普通参数9319'
    channel = {'provider': 'bark', 'cipherkey': secrets[0],
               'device_key': secrets[1], 'device_keys': device_keys,
               'ciphermethod': 'ecb', 'group': ordinary, 'sound': ordinary}
    title = ' '.join(secrets)
    config = {'push': {
        'templates': {'on_end': {'title': title, 'content': ordinary}},
        'push_channel_settings': {'channels': [channel]},
        'retry': {'interval': '0s', 'max_count': 1},
    }}
    before = deepcopy(config)
    stream = io.StringIO()
    path = tmp_path / 'bark.log'
    handlers = [log_safety.SafeStreamHandler(stream),
                log_safety.SafeRotatingFileHandler(path, encoding='utf-8')]
    sent = []

    def notify(**kwargs):
        """记录发送原值并让真实安全 handler 处理线程日志。"""
        sent.append(kwargs)
        notif.LOGGER.warning('发送参数 %s %s', title, ordinary)
        return Mock(status_code=200, json=Mock(return_value={'code': 0}))

    monkeypatch.setattr(notif, 'get_notifier', lambda provider: Mock(notify=notify))
    for handler in handlers:
        handler.setFormatter(log_safety.SafeFormatter('%(message)s'))
        notif.LOGGER.addHandler(handler)
    try:
        assert notif.send_notification(config, 'on_end') == [('bark', True)]
    finally:
        for handler in handlers:
            notif.LOGGER.removeHandler(handler)
            handler.close()
    assert sent == [dict(title=title, content=ordinary,
                         **{key: value for key, value in channel.items()
                            if key != 'provider'})]
    assert config == before
    for output in (stream.getvalue(), path.read_text(encoding='utf-8')):
        assert all(secret not in output for secret in secrets)
        assert ordinary in output
        assert log_safety.REDACTED in output
    assert log_safety.secret_snapshot() == ()
