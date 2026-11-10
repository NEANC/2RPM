#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""统一监视目标布局的配置加载与写回测试。"""

import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.config import COMMENTS, DEFAULT_VALUES, get_default_config

# 合法新配置的最小监视节
VALID_MONITOR = {'monitor': {'target': 'ok.exe'}}


def test_default_notification_templates_survive_production_yaml_round_trip(
        tmp_path):
    """验证默认通知模板内容经生产写回后保持不变。"""
    from modules.config import load_config

    path = _write_config(tmp_path, VALID_MONITOR)
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
        'mode', 'target', 'common', 'launch', 'watch'
    }
    assert set(DEFAULT_VALUES['monitor']['common']) == {
        'timeout_interval', 'loop_interval', 'timeout_threshold',
        'max_wait', 'check_interval'
    }
    assert set(DEFAULT_VALUES['monitor']['launch']) == {'args', 'cwd'}
    assert set(DEFAULT_VALUES['monitor']['watch']) == {'lookback_minutes'}
    assert 'timeout_threshold' not in DEFAULT_VALUES['external']
    assert 'wait' not in DEFAULT_VALUES
    assert 'task' not in DEFAULT_VALUES
    assert 'launch' not in DEFAULT_VALUES


def test_comments_match_monitor_schema():
    """验证默认配置注释与新的 monitor 布局一致。"""
    monitor_comments = COMMENTS['monitor']
    assert set(monitor_comments) == {
        '_comment', 'mode', 'target', 'common', 'launch', 'watch'
    }
    assert set(monitor_comments['common']) == {
        '_comment', 'timeout_interval', 'loop_interval',
        'timeout_threshold', 'max_wait', 'check_interval'
    }
    assert set(monitor_comments['launch']) == {'_comment', 'args', 'cwd'}
    assert set(monitor_comments['watch']) == {
        '_comment', 'lookback_minutes'}
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


@pytest.mark.parametrize('value', [
    {'monitor': {'monitor_mode': 'task_scheduler'}},
    {'monitor': {'process_name': 'old.exe'}},
    {'monitor': {'timeout_interval': '2m'}},
    {'monitor': {'loop_interval': '0s'}},
    {'monitor': {'psutil': {'process_name': 'old.exe'}}},
    {'monitor': {'task_scheduler': {'task_name': 'old-task'}}},
    {'monitor': {'launch': {'type': 'task'}}},
    {'monitor': {'launch': {'path': 'C:\\old.exe'}}},
    {'monitor': {'launch': {'task_name': 'launch-task'}}},
    {'task': {'task_name': 'old-task'}},
    {'launch': {'type': 'program'}},
    {'wait': {'max_wait': '0s'}},
    {'external': {'timeout_threshold': 9}},
], ids=[
    'monitor_mode', 'process_name', 'timeout_interval', 'loop_interval',
    'psutil', 'task_scheduler', 'launch_type', 'launch_path',
    'launch_task_name', 'top_task', 'top_launch', 'top_wait',
    'external_threshold',
])
def test_legacy_layout_is_rejected_and_bytes_preserved(tmp_path, value):
    """旧监视布局任一字段存在时拒绝加载且原文件字节不变。"""
    from modules.config import load_config

    path = _write_config(tmp_path, value)
    original = path.read_bytes()
    with pytest.raises(SystemExit) as error:
        load_config(str(path))
    assert error.value.code == 1
    assert path.read_bytes() == original


def test_new_and_legacy_fields_mixed_are_rejected(tmp_path):
    """新旧字段混用时拒绝加载且不写回。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {
        'monitor': {
            'mode': 'watch',
            'target': 'ok.exe',
            'watch': {'lookback_minutes': 5},
            'psutil': {'process_name': 'ok.exe'},
        },
    })
    original = path.read_bytes()
    with pytest.raises(SystemExit) as error:
        load_config(str(path))
    assert error.value.code == 1
    assert path.read_bytes() == original


@pytest.mark.parametrize('target', [None, '', '   ', 0, False], ids=[
    'none', 'empty_str', 'blank_str', 'zero', 'false'])
def test_invalid_explicit_target_is_rejected(tmp_path, target):
    """显式 target 非法时拒绝加载且不写回。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {'monitor': {'target': target}})
    original = path.read_bytes()
    with pytest.raises(SystemExit) as error:
        load_config(str(path))
    assert error.value.code == 1
    assert path.read_bytes() == original


@pytest.mark.parametrize('value', [
    {'monitor': {'common': 'invalid'}},
    {'monitor': {}, 'external': 'invalid'},
], ids=['common_not_mapping', 'external_not_mapping'])
def test_invalid_structure_does_not_write(tmp_path, value):
    """新节点或 external 结构错误不写回。"""
    from modules.config import load_config

    path = _write_config(tmp_path, value)
    original = path.read_text(encoding='utf-8')
    with pytest.raises(SystemExit) as error:
        load_config(str(path))
    assert error.value.code == 1
    assert path.read_text(encoding='utf-8') == original


def test_minimal_new_layout_is_completed_with_defaults(tmp_path):
    """最小新布局加载后补齐默认值，且第二次加载不写回。"""
    from modules.config import load_config
    from unittest.mock import patch

    path = _write_config(tmp_path, {
        'monitor': {'target': 'custom.exe'},
    })
    config = load_config(str(path))
    assert config['monitor']['mode'] == 'watch'
    assert config['monitor']['target'] == 'custom.exe'
    assert config['monitor']['common']['timeout_interval'] == '15m'
    assert config['monitor']['watch']['lookback_minutes'] == 10

    persisted = path.read_text(encoding='utf-8')
    with patch('modules.config._make_write_yaml') as write_yaml:
        load_config(str(path))
    assert path.read_text(encoding='utf-8') == persisted
    write_yaml.assert_not_called()


def test_inactive_launch_fields_are_retained(tmp_path):
    """非当前模式的合法 launch 字段与 watch 值保持原样。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {
        'monitor': {
            'mode': 'watch',
            'target': 'ok.exe',
            'launch': {'args': '--flag', 'cwd': None},
            'watch': {'lookback_minutes': 4},
        },
    })
    config = load_config(str(path))
    assert config['monitor']['launch']['args'] == '--flag'
    assert config['monitor']['launch']['cwd'] is None
    assert config['monitor']['watch']['lookback_minutes'] == 4


@pytest.mark.parametrize('value', [
    {'monitor': {'mode': 'invalid'}},
    {'monitor': {'target': 'ok.exe', 'launch': {'args': 123}}},
    {'monitor': {'target': 'ok.exe', 'watch': '10'}},
    {'monitor': {'target': 'ok.exe', 'common': []}},
], ids=['invalid_mode', 'args_int', 'watch_str', 'common_list'])
def test_invalid_mode_or_section_does_not_write(tmp_path, value):
    """mode 或子节类型错误时不写回。"""
    from modules.config import load_config

    path = _write_config(tmp_path, value)
    original = path.read_text(encoding='utf-8')
    with pytest.raises(SystemExit) as error:
        load_config(str(path))
    assert error.value.code == 1
    assert path.read_text(encoding='utf-8') == original


def test_legacy_rejection_logs_path_without_values(tmp_path, caplog):
    """旧布局拒绝日志只包含路径，不泄漏字段值。"""
    from modules.config import load_config

    secret = 'LEGACY_SECRET_VALUE'
    path = _write_config(tmp_path, {
        'monitor': {
            'target': 'ok.exe',
            'psutil': {'process_name': secret},
        },
    })
    original = path.read_text(encoding='utf-8')
    with pytest.raises(SystemExit) as error:
        load_config(str(path))

    assert error.value.code == 1
    assert path.read_text(encoding='utf-8') == original
    assert 'monitor.psutil' in caplog.text
    assert secret not in caplog.text


def test_migration_preserves_push_log_and_custom_user_values(tmp_path):
    """新布局加载不重置用户推送、日志和自定义值。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {
        'monitor': {'target': 'custom.exe'},
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
        'monitor': {'target': 'custom.exe'},
    })
    def fail_write_temp(*args, **kwargs):
        raise OSError('write-open-failed')

    monkeypatch.setattr('modules.config.tempfile.mkstemp', fail_write_temp)
    config = load_config(str(path))
    assert config['monitor']['mode'] == 'watch'
    assert config['monitor']['target'] == 'custom.exe'
    assert '正在写回配置信息' not in caplog.text
    assert '无法写回配置文件' in caplog.text


@pytest.mark.parametrize('failure_stage', [
    'fdopen', 'fdopen_close_cleanup', 'replace', 'replace_unlink_cleanup',
])
def test_atomic_write_failure_cleans_temp_and_preserves_original_error(
        tmp_path, caplog, monkeypatch, failure_stage):
    """真实临时文件的写回失败保留原字节、内存配置和原始错误。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {
        'monitor': {'target': 'custom.exe'},
    })
    original = path.read_bytes()
    original_close = os.close
    original_unlink = os.unlink
    opened_fds = []
    closed_fds = []
    error_message = f'{failure_stage}-original-secret'
    primary_error = OSError(28, error_message, 'private-filename')
    primary_error.winerror = 112
    cleanup_error = OSError(5, 'cleanup-secret')
    cleanup_error.winerror = 1117

    def fail_fdopen(fd, *args, **kwargs):
        """记录真实描述符并模拟包装失败。"""
        opened_fds.append(fd)
        raise primary_error

    def close_fd(fd):
        """关闭真实描述符，并按场景报告清理异常。"""
        original_close(fd)
        closed_fds.append(fd)
        if failure_stage == 'fdopen_close_cleanup':
            raise cleanup_error

    def fail_replace(source, destination):
        """确认真实临时文件已写入后模拟原子替换失败。"""
        assert os.path.getsize(source) > 0
        assert os.path.abspath(destination) == str(path)
        raise primary_error

    def unlink_temp(temp_path):
        """删除真实临时文件，并按场景报告清理异常。"""
        original_unlink(temp_path)
        if failure_stage == 'replace_unlink_cleanup':
            raise cleanup_error

    caplog.set_level('INFO', logger='modules.config')
    monkeypatch.setattr('modules.config.os.close', close_fd)
    monkeypatch.setattr('modules.config.os.unlink', unlink_temp)
    if failure_stage.startswith('fdopen'):
        monkeypatch.setattr('modules.config.os.fdopen', fail_fdopen)
    else:
        monkeypatch.setattr('modules.config.os.replace', fail_replace)

    try:
        config = load_config(str(path))
        assert config['monitor']['mode'] == 'watch'
        assert config['monitor']['target'] == 'custom.exe'
        assert 'monitor_mode' not in config['monitor']
        assert path.read_bytes() == original
        assert closed_fds == opened_fds
        assert list(tmp_path.iterdir()) == [path]
        assert '无法写回配置文件' in caplog.text
        stage = 'fdopen' if failure_stage.startswith('fdopen') else 'replace'
        assert f'stage={stage}' in caplog.text
        assert 'type=OSError' in caplog.text
        assert 'errno=28' in caplog.text
        assert 'winerror=112' in caplog.text
        assert 'category=ENOSPC' in caplog.text
        assert 'errno=5' not in caplog.text
        assert 'winerror=1117' not in caplog.text
        assert error_message not in caplog.text
        assert 'private-filename' not in caplog.text
        assert 'cleanup-secret' not in caplog.text
        assert '正在写回配置信息' not in caplog.text
        assert '配置参数版本差异检查完成' not in caplog.text
    finally:
        # 红灯阶段也释放真实资源，避免测试自身遗留句柄和文件。
        for fd in opened_fds:
            if fd not in closed_fds:
                original_close(fd)
        for temp_path in tmp_path.glob('*.tmp'):
            original_unlink(temp_path)


def test_write_dump_failure_returns_memory_config_without_completion_log(tmp_path, caplog, monkeypatch):
    """写回 dump 失败时返回内存配置且不记录完成日志。"""
    from modules.config import _make_write_yaml, load_config

    path = _write_config(tmp_path, {
        'monitor': {'target': 'custom.exe'},
    })
    original = path.read_bytes()
    yaml = _make_write_yaml()

    def fail_dump(*args, **kwargs):
        raise OSError('dump-failed')

    monkeypatch.setattr(yaml, 'dump', fail_dump)
    monkeypatch.setattr('modules.config._make_write_yaml', lambda: yaml)
    config = load_config(str(path))
    assert config['monitor']['mode'] == 'watch'
    assert path.read_bytes() == original
    assert '正在写回配置信息' not in caplog.text
    assert '配置参数版本差异检查完成' not in caplog.text
    assert '无法写回配置文件' in caplog.text


def test_partial_dump_failure_preserves_original_bytes(tmp_path, caplog, monkeypatch):
    """写回部分内容后 dump 失败时保留原配置字节。"""
    from modules.config import _make_write_yaml, load_config

    path = _write_config(tmp_path, {
        'monitor': {'target': 'custom.exe'},
    })
    original = path.read_bytes()
    yaml = _make_write_yaml()

    def partial_dump(config, stream):
        stream.write('partial: true\n')
        raise OSError('partial-dump-failed')

    monkeypatch.setattr(yaml, 'dump', partial_dump)
    monkeypatch.setattr('modules.config._make_write_yaml', lambda: yaml)
    config = load_config(str(path))

    assert config['monitor']['mode'] == 'watch'
    assert path.read_bytes() == original
    assert '无法写回配置文件' in caplog.text


def test_write_close_failure_preserves_original_bytes(tmp_path, caplog, monkeypatch):
    """临时文件关闭失败时保留原配置字节。"""
    from modules.config import load_config

    path = _write_config(tmp_path, {
        'monitor': {'target': 'custom.exe'},
    })
    original = path.read_bytes()
    original_fdopen = os.fdopen
    streams = []

    class CloseFailingStream:
        """转发真实流接口，仅在关闭资源后注入关闭异常。"""

        def __init__(self, stream):
            """保存真实流以验证资源释放。"""
            self._stream = stream

        def __getattr__(self, name):
            """转发序列化所需属性和关闭状态。"""
            return getattr(self._stream, name)

        def __enter__(self):
            """兼容上下文管理器调用。"""
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            """退出上下文时执行相同的关闭故障。"""
            self.close()

        def close(self):
            """释放真实资源后抛出合成关闭异常。"""
            self._stream.close()
            raise OSError('close-failed')

        def write(self, value):
            """向真实临时流写入序列化内容。"""
            return self._stream.write(value)

    def fdopen(*args, **kwargs):
        """记录包装流以检查底层资源已经关闭。"""
        stream = CloseFailingStream(original_fdopen(*args, **kwargs))
        streams.append(stream)
        return stream

    monkeypatch.setattr('modules.config.os.fdopen', fdopen)
    config = load_config(str(path))

    assert config['monitor']['mode'] == 'watch'
    assert path.read_bytes() == original
    assert '无法写回配置文件' in caplog.text
    assert 'stage=close type=OSError' in caplog.text
    assert 'AttributeError' not in caplog.text
    assert len(streams) == 1
    assert all(stream.closed for stream in streams)
    assert list(tmp_path.iterdir()) == [path]
