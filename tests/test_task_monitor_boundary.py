import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules import monitor
from modules import task_monitor
from test_task_monitor_dispatch import BASE_CONFIG
from test_task_monitor_dispatch import IDENTITY
from test_task_monitor_dispatch import TASK_TARGET
from test_task_monitor_dispatch import _Harness
from test_task_monitor_dispatch import _event
from test_task_monitor_events import _event_xml


@pytest.fixture
def log_env(monkeypatch):
    env = _Harness()
    env.install(monkeypatch)
    env.log = {100: _event_xml('\\Other', pid=9)}
    env.handles = []
    env.failure = None

    def query(channel, flags, xpath, context):
        if env.failure == 'query':
            raise RuntimeError('query failed')
        if xpath == '*' or 'EventRecordID=' not in xpath:
            ids = sorted(env.log, reverse=True)[:1]
        else:
            ids = [rid for rid in env.log if f'EventRecordID={rid}' in xpath]
        handle = SimpleNamespace(Close=Mock(), records=ids)
        env.handles.append(handle)
        return handle

    def next_events(handle, count):
        if env.failure == 'next':
            raise RuntimeError('next failed')
        result = [SimpleNamespace(Close=Mock(), xml=env.log[rid])
                  for rid in handle.records]
        env.handles.extend(result)
        handle.records = []
        return result

    def render(handle, flags):
        if env.failure == 'render':
            raise RuntimeError('render failed')
        return handle.xml

    monkeypatch.setattr(task_monitor.win32evtlog, 'EvtQuery', query)
    monkeypatch.setattr(task_monitor.win32evtlog, 'EvtNext', next_events)
    monkeypatch.setattr(task_monitor.win32evtlog, 'EvtRender', render)
    monkeypatch.setattr(monitor, 'get_task_event_boundary',
                        task_monitor.get_task_event_boundary)
    yield env
    for handle in env.handles:
        handle.Close.assert_called_once_with()


@pytest.mark.parametrize('records,candidates', [
    ({1: _event_xml('\\Other', pid=1, record_id=1)}, [_event(1, 4321)]),
    ({1: _event_xml('\\Other', pid=1, record_id=1)}, []),
    ({100: _event_xml('\\Other', pid=2, event_time='2026-11-10T05:00:00Z'),
      150: _event_xml('\\Other', pid=3, record_id=150)}, [_event(150, 4321)]),
    ({101: _event_xml('\\Other', pid=3, record_id=101)}, [_event(101, 4321)]),
    ({}, []),
])
def test_reset_or_missing_anchor_never_binds_or_runs_actions(
        log_env, records, candidates):
    env = log_env

    def trigger(*args, **kwargs):
        env.log = records
        return SimpleNamespace(returncode=0)

    env.run.side_effect = trigger
    env.query.return_value = candidates
    env.bind.return_value = dict(IDENTITY)
    config = dict(BASE_CONFIG, external={
        'on_end': 'end.exe', 'on_wait_timeout': 'timeout.exe'})
    with pytest.raises(SystemExit) as exc:
        monitor._launch_task_target(config, TASK_TARGET)
    assert exc.value.code == 1
    assert env.notify_fail.call_args.args[0] == '已触发任务，但无法确认监视实例'
    env.run.assert_called_once()
    env.bind.assert_not_called()
    env.action.assert_not_called()
    env.send.assert_not_called()


def test_continuous_log_binds_after_anchor_check(log_env):
    env = log_env
    env.query.return_value = [_event(101, 4321)]
    env.bind.return_value = dict(IDENTITY)
    result = monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)
    assert result['pid'] == 4321
    assert len(env.handles) == 4
    env.run.assert_called_once()


def test_empty_log_binds_only_event_after_trigger(log_env):
    env = log_env
    env.log.clear()
    old = SimpleNamespace(record_id=1, pid=1111,
                          event_time='1970-01-01T00:00:01+00:00')
    new = SimpleNamespace(
        record_id=2, pid=4321,
        event_time=datetime.datetime.fromtimestamp(
            1005, datetime.timezone.utc).isoformat())
    env.query.return_value = [old, new]
    env.bind.return_value = dict(IDENTITY)

    result = monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)

    assert result['pid'] == 4321
    assert [call.args[0] for call in env.bind.call_args_list] == [new]
    env.run.assert_called_once()


def test_empty_log_skips_invalid_event_time(log_env):
    env = log_env
    env.log.clear()
    invalid = SimpleNamespace(record_id=1, pid=1111, event_time='broken')
    env.query.return_value = [invalid]
    env.bind.return_value = dict(IDENTITY)

    with pytest.raises(SystemExit):
        monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)

    env.run.assert_called_once()
    env.bind.assert_not_called()
    assert env.send.call_args_list[0].args[1] == 'on_wait_timeout'


def test_empty_log_without_candidate_uses_normal_timeout(log_env):
    env = log_env
    env.log.clear()
    config = dict(BASE_CONFIG, external={'on_wait_timeout': 'timeout.exe'})

    with pytest.raises(SystemExit) as exc:
        monitor._launch_task_target(config, TASK_TARGET)

    assert exc.value.code == 1
    env.run.assert_called_once()
    env.bind.assert_not_called()
    assert env.send.call_args_list[0].args[1] == 'on_wait_timeout'
    env.action.assert_called_once_with('timeout.exe')


@pytest.mark.parametrize('phase', ['before', 'after'])
@pytest.mark.parametrize('failure', ['query', 'next', 'render'])
def test_empty_log_errors_never_bind_or_run_actions(
        log_env, monkeypatch, phase, failure):
    env = log_env
    env.log.clear()
    if failure == 'render':
        if phase == 'before':
            env.log[1] = _event_xml('\\Other', pid=1, record_id=1)
    monkeypatch.setattr(monitor, 'query_task_events',
                        task_monitor.query_task_events)
    if phase == 'before':
        env.failure = failure
    else:
        def trigger(*args, **kwargs):
            env.failure = failure
            if failure == 'render':
                env.log[2] = _event_xml('\\Other', pid=2, record_id=2)
            return SimpleNamespace(returncode=0)

        env.run.side_effect = trigger

    with pytest.raises(SystemExit) as exc:
        monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)

    assert exc.value.code == 1
    assert env.run.call_count == (phase == 'after')
    env.bind.assert_not_called()
    env.send.assert_not_called()
    env.action.assert_not_called()


def test_rotation_of_older_events_preserves_anchor(log_env):
    env = log_env
    env.log[99] = _event_xml('\\Other', pid=8, record_id=99)

    def trigger(*args, **kwargs):
        del env.log[99]
        env.log[101] = _event_xml('\\Other', pid=7, record_id=101)
        return SimpleNamespace(returncode=0)

    env.run.side_effect = trigger
    env.query.return_value = [_event(101, 4321)]
    env.bind.return_value = dict(IDENTITY)
    assert monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)['pid'] == 4321


def test_reset_between_polls_is_not_a_normal_timeout(log_env):
    env = log_env
    env.clock.step = 0.001

    def reset_log(seconds):
        """模拟等待后日志重置和时钟推进。"""
        env.log.clear()
        env.clock.current += seconds

    env.sleep.side_effect = reset_log
    with pytest.raises(SystemExit):
        monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)
    assert env.notify_fail.call_args.args[0] == '已触发任务，但无法确认监视实例'
    env.run.assert_called_once()
    env.bind.assert_not_called()
    env.send.assert_not_called()
    env.action.assert_not_called()


@pytest.mark.parametrize('phase', ['before', 'after'])
@pytest.mark.parametrize('failure', ['query', 'next', 'render'])
def test_boundary_errors_do_not_trigger_or_run_actions(log_env, phase, failure):
    env = log_env
    if phase == 'before':
        env.failure = failure
    else:
        def trigger(*args, **kwargs):
            env.failure = failure
            return SimpleNamespace(returncode=0)

        env.run.side_effect = trigger
    with pytest.raises(SystemExit) as exc:
        monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)
    assert exc.value.code == 1
    assert env.run.call_count == (phase == 'after')
    if phase == 'after':
        assert env.notify_fail.call_args.args[0] == '已触发任务，但无法确认监视实例'
    env.bind.assert_not_called()
    env.send.assert_not_called()
    env.action.assert_not_called()


def test_reset_during_query_is_checked_before_binding(log_env):
    env = log_env

    def candidates(*args):
        if env.run.called:
            env.log = {}
        return [_event(101, 4321)]

    env.query.side_effect = candidates
    env.bind.return_value = dict(IDENTITY)
    with pytest.raises(SystemExit):
        monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)
    env.bind.assert_not_called()
    assert env.notify_fail.call_args.args[0] == '已触发任务，但无法确认监视实例'


def test_boundary_failure_at_timeout_does_not_run_timeout_action(log_env):
    env = log_env
    env.clock.step = 10

    def trigger(*args, **kwargs):
        env.log = {}
        return SimpleNamespace(returncode=0)

    env.run.side_effect = trigger
    with pytest.raises(SystemExit):
        monitor._launch_task_target(BASE_CONFIG, TASK_TARGET)
    assert env.notify_fail.call_args.args[0] == '已触发任务，但无法确认监视实例'
    env.send.assert_not_called()
    env.action.assert_not_called()
