#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证截图 CLI 内联上传与本地保存的独立编排，不访问真实网络。"""

from argparse import Namespace
from datetime import date
from importlib import import_module
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest

from modules.image_host.core import ImageHostError
from modules.operation_trace import trace_scope
from modules.operation_trace import trace_operation
from modules.operation_trace import trace_slot
from modules.screenshot.models import CaptureResult
from modules.screenshot.retention import SaveOutcome
from modules.screenshot import winfs


URL = 'https://cdn.example.com/image.jpg'


def cli_module():
    """导入截图 CLI。"""
    return import_module('modules.screenshot.cli')


def make_image(image_format='png', size=(4, 3)):
    """使用 Pillow 合成指定格式的载荷。"""
    buffer = BytesIO()
    mode = 'RGB' if image_format == 'jpeg' else 'RGBA'
    Image.new(mode, size, (9, 8, 7)).save(buffer, format=image_format.upper())
    return buffer.getvalue()


def load_hosts(text):
    """解析内联图床文本。"""
    return import_module('modules.screenshot.inline_upload').parse_inline_hosts(text)


def args_for(output=None, hosts=None):
    """构造新契约参数命名空间。"""
    return Namespace(source='window', target='fixture', output=output, hosts=hosts)


def run_cli(monkeypatch, tmp_path, output=None, hosts=None, image_format='png'):
    """执行一次只使用合成截图及本地上传替身的 CLI。"""
    program_dir = tmp_path / 'program'
    program_dir.mkdir(exist_ok=True)
    payload = make_image(image_format)
    result = CaptureResult(payload, 'window', 'fixture', 4, 3,
                           image_format=image_format)
    capture = Mock(return_value=result)
    monkeypatch.setattr(cli_module(), 'capture', capture)
    code = cli_module().run_screenshot_cli(
        args_for(output, hosts), str(program_dir))
    return code, payload, program_dir, capture


def install_upload(monkeypatch, behavior):
    """替换上传入口并记录请求载荷。"""
    cli = cli_module()
    calls = []

    def upload(data, filename, hosts):
        """记录实际载荷并返回指定的上传状态。"""
        calls.append((data, filename, hosts))
        return behavior(data, filename, hosts)

    monkeypatch.setattr(cli, '_upload_all', upload)
    return calls


def patch_context(monkeypatch):
    """替换上传凭证上下文并记录创建次数。"""
    cli = cli_module()
    created = []

    class FakeContext:
        """记录构造参数的最小上下文替身。"""

        def __init__(self, **kwargs):
            """记录一次上下文创建。"""
            created.append(kwargs)

        def __enter__(self):
            """进入上下文。"""
            return self

        def __exit__(self, *exc_info):
            """不做处理，不吞异常。"""
            return False

    monkeypatch.setattr(cli, 'UploadContext', FakeContext)
    return created


def test_upload_only_does_not_create_local_file(monkeypatch, tmp_path):
    """仅上传不创建 screenshot 目录或占位文件。"""
    install_upload(monkeypatch, lambda *args: True)
    hosts = load_hosts('provider: catbox')
    code, _, program_dir, _ = run_cli(monkeypatch, tmp_path, hosts=hosts)
    assert code == 0
    assert not (program_dir / 'screenshot').exists()
    assert list(program_dir.iterdir()) == []


@pytest.mark.parametrize(('image_format', 'suffix'), [
    ('jpeg', '.jpg'), ('png', '.png'), ('webp', '.webp')])
def test_upload_filename_uses_actual_capture_format(
        monkeypatch, tmp_path, image_format, suffix):
    """上传候选名后缀跟随实际截图格式，且始终请求 JPEG。"""
    hosts = load_hosts('provider: catbox')
    calls = install_upload(monkeypatch, lambda *args: True)
    code, payload, _, capture = run_cli(
        monkeypatch, tmp_path, hosts=hosts, image_format=image_format)
    assert code == 0
    capture.assert_called_once_with(
        'window', 'fixture', image_format='jpeg', purpose='cli')
    assert calls[0][0] == payload
    assert calls[0][1].endswith(suffix)


@pytest.mark.parametrize('name, image_format, expected', [
    ('Capture.PNG', 'png', 'Capture.PNG'),
    ('Capture.WebP', 'webp', 'Capture.WebP'),
    ('Capture.JPG', 'jpeg', 'Capture.JPG'),
    ('Capture.JpEg', 'jpeg', 'Capture.JpEg'),
    ('Capture.WebP', 'png', 'Capture.png'),
])
def test_explicit_save_and_upload_share_actual_filename(
        monkeypatch, tmp_path, name, image_format, expected):
    """显式输出保存与上传共用保留大小写或按实际格式回退的名称。"""
    hosts = load_hosts('provider: catbox')
    calls = install_upload(monkeypatch, lambda *args: True)
    code, payload, _, _ = run_cli(
        monkeypatch, tmp_path, str(tmp_path / name), hosts,
        image_format=image_format)
    assert code == 0
    assert calls == [(payload, expected, hosts)]
    saved = [path for path in tmp_path.iterdir() if path.is_file()]
    assert [path.name for path in saved] == [expected]
    assert saved[0].read_bytes() == payload


def test_save_failure_still_uploads_and_success_returns_zero(
        monkeypatch, tmp_path):
    """保存失败仍继续上传，且上传成功时整体成功。"""
    cli = cli_module()
    outcome = SaveOutcome(None, 'shot.jpg', ('截图保存失败：无法写入输出路径',))
    monkeypatch.setattr(cli, 'save_cli', lambda *args, **kwargs: outcome)
    calls = install_upload(monkeypatch, lambda *args: True)
    hosts = load_hosts('provider: catbox')
    code, payload, _, _ = run_cli(
        monkeypatch, tmp_path, str(tmp_path / 'shot.jpg'), hosts,
        image_format='jpeg')
    assert code == 0
    assert calls == [(payload, 'shot.jpg', hosts)]


def test_upload_failure_returns_one_but_keeps_saved_file(monkeypatch, tmp_path):
    """上传失败返回 1，但本地保存已经完成。"""
    hosts = load_hosts('provider: catbox')
    install_upload(monkeypatch, lambda *args: False)
    target = tmp_path / 'shot.jpg'
    code, _, _, _ = run_cli(
        monkeypatch, tmp_path, str(target), hosts, image_format='jpeg')
    assert code == 1
    assert target.exists()


@pytest.mark.parametrize('write_failed', [False, True])
def test_upload_uses_last_conflict_free_candidate_name(
        monkeypatch, tmp_path, capsys, write_failed):
    """目录候选名冲突递增后，上传沿用最终实际候选名。"""
    clock = Mock()
    clock.today.return_value = date(2026, 10, 11)
    monkeypatch.setattr(cli_module(), 'date', clock)
    hosts = load_hosts('provider: catbox')
    calls = install_upload(monkeypatch, lambda *args: True)
    output = tmp_path / 'shots'
    output.mkdir()
    first = output / 'screenshot_20261011_0000.jpg'
    first.write_bytes(b'existing')
    second = output / 'screenshot_20261011_0001.jpg'
    writes = []
    original_write = winfs._write

    def write_then_fail(handle, data):
        """真实写入部分字节后注入故障，由生产路径清理半成品。"""
        writes.append(data)
        original_write(handle, data[:2])
        raise OSError('synthetic-private-path')

    if write_failed:
        monkeypatch.setattr(winfs, '_write', write_then_fail)
    code, payload, _, _ = run_cli(
        monkeypatch, tmp_path, output=str(output), hosts=hosts,
        image_format='jpeg')
    assert code == 0
    clock.today.assert_called_once_with()
    assert len(calls) == 1
    assert calls[0][0] is payload
    assert calls[0][1:] == ('screenshot_20261011_0001.jpg', hosts)
    assert first.read_bytes() == b'existing'
    printed = capsys.readouterr().out
    if write_failed:
        assert writes == [payload]
        assert not second.exists()
        assert list(output.iterdir()) == [first]
        assert printed == '截图保存失败：无法写入输出路径\n'
    else:
        assert second.read_bytes() == payload
        assert set(output.iterdir()) == {first, second}
        assert printed == str(second) + '\n'


def test_raw_upload_is_rejected_before_capture(monkeypatch, tmp_path):
    """RAW 与上传组合在截图、保存与上传之前拒绝。"""
    cli = cli_module()
    capture = Mock()
    save = Mock()
    upload = Mock()
    monkeypatch.setattr(cli, 'capture', capture)
    monkeypatch.setattr(cli, 'save_cli', save)
    monkeypatch.setattr(cli, '_upload_all', upload)
    hosts = load_hosts('provider: catbox')
    assert cli.run_screenshot_cli(
        args_for(str(tmp_path / 'capture.raw'), hosts), str(tmp_path)) == 2
    capture.assert_not_called()
    save.assert_not_called()
    upload.assert_not_called()


def test_upload_all_continues_after_failed_prepare(monkeypatch):
    """首项准备失败仍尝试次项，但总结果失败且上下文只建一次。"""
    cli = cli_module()
    hosts = [{'provider': 'catbox', 'options': {}},
             {'provider': 'beeimg', 'options': {}}]

    def prepare(host):
        """首项模拟缺少环境变量，次项准备成功。"""
        if host['provider'] == 'catbox':
            return None, 'missing_environment'
        return dict(host, token=''), None

    monkeypatch.setattr(cli, 'prepare_cli_host', prepare)
    created = patch_context(monkeypatch)
    calls = []

    def upload(data, filename, prepared, context=None):
        """记录实际上传的 provider 并返回成功。"""
        calls.append(prepared[0]['provider'])
        return SimpleNamespace(success=True, url=URL)

    monkeypatch.setattr(cli, 'upload_with_fallback', upload)
    assert cli._upload_all(b'x', 'a.jpg', hosts) is False
    assert calls == ['beeimg']
    assert len(created) == 1


def test_upload_all_uploads_all_when_first_succeeds(monkeypatch):
    """首项成功后仍继续上传后续项，全部成功才返回成功。"""
    cli = cli_module()
    hosts = [{'provider': 'catbox', 'options': {}},
             {'provider': 'beeimg', 'options': {}}]
    monkeypatch.setattr(
        cli, 'prepare_cli_host', lambda host: (dict(host, token=''), None))
    patch_context(monkeypatch)
    calls = []

    def upload(data, filename, prepared, context=None):
        """记录每次上传的 provider 并返回成功。"""
        calls.append(prepared[0]['provider'])
        return SimpleNamespace(success=True, url=URL)

    monkeypatch.setattr(cli, 'upload_with_fallback', upload)
    assert cli._upload_all(b'x', 'a.jpg', hosts) is True
    assert calls == ['catbox', 'beeimg']


def test_upload_all_propagates_control_signals(monkeypatch):
    """控制信号以实例身份原样传播，不被吞掉。"""
    cli = cli_module()
    hosts = [{'provider': 'catbox', 'options': {}}]
    monkeypatch.setattr(
        cli, 'prepare_cli_host', lambda host: (dict(host, token=''), None))
    patch_context(monkeypatch)
    for signal in (SystemExit(9), KeyboardInterrupt()):
        def upload(*args, __signal=signal, **kwargs):
            """触发控制信号。"""
            raise __signal

        monkeypatch.setattr(cli, 'upload_with_fallback', upload)
        with pytest.raises(type(signal)) as caught:
            cli._upload_all(b'x', 'a.jpg', hosts)
        assert caught.value is signal


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_save_control_signal_keeps_image_and_cancels_unstarted_uploads(
        signal_type, monkeypatch, tmp_path):
    """保存控制信号保留图片成功事实，并取消保存和跳过全部上传槽位。"""
    cli = cli_module()
    signal = signal_type('stop-save')
    hosts = load_hosts('provider: catbox; provider: beeimg')

    def save(*args, **kwargs):
        """在真实保存阶段边界传播指定控制信号。"""
        with trace_operation('save', 'write'):
            raise signal

    monkeypatch.setattr(cli, 'save_cli', save)
    upload = Mock()
    monkeypatch.setattr(cli, '_upload_all', upload)
    events = []
    monkeypatch.setattr(
        cli, 'make_status_consumer', lambda: events.append)

    with pytest.raises(signal_type) as caught:
        run_cli(
            monkeypatch, tmp_path, str(tmp_path / 'shot.jpg'), hosts,
            image_format='jpeg')

    assert caught.value is signal
    upload.assert_not_called()
    image = [event for event in events if event.stage == 'batch'
             and event.status == 'cancelled'][0]
    slots = [event for event in events if event.stage == 'slot']
    saves = [event for event in events if event.stage == 'save']
    assert image.status == 'cancelled'
    assert [event.status for event in saves] == ['started', 'cancelled']
    assert [(event.status, event.reason_code) for event in slots] == [
        ('skipped', 'batch_cancelled'), ('skipped', 'batch_cancelled')]


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_upload_control_signal_keeps_completed_slot_and_skips_remaining(
        signal_type, monkeypatch):
    """上传控制信号不改写已完成槽位，仅跳过尚未开始的槽位。"""
    cli = cli_module()
    signal = signal_type('stop-upload')
    hosts = [
        {'provider': 'catbox', 'options': {}},
        {'provider': 'beeimg', 'options': {}},
        {'provider': 'wmimg', 'options': {}},
    ]
    monkeypatch.setattr(
        cli, 'prepare_cli_host', lambda host: (dict(host, token=''), None))
    patch_context(monkeypatch)
    results = iter([SimpleNamespace(success=True, url=URL), signal])

    def upload(*args, **kwargs):
        """首槽成功，次槽传播指定控制信号。"""
        result = next(results)
        if isinstance(result, BaseException):
            raise result
        trace_slot('catbox', 0).finish('succeeded')
        return result

    monkeypatch.setattr(cli, 'upload_with_fallback', upload)
    events = []
    with pytest.raises(signal_type) as caught:
        with trace_scope(mode='cli', images_planned=1, slots_planned=3,
                         on_status=events.append) as trace:
            cli._upload_all(b'x', 'a.jpg', hosts)
    assert caught.value is signal
    summary = trace.summary()
    assert summary['slots_planned'] == 3
    assert summary['slots_succeeded'] == 1
    assert summary['slots_cancelled'] == 1
    assert summary['slots_skipped'] == 1
    terminals = {event.slot_index: (event.status, event.reason_code)
                 for event in events
                 if event.stage == 'slot' and event.status != 'started'}
    assert terminals == {
        0: ('succeeded', ''),
        1: ('cancelled', 'batch_cancelled'),
        2: ('skipped', 'batch_cancelled'),
    }


def test_upload_all_keeps_real_slot_indices(monkeypatch, tmp_path):
    """同一图片的两个图床按真实配置项序号生成两个独立槽位。"""
    cli = cli_module()
    registry = import_module('modules.image_host.registry')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    hosts = [{'provider': 'catbox', 'token': 't'},
             {'provider': 'beeimg', 'token': 't'}]

    def failing(image, filename, token, options):
        """首项在真实上传链边界返回固定传输失败。"""
        raise ImageHostError('transport_failed', '')

    monkeypatch.setitem(registry.UPLOADERS, 'catbox', failing)
    monkeypatch.setitem(
        registry.UPLOADERS, 'beeimg',
        lambda image, filename, token, options: URL)
    events = []
    with trace_scope(mode='cli', images_planned=1, slots_planned=2,
                     on_status=events.append) as trace:
        assert cli._upload_all(b'x', 'a.jpg', hosts) is False
    summary = trace.summary()
    assert summary['slots_planned'] == 2
    assert summary['slots_failed'] == 1
    assert summary['slots_succeeded'] == 1
    slots = [event for event in events if event.stage == 'slot']
    assert sorted({(event.image_index, event.slot_index)
                   for event in slots}) == [(1, 0), (1, 1)]


def test_upload_all_records_prepare_failure_as_one_slot(monkeypatch):
    """准备失败零 POST，但补记恰好一个槽位失败终态。"""
    cli = cli_module()
    hosts = [{'provider': 'catbox', 'options': {}}]
    monkeypatch.setattr(
        cli, 'prepare_cli_host',
        lambda host: (None, 'missing_environment'))
    created = patch_context(monkeypatch)
    posts = []
    monkeypatch.setattr(
        cli, 'upload_with_fallback',
        lambda *args, **kwargs: posts.append(args))
    events = []
    with trace_scope(mode='cli', images_planned=1, slots_planned=1,
                     on_status=events.append) as trace:
        assert cli._upload_all(b'x', 'a.jpg', hosts) is False
    assert created == []
    assert posts == []
    summary = trace.summary()
    assert summary['upload_post_attempts'] == 0
    assert summary['slots_failed'] == 1
    assert summary['slots_succeeded'] == 0
    slots = [event for event in events if event.stage == 'slot']
    assert [event.status for event in slots] == ['started', 'failed']
    assert slots[1].reason_code == 'missing_environment'
