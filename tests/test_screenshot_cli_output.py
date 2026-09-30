#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证截图输出的路径判定、真实编码与保存失败分类。

测试只替换截图边界为内存合成的 CaptureResult，使用真实 Pillow 生成与
校验图像，不执行真实截图、ADB、窗口或图床调用。
"""

import os
from argparse import Namespace
from importlib import import_module
from importlib.util import find_spec
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
import pytest

from modules.screenshot.models import CaptureError, CaptureResult


def cli_module():
    """导入截图 CLI，模块缺失时让测试明确失败。"""
    assert find_spec('modules.screenshot.cli') is not None, '尚未实现截图 CLI'
    return import_module('modules.screenshot.cli')


def make_png(size=(8, 6), color=(10, 20, 30, 255)):
    """用真实 Pillow 生成可解码的 PNG 字节（含 alpha 通道）。"""
    image = Image.new('RGBA', size, color)
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def patch_capture(monkeypatch, result=None, error=None):
    """同时替换服务层与 CLI 的截图调用，返回可直接断言的 Mock。"""
    service = import_module('modules.screenshot.service')
    cli = cli_module()
    mock = Mock(side_effect=error) if error else Mock(return_value=result)
    monkeypatch.setattr(service, 'capture', mock)
    monkeypatch.setattr(cli, 'capture', mock, raising=False)
    return mock


def args_for(output, **overrides):
    """构造 run_screenshot_cli 所需的完整参数命名空间。"""
    values = {
        'source': 'window',
        'target': 'MuMu模拟器 1',
        'output': output,
        'upload': None,
        'image_host': None,
        'config': None,
    }
    values.update(overrides)
    return Namespace(**values)


def run_cli(monkeypatch, tmp_path, output, program_name='program', **kwargs):
    """在隔离目录内执行一次截图输出，返回退出码、合成 PNG 与程序根目录。"""
    program_dir = tmp_path / program_name
    program_dir.mkdir(exist_ok=True)
    size = kwargs.pop('size', (8, 6))
    png = make_png(size=size)
    result = CaptureResult(
        png, 'window', 'MuMu模拟器 1', size[0], size[1],
        kwargs.pop('warnings', ()))
    patch_capture(monkeypatch, result=result)
    code = cli_module().run_screenshot_cli(
        args_for(output, **kwargs), str(program_dir))
    return code, png, program_dir


def decode(path):
    """用真实 Pillow 打开产物，返回格式、尺寸与像素模式。"""
    with Image.open(path) as image:
        image.load()
        return image.format, image.size, image.mode


def created_pngs(directory):
    """列出目录内的 PNG 产物路径。"""
    return sorted(Path(directory).glob('*.png'))


def test_default_output_lives_under_program_dir(monkeypatch, tmp_path, capsys):
    """未指定 --output 时写入程序根目录下的 screenshot 子目录。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)

    code, _, program_dir = run_cli(monkeypatch, tmp_path, None)

    assert code == 0
    saved = created_pngs(program_dir / 'screenshot')
    assert len(saved) == 1
    assert decode(saved[0])[0] == 'PNG'
    assert not (work / 'screenshot').exists()
    assert str(saved[0]) in capsys.readouterr().out


def test_explicit_relative_path_is_relative_to_cwd(monkeypatch, tmp_path):
    """显式相对路径相对当前工作目录，而非程序根目录。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)

    code, _, program_dir = run_cli(monkeypatch, tmp_path, 'shots')

    assert code == 0
    assert len(created_pngs(work / 'shots')) == 1
    assert not (program_dir / 'shots').exists()
    assert not (program_dir / 'screenshot').exists()


def test_existing_directory_wins_over_image_suffix(monkeypatch, tmp_path):
    """已存在同名目录时优先按目录处理，即使后缀形似图片。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    (work / 'out.png').mkdir()

    code, _, _ = run_cli(monkeypatch, tmp_path, 'out.png')

    assert code == 0
    assert (work / 'out.png').is_dir()
    assert len(created_pngs(work / 'out.png')) == 1


@pytest.mark.parametrize('separator', [os.sep, '/'])
def test_trailing_separator_means_directory(monkeypatch, tmp_path, separator):
    """带尾随分隔符的取值按目录处理，即使是图片后缀。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)

    code, _, _ = run_cli(monkeypatch, tmp_path, 'bundle.png' + separator)

    assert code == 0
    assert (work / 'bundle.png').is_dir()
    assert len(created_pngs(work / 'bundle.png')) == 1


@pytest.mark.parametrize('name', ['shot.png', 'shot.PNG', 'shot.Png'])
def test_png_suffix_is_treated_as_file(monkeypatch, tmp_path, name):
    """PNG 后缀不区分大小写时按文件处理，且写入原始字节。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)

    code, png, _ = run_cli(monkeypatch, tmp_path, name)

    assert code == 0
    target = work / name
    assert target.is_file()
    assert target.read_bytes() == png
    assert decode(target)[0] == 'PNG'


@pytest.mark.parametrize('name', ['shot.jpg', 'shot.JPEG'])
def test_jpeg_suffix_is_real_jpeg_without_alpha(monkeypatch, tmp_path, name):
    """JPEG 后缀以真实 JPEG 编码落盘，并去除 alpha 通道。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)

    code, _, _ = run_cli(monkeypatch, tmp_path, name)

    assert code == 0
    target = work / name
    assert target.read_bytes().startswith(b'\xff\xd8')
    image_format, size, mode = decode(target)
    assert image_format == 'JPEG'
    assert size == (8, 6)
    assert mode == 'RGB'


def test_jpeg_encoder_documents_black_background_and_alpha_drop():
    """_encode_jpeg 的契约说明按黑底合成并丢弃 alpha，以保证 JPEG 无 alpha。"""
    document = cli_module()._encode_jpeg.__doc__ or ''
    assert '黑底' in document
    assert 'alpha' in document


def test_directory_with_other_suffix_is_created(monkeypatch, tmp_path):
    """非图片后缀的取值按目录处理。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)

    code, _, _ = run_cli(monkeypatch, tmp_path, 'shots.dat')

    assert code == 0
    assert (work / 'shots.dat').is_dir()


def test_missing_nested_directory_is_created(monkeypatch, tmp_path):
    """不存在的多级输出目录自动创建。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)

    code, _, _ = run_cli(monkeypatch, tmp_path, os.path.join('a', 'b', 'c'))

    assert code == 0
    assert len(created_pngs(work / 'a' / 'b' / 'c')) == 1


def test_explicit_file_parent_directory_is_created(monkeypatch, tmp_path):
    """显式文件路径的缺失父目录自动创建。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)

    code, png, _ = run_cli(
        monkeypatch, tmp_path, os.path.join('nested', 'shot.png'))

    assert code == 0
    assert (work / 'nested' / 'shot.png').read_bytes() == png


def test_directory_creation_failure_returns_one(monkeypatch, tmp_path, capsys):
    """目录创建失败时报错返回 1，不更换路径也不回退默认目录。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    blocked = work / 'blocked'
    blocked.write_bytes(b'not-a-directory')

    code, _, program_dir = run_cli(monkeypatch, tmp_path, 'blocked')

    assert code == 1
    assert blocked.read_bytes() == b'not-a-directory'
    assert not (program_dir / 'screenshot').exists()
    assert '失败' in capsys.readouterr().out


def test_write_failure_returns_one_without_details(
        monkeypatch, tmp_path, capsys):
    """写入失败返回 1 并输出固定分类，不泄露底层异常内容。"""
    cli = cli_module()
    program_dir = tmp_path / 'program'
    program_dir.mkdir()
    png = make_png()
    result = CaptureResult(png, 'window', 'MuMu模拟器 1', 8, 6)
    patch_capture(monkeypatch, result=result)
    monkeypatch.setattr(
        cli, '_write_exclusive_file',
        Mock(side_effect=OSError('secret-token')))

    code = cli.run_screenshot_cli(args_for(None), str(program_dir))

    assert code == 1
    out = capsys.readouterr().out
    assert '失败' in out
    assert '无法写入输出路径' in out
    assert '目标文件已存在' not in out
    assert 'secret-token' not in out
    assert created_pngs(program_dir / 'screenshot') == []


def test_existing_explicit_file_is_not_overwritten(
        monkeypatch, tmp_path, capsys):
    """显式文件已存在时报错返回 1，使用未覆盖专用提示且不覆盖既有内容。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    target = work / 'shot.png'
    target.write_bytes(b'user-data')

    code, _, _ = run_cli(monkeypatch, tmp_path, 'shot.png')

    assert code == 1
    assert target.read_bytes() == b'user-data'
    out = capsys.readouterr().out
    assert '失败' in out
    assert '目标文件已存在' in out
    assert '无法写入输出路径' not in out


def test_consecutive_calls_do_not_overwrite(monkeypatch, tmp_path):
    """连续两次调用生成两个互不覆盖且均可解码的 PNG。"""
    code_first, _, program_dir = run_cli(monkeypatch, tmp_path, None)
    code_second, _, _ = run_cli(monkeypatch, tmp_path, None)

    assert (code_first, code_second) == (0, 0)
    saved = created_pngs(program_dir / 'screenshot')
    assert len(saved) == 2
    assert saved[0] != saved[1]
    assert all(decode(path)[0] == 'PNG' for path in saved)


def test_same_timestamp_calls_use_distinct_names(monkeypatch, tmp_path):
    """时间戳令牌相同时仍以序号生成不同文件名。"""
    cli = cli_module()
    monkeypatch.setattr(cli, '_timestamp_token', lambda: 'STAMP')
    code_first, _, program_dir = run_cli(monkeypatch, tmp_path, None)
    code_second, _, _ = run_cli(monkeypatch, tmp_path, None)

    assert (code_first, code_second) == (0, 0)
    names = sorted(path.name for path in created_pngs(
        program_dir / 'screenshot'))
    assert names == ['screenshot_STAMP.png', 'screenshot_STAMP_1.png']


def test_partial_write_discards_new_file_only(monkeypatch, tmp_path):
    """写入中途失败只清理本次半成品，保留用户既有文件。"""
    cli = cli_module()
    existing = tmp_path / 'existing.png'
    existing.write_bytes(b'keep')
    target = tmp_path / 'out.png'

    class FlakyHandle:
        """先真实创建并写入部分内容，再在写入时失败的文件替身。"""

        def __init__(self, path):
            """记录目标路径。"""
            self.path = path
            self.real = None

        def __enter__(self):
            """真实排他创建文件并写入部分内容。"""
            self.real = open(self.path, 'xb')
            self.real.write(b'partial')
            return self

        def write(self, data):
            """模拟磁盘写入中途失败。"""
            raise OSError('disk-full: secret-token')

        def __exit__(self, *exc_info):
            """关闭真实文件句柄。"""
            self.real.close()
            return False

    def fake_open(path, mode='r', *args, **kwargs):
        """返回失败替身。"""
        return FlakyHandle(path)

    monkeypatch.setattr(cli, 'open', fake_open, raising=False)

    with pytest.raises(OSError):
        cli._write_exclusive_file(str(target), b'payload')

    assert not target.exists()
    assert existing.read_bytes() == b'keep'


def test_warnings_are_surfaced_to_terminal(monkeypatch, tmp_path, capsys):
    """截图告警随成功信息一并输出，不改变退出码。"""
    code, _, _ = run_cli(
        monkeypatch, tmp_path, None, warnings=('可疑的纯色图像',))

    assert code == 0
    assert '可疑的纯色图像' in capsys.readouterr().out


def test_success_message_reports_source_target_size_and_path(
        monkeypatch, tmp_path, capsys):
    """成功输出包含来源、目标、尺寸与完整保存路径。"""
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)

    code, _, program_dir = run_cli(monkeypatch, tmp_path, 'shots')

    assert code == 0
    out = capsys.readouterr().out
    assert 'window' in out
    assert 'MuMu模拟器 1' in out
    assert '8x6' in out
    saved = created_pngs(work / 'shots')[0]
    assert str(saved) in out
    assert program_dir not in saved.parents


def test_capture_failure_returns_one_and_saves_nothing(
        monkeypatch, tmp_path, capsys):
    """截图失败返回 1 且不产生任何输出文件。"""
    program_dir = tmp_path / 'program'
    program_dir.mkdir()
    patch_capture(monkeypatch, error=CaptureError('backend_failed', '安全消息'))

    code = cli_module().run_screenshot_cli(args_for(None), str(program_dir))

    assert code == 1
    assert not (program_dir / 'screenshot').exists()
    assert 'backend_failed' in capsys.readouterr().out


@pytest.mark.parametrize('args', [
    pytest.param(Namespace(source='auto', target='x', output=None),
                 id='unknown-source'),
    pytest.param(Namespace(source='adb', target='', output=None),
                 id='empty-target'),
])
def test_invalid_arguments_return_two(monkeypatch, tmp_path, args):
    """入参不合法时返回 2，且不截图、不写出文件。"""
    program_dir = tmp_path / 'program'
    program_dir.mkdir()
    patch_capture(monkeypatch, result=CaptureResult(b'png', 'adb', 'x', 1, 1))

    assert cli_module().run_screenshot_cli(args, str(program_dir)) == 2
    assert list(program_dir.iterdir()) == []


def test_output_ignores_config_and_upload_options(monkeypatch, tmp_path):
    """输出路径不读取配置，也不因上传预留选项改变行为。"""
    config = import_module('modules.config')
    config_spy = Mock(return_value={})
    monkeypatch.setattr(config, 'load_config', config_spy)

    code, _, program_dir = run_cli(
        monkeypatch, tmp_path, None, upload='true',
        image_host='superbed', config='custom.yaml')

    assert code == 0
    config_spy.assert_not_called()
    assert len(created_pngs(program_dir / 'screenshot')) == 1
