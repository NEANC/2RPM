#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证终端上传状态显示与批次汇总，全部外部操作使用本地替身。"""

from argparse import Namespace
from collections import UserDict
from contextlib import contextmanager
from contextvars import copy_context
from copy import deepcopy
from importlib import import_module
from io import StringIO
import logging
import socket
import threading
from unittest.mock import Mock, call, patch

import colorama
import pytest
import requests

from modules.image_host.core import ImageHostError
from modules.log_safety import remove_terminal_sequences
from modules.operation_trace import StageEvent
from modules.screenshot.models import CaptureError
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


def slot_event(batch_id, image_index, slot_index, provider, status,
               reason_code=None):
    """构造带批次身份的最小安全槽位事件。"""
    return StageEvent(batch_id=batch_id, image_index=image_index,
                      slot_index=slot_index, stage='slot', action='upload',
                      provider=provider, status=status, reason_code=reason_code)


def plain_stream(isatty=False):
    """构造记录写入与 flush 的普通输出流替身。"""
    stream = Mock()
    stream.isatty.return_value = isatty
    return stream


def written_lines(stream):
    """按写入顺序还原写入的行（去掉行尾换行）。"""
    return [item.args[0].rstrip('\n') for item in stream.write.call_args_list]


def spy_trace_scope(monkeypatch, module, events, traces, callbacks=None):
    """包装编排入口作用域以记录真实事件、批次对象与被包裹的原回调。"""
    real = module.trace_scope

    @contextmanager
    def scope(**kwargs):
        """记录事件后仍交给生产消费者，两条出口互不影响。"""
        inner = kwargs.get('on_status')
        if callbacks is not None:
            callbacks.append(inner)

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
    """只保留安全上传终态显示行，跳过与取消不显示。"""
    return [line for line in text.splitlines()
            if line.startswith('✔️') or line.startswith('❌')]


class LogDegradeHandle:
    """非 TTY 日志降级句柄替身：只有日志方法，缺借用所需公开属性。

    刻意不定义 ``closed``/``owner_thread_id``/``stream`` 三项，
    以此复现 ``_LogSpinner`` 被当作 ``spinner=`` 传入时的形态。
    """

    def __init__(self):
        """初始化三个记录列表，便于断言句柄确实没被借用。"""
        self.texts = []
        self.dones = []
        self.fails = []

    def text(self, message):
        """记录阶段文案（对应 _LogSpinner.text）。"""
        self.texts.append(message)

    def write(self, message):
        """记录插入文本（对应 _LogSpinner.write）。"""
        self.texts.append(message)

    def write_done(self, message):
        """记录成功收尾文案（对应 _LogSpinner.write_done）。"""
        self.dones.append(message)

    def write_fail(self, message):
        """记录失败收尾文案（对应 _LogSpinner.write_fail）。"""
        self.fails.append(message)


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


def test_slot_identity_and_suffixes(monkeypatch):
    """两图重复槽位各显示一次唯一终态，后缀来自计划量与净化槽位序列。"""
    spinner_mod = import_module('modules.spinner')
    upload_status = import_module('modules.upload_status')
    stream = plain_stream()
    borrowed = Mock(closed=False, owner_thread_id=threading.get_ident(),
                    stream=stream)
    monkeypatch.setattr(upload_status, 'current_spinner', lambda: borrowed)
    with patch.object(spinner_mod, '_SimpleTTYSpinner') as factory:
        consumer = upload_status.make_status_consumer(
            stream=stream, images_planned=2,
            slot_providers=('beeimg_cn', 'beeimg_cn'))
        with upload_status.upload_feedback_scope(consumer):
            for event in (
                    slot_event('a', 1, 0, 'beeimg_cn', 'started'),
                    slot_event('a', 1, 0, 'beeimg_cn', 'failed'),
                    slot_event('a', 1, 1, 'beeimg_cn', 'succeeded'),
                    slot_event('a', 1, 1, 'beeimg_cn', 'succeeded')):
                consumer(event)
        assert written_lines(stream) == [
            '正在上传截图',
            '❌ 通道 [BeeIMG.CN] 上传失败（截图 1）（配置项 1）',
            '✔️ 通道 [BeeIMG.CN] 上传成功（截图 1）（配置项 2）',
        ]
        assert stream.flush.call_count >= 3
        assert factory.call_count == 0
        assert borrowed.done.call_count == 0
        assert borrowed.fail.call_count == 0
        assert borrowed.stop.call_count == 0
        # 另一图另进 scope，身份集合不重置，不同 batch_id 各再来一次
        stream.write.reset_mock()
        with upload_status.upload_feedback_scope(consumer):
            for event in (
                    slot_event('b', 2, 0, 'beeimg_cn', 'started'),
                    slot_event('b', 2, 0, 'beeimg_cn', 'failed'),
                    slot_event('b', 2, 1, 'beeimg_cn', 'succeeded'),
                    slot_event('b', 2, 1, 'beeimg_cn', 'succeeded'),
                    slot_event('b', 2, 2, 'beeimg_cn', 'cancelled'),
                    slot_event('b', 2, 2, 'beeimg_cn', 'failed'),
                    slot_event('b', 2, 3, 'beeimg_cn', 'skipped')):
                consumer(event)
        assert written_lines(stream) == [
            '正在上传截图',
            '❌ 通道 [BeeIMG.CN] 上传失败（截图 2）（配置项 1）',
            '✔️ 通道 [BeeIMG.CN] 上传成功（截图 2）（配置项 2）',
        ]
        # CLI 一图重复 provider 只加配置项，使用实际槽位序号且首条已带后缀
        single = plain_stream()
        single_consumer = upload_status.make_status_consumer(
            stream=single, images_planned=1,
            slot_providers=('catbox', 'catbox'))
        with upload_status.upload_feedback_scope(single_consumer):
            single_consumer(slot_event('c', 1, 0, 'catbox', 'started'))
            single_consumer(slot_event('c', 1, 0, 'catbox', 'succeeded'))
            single_consumer(slot_event('c', 1, 1, 'catbox', 'started'))
            single_consumer(slot_event('c', 1, 1, 'catbox', 'failed'))
        assert written_lines(single) == [
            '正在上传截图',
            '✔️ 通道 [Catbox] 上传成功（配置项 1）',
            '❌ 通道 [Catbox] 上传失败（配置项 2）',
        ]
        # 未知 provider 始终安全名称加槽位数字，绝不回显原文
        unknown = plain_stream()
        unknown_consumer = upload_status.make_status_consumer(
            stream=unknown, slot_providers=('unknown',))
        with upload_status.upload_feedback_scope(unknown_consumer):
            unknown_consumer(
                slot_event('d', 1, 3, 'token=' + SECRET, 'started'))
            unknown_consumer(
                slot_event('d', 1, 3, 'token=' + SECRET, 'failed'))
        assert written_lines(unknown) == [
            '正在上传截图', '❌ 通道 [未知图床 第 4 项] 上传失败']
        assert SECRET not in ''.join(written_lines(unknown))


def test_scope_laziness_and_late_terminal(monkeypatch):
    """首个 slot.started 才懒启动，叠加 scope 复用活动资源且空 scope 不显示。"""
    spinner_mod = import_module('modules.spinner')
    upload_status = import_module('modules.upload_status')
    stream = plain_stream()
    borrowed = Mock(closed=False, owner_thread_id=threading.get_ident(),
                    stream=stream)
    monkeypatch.setattr(upload_status, 'current_spinner', lambda: borrowed)
    with patch.object(spinner_mod, '_SimpleTTYSpinner') as factory:
        consumer = upload_status.make_status_consumer(stream=stream)
        # 阶段外 started 不开动画，阶段外终态只普通行
        consumer(slot_event('a', 1, 0, 'catbox', 'started'))
        consumer(slot_event('a', 1, 0, 'catbox', 'failed'))
        assert written_lines(stream) == ['❌ 通道 [Catbox] 上传失败']
        assert '正在上传截图' not in written_lines(stream)
        assert '\x1b' not in ''.join(written_lines(stream))
        assert factory.call_count == 0
        stream.write.reset_mock()
        # 空 scope 无槽位事件，不显示
        with upload_status.upload_feedback_scope(consumer):
            pass
        assert stream.write.call_count == 0
        # 活动 scope 内首个 started 才进入阶段
        with upload_status.upload_feedback_scope(consumer):
            consumer(slot_event('a', 2, 0, 'catbox', 'started'))
            consumer(slot_event('a', 2, 0, 'catbox', 'succeeded'))
        assert written_lines(stream) == [
            '正在上传截图', '✔️ 通道 [Catbox] 上传成功']
        assert factory.call_count == 0
        # TTY 自有 spinner：嵌套 scope 复用同一活动资源，最外层负责关闭
        tty = plain_stream(isatty=True)
        own = Mock()
        with patch.object(spinner_mod, '_SimpleTTYSpinner',
                          return_value=own) as own_factory:
            nested = upload_status.make_status_consumer(stream=tty)
            with upload_status.upload_feedback_scope(nested):
                with upload_status.upload_feedback_scope(nested):
                    nested(slot_event('b', 1, 0, 'catbox', 'started'))
                    nested(slot_event('b', 1, 0, 'catbox', 'succeeded'))
                assert own.stop.call_count == 0
            assert own_factory.call_count == 1
            assert own_factory.call_args_list[0] == call(
                '正在上传截图', output=tty)
            assert own.stop.call_count == 1
            assert own.write.call_args_list == [
                call(colorama.Fore.GREEN + '✔️ 通道 [Catbox] 上传成功'
                     + colorama.Style.RESET_ALL)]
            assert own.fail.call_count == 0
            # 下一次上传再次进入同一阶段，不泄漏也不嵌套第二个活动实例
            with upload_status.upload_feedback_scope(nested):
                nested(slot_event('b', 2, 0, 'catbox', 'started'))
                nested(slot_event('b', 2, 0, 'catbox', 'failed'))
            assert own_factory.call_count == 2


def test_sink_error_and_control_stop(monkeypatch):
    """普通写异常不重播，控制异常先停 UI 再传播且不阻断 trace 核算。"""
    upload_status = import_module('modules.upload_status')
    trace = import_module('modules.operation_trace')

    broken = plain_stream()
    broken.write.side_effect = RuntimeError(SECRET)
    consumer = upload_status.make_status_consumer(stream=broken)
    consumer(slot_event('a', 1, 0, 'catbox', 'failed'))
    assert broken.write.call_count == 1
    consumer(slot_event('a', 1, 0, 'catbox', 'failed'))
    assert broken.write.call_count == 1
    assert SECRET not in str(broken.write.call_args_list)

    signal = KeyboardInterrupt('stop')
    calls = []

    def interrupt(text):
        """记录写入并抛出同一控制信号对象。"""
        calls.append(text)
        raise signal

    sink = plain_stream()
    sink.write.side_effect = interrupt
    consumer2 = upload_status.make_status_consumer(stream=sink)
    with pytest.raises(KeyboardInterrupt) as caught:
        consumer2(slot_event('b', 1, 0, 'catbox', 'failed'))
    assert caught.value is signal
    # 控制异常后后续 UI 全停，但 trace 侧仍然登记真实终态
    consumer2(slot_event('b', 1, 0, 'catbox', 'succeeded'))
    consumer2(slot_event('b', 2, 0, 'catbox', 'failed'))
    assert [item.rstrip('\n') for item in calls] == [
        '❌ 通道 [Catbox] 上传失败']

    calls3 = []

    def interrupt3(text):
        """记录写入并抛出同一控制信号对象。"""
        calls3.append(text)
        raise signal

    sink3 = plain_stream()
    sink3.write.side_effect = interrupt3
    consumer3 = upload_status.make_status_consumer(stream=sink3)
    with pytest.raises(KeyboardInterrupt) as caught3:
        with trace.trace_scope(
                mode='cli', images_planned=1, slots_planned=1,
                on_status=consumer3) as traced:
            traced.start_slot(1, 0, provider='catbox')
            traced.finish_slot(1, 0, 'failed', reason_code='upload_failed')
    assert caught3.value is signal
    assert [item.rstrip('\n') for item in calls3] == [
        '❌ 通道 [Catbox] 上传失败']
    assert traced.summary()['slots_failed'] == 1


def test_wrong_thread_or_stream_handle(monkeypatch):
    """非 TTY 句柄不借用、跨线程不写 UI、显式流限制可借用句柄。"""
    spinner_mod = import_module('modules.spinner')
    upload_status = import_module('modules.upload_status')

    outer = plain_stream()
    selected = plain_stream()
    with spinner_mod.spinner_phase('等待中', stream=outer):
        assert spinner_mod.current_spinner() is None
        consumer = upload_status.make_status_consumer(stream=selected)
        with upload_status.upload_feedback_scope(consumer):
            consumer(slot_event('a', 1, 0, 'catbox', 'started'))
            consumer(slot_event('a', 1, 0, 'catbox', 'succeeded'))
    assert written_lines(selected) == [
        '正在上传截图', '✔️ 通道 [Catbox] 上传成功']
    assert '\x1b' not in ''.join(written_lines(selected))
    assert outer.write.call_count == 0

    cross = plain_stream()
    consumer2 = upload_status.make_status_consumer(stream=cross)
    context = copy_context()

    def run():
        """在外线程用复制的上下文消费事件。"""
        with upload_status.upload_feedback_scope(consumer2):
            consumer2(slot_event('b', 1, 0, 'catbox', 'started'))
            consumer2(slot_event('b', 1, 0, 'catbox', 'succeeded'))

    worker = threading.Thread(target=lambda: context.run(run))
    worker.start()
    worker.join()
    assert cross.write.call_count == 0
    assert cross.flush.call_count == 0

    stdout_handle = Mock(closed=False, owner_thread_id=threading.get_ident(),
                         stream=plain_stream(isatty=True))
    monkeypatch.setattr(upload_status, 'current_spinner',
                        lambda: stdout_handle)
    stderr = plain_stream(isatty=True)
    own = Mock()
    with patch.object(spinner_mod, '_SimpleTTYSpinner',
                      return_value=own) as factory:
        consumer3 = upload_status.make_status_consumer(stream=stderr)
        with upload_status.upload_feedback_scope(consumer3):
            consumer3(slot_event('c', 1, 0, 'catbox', 'started'))
            consumer3(slot_event('c', 1, 0, 'catbox', 'succeeded'))
        assert stdout_handle.write.call_count == 0
        assert stdout_handle.text.call_count == 0
        assert factory.call_count == 1
        assert own.write.call_args_list == [
            call(colorama.Fore.GREEN + '✔️ 通道 [Catbox] 上传成功'
                 + colorama.Style.RESET_ALL)]


def test_tty_reuses_outer_spinner():
    """TTY 下复用外层 spinner：只恢复文案，绝不 done/fail/stop 被借用句柄。"""
    spinner_mod = import_module('modules.spinner')
    upload_status = import_module('modules.upload_status')
    outer = Mock()
    outer._message = '等待中...'
    with patch.object(spinner_mod.sys.stdout, 'isatty', return_value=True), \
            patch.object(spinner_mod, '_SimpleTTYSpinner',
                         return_value=outer) as factory:
        with spinner_mod.spinner_phase('等待中...'):
            consumer = upload_status.make_status_consumer()
            with upload_status.upload_feedback_scope(consumer):
                consumer(slot_event('a', 1, 0, 'beeimg_cn', 'started'))
                consumer(slot_event('a', 1, 0, 'beeimg_cn', 'succeeded'))
    factory.assert_called_once()
    assert spinner_mod.current_spinner() is None
    assert outer.set_text.call_args_list == [
        call('正在上传截图'), call('等待中...')]
    assert outer.write.call_args_list == [
        call(colorama.Fore.GREEN + '✔️ 通道 [BeeIMG.CN] 上传成功'
             + colorama.Style.RESET_ALL)]
    assert outer.done.call_count == 0
    assert outer.fail.call_count == 0


def test_upload_terminal_result_colors():
    """借用真实 TTY 包装器与自建 stderr TTY 都按结果语义着色，重复终态不重播。"""
    spinner_mod = import_module('modules.spinner')
    upload_status = import_module('modules.upload_status')
    slots = ('beeimg_cn', 'beeimg_cn')
    green_line = (colorama.Fore.GREEN
                  + '✔️ 通道 [BeeIMG.CN] 上传成功（截图 1）（配置项 1）'
                  + colorama.Style.RESET_ALL)
    red_line = (colorama.Fore.RED
                + '❌ 通道 [BeeIMG.CN] 上传失败（截图 1）（配置项 2）'
                + colorama.Style.RESET_ALL)
    plain_success = remove_terminal_sequences(green_line)
    plain_fail = remove_terminal_sequences(red_line)

    def drive(consumer, check=None):
        """经真实消费者发送同图不同槽位的成败与重复、静默终态。

        check 会在 upload_feedback_scope 活动期内、全部事件发送完毕后调用，
        供调用方把生命周期断言落在借用句柄真正被使用的活动期内。
        """
        with upload_status.upload_feedback_scope(consumer):
            consumer(slot_event('a', 1, 0, 'beeimg_cn', 'started'))
            consumer(slot_event('a', 1, 0, 'beeimg_cn', 'succeeded'))
            consumer(slot_event('a', 1, 1, 'beeimg_cn', 'failed'))
            consumer(slot_event('a', 1, 0, 'beeimg_cn', 'succeeded'))
            consumer(slot_event('a', 1, 2, 'beeimg_cn', 'cancelled'))
            consumer(slot_event('a', 1, 3, 'beeimg_cn', 'skipped'))
            if check is not None:
                check()

    # 借用外层真实 TTY 包装器：着色在 _TtySpinner 内完成
    bottom = Mock()
    bottom.closed = False
    outer_tty = plain_stream(isatty=True)
    with patch.object(spinner_mod, '_SimpleTTYSpinner', return_value=bottom):
        with spinner_mod.spinner_phase('等待中', stream=outer_tty) as outer:

            def assert_borrowed_open():
                """上传作用域活动期内断言被借用的外层句柄仍未被关闭。"""
                assert outer.closed is False

            consumer = upload_status.make_status_consumer(
                images_planned=2, slot_providers=slots)
            drive(consumer, assert_borrowed_open)
            assert outer.closed is False
    assert bottom.write.call_args_list == [call(green_line), call(red_line)]
    assert [remove_terminal_sequences(item.args[0])
            for item in bottom.write.call_args_list] == [
        plain_success, plain_fail]

    # 自建 stderr TTY：无外层句柄时自建 spinner 阶段并着色
    own_bottom = Mock()
    stderr = plain_stream(isatty=True)
    with patch.object(spinner_mod, '_SimpleTTYSpinner', return_value=own_bottom):
        consumer2 = upload_status.make_status_consumer(
            stream=stderr, images_planned=2, slot_providers=slots)
        drive(consumer2)
    assert own_bottom.write.call_args_list == [call(green_line), call(red_line)]
    assert stderr.write.call_count == 0


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
        consume(slot_event('a', 1, 0, 'catbox', 'started'))
        consume(slot_event('a', 1, 0, 'catbox', 'failed', 'missing_environment'))
    finally:
        root.handlers = saved
    text = stream.getvalue()
    assert '❌ 通道 [Catbox] 上传失败' in text
    assert '正在上传截图' not in text
    assert '\x1b' not in text


def test_handle_missing_borrow_attrs_falls_back_to_plain_lines():
    """缺借用属性的日志降级句柄不被借用，阶段与终态都走普通行并 flush。"""
    upload_status = import_module('modules.upload_status')
    stream = plain_stream()
    handle = LogDegradeHandle()
    consumer = upload_status.make_status_consumer(spinner=handle, stream=stream)
    with upload_status.upload_feedback_scope(consumer):
        consumer(slot_event('a', 1, 0, 'catbox', 'started'))
        consumer(slot_event('a', 1, 0, 'catbox', 'succeeded'))
    assert written_lines(stream) == [
        '正在上传截图', '✔️ 通道 [Catbox] 上传成功']
    assert stream.flush.call_count == 2
    # 不可借用降级为普通行，无任何 ANSI
    assert '\x1b' not in ''.join(written_lines(stream))
    # 句柄缺属性只判定为不可借用，绝不借用它的任何日志方法
    assert handle.texts == []
    assert handle.dones == []
    assert handle.fails == []


def test_fallback_and_request_events_produce_no_terminal_ui():
    """备用域名、储存候选与请求事件不再产生任何逐站终态行。"""
    upload_status = import_module('modules.upload_status')
    stream = StringIO()
    consume = upload_status.make_status_consumer(stream=stream)
    for reason in ('slot_failed', 'domain_failed'):
        for status in ('started', 'succeeded', 'failed'):
            consume(StageEvent(stage='fallback', fallback_kind='host_failover',
                               status=status, reason_code=reason))
    consume(StageEvent(stage='upload_attempt', action='post',
                       status='failed', reason_code='upload_failed'))
    assert stream.getvalue() == ''


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

    class BrokenConsumer:
        """显示消费者替身：阶段进出正常，消费事件必抛普通异常。"""

        def _enter_phase(self):
            """返回空阶段帧，保证上传作用域可正常进入。"""
            return None

        def _exit_phase(self, frame):
            """忽略阶段帧，退出阶段不影响上传结果。"""

        def __call__(self, event):
            """模拟终端显示失败。"""
            raise RuntimeError(SECRET)

    def broken_consumer(**kwargs):
        """返回必然报错的显示消费者替身。"""
        return BrokenConsumer()

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
    result = capsys.readouterr()
    result_stdout = result.out
    status_text = result.err
    assert code == 0
    assert 'catbox：上传成功 https://example.invalid/result\n' in result_stdout
    assert '正在上传截图' not in result_stdout
    assert '✔️ 通道 [Catbox] 上传成功' in status_text
    assert '正在上传截图' in status_text
    assert '准备上传' not in status_text
    assert '\x1b' not in status_text
    assert URL not in status_text + file_text


def test_cli_tty_color_not_in_file_log(monkeypatch, tmp_path, capsys):
    """活动 TTY 上传着色落在终端状态流，文件日志保持纯文本且不含链接。"""
    cli = cli_module()
    logger_module = import_module('modules.logger')
    spinner_mod = import_module('modules.spinner')

    def uploader(image, filename, token, options):
        """返回合成成功链接，不发起网络请求。"""
        return URL

    monkeypatch.setitem(registry_module().UPLOADERS, 'catbox', uploader)
    monkeypatch.setattr(cli, 'capture', Mock(return_value=capture_result()))
    handler = logger_module._create_file_handler(str(tmp_path / 'logs'), 'app')
    assert handler is not None
    root = logging.getLogger()
    root.addHandler(handler)
    saved_level = root.level
    # 抬级到 INFO，否则 INFO 记录在生效级别处被丢弃，file_text 恒空、断言恒真
    root.setLevel(logging.INFO)
    try:
        # 合成凭证刻意避开通道名 "Catbox" 的子串，否则 TTY 写路径的脱敏
        # 会把通道名里的同名子串一并替换，令成功终态行断言失真
        with patch.object(spinner_mod.sys.stderr, 'isatty', return_value=True):
            code = cli.run_screenshot_cli(
                cli_args(hosts=[{'provider': 'catbox', 'token': SECRET}]),
                str(tmp_path))
    finally:
        root.setLevel(saved_level)
        root.removeHandler(handler)
    path = handler.baseFilename
    handler.close()
    with open(path, 'rb') as stream:
        file_text = stream.read().decode('utf-8')
    result = capsys.readouterr()
    status_text = result.err
    result_stdout = result.out
    assert code == 0
    # 着色确实发生：同一次运行里终端状态流带 ANSI 且含成功终态
    assert '\x1b' in status_text
    assert '✔️ 通道 [Catbox] 上传成功' in remove_terminal_sequences(status_text)
    # 文件日志非空，成对断言才有意义：着色发生而 ANSI 不进文件日志
    assert file_text
    assert '\x1b' not in file_text
    assert '\x1b' not in result_stdout
    assert URL not in file_text


def test_pipeline_failover_records_each_slot(monkeypatch, capsys):
    """同一图片两图床先败后成：槽位一败一成，批次仍完成。"""
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
    assert '❌ 通道 [Catbox] 上传失败' in out
    assert '✔️ 通道 [BeeIMG] 上传成功' in out
    assert out.count('正在上传截图') == 1
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


def test_cli_profile_forbidden_shows_actionable_hint(monkeypatch, tmp_path, capsys):
    """CLI 遇资料受限码时直接注入错误：呈现安全终态、零请求且退出码为 1。"""
    support = import_module('test_upload_stage_logging')
    cli = cli_module()

    def forbidden(image, filename, token, options):
        """直接注入资料受限错误以验证提示可达，不发起任何真实请求。"""
        raise ImageHostError('profile_forbidden', '')

    monkeypatch.setitem(registry_module().UPLOADERS, 'beeimg_cn', forbidden)
    monkeypatch.setattr(cli, 'capture', Mock(return_value=capture_result()))
    transport = support.Transport(monkeypatch, [])
    code = cli.run_screenshot_cli(
        cli_args(hosts=[{'provider': 'beeimg_cn', 'token': SECRET,
                         'options': {'storage_id': 13}}]),
        str(tmp_path))
    captured = capsys.readouterr()
    out = captured.out
    assert code == 1
    assert transport.posts == []
    assert terminal_lines(captured.err) == ['❌ 通道 [BeeIMG.CN] 上传失败']
    assert out.count(ImageHostError('profile_forbidden', '').message) == 1
    assert 'beeimg_cn：上传失败\n' in out
    assert SECRET not in out
    assert SECRET not in captured.err


def test_cli_capture_failure_skips_planned_slots(monkeypatch, tmp_path, capsys):
    """CLI 捕获失败且已声明图床时，把计划槽位补记为跳过，等式仍成立。"""
    cli = cli_module()
    monkeypatch.setattr(cli, 'capture', Mock(
        side_effect=CaptureError('window_not_found', '')))
    events, traces = [], []
    spy_trace_scope(monkeypatch, cli, events, traces)
    code = cli.run_screenshot_cli(
        cli_args(hosts=[{'provider': 'catbox', 'token': 't'}]), str(tmp_path))
    summary = traces[0].summary()
    assert code == 1
    assert summary['slots_planned'] == 1
    assert summary['slots_skipped'] == 1
    assert summary['slots_planned'] == sum(
        summary['slots_' + state]
        for state in ('succeeded', 'failed', 'cancelled', 'skipped'))
    reasons = [event.reason_code for event in events
               if event.stage == 'slot' and event.status == 'skipped']
    assert reasons == ['capture_failed']
    assert capsys.readouterr().out.count('通道 [') == 0


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
    assert terminal_lines(out) == ['❌ 通道 [Catbox] 上传失败']


def test_recovery_success_does_not_display_intermediate_failure(
        monkeypatch, tmp_path, capsys):
    """手填直传遇精确拒绝后恢复成功只显示一次最终成功，不显示中途请求失败。"""
    support = import_module('test_upload_stage_logging')
    pipeline = pipeline_module()
    monkeypatch.setattr(pipeline, 'capture', Mock(return_value=capture_result()))
    monkeypatch.setattr(pipeline, 'save_automatic', Mock(
        return_value=SaveOutcome(None, 'shot.jpg')))
    support.Transport(monkeypatch, [
        support.REJECTED_UPLOAD, support.group_reply(),
        support.SUCCESS_UPLOAD])
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
    assert terminal_lines(out) == ['✔️ 通道 [BeeIMG.CN] 上传成功']


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


@pytest.mark.parametrize('error', [
    CaptureError('window_not_found', ''),
    RuntimeError('capture-failure-marker'),
])
def test_cli_capture_failure_records_save_skip(monkeypatch, tmp_path, error):
    """CLI 两个捕获失败分支都补齐保存跳过事实，且不伪造保存开始。"""
    cli = cli_module()
    monkeypatch.setattr(cli, 'capture', Mock(side_effect=error))
    events, traces = [], []
    spy_trace_scope(monkeypatch, cli, events, traces)
    code = cli.run_screenshot_cli(
        cli_args(hosts=[{'provider': 'catbox', 'token': 't'}]), str(tmp_path))
    assert code == 1
    save = [(event.status, event.reason_code, event.image_index)
            for event in events if event.stage == 'save']
    assert save == [('skipped', 'capture_failed', 1)]
    summary = traces[0].summary()
    assert summary['save_started'] == 0
    assert summary['save_skipped'] == 1


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


class BorrowedHandle:
    """可借用的外层 TTY 句柄替身：暴露文案、输出流与公开借用属性。"""

    def __init__(self, message, stream):
        """保存初始文案、输出流与同线程身份。"""
        self.message = message
        self._stream = stream
        self.texts = []
        self.writes = []
        self.successes = []
        self.closed = False
        self.owner_thread_id = threading.get_ident()

    @property
    def stream(self):
        """返回绑定的 TTY 输出流。"""
        return self._stream

    def text(self, message):
        """记录文案切换并更新当前公开文案。"""
        self.texts.append(message)
        self.message = message

    def write(self, message, *, success=None):
        """记录插入的终态行与结果语义，签名与 _TtySpinner.write 一致。"""
        self.writes.append(message)
        self.successes.append(success)


class OrderedStream:
    """非 TTY 普通行替身：写入顺序与上传调用共用同一顺序列表。"""

    def __init__(self, order):
        """保存共享顺序列表并初始化行与刷新计数。"""
        self._order = order
        self.lines = []
        self.flushes = 0

    def write(self, text):
        """记录一行并标记与上传调用交错的顺序。"""
        self._order.append(('write', text.rstrip('\n')))
        self.lines.append(text)

    def flush(self):
        """记录一次刷新。"""
        self.flushes += 1

    def isatty(self):
        """恒为非 TTY，强制普通行路径。"""
        return False


def test_stable_consumer_per_image_scope(monkeypatch):
    """两图共享同一消费者：逐图独立上传 scope 且外层文案逐图恢复。"""
    upload_status = import_module('modules.upload_status')
    pipeline = pipeline_module()

    tty = plain_stream(isatty=True)
    handle = BorrowedHandle('监视中', tty)
    monkeypatch.setattr(upload_status, 'current_spinner', lambda: handle)

    monkeypatch.setitem(
        registry_module().UPLOADERS, 'wmimg',
        lambda image, filename, token, options: URL)

    def failing(image, filename, token, options):
        """首站返回固定传输失败。"""
        raise ImageHostError('transport_failed', '')

    monkeypatch.setitem(registry_module().UPLOADERS, 'catbox', failing)

    capture_messages = []

    def capture(*args, **kwargs):
        """记录捕获时外层句柄的公开文案并返回合成结果。"""
        capture_messages.append(handle.message)
        return capture_result()

    monkeypatch.setattr(pipeline, 'capture', capture)
    monkeypatch.setattr(pipeline, 'save_automatic', Mock(
        return_value=SaveOutcome(None, 'shot.jpg')))

    created = []
    real_make = upload_status.make_status_consumer

    def factory(**kwargs):
        """记录构造参数并返回真实消费者，核对批次内唯一构造。"""
        consumer = real_make(**kwargs)
        created.append((kwargs, consumer))
        return consumer

    monkeypatch.setattr(pipeline, 'make_status_consumer', factory)

    real_scope = upload_status.upload_feedback_scope
    scoped = []

    @contextmanager
    def scope(consumer):
        """记录被包裹的消费者身份后仍走真实阶段绑定。"""
        scoped.append(consumer)
        with real_scope(consumer):
            yield consumer

    monkeypatch.setattr(pipeline, 'upload_feedback_scope', scope)
    events, traces, callbacks = [], [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces, callbacks)
    pipeline.prepare_screenshots(
        {'targets': [{'provider': 'window', 'target': '窗口'},
                     {'provider': 'window', 'target': '窗口'}],
         'image_host': [{'provider': 'catbox', 'token': 't'},
                        {'provider': 'wmimg', 'token': 't'}]},
        True, ())
    consumer = created[0][1]
    assert len(created) == 1
    assert callbacks == [consumer]
    assert scoped == [consumer, consumer]
    assert capture_messages == ['监视中', '监视中']
    summary = traces[0].summary()
    assert summary['slots_planned'] == 4
    assert summary['slots_succeeded'] == 2
    assert summary['slots_failed'] == 2
    assert handle.texts == ['正在上传截图', '监视中'] * 2
    assert handle.writes == [
        '❌ 通道 [Catbox] 上传失败（截图 1）',
        '✔️ 通道 [WMImg] 上传成功（截图 1）',
        '❌ 通道 [Catbox] 上传失败（截图 2）',
        '✔️ 通道 [WMImg] 上传成功（截图 2）',
    ]
    # 借用句柄收到真实结果语义，且外层 spinner 不被关闭
    assert handle.successes == [False, True, False, True]
    assert handle.closed is False


def test_automatic_first_success_and_cli_all_slots(monkeypatch, tmp_path, capsys):
    """自动逐图首成功即停并跳过剩余槽位；CLI 逐声明项全传仍调后续站。"""
    upload_status = import_module('modules.upload_status')
    registry = registry_module()
    pipeline = pipeline_module()
    order = []
    stream = OrderedStream(order)
    auto_calls = []

    def catbox(image, filename, token, options):
        """记录调用顺序并返回固定传输失败。"""
        auto_calls.append('catbox')
        order.append(('call', 'catbox'))
        raise ImageHostError('transport_failed', '')

    def wmimg(image, filename, token, options):
        """记录调用顺序并返回合成成功链接。"""
        auto_calls.append('wmimg')
        order.append(('call', 'wmimg'))
        return URL

    monkeypatch.setitem(registry.UPLOADERS, 'catbox', catbox)
    monkeypatch.setitem(registry.UPLOADERS, 'wmimg', wmimg)
    monkeypatch.setattr(pipeline, 'capture', Mock(return_value=capture_result()))
    monkeypatch.setattr(pipeline, 'save_automatic', Mock(
        return_value=SaveOutcome(None, 'shot.jpg')))
    monkeypatch.setattr(
        pipeline, 'make_status_consumer',
        lambda **kwargs: upload_status.make_status_consumer(
            stream=stream, **kwargs))
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)
    pipeline.prepare_screenshots(
        {'targets': [{'provider': 'window', 'target': '窗口'}],
         'image_host': [{'provider': 'catbox', 'token': 't'},
                        {'provider': 'wmimg', 'token': 't'},
                        {'provider': 'superbed', 'token': 't'}]},
        True, ())
    auto_summary = traces[0].summary()
    assert auto_calls == ['catbox', 'wmimg']
    assert auto_summary['slots_planned'] == 3
    assert auto_summary['slots_skipped'] == 1
    assert order == [
        ('write', '正在上传截图'),
        ('call', 'catbox'),
        ('write', '❌ 通道 [Catbox] 上传失败'),
        ('call', 'wmimg'),
        ('write', '✔️ 通道 [WMImg] 上传成功'),
    ]

    cli = cli_module()
    cli_calls = []

    def cli_catbox(image, filename, token, options):
        """记录调用并返回合成成功链接。"""
        cli_calls.append('catbox')
        return URL

    def cli_wmimg(image, filename, token, options):
        """记录调用并返回固定传输失败。"""
        cli_calls.append('wmimg')
        raise ImageHostError('transport_failed', '')

    monkeypatch.setitem(registry.UPLOADERS, 'catbox', cli_catbox)
    monkeypatch.setitem(registry.UPLOADERS, 'wmimg', cli_wmimg)
    monkeypatch.setattr(cli, 'capture', Mock(return_value=capture_result()))
    saved_path = str(tmp_path / 'shot.jpg')
    monkeypatch.setattr(cli, 'save_cli', Mock(
        return_value=SaveOutcome(saved_path, 'shot.jpg')))
    exit_code = cli.run_screenshot_cli(
        cli_args(output=saved_path,
                 hosts=[{'provider': 'catbox', 'token': 't'},
                        {'provider': 'wmimg', 'token': 't'}]),
        str(tmp_path))
    captured = capsys.readouterr()
    assert cli_calls == ['catbox', 'wmimg']
    assert exit_code == 1
    assert captured.out.splitlines() == [
        saved_path, 'catbox：上传成功 ' + URL, 'wmimg：上传失败']
    assert URL not in captured.err and saved_path not in captured.err
    assert '正在上传截图' not in captured.out


def test_host_providers_preserves_slots():
    """共享提取只接受列表并按槽位保留原始 provider、占位与顺序。"""
    upload_status = import_module('modules.upload_status')
    assert upload_status.host_providers(None) == ()
    assert upload_status.host_providers([]) == ()
    assert upload_status.host_providers(({'provider': 'catbox'},)) == ()
    assert upload_status.host_providers({'provider': 'catbox'}) == ()
    assert upload_status.host_providers('catbox') == ()

    hosts = [
        {'provider': 'catbox'}, None, {}, {'provider': 'catbox'},
        {'provider': None}, {'provider': 7}, {'provider': 'unregistered'},
    ]
    snapshot = deepcopy(hosts)
    assert upload_status.host_providers(hosts) == (
        'catbox', '', '', 'catbox', None, 7, 'unregistered')
    assert hosts == snapshot

    # Mapping 子类同样按 provider 取值，缺失字段仍填占位
    assert upload_status.host_providers(
        [UserDict({'provider': 'catbox'}), {}]) == ('catbox', '')

    def one_host():
        """产出单个图床配置，用于证明提取不消费生成器。"""
        yield {'provider': 'catbox'}

    source = one_host()
    assert upload_status.host_providers(source) == ()
    assert next(source) == {'provider': 'catbox'}


def test_host_providers_preserves_list_subclass_and_identity():
    """list 子类被接受，provider 原对象原样返回且不重建槽位。"""
    upload_status = import_module('modules.upload_status')

    class HostsList(list):
        """用于证明共享提取接受 list 子类的局部序列类型。"""

    provider = {'marker': 'raw-provider'}
    hosts = HostsList([{'provider': provider}])
    result = upload_status.host_providers(hosts)
    assert len(result) == 1
    assert result[0] is provider
    assert hosts == [{'provider': {'marker': 'raw-provider'}}]


def test_host_providers_consumer_normalizes_unknown_slots():
    """原始槽位中的 None 与未知名称由消费者归一，绝不回显原文。"""
    upload_status = import_module('modules.upload_status')
    hosts = [{'provider': None}, {'provider': 'unregistered-secret'}]
    stream = StringIO()
    consumer = upload_status.make_status_consumer(
        stream=stream, images_planned=1,
        slot_providers=upload_status.host_providers(hosts))
    consumer(slot_event('a', 1, 0, 'unknown', 'failed'))
    consumer(slot_event('a', 1, 1, 'unknown', 'failed'))
    assert stream.getvalue().splitlines() == [
        '❌ 通道 [未知图床 第 1 项] 上传失败（配置项 1）',
        '❌ 通道 [未知图床 第 2 项] 上传失败（配置项 2）',
    ]
    assert 'unregistered-secret' not in stream.getvalue()
    assert 'None' not in stream.getvalue()
