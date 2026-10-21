#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""跨层离线验收：monitor、推送、截图编排、CLI 与旧渲染合同。

全部用例显式使用临时缓存与保存目录，截图、外部程序、任务及进程查询均由
本地替身提供；真实网络、netrc、设备与邮件服务器在夹具中 fail-closed。
"""

from argparse import Namespace
from contextlib import contextmanager
from importlib import import_module
from io import BytesIO
import json
import logging
import netrc
import socket
import threading
from unittest.mock import Mock

from PIL import Image
import pytest
import requests

from modules.image_host.core import ImageHostError
from modules.notification_rendering import ScreenshotRenderContext
from modules.notification_rendering import prepare_channel
from modules.notification_rendering import render_screenshot_content
from modules.notification_rendering import render_screenshot_template
from modules.screenshot.models import CaptureResult
from modules.screenshot.pipeline import ScreenshotBatch
from modules.screenshot.pipeline import ScreenshotFailureDetail
from modules.screenshot.pipeline import build_screenshot_fragments


URL = 'https://cdn.example.com/synthetic.png'
SECOND_URL = 'https://cdn.example.com/second.png'
HOST_SECRET = 'FAKE_ACCEPTANCE_TOKEN_7319'
CHANNEL_SECRET = 'FAKE_CHANNEL_SECRET_8421'
EXCEPTION_SECRET = 'FAKE_DYNAMIC_EXCEPTION_9532'
PERMISSION = (
    '截图上传失败：通道 BeeIMG.CN 的账号资料接口无访问权限，'
    '请为令牌授权资料读取权限，或填写该账号可用的 options.storage_id。'
)
CATBOX = '截图上传失败：通道 Catbox 图床网络传输失败。'
SUCCESS_UPLOAD = (200, json.dumps({
    'status': 'success', 'data': {'public_url': URL}}).encode('utf-8'))
REJECTED_UPLOAD = (200, json.dumps({
    'status': 'error', 'message': '不存在的储存驱动'}).encode('utf-8'))
TERMINAL = frozenset(('succeeded', 'failed', 'cancelled', 'skipped'))


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """隔离缓存、日志与网络，未替身化的外部访问立即失败。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))

    def forbidden(*args, **kwargs):
        """任何越过合成边界的访问都立即令测试失败。"""
        pytest.fail('验收测试禁止真实网络、netrc 或设备访问')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', forbidden)
    monkeypatch.setattr(requests.sessions, 'get_netrc_auth', forbidden)
    monkeypatch.setattr(requests.utils, 'get_netrc_auth', forbidden)
    monkeypatch.setattr(netrc, 'netrc', forbidden)

    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    root.handlers = []
    root.setLevel(logging.DEBUG)
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)


class Transport:
    """替换 HTTPAdapter.send，记录真实请求并按队列返回合成响应。"""

    def __init__(self, monkeypatch, replies):
        """登记发送边界并保存有序响应队列。

        Args:
            monkeypatch: pytest 的补丁入口。
            replies: 合成响应或待抛出异常的有序队列。
        """
        self.sent = []
        self.replies = list(replies)

        def send(adapter, request, **kwargs):
            """记录一次真实请求并返回预设响应。"""
            self.sent.append(request)
            reply = self.replies.pop(0)
            if isinstance(reply, BaseException):
                raise reply
            status, content = reply
            response = requests.Response()
            response.request = request
            response.url = request.url
            response.status_code = status
            response._content = content
            response._content_consumed = True
            return response

        monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)

    @property
    def posts(self):
        """返回按发生顺序记录的真实 POST 请求。"""
        return [item for item in self.sent if item.method == 'POST']


def group_reply(ids=(13, 14), expire=None):
    """构造一次 group 查询响应。"""
    return (200, json.dumps({'status': 'success', 'data': {
        'group': {'options': {'file_expire_seconds': expire}},
        'storages': [{'id': value} for value in ids]}}).encode('utf-8'))


def profile_reply(default=14):
    """构造一次 profile 查询响应。"""
    return (200, json.dumps({'status': 'success', 'data': {'options': {
        'default_storage_id': default}}}).encode('utf-8'))


def install_boundaries(monkeypatch):
    """只替换截图与保存边界，保留真实编排和注册表。"""
    pipeline = import_module('modules.screenshot.pipeline')

    def capture(source, target, **kwargs):
        """返回与目标一一对应的合成截图。"""
        return CaptureResult(
            b'png-' + target.encode(), source, target, 2, 3,
            image_format='jpeg')

    monkeypatch.setattr(pipeline, 'capture', Mock(side_effect=capture))
    return pipeline


def spy_trace_scope(monkeypatch, module, events, traces):
    """包装真实 trace 作用域并同时记录事件与批次对象。"""
    real = module.trace_scope

    @contextmanager
    def scope(**kwargs):
        """在保留生产回调的前提下收集真实事件与汇总。"""
        inner = kwargs.get('on_status')

        def combined(event):
            """先记录事件，再交给生产消费者。"""
            events.append(event)
            if inner is not None:
                inner(event)

        kwargs['on_status'] = combined
        with real(**kwargs) as trace:
            traces.append(trace)
            yield trace

    monkeypatch.setattr(module, 'trace_scope', scope)


def install_push(monkeypatch, *, slow_started, release, sent):
    """安装真实通道发送之外的通知器替身。"""
    notifier = Mock()

    def notify(title=None, content=None, **params):
        """慢通道等待释放，快通道立即返回合成成功响应。"""
        if 'sckey' in params:
            slow_started.set()
            release.wait(timeout=10)
        sent.append((title, content))
        response = Mock()
        response.status_code = 200
        response.json.return_value = {'code': 0}
        return response

    notifier.notify.side_effect = notify
    monkeypatch.setattr(
        import_module('modules.notification'), 'get_notifier',
        Mock(return_value=notifier))


class FeedbackHandle:
    """满足 monitor 借用条件的合成 TTY 句柄。"""

    def __init__(self, fast_seen):
        """保存快通道反馈事件与写入记录。"""
        self._fast_seen = fast_seen
        self._stream = StringTty()
        self.lines = []

    @property
    def stream(self):
        """返回可判定为 TTY 的反馈流。"""
        return self._stream

    @property
    def owner_thread_id(self):
        """借用判定要求同线程，这里返回当前线程身份。"""
        return threading.get_ident()

    @property
    def closed(self):
        """句柄保持未关闭。"""
        return False

    def write_done(self, message):
        """记录成功终态；快通道出现时释放等待方。"""
        self.lines.append(message)
        if 'dingtalk' in message:
            self._fast_seen.set()

    def write_fail(self, message):
        """记录失败终态。"""
        self.lines.append(message)


class StringTty:
    """仅需 isatty/写入能力的反馈流替身。"""

    def __init__(self):
        """初始化写入记录。"""
        self.writes = []

    def isatty(self):
        """固定声明为 TTY。"""
        return True

    def write(self, text):
        """记录一次写入。"""
        self.writes.append(text)

    def flush(self):
        """无需刷新。"""


class ControlStream:
    """非 TTY 反馈流，写入指定成功行时抛出控制异常。"""

    def __init__(self, signal, marker):
        """保存控制异常与触发标记。"""
        self._signal = signal
        self._marker = marker
        self.writes = []

    def isatty(self):
        """固定声明为非 TTY，走普通行分支。"""
        return False

    def write(self, text):
        """记录写入，命中标记后抛出原控制异常。"""
        self.writes.append(text)
        if self._marker in text:
            raise self._signal

    def flush(self):
        """无需刷新。"""

    def text(self):
        """拼接已写入文本。"""
        return ''.join(self.writes)


def notification_config(tmp_path, hosts):
    """构造两图通知的合成配置与临时运行时。"""
    return {
        'push': {
            'screenshot': {
                'targets': [
                    {'provider': 'window', 'target': '窗口一'},
                    {'provider': 'adb', 'target': '设备一'},
                ],
                'image_host': hosts,
            },
            'templates': {
                'on_end': {
                    'enable': True,
                    'capture_screenshot': True,
                    'title': '标题 {process_name}',
                    'content': '正文 {process_name}\n\n{screenshot}',
                },
            },
            'push_channel_settings': {'channels': [
                {'provider': 'serverchan', 'sckey': CHANNEL_SECRET},
                {'provider': 'dingtalk', 'token': CHANNEL_SECRET},
            ]},
            'retry': {'interval': '0s', 'max_count': 1},
        },
        '_runtime': {
            'program_dir': str(tmp_path / 'program'),
            'config_stem': 'config',
        },
    }


def test_monitor_to_upload_to_push_feedback(monkeypatch, tmp_path):
    """monitor 包装串起真实截图、恢复、上传与完成顺序推送。"""
    monitor = import_module('modules.monitor')
    pipeline = install_boundaries(monkeypatch)
    transport = Transport(monkeypatch, [
        REJECTED_UPLOAD, group_reply(), REJECTED_UPLOAD,
        group_reply(), SUCCESS_UPLOAD,
        REJECTED_UPLOAD, group_reply(), REJECTED_UPLOAD,
        SUCCESS_UPLOAD,
    ])
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)

    slow_started = threading.Event()
    release = threading.Event()
    fast_seen = threading.Event()
    sent = []
    install_push(
        monkeypatch, slow_started=slow_started, release=release, sent=sent)
    handle = FeedbackHandle(fast_seen)
    config = notification_config(tmp_path, [
        {'provider': 'beeimg_cn', 'token': '',
         'options': {'storage_id': 13}},
        {'provider': 'beeimg_cn', 'token': HOST_SECRET},
    ])

    outcome = {}

    def run():
        """在独立线程执行真实 monitor 包装，避免慢通道阻塞主线程。"""
        try:
            outcome['results'] = monitor._send_notification_with_feedback(
                config, 'on_end', spinner=handle, process_name='合成进程')
        except BaseException as error:
            outcome['error'] = error

    caller = threading.Thread(target=run, daemon=True)
    caller.start()
    try:
        assert slow_started.wait(timeout=10)
        assert fast_seen.wait(timeout=10)
        assert not release.is_set()
        assert caller.is_alive()
    finally:
        release.set()
    caller.join(timeout=10)

    assert not caller.is_alive()
    assert 'error' not in outcome
    assert outcome['results'] == [('serverchan', True), ('dingtalk', True)]

    batch_id = events[0].batch_id
    started = [
        (event.batch_id, event.image_index, event.slot_index)
        for event in events
        if event.stage == 'slot' and event.status == 'started']
    terminal = [
        (event.batch_id, event.image_index, event.slot_index)
        for event in events
        if event.stage == 'slot' and event.status in TERMINAL]
    expected = {
        (batch_id, 1, 0), (batch_id, 1, 1),
        (batch_id, 2, 0), (batch_id, 2, 1),
    }
    assert set(started) == expected
    assert terminal == started
    assert len(terminal) == len(set(terminal))

    summary = traces[0].summary()
    assert len(transport.posts) == summary['upload_post_attempts'] == 6
    assert summary['slots_planned'] == sum(
        summary['slots_' + state]
        for state in ('succeeded', 'failed', 'cancelled', 'skipped'))
    assert (summary['slots_succeeded'], summary['slots_failed']) == (2, 2)
    assert summary['postupload_recoveries'] == 2
    assert summary['status'] == 'completed'
    assert not transport.replies

    assert len(sent) == 2
    assert all(HOST_SECRET not in value for item in sent for value in item)
    assert handle.lines == [
        '通道 [dingtalk] 推送成功', '通道 [serverchan] 推送成功']


def make_image(size=(4, 3)):
    """在内存生成可真实保存的 PNG 载荷。"""
    with Image.new('RGBA', size, (9, 8, 7)) as image:
        with BytesIO() as output:
            image.save(output, format='PNG')
            return output.getvalue()


def install_cli_boundaries(monkeypatch):
    """替换 CLI 截图与两个图床适配器，保留真实编排。"""
    cli = import_module('modules.screenshot.cli')
    registry = import_module('modules.image_host.registry')
    monkeypatch.setattr(cli, 'capture', Mock(return_value=CaptureResult(
        make_image(), 'window', '目标窗口', 4, 3, image_format='png')))
    monkeypatch.setitem(
        registry.UPLOADERS, 'catbox',
        lambda image, filename, token, options: URL)

    def reject(image, filename, token, options):
        """抛出携带合成动态原文的固定上传拒绝。"""
        raise ImageHostError('upload_failed', EXCEPTION_SECRET)

    monkeypatch.setitem(registry.UPLOADERS, 'wmimg', reject)
    return cli


def test_cli_stream_stats_and_security(monkeypatch, tmp_path, capsys):
    """真实 CLI 部分成功：进度只在 stderr，结果与 URL 只在既有 stdout。"""
    cli = install_cli_boundaries(monkeypatch)
    logger_module = import_module('modules.logger')
    handler = logger_module._create_file_handler(str(tmp_path / 'logs'), 'cli')
    assert handler is not None
    events, traces = [], []
    spy_trace_scope(monkeypatch, cli, events, traces)

    hosts = [
        {'provider': 'catbox', 'token': HOST_SECRET},
        {'provider': 'wmimg', 'token': HOST_SECRET},
    ]
    saved_path = str(tmp_path / 'shot.png')
    args = Namespace(
        source='window', target='目标窗口', output=saved_path, hosts=hosts)

    root = logging.getLogger()
    root.addHandler(handler)
    try:
        partial_code = cli.run_screenshot_cli(args, str(tmp_path / 'program'))
        invalid_code = cli.run_screenshot_cli(
            Namespace(source='window', target='', output=None, hosts=None),
            str(tmp_path / 'program'))
        success_code = cli.run_screenshot_cli(
            Namespace(source='window', target='目标窗口',
                      output=str(tmp_path / 'single.png'),
                      hosts=[{'provider': 'catbox', 'token': HOST_SECRET}]),
            str(tmp_path / 'program'))
    finally:
        root.removeHandler(handler)
    captured = capsys.readouterr()
    handler.flush()
    with open(handler.baseFilename, 'rb') as stream:
        file_text = stream.read().decode('utf-8')
    handler.close()

    assert partial_code == 1
    assert invalid_code == 2
    assert success_code == 0
    assert URL in captured.out
    assert URL not in captured.err
    assert saved_path in captured.out
    assert saved_path not in captured.err
    assert '正在上传截图' in captured.err
    assert '✔️  通道 [Catbox] 上传成功' in captured.err
    assert '❌ 通道 [WMImg] 上传失败' in captured.err
    assert EXCEPTION_SECRET not in captured.out
    assert HOST_SECRET not in captured.out + captured.err + file_text
    assert EXCEPTION_SECRET not in captured.err + file_text
    assert URL not in file_text
    assert saved_path not in file_text

    summary = traces[0].summary()
    assert summary['slots_planned'] == sum(
        summary['slots_' + state]
        for state in ('succeeded', 'failed', 'cancelled', 'skipped'))
    assert (summary['slots_succeeded'], summary['slots_failed']) == (1, 1)
    assert summary['status'] == 'partial_failure'


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_control_exit_across_trace_and_ui(monkeypatch, tmp_path, signal_type):
    """UI 写入控制异常原对象传播，终态不补打，槽位与批次收尾完整。"""
    pipeline = install_boundaries(monkeypatch)
    registry = import_module('modules.image_host.registry')
    for provider in ('catbox', 'wmimg'):
        monkeypatch.setitem(
            registry.UPLOADERS, provider,
            lambda image, filename, token, options: URL)
    signal = signal_type('stop-ui')
    stream = ControlStream(signal, '✔️  通道 [Catbox] 上传成功')
    real_make = pipeline.make_status_consumer

    def make(**kwargs):
        """强制真实消费者使用可注入控制异常的反馈流。"""
        kwargs['stream'] = stream
        return real_make(**kwargs)

    monkeypatch.setattr(pipeline, 'make_status_consumer', make)
    events, traces = [], []
    spy_trace_scope(monkeypatch, pipeline, events, traces)
    section = {
        'targets': [
            {'provider': 'window', 'target': '窗口'},
            {'provider': 'adb', 'target': '设备'},
        ],
        'image_host': [{'provider': 'catbox'}, {'provider': 'wmimg'}],
    }

    with pytest.raises(signal_type) as caught:
        pipeline.prepare_screenshots(
            section, True, (),
            runtime={'program_dir': str(tmp_path), 'config_stem': 'config'})
    assert caught.value is signal

    summary = traces[0].summary()
    assert summary['status'] == 'cancelled'
    assert (summary['images_processed'], summary['images_succeeded'],
            summary['images_skipped']) == (1, 1, 1)
    assert summary['slots_planned'] == 4
    assert (summary['slots_succeeded'], summary['slots_failed'],
            summary['slots_cancelled'], summary['slots_skipped']) == (
                1, 0, 0, 3)
    assert summary['slots_planned'] == sum(
        summary['slots_' + state]
        for state in ('succeeded', 'failed', 'cancelled', 'skipped'))

    terminal = [
        (event.image_index, event.slot_index, event.status, event.reason_code)
        for event in events
        if event.stage == 'slot' and event.status in TERMINAL]
    assert terminal == [
        (1, 0, 'succeeded', ''),
        (1, 1, 'skipped', 'batch_cancelled'),
        (2, 0, 'skipped', 'batch_cancelled'),
        (2, 1, 'skipped', 'batch_cancelled'),
    ]
    keys = [(image, slot) for image, slot, _, _ in terminal]
    assert len(keys) == len(set(keys))
    assert stream.text().count('✔️  通道 [Catbox] 上传成功') == 1
    assert '程序已退出' not in stream.text()


def legacy_batch():
    """构造含重复同因、成功图片与旧聚合值的受控批次。"""
    return ScreenshotBatch(
        {
            'screenshot_1': '旧失败值',
            'screenshot_2': f'![screenshot_2]({SECOND_URL})',
            'screenshot': '旧聚合',
        },
        (),
        (),
        (
            ScreenshotFailureDetail(
                'screenshot_1', 'upload', 'beeimg_cn', 'profile_forbidden'),
            ScreenshotFailureDetail(
                'screenshot_1', 'upload', 'catbox', 'transport_failed'),
            ScreenshotFailureDetail(
                'screenshot_1', 'upload', 'beeimg_cn', 'profile_forbidden'),
        ),
    )


def render_modes(batch):
    """使用真实渲染器生成四种模式及对应预期。"""
    values = {'process_name': '合成', 'screenshot': batch.values['screenshot']}
    context = ScreenshotRenderContext(
        '标题 {process_name}', '正文\n\n{screenshot}', values, batch, True)
    image_markdown = f'![screenshot_2]({SECOND_URL})'
    expected = {
        'markdown': '\n\n'.join((
            '正文', PERMISSION, CATBOX, image_markdown)),
        'url': '\n\n'.join(('正文', PERMISSION, CATBOX, SECOND_URL)),
        'plain': '\n\n'.join((
            '正文', PERMISSION, CATBOX, f'screenshot_2: {SECOND_URL}')),
        'html': (
            '正文<br>\n<br>\n'
            + PERMISSION + '<br>\n<br>\n'
            + CATBOX + '<br>\n<br>\n'
            + f'<img src="{SECOND_URL}" alt="screenshot_2">'),
    }
    rendered = {
        mode: render_screenshot_content(context, mode) for mode in expected}
    return values, context, expected, rendered


def test_legacy_contract_and_provenance():
    """HTML、SMTP 与用户模板保持既有所有权、顺序与转义语义。"""
    batch = legacy_batch()
    assert build_screenshot_fragments(batch) == (
        ('screenshot_1', 'text', PERMISSION),
        ('screenshot_1', 'text', CATBOX),
        ('screenshot_2', 'image', f'![screenshot_2]({SECOND_URL})'),
    )

    values, context, expected, rendered = render_modes(batch)
    for mode in ('markdown', 'url', 'plain', 'html'):
        assert rendered[mode] == expected[mode]
        assert rendered[mode].count(PERMISSION) == 1
        assert rendered[mode].count(CATBOX) == 1
        assert rendered[mode].index(PERMISSION) < rendered[mode].index(CATBOX)

    title, plain, params = prepare_channel(
        {'provider': 'smtp', 'user': 'user@example.invalid'},
        '基准标题', '基准正文', context)
    message = params['msg']
    plain_body = message.get_body(preferencelist=('plain',)).get_content()
    html_body = message.get_body(preferencelist=('html',)).get_content()
    assert title == '标题 合成'
    assert plain_body.rstrip('\n') == plain == expected['plain']
    assert html_body.rstrip('\n') == expected['html']
    assert plain_body.index(PERMISSION) < plain_body.index(CATBOX)
    assert html_body.index(PERMISSION) < html_body.index(CATBOX)
    assert f'screenshot_2: {SECOND_URL}' in plain_body
    assert f'<img src="{SECOND_URL}" alt="screenshot_2">' in html_body
    assert '&amp;lt;' not in html_body

    legacy = ScreenshotBatch({'screenshot': '<旧原因>&'}, (), ())
    escaped = render_screenshot_template(
        '{screenshot}', legacy.values, legacy, 'html')
    assert escaped == '&lt;旧原因&gt;&amp;'
    assert escaped.count('&lt;旧原因&gt;&amp;') == 1
    assert '&amp;lt;' not in escaped
    for mode in ('markdown', 'url', 'plain'):
        assert render_screenshot_template(
            '{screenshot}', legacy.values, legacy, mode) == '<旧原因>&'
    empty = ScreenshotBatch({'screenshot': ''}, (), ())
    blank = ScreenshotBatch({'screenshot': ' '}, (), ())
    assert render_screenshot_template(
        '{screenshot}', empty.values, empty, 'markdown') == ''
    assert render_screenshot_template(
        '{screenshot}', blank.values, blank, 'markdown') == ' '

    user = {'screenshot': 'OWNED', 'screenshot_1': 'legacy',
            'custom': 'CUSTOM', 'process_name': '合成'}
    assert render_screenshot_template(
        '{{screenshot}}', user, batch, 'markdown') == '{screenshot}'
    assert render_screenshot_template(
        '{screenshot!r}', user, batch, 'markdown') == "'OWNED'"
    assert render_screenshot_template(
        '{screenshot:>20}', user, batch, 'markdown') == f'{"OWNED":>20}'
    assert render_screenshot_template(
        '{screenshot_1}', user, batch, 'markdown') == 'legacy'
    assert render_screenshot_template(
        '{custom}', user, batch, 'markdown') == 'CUSTOM'
    handwritten = ScreenshotRenderContext(
        '', PERMISSION, user, batch, False)
    assert render_screenshot_content(handwritten, 'markdown') == PERMISSION

    base_title, base_content, base_params = prepare_channel(
        {'provider': 'smtp', 'user': 'user@example.invalid'},
        '用户标题', '用户正文 {screenshot}', None)
    assert (base_title, base_content) == ('用户标题', '用户正文 {screenshot}')
    assert base_params == {'user': 'user@example.invalid'}
    assert 'msg' not in base_params
