#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""截图调试 CLI 的输出路径、格式与保存失败行为。"""

from argparse import Namespace
from importlib import import_module
from io import BytesIO
import netrc
import os
import socket
import sys
from unittest.mock import Mock

from PIL import Image
import pytest
import requests

from modules.screenshot.models import CaptureError, CaptureResult
from modules.screenshot.retention import SaveOutcome


URL = 'https://cdn.example.com/result.jpg'


def cli_module():
    """导入 CLI 模块。"""
    return import_module('modules.screenshot.cli')


def make_image(image_format='png', size=(8, 6)):
    """生成指定格式的真实图像字节；RAW 无编码器时以 PNG 代替载荷。"""
    buffer = BytesIO()
    if image_format == 'raw':
        Image.new('RGBA', size, (10, 20, 30)).save(buffer, format='PNG')
        return buffer.getvalue()
    mode = 'RGB' if image_format == 'jpeg' else 'RGBA'
    Image.new(mode, size, (10, 20, 30)).save(buffer, format=image_format.upper())
    return buffer.getvalue()


def args_for(output, **overrides):
    """构造完整 CLI 参数。"""
    values = dict(source='window', target='MuMu模拟器 1', output=output,
                  hosts=None)
    values.update(overrides)
    return Namespace(**values)


def run_cli(monkeypatch, tmp_path, output, image_format='png', **overrides):
    """使用合成截图执行一次 CLI。"""
    program_dir = tmp_path / 'program'
    program_dir.mkdir(exist_ok=True)
    payload = make_image(image_format)
    result = CaptureResult(payload, 'window', 'MuMu模拟器 1', 8, 6,
                           image_format=image_format)
    capture = Mock(return_value=result)
    monkeypatch.setattr(cli_module(), 'capture', capture)
    code = cli_module().run_screenshot_cli(
        args_for(output, **overrides), str(program_dir))
    return code, payload, program_dir, capture


def test_capture_without_output_defaults_to_jpeg(monkeypatch, tmp_path):
    """空 --output 使用默认目录并默认请求 JPEG。"""
    code, payload, program_dir, capture = run_cli(
        monkeypatch, tmp_path, '', image_format='jpeg')
    assert code == 0
    capture.assert_called_once_with(
        'window', 'MuMu模拟器 1', image_format='jpeg', purpose='cli')
    saved = list((program_dir / 'screenshot').glob('*.jpg'))
    assert len(saved) == 1
    assert saved[0].name.startswith('screenshot_')
    assert saved[0].read_bytes() == payload


def test_output_none_does_not_save_and_fails_without_upload(monkeypatch, tmp_path):
    """没有保存目标且没有上传时不创建文件，返回失败。"""
    code, _, program_dir, _ = run_cli(monkeypatch, tmp_path, None)
    assert code == 1
    assert list(program_dir.iterdir()) == []


@pytest.mark.parametrize(('suffix', 'image_format'), [
    ('.jpg', 'jpeg'), ('.jpeg', 'jpeg'), ('.png', 'png'),
    ('.webp', 'webp'), ('.raw', 'raw'),
])
def test_explicit_suffix_selects_capture_format(monkeypatch, tmp_path,
                                                suffix, image_format):
    """显式文件后缀决定 capture 请求格式。"""
    target = str(tmp_path / ('shot' + suffix))
    code, _, _, capture = run_cli(monkeypatch, tmp_path, target,
                                  image_format=image_format)
    assert code == 0
    capture.assert_called_once_with(
        'window', 'MuMu模拟器 1', image_format=image_format, purpose='cli')


def test_raw_upload_rejected_before_capture(monkeypatch, tmp_path):
    """RAW 与上传组合在截图、保存与上传之前拒绝。"""
    cli = cli_module()
    capture = Mock()
    save = Mock()
    upload = Mock()
    monkeypatch.setattr(cli, 'capture', capture)
    monkeypatch.setattr(cli, 'save_cli', save)
    monkeypatch.setattr(cli, '_upload_all', upload)
    assert cli.run_screenshot_cli(
        args_for(str(tmp_path / 'shot.raw'), hosts=[{'provider': 'catbox'}]),
        str(tmp_path)) == 2
    capture.assert_not_called()
    save.assert_not_called()
    upload.assert_not_called()


def test_existing_explicit_file_is_not_overwritten(monkeypatch, tmp_path):
    """既有显式文件不覆盖并返回失败。"""
    target = tmp_path / 'shot.png'
    target.write_bytes(b'user-data')
    code, _, _, _ = run_cli(monkeypatch, tmp_path, str(target))
    assert code == 1
    assert target.read_bytes() == b'user-data'


def test_save_failure_returns_one_without_upload(monkeypatch, tmp_path):
    """纯保存失败返回 1。"""
    cli = cli_module()
    outcome = SaveOutcome(None, 'shot.png', ('截图保存失败：无法写入输出路径',))
    monkeypatch.setattr(cli, 'save_cli', lambda *args, **kwargs: outcome)
    code, _, _, _ = run_cli(monkeypatch, tmp_path, str(tmp_path / 'shot.png'))
    assert code == 1


def test_capture_failure_returns_one(monkeypatch, tmp_path):
    """截图异常以固定失败码返回。"""
    capture = Mock(side_effect=CaptureError('backend_failed', 'secret'))
    monkeypatch.setattr(cli_module(), 'capture', capture)
    assert cli_module().run_screenshot_cli(
        args_for(str(tmp_path / 'shot.png')), str(tmp_path)) == 1


def test_explicit_directory_is_relative_to_cwd(monkeypatch, tmp_path):
    """显式相对目录位于当前目录，空 output 的默认目录位于程序目录。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    code, _, program_dir, _ = run_cli(
        monkeypatch, tmp_path, 'shots', image_format='jpeg')
    assert code == 0
    assert len(list((work / 'shots').glob('*.jpg'))) == 1
    assert not (program_dir / 'screenshot').exists()


def test_managed_date_target_is_rejected_before_capture(monkeypatch, tmp_path):
    """受管日期目录内的目标是保留区的，截图前拒绝。"""
    capture = Mock()
    monkeypatch.setattr(cli_module(), 'capture', capture)
    managed = tmp_path / 'program' / 'screenshot' / '2026_10_07'
    managed.mkdir(parents=True)
    code = cli_module().run_screenshot_cli(
        args_for(str(managed / 'shot.png')), str(tmp_path / 'program'))
    assert code == 2
    capture.assert_not_called()


def test_managed_date_target_is_rejected_even_when_absent(monkeypatch, tmp_path):
    """受管日期目录即使尚不存在也按保留区拒绝。"""
    capture = Mock()
    monkeypatch.setattr(cli_module(), 'capture', capture)
    absent = tmp_path / 'program' / 'screenshot' / '2026_10_07' / 'shots'
    code = cli_module().run_screenshot_cli(
        args_for(str(absent)), str(tmp_path / 'program'))
    assert code == 2
    capture.assert_not_called()


def test_date_directory_outside_managed_root_is_allowed(monkeypatch, tmp_path):
    """程序目录之外的同名日期目录不属于保留区，允许输出。"""
    outside = tmp_path / '2026_10_07' / 'shots'
    code, _, _, _ = run_cli(monkeypatch, tmp_path, str(outside),
                            image_format='jpeg')
    assert code == 0
    assert len(list(outside.glob('*.jpg'))) == 1


def test_existing_image_named_directory_is_treated_as_directory(monkeypatch, tmp_path):
    """已存在的图片后缀同名目录优先按目录处理。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    (work / 'out.png').mkdir()
    code, _, _, _ = run_cli(monkeypatch, tmp_path, 'out.png')
    assert code == 0
    assert len(list((work / 'out.png').glob('*.png'))) == 1


def test_trailing_separator_means_directory(monkeypatch, tmp_path):
    """带尾随分隔符的取值按目录处理，即使是图片后缀。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    code, _, _, _ = run_cli(
        monkeypatch, tmp_path, 'bundle.png' + os.sep)
    assert code == 0
    assert (work / 'bundle.png').is_dir()
    assert len(list((work / 'bundle.png').glob('*.png'))) == 1


@pytest.mark.parametrize(('suffix', 'requested'), [
    ('.jpg', 'jpeg'), ('.jpeg', 'jpeg'), ('.webp', 'webp'), ('.raw', 'raw')])
def test_png_fallback_renames_and_keeps_existing_png(monkeypatch, tmp_path,
                                                     suffix, requested):
    """显式非 PNG 后缀但实际为 PNG 时改用 .png 名且不覆盖既有 .png。"""
    target = tmp_path / ('capture' + suffix)
    existing = tmp_path / 'capture.png'
    existing.write_bytes(b'user-data')
    code, _, _, capture = run_cli(
        monkeypatch, tmp_path, str(target), image_format='png')
    assert code == 1
    capture.assert_called_once_with(
        'window', 'MuMu模拟器 1', image_format=requested, purpose='cli')
    assert existing.read_bytes() == b'user-data'
    assert not target.exists()


def test_capture_warnings_are_printed(monkeypatch, tmp_path, capsys):
    """截图回退告警在成功路径原样输出。"""
    result = CaptureResult(make_image('jpeg'), 'window', 'MuMu模拟器 1', 8, 6,
                           warnings=('已回退为 PNG',), image_format='jpeg')
    monkeypatch.setattr(cli_module(), 'capture', Mock(return_value=result))
    code = cli_module().run_screenshot_cli(
        args_for(str(tmp_path / 'shot.jpg')), str(tmp_path))
    assert code == 0
    assert '已回退为 PNG' in capsys.readouterr().out


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """隔离缓存根并阻断任何未替身化的真实网络或账号访问。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    counter = {'forbidden': 0}

    def forbidden(*args, **kwargs):
        """记录并阻断未替身化的网络或账号访问。"""
        counter['forbidden'] += 1
        raise AssertionError('禁止真实网络或账号访问')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(netrc, 'netrc', forbidden)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', forbidden)
    yield
    assert counter['forbidden'] == 0


class RecordingStream:
    """可控 TTY 判定的输出流替身，按顺序记录写入与刷新。"""

    def __init__(self, is_tty):
        """保存 TTY 判定并初始化写入与刷新记录。"""
        self._is_tty = is_tty
        self.writes = []
        self.flushes = 0

    def write(self, text):
        """记录一次写入。"""
        self.writes.append(text)

    def flush(self):
        """记录一次刷新。"""
        self.flushes += 1

    def isatty(self):
        """返回构造时指定的 TTY 判定。"""
        return self._is_tty

    def text(self):
        """按写入顺序拼接全部文本。"""
        return ''.join(self.writes)


def install_hosts(monkeypatch, cli):
    """把两个图床都替换为合成成功链接，不发起任何请求。"""
    registry = import_module('modules.image_host.registry')
    monkeypatch.setitem(registry.UPLOADERS, 'catbox',
                        lambda image, filename, token, options: URL)
    monkeypatch.setitem(registry.UPLOADERS, 'wmimg',
                        lambda image, filename, token, options: URL)


def two_host_args(saved_path):
    """构造两个声明图床的 CLI 参数，凭证取值不落入安全名称。"""
    token = 'CLI_MATRIX_TOKEN_1'
    return args_for(saved_path, hosts=[{'provider': 'catbox', 'token': token},
                                       {'provider': 'wmimg', 'token': token}])


@pytest.mark.parametrize('stdout_tty', [False, True])
@pytest.mark.parametrize('stderr_tty', [False, True])
def test_cli_feedback_stream_matrix(
        monkeypatch, tmp_path, stdout_tty, stderr_tty):
    """四种 TTY 组合下结果行只在 stdout，阶段与安全终态只在 stderr。"""
    cli = cli_module()
    out = RecordingStream(stdout_tty)
    err = RecordingStream(stderr_tty)
    monkeypatch.setattr(sys, 'stdout', out)
    monkeypatch.setattr(sys, 'stderr', err)
    install_hosts(monkeypatch, cli)
    result = CaptureResult(make_image('jpeg'), 'window', 'MuMu模拟器 1', 8, 6,
                           image_format='jpeg')
    monkeypatch.setattr(cli, 'capture', Mock(return_value=result))
    saved_path = str(tmp_path / 'shot.jpg')
    monkeypatch.setattr(cli, 'save_cli', Mock(
        return_value=SaveOutcome(saved_path, 'shot.jpg')))
    code = cli.run_screenshot_cli(
        two_host_args(saved_path), str(tmp_path))
    assert code == 0
    assert out.text().splitlines() == [
        saved_path, 'catbox：上传成功 ' + URL, 'wmimg：上传成功 ' + URL]
    assert '正在上传截图' not in out.text()
    assert '\r' not in out.text() and '\x1b' not in out.text()
    assert URL not in err.text() and saved_path not in err.text()
    assert '正在上传截图' in err.text()
    assert '✔️ 通道 [Catbox] 上传成功' in err.text()
    assert '✔️ 通道 [WMImg] 上传成功' in err.text()
    if stderr_tty:
        assert '\x1b' in err.text() and '\r' in err.text()
    else:
        assert '\x1b' not in err.text() and '\r' not in err.text()


@pytest.mark.parametrize('mode', [
    'disabled', 'no_hosts', 'save_only', 'capture_failure', 'config_error'])
def test_no_upload_animation_for_early_paths(monkeypatch, tmp_path, mode):
    """提前返回路径不创建上传阶段：禁用、无图床、仅保存、捕获失败与配置错误。"""
    pipeline = import_module('modules.screenshot.pipeline')
    cli = cli_module()
    upload_status = import_module('modules.upload_status')
    stream = RecordingStream(False)
    created = []

    def factory(**kwargs):
        """记录构造并把消费者输出定向到本地流替身。"""
        kwargs['stream'] = stream
        consumer = upload_status.make_status_consumer(**kwargs)
        created.append(consumer)
        return consumer

    payload = CaptureResult(make_image('jpeg'), 'window', '窗口', 8, 6,
                            image_format='jpeg')
    if mode in ('disabled', 'no_hosts', 'config_error'):
        monkeypatch.setattr(pipeline, 'make_status_consumer', factory)
        monkeypatch.setattr(pipeline, 'capture', Mock(return_value=payload))
        monkeypatch.setattr(pipeline, 'save_automatic', Mock(
            return_value=SaveOutcome(None, 'shot.jpg')))
        section = {'targets': [{'provider': 'window', 'target': '窗口'}]}
        if mode == 'no_hosts':
            section['image_host'] = []
        if mode == 'config_error':
            section['targets'] = [{'provider': 'unknown', 'target': '窗口'}]
            section['image_host'] = [{'provider': 'catbox', 'token': 't'}]
        pipeline.prepare_screenshots(section, mode != 'disabled', ())
        if mode == 'disabled':
            assert created == []
    else:
        monkeypatch.setattr(cli, 'make_status_consumer', factory)
        error = (CaptureError('window_not_found', '')
                 if mode == 'capture_failure' else None)
        monkeypatch.setattr(cli, 'capture', Mock(
            side_effect=error, return_value=payload))
        if mode == 'capture_failure':
            hosts = [{'provider': 'catbox', 'token': 't'}]
            args = args_for(None, hosts=hosts)
        else:
            saved_path = str(tmp_path / 'shot.jpg')
            monkeypatch.setattr(cli, 'save_cli', Mock(
                return_value=SaveOutcome(saved_path, 'shot.jpg')))
            args = args_for(saved_path)
        cli.run_screenshot_cli(args, str(tmp_path))
    assert stream.text() == ''
