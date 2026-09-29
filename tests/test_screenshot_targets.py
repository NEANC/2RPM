#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证截图结果模型、目标解析及输出变量分配。"""

from collections import UserDict
from copy import deepcopy
from dataclasses import FrozenInstanceError
from importlib import import_module

import pytest


def allocate(raw_targets=None, reserved_names=()):
    """调用实际解析接口，让缺失实现表现为测试失败。"""
    module = import_module('modules.screenshot.targets')
    return module.allocate_targets(raw_targets, reserved_names)


def make_target(**overrides):
    """构造有效目标，并覆盖当前用例关注的字段。"""
    return {'provider': 'window', 'target': '示例窗口', **overrides}


def output_names(targets):
    """提取顺序输出名以便验证分配结果。"""
    return [target['out'] for target in targets]


def test_capture_result_is_frozen_and_exported():
    """结果模型保留全部字段、默认告警，并禁止重新赋值。"""
    models = import_module('modules.screenshot.models')
    package = import_module('modules.screenshot')
    result = models.CaptureResult(b'png', 'window', '窗口', 320, 240)
    assert result.png_bytes == b'png'
    assert result.source == 'window'
    assert result.target == '窗口'
    assert (result.width, result.height) == (320, 240)
    assert result.warnings == ()
    with pytest.raises(FrozenInstanceError):
        result.width = 640
    warned = models.CaptureResult(
        b'png', 'adb', 'serial', 10, 20, ('已缩放',))
    assert warned.warnings == ('已缩放',)
    assert package.CaptureResult is models.CaptureResult
    assert package.allocate_targets is import_module(
        'modules.screenshot.targets').allocate_targets


def test_missing_or_empty_targets():
    """缺省、空值与空列表都返回空结果。"""
    module = import_module('modules.screenshot.targets')
    assert module.allocate_targets() == ([], [])
    assert allocate(None) == ([], [])
    assert allocate([]) == ([], [])


@pytest.mark.parametrize('raw', [False, 42, 'secret-token', {}, ()])
def test_non_list_returns_safe_diagnostic(raw):
    """顶层类型错误不抛整批异常，也不暴露输入内容。"""
    targets, warnings = allocate(raw)
    assert targets == []
    assert len(warnings) == 1
    assert '列表' in warnings[0]
    assert 'secret-token' not in warnings[0]


def test_normalizes_mapping_provider_and_target():
    """接受一般映射，规范化后端及目标且不推断目标类型。"""
    targets, warnings = allocate([
        UserDict(provider=' Window ', target=' 窗口 '),
        make_target(provider=' ADB ', target=' emulator-5554 '),
        make_target(provider='adb', target='127.0.0.1:5555'),
        make_target(provider='window', target='adb:serial'),
        make_target(provider='adb', target='任意非空序列号'),
    ])
    assert targets[0] == {
        'index': 1, 'provider': 'window', 'target': '窗口',
        'out': 'screenshot_1', 'error': None, 'renamed_from': None,
    }
    assert targets[1]['provider'] == 'adb'
    assert targets[1]['target'] == 'emulator-5554'
    assert targets[2]['target'] == '127.0.0.1:5555'
    assert targets[3]['provider'] == 'window'
    assert targets[3]['target'] == 'adb:serial'
    assert all(target['error'] is None for target in targets)
    assert warnings == []


@pytest.mark.parametrize('raw', [None, False, 7, 'secret-token', []])
def test_non_mapping_retains_position_and_failure(raw):
    """非法列表项仍占据原序号与输出位置，不泄露原值。"""
    targets, warnings = allocate([raw, make_target()])
    assert [target['index'] for target in targets] == [1, 2]
    assert output_names(targets) == ['screenshot_1', 'screenshot_2']
    assert targets[0]['error']
    assert targets[0]['renamed_from'] is None
    assert targets[1]['error'] is None
    assert 'secret-token' not in str(warnings)
    assert 'secret-token' not in targets[0]['error']


@pytest.mark.parametrize('provider', [None, 12, '', ' ', 'unknown-secret'])
def test_invalid_provider_retains_failure(provider):
    """非法后端安全失败，不尝试推断其他后端。"""
    targets, warnings = allocate([
        make_target(provider=provider), make_target(provider='adb')])
    assert targets[0]['error']
    assert output_names(targets) == ['screenshot_1', 'screenshot_2']
    assert targets[1]['error'] is None
    assert 'unknown-secret' not in targets[0]['error']
    assert 'unknown-secret' not in str(warnings)


@pytest.mark.parametrize('target', [None, 123, False, '', ' \t ', {}])
def test_invalid_target_is_not_stringified(target):
    """非字符串或空目标应失败，而不是被强制转成字符串。"""
    targets, _ = allocate([make_target(target=target)])
    assert targets[0]['error']
    assert targets[0]['out'] == 'screenshot_1'


def test_arbitrary_objects_are_not_formatted():
    """未知对象不能通过字符串转换泄露数据或中断解析。"""
    class Unprintable:
        """模拟禁止隐式格式化的配置对象。"""

        def __str__(self):
            """拒绝字符串转换。"""
            raise AssertionError('不得转换任意对象')

        def __repr__(self):
            """拒绝调试格式化。"""
            raise AssertionError('不得格式化任意对象')

    targets, warnings = allocate([
        make_target(target=Unprintable(), out=Unprintable()),
        make_target(provider=Unprintable()),
        Unprintable(),
    ])
    assert all(target['error'] for target in targets)
    assert output_names(targets) == [
        'screenshot_1', 'screenshot_2', 'screenshot_3']
    assert warnings


@pytest.mark.parametrize(('declarations', 'expected'), [
    ([{'out': 'screenshot_2'}, {}], ['screenshot_2', 'screenshot_3']),
    ([{'out': 'screenshot_2'}, {}, {}],
     ['screenshot_2', 'screenshot_4', 'screenshot_3']),
    ([{'out': 'screenshot_2'}, {'out': 'screenshot_1'}],
     ['screenshot_2', 'screenshot_1']),
    ([{'out': 'custom'}, {'out': 'custom'}, {}],
     ['custom', 'screenshot_2', 'screenshot_3']),
    ([{'out': 'screenshot_8'}, {'out': 'screenshot_8'},
      {'out': 'screenshot_9'}],
     ['screenshot_8', 'screenshot_10', 'screenshot_9']),
    ([{'out': 'custom'}, {}, {'out': 'custom'}, {}],
     ['custom', 'screenshot_2', 'screenshot_3', 'screenshot_4']),
])
def test_declared_names_are_reserved_before_allocation(declarations, expected):
    """重复项不能抢占未来声明名，自定义名替代默认名。"""
    targets, warnings = allocate([
        make_target(**declaration) for declaration in declarations])
    assert output_names(targets) == expected
    for index, (declaration, actual) in enumerate(
            zip(declarations, targets), 1):
        original = declaration.get('out', f'screenshot_{index}')
        renamed = original != actual['out']
        assert actual['renamed_from'] == (original if renamed else None)
        if renamed:
            assert any(
                str(index) in warning and original in warning
                and actual['out'] in warning for warning in warnings)
    assert len(warnings) == sum(
        target['renamed_from'] is not None for target in targets)


@pytest.mark.parametrize(('suffix', 'next_suffix'), [
    pytest.param('9' * 5000, '1' + '0' * 5000, id='long-carry'),
    pytest.param('0' * 10 + '9' * 5000, '1' + '0' * 5000,
                 id='long-leading-zeros'),
    pytest.param('0' * 5000 + '9', '10', id='many-leading-zeros'),
    pytest.param('8', '9', id='ordinary'),
    pytest.param('009', '10', id='leading-zeros'),
    pytest.param('000', '1', id='all-zeros'),
    pytest.param('0', '1', id='zero'),
    pytest.param('099', '100', id='carry-leading-zero'),
])
def test_duplicate_numbered_output_increments_without_integer_limit(
        suffix, next_suffix):
    """重复编号支持超长后缀，保留首项且规范化递增后的前导零。"""
    name = 'screenshot_' + suffix
    replacement = 'screenshot_' + next_suffix
    raw = [make_target(out=name), make_target(out=name), make_target()]
    before = deepcopy(raw)

    targets, warnings = allocate(raw)

    assert output_names(targets) == [name, replacement, 'screenshot_3']
    assert [target['index'] for target in targets] == [1, 2, 3]
    assert all(target['error'] is None for target in targets)
    assert [target['renamed_from'] for target in targets] == [None, name, None]
    assert warnings == [
        f'截图目标 2：输出名“{name}”重复，已改为“{replacement}”。']
    assert raw == before


@pytest.mark.parametrize('occupancy', ['declared', 'reserved'])
def test_long_numbered_output_skips_occupied_successors(occupancy):
    """超长编号递增时连续跳过后续声明或保留名以及已分配名。"""
    name = 'screenshot_' + '9' * 5000
    prefix = 'screenshot_1' + '0' * 4999
    occupied = [prefix + '0', prefix + '1']
    raw = [
        make_target(out=name), make_target(out=name), make_target(),
        make_target(out=name),
    ]
    reserved = occupied.copy() if occupancy == 'reserved' else []
    if occupancy == 'declared':
        raw.extend(make_target(out=out) for out in occupied)
    before = deepcopy((raw, reserved))

    targets, warnings = allocate(raw, reserved)

    expected = [name, prefix + '2', 'screenshot_3', prefix + '3']
    if occupancy == 'declared':
        expected.extend(occupied)
    assert output_names(targets) == expected
    assert all(target['error'] is None for target in targets)
    assert warnings == [
        f'截图目标 2：输出名“{name}”重复，已改为“{prefix}2”。',
        f'截图目标 4：输出名“{name}”重复，已改为“{prefix}3”。',
    ]
    assert (raw, reserved) == before


def test_reserved_names_and_aggregate_are_never_overwritten():
    """聚合变量及调用方保留名不可覆盖，也不修改保留集合。"""
    reserved = {'custom', 'screenshot_1', 'screenshot_2', 'screenshot_4'}
    original = reserved.copy()
    targets, warnings = allocate([
        make_target(out='screenshot'), make_target(out='custom'),
        make_target(), make_target(out='screenshot_4'),
    ], reserved)
    assert output_names(targets) == [
        'screenshot_5', 'screenshot_6', 'screenshot_3', 'screenshot_7']
    assert reserved == original
    assert [target['renamed_from'] for target in targets] == [
        'screenshot', 'custom', None, 'screenshot_4']
    assert len(warnings) == 3
    assert all('screenshot_' in warning for warning in warnings)


@pytest.mark.parametrize('out', [
    None, False, 123, '', ' ', 'has space', '9name', '中文',
    'name\n', ' name ', 'secret-token\npassword', {'secret': 'token'}, [],
])
def test_invalid_out_is_repaired_with_safe_warning(out):
    """非法输出名修复为可用编号，原名仅保留安全概括。"""
    targets, warnings = allocate([make_target(out=out), make_target()])
    assert output_names(targets) == ['screenshot_1', 'screenshot_2']
    assert targets[0]['error'] is None
    description = targets[0]['renamed_from']
    assert isinstance(description, str) and description
    assert len(warnings) == 1
    assert '1' in warnings[0]
    assert description in warnings[0]
    assert 'screenshot_1' in warnings[0]
    assert 'secret' not in warnings[0]
    assert 'password' not in warnings[0]
    assert '\n' not in warnings[0]


@pytest.mark.parametrize('out', ['x', '_', '_A9', 'Window_1', 'screenshot_0'])
def test_legal_ascii_out_is_used_without_alias(out):
    """合法显式名称原样使用，且不额外产生默认别名。"""
    targets, warnings = allocate([make_target(out=out)])
    assert output_names(targets) == [out]
    assert targets[0]['renamed_from'] is None
    assert warnings == []


def test_invalid_output_skips_future_declarations():
    """非法输出的回退编号也跳过全部未来声明。"""
    targets, warnings = allocate([
        make_target(out=None), make_target(out='screenshot_1'),
        make_target(),
    ])
    assert output_names(targets) == [
        'screenshot_2', 'screenshot_1', 'screenshot_3']
    assert len(warnings) == 1


def test_invalid_targets_still_reserve_declared_names():
    """解析失败项同样参与名称占位和冲突消解。"""
    targets, warnings = allocate([
        make_target(out='screenshot_2'), None,
        make_target(provider='bad', out='screenshot_3'),
        make_target(out='screenshot_3'),
    ])
    assert output_names(targets) == [
        'screenshot_2', 'screenshot_4', 'screenshot_3', 'screenshot_5']
    assert targets[1]['error'] and targets[2]['error']
    assert targets[3]['error'] is None
    assert targets[1]['renamed_from'] == 'screenshot_2'
    assert targets[3]['renamed_from'] == 'screenshot_3'
    assert warnings


def test_inputs_remain_deeply_unchanged_and_warnings_ordered():
    """分配不修改嵌套配置或保留列表，告警按目标顺序返回。"""
    raw = [
        make_target(out='screenshot', extra={'nested': [1, {'x': 2}]}),
        make_target(out={'secret': ['token']}),
        make_target(out='reserved'),
    ]
    reserved = ['reserved', 'screenshot_1']
    before = deepcopy((raw, reserved))
    first = allocate(raw, reserved)
    assert (raw, reserved) == before
    assert first == allocate(raw, reserved)
    targets, warnings = first
    assert len(warnings) == 3
    for index, (target, warning) in enumerate(zip(targets, warnings), 1):
        assert str(index) in warning
        assert target['renamed_from'] in warning
        assert target['out'] in warning
    targets[0]['target'] = '修改返回值'
    assert (raw, reserved) == before
