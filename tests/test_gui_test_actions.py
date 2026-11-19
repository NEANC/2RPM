#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 独立测试动作的离线验证。"""

import copy
from pathlib import Path
from urllib.parse import quote
from unittest.mock import Mock

import pytest

from modules.gui.protocol import TestRequest as GuiRequest
from modules.gui.protocol import TestResult as GuiResult
from modules.gui.test_actions import (prepare_notification_snapshot, run_test,
                                      summarize_notification)
from modules.screenshot.models import CaptureError, CaptureResult


def _config():
    """提供带截图引用和两种图床的最小配置。"""
    return {'push': {'templates': {'on_end': {
        'enable': True, 'capture_screenshot': True,
        'title': '{host_name}', 'content': '{process_name} {screenshot}'}},
        'push_channel_settings': {'channels': [
            {'provider': 'serverchan', 'sckey': 'secret-value'}]},
        'screenshot': {'targets': [
            {'provider': 'window', 'target': '演示窗口'}],
            'image_host': [{'provider': 'first'},
                           {'provider': 'second', 'token': 'secret-value'}]}}}


def _request(tmp_path, action, config=None, **fields):
    """创建动作所需的绝对路径快照。"""
    return GuiRequest('req-1', 2, 3, action, config or _config(),
                       str(tmp_path), str(tmp_path / 'session'),
                       fields.get('target_index'), fields.get('host_index'),
                       fields.get('image_path'), fields.get('template_key'))


def test_protocol_data_classes_keep_identity(tmp_path):
    """请求及结果数据结构承载三元标识和预览位置。"""
    request = _request(tmp_path, 'capture', target_index=0)
    result = GuiResult(request.request_id, request.document_generation,
                        request.revision, 'success', '', ('preview.jpg',))
    assert (result.request_id, result.document_generation, result.revision) == (
        'req-1', 2, 3)
    assert result.preview_paths == ('preview.jpg',)


@pytest.mark.parametrize('results, expected', [
    ([], 'failure'), ([('a', True)], 'success'),
    ([('a', False)], 'failure'),
    ([('a', True), ('b', False)], 'partial'),
])
def test_summarize_notification(results, expected):
    """空结果与全假不当作已发送成功。"""
    assert summarize_notification(results) == expected


def test_notify_snapshot_disables_capture(monkeypatch, tmp_path):
    """真实发送链配假通道，截图、清理及其他业务入口不得被触发。"""
    import modules.monitor as monitor
    import modules.notification as notification
    import modules.screenshot.pipeline as pipeline
    import modules.screenshot.retention as retention
    import modules.utils as utils
    import modules.gui.test_actions as actions

    def forbidden(*_args, **_kwargs):
        """拦截与发送测试无关的副作用。"""
        pytest.fail('通知测试触发了其他业务动作')

    for module, name in ((actions, 'capture'),
                         (actions, 'upload_with_fallback'),
                         (retention, 'cleanup_retention'),
                         (monitor, 'monitor_processes'),
                         (utils, 'run_external_action'),
                         (pipeline, 'capture')):
        monkeypatch.setattr(module, name, forbidden)
    prepared = []

    def no_capture(_section, enabled, _names, **_kwargs):
        """真实通知编排可进入，但截图启用标志必须为假。"""
        prepared.append(enabled)
        assert enabled is False
        return pipeline.ScreenshotBatch({}, (), ())

    monkeypatch.setattr(notification, 'prepare_screenshots', no_capture)
    monkeypatch.setattr(notification, '_notify_single_channel',
                        lambda *_args, **_kwargs: True)
    config = _config()
    before = copy.deepcopy(config)
    snapshot = prepare_notification_snapshot(config, 'on_end')
    assert snapshot['push']['templates']['on_end']['capture_screenshot'] is False
    result = run_test(_request(tmp_path, 'notify', config, template_key='on_end'))
    assert result.status == 'success'
    assert prepared == [False]
    assert result.preview_paths == ()
    assert config == before


def test_notify_sends_mixed_legacy_string_and_dict_channels(monkeypatch, tmp_path):
    """字符串旧式通道与字典通道逐项发送固定通知并保留索引。"""
    import modules.gui.test_actions as actions

    config = _config()
    config['push']['push_channel_settings']['channels'] = [
        '[serverchan, synthetic-secret]',
        {'provider': 'bark', 'key': 'other-secret'}]
    sent = []

    def sender(snapshot, key, **_kwargs):
        """记录逐项传入的标准通道与固定测试模板。"""
        channel = snapshot['push']['push_channel_settings']['channels']
        template = snapshot['push']['templates'][key]
        sent.append((copy.deepcopy(channel), template['title'], template['content']))
        return [(channel[0]['provider'], True)]

    monkeypatch.setattr(actions, 'send_notification', sender)
    result = run_test(_request(tmp_path, 'notify', config, template_key='on_end'))

    assert sent == [
        ([{'provider': 'serverchan', 'sckey': 'synthetic-secret'}], '2RPM推送测试',
         '这是一个测试，当你收到时表明一切正常。'),
        ([{'provider': 'bark', 'key': 'other-secret'}], '2RPM推送测试',
         '这是一个测试，当你收到时表明一切正常。')]
    assert result.status == 'success'
    assert [(item.index, item.provider, item.status) for item in result.channel_results] == [
        (0, 'serverchan', 'success'), (1, 'bark', 'success')]
    assert 'synthetic-secret' not in str(result)
    assert 'other-secret' not in str(result)


def test_notify_real_chain_sends_mixed_channels_without_leaking(
        monkeypatch, tmp_path, caplog):
    """真实通知编排发送混合通道，日志及结果不泄露凭证。"""
    import modules.notification as notification

    config = _config()
    config['push']['push_channel_settings']['channels'] = [
        '[serverchan, synthetic-secret]',
        {'provider': 'bark', 'key': 'other-secret'}]
    config['push']['retry'] = {'max_count': 1}
    sent = []

    def fake_channel(channel, title, content, *_args, **_kwargs):
        """代替网络发送并记录实际解析出的单通道与固定消息。"""
        sent.append((channel, title, content))
        return True

    monkeypatch.setattr(notification, '_notify_single_channel', fake_channel)
    with caplog.at_level('INFO', logger=notification.__name__):
        result = run_test(_request(tmp_path, 'notify', config, template_key='on_end'))

    assert sent == [
        ({'provider': 'serverchan', 'sckey': 'synthetic-secret'},
         '2RPM推送测试', '这是一个测试，当你收到时表明一切正常。'),
        ({'provider': 'bark', 'key': 'other-secret'},
         '2RPM推送测试', '这是一个测试，当你收到时表明一切正常。')]
    assert result.status == 'success'
    assert [(item.index, item.provider, item.status) for item in result.channel_results] == [
        (0, 'serverchan', 'success'), (1, 'bark', 'success')]
    assert 'synthetic-secret' not in caplog.text
    assert 'other-secret' not in caplog.text
    assert 'synthetic-secret' not in str(result)
    assert 'other-secret' not in str(result)


def test_notify_real_chain_partial_failure_keeps_fixed_message_and_yaml(
        monkeypatch, tmp_path):
    """真实编排逐通道发固定消息，部分失败不报整体成功且不改 YAML。"""
    import modules.gui.test_actions as actions
    import modules.notification as notification

    yaml_path = tmp_path / 'config.yaml'
    original = b'push:\n  templates: untouched\n'
    yaml_path.write_bytes(original)
    config = _config()
    config['push']['push_channel_settings']['channels'].append(
        {'provider': 'bark', 'key': 'other-secret'})
    before = copy.deepcopy(config)
    sent = []

    def fake_channel(channel, title, content, *_args, **_kwargs):
        """替代外发，记录真实通道调用及消息。"""
        sent.append((channel['provider'], title, content))
        return channel['provider'] == 'serverchan'

    monkeypatch.setattr(notification, '_notify_single_channel', fake_channel)
    monkeypatch.setattr(actions, 'capture',
                        Mock(side_effect=AssertionError('意外截图')))
    monkeypatch.setattr(actions, 'upload_with_fallback',
                        Mock(side_effect=AssertionError('意外上传')))

    result = run_test(_request(tmp_path, 'notify', config, template_key='on_end'))

    assert result.status == 'partial'
    assert result.status != 'success'
    assert sent == [
        (provider, '2RPM推送测试', '这是一个测试，当你收到时表明一切正常。')
        for provider in ('serverchan', 'bark')]
    assert [(item.index, item.status) for item in result.channel_results] == [
        (0, 'success'), (1, 'failure')]
    assert result.preview_paths == ()
    assert yaml_path.read_bytes() == original
    assert config == before


def test_notify_empty_result_is_failure(monkeypatch, tmp_path):
    """无通道时不调用发送入口。"""
    import modules.notification as notification

    monkeypatch.setattr(notification, '_notify_single_channel',
                        lambda *_args, **_kwargs: pytest.fail('意外发送'))
    config = _config()
    config['push']['push_channel_settings']['channels'] = []
    result = run_test(_request(tmp_path, 'notify', config, template_key='on_end'))
    assert result.status == 'failure'
    assert result.reason == '请先添加推送通道'


def test_notify_uses_snapshot_and_aggregates(monkeypatch, tmp_path):
    """替身发送者收到单通道禁图快照，混合结果标为部分成功。"""
    import modules.gui.test_actions as actions

    received = []

    def sender(config, _key, **_kwargs):
        """保存通知副本并给出多通道混合结果。"""
        received.append(copy.deepcopy(config))
        provider = config['push']['push_channel_settings']['channels'][0]['provider']
        return [(provider, provider == 'serverchan')]

    monkeypatch.setattr(actions, 'send_notification', sender)
    config = _config()
    config['push']['push_channel_settings']['channels'].append({'provider': 'bark', 'key': 'key'})
    result = run_test(_request(tmp_path, 'notify', config, template_key='on_end'))
    assert len(received) == 2
    assert all(item['push']['templates']['on_end']['capture_screenshot'] is False
               for item in received)
    assert result.status == 'partial'


def test_capture_saves_only_selected_preview(monkeypatch, tmp_path):
    """预览只截指定目标，将字节保存于会话目录。"""
    import modules.gui.test_actions as actions

    mock_capture = Mock(return_value=CaptureResult(b'jpeg-data', 'window',
                                                   '演示窗口', 1, 1))
    monkeypatch.setattr(actions, 'capture', mock_capture)
    result = run_test(_request(tmp_path, 'capture', target_index=0))
    assert result.status == 'success'
    assert Path(result.preview_paths[0]).read_bytes() == b'jpeg-data'
    assert Path(result.preview_paths[0]).name == 'req-1.jpg'
    mock_capture.assert_called_once_with('window', '演示窗口',
                                         image_format='jpeg', purpose='cli')


def test_adb_png_fallback_preview_uses_png_suffix(monkeypatch, tmp_path):
    """ADB 以 PNG 回退时，预览文件扩展名与实际截图格式一致。"""
    import modules.gui.test_actions as actions

    config = _config()
    config['push']['screenshot']['targets'] = [
        {'provider': 'adb', 'target': 'device-1'}]
    png = b'\x89PNG\r\n\x1a\nsynthetic'
    captured = Mock(return_value=CaptureResult(
        png, 'adb', 'device-1', 1, 1, image_format='png'))
    monkeypatch.setattr(actions, 'capture', captured)

    result = run_test(_request(tmp_path, 'capture', config, target_index=0))

    assert result.status == 'success'
    assert Path(result.preview_paths[0]).name == 'req-1.png'
    assert Path(result.preview_paths[0]).read_bytes() == png
    assert not (tmp_path / 'session' / 'req-1.jpg').exists()
    captured.assert_called_once_with('adb', 'device-1',
                                     image_format='jpeg', purpose='cli')


@pytest.mark.parametrize('request_id', ['../escape', '..\\escape',
                                         '/absolute/escape', 'C:\\escape'])
def test_capture_request_id_cannot_escape_session(monkeypatch, tmp_path,
                                                   request_id):
    """带路径语义的请求标识不得写入会话目录外。"""
    import modules.gui.test_actions as actions

    captured = Mock(return_value=CaptureResult(
        b'image', 'window', '演示窗口', 1, 1))
    monkeypatch.setattr(actions, 'capture', captured)
    request = _request(tmp_path, 'capture', target_index=0)
    request = GuiRequest(request_id, request.document_generation,
                         request.revision, request.action, request.config,
                         request.config_dir, request.session_dir,
                         request.target_index, request.host_index,
                         request.image_path, request.template_key)

    result = run_test(request)

    assert result.status == 'failure'
    assert result.preview_paths == ()
    assert list(tmp_path.rglob('*.jpg')) == []
    captured.assert_not_called()


def test_capture_does_not_run_other_actions(monkeypatch, tmp_path):
    """独立预览不执行保留清理、监视或外部动作。"""
    import modules.gui.test_actions as actions
    import modules.monitor as monitor
    import modules.screenshot.pipeline as pipeline
    import modules.screenshot.retention as retention
    import modules.utils as utils

    def forbidden(*_args, **_kwargs):
        """一旦调用其他业务入口即使测试失败。"""
        pytest.fail('截图测试触发了其他业务动作')

    for module, name in ((actions, 'upload_with_fallback'),
                         (retention, 'cleanup_retention'),
                         (monitor, 'monitor_processes'),
                         (utils, 'run_external_action'),
                         (pipeline, 'capture')):
        monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(actions, 'capture', Mock(return_value=CaptureResult(
        b'image', 'window', '演示窗口', 1, 1)))

    assert run_test(_request(tmp_path, 'capture', target_index=0)).status == 'success'


def test_capture_does_not_upload_notify_or_rewrite_yaml(monkeypatch, tmp_path):
    """预览只生成会话图片，不发送请求或改动磁盘 YAML。"""
    import modules.gui.test_actions as actions

    yaml_path = tmp_path / 'config.yaml'
    original = b'push:\n  templates: untouched\n'
    yaml_path.write_bytes(original)
    config = _config()
    before = copy.deepcopy(config)
    captured = Mock(return_value=CaptureResult(b'image', 'window', '演示窗口', 1, 1))
    monkeypatch.setattr(actions, 'capture', captured)
    monkeypatch.setattr(actions, 'upload_with_fallback',
                        Mock(side_effect=AssertionError('意外上传')))
    monkeypatch.setattr(actions, 'send_notification',
                        Mock(side_effect=AssertionError('意外通知')))

    result = run_test(_request(tmp_path, 'capture', config, target_index=0))

    assert result.status == 'success'
    assert Path(result.preview_paths[0]).read_bytes() == b'image'
    assert yaml_path.read_bytes() == original
    assert config == before
    captured.assert_called_once()


def test_capture_failure_is_safe(monkeypatch, tmp_path):
    """截图失败只显示可信错误分类而不回传原始异常。"""
    import modules.gui.test_actions as actions

    def failing(*_args, **_kwargs):
        """模拟后端携带敏感原文的失败。"""
        raise CaptureError('window_not_found', 'secret-value')

    monkeypatch.setattr(actions, 'capture', failing)
    result = run_test(_request(tmp_path, 'capture', target_index=0))
    assert result.status == 'failure'
    assert 'secret-value' not in result.reason


def test_upload_only_selected_host_without_capture(monkeypatch, tmp_path):
    """上传只读取指定文件且只传选中图床，不截图或进入回退链。"""
    import modules.gui.test_actions as actions

    image = tmp_path / '照片.jpg'
    image.write_bytes(b'image-data')
    monkeypatch.setattr(actions, 'capture',
                        lambda *_a, **_k: pytest.fail('意外截图'))
    uploaded = Mock(return_value=Mock(success=True, url='https://images.example/photo.jpg'))
    monkeypatch.setattr(actions, 'upload_with_fallback', uploaded)
    config = _config()
    result = run_test(_request(tmp_path, 'upload', config, host_index=1,
                               image_path=str(image)))
    assert result.status == 'success'
    assert uploaded.call_args.args == (
        b'image-data', '照片.jpg', [config['push']['screenshot']['image_host'][1]])
    assert 'secret-value' not in result.reason


@pytest.mark.parametrize('selected_success', [True, False])
def test_upload_real_registry_calls_only_selected_host(monkeypatch, tmp_path,
                                                       selected_success):
    """真实注册表分发只调用所选图床，失败也不尝试其他图床。"""
    import modules.gui.test_actions as actions
    import modules.image_host.registry as registry
    from modules.image_host.core import ImageHostError

    image = tmp_path / 'image.jpg'
    image.write_bytes(b'image-data')
    config = _config()
    config['push']['screenshot']['image_host'] = [
        {'provider': 'wmimg', 'token': 'unused'}, {'provider': 'catbox'}]
    first = Mock(side_effect=AssertionError('意外回退到未选图床'))
    selected = Mock(return_value='https://images.example/selected.jpg')
    if not selected_success:
        selected.side_effect = ImageHostError('upload_failed', '')
    monkeypatch.setitem(registry.UPLOADERS, 'wmimg', first)
    monkeypatch.setitem(registry.UPLOADERS, 'catbox', selected)
    monkeypatch.setattr(actions, 'capture',
                        Mock(side_effect=AssertionError('意外截图')))
    monkeypatch.setattr(actions, 'send_notification',
                        Mock(side_effect=AssertionError('意外通知')))

    result = run_test(_request(tmp_path, 'upload', config, host_index=1,
                               image_path=str(image)))

    assert result.status == ('success' if selected_success else 'failure')
    assert result.upload_url == ('https://images.example/selected.jpg'
                                 if selected_success else '')
    selected.assert_called_once()
    first.assert_not_called()


def test_upload_failed_result_does_not_expose_url(monkeypatch, tmp_path):
    """上传结果只取布尔终态，不展示外部诊断或带签名链接。"""
    import modules.gui.test_actions as actions

    image = tmp_path / 'image.jpg'
    image.write_bytes(b'data')
    monkeypatch.setattr(actions, 'upload_with_fallback',
                        Mock(return_value=Mock(success=False, url='secret-value')))
    result = run_test(_request(tmp_path, 'upload', host_index=0,
                               image_path=str(image)))
    assert result.status == 'failure'
    assert 'secret-value' not in result.reason


@pytest.mark.parametrize('provider,target', [
    ('window', '新窗口'), ('adb', 'device-serial')])
def test_capture_accepts_window_and_adb_input(monkeypatch, tmp_path, provider, target):
    """直传目标优先于旧索引且不改配置快照。"""
    import modules.gui.test_actions as actions
    from dataclasses import replace

    captured = Mock(return_value=CaptureResult(b'img', provider, target, 1, 1))
    monkeypatch.setattr(actions, 'capture', captured)
    request = replace(_request(tmp_path, 'capture', target_index=0),
                      capture_provider=provider, capture_target=target)
    before = copy.deepcopy(request.config)
    assert run_test(request).status == 'success'
    captured.assert_called_once_with(provider, target, image_format='jpeg', purpose='cli')
    assert request.config == before


def test_upload_returns_real_url(monkeypatch, tmp_path):
    """只把选中图床真实回调地址交给结果。"""
    import modules.gui.test_actions as actions

    image = tmp_path / 'image.png'
    image.write_bytes(b'img')
    uploaded = Mock(return_value=Mock(success=True, url='https://images.example/real.png'))
    monkeypatch.setattr(actions, 'upload_with_fallback', uploaded)
    result = run_test(_request(tmp_path, 'upload', host_index=1, image_path=str(image)))
    assert result.upload_url == 'https://images.example/real.png'
    assert len(uploaded.call_args.args[2]) == 1


def test_notify_uses_fixed_text_for_every_channel(monkeypatch, tmp_path):
    """禁用或无效的事件模板也不影响固定测试消息逐通道发送。"""
    import modules.gui.test_actions as actions

    config = _config()
    config['push']['templates']['on_end'].update(
        enable=False, title='{broken}', content='{unknown}')
    config['push']['push_channel_settings']['channels'].append(
        {'provider': 'bark', 'key': 'other-secret'})
    sent = []

    def sender(snapshot, key, **_kwargs):
        """记录各通道的测试消息。"""
        template = snapshot['push']['templates'][key]
        sent.append((template['title'], template['content'],
                     snapshot['push']['push_channel_settings']['channels']))
        return [(sent[-1][2][0]['provider'], True)]

    monkeypatch.setattr(actions, 'send_notification', sender)
    result = run_test(_request(tmp_path, 'notify', config, template_key='on_end'))
    assert result.status == 'success'
    assert len(sent) == 2
    assert all(title == '2RPM推送测试' and content == '这是一个测试，当你收到时表明一切正常。'
               and len(channels) == 1 for title, content, channels in sent)
    assert config['push']['templates']['on_end']['enable'] is False


def test_channel_failure_does_not_skip_remaining_channels(monkeypatch, tmp_path):
    """不合法通道计失败，后续通道仍发送且按原索引报告。"""
    import modules.gui.test_actions as actions

    config = _config()
    config['push']['push_channel_settings']['channels'] = [
        {'provider': 'bad\nsecret-value'}, {'provider': 'bark', 'key': 'ok'}]
    sent = []

    def sender(snapshot, key, **_kwargs):
        """模拟有效通道的真实发送入口。"""
        sent.append(snapshot['push']['push_channel_settings']['channels'][0]['provider'])
        return [('bark', True)]

    monkeypatch.setattr(actions, 'send_notification', sender)
    result = run_test(_request(tmp_path, 'notify', config, template_key='on_end'))
    assert result.status == 'partial'
    assert sent == ['bark']
    assert [(item.index, item.status) for item in result.channel_results] == [
        (0, 'failure'), (1, 'success')]
    assert 'secret-value' not in str(result)


def test_notify_accepts_legacy_channel_without_provider(monkeypatch, tmp_path):
    """旧式无 provider 通道应经现有解析器发送并报告实际渠道。"""
    import modules.gui.test_actions as actions

    config = _config()
    config['push']['push_channel_settings']['channels'] = [
        {'serverchan': 'synthetic-key'}]
    sent = Mock(return_value=[('serverchan', True)])
    monkeypatch.setattr(actions, 'send_notification', sent)

    result = run_test(_request(tmp_path, 'notify', config, template_key='on_end'))

    assert result.status == 'success'
    assert [(item.index, item.provider, item.status) for item in result.channel_results] == [
        (0, 'serverchan', 'success')]
    sent.assert_called_once()


def test_upload_url_with_configured_secret_is_not_returned(monkeypatch, tmp_path):
    """回调链接中若包含配置凭证不能向 GUI 暴露。"""
    import modules.gui.test_actions as actions

    image = tmp_path / 'image.png'
    image.write_bytes(b'img')
    monkeypatch.setattr(actions, 'upload_with_fallback', Mock(return_value=Mock(
        success=True, url='https://images.example/file?token=secret-value')))
    result = run_test(_request(tmp_path, 'upload', host_index=1, image_path=str(image)))
    assert result.status == 'failure'
    assert result.upload_url == ''
    assert 'secret-value' not in str(result)


def test_upload_url_with_double_encoded_secret_is_not_returned(monkeypatch, tmp_path):
    """回调链接双重编码的配置凭证也不能越过 GUI 边界。"""
    import modules.gui.test_actions as actions

    image = tmp_path / 'image.png'
    image.write_bytes(b'img')
    secret = 'synthetic:credential'
    config = _config()
    config['push']['screenshot']['image_host'][1]['token'] = secret
    url = f'https://images.example/file?token={quote(quote(secret))}'
    monkeypatch.setattr(actions, 'upload_with_fallback', Mock(return_value=Mock(
        success=True, url=url)))

    result = run_test(_request(tmp_path, 'upload', config, host_index=1,
                               image_path=str(image)))

    assert result.status == 'failure'
    assert result.upload_url == ''
    assert quote(quote(secret)) not in str(result)
