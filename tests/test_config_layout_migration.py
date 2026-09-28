#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""配置布局迁移前的目标 schema 测试。"""

import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.config import COMMENTS, DEFAULT_VALUES, get_default_config


def test_default_config_has_independent_nested_mutable_nodes():
    """验证 get_default_config 每次返回独立的嵌套可变节点。"""
    first = get_default_config()
    second = get_default_config()

    first['monitor']['common']['timeout_interval'] = '1h'
    first['monitor']['launch']['args'] = ['--changed']
    first['push']['templates']['on_end']['enable'] = False
    first['push']['push_channel_settings']['channels'].append({'provider': 'test'})

    assert second['monitor']['common']['timeout_interval'] == '15m'
    assert second['monitor']['launch']['args'] is None
    assert second['push']['templates']['on_end']['enable'] is True
    assert len(second['push']['push_channel_settings']['channels']) == 5


def test_monitor_schema_is_nested_and_external_threshold_is_removed():
    """验证 monitor 使用独立可变节点且超时阈值归入 common。"""
    assert set(DEFAULT_VALUES['monitor']) == {
        'mode', 'common', 'psutil', 'task_scheduler', 'launch'
    }
    assert set(DEFAULT_VALUES['monitor']['common']) == {
        'timeout_interval', 'loop_interval', 'timeout_threshold',
        'max_wait', 'check_interval'
    }
    assert 'timeout_threshold' not in DEFAULT_VALUES['external']
    assert 'wait' not in DEFAULT_VALUES
    assert 'task' not in DEFAULT_VALUES
    assert 'launch' not in DEFAULT_VALUES


def test_comments_match_monitor_schema():
    """验证默认配置注释与新的 monitor 布局一致。"""
    monitor_comments = COMMENTS['monitor']
    assert set(monitor_comments) == {
        '_comment', 'mode', 'common', 'psutil', 'task_scheduler', 'launch'
    }
    assert set(monitor_comments['common']) == {
        '_comment', 'timeout_interval', 'loop_interval',
        'timeout_threshold', 'max_wait', 'check_interval'
    }
    assert 'timeout_threshold' not in COMMENTS['external']
    assert 'wait' not in COMMENTS
    assert 'task' not in COMMENTS


def _write_config(tmp_path, value):
    """写入临时 YAML 配置。"""
    from ruamel.yaml import YAML

    path = tmp_path / 'config.yaml'
    with path.open('w', encoding='utf-8') as stream:
        YAML().dump(value, stream)
    return path


def test_old_layout_migrates_and_preserves_external_actions(tmp_path):
    """旧布局完整迁移且保留 external 动作。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {
        'monitor': {'monitor_mode': 'task_scheduler', 'timeout_interval': '2m',
                    'loop_interval': '0s', 'process_name': 'old.exe'},
        'task': {'task_name': 'old-task', 'lookback_minutes': 0},
        'launch': {'type': 'task', 'task_name': 'launch-task'},
        'wait': {'max_wait': '0s', 'check_interval': '2s'},
        'external': {'timeout_threshold': 0, 'on_end': 'end',
                     'on_timeout': 'timeout', 'on_wait_timeout': 'wait'},
    })
    config = load_config(str(path))
    assert config['monitor']['mode'] == 'task_scheduler'
    assert config['monitor']['common']['timeout_interval'] == '2m'
    assert config['monitor']['common']['timeout_threshold'] == 0
    assert config['monitor']['task_scheduler']['lookback_minutes'] == 0
    assert config['monitor']['launch']['task_name'] == 'launch-task'
    assert config['external']['on_end'] == 'end'
    assert 'task' not in config and 'wait' not in config


def test_new_values_win_by_key_presence(tmp_path):
    """新字段即使为空也优先于旧字段。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {
        'monitor': {'mode': 'psutil', 'common': {'timeout_threshold': 0},
                    'psutil': {'process_name': None}},
        'external': {'timeout_threshold': 9},
    })
    original = path.read_text(encoding='utf-8')
    with pytest.raises(SystemExit) as error:
        load_config(str(path))
    assert error.value.code == 1
    assert path.read_text(encoding='utf-8') == original


def test_invalid_structure_and_external_do_not_write(tmp_path):
    """新节点或 external 结构错误不写回。"""
    from modules.config import load_config

    for value in ({'monitor': {'common': 'invalid'}},
                  {'monitor': {}, 'external': 'invalid'}):
        path = _write_config(tmp_path, value)
        original = path.read_text(encoding='utf-8')
        with pytest.raises(SystemExit) as error:
            load_config(str(path))
        assert error.value.code == 1
        assert path.read_text(encoding='utf-8') == original
        path.unlink()


def test_non_current_mode_invalid_values_are_retained(tmp_path):
    """非当前模式的非法目标保留。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {
        'monitor': {'mode': 'psutil', 'psutil': {'process_name': 'ok.exe'},
                    'task_scheduler': {'task_name': None},
                    'launch': {'type': None, 'path': None, 'task_name': None}},
    })
    config = load_config(str(path))
    assert config['monitor']['task_scheduler']['task_name'] is None
    assert config['monitor']['launch']['type'] is None


def test_invalid_mode_and_active_required_field_do_not_write(tmp_path):
    """mode 或当前模式必填字段错误时不写回。"""
    from modules.config import load_config

    cases = [
        {'monitor': {'mode': 'invalid'}},
        {'monitor': {'mode': 'psutil', 'psutil': {'process_name': ''}}},
        {'monitor': {'mode': 'task_scheduler',
                     'task_scheduler': {'task_name': ''}}},
        {'monitor': {'mode': 'launch', 'launch': {'type': 'program', 'path': ''}}},
        {'monitor': {'mode': 'launch', 'launch': {'type': 'task', 'task_name': ''}}},
    ]
    for value in cases:
        path = _write_config(tmp_path, value)
        original = path.read_text(encoding='utf-8')
        with pytest.raises(SystemExit) as error:
            load_config(str(path))
        assert error.value.code == 1
        assert path.read_text(encoding='utf-8') == original
        path.unlink()


def test_migration_is_idempotent_and_logs_conflict_without_values(tmp_path, caplog):
    """迁移可幂等，冲突日志只包含路径。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {
        'monitor': {'mode': 'psutil', 'process_name': 'old.exe',
                    'common': {'timeout_threshold': 0}},
        'external': {'timeout_threshold': 9},
    })
    first = load_config(str(path))
    persisted = path.read_text(encoding='utf-8')
    second = load_config(str(path))
    assert first == second
    assert path.read_text(encoding='utf-8') == persisted
    assert 'monitor.common.timeout_threshold' in caplog.text
    assert 'external.timeout_threshold' in caplog.text
    assert 'external.timeout_threshold: 9' not in caplog.text
