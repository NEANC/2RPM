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
