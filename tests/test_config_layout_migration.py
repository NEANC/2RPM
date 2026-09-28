#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""配置布局迁移前的目标 schema 测试。"""

import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.config import COMMENTS, DEFAULT_VALUES, get_default_config


def test_default_notification_templates_survive_production_yaml_round_trip(
        tmp_path):
    """验证默认通知模板内容经生产写回后保持不变。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {
        'monitor': {'mode': 'psutil', 'psutil': {'process_name': 'ok.exe'}},
    })
    first = load_config(str(path))
    second = load_config(str(path))

    for name, template in DEFAULT_VALUES['push']['templates'].items():
        assert first['push']['templates'][name]['content'] == template['content']
        assert second['push']['templates'][name]['content'] == template['content']


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


@pytest.mark.parametrize('value', [None, '', 0, False, {'invalid': 'value'}])
def test_migration_preserves_present_new_values_and_removes_old_keys(value):
    """迁移按键存在优先保留新值，并删除冲突的旧字段。"""
    from modules.config import _migrate_config_layout

    config = {
        'monitor': {
            'mode': value, 'monitor_mode': 'psutil',
            'process_name': 'old.exe',
            'psutil': {'process_name': value},
            'timeout_interval': '2m', 'loop_interval': '3s',
            'common': {key: value for key in (
                'timeout_interval', 'loop_interval', 'timeout_threshold',
                'max_wait', 'check_interval')},
            'task_scheduler': {'task_name': value, 'lookback_minutes': value},
            'launch': {key: value for key in (
                'type', 'path', 'args', 'cwd', 'task_name')},
        },
        'external': {'timeout_threshold': 9},
        'wait': {'max_wait': '4m', 'check_interval': '5s'},
        'task': {'task_name': 'old-task', 'lookback_minutes': 7},
        'launch': {'type': 'program', 'path': 'old.exe', 'args': '--old',
                   'cwd': 'old-dir', 'task_name': 'old-launch-task'},
    }

    assert _migrate_config_layout(config)
    monitor = config['monitor']
    assert set(monitor) == {
        'mode', 'common', 'psutil', 'task_scheduler', 'launch'
    }
    assert monitor['mode'] is value
    for section in ('common', 'psutil', 'task_scheduler', 'launch'):
        assert all(item is value for item in monitor[section].values())
    assert not {'task', 'launch', 'wait'} & config.keys()
    assert 'timeout_threshold' not in config['external']


@pytest.mark.parametrize('value', [
    {'monitor': {'common': {'loop_interval': '2s'}}},
    {'monitor': {'task_scheduler': {'task_name': 'task-a'}}},
    {'task': {'task_name': 'old-task'}},
    {'wait': {'max_wait': '2m'}},
    {},
])
def test_missing_mode_uses_runnable_defaults(tmp_path, value):
    """未指定模式的旧布局和部分新布局允许补全可运行默认配置。"""
    from modules.config import load_config

    config = load_config(str(_write_config(tmp_path, value)))
    assert config['monitor']['mode'] == 'psutil'
    assert config['monitor']['psutil']['process_name'] == (
        DEFAULT_VALUES['monitor']['psutil']['process_name']
    )


@pytest.mark.parametrize('value', [None, '', 0, False, {'invalid': 'value'}])
def test_missing_mode_still_validates_final_active_target(tmp_path, value):
    """缺省模式不跳过补全后当前模式目标有效性校验。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {
        'monitor': {'psutil': {'process_name': value}},
    })
    original = path.read_bytes()
    with pytest.raises(SystemExit) as error:
        load_config(str(path))
    assert error.value.code == 1
    assert path.read_bytes() == original


@pytest.mark.parametrize('mode, section', [
    ('psutil', {}),
    ('task_scheduler', {}),
    ('launch', {}),
    ('launch', {'type': 'program'}),
    ('launch', {'type': 'task'}),
])
def test_explicit_mode_requires_explicit_target(tmp_path, mode, section):
    """显式模式不能用默认值补齐当前模式的必填目标。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {'monitor': {'mode': mode, mode: section}})
    original = path.read_bytes()
    with pytest.raises(SystemExit) as error:
        load_config(str(path))
    assert error.value.code == 1
    assert path.read_bytes() == original


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


def test_successful_migration_is_idempotent_and_second_load_does_not_write(tmp_path):
    """成功迁移后第二次加载不触发写回。"""
    from modules.config import load_config
    from unittest.mock import patch

    path = _write_config(tmp_path, {
        'monitor': {'monitor_mode': 'psutil', 'process_name': 'custom.exe'},
    })
    load_config(str(path))
    persisted = path.read_text(encoding='utf-8')
    with patch('modules.config._make_write_yaml') as write_yaml:
        load_config(str(path))
    assert path.read_text(encoding='utf-8') == persisted
    write_yaml.assert_not_called()


def test_migration_preserves_push_log_and_custom_user_values(tmp_path):
    """迁移不重置用户推送、日志和自定义值。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {
        'monitor': {'monitor_mode': 'psutil', 'process_name': 'custom.exe'},
        'push': {'templates': {'on_end': {'title': '用户标题'}},
                 'push_channel_settings': {'channels': [
                     {'provider': 'custom', 'token': 'user-token'}]}},
        'log': {'log_directory': 'user-logs', 'max_log_files': 7},
    })
    config = load_config(str(path))
    assert config['push']['templates']['on_end']['title'] == '用户标题'
    assert config['push']['push_channel_settings']['channels'][0]['token'] == 'user-token'
    assert config['log']['log_directory'] == 'user-logs'
    assert config['log']['max_log_files'] == 7


def test_write_open_failure_returns_memory_config_without_success_log(tmp_path, caplog, monkeypatch):
    """写回打开失败时返回内存配置且不记录成功写回。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {
        'monitor': {'monitor_mode': 'psutil', 'process_name': 'custom.exe'},
    })
    original_open = open

    def fail_write_open(file, mode='r', *args, **kwargs):
        if mode == 'w':
            raise OSError('write-open-failed')
        return original_open(file, mode, *args, **kwargs)

    monkeypatch.setattr('builtins.open', fail_write_open)
    config = load_config(str(path))
    assert config['monitor']['mode'] == 'psutil'
    assert '正在写回配置信息' not in caplog.text
    assert '无法写回配置文件' in caplog.text


def test_write_dump_failure_returns_memory_config_without_completion_log(tmp_path, caplog, monkeypatch):
    """写回 dump 失败时返回内存配置且不记录完成日志。"""
    from modules.config import _make_write_yaml, load_config

    path = _write_config(tmp_path, {
        'monitor': {'monitor_mode': 'psutil', 'process_name': 'custom.exe'},
    })
    yaml = _make_write_yaml()

    def fail_dump(*args, **kwargs):
        raise OSError('dump-failed')

    monkeypatch.setattr(yaml, 'dump', fail_dump)
    monkeypatch.setattr('modules.config._make_write_yaml', lambda: yaml)
    config = load_config(str(path))
    assert config['monitor']['mode'] == 'psutil'
    assert '正在写回配置信息' not in caplog.text
    assert '配置参数版本差异检查完成' not in caplog.text
    assert '无法写回配置文件' in caplog.text
