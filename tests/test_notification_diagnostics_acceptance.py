#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""离线端到端验收：锁定诊断安全、真实计数与终态事实，全部外部边界为替身。"""

from importlib import import_module
from io import StringIO
import logging
import socket
from unittest.mock import Mock, patch

import pytest

from modules.image_host.context import UploadContext
from modules.image_host.storage import StorageMetadata
from modules.image_host.storage_cache import StorageCache
from modules.operation_trace import trace_item
from modules.operation_trace import trace_scope
from modules.screenshot.inline_upload import InlineUploadError
from modules.screenshot.inline_upload import parse_inline_hosts
from modules.screenshot.retention import SaveOutcome


MARKER = 'SYNTHETIC_ACCEPT_MARKER_5521'
HOST_SECRET = 'SYNTHETIC_HOST_SECRET_6632'
CHANNEL_SECRET = 'SYNTHETIC_CHANNEL_SECRET_7743'
TARGET = 'SYNTHETIC_WINDOW_TARGET_7788'
URL = 'https://images.example/synthetic.png'


def _stage():
    """导入上传阶段测试模块，复用其传输替身与合成响应。"""
    return import_module('test_upload_stage_logging')


def _capture_stage():
    """导入截图阶段测试模块，复用其 ADB 采集替身。"""
    return import_module('test_screenshot_stage_logging')


def _support():
    """导入终端状态测试模块，复用其既有测试辅助。"""
    return import_module('test_upload_status')


def _pipeline():
    """导入真实截图编排模块。"""
    return import_module('modules.screenshot.pipeline')


def _cli():
    """导入真实截图命令行模块。"""
    return import_module('modules.screenshot.cli')


def _notif():
    """导入真实通知模块。"""
    return import_module('modules.notification')


def _logger_mod():
    """导入真实日志模块。"""
    return import_module('modules.logger')


def _registry():
    """导入真实图床注册表模块。"""
    return import_module('modules.image_host.registry')


def _log_safety():
    """导入真实日志安全模块。"""
    return import_module('modules.log_safety')


def _silent_consumer():
    """构造显示写入丢弃流的真实消费者，用于关闭状态回调的变体。"""
    return import_module('modules.upload_status').make_status_consumer(
        stream=StringIO())


class ScopedConsumer:
    """把仅回调事件的函数包装为满足上传阶段合同的对象。"""

    def __init__(self, callback):
        """保存被包装的事件回调。"""
        self._callback = callback

    def _enter_phase(self):
        """返回空阶段帧，允许上传阶段正常进入。"""
        return None

    def _exit_phase(self, frame):
        """忽略阶段帧，不影响控制信号传播。"""

    def __call__(self, event):
        """把事件转发给被包装回调。"""
        self._callback(event)


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """隔离缓存根与根输出，并禁止任何未替身化的真实网络访问。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))

    def forbidden(*args, **kwargs):
        """未替身化网络立即令测试失败。"""
        pytest.fail('验收测试禁止真实网络')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)

    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    root.handlers = []
    root.setLevel(logging.DEBUG)
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)


def file_sink(root_dir, name='accept'):
    """在指定目录建立真实文件处理器作为最终字节出口。"""
    handler = _logger_mod()._create_file_handler(str(root_dir / 'logs'), name)
    assert handler is not None
    return handler


def read_text(handler):
    """读取真实文件处理器的最终字节并解码为文本。"""
    handler.flush()
    with open(handler.baseFilename, 'rb') as stream:
        return stream.read().decode('utf-8')


def notification_config():
    """构造经真实注册表处理的单目标单通道合成配置。"""
    return {
        'push': {
            'screenshot': {
                'targets': [{'provider': 'window', 'target': TARGET}],
                'image_host': [{'provider': 'beeimg_cn', 'token': HOST_SECRET}],
            },
            'templates': {
                'on_end': {
                    'enable': True,
                    'capture_screenshot': True,
                    'title': '标题 {process_name}',
                    'content': '正文 {process_name}\n\n{screenshot}',
                },
            },
            'push_channel_settings': {
                'channels': [{'provider': 'serverchan', 'sckey': CHANNEL_SECRET}],
            },
            'retry': {'interval': '0s', 'max_count': 1},
        },
    }


def run_notification(monkeypatch, root_dir, *, handler, failed=False):
    """执行一次真实通知链路并把最终字节与真实事实一并返回。"""
    notif = _notif()
    pipeline = _pipeline()
    stage = _stage()
    support = _support()
    monkeypatch.setenv('LOCALAPPDATA', str(root_dir / 'local'))
    monkeypatch.setattr(
        pipeline, 'capture', Mock(return_value=support.capture_result()))
    monkeypatch.setattr(pipeline, 'save_automatic', Mock(
        return_value=SaveOutcome(None, 'shot.jpg')))
    transport = stage.Transport(monkeypatch, [
        stage.group_reply(), stage.profile_reply(),
        (200, (HOST_SECRET + ' ' + CHANNEL_SECRET).encode())
        if failed else stage.SUCCESS_UPLOAD])

    sent = []
    notifier = Mock()

    def notify(title=None, content=None, **params):
        """记录待发送标题正文并返回合成成功响应。"""
        assert params['sckey'] == CHANNEL_SECRET
        logging.getLogger().warning('合成凭证 %s %s', HOST_SECRET, CHANNEL_SECRET)
        sent.append((title, content))
        response = Mock()
        response.status_code = 200
        response.json.return_value = {'code': 0}
        return response

    notifier.notify.side_effect = notify
    monkeypatch.setattr(notif, 'get_notifier', Mock(return_value=notifier))

    cache_keys = []
    real_identity = StorageCache.identity

    def recording_identity(self, provider, token):
        """返回真实缓存键并记录，供最终字节断言使用。"""
        value = real_identity(self, provider, token)
        if value:
            cache_keys.append(value)
        return value

    monkeypatch.setattr(StorageCache, 'identity', recording_identity)

    events, traces = [], []
    support.spy_trace_scope(monkeypatch, pipeline, events, traces)

    root = logging.getLogger()
    root.addHandler(handler)
    try:
        results = notif.send_notification(
            notification_config(), 'on_end', process_name=MARKER)
    finally:
        root.removeHandler(handler)
    return {
        'sent': sent, 'results': results, 'events': events, 'traces': traces,
        'transport': transport, 'cache_keys': cache_keys,
        'file_text': read_text(handler),
    }


def private_markers(outcome, root_dir):
    """给出本次注入的私有标记：凭证、target、路径、缓存键与完整链接。"""
    return (HOST_SECRET, CHANNEL_SECRET, TARGET, str(root_dir),
            outcome['cache_keys'][0], URL)


@pytest.mark.parametrize('failed', [False, True])
def test_notification_to_upload_safe_final_bytes(monkeypatch, tmp_path, failed):
    """通知到上传链路：最终字节无私有标记，批次唯一，计数与交付逐字一致。"""
    outcome = run_notification(
        monkeypatch, tmp_path, handler=file_sink(tmp_path), failed=failed)
    summary = outcome['traces'][0].summary()
    final_file_text = outcome['file_text']
    transport = outcome['transport']

    assert all(marker not in final_file_text
               for marker in private_markers(outcome, tmp_path))
    batch_final_count = sum('生成成功' in line
                            for line in final_file_text.splitlines())
    assert batch_final_count == 1
    actual_get_count = len(transport.gets('group')) + len(
        transport.gets('profile'))
    actual_post_count = len(transport.posts)
    assert actual_get_count == summary['group_get_attempts'] + summary[
        'profile_get_attempts']
    assert actual_post_count == summary['upload_post_attempts']
    assert transport.posts[0].headers['Authorization'] == 'Bearer ' + HOST_SECRET
    assert CHANNEL_SECRET not in str(transport.posts[0].headers)
    sent_payload = outcome['sent'][0]
    assert sent_payload[0] == f'标题 {MARKER}'
    assert sent_payload[1].startswith(f'正文 {MARKER}\n\n')
    if failed:
        assert '截图上传失败：' in sent_payload[1]
        assert '![' not in sent_payload[1]
        assert (summary['slots_failed'], summary['slots_succeeded']) == (1, 0)
    else:
        assert sent_payload[1] == f'正文 {MARKER}\n\n![screenshot_1]({URL})'
    assert HOST_SECRET not in sent_payload[1]
    assert CHANNEL_SECRET not in sent_payload[1]


def test_acceptance_detects_missing_safety_layer(monkeypatch, tmp_path):
    """临时移除安全 formatter 后原验收断言必须失败，恢复后必须通过。"""
    with monkeypatch.context() as broken:
        broken.setattr(
            _log_safety(), 'redact_text', lambda text, secrets=None: text)
        broken.setattr(
            import_module('modules.notification'), 'redact_text',
            lambda text, secrets=None: text)
        outcome = run_notification(
            broken, tmp_path / 'broken',
            handler=file_sink(tmp_path / 'broken'))
        assert HOST_SECRET in outcome['file_text']
        with pytest.raises(AssertionError):
            assert all(marker not in outcome['file_text']
                       for marker in private_markers(outcome, tmp_path / 'broken'))

    outcome = run_notification(
        monkeypatch, tmp_path / 'intact', handler=file_sink(tmp_path / 'intact'))
    assert all(marker not in outcome['file_text']
               for marker in private_markers(outcome, tmp_path / 'intact'))


def test_cli_mixed_results_and_forbidden_profile(monkeypatch, tmp_path, capsys):
    """命令行混合结果：权限失败零 POST，成功链接保留在结果行且不落盘。"""
    cli = _cli()
    stage = _stage()
    support = _support()
    transport = stage.Transport(monkeypatch, [
        stage.group_reply(), stage.profile_reply(status=403),
        (200, URL.encode())])
    monkeypatch.setattr(
        cli, 'capture', Mock(return_value=support.capture_result()))
    events, traces = [], []
    support.spy_trace_scope(monkeypatch, cli, events, traces)

    handler = file_sink(tmp_path, 'cli')
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        cli_exit_code = cli.run_screenshot_cli(
            support.cli_args(hosts=[
                {'provider': 'beeimg_cn', 'token': MARKER},
                {'provider': 'catbox', 'token': ''}]),
            str(tmp_path))
    finally:
        root.removeHandler(handler)

    final_file_text = read_text(handler)
    result_stdout = capsys.readouterr().out
    summary = traces[0].summary()
    successful_url = URL

    assert cli_exit_code == 1
    assert successful_url in result_stdout and successful_url not in final_file_text
    assert final_file_text.count('截图批次') == 2
    assert final_file_text.count('生成成功') == 1
    actual_get_count = len(transport.gets('group')) + len(
        transport.gets('profile'))
    actual_post_count = len(transport.posts)
    assert actual_get_count == summary['group_get_attempts'] + summary[
        'profile_get_attempts']
    assert actual_post_count == summary['upload_post_attempts']
    assert (summary['slots_failed'], summary['slots_succeeded']) == (1, 1)
    assert all(marker not in final_file_text
               for marker in (MARKER, str(tmp_path), successful_url))


def run_pipeline(monkeypatch, handler, *, no_callback=False):
    """执行一次真实编排并在给定真实处理器上留下最终字节。"""
    pipeline = _pipeline()
    stage = _stage()
    support = _support()
    monkeypatch.setattr(
        pipeline, 'capture', Mock(return_value=support.capture_result()))
    monkeypatch.setattr(pipeline, 'save_automatic', Mock(
        return_value=SaveOutcome(None, 'shot.jpg')))
    stage.Transport(monkeypatch, [
        stage.group_reply(), stage.profile_reply(), stage.SUCCESS_UPLOAD])
    if no_callback:
        # 关闭状态回调：真实消费者把显示写入丢弃流，上传编排仍完整执行
        monkeypatch.setattr(
            pipeline, 'make_status_consumer',
            lambda **kwargs: _silent_consumer())
    section = {
        'targets': [{'provider': 'window', 'target': TARGET}],
        'image_host': [{'provider': 'beeimg_cn', 'token': MARKER,
                        'options': {'storage_id': 13}}],
    }
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        batch = pipeline.prepare_screenshots(section, True, ())
    finally:
        root.removeHandler(handler)
    return batch, read_text(handler)


@pytest.mark.parametrize('variant', [
    'closed_diagnostics', 'non_tty', 'no_callback', 'spinner_temp',
    'reopen_handler',
])
def test_file_records_complete_across_toggles(
        variant, monkeypatch, tmp_path, capsys):
    """诊断关闭、非 TTY、无回调、spinner 临时 handler 与日志重开都完整落盘。"""
    if variant == 'reopen_handler':
        handler = _logger_mod()._reopen_file_handler(
            str(tmp_path / 'reopen.log'), 0)
    else:
        handler = file_sink(tmp_path, variant)

    if variant == 'no_callback':
        batch, text = run_pipeline(monkeypatch, handler, no_callback=True)
    elif variant == 'spinner_temp':
        spinner = import_module('modules.spinner')
        with patch.object(spinner.sys.stdout, 'isatty', return_value=True):
            with spinner.spinner_phase('等待中...'):
                batch, text = run_pipeline(monkeypatch, handler)
    else:
        batch, text = run_pipeline(monkeypatch, handler)

    assert text.count('截图批次') == 2
    assert text.count('生成成功') == 1
    assert all(marker not in text for marker in (MARKER, TARGET, str(tmp_path), URL))
    assert batch.values['screenshot_1'].startswith('![')
    if variant == 'non_tty':
        assert '\x1b' not in capsys.readouterr().out


def _assert_file_failure_keeps_success(monkeypatch, tmp_path, capsys):
    """文件写入失败时业务成功保留，无递归、无原始 stderr，不声称已持久化。"""
    pipeline = _pipeline()
    stage = _stage()
    support = _support()
    monkeypatch.setattr(
        pipeline, 'capture', Mock(return_value=support.capture_result()))
    monkeypatch.setattr(pipeline, 'save_automatic', Mock(
        return_value=SaveOutcome(None, 'shot.jpg')))
    stage.Transport(monkeypatch, [
        stage.group_reply(), stage.profile_reply(), stage.SUCCESS_UPLOAD])
    handler = file_sink(tmp_path, 'iofail')
    handler.stream.close()

    root = logging.getLogger()
    root.addHandler(handler)
    try:
        batch = pipeline.prepare_screenshots(
            {'targets': [{'provider': 'window', 'target': TARGET}],
             'image_host': [{'provider': 'beeimg_cn', 'token': MARKER,
                             'options': {'storage_id': 13}}]},
            True, ())
    finally:
        root.removeHandler(handler)

    captured = capsys.readouterr()
    assert batch.values['screenshot_1'] == f'![screenshot_1]({URL})'
    assert captured.err == ''
    assert '持久化成功' not in captured.out + captured.err


@pytest.mark.parametrize('operation, signal_type', [
    ('capture', KeyboardInterrupt), ('capture', SystemExit),
    ('save', KeyboardInterrupt), ('save', SystemExit),
    ('query', KeyboardInterrupt), ('query', SystemExit),
    ('upload', KeyboardInterrupt), ('upload', SystemExit),
    (None, None),
], ids=['capture_kb', 'capture_exit', 'save_kb', 'save_exit', 'query_kb',
        'query_exit', 'upload_kb', 'upload_exit', 'file_io_failure'])
def test_cancellation_and_file_failure_preserve_facts(
        operation, signal_type, monkeypatch, tmp_path, capsys):
    """控制信号原对象传播、已完成终态不被改写、剩余项记为跳过。"""
    if operation is None:
        _assert_file_failure_keeps_success(monkeypatch, tmp_path, capsys)
        return

    error = signal_type('synthetic-stop')
    pipeline = _pipeline()
    stage = _stage()
    support = _support()
    monkeypatch.setattr(pipeline, 'save_automatic', Mock(
        return_value=SaveOutcome(None, 'shot.jpg')))
    if operation in ('capture', 'save'):
        monkeypatch.setattr(
            pipeline, 'capture', Mock(return_value=support.capture_result()))
        section = {
            'targets': [{'provider': 'window', 'target': TARGET},
                        {'provider': 'window', 'target': TARGET + '_2'}],
            'image_host': [{'provider': 'catbox', 'token': ''}],
        }
        replies = [(200, URL.encode())]
        if operation == 'capture':
            monkeypatch.setattr(pipeline, 'capture', Mock(
                side_effect=[support.capture_result(), error]))
        else:
            monkeypatch.setattr(pipeline, 'save_automatic', Mock(
                side_effect=[SaveOutcome(None, 'shot.jpg'), error]))
        expected = {'slots_planned': 2, 'slots_succeeded': 1,
                    'slots_failed': 0, 'slots_cancelled': 0, 'slots_skipped': 1,
                    'terminal': ['started', 'succeeded']}
    else:
        monkeypatch.setattr(
            pipeline, 'capture', Mock(return_value=support.capture_result()))
        section = {
            'targets': [{'provider': 'window', 'target': TARGET}],
            'image_host': [{'provider': 'catbox', 'token': ''},
                           {'provider': 'beeimg_cn', 'token': MARKER,
                            'options': {'storage_id': 13}},
                           {'provider': 'wmimg', 'token': ''}],
        }
        if operation == 'query':
            replies = [(500, b''), error]
        else:
            replies = [(500, b''), stage.group_reply(),
                       stage.profile_reply(), error]
        expected = {'slots_planned': 3, 'slots_succeeded': 0,
                    'slots_failed': 1, 'slots_cancelled': 1, 'slots_skipped': 1,
                    'terminal': ['started', 'failed']}
    stage.Transport(monkeypatch, replies)
    events, traces = [], []
    support.spy_trace_scope(monkeypatch, pipeline, events, traces)

    with pytest.raises(signal_type) as caught:
        pipeline.prepare_screenshots(section, True, ())

    assert caught.value is error
    summary = traces[0].summary()
    assert summary['status'] == 'cancelled'
    for key, value in expected.items():
        if key != 'terminal':
            assert summary[key] == value
    completed = [event.status for event in events if event.stage == 'slot'
                 and (event.image_index, event.slot_index) == (1, 0)]
    assert completed == expected['terminal']
    assert [event.status for event in events if event.stage == 'batch'] == [
        'started', 'cancelled']


def test_review_regressions_run_jointly(monkeypatch, tmp_path):
    """四点回归联合运行：共享预算、Boltp 零 GET、ADB 计数与 merge 覆盖合法。"""
    stage = _stage()
    capture_stage = _capture_stage()
    backend = capture_stage.adb_backend()

    boltp = stage.run_chain(
        monkeypatch, tmp_path,
        [{'provider': 'boltp', 'token': '',
          'options': {'storage_id': 0}}],
        replies=[stage.REJECTED_UPLOAD, stage.REJECTED_UPLOAD,
                 stage.SUCCESS_UPLOAD])
    assert stage.storage_ids(boltp.transport) == [0, 2, 3]
    assert (boltp.summary['group_get_attempts'],
            boltp.summary['profile_get_attempts']) == (0, 0)
    assert boltp.summary['upload_post_attempts'] == 3
    assert boltp.summary['boltp_candidate_switches'] == 2

    cache = StorageCache(tmp_path / 'cache', clock=lambda: 1000.0)
    identity = cache.identity('beeimg_cn', MARKER)
    assert cache.save(identity, StorageMetadata((13, 14), 14, None, 1000.0))
    context = UploadContext(cache=cache, clock=lambda: 1000.0)
    transport = stage.Transport(monkeypatch, [
        stage.group_reply(), stage.profile_reply(default=None),
        stage.REJECTED_UPLOAD])
    events = []
    with trace_scope(mode='cli', on_status=events.append) as trace:
        with trace_item(image_index=1):
            result = _registry().upload_with_fallback(
                b'image', 'image.png',
                [{'provider': 'beeimg_cn', 'token': MARKER,
                  'options': {'storage_id': 99}}], context=context)
        summary = trace.summary()
    context.close()
    assert not result.success
    assert (summary['group_get_attempts'], summary['profile_get_attempts'],
            summary['upload_post_attempts']) == (1, 1, 1)
    assert summary['preupload_refreshes'] == 1
    assert summary['postupload_recoveries'] == 0
    assert len(transport.posts) == 1
    fallback = [(event.fallback_kind, event.status, event.reason_code)
                for event in events
                if event.stage == 'fallback' and event.status != 'started']
    assert ('postupload_recovery', 'skipped', 'budget_used') in fallback

    result, adb_summary, read = capture_stage.run_capture(
        backend, monkeypatch,
        [b'bad-one', b'bad-two', capture_stage.make_png()])
    assert result.image_format == 'jpeg'
    assert (adb_summary['capture_started'], adb_summary['capture_failed'],
            adb_summary['capture_succeeded']) == (3, 2, 1)
    assert adb_summary['capture_format_fallbacks'] == 1
    assert [item.args for item in read.call_args_list] == [
        (capture_stage.SERIAL, False), (capture_stage.SERIAL, False),
        (capture_stage.SERIAL, True)]

    raw, png = capture_stage.make_raw(), capture_stage.make_png()
    read = Mock(side_effect=[raw, raw, png])
    monkeypatch.setattr(backend, '_read_capture', read)
    capture_stage.fail_first_saves(monkeypatch, 2)
    with trace_scope(mode='automatic', images_planned=1) as trace:
        with trace_item(image_index=1):
            backend.capture_adb(capture_stage.SERIAL, purpose='automatic')
        trace.finish_image(1, 'succeeded')
        retry_summary = trace.summary()
    assert (retry_summary['capture_started'], retry_summary['capture_succeeded'],
            retry_summary['capture_failed']) == (3, 3, 0)
    assert (retry_summary['encode_started'], retry_summary['encode_succeeded'],
            retry_summary['encode_failed']) == (3, 1, 2)
    assert retry_summary['capture_retries'] == 1
    assert retry_summary['capture_format_fallbacks'] == 1

    host = parse_inline_hosts(
        '<<: {provider: catbox, token: base}, token: `override`')[0]
    assert host['token'] == 'override' and host['provider'] == 'catbox'
    with pytest.raises(InlineUploadError):
        parse_inline_hosts('provider: catbox, token: a, token: b')


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('boundary', [
    'profile', 'storage_select', 'metadata_cache', 'postupload_recovery',
])
def test_joint_started_cancellation(monkeypatch, tmp_path, boundary, signal_type):
    """真实链路在查询、选择、缓存或恢复开始取消，保留已发请求与槽位事实。"""
    stage = _stage()
    error = signal_type('synthetic-cancel')
    replies = [] if boundary == 'metadata_cache' else [
        stage.group_reply(), stage.profile_reply()]
    if boundary == 'profile':
        replies = [stage.group_reply()]
    if boundary == 'postupload_recovery':
        replies.append(stage.REJECTED_UPLOAD)
    transport = stage.Transport(monkeypatch, replies)
    pipeline = _pipeline()
    monkeypatch.setattr(pipeline, 'capture', Mock(return_value=_support().capture_result()))
    monkeypatch.setattr(pipeline, 'save_automatic', Mock(
        return_value=SaveOutcome(None, 'shot.jpg')))
    events, traces = [], []

    def consume(event):
        """只在指定真实阶段开始时抛出同一个控制信号。"""
        match = (event.stage == boundary or
                 boundary == 'profile' and event.stage == 'metadata_query'
                 and event.action == 'profile' or
                 event.fallback_kind == boundary)
        if match and event.status == 'started':
            raise error

    monkeypatch.setattr(
        pipeline, 'make_status_consumer',
        lambda **kwargs: ScopedConsumer(consume))
    _support().spy_trace_scope(monkeypatch, pipeline, events, traces)
    with pytest.raises(signal_type) as caught:
        pipeline.prepare_screenshots({
            'targets': [{'provider': 'window', 'target': TARGET}],
            'image_host': [{'provider': 'beeimg_cn', 'token': HOST_SECRET},
                           {'provider': 'catbox', 'token': CHANNEL_SECRET}],
        }, True, ())
    assert caught.value is error
    assert not transport.replies
    summary = traces[0].summary()
    assert summary['group_get_attempts'] == len(transport.gets('group'))
    assert summary['profile_get_attempts'] == len(transport.gets('profile'))
    assert summary['upload_post_attempts'] == len(transport.posts)
    assert summary['slots_cancelled'] == 1
    assert summary['slots_skipped'] == 1
    assert summary['slots_failed'] == summary['slots_succeeded'] == 0
    assert [event.status for event in events if event.stage == 'batch'] == [
        'started', 'cancelled']
    cancelled = [event for event in events if event.status == 'cancelled'
                 and event.stage != 'batch']
    assert cancelled
    for event in cancelled:
        assert sum(item.status == 'started' and item.operation_id == event.operation_id
                   for item in events) == 1
    if boundary == 'profile':
        assert summary['profile_get_attempts'] == 0
        assert summary['profile_get_cancelled'] == 0


def test_joint_automatic_slots_reuse_existing_transport(monkeypatch, tmp_path):
    """复用真实传输辅助，核对自动模式两图槽位和尝试号独立复位。"""
    stage = _stage()
    pipeline = _pipeline()
    monkeypatch.setattr(pipeline, 'capture', Mock(return_value=_support().capture_result()))
    monkeypatch.setattr(pipeline, 'save_automatic', Mock(
        return_value=SaveOutcome(None, 'shot.jpg')))
    transport = stage.Transport(monkeypatch, [stage.REJECTED_UPLOAD,
        stage.REJECTED_UPLOAD, stage.SUCCESS_UPLOAD, stage.SUCCESS_UPLOAD])
    events, traces = [], []
    _support().spy_trace_scope(monkeypatch, pipeline, events, traces)
    pipeline.prepare_screenshots({
        'targets': [{'provider': 'window', 'target': TARGET}] * 2,
        'image_host': [{'provider': 'boltp'}, {'provider': 'boltp'}],
    }, True, ())
    started = [event for event in events if event.stage == 'upload_attempt'
               and event.status == 'started']
    assert [(event.image_index, event.slot_index, event.attempt_index)
            for event in started] == [(1, 0, 1), (1, 0, 2), (1, 1, 1), (2, 0, 1)]
    summary = traces[0].summary()
    assert len(transport.posts) == summary['upload_post_attempts'] == 4
    assert (summary['slots_planned'], summary['slots_failed'],
            summary['slots_succeeded'], summary['slots_skipped']) == (4, 1, 2, 1)
