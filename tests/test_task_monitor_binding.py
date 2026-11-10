#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证基于 PID、可执行路径与创建时间的弱身份绑定。"""

import datetime
import os
import sys
import unittest
from unittest.mock import patch

import psutil

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import task_monitor

EVENT_TIME = '2026-11-10T04:30:28.1800210Z'
EVENT_TIMESTAMP = datetime.datetime(
    2026, 11, 10, 4, 30, 28, 180021,
    tzinfo=datetime.timezone.utc,
).timestamp()

EVENT_EXE = 'E:\\GitHub\\2RPM\\.venv\\Scripts\\python.exe'


def _make_event(pid=80612, path=EVENT_EXE, event_time=EVENT_TIME):
    """构造与 G3 实测字段一致的 TaskEvent。"""
    return task_monitor.TaskEvent(
        task_path='\\Folder\\Task',
        pid=pid,
        event_time=event_time,
        record_id=339373,
        instance_id=None,
        path=path,
    )


def _raise_or_return(value):
    """替身方法按需抛出异常或返回固定值。"""
    if isinstance(value, BaseException):
        raise value
    return value


class _FakeProcess:
    """替身进程对象，只实现绑定所需的三个方法。"""

    def __init__(self, exe, create_time, name='python.exe'):
        self._exe = exe
        self._create_time = create_time
        self._name = name

    def exe(self):
        """返回可执行文件路径或抛出替身异常。"""
        return _raise_or_return(self._exe)

    def create_time(self):
        """返回创建时间或抛出替身异常。"""
        return _raise_or_return(self._create_time)

    def name(self):
        """返回进程名或抛出替身异常。"""
        return _raise_or_return(self._name)


class TestBindTaskEvent(unittest.TestCase):
    """验证 bind_task_event 的逐项校验。"""

    def test_bind_ok(self):
        """路径一致且时间差 5 毫秒时返回进程身份。"""
        process = _FakeProcess(EVENT_EXE, EVENT_TIMESTAMP + 0.005)
        with patch.object(task_monitor.psutil, 'Process', return_value=process):
            bound = task_monitor.bind_task_event(_make_event())

        self.assertEqual(bound, {
            'pid': 80612,
            'name': 'python.exe',
            'create_time': EVENT_TIMESTAMP + 0.005,
        })

    def test_bind_rejects_pid_reuse_by_time(self):
        """创建时间相差 30 秒视为 PID 复用，拒绝绑定。"""
        process = _FakeProcess(EVENT_EXE, EVENT_TIMESTAMP - 30.0)
        with patch.object(task_monitor.psutil, 'Process', return_value=process):
            bound = task_monitor.bind_task_event(_make_event())

        self.assertIsNone(bound)

    def test_bind_rejects_different_exe(self):
        """可执行路径不一致时拒绝绑定。"""
        process = _FakeProcess('E:\\Tools\\other.exe', EVENT_TIMESTAMP + 0.005)
        with patch.object(task_monitor.psutil, 'Process', return_value=process):
            bound = task_monitor.bind_task_event(_make_event())

        self.assertIsNone(bound)

    def test_bind_skips_no_such_process(self):
        """进程不存在时返回 None。"""
        with patch.object(task_monitor.psutil, 'Process',
                          side_effect=psutil.NoSuchProcess(80612)):
            bound = task_monitor.bind_task_event(_make_event())

        self.assertIsNone(bound)

    def test_bind_skips_access_denied(self):
        """无法读取进程身份时返回 None。"""
        with patch.object(task_monitor.psutil, 'Process',
                          side_effect=psutil.AccessDenied(80612)):
            bound = task_monitor.bind_task_event(_make_event())

        self.assertIsNone(bound)

    def test_bind_rejects_unexpanded_env_path(self):
        """事件路径含未展开环境变量时拒绝绑定。"""
        unexpanded = '%windir%\\System32\\x.exe'
        process = _FakeProcess(unexpanded, EVENT_TIMESTAMP + 0.005)
        with patch.object(task_monitor.psutil, 'Process', return_value=process):
            bound = task_monitor.bind_task_event(_make_event(path=unexpanded))

        self.assertIsNone(bound)

    def test_bind_handles_bad_event_time(self):
        """事件时间非法时返回 None 且不抛异常。"""
        process = _FakeProcess(EVENT_EXE, EVENT_TIMESTAMP + 0.005)
        with patch.object(task_monitor.psutil, 'Process', return_value=process):
            bound = task_monitor.bind_task_event(
                _make_event(event_time='not-a-time'))

        self.assertIsNone(bound)

    def test_bind_without_event_link_is_documented(self):
        """任务名相同不放行，只有 exe 与时间都匹配才返回身份。"""
        mismatched = _FakeProcess('E:\\Tools\\other.exe', EVENT_TIMESTAMP + 0.005)
        with patch.object(task_monitor.psutil, 'Process', return_value=mismatched):
            rejected = task_monitor.bind_task_event(_make_event())
        self.assertIsNone(rejected)

        matched = _FakeProcess(EVENT_EXE, EVENT_TIMESTAMP - 0.006)
        with patch.object(task_monitor.psutil, 'Process', return_value=matched):
            accepted = task_monitor.bind_task_event(_make_event())
        self.assertIsNotNone(accepted)


if __name__ == '__main__':
    unittest.main()
