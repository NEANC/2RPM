import logging
from unittest.mock import Mock

import pytest

from modules import notification as notif


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
