#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""截图目标身份与模板映射的纯逻辑测试。"""

import copy

import pytest

from modules.gui.target_order import delete_screenshot_target, move_screenshot_target


def _config(targets=None, content='{screenshot_1} / {screenshot_2}'):
    """构造保留非截图字段的配置草稿。"""
    return {'extra': {'hidden': 1}, 'push': {
        'screenshot': {'targets': targets or [
            {'provider': 'window', 'target': 'A'},
            {'provider': 'window', 'target': 'B'}],
            'image_host': [{'provider': 'catbox', 'token': 'secret'}]},
        'templates': {'on_end': {'title': '{{screenshot_1}} {screenshot_2}',
                                 'content': content, 'extra': 4}}}}


def test_reorder_preserves_referenced_identity():
    """移动整项目标并同时更新编号引用，不触碰转义或聚合变量。"""
    config = _config(content='{screenshot_1} / {screenshot_2} / {screenshot}')
    original = copy.deepcopy(config)
    updated = move_screenshot_target(config, 0, 1)
    assert updated['push']['screenshot']['targets'][0]['target'] == 'B'
    assert updated['push']['templates']['on_end']['content'] == (
        '{screenshot_2} / {screenshot_1} / {screenshot}')
    assert updated['push']['templates']['on_end']['title'] == '{{screenshot_1}} {screenshot_1}'
    assert config == original
    assert updated['extra'] == config['extra']
    assert updated['push']['screenshot']['image_host'] == config['push']['screenshot']['image_host']


def test_duplicate_output_blocks_reorder():
    """重复输出名的自动重命名不能猜测引用身份。"""
    config = _config([{'provider': 'window', 'target': 'A', 'out': 'same'},
                      {'provider': 'window', 'target': 'B', 'out': 'same'}])
    original = copy.deepcopy(config)
    with pytest.raises(ValueError):
        move_screenshot_target(config, 0, 1)
    assert config == original


def test_delete_does_not_retarget_reference():
    """删首项后只移除其精确占位符，存活目标重编号。"""
    config = _config(content='{screenshot_1} / {screenshot_2} / {screenshot}')
    updated = delete_screenshot_target(config, 0)
    assert updated['push']['templates']['on_end']['content'] == (
        ' / {screenshot_1} / {screenshot}')
    assert updated['push']['templates']['on_end']['title'] == '{{screenshot_1}} {screenshot_1}'
    assert updated['push']['screenshot']['targets'][0]['target'] == 'B'
    assert config['push']['screenshot']['targets'][0]['target'] == 'A'


def test_delete_middle_target_maps_survivors_and_custom_out():
    """自定义输出名随整项移动，后继位置编号同步更新。"""
    config = _config([{'provider': 'window', 'target': 'A', 'out': 'custom'},
                      {'provider': 'window', 'target': 'B'},
                      {'provider': 'window', 'target': 'C'}],
                     '{custom} {screenshot_2} {screenshot_3}')
    updated = delete_screenshot_target(config, 1)
    assert updated['push']['templates']['on_end']['content'] == (
        '{custom}  {screenshot_2}')
    assert updated['push']['screenshot']['targets'][0]['out'] == 'custom'


def test_unknown_numbered_reference_blocks_mutation():
    """悬空编号未来可能被新目标占用，禁止静默保留。"""
    config = _config(content='{screenshot_9}')
    with pytest.raises(ValueError):
        delete_screenshot_target(config, 0)


def test_invalid_mapping_blocks_without_partial_changes():
    """非法输出名和字段访问语法无法判定归属时拒绝事务。"""
    config = _config([{'provider': 'window', 'target': 'A', 'out': 'bad-name'},
                      {'provider': 'window', 'target': 'B'}])
    original = copy.deepcopy(config)
    with pytest.raises(ValueError):
        delete_screenshot_target(config, 0)
    assert config == original
    config = _config(content='{screenshot_1.attr}')
    with pytest.raises(ValueError):
        move_screenshot_target(config, 0, 1)


@pytest.mark.parametrize('mutate', [
    lambda config: config.update({'push': None}),
    lambda config: config['push'].update({'templates': 'bad'}),
    lambda config: config['push']['templates'].update({'on_end': 'bad'}),
])
def test_non_mapping_structure_raises_value_error(mutate):
    """push 或模板结构非映射时统一拒绝，不抛其他异常类型。"""
    config = _config()
    mutate(config)
    with pytest.raises(ValueError, match='消息模板结构无效'):
        move_screenshot_target(config, 0, 1)
    with pytest.raises(ValueError, match='消息模板结构无效'):
        delete_screenshot_target(config, 0)


def test_missing_templates_allows_target_reorder():
    """未配置 templates 等同于空模板，不影响目标重排。"""
    config = _config()
    config['push'].pop('templates')
    updated = move_screenshot_target(config, 0, 1)
    assert updated['push']['screenshot']['targets'][0]['target'] == 'B'
