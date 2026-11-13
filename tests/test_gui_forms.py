#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""通知表单和纯模板预览测试。"""

import copy

import pytest

from modules.gui.forms import (event_variables, preview_template,
                               provider_fields, screenshot_conditions)
from modules.notification import validate_template_fields


def _config(content='{process_name} {screenshot_1}', targets=None, channels=None):
    """建立不依赖真实发送的最小通知配置。"""
    return {'push': {
        'templates': {'on_end': {'enable': True, 'capture_screenshot': True,
                                 'title': '标题 {host_name}', 'content': content}},
        'push_channel_settings': {'channels': channels if channels is not None else
                                  [{'provider': 'serverchan', 'sckey': 'secret'}]},
        'screenshot': {'targets': targets if targets is not None else
                       [{'provider': 'window', 'target': 'demo'}]},
    }}


def test_provider_fields_use_installed_metadata():
    """凭证字段来自已安装 provider，标题正文不当作凭证。"""
    fields = {field.name: field for field in provider_fields('smtp')}
    assert fields['password'].secret is True
    assert fields['password'].required is True
    assert fields['host'].label
    assert 'title' not in fields
    assert provider_fields('private-channel') == ()


@pytest.mark.parametrize('provider, field', [
    ('bark', 'device_key'),
    ('pushplus', 'token'),
    ('smtp', 'password'),
])
def test_provider_credentials_use_notification_secret_rules(provider, field):
    """表单对通知发送侧识别的凭证字段保持遮挡。"""
    fields = {item.name: item for item in provider_fields(provider)}
    assert fields[field].secret is True


def test_event_variables_are_scoped():
    """等待变量不得进入结束事件。"""
    assert 'process_wait_time' in event_variables('on_wait_timeout')
    assert 'process_wait_time' not in event_variables('on_end')
    assert event_variables('on_external')['external_program_name']


def test_external_preview_accepts_process_context():
    """外部动作通知预览使用真实发送侧可提供的进程变量。"""
    config = _config()
    config['push']['templates']['on_external'] = {
        'title': '{process_name} ({process_pid})',
        'content': '{external_program_name}',
    }
    title, body = preview_template(config, 'on_external')
    assert title == '[模拟] 模拟程序.exe (1234)'
    assert body.startswith('[模拟]')


def test_preview_has_no_side_effects(monkeypatch):
    """预览不获取真实主机、截图或调用通知发送。"""
    import modules.notification as notification
    config = _config()
    original = copy.deepcopy(config)
    monkeypatch.setattr(notification.socket, 'gethostname',
                        lambda: pytest.fail('读取真实主机'))
    monkeypatch.setattr(notification, 'send_notification',
                        lambda *_a, **_k: pytest.fail('真实发送'))
    title, body = preview_template(config, 'on_end')
    assert title.startswith('[模拟]')
    assert '模拟主机' in title
    assert '模拟程序.exe' in body
    assert config == original


def test_template_validation_nested_and_escaped():
    """递归解析 format spec 且转义花括号无字段引用。"""
    assert validate_template_fields('{{escaped}} {process_name:{width}}', '',
                                    {'process_name': 'demo', 'width': 10}, set()) == ()
    assert validate_template_fields('{process_name:{missing}}', '',
                                    {'process_name': 'demo'}, set())


@pytest.mark.parametrize('content', ['{unknown}', '{process_wait_time}',
                                     '{screenshot_1.bad}', '{process_name!q}', '{'])
def test_preview_rejects_invalid_fields_safely(content):
    """无效字段返回安全诊断，不回显模板原文。"""
    config = _config(content)
    with pytest.raises(ValueError) as exc:
        preview_template(config, 'on_end')
    assert content not in str(exc.value)


def test_screenshot_conditions_reuse_allocated_names():
    """通道、引用和目标各自体现真实生效条件。"""
    config = _config(content='{screenshot_1}', targets=[
        {'provider': 'window', 'target': 'a', 'out': 'host_name'}])
    assert screenshot_conditions(config, 'on_end') == {
        'enabled': True, 'channels': True, 'references': True, 'targets': True}
    config['push']['templates']['on_end']['content'] = '{screenshot_2}'
    assert screenshot_conditions(config, 'on_end')['references'] is False
    config['push']['push_channel_settings']['channels'] = []
    assert screenshot_conditions(config, 'on_end')['channels'] is False


@pytest.mark.parametrize('channels', [
    [{'provider': 'private-channel', 'token': 'secret'}],
    [{'provider': '', 'token': 'secret'}],
])
def test_screenshot_conditions_require_known_channel(channels):
    """仅被 OnePush 识别的推送通道满足截图通道条件。"""
    config = _config(channels=channels)
    assert screenshot_conditions(config, 'on_end')['channels'] is False


@pytest.mark.parametrize('targets', [
    [{'provider': 'window', 'target': ''}],
    [{'provider': 'unknown', 'target': 'demo'}],
    [{'provider': 'window', 'target': 'demo@{version}suffix'}],
])
def test_screenshot_conditions_require_valid_target(targets):
    """目标分配保留的错误槽位不能视作有效截图目标。"""
    config = _config(targets=targets)
    assert screenshot_conditions(config, 'on_end')['targets'] is False


def test_aggregate_screenshot_reference_enables_capture():
    """聚合截图变量与单个输出变量一样可以触发截图。"""
    config = _config(content='{screenshot}')
    assert screenshot_conditions(config, 'on_end')['references'] is True
    assert '[模拟截图]' in preview_template(config, 'on_end')[1]


def test_template_diagnostics_do_not_echo_secrets():
    """无效访问不能把敏感原值写入诊断。"""
    issues = validate_template_fields('{token.secret}', '',
                                      {'token': 'secret-98765'}, set())
    assert issues
    assert 'secret-98765' not in repr(issues)
