#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""配置布局迁移前的目标 schema 测试。"""

import os
import sys

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
    assert 'launch' not in COMMENTS
