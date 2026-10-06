#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证显式截图分派，不连接真实设备或操作真实窗口。"""

from importlib import import_module
from importlib.util import find_spec
from unittest.mock import Mock

import pytest


def service_environment(monkeypatch):
    """隔离两个后端，让缺失分派入口明确表现为测试断言失败。"""
    assert find_spec('modules.screenshot.service') is not None, (
        '尚未实现显式截图分派')
    module = import_module('modules.screenshot.service')
    monkeypatch.setattr(module, 'capture_window', Mock())
    monkeypatch.setattr(module, 'capture_adb', Mock())
    return module


@pytest.mark.parametrize(('source', 'target'), [
    ('window', '测试窗口'),
    ('adb', '127.0.0.1:16384'),
    ('window', 'adb:emulator-5554'),
    ('adb', 'window:测试窗口'),
    ('window', ' 标题 '),
    ('adb', ' serial '),
    ('window', None),
    ('adb', None),
    ('window', ''),
    ('adb', ''),
])
def test_explicit_source_calls_only_selected_backend(
        monkeypatch, source, target):
    """只调用明确指定的后端，不解析前缀也不猜测或修改目标。"""
    service = service_environment(monkeypatch)
    selected = getattr(service, f'capture_{source}')
    other = service.capture_window if source == 'adb' else service.capture_adb
    expected = service.CaptureResult(
        b'png', source, target, 2, 3, (), 'jpeg')
    selected.return_value = expected
    assert service.capture(source, target) is expected
    selected.assert_called_once_with(
        target, image_format='jpeg',
        **({'purpose': 'automatic'} if source == 'adb' else {}))
    other.assert_not_called()


def test_custom_format_and_purpose_reach_selected_backend(monkeypatch):
    """显式格式和调用目的必须原样传给对应后端。"""
    service = service_environment(monkeypatch)
    expected = service.CaptureResult(
        b'webp', 'adb', 'serial', 2, 3, (), 'webp')
    service.capture_adb.return_value = expected

    assert service.capture(
        'adb', 'serial', image_format='webp', purpose='manual') is expected
    service.capture_adb.assert_called_once_with(
        'serial', image_format='webp', purpose='manual')
    service.capture_window.assert_not_called()


def test_window_capture_uses_explicit_format_without_purpose(monkeypatch):
    """窗口后端接收格式参数，调用目的不改变其既有接口。"""
    service = service_environment(monkeypatch)
    expected = service.CaptureResult(
        b'png', 'window', '标题', 2, 3, (), 'png')
    service.capture_window.return_value = expected

    assert service.capture(
        'window', '标题', image_format='png', purpose='manual') is expected
    service.capture_window.assert_called_once_with(
        '标题', image_format='png')
    service.capture_adb.assert_not_called()
@pytest.mark.parametrize('source', [
    None, False, 123, [], {}, '', 'auto', 'ADB', ' window ', 'adb:serial',
])
def test_unknown_source_fails_without_backend_call(monkeypatch, source):
    """只接受已解析的精确后端名，未知来源明确安全失败。"""
    service = service_environment(monkeypatch)
    with pytest.raises(service.CaptureError) as caught:
        service.capture(source, 'secret-token')
    assert caught.value.code == 'invalid_source'
    assert 'secret-token' not in str(caught.value)
    service.capture_window.assert_not_called()
    service.capture_adb.assert_not_called()


@pytest.mark.parametrize('source', ['window', 'adb'])
@pytest.mark.parametrize('kind', ['capture', 'interrupt', 'exit'])
def test_backend_error_propagates_without_fallback(monkeypatch, source, kind):
    """后端失败及控制信号原对象传播，绝不尝试其他后端。"""
    service = service_environment(monkeypatch)
    errors = {
        'capture': service.CaptureError('backend_failed', '固定安全消息'),
        'interrupt': KeyboardInterrupt(),
        'exit': SystemExit(),
    }
    error = errors[kind]
    selected = getattr(service, f'capture_{source}')
    selected.side_effect = error
    with pytest.raises(type(error)) as caught:
        service.capture(source, 'target')
    assert caught.value is error
    selected.assert_called_once_with(
        'target', image_format='jpeg',
        **({'purpose': 'automatic'} if source == 'adb' else {}))
    other = service.capture_window if source == 'adb' else service.capture_adb
    other.assert_not_called()


def test_package_exports_only_implemented_interfaces():
    """包入口导出真实实现，复用同一份模型且不引入循环依赖。"""
    package = import_module('modules.screenshot')
    assert hasattr(package, 'capture'), '尚未导出统一截图接口'
    assert hasattr(package, 'capture_adb'), '尚未导出 ADB 截图接口'
    service = import_module('modules.screenshot.service')
    adb = import_module('modules.screenshot.adb')
    models = import_module('modules.screenshot.models')
    assert package.capture is service.capture
    assert package.capture_adb is adb.capture_adb
    assert package.CaptureResult is adb.CaptureResult is models.CaptureResult
    assert package.CaptureError is service.CaptureError is models.CaptureError
    assert all(hasattr(package, name) for name in package.__all__)
