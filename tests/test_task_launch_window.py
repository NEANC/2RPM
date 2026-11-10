#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""用内存时钟验证计划任务 launch 的事件查询窗口。"""

from types import SimpleNamespace

import pytest

from modules import monitor
from modules.task_monitor import TaskQueryError
from test_task_monitor_dispatch import BASE_CONFIG
from test_task_monitor_dispatch import IDENTITY
from test_task_monitor_dispatch import TASK_TARGET
from test_task_monitor_dispatch import _Harness
from test_task_monitor_dispatch import _event


@pytest.mark.parametrize('poll_delay,trigger_delay', [
    (90, 0),
    (90, 5),
    (60, 0),
    (120, 0),
])
def test_launch_window_retains_first_second_event(
        monkeypatch, poll_delay, trigger_delay):
    """第二轮查询包括触发后第 1 秒的事件，含整分钟边界与触发耗时。"""
    env = _Harness()
    env.install(monkeypatch)
    env.clock.step = 0
    env.clock.current = 1000
    config = {
        'monitor': {
            'common': {'max_wait': '180s', 'check_interval': '90s'},
            'watch': {'lookback_minutes': 0},
        },
    }
    event_time = 1000 + trigger_delay + 1
    queries = []

    def trigger(*args, **kwargs):
        """模拟触发命令执行期间流逝的时间。"""
        env.clock.current += trigger_delay
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    def sleep(seconds):
        """仅推进内存时钟，不等待。"""
        env.clock.current += poll_delay

    def query(task_path, minutes):
        """模拟按查询时刻的分钟窗口筛选事件。"""
        queries.append((env.clock.current, minutes))
        if len(queries) <= 2:
            return []
        if env.clock.current - event_time <= minutes * 60:
            return [_event(101, 4321)]
        return []

    env.run.side_effect = trigger
    env.sleep.side_effect = sleep
    env.query.side_effect = query
    env.bind.return_value = dict(IDENTITY)

    result = monitor._launch_task_target(config, TASK_TARGET)

    assert result['pid'] == 4321
    assert queries[0][1] == 1
    assert queries[2][1] * 60 >= queries[2][0] - 1000
    assert env.sleep.call_count == 1
    env.send.assert_not_called()
    env.action.assert_not_called()


def test_launch_query_failure_after_first_poll_is_not_timeout(monkeypatch):
    """第二轮查询失败不应执行正常超时通知或外部动作。"""
    env = _Harness()
    env.install(monkeypatch)
    env.clock.step = 0
    env.clock.current = 1000
    env.sleep.side_effect = lambda seconds: setattr(
        env.clock, 'current', env.clock.current + 90)
    env.query.side_effect = [[], [], TaskQueryError('查询失败')]
    config = {'monitor': {'common': {
        'max_wait': '180s', 'check_interval': '90s'}}}

    with pytest.raises(SystemExit) as error:
        monitor._launch_task_target(config, TASK_TARGET)

    assert error.value.code == 1
    assert env.notify_fail.call_args.args[0] == '已触发任务，但无法确认监视实例'
    env.send.assert_not_called()
    env.action.assert_not_called()
