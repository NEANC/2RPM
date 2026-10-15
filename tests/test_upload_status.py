#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证终端上传状态显示与批次汇总，全部外部操作使用本地替身。"""

from argparse import Namespace
from contextlib import contextmanager
from importlib import import_module
from io import StringIO
import logging
import socket
from unittest.mock import Mock, patch

import pytest
import requests

from modules.image_host.core import ImageHostError
from modules.operation_trace import StageEvent
from modules.screenshot.models import CaptureResult
from modules.screenshot.retention import SaveOutcome


URL = 'https://example.invalid/result'
SECRET = 'FAKE_STATUS_SECRET_8421'
MISSING_ENV = '${SYNTHETIC_STATUS_MISSING_ENV}'


def pipeline_module():
    """导入截图编排模块。"""
    return import_module('modules.screenshot.pipeline')


def cli_module():
    """导入截图 CLI 模块。"""
    return import_module('modules.screenshot.cli')


def registry_module():
    """导入图床注册表模块。"""
    return import_module('modules.image_host.registry')


def capture_result(name='窗口'):
    """构造最小合成截图结果，不访问设备。"""
    return CaptureResult(b'png-bytes', 'window', name, 2, 3)


def cli_args(output=None, hosts=None):
    """构造截图 CLI 参数命名空间。"""
    return Namespace(source='window', target='窗口', output=output, hosts=hosts)


def slot_event(image_index, slot_index, provider, status, reason_code=None):
    """构造最小安全槽位事件。"""
    return StageEvent(image_index=image_index, slot_index=slot_index,
                      stage='slot', action='upload', provider=provider,
                      status=status, reason_code=reason_code)


def spy_trace_scope(monkeypatch, module, events, traces):
    """包装编排入口作用域以记录真实事件与批次对象。"""
    real = module.trace_scope

    @contextmanager
    def scope(**kwargs):
        """记录事件后仍交给生产消费者，两条出口互不影响。"""
        inner = kwargs.get('on_status')

        def combined(event):
            """先记录事件，再转发给生产显示回调。"""
            events.append(event)
            if inner is not None:
                inner(event)

        kwargs['on_status'] = combined
        with real(**kwargs) as trace:
            traces.append(trace)
            yield trace

    monkeypatch.setattr(module, 'trace_scope', scope)


def terminal_lines(text):
    """统计最终上传终态显示行数，跳过与取消不计入。"""
    return [line for line in text.splitlines()
            if line == '上传成功' or line.startswith('上传失败：')]


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """隔离缓存根并禁止任何未替身化的真实网络访问。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.delenv('SYNTHETIC_STATUS_MISSING_ENV', raising=False)

    def forbidden(*args, **kwargs):
        """未替身化网络立即令测试失败。"""
        pytest.fail('状态测试禁止真实网络')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', forbidden)


def test_tty_reuses_outer_spinner():
    """TTY 下复用外层 spinner 的安全写入，不新建嵌套 spinner。"""
    spinner_mod = import_module('modules.spinner')
    upload_status = import_module('modules.upload_status')
    outer = Mock()
    with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
            patch.object(spinner_mod, '_SimpleTTYSpinner',
                         return_value=outer) as factory:
        with spinner_mod.spinner_phase('等待中...'):
            consume = upload_status.make_status_consumer()
            consume(slot_event(1, 0, 'beeimg_cn', 'started'))
            consume(slot_event(1, 0, 'beeimg_cn', 'succeeded'))
    factory.assert_called_once()
    assert spinner_mod.current_spinner() is None
    assert [call.args[0] for call in outer.write.call_args_list] == [
        '截图 1：准备上传',
        '截图 1 / 图床 1 beeimg_cn：正在上传',
        '上传成功',
    ]


def test_non_tty_shows_status_with_muted_logger():
    """非 TTY 直接输出安全普通行，无 ANSI 且不依赖被静音的控制台。"""
    upload_status = import_module('modules.upload_status')
    root = logging.getLogger()
    saved = root.handlers[:]
    muted = logging.StreamHandler(StringIO())
    muted.setLevel(logging.CRITICAL + 1)
    root.handlers = [muted]
    stream = StringIO()
    try:
        consume = upload_status.make_status_consumer(stream=stream)
        consume(slot_event(1, 0, 'catbox', 'started'))
        consume(slot_event(1, 0, 'catbox', 'failed', 'missing_environment'))
    finally:
        root.handlers = saved
    text = stream.getvalue()
    assert '截图 1：准备上传' in text
    assert '截图 1 / 图床 1 catbox：正在上传' in text
    assert '上传失败：missing_environment' in text
    assert '\x1b' not in text


def test_display_failure_cannot_reupload(monkeypatch, tmp_path):
    """显示回调抛普通异常被忽略，不造成重新上传。"""
    pipeline = pipeline_module()
    uploads = []

    def uploader(image, filename, token, options):
        """记录一次真实上传调用并返回合成链接。"""
        uploads.append(filename)
        return URL

    monkeypatch.setitem(registry_module().UPLOADERS, 'catbox', uploader)
    monkeypatch.setattr(pipeline, 'capture', Mock(return_value=capture_result()))
    monkeypatch.setattr(pipeline, 'save_automatic', Mock(
        return_value=SaveOutcome(None, 'shot.jpg')))

    def broken_consumer(**kwargs):
        """返回必然报错的显示回调。"""
        def consume(event):
            """模拟终端显示失败。"""
            raise RuntimeError(SECRET)
        return consume

    monkeypatch.setattr(pipeline, 'make_status_consumer', broken_consumer)
    batch = pipeline.prepare_screenshots(
        {'targets': [{'provider': 'window', 'target': '窗口'}],
         'image_host': [{'provider': 'catbox', 'token': 't'}]}, True, ())
    assert len(uploads) == 1
    assert batch.values['screenshot_1'] == f'![screenshot_1]({URL})'


def test_cli_success_url_contract_unchanged(monkeypatch, tmp_path, capsys):
    """结果行原样保留成功链接，状态与文件日志都不得出现该链接。"""
    cli = cli_module()
    logger_module = import_module('modules.logger')

    def uploader(image, filename, token, options):
        """返回合成成功链接，不发起网络请求。"""
        return URL

    monkeypatch.setitem(registry_module().UPLOADERS, 'catbox', uploader)
    monkeypatch.setattr(cli, 'capture', Mock(return_value=capture_result()))
    handler = logger_module._create_file_handler(str(tmp_path / 'logs'), 'app')
    assert handler is not None
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        code = cli.run_screenshot_cli(
            cli_args(hosts=[{'provider': 'catbox', 'token': 't'}]),
            str(tmp_path))
    finally:
        root.removeHandler(handler)
    path = handler.baseFilename
    handler.close()
    with open(path, 'rb') as stream:
        file_text = stream.read().decode('utf-8')
    result_stdout = capsys.readouterr().out
    assert code == 0
    assert 'catbox：上传成功 https://example.invalid/result\n' in result_stdout
    status_text = '\n'.join(
        line for line in result_stdout.splitlines()
        if not line.startswith('catbox：'))
    assert '截图 1：准备上传' in status_text
    assert '截图 1 / 图床 1 catbox：正在上传' in status_text
    assert '\x1b' not in status_text
    assert URL not in status_text + file_text


def test_pipeline_failover_records_each_slot(monkeypatch, capsys):
    """同一图片两图床先败后成：槽位一败一成，批次仍完成并显示故障转移。"""
    pipeline = pipeline_module()

    def failing(image, filename, token, options):
        """首站返回固定传输失败。"""
        raise ImageHostError('transport_failed', '')

    def winning(image, filename, token, options):
        """次站返回合成成功链接。"""
        return URL

    monkeypatch.setitem(registry_module().UPLOADERS, 'catbox', failing)
    monkeypatch.setitem(registry_module().UPLOADERS, 'beeimg', winning)
    monkeypatch.setattr(pipeline, 'capture', Mock(return_value=capture_result()))
    monkeypatch.setattr(pipeline, 'save_automatic', Mock(
        return_value=SaveOutcome(None, 'shot.jpg')))
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)
    batch = pipeline.prepare_screenshots(
        {'targets': [{'provider': 'window', 'target': '窗口'}],
         'image_host': [{'provider': 'catbox', 'token': 't'},
                        {'provider': 'beeimg', 'token': 't'}]}, True, ())
    summary = traces[0].summary()
    assert batch.values['screenshot_1'] == f'![screenshot_1]({URL})'
    assert summary['slots_failed'] == 1
    assert summary['slots_succeeded'] == 1
    assert summary['slots_planned'] == 2
    assert summary['status'] == 'completed'
    out = capsys.readouterr().out
    assert '截图 1：准备上传' in out
    assert '截图 1 / 图床 1 catbox：正在上传' in out
    assert '上传失败，尝试下一图床' in out
    assert '截图 1 / 图床 2 beeimg：正在上传' in out
    assert out.count('上传成功') == 1
    assert '\x1b' not in out


def test_cli_two_hosts_slots_not_folded(monkeypatch, tmp_path, capsys):
    """CLI 两个图床先败后成：槽位按真实配置项序号分为一败一成。"""
    cli = cli_module()
    calls = []

    def failing(image, filename, token, options):
        """首项返回固定传输失败。"""
        calls.append('catbox')
        raise ImageHostError('transport_failed', '')

    def winning(image, filename, token, options):
        """次项返回合成成功链接。"""
        calls.append('beeimg')
        return URL

    monkeypatch.setitem(registry_module().UPLOADERS, 'catbox', failing)
    monkeypatch.setitem(registry_module().UPLOADERS, 'beeimg', winning)
    monkeypatch.setattr(cli, 'capture', Mock(return_value=capture_result()))
    events, traces = [], []
    spy_trace_scope(monkeypatch, cli, events, traces)
    code = cli.run_screenshot_cli(
        cli_args(hosts=[{'provider': 'catbox', 'token': 't'},
                        {'provider': 'beeimg', 'token': 't'}]),
        str(tmp_path))
    summary = traces[0].summary()
    assert code == 1
    assert calls == ['catbox', 'beeimg']
    assert summary['slots_planned'] == 2
    assert summary['slots_failed'] == 1
    assert summary['slots_succeeded'] == 1
    assert summary['slots_succeeded'] != 0
    assert summary['status'] == 'partial_failure'
    out = capsys.readouterr().out
    assert 'catbox：上传失败\n' in out
    assert f'beeimg：上传成功 {URL}\n' in out


def test_preupload_failure_displays_one_slot_failure(monkeypatch, capsys):
    """凭证缺失等前置失败零 POST，但仍显示且只显示一次槽位失败。"""
    pipeline = pipeline_module()
    uploads = []

    def uploader(image, filename, token, options):
        """记录任何真实上传调用（前置失败时不得发生）。"""
        uploads.append(filename)
        return URL

    monkeypatch.setitem(registry_module().UPLOADERS, 'catbox', uploader)
    monkeypatch.setattr(pipeline, 'capture', Mock(return_value=capture_result()))
    monkeypatch.setattr(pipeline, 'save_automatic', Mock(
        return_value=SaveOutcome(None, 'shot.jpg')))
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)
    pipeline.prepare_screenshots(
        {'targets': [{'provider': 'window', 'target': '窗口'}],
         'image_host': [{'provider': 'catbox', 'token': MISSING_ENV}]},
        True, ())
    summary = traces[0].summary()
    assert uploads == []
    assert summary['upload_post_attempts'] == 0
    assert summary['slots_failed'] == 1
    assert summary['slots_succeeded'] == 0
    assert summary['status'] == 'failed'
    out = capsys.readouterr().out
    assert terminal_lines(out) == ['上传失败：missing_environment']


def test_recovery_success_does_not_display_intermediate_failure(
        monkeypatch, tmp_path, capsys):
    """精确拒绝后恢复成功只显示一次最终成功，不显示中途请求失败。"""
    support = import_module('test_upload_stage_logging')
    pipeline = pipeline_module()
    monkeypatch.setattr(pipeline, 'capture', Mock(return_value=capture_result()))
    monkeypatch.setattr(pipeline, 'save_automatic', Mock(
        return_value=SaveOutcome(None, 'shot.jpg')))
    support.Transport(monkeypatch, [
        support.group_reply(), support.REJECTED_UPLOAD,
        support.group_reply(), support.SUCCESS_UPLOAD])
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)
    batch = pipeline.prepare_screenshots(
        {'targets': [{'provider': 'window', 'target': '窗口'}],
         'image_host': [{'provider': 'beeimg_cn', 'token': '',
                         'options': {'storage_id': 13}}]}, True, ())
    summary = traces[0].summary()
    assert batch.values['screenshot_1'].startswith('![')
    assert summary['upload_post_attempts'] == 2
    assert summary['postupload_recoveries'] == 1
    assert summary['slots_succeeded'] == 1
    assert summary['slots_failed'] == 0
    out = capsys.readouterr().out
    assert terminal_lines(out) == ['上传成功']


@pytest.mark.parametrize('mode', ['automatic', 'multi_image', 'cli',
                                  'save_only', 'no_host'])
def test_batch_summary_matches_requested_results(
        monkeypatch, tmp_path, mode):
    """各模式槽位计划量等于各终态之和，且业务终态与结果一致。"""
    if mode == 'cli':
        cli = cli_module()
        monkeypatch.setitem(
            registry_module().UPLOADERS, 'catbox',
            lambda image, filename, token, options: URL)
        monkeypatch.setattr(cli, 'capture', Mock(return_value=capture_result()))
        events, traces = [], []
        spy_trace_scope(monkeypatch, cli, events, traces)
        code = cli.run_screenshot_cli(
            cli_args(hosts=[{'provider': 'catbox', 'token': 't'}]),
            str(tmp_path))
        assert code == 0
    elif mode == 'save_only':
        cli = cli_module()
        monkeypatch.setattr(cli, 'capture', Mock(return_value=capture_result()))
        monkeypatch.setattr(cli, 'save_cli', Mock(
            return_value=SaveOutcome(str(tmp_path / 'shot.jpg'), 'shot.jpg')))
        events, traces = [], []
        spy_trace_scope(monkeypatch, cli, events, traces)
        code = cli.run_screenshot_cli(
            cli_args(output=str(tmp_path / 'shot.jpg')), str(tmp_path))
        assert code in (0, 1)
    else:
        pipeline = pipeline_module()
        monkeypatch.setitem(
            registry_module().UPLOADERS, 'catbox',
            lambda image, filename, token, options: URL)
        monkeypatch.setattr(
            pipeline, 'capture', Mock(return_value=capture_result()))
        monkeypatch.setattr(pipeline, 'save_automatic', Mock(
            return_value=SaveOutcome(None, 'shot.jpg')))
        section = {'targets': [{'provider': 'window', 'target': '窗口'}]}
        if mode == 'multi_image':
            section['targets'].append(
                {'provider': 'adb', 'target': '第二设备'})
            section['image_host'] = [{'provider': 'catbox', 'token': 't'}]
        if mode == 'automatic':
            section['image_host'] = [{'provider': 'catbox', 'token': 't'}]
        events, traces = [], []
        spy_trace_scope(monkeypatch, pipeline, events, traces)
        pipeline.prepare_screenshots(section, True, ())
    summary = traces[0].summary()
    assert summary['slots_planned'] == sum(
        summary['slots_' + state]
        for state in ('succeeded', 'failed', 'cancelled', 'skipped'))
    assert summary['status'] in (
        'completed', 'partial_failure', 'failed', 'cancelled')


@pytest.mark.parametrize('entry', ['pipeline', 'cli'])
def test_cancellation_marks_started_slot_and_skips_rest(
        monkeypatch, tmp_path, entry):
    """控制信号下未完成槽位取消、剩余槽位跳过，计划量与终态之和一致。"""
    signal = KeyboardInterrupt('stop')

    def interrupted(image, filename, token, options):
        """在上传边界触发控制信号。"""
        raise signal

    monkeypatch.setitem(
        registry_module().UPLOADERS, 'catbox', interrupted)
    hosts = [{'provider': 'catbox', 'token': 't'},
             {'provider': 'beeimg', 'token': 't'}]
    module = pipeline_module() if entry == 'pipeline' else cli_module()
    monkeypatch.setattr(
        module, 'capture', Mock(return_value=capture_result()))
    if entry == 'pipeline':
        monkeypatch.setattr(module, 'save_automatic', Mock(
            return_value=SaveOutcome(None, 'shot.jpg')))
    events, traces = [], []
    spy_trace_scope(monkeypatch, module, events, traces)
    with pytest.raises(KeyboardInterrupt) as caught:
        if entry == 'pipeline':
            module.prepare_screenshots(
                {'targets': [{'provider': 'window', 'target': '窗口'}],
                 'image_host': hosts}, True, ())
        else:
            module.run_screenshot_cli(cli_args(hosts=hosts), str(tmp_path))
    assert caught.value is signal
    summary = traces[0].summary()
    assert summary['slots_planned'] == 2
    assert summary['slots_cancelled'] == 1
    assert summary['slots_skipped'] == 1
    assert summary['status'] == 'cancelled'
