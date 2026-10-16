import ast
import logging
from pathlib import Path

import pytest

from modules import operation_trace as trace
from modules.image_host.core import _ERROR_MESSAGES
from modules.screenshot.models import _CAPTURE_MESSAGES


class UnsafeValue:
    """禁止通过对象转换泄露输入。"""

    def __str__(self):
        """任何字符串转换都使测试失败。"""
        raise AssertionError('不得转换对象')


@pytest.mark.parametrize('field', ['reason_code', 'image_format'])
@pytest.mark.parametrize('value', ['secret\nforged=value', 'unregistered', UnsafeValue(), [], True])
def test_classification_boundary(field, value):
    """未知文本固定归类，其他对象省略。"""
    event = trace._create_event(**{field: value})
    assert getattr(event, field) == ('unknown' if type(value) is str else None)


@pytest.mark.parametrize('mode', ['secret\nforged=value', 'capture', UnsafeValue(), [], None])
def test_mode_boundary_covers_plan_summary_and_events(mode, caplog):
    """开始计划与结束汇总都使用安全模式。"""
    events = []
    with caplog.at_level(logging.DEBUG, logger=trace.__name__):
        with trace.trace_scope(mode=mode, on_status=events.append) as batch:
            assert batch.summary()['mode'] == 'unknown'
    assert [event.action for event in events] == ['unknown', 'unknown']
    assert 'mode=unknown' in caplog.text
    assert 'secret' not in caplog.text
    assert 'forged' not in caplog.text


@pytest.mark.parametrize('field,accepted,rejected', [
    ('http_status', [100, 200, 599], [99, 600, -1, True, 200.0, '200']),
    ('width', [0, 1, 1920], [-1, True, 1.0, '1']),
    ('height', [0, 1, 1080], [-1, False, 1.0, '1']),
])
def test_numeric_boundaries(field, accepted, rejected):
    """数值字段拒绝布尔、错误类型及越界整数。"""
    for value in accepted:
        assert getattr(trace._create_event(**{field: value}), field) == value
    for value in rejected:
        assert getattr(trace._create_event(**{field: value}), field) is None
    assert trace._create_event(provider='boltp', storage_id=0).storage_id == 0


def test_production_classifications_are_preserved():
    """核对错误码表、直接原因与辅助函数原因，防止合法常量误拒。"""
    reasons = set(_ERROR_MESSAGES) | set(_CAPTURE_MESSAGES) | {
        '', 'adb_raw_invalid', 'batch_cancelled', 'slot_unfinished',
        'response_too_large', 'encoding_failed', 'encoding_reused',
        'capture_failed', 'recoverable_warning',
    }
    positions = {'_cache_skipped': 1, '_reuse_metadata': 0,
                 '_refresh_event': 1, '_record_fallback': 1, '_skip_fallback': 1}
    root = Path(__file__).resolve().parents[1] / 'modules'
    for path in root.rglob('*.py'):
        if path.name == 'operation_trace.py':
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if not isinstance(node, ast.Call):
                continue
            values = [kw.value for kw in node.keywords if kw.arg == 'reason_code']
            position = positions.get(getattr(node.func, 'id', ''))
            if position is not None:
                values.append(node.args[position])
            for value in values:
                reasons.update(item.value for item in ast.walk(value)
                               if isinstance(item, ast.Constant)
                               and type(item.value) is str)
    for reason in reasons:
        assert trace._create_event(reason_code=reason).reason_code == reason
    for image_format in ('jpeg', 'png', 'webp', 'raw'):
        assert trace._create_event(image_format=image_format).image_format == image_format
    for mode in ('automatic', 'cli'):
        with trace.trace_scope(mode=mode) as batch:
            assert batch.summary()['mode'] == mode
