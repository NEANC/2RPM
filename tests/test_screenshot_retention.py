#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""截图保留策略测试。"""

from datetime import date

import pytest

from modules.screenshot import retention
from modules.screenshot.models import CaptureResult


def test_policy_rejects_boolean_as_days():
    """天数配置只接受严格正整数。"""
    policy = retention.parse_policy({'enabled': True, 'max_days': True})
    assert policy.enabled is True
    assert policy.max_days == 14
    assert policy.warnings


def test_component_sanitizing_blocks_devices_and_path_separators():
    """自动名称组件净化非法字符与设备名。"""
    assert retention.sanitize_component('../CON.txt', 'event') == '.._CON.txt'
    assert retention.sanitize_component('NUL', 'event') == '_NUL'
    assert len(retention.sanitize_component('x' * 100, 'event')) == 80


def test_cleanup_uses_calendar_days_and_handle_tree_removal(monkeypatch, tmp_path):
    """仅按严格有效日期计算保留边界并经 winfs 删除日期树。"""
    removed = []
    monkeypatch.setattr(retention.winfs, 'list_names', lambda path: (
        '2024_02_29', '2024_03_01', '2024_02_30', '2024_03_02'))
    monkeypatch.setattr(retention.winfs, 'remove_tree', lambda path: removed.append(path))
    assert retention.cleanup_retention(str(tmp_path), retention.RetentionPolicy(True, 2),
                                       today=date(2024, 3, 2)) == ()
    assert removed == [str(tmp_path / 'screenshot' / '2024_02_29')]


def test_cli_save_increments_unbounded_sequence(monkeypatch, tmp_path):
    """目录保存使用四位递增编号并保留原始载荷。"""
    result = CaptureResult(b'fixture', 'adb', 'device', 1, 1, image_format='png')
    calls = []

    def write(path, data):
        calls.append((path, data))
        if len(calls) == 1:
            raise FileExistsError(path)

    monkeypatch.setattr(retention.winfs, 'write_exclusive', write)
    outcome = retention.save_cli(result, str(tmp_path), False, today=date(2026, 10, 7))
    assert outcome.filename == 'screenshot_20261007_0001.png'
    assert calls[-1][1] == result.image_bytes
