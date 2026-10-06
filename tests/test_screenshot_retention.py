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


def test_automatic_save_uses_jpeg_and_starts_at_one(monkeypatch, tmp_path):
    """自动保存使用 JPEG，并从两位序号 01 开始。"""
    result = CaptureResult(b'fixture', 'window', 'test', 1, 1)
    writes = []

    monkeypatch.setattr(retention.winfs, 'list_names', lambda path: ())
    monkeypatch.setattr(retention.winfs, 'write_exclusive',
                        lambda path, data: writes.append((path, data)))
    runtime = retention.runtime_context(str(tmp_path), 'config.yaml')
    outcome = retention.save_automatic(
        result, runtime, 'event', retention.RetentionPolicy(True),
        today=date(2026, 10, 6))

    assert outcome.filename == 'event_01.jpg'
    assert writes == [(outcome.path, result.image_bytes)]


def test_automatic_save_scans_jpeg_and_increments_conflicts(monkeypatch, tmp_path):
    """自动保存按 JPEG 最大序号继续，并在排他冲突后递增。"""
    result = CaptureResult(b'fixture', 'window', 'test', 1, 1)
    calls = []

    monkeypatch.setattr(retention.winfs, 'list_names', lambda path: (
        'event_02.jpg', 'event_09.JPG', 'event_100.png'))

    def write(path, data):
        calls.append(path)
        if len(calls) == 1:
            raise FileExistsError(path)

    monkeypatch.setattr(retention.winfs, 'write_exclusive', write)
    runtime = retention.runtime_context(str(tmp_path), 'config.yaml')
    outcome = retention.save_automatic(
        result, runtime, 'event', retention.RetentionPolicy(True),
        today=date(2026, 10, 6))

    assert outcome.filename == 'event_11.jpg'
    assert [retention.ntpath.basename(path) for path in calls] == [
        'event_10.jpg', 'event_11.jpg']


def test_cleanup_does_not_delete_date_named_file(monkeypatch, tmp_path):
    """日期名称普通文件由安全删除层拒绝，不被清理误删。"""
    removed = []

    monkeypatch.setattr(retention.winfs, 'list_names', lambda path: ('2024_02_29',))

    def reject_file(path):
        removed.append(path)
        raise retention.winfs.UnsafeObjectError('根不是目录')

    monkeypatch.setattr(retention.winfs, 'remove_tree', reject_file)
    warnings = retention.cleanup_retention(
        str(tmp_path), retention.RetentionPolicy(True, 2), today=date(2024, 3, 2))

    assert len(removed) == 1
    assert warnings == ('截图清理失败：已跳过不安全或无法删除的日期目录',)





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


@pytest.mark.parametrize('days', [True, '2', 2.0, 0, -1])
def test_policy_rejects_non_positive_or_non_integer_days(days):
    """非法保留天数回退到默认值并告警。"""
    policy = retention.parse_policy({'enabled': True, 'max_days': days})
    assert policy.enabled is True
    assert policy.max_days == 14
    assert policy.warnings


@pytest.mark.parametrize('value, enabled, warning', [
    ({}, False, False),
    ({'enabled': None}, False, False),
    ({'enabled': False}, False, False),
    ({'enabled': True}, True, False),
    ({'enabled': 'true'}, False, True),
])
def test_policy_switch_boundaries(value, enabled, warning):
    """保留开关缺省、空值、布尔值及非法值边界。"""
    policy = retention.parse_policy(value)
    assert policy.enabled is enabled
    assert bool(policy.warnings) is warning


def test_cleanup_skips_future_and_retained_directories_without_enumerating(monkeypatch, tmp_path):
    """未来日期及保留期内日期不进入删除层。"""
    removed = []
    monkeypatch.setattr(retention.winfs, 'list_names', lambda path: (
        '2024_03_01', '2024_03_02', '2024_03_03', 'other'))
    monkeypatch.setattr(retention.winfs, 'remove_tree', lambda path: removed.append(path))
    assert retention.cleanup_retention(
        str(tmp_path), retention.RetentionPolicy(True, 2), today=date(2024, 3, 2)) == ()
    assert removed == []


def test_cleanup_continues_after_removal_failure_and_has_no_count_limit(monkeypatch, tmp_path):
    """删除失败后继续清理其余过期目录，且不限制数量。"""
    names = tuple(f'2020_01_{day:02d}' for day in range(1, 32))
    removed = []

    def remove(path):
        removed.append(path)
        if path.endswith('2020_01_01'):
            raise OSError('fixture')

    monkeypatch.setattr(retention.winfs, 'list_names', lambda path: names)
    monkeypatch.setattr(retention.winfs, 'remove_tree', remove)
    warnings = retention.cleanup_retention(
        str(tmp_path), retention.RetentionPolicy(True, 1), today=date(2024, 1, 1))
    assert len(removed) == len(names)
    assert warnings == ('截图清理失败：已跳过不安全或无法删除的日期目录',)


def test_automatic_save_does_not_trigger_retention_cleanup(monkeypatch, tmp_path):
    """自动保存不调用保留清理。"""
    result = CaptureResult(b'fixture', 'window', 'test', 1, 1)
    monkeypatch.setattr(retention.winfs, 'list_names', lambda path: ())
    monkeypatch.setattr(retention.winfs, 'write_exclusive', lambda path, data: None)
    monkeypatch.setattr(retention, 'cleanup_retention',
                        lambda *args, **kwargs: pytest.fail('cleanup called'))
    retention.save_automatic(
        result, retention.runtime_context(str(tmp_path), 'config.yaml'), 'event',
        retention.RetentionPolicy(True), today=date(2026, 10, 6))


def test_cli_sequence_exceeds_four_digits(monkeypatch, tmp_path):
    """CLI 目录序号超过四位后自然扩展。"""
    result = CaptureResult(b'fixture', 'adb', 'device', 1, 1, image_format='png')
    calls = []

    def write(path, data):
        calls.append(path)
        if len(calls) <= 10000:
            raise FileExistsError(path)

    monkeypatch.setattr(retention.winfs, 'write_exclusive', write)
    outcome = retention.save_cli(result, str(tmp_path), False, today=date(2026, 10, 7))
    assert outcome.filename == 'screenshot_20261007_10000.png'


def test_cli_save_failure_returns_last_candidate(monkeypatch, tmp_path):
    """选择候选名后写入失败时返回该候选名与固定告警。"""
    result = CaptureResult(b'fixture', 'adb', 'device', 1, 1, image_format='png')
    monkeypatch.setattr(retention.winfs, 'write_exclusive',
                        lambda path, data: (_ for _ in ()).throw(OSError('fixture')))
    outcome = retention.save_cli(result, str(tmp_path), False, today=date(2026, 10, 7))
    assert outcome.filename == 'screenshot_20261007_0000.png'
    assert outcome.path is None
    assert outcome.warnings


def test_cli_explicit_target_conflict_is_not_overwritten(monkeypatch, tmp_path):
    """显式文件目标冲突时不覆盖且返回固定失败结果。"""
    result = CaptureResult(b'fixture', 'adb', 'device', 1, 1, image_format='jpeg')
    calls = []

    def write(path, data):
        calls.append((path, data))
        raise FileExistsError(path)

    target = str(tmp_path / 'capture.jpeg')
    monkeypatch.setattr(retention.winfs, 'write_exclusive', write)
    outcome = retention.save_cli(result, target, True, today=date(2026, 10, 7))
    assert calls == [(target, result.image_bytes)]
    assert outcome.path is None
    assert outcome.filename == 'capture.jpeg'
    assert outcome.warnings


def test_cli_png_extension_replacement_conflict(monkeypatch, tmp_path):
    """PNG 替换显式后缀后发生冲突时不重试其他名称。"""
    result = CaptureResult(b'fixture', 'adb', 'device', 1, 1, image_format='png')
    calls = []
    target = str(tmp_path / 'capture.png')

    def write(path, data):
        calls.append(path)
        raise FileExistsError(path)

    monkeypatch.setattr(retention.winfs, 'write_exclusive', write)
    outcome = retention.save_cli(result, target, True, today=date(2026, 10, 7))
    assert calls == [str(tmp_path / 'capture.png')]
    assert outcome.path is None
    assert outcome.filename == 'capture.png'
    assert outcome.warnings
