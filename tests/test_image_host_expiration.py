#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证组合期限解析和无外部输入输出的到期时间换算。"""

from copy import deepcopy
from dataclasses import fields
from dataclasses import FrozenInstanceError
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from importlib import import_module
from inspect import signature

import pytest


MAX_SECONDS = 315537897599
SECRET = 'FAKE_EXPIRATION_SECRET_7319'
START = 1700000000


def expiration():
    """延迟导入真实模块，使缺少实现表现为测试失败。"""
    return import_module('modules.image_host.expiration')


def assert_safe_error(error):
    """核对固定错误元数据且不保留敏感内容或底层异常链。"""
    assert error.code == 'config_error'
    assert error.stage == 'expiration'
    assert error.args == ('图床配置无效',)
    assert SECRET not in str(error) + repr(error) + repr(error.args)
    assert SECRET not in repr(vars(error))
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize('value, expected', [
    ('7d', 604800), ('1d12h', 129600), ('2h30m', 9000),
    ('1w2d3h4m5s', 788645), (' \t30m\r\n', 1800), ('90m', 5400),
    ('1s', 1), ('01h002m', 3720), ('1w1s', 604801),
    ('315537897599s', MAX_SECONDS),
    ('3652058d23h59m59s', MAX_SECONDS),
    ('0' * 5000 + '1s', 1),
], ids=lambda value: str(value)[:40])
def test_parse_valid_combinations(value, expected):
    """完整解析降序单位、非归一数字和首尾空白，不修改原文。"""
    original = value
    result = expiration().parse_expiration(value)
    assert result == expected
    assert type(result) is int
    assert value == original


@pytest.mark.parametrize('value', [
    None, True, False, 1, 1.0, [], {}, b'1d', '', ' \t\n',
    '0s', '00h', '1d0h', '0w1s', '-1d', '+1d', '1.5h',
    '1D', '1W', '1y', '1ms', '1', 'd', '1d2', '1dgarbage',
    'garbage1d', '1d1d', '1h1d', '1m1w', '1s1m', '1w1w',
    '1d 2h', '1 d', '1d\t2h', '1d\n2h', '1d\x002h',
    '１d', '١d', '²h', '1\u200bd',
    '315537897600s', '3652058d24h', '521723w',
    '9' * 5000 + 's', SECRET, '1d' + SECRET,
], ids=lambda value: repr(value)[:40])
def test_parse_invalid_values_fail_safely(value):
    """拒绝类型、非法片段及超界总量，不泄漏输入或转换异常。"""
    module = expiration()
    error_type = import_module('modules.image_host.core').ImageHostError
    before = deepcopy(value)
    with pytest.raises(error_type) as caught:
        module.parse_expiration(value)
    assert_safe_error(caught.value)
    assert value == before


def test_span_bound_matches_datetime_range():
    """最大整秒跨度来自 datetime 表示范围，不另设业务上限。"""
    span = datetime.max - datetime.min
    assert span.days * 86400 + span.seconds == MAX_SECONDS
    assert expiration().parse_expiration(f'{MAX_SECONDS}s') == MAX_SECONDS


def test_decision_is_frozen_with_ordered_typed_fields():
    """决策字段按约定顺序定义，支持位置构造且全部不可变。"""
    decision_type = expiration().ExpirationDecision
    assert [(item.name, item.type) for item in fields(decision_type)] == [
        ('deadline', float | None), ('expired_at', str | None),
        ('shortened', bool),
    ]
    decision = decision_type(4600.0, '4600', True)
    for name, value in [('deadline', 5000), ('expired_at', 'changed'),
                        ('shortened', False)]:
        with pytest.raises(FrozenInstanceError):
            setattr(decision, name, value)


def test_public_function_signatures():
    """两个函数保持固定参数名、顺序、默认值和返回类型。"""
    module = expiration()
    assert list(signature(module.parse_expiration).parameters) == ['value']
    assert signature(module.parse_expiration).return_annotation is int
    parameters = signature(module.compute_expiration).parameters
    assert list(parameters) == [
        'seconds', 'started_at', 'retention_seconds', 'previous_deadline',
        'now', 'formatter', 'previous_shortened',
    ]
    assert parameters['formatter'].default is None
    assert parameters['previous_shortened'].default is None
    assert (parameters['previous_shortened'].kind
            is parameters['previous_shortened'].KEYWORD_ONLY)
    for name in list(parameters)[:6]:
        assert parameters[name].kind is parameters[name].POSITIONAL_OR_KEYWORD
    assert (signature(module.compute_expiration).return_annotation
            is module.ExpirationDecision)


@pytest.mark.parametrize('retention', [None, 0, 7200])
def test_unconfigured_expiration_does_not_invent_deadline(retention):
    """未配置即省略期限，不因组策略或历史期限产生显示值。"""
    def unexpected(value):
        """未配置时禁止格式化。"""
        pytest.fail('未配置期限不应格式化')

    module = expiration()
    assert module.compute_expiration(
        None, START, retention, START + 100, START + 1,
        formatter=unexpected) == module.ExpirationDecision(None, None, False)


@pytest.mark.parametrize('retention, previous, duration, shortened', [
    (None, None, 7200, False), (0, None, 7200, False),
    (3600, None, 3600, True), (7200, None, 7200, False),
    (9000, None, 7200, False),
    (None, START + 1800, 1800, True),
    (None, START + 7200, 7200, False),
    (None, START + 9000, 7200, False),
    (3600, START + 1800, 1800, True),
    (3600, START + 3600, 3600, True),
    (3600, START + 5000, 3600, True),
])
def test_policy_and_previous_deadline_only_shorten(
        retention, previous, duration, shortened):
    """未知组上限不猜测，策略和历史期限只能缩短原始请求。"""
    result = expiration().compute_expiration(
        7200, START, retention, previous, START + 1,
        formatter=lambda value: str(int(value)))
    assert result.deadline == START + duration
    assert result.expired_at == str(START + duration)
    assert result.shortened is shortened


def test_representative_combined_limits():
    """固定代表用例同时应用组上限与上次绝对期限。"""
    result = expiration().compute_expiration(
        604800, 1000, 7200, 4600, 1001,
        formatter=lambda value: str(int(value)))
    assert result == expiration().ExpirationDecision(4600, '4600', True)


@pytest.mark.parametrize('previous, expected, shortened', [
    (None, START + 10, False),
    (START + 10.25, START + 10, True),
    (START + 10.75, START + 10, False),
])
def test_floor_does_not_itself_count_as_policy_shortening(
        previous, expected, shortened):
    """格式化收到向下取整期限，单纯丢弃亚秒不属于策略缩短。"""
    seen = []

    def formatter(value):
        """记录格式化收到的绝对秒数。"""
        seen.append(value)
        return str(int(value))

    result = expiration().compute_expiration(
        10, START + 0.75, None, previous, START, formatter=formatter)
    assert result.deadline == expected
    assert result.expired_at == str(expected)
    assert result.shortened is shortened
    assert seen == [expected]


@pytest.mark.parametrize('now, valid', [
    (START + 9.999, True), (START + 10, False),
    (START + 10.25, False), (START + 11, False),
])
def test_expiration_checked_after_floor_and_before_formatter(now, valid):
    """取整后须严格晚于 now；过期时不能调用显示格式化。"""
    module = expiration()
    calls = []

    def formatter(value):
        """记录显示调用，不参与过期判断。"""
        calls.append(value)
        return '任意显示字符串'

    if valid:
        result = module.compute_expiration(
            10, START + 0.75, None, None, now, formatter=formatter)
        assert result.deadline == START + 10
        assert calls == [START + 10]
        return
    error_type = import_module('modules.image_host.core').ImageHostError
    with pytest.raises(error_type) as caught:
        module.compute_expiration(
            10, START + 0.75, None, None, now, formatter=formatter)
    assert_safe_error(caught.value)
    assert calls == []


def test_repeated_calls_keep_start_and_reuploads_never_extend():
    """重复计算不读取时钟，重传继承缩短事实且不从 now 重启。"""
    compute = expiration().compute_expiration
    first = compute(7200, START, 3600, None, START + 1, formatter=str)
    assert compute(7200, START, 3600, None, START + 1,
                   formatter=str) == first
    inherited = compute(7200, START, None, first.deadline,
                        START + 100, formatter=str)
    assert inherited == first
    shorter = compute(7200, START, 1800, inherited.deadline,
                      START + 200, formatter=str)
    assert shorter.deadline == START + 1800
    assert shorter.shortened
    assert compute(7200, START, 9000, shorter.deadline,
                   START + 300, formatter=str) == shorter


def test_different_hosts_share_start_but_not_limits():
    """同图不同站共享调用方起点，各自裁剪且不以当前时间起算。"""
    compute = expiration().compute_expiration
    first = compute(86400, START, 3600, None, START + 20, formatter=str)
    second = compute(86400, START, None, None, START + 30, formatter=str)
    assert first.deadline == START + 3600
    assert second.deadline == START + 86400
    assert not second.shortened


def test_elapsed_day_remains_86400_across_dst_display_change():
    """确定性显示模拟夏令时跳转，绝对经过时长仍为八万六千四百秒。"""
    transition = START + 3600

    def formatter(value):
        """按固定跳转点选时区，不读取或修改进程时区。"""
        offset = -5 if value < transition else -4
        zone = timezone(timedelta(hours=offset))
        return datetime.fromtimestamp(value, zone).strftime(
            '%Y-%m-%d %H:%M:%S')

    result = expiration().compute_expiration(
        86400, START, None, None, START, formatter=formatter)
    assert result.deadline - START == 86400
    assert result.expired_at == formatter(START + 86400)
    displayed_start = datetime.strptime(formatter(START), '%Y-%m-%d %H:%M:%S')
    displayed_end = datetime.strptime(result.expired_at, '%Y-%m-%d %H:%M:%S')
    assert displayed_end - displayed_start == timedelta(hours=25)


def test_default_formatter_matches_local_datetime():
    """默认显示与同机本地 datetime 一致，不假设测试机器时区。"""
    result = expiration().compute_expiration(
        7200, START + 0.75, None, None, START)
    assert result.deadline == START + 7200
    assert result.expired_at == datetime.fromtimestamp(
        START + 7200).strftime('%Y-%m-%d %H:%M:%S')


@pytest.mark.parametrize('name, value', [
    ('seconds', True), ('seconds', False), ('seconds', 0),
    ('seconds', -1), ('seconds', 1.5), ('seconds', '1'),
    ('seconds', float('nan')), ('seconds', float('inf')),
    ('seconds', MAX_SECONDS + 1), ('seconds', 10 ** 5000),
    ('retention_seconds', True), ('retention_seconds', False),
    ('retention_seconds', -1), ('retention_seconds', 1.5),
    ('retention_seconds', float('nan')),
    ('retention_seconds', float('inf')),
    *[(name, value)
      for name in ('started_at', 'previous_deadline', 'now')
      for value in (True, False, -1, float('nan'), float('inf'),
                    float('-inf'), 10 ** 5000, '1000')],
    ('started_at', None), ('now', None),
], ids=lambda value: type(value).__name__)
def test_invalid_numeric_contract_fails_safely(name, value):
    """明确数值边界拒绝布尔、负值、非有限值和不支持的类型。"""
    module = expiration()
    error_type = import_module('modules.image_host.core').ImageHostError
    arguments = dict(seconds=7200, started_at=START,
                     retention_seconds=None, previous_deadline=None,
                     now=START + 1, formatter=str)
    arguments[name] = value
    with pytest.raises(error_type) as caught:
        module.compute_expiration(**arguments)
    assert_safe_error(caught.value)


@pytest.mark.parametrize('formatter', [None, str])
@pytest.mark.parametrize('started_at, seconds', [
    (1e300, 1), (START, MAX_SECONDS), (253402300799, 86400),
])
def test_unrepresentable_deadline_fails_even_with_injected_formatter(
        formatter, started_at, seconds):
    """实际平台和 datetime 的表示边界不能被测试格式化替身绕过。"""
    module = expiration()
    error_type = import_module('modules.image_host.core').ImageHostError
    with pytest.raises(error_type) as caught:
        module.compute_expiration(
            seconds, started_at, None, None, START, formatter=formatter)
    assert_safe_error(caught.value)


@pytest.mark.parametrize('error_type', [ValueError, OSError, OverflowError,
                                       RuntimeError])
def test_formatter_failures_have_no_sensitive_chain(error_type, caplog):
    """普通格式化异常转换为安全错误，不保存原文或日志。"""
    def formatter(value):
        """抛出包含合成秘密的普通异常。"""
        raise error_type(SECRET)

    module = expiration()
    safe_type = import_module('modules.image_host.core').ImageHostError
    with pytest.raises(safe_type) as caught:
        module.compute_expiration(
            10, START, None, None, START, formatter=formatter)
    assert_safe_error(caught.value)
    assert not caplog.records


@pytest.mark.parametrize('result', [None, False, 123, [], {}, object()])
def test_formatter_non_string_result_is_rejected(result):
    """拒绝非字符串显示结果，不把任意对象放入决策。"""
    module = expiration()
    error_type = import_module('modules.image_host.core').ImageHostError
    with pytest.raises(error_type) as caught:
        module.compute_expiration(
            10, START, None, None, START, formatter=lambda value: result)
    assert_safe_error(caught.value)


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_formatter_control_signals_propagate_same_object(signal_type):
    """两个进程控制信号原对象传播，不被转换成配置错误。"""
    signal = signal_type(SECRET)

    def formatter(value):
        """抛出预先构造的控制信号。"""
        raise signal

    with pytest.raises(signal_type) as caught:
        expiration().compute_expiration(
            10, START, None, None, START, formatter=formatter)
    assert caught.value is signal


def test_explicit_history_avoids_rounding_only_shortening():
    """亚秒起点的连续重传携带历史事实，不把取整损失视为缩短。"""
    compute = expiration().compute_expiration
    start = START + 0.75
    first = compute(10, start, None, None, start, formatter=str)
    second = compute(
        10, start, None, first.deadline, start, formatter=str,
        previous_shortened=first.shortened)
    third = compute(
        10, start, None, second.deadline, start, formatter=str,
        previous_shortened=second.shortened)
    assert first == expiration().ExpirationDecision(
        float(START + 10), str(float(START + 10)), False)
    assert second == first
    assert third == first


def test_none_history_flag_preserves_legacy_inference():
    """旧调用和显式 None 均保留无法消除历史取整歧义的推断。"""
    compute = expiration().compute_expiration
    start = START + 0.75
    first = compute(10, start, None, None, start, str)
    legacy = compute(10, start, None, first.deadline, start, str)
    explicit_none = compute(
        10, start, None, first.deadline, start, str,
        previous_shortened=None)
    assert first.shortened is False
    assert legacy.shortened is True
    assert explicit_none == legacy
    assert legacy.deadline == first.deadline
    assert legacy.expired_at == first.expired_at


@pytest.mark.parametrize('retention', [None, 0, 10, 20])
def test_same_second_real_shortening_survives_relaxed_policy(retention):
    """同秒亚秒历史限制即使取整后相同，也通过 True 保留事实。"""
    compute = expiration().compute_expiration
    start = START + 0.75
    unrestricted = compute(10, start, None, None, start, str)
    first = compute(10, start, None, start + 9.5, start, str)
    assert start + 9.5 < start + 10
    assert first.deadline == unrestricted.deadline == START + 10
    assert first.shortened is True
    assert unrestricted.shortened is False
    second = compute(
        10, start, retention, first.deadline, start, str,
        previous_shortened=first.shortened)
    third = compute(
        10, start, retention, second.deadline, start, str,
        previous_shortened=second.shortened)
    assert second == first
    assert third == first


@pytest.mark.parametrize(
    'retention, previous_offset, flag, expected_offset, shortened', [
        (None, None, False, 10, False),
        (None, 10, False, 10, False),
        (None, 10.25, False, 10, False),
        (None, 10.75, False, 10, False),
        (None, 20, False, 10, False),
        (None, 9, False, 9, True),
        (None, 9.75, False, 9, True),
        (9, None, False, 9, True),
        (9, 10, False, 9, True),
        (9, 20, False, 9, True),
        (9, 8, False, 8, True),
        (20, 9, False, 9, True),
        (None, 10, True, 10, True),
        (None, 20, True, 10, True),
        (0, 10, True, 10, True),
        (20, 10, True, 10, True),
        (9, 10, True, 9, True),
    ])
def test_explicit_history_preserves_limits_and_policy_facts(
        retention, previous_offset, flag, expected_offset, shortened):
    """标志不改变实际期限，False 不掩盖组策略和可确定历史限制。"""
    compute = expiration().compute_expiration
    start = START + 0.75
    previous = None if previous_offset is None else START + previous_offset
    legacy = compute(10, start, retention, previous, start, str)
    result = compute(
        10, start, retention, previous, start, str,
        previous_shortened=flag)
    assert result.deadline == legacy.deadline == START + expected_offset
    assert result.expired_at == legacy.expired_at
    assert result.shortened is shortened


@pytest.mark.parametrize('retention', [None, 0, 10, 20])
def test_group_shortening_survives_policy_relaxation(retention):
    """首次组策略缩短在后续放宽或取消上限时仍被继承。"""
    compute = expiration().compute_expiration
    start = START + 0.75
    first = compute(
        10, start, 9, None, start, str, previous_shortened=False)
    assert first.shortened is True
    result = compute(
        10, start, retention, first.deadline, start, str,
        previous_shortened=first.shortened)
    assert result == first


@pytest.mark.parametrize('flag', [
    0, 1, -1, 0.0, 1.0, float('nan'), float('inf'),
    '', 'False', SECRET, [], {}, object(),
], ids=lambda value: type(value).__name__)
def test_invalid_history_flag_fails_safely(flag):
    """历史标志只接受真正布尔或 None，错误不包含输入或异常链。"""
    error_type = import_module('modules.image_host.core').ImageHostError
    with pytest.raises(error_type) as caught:
        expiration().compute_expiration(
            10, START, None, START + 10, START, str,
            previous_shortened=flag)
    assert_safe_error(caught.value)


def test_true_history_flag_requires_previous_deadline():
    """有限期限中 True 缺少历史期限属于固定参数错误。"""
    error_type = import_module('modules.image_host.core').ImageHostError
    with pytest.raises(error_type) as caught:
        expiration().compute_expiration(
            10, START, None, None, START, str, previous_shortened=True)
    assert_safe_error(caught.value)


@pytest.mark.parametrize('flag', [None, False, True, 0, SECRET, object()])
def test_omitted_seconds_skips_history_and_other_validation(flag):
    """省略期限直接返回，不校验历史标志或计算其他无效参数。"""
    def unexpected(value):
        """省略期限时禁止调用显示格式化。"""
        pytest.fail('省略期限不应格式化')

    module = expiration()
    result = module.compute_expiration(
        None, object(), SECRET, None, object(), unexpected,
        previous_shortened=flag)
    assert result == module.ExpirationDecision(None, None, False)


def test_history_flag_is_keyword_only():
    """第七个位置参数被拒绝，原有六个位置参数仍可调用。"""
    compute = expiration().compute_expiration
    expected = compute(10, START, None, None, START, formatter=str)
    assert compute(10, START, None, None, START, str) == expected
    assert compute(
        10, START, None, None, START, str,
        previous_shortened=False) == expected
    with pytest.raises(TypeError):
        compute(10, START, None, None, START, str, False)
