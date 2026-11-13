#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""配置纯诊断和候选变换的行为测试。"""

import builtins
import copy
import socket
import sys
import urllib.request

import pytest

from modules.config import get_default_config, load_config
from modules.config_rules import analyze_config, layout_issues


@pytest.mark.parametrize('source, expected_path', [
    ({'monitor': {'target': 'ok.exe'}}, None),
    ({}, ('monitor',)),
    ([], ()),
    ({'monitor': {'process_name': 'old.exe'}}, ('monitor', 'process_name')),
    ({'launch': {'type': 'program'}}, ('launch',)),
    ({'monitor': {'target': 'ok.exe', 'mode': 'wrong'}}, ('monitor', 'mode')),
    ({'monitor': {'target': ''}}, ('monitor', 'target')),
    ({'monitor': {'target': 'ok.exe', 'mode': 'launch'}}, ('monitor', 'target')),
])
def test_structure_diagnostics_are_pure(tmp_path, monkeypatch, source, expected_path):
    """结构分析不会输入、退出、写文件或调用网络。"""
    def forbidden(*args, **kwargs):
        """拒绝任何交互或 IO。"""
        raise AssertionError('配置分析发生副作用')

    before = copy.deepcopy(source)
    monkeypatch.setattr(builtins, 'input', forbidden)
    monkeypatch.setattr(sys, 'exit', forbidden)
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr('os.replace', forbidden)
    monkeypatch.setattr('tempfile.mkstemp', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    candidate = analyze_config(source, config_dir=tmp_path,
                               defaults=get_default_config())
    assert source == before
    if expected_path is None:
        assert not any(issue.severity == 'error' for issue in candidate.issues)
    else:
        assert any(issue.path == expected_path and issue.severity == 'error'
                   for issue in candidate.issues)


@pytest.mark.parametrize('raw, field, expected', [
    (True, 'max_wait', '1'),
    (-5, 'max_wait', '5'),
    (float('nan'), 'max_wait', '30s'),
    (False, 'timeout_threshold', 3),
    (-3, 'timeout_threshold', 3),
])
def test_common_value_candidate(tmp_path, raw, field, expected):
    """候选独立规范化异常时间及阈值。"""
    source = {'monitor': {'target': 'ok.exe', 'common': {field: raw}}}
    candidate = analyze_config(source, config_dir=tmp_path,
                               defaults=get_default_config())
    assert candidate.tree['monitor']['common'][field] == expected
    assert source['monitor']['common'][field] == raw or (
        isinstance(raw, float) and source['monitor']['common'][field] is raw)


def test_time_change_keeps_original_value_after_source_edit(tmp_path):
    """时间字段差异中的原值不随源配置后续编辑变化。"""
    raw = ['invalid']
    source = {'monitor': {'target': 'ok.exe', 'common': {'max_wait': raw}}}
    candidate = analyze_config(source, config_dir=tmp_path,
                               defaults=get_default_config())
    change = next(change for change in candidate.changes
                  if change.path == ('monitor', 'common', 'max_wait'))

    raw.append('later')

    assert change.before == ['invalid']
    assert change.after == '30s'
    assert candidate.tree['monitor']['common']['max_wait'] == '30s'


@pytest.mark.parametrize('raw, expected', [
    ('private-time-98765', '30s'),
    ('45s', '45s'),
])
def test_analyze_config_time_validation_does_not_log_values(tmp_path, caplog,
                                                            raw, expected):
    """配置候选的时间校验不将输入原值写入日志。"""
    source = {'monitor': {'target': 'ok.exe', 'common': {'max_wait': raw}}}

    candidate = analyze_config(source, config_dir=tmp_path,
                               defaults=get_default_config())

    assert candidate.tree['monitor']['common']['max_wait'] == expected
    assert raw not in caplog.text


def test_candidate_defaults_cleanup_and_secret(tmp_path):
    """候选补缺、删除未知项且不展开令牌。"""
    source = {'monitor': {'target': 'ok.exe', 'watch': {'lookback_minutes': -10}},
              'unknown': 'value', 'push': {'screenshot': {'image_host': [
                  {'provider': 'catbox', 'token': '${TOKEN}'}]}}}
    before = copy.deepcopy(source)
    candidate = analyze_config(source, config_dir=tmp_path,
                               defaults=get_default_config())
    assert source == before
    assert candidate.tree['monitor']['common']['max_wait'] == '30s'
    assert candidate.tree['monitor']['common']['timeout_interval'] == '15m'
    assert candidate.tree['monitor']['watch']['lookback_minutes'] == 10
    assert any(change.operation == 'delete' for change in candidate.changes)
    assert candidate.tree['push']['screenshot']['image_host'][0]['token'] == '${TOKEN}'
    assert '${TOKEN}' not in repr(candidate.issues)


def test_candidate_matches_cli_for_valid_config(tmp_path):
    """候选与既有 CLI 对同一有效输入生成同等配置。"""
    from ruamel.yaml import YAML

    source = {'monitor': {'target': 'ok.exe', 'common': {'max_wait': '-5s'}}}
    path = tmp_path / 'valid.yaml'
    with path.open('w', encoding='utf-8') as stream:
        YAML().dump(source, stream)
    candidate = analyze_config(source, config_dir=tmp_path,
                               defaults=get_default_config())
    assert candidate.tree == load_config(str(path))


def test_cli_reuses_pure_layout_issues(tmp_path, monkeypatch):
    """CLI 的拒绝路径由共享纯规则提供。"""
    from modules import config

    path = tmp_path / 'invalid.yaml'
    path.write_text('monitor:\n  target: ok.exe\n', encoding='utf-8')
    original = layout_issues
    seen = []

    def inspect(tree, config_dir, **kwargs):
        """记录 CLI 对共享布局检查的调用。"""
        seen.append((tree, config_dir))
        return original(tree, config_dir, **kwargs)

    monkeypatch.setattr(config, 'layout_issues', inspect, raising=False)
    load_config(str(path))
    assert seen and seen[0][0]['monitor']['target'] == 'ok.exe'


@pytest.mark.parametrize('section, field, raw', [
    ('common', 'max_wait', '-30s'),
    ('common', 'max_wait', 0),
    ('common', 'max_wait', 'invalid'),
    ('common', 'max_wait', False),
    ('common', 'timeout_threshold', '003'),
    ('watch', 'lookback_minutes', '-10'),
    ('watch', 'lookback_minutes', ' 10 '),
    ('watch', 'lookback_minutes', 2.5),
])
def test_candidate_matches_cli_value_normalization(tmp_path, section, field, raw):
    """常用输入的规范化候选与 CLI 结果完全一致。"""
    from ruamel.yaml import YAML

    source = {'monitor': {'target': 'ok.exe', section: {field: raw}}}
    path = tmp_path / 'values.yaml'
    with path.open('w', encoding='utf-8') as stream:
        YAML().dump(source, stream)
    candidate = analyze_config(source, config_dir=tmp_path,
                               defaults=get_default_config())
    assert candidate.tree == load_config(str(path))


@pytest.mark.parametrize('section, field, raw', [
    ('monitor.common', 'timeout_interval', '-5m'),
    ('monitor.common', 'loop_interval', False),
    ('monitor.common', 'max_wait', float('nan')),
    ('monitor.common', 'check_interval', -2.5),
    ('monitor.common', 'timeout_threshold', True),
    ('monitor.watch', 'lookback_minutes', '-10'),
    ('push.retry', 'interval', '-3s'),
    ('push.retry', 'max_count', float('inf')),
    ('log', 'max_log_files', -15),
    ('log', 'retention_days', ''),
])
def test_all_numeric_fields_share_cli_candidate_rules(tmp_path, monkeypatch,
                                                       section, field, raw):
    """全部可写数值字段均通过同一纯函数处理且与 CLI 结果一致。"""
    from ruamel.yaml import YAML

    from modules import config
    from modules import config_rules

    parts = section.split('.')
    source = {'monitor': {'target': 'ok.exe'}}
    group = source
    for part in parts:
        group = group.setdefault(part, {})
    group[field] = raw
    path = tmp_path / 'shared.yaml'
    with path.open('w', encoding='utf-8') as stream:
        YAML().dump(source, stream)

    original = config_rules.normalize_numeric_value
    calls = []

    def inspect(value, default, kind):
        """记录候选和 CLI 使用同一入口的调用。"""
        calls.append((value, kind))
        return original(value, default, kind)

    monkeypatch.setattr(config_rules, 'normalize_numeric_value', inspect)
    candidate = config_rules.analyze_config(source, config_dir=tmp_path,
                                            defaults=config.get_default_config())
    candidate_calls = len(calls)
    loaded = config.load_config(str(path))
    assert candidate_calls > 0
    assert len(calls) > candidate_calls
    assert candidate.tree == loaded


@pytest.mark.parametrize('source, path', [
    ({'monitor': {'target': 'ok.exe'}, 'push': []}, ('push',)),
    ({'monitor': {'target': 'ok.exe'}, 'log': []}, ('log',)),
    ({'monitor': {'target': 'ok.exe'}, 'push': {'retry': []}},
     ('push', 'retry')),
    ({'monitor': {'target': 'ok.exe'}, 'push': {'screenshot': []}},
     ('push', 'screenshot')),
])
def test_invalid_shared_sections_are_diagnosed(tmp_path, source, path):
    """编辑器对非映射的共享节点给出问题而不发生异常。"""
    candidate = analyze_config(source, config_dir=tmp_path,
                               defaults=get_default_config())
    assert any(issue.path == path for issue in candidate.issues)


def test_cli_keeps_non_monitor_layout_handling(tmp_path, monkeypatch):
    """CLI 保留其既有非监视节判定，仅 GUI 添加纯诊断。"""
    from modules import config

    path = tmp_path / 'legacy.yaml'
    path.write_text('monitor:\n  target: ok.exe\npush: []\n', encoding='utf-8')
    seen = []
    original = layout_issues

    def inspect(tree, config_dir, **kwargs):
        """记录 CLI 对纯校验的策略。"""
        seen.append(kwargs)
        return original(tree, config_dir, **kwargs)

    monkeypatch.setattr(config, 'layout_issues', inspect)
    loaded = load_config(str(path))
    assert loaded['push'] == []
    assert seen and seen[0]['strict_shared'] is False


@pytest.mark.parametrize('path', [
    ('monitor', 'launch', 'args'),
    ('monitor', 'launch', 'cwd'),
    ('push', 'screenshot', 'retention', 'enabled'),
])
def test_optional_defaults_do_not_claim_cli_write(tmp_path, path):
    """缺失的可选空值不会被 CLI 补写，候选也不应提示变更。"""
    from ruamel.yaml import YAML

    source = get_default_config()
    section = source
    for key in path[:-1]:
        section = section[key]
    del section[path[-1]]
    config_path = tmp_path / 'optional.yaml'
    with config_path.open('w', encoding='utf-8') as stream:
        YAML().dump(source, stream)
    before = config_path.read_bytes()

    candidate = analyze_config(source, config_dir=tmp_path,
                               defaults=get_default_config())
    assert candidate.tree == load_config(str(config_path))
    assert config_path.read_bytes() == before
    assert not any(change.path == path for change in candidate.changes)


def test_screenshot_style_change_is_reported(tmp_path):
    """截图列表仅格式变化时仍报告 CLI 会写回的字段。"""
    from ruamel.yaml import YAML

    source = get_default_config()
    source['push']['screenshot']['image_host'] = [
        {'provider': 'catbox', 'token': '${TOKEN}'}]
    path = tmp_path / 'screenshot.yaml'
    with path.open('w', encoding='utf-8') as stream:
        YAML().dump(source, stream)
    before = path.read_bytes()

    candidate = analyze_config(source, config_dir=tmp_path,
                               defaults=get_default_config())
    loaded = load_config(str(path))
    assert candidate.tree == loaded
    assert path.read_bytes() != before
    assert any(change.path == ('push', 'screenshot', 'image_host')
               for change in candidate.changes)


@pytest.mark.parametrize('section, field', [
    ('monitor.common', 'timeout_threshold'),
    ('push.retry', 'max_count'),
    ('log', 'max_log_files'),
])
def test_integral_float_cli_matches_candidate_type(tmp_path, section, field):
    """整数配置的浮点写法被 CLI 与候选一致地写成整数。"""
    from ruamel.yaml import YAML

    source = {'monitor': {'target': 'ok.exe'}}
    group = source
    for key in section.split('.'):
        group = group.setdefault(key, {})
    group[field] = 2.0
    path = tmp_path / 'integer.yaml'
    with path.open('w', encoding='utf-8') as stream:
        YAML().dump(source, stream)

    candidate = analyze_config(source, config_dir=tmp_path,
                               defaults=get_default_config())
    loaded = load_config(str(path))
    result = loaded
    for key in section.split('.'):
        result = result[key]
    assert type(result[field]) is int
    assert candidate.tree == loaded
    assert any(change.path == (*section.split('.'), field)
               for change in candidate.changes)
