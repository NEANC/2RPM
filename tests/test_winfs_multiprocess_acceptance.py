#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""两个独立 Windows 进程的真实 FILE_CREATE 竞争验收。"""

import multiprocessing
import os
import time

import pytest

from modules.screenshot import winfs
from test_winfs_partial_acceptance import require_ntfs


PROCESS_TIMEOUT = 15
REAP_TIMEOUT = 3
PAYLOADS = (b'A' * 20001, b'B' * 17003)


def compete_in_process(target, payload, barrier, connection):
    """在原生排他创建入口同步独立进程并回传真实结果。"""
    original = winfs._NtCreate

    def synchronized_create(*args):
        """仅在 FILE_CREATE 处分阶段同步，随后调用真实 NT API。"""
        if args[7] == 2:
            barrier.wait(timeout=PROCESS_TIMEOUT)
        return original(*args)

    winfs._NtCreate = synchronized_create
    try:
        try:
            winfs.write_exclusive(target, payload)
        except FileExistsError:
            connection.send((os.getpid(), False))
        else:
            connection.send((os.getpid(), True))
    finally:
        connection.close()


def test_independent_processes_exclusive_create_preserves_winner(tmp_path):
    """两进程争同名仅一胜者，完整字节及再次冲突后的原文件均保持。"""
    require_ntfs(tmp_path)
    context = multiprocessing.get_context('spawn')
    barrier = context.Barrier(2)
    target = tmp_path / 'capture.bin'
    sentinel = tmp_path / 'existing.bin'
    sentinel.write_bytes(b'original')
    processes = []
    connections = []
    deadline = time.monotonic() + PROCESS_TIMEOUT
    try:
        for payload in PAYLOADS:
            receiver, sender = context.Pipe(duplex=False)
            connections.extend((receiver, sender))
            process = context.Process(
                target=compete_in_process,
                args=(str(target), payload, barrier, sender))
            process.start()
            processes.append(process)
            sender.close()
        for process in processes:
            process.join(timeout=max(0, deadline - time.monotonic()))
        assert all(not process.is_alive() for process in processes), '竞争进程超时'
        assert [process.exitcode for process in processes] == [0, 0]
        results = []
        for connection in connections[::2]:
            assert connection.poll(max(0, deadline - time.monotonic())), '缺少进程结果'
            results.append(connection.recv())
        assert len({pid for pid, _ in results}) == 2
        assert all(pid != os.getpid() for pid, _ in results)
        successes = [success for _, success in results]
        assert successes.count(True) == 1
        expected = PAYLOADS[successes.index(True)]
        assert target.read_bytes() == expected
        with pytest.raises(FileExistsError):
            winfs.write_exclusive(str(target), b'cannot overwrite')
        assert target.read_bytes() == expected
        with pytest.raises(FileExistsError):
            winfs.write_exclusive(str(sentinel), b'cannot overwrite')
        assert sentinel.read_bytes() == b'original'
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=REAP_TIMEOUT)
            if process.is_alive():
                process.kill()
                process.join(timeout=REAP_TIMEOUT)
            assert not process.is_alive(), '无法回收竞争进程'
            process.close()
        for connection in connections:
            connection.close()
