#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""真实 NTFS 排他创建及自动编号并发验收。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Barrier

from modules.screenshot import retention
from modules.screenshot import winfs
from modules.screenshot.models import CaptureResult
from test_winfs_partial_acceptance import require_ntfs
from winfs_process_guard import run_isolated


def test_concurrent_exclusive_creation_has_one_complete_winner(request, tmp_path):
    """同时竞争同一路径时仅一个完整载荷成功且失败者不清理胜者。"""
    if run_isolated(request, tmp_path):
        return
    require_ntfs(tmp_path)
    target = tmp_path / 'capture.bin'
    barrier = Barrier(2)
    payloads = (b'A' * 20001, b'B' * 17003)

    def compete(payload):
        """同步开始原生排他创建并记录精确的冲突结果。"""
        barrier.wait(timeout=10)
        try:
            winfs.write_exclusive(str(target), payload)
        except FileExistsError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, payloads))
    assert results.count(True) == 1
    assert target.read_bytes() == payloads[results.index(True)]
    target.unlink()


def test_automatic_concurrent_snapshot_conflict_keeps_both_payloads(
        monkeypatch, request, tmp_path):
    """两个保存者观察相同最大编号，冲突递增后各自载荷完整且旧文件不变。"""
    if run_isolated(request, tmp_path):
        return
    require_ntfs(tmp_path)
    folder = tmp_path / 'screenshot' / '2026_10_06' / 'config'
    folder.mkdir(parents=True)
    existing = folder / 'event_09.JPG'
    existing.write_bytes(b'existing')
    barrier = Barrier(2)
    original = winfs.list_names
    payloads = (b'A' * 20001, b'B' * 17003)

    def shared_snapshot(path):
        """两个线程完成真实枚举后才允许进入写入竞争。"""
        names = original(path)
        barrier.wait(timeout=10)
        return names

    def save(payload):
        """只提交固定字节载荷，不调用截图或上传。"""
        return retention.save_automatic(
            CaptureResult(payload, 'window', 'fixture', 1, 1),
            retention.runtime_context(str(tmp_path), 'config.yaml'),
            'event', retention.RetentionPolicy(True), today=date(2026, 10, 6))

    monkeypatch.setattr(winfs, 'list_names', shared_snapshot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save, payloads))
    assert {outcome.filename for outcome in outcomes} == {'event_10.jpg', 'event_11.jpg'}
    for outcome, payload in zip(outcomes, payloads):
        assert outcome.warnings == ()
        assert outcome.path is not None
        assert Path(outcome.path).read_bytes() == payload
    assert existing.read_bytes() == b'existing'
    assert len(tuple(folder.iterdir())) == 3
    folder.rename(folder.with_name('released'))
