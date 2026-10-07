#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证截图调试 CLI 的参数归一、错误分类与入口分流。

测试只替换截图与配置的模块级边界，使用内存合成的 CaptureResult，
不执行真实截图、ADB、窗口或图床调用。
"""

import os
import sys
from argparse import Namespace
from importlib import import_module
from importlib.util import find_spec
from io import BytesIO
from unittest.mock import MagicMock, Mock

from PIL import Image
import pytest

from modules.screenshot.models import CaptureError, CaptureResult


def cli_module():
    """导入截图 CLI，模块缺失时让测试明确失败。"""
    assert find_spec('modules.screenshot.cli') is not None, '尚未实现截图 CLI 参数解析'
    return import_module('modules.screenshot.cli')


def entry_module():
    """导入程序入口模块，用于验证命令分流。"""
    return import_module('2RPM')


def parse(argv):
    """调用真实解析接口，验证归一结果而非 argparse 内部行为。"""
    return cli_module().parse_screenshot_args(argv)


def patch_capture(monkeypatch, mock):
    """同时替换服务层与 CLI 的截图调用，不依赖具体绑定方式。"""
    service = import_module('modules.screenshot.service')
    monkeypatch.setattr(service, 'capture', mock)
    monkeypatch.setattr(cli_module(), 'capture', mock, raising=False)
    return mock


def _spies(monkeypatch):
    """安装截图与配置监视，返回可直接断言的 Mock。"""
    capture = patch_capture(monkeypatch, Mock())
    config = import_module('modules.config')
    config_spy = Mock()
    monkeypatch.setattr(config, 'load_config', config_spy)
    return capture, config_spy


def _png_bytes(size=(4, 3)):
    """用真实 Pillow 生成可解码的 PNG 字节。"""
    buffer = BytesIO()
    Image.new('RGB', size, (12, 34, 56)).save(buffer, format='PNG')
    return buffer.getvalue()


@pytest.mark.parametrize(('argv', 'expected'), [
    (['--source', 'adb:127.0.0.1:16384'], ('adb', '127.0.0.1:16384')),
    (['--source', 'window:MuMu模拟器 1'], ('window', 'MuMu模拟器 1')),
    (['--source', 'MuMu模拟器 1'], ('window', 'MuMu模拟器 1')),
    (['--source', 'adb', '127.0.0.1:16384'], ('adb', '127.0.0.1:16384')),
    (['--source', 'window', 'MuMu模拟器 1'], ('window', 'MuMu模拟器 1')),
    (['--source', 'adb', '--target', '127.0.0.1:16384'],
     ('adb', '127.0.0.1:16384')),
    (['--source', 'window', '--target', 'MuMu模拟器 1'],
     ('window', 'MuMu模拟器 1')),
])
def test_each_documented_form_maps_to_expected_pair(argv, expected):
    """逐条验证七种写法对应的来源与目标。"""
    args = parse(argv)
    assert (args.source, args.target) == expected


@pytest.mark.parametrize('argv', [
    pytest.param(['--source', 'adb:127.0.0.1:16384'], id='inline'),
    pytest.param(['--source', 'adb', '127.0.0.1:16384'], id='positional'),
    pytest.param(['--source', 'adb', '--target', '127.0.0.1:16384'],
                 id='option-target'),
])
def test_adb_forms_are_equivalent(argv):
    """ADB 的三种等价写法归一到同一 serial。"""
    args = parse(argv)
    assert (args.source, args.target) == ('adb', '127.0.0.1:16384')


@pytest.mark.parametrize('argv', [
    pytest.param(['--source', 'window:MuMu模拟器 1'], id='prefix'),
    pytest.param(['--source', 'MuMu模拟器 1'], id='bare-title'),
    pytest.param(['--source', 'window', 'MuMu模拟器 1'], id='positional'),
    pytest.param(['--source', 'window', '--target', 'MuMu模拟器 1'],
                 id='option-target'),
])
def test_window_forms_are_equivalent(argv):
    """窗口标题的四种等价写法归一到 window 后端。"""
    args = parse(argv)
    assert (args.source, args.target) == ('window', 'MuMu模拟器 1')


@pytest.mark.parametrize('argv', [
    pytest.param(['--source', 'window:adb'], id='inline-prefix'),
    pytest.param(['--source', 'window', 'adb'], id='positional'),
    pytest.param(['--source', 'window', '--target', 'adb'],
                 id='option-target'),
])
def test_keyword_like_titles_use_window_form(argv):
    """窗口名恰为 adb 时用 window: 形式明确表达。"""
    args = parse(argv)
    assert (args.source, args.target) == ('window', 'adb')


@pytest.mark.parametrize('argv', [
    pytest.param(['--source', 'window:window'], id='inline-prefix'),
    pytest.param(['--source', 'window', 'window'], id='positional'),
])
def test_window_title_named_window(argv):
    """窗口名恰为 window 时仍归一到 window 后端。"""
    args = parse(argv)
    assert (args.source, args.target) == ('window', 'window')


@pytest.mark.parametrize(('argv', 'expected'), [
    (['--source', 'window:\t标题 \t'], ('window', '\t标题 \t')),
    (['--source', '\t标题 \t'], ('window', '\t标题 \t')),
    (['--source', 'window', '\t标题 \t'], ('window', '\t标题 \t')),
    (['--source', 'window', '--target', '\t标题 \t'],
     ('window', '\t标题 \t')),
])
def test_window_titles_preserve_surrounding_whitespace(argv, expected):
    """窗口标题四种写法均保留首尾空白，供窗口匹配原样使用。"""
    args = parse(argv)
    assert (args.source, args.target) == expected


@pytest.mark.parametrize('argv', [
    pytest.param(['--source', 'window: \t'], id='inline-prefix-blank'),
    pytest.param(['--source', ' \t '], id='bare-title-blank'),
    pytest.param(['--source', 'window', ' \t '], id='positional-blank'),
    pytest.param(['--source', 'window', '--target', ' \t '],
                 id='option-target-blank'),
])
def test_window_titles_reject_all_whitespace(argv):
    """窗口标题全为空白时仍然拒绝。"""
    with pytest.raises(SystemExit) as exit_info:
        parse(argv)
    assert exit_info.value.code == 2


@pytest.mark.parametrize(('argv', 'expected'), [
    (['--source', ' adb: 127.0.0.1:16384 '], ('adb', '127.0.0.1:16384')),
    (['--source', ' adb ', ' 127.0.0.1:16384 '], ('adb', '127.0.0.1:16384')),
    (['--source', '  MuMu模拟器 1  '], ('window', '  MuMu模拟器 1  ')),
    (['--source', '\twindow: 标题\t '], ('window', ' 标题\t ')),
    (['--source', ' window\t', ' 标题 '], ('window', ' 标题 ')),
    (['--source', '\tadb ', '--target', '\tserial '], ('adb', 'serial')),
    (['--source', ' window ', '--target', ' MuMu模拟器 1 '],
     ('window', ' MuMu模拟器 1 ')),
])
def test_source_keywords_are_normalized_without_trimming_window_titles(
        argv, expected):
    """来源关键字独立去空白，窗口目标保留原始取值。"""
    args = parse(argv)
    assert (args.source, args.target) == expected


def test_adb_serial_is_opaque_and_not_reclassified():
    """ADB 目标不做外形判定，不因带冒号或 window: 前缀改判。"""
    args = parse(['--source', 'adb', 'window:测试窗口'])
    assert (args.source, args.target) == ('adb', 'window:测试窗口')
    args = parse(['--source', 'adb', '--target', 'emulator-5554'])
    assert (args.source, args.target) == ('adb', 'emulator-5554')


def test_inline_upload_option_is_parsed_and_validated():
    """内联上传配置可被解析并保留为已校验图床列表。"""
    args = parse([
        '--source', 'window:MuMu模拟器 1',
        '--output', 'debug.png',
        '--upload', 'provider: catbox',
    ])
    assert (args.source, args.target) == ('window', 'MuMu模拟器 1')
    assert args.output == 'debug.png'
    assert args.hosts == [{'provider': 'catbox', 'options': {}}]


@pytest.mark.parametrize('option', [
    ['--image-host', 'catbox'],
    ['-c', 'custom.yaml'],
])
def test_legacy_upload_options_are_rejected(option):
    """旧上传参数在截图前拒绝。"""
    with pytest.raises(SystemExit) as exit_info:
        parse(['--source', 'window:MuMu模拟器 1'] + option)
    assert exit_info.value.code == 2


def test_upload_defaults_to_disabled():
    """未提供内联上传时不创建图床项。"""
    args = parse(['--source', 'window:MuMu模拟器 1'])
    assert args.hosts is None


@pytest.mark.parametrize('argv', [
    pytest.param([], id='missing-source'),
    pytest.param(['--source', ''], id='empty-source-value'),
    pytest.param(['--source', '   '], id='blank-source-value'),
    pytest.param(['--source', 'adb:127.0.0.1:16384', '--source', 'window:X'],
                 id='duplicate-source'),
    pytest.param(['--source', 'adb'], id='adb-keyword-without-target'),
    pytest.param(['--source', 'window'], id='window-keyword-without-target'),
    pytest.param(['--source', 'adb:'], id='inline-prefix-empty'),
    pytest.param(['--source', 'window:   '], id='inline-prefix-blank'),
    pytest.param(['--source', 'adb:127.0.0.1:16384', '127.0.0.1:16384'],
                 id='inline-and-positional'),
    pytest.param(['--source', 'adb:127.0.0.1:16384', '--target', 'serial'],
                 id='inline-and-option-target'),
    pytest.param(['--source', 'MuMu模拟器 1', '--target', 'MuMu模拟器 1'],
                 id='title-and-option-target'),
    pytest.param(['--source', 'adb', '127.0.0.1:16384', '--target', 'serial'],
                 id='positional-and-option-target'),
    pytest.param(['--source', 'adb', '127.0.0.1:16384', 'extra'],
                 id='extra-positional'),
    pytest.param(['--source', 'adb', '--target'], id='option-target-no-value'),
    pytest.param(['--', 'source', 'adb:127.0.0.1:16384'],
                 id='dash-dash-source'),
    pytest.param(['-- source', 'adb:127.0.0.1:16384'],
                 id='joined-dash-source'),
])
def test_argument_errors_exit_with_code_two(monkeypatch, argv):
    """参数错误以退出码 2 表达，且不截图、不读取配置。"""
    capture, config_spy = _spies(monkeypatch)
    with pytest.raises(SystemExit) as exit_info:
        parse(argv)
    assert exit_info.value.code == 2
    capture.assert_not_called()
    config_spy.assert_not_called()


def test_argument_error_creates_no_config_file(monkeypatch, tmp_path):
    """参数错误不触发默认配置文件创建。"""
    capture, config_spy = _spies(monkeypatch)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        parse(['--source', 'adb'])
    assert exit_info.value.code == 2
    capture.assert_not_called()
    config_spy.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_run_prints_source_target_and_size(monkeypatch, capsys, tmp_path):
    """run_screenshot_cli 成功时返回 0 并输出来源、目标与尺寸，同时落盘。"""
    cli = cli_module()
    expected = CaptureResult(
        _png_bytes(), 'window', 'MuMu模拟器 1', 320, 240)
    capture = patch_capture(monkeypatch, Mock(return_value=expected))
    config = import_module('modules.config')
    load_spy = Mock()
    monkeypatch.setattr(config, 'load_config', load_spy)

    args = parse(['--source', 'window:MuMu模拟器 1'])

    assert cli.run_screenshot_cli(args, str(tmp_path)) == 0
    capture.assert_called_once_with('window', 'MuMu模拟器 1')
    load_spy.assert_not_called()
    out = capsys.readouterr().out
    assert 'window' in out
    assert 'MuMu模拟器 1' in out
    assert '320' in out and '240' in out
    saved = list((tmp_path / 'screenshot').glob('*.png'))
    assert len(saved) == 1
    assert str(saved[0]) in out


def test_run_returns_one_on_capture_failure(monkeypatch, capsys):
    """截图失败返回 1，并输出固定安全分类。"""
    cli = cli_module()
    error = CaptureError('backend_failed', '固定安全消息')
    capture = patch_capture(monkeypatch, Mock(side_effect=error))
    args = parse(['--source', 'adb:127.0.0.1:16384'])

    assert cli.run_screenshot_cli(args, 'C:\\program') == 1
    capture.assert_called_once_with('adb', '127.0.0.1:16384')
    out = capsys.readouterr().out
    assert '失败' in out
    assert 'backend_failed' in out


def test_run_hides_unexpected_error_details(monkeypatch, capsys):
    """未预期异常返回 1，且不把底层细节写入终端。"""
    cli = cli_module()
    capture = patch_capture(
        monkeypatch, Mock(side_effect=RuntimeError('secret-token')))
    args = parse(['--source', 'adb:127.0.0.1:16384'])

    assert cli.run_screenshot_cli(args, 'C:\\program') == 1
    assert capture.call_count == 1
    assert 'secret-token' not in capsys.readouterr().out


@pytest.mark.parametrize('args', [
    pytest.param(Namespace(source='auto', target='x'), id='unknown-source'),
    pytest.param(Namespace(source='adb', target=''), id='empty-target'),
    pytest.param(Namespace(source='window', target=None), id='none-target'),
    pytest.param(Namespace(source=None, target='x'), id='none-source'),
])
def test_run_returns_two_for_invalid_arguments(monkeypatch, args):
    """入参不合法时 run_screenshot_cli 返回 2 且不调用截图。"""
    cli = cli_module()
    capture = patch_capture(monkeypatch, Mock())

    assert cli.run_screenshot_cli(args, 'C:\\program') == 2
    capture.assert_not_called()


def _capture_entry_environment(monkeypatch, tmp_path):
    """统一替换入口副作用，返回入口模块与副作用监视集合。"""
    entry = entry_module()
    spies = {
        'setup_default_logging': Mock(),
        'setup_logging': Mock(),
        'monitor_processes': Mock(),
        'spinner_phase': MagicMock(),
    }
    for name, spy in spies.items():
        monkeypatch.setattr(entry, name, spy)
    monkeypatch.setattr(entry, 'print_info', Mock())
    monkeypatch.setattr(
        entry, 'get_program_directory', Mock(return_value=str(tmp_path)))
    return entry, spies


def _patch_entry_config(monkeypatch, entry):
    """替换入口配置读取，供断言截图路径不触碰配置。"""
    spy = Mock(return_value={})
    monkeypatch.setattr(entry, 'load_config', spy)
    return spy


def test_entry_dispatches_screenshot_command(monkeypatch, tmp_path):
    """首个命令参数为 screenshot 时进入截图 CLI，不进入常规流程。"""
    entry, spies = _capture_entry_environment(monkeypatch, tmp_path)
    config_spy = _patch_entry_config(monkeypatch, entry)
    parse_spy = Mock(return_value=Namespace(source='adb', target='serial'))
    run_spy = Mock(return_value=0)
    monkeypatch.setattr(entry, 'parse_screenshot_args', parse_spy)
    monkeypatch.setattr(entry, 'run_screenshot_cli', run_spy)
    monkeypatch.setattr(
        sys, 'argv', ['2RPM.py', 'screenshot', '--source', 'adb:serial'])

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert exit_info.value.code == 0
    parse_spy.assert_called_once_with(['--source', 'adb:serial'])
    run_spy.assert_called_once()
    assert run_spy.call_args.args[0] is parse_spy.return_value
    assert run_spy.call_args.args[1] == str(tmp_path)
    config_spy.assert_not_called()
    for spy in spies.values():
        spy.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_entry_propagates_screenshot_exit_code(monkeypatch, tmp_path):
    """截图失败时入口返回截图 CLI 的退出码。"""
    entry, _ = _capture_entry_environment(monkeypatch, tmp_path)
    _patch_entry_config(monkeypatch, entry)
    monkeypatch.setattr(
        entry, 'parse_screenshot_args',
        Mock(return_value=Namespace(source='adb', target='serial')))
    monkeypatch.setattr(entry, 'run_screenshot_cli', Mock(return_value=1))
    monkeypatch.setattr(
        sys, 'argv', ['2RPM.py', 'screenshot', '--source', 'adb:serial'])

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert exit_info.value.code == 1


def test_entry_screenshot_path_never_reads_config(monkeypatch, tmp_path):
    """入口分流使用真实解析与真实分派，不读取配置，仅写出截图产物。"""
    entry, spies = _capture_entry_environment(monkeypatch, tmp_path)
    config_spy = _patch_entry_config(monkeypatch, entry)
    result = CaptureResult(_png_bytes((8, 6)), 'window', 'MuMu模拟器 1', 8, 6)
    capture = patch_capture(monkeypatch, Mock(return_value=result))
    monkeypatch.setattr(
        sys, 'argv', ['2RPM.py', 'screenshot', '--source', 'MuMu模拟器 1'])

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert exit_info.value.code == 0
    capture.assert_called_once_with('window', 'MuMu模拟器 1')
    config_spy.assert_not_called()
    for spy in spies.values():
        spy.assert_not_called()
    assert [item.name for item in tmp_path.iterdir()] == ['screenshot']
    saved = list((tmp_path / 'screenshot').glob('*.png'))
    assert len(saved) == 1
    with Image.open(saved[0]) as image:
        assert image.format == 'PNG'


def test_entry_keeps_legacy_parsing_without_screenshot_command(
        monkeypatch, tmp_path):
    """首个命令参数不是 screenshot 时完全沿用既有配置文件解析。"""
    entry, _ = _capture_entry_environment(monkeypatch, tmp_path)
    config_spy = _patch_entry_config(monkeypatch, entry)
    run_spy = Mock()
    monkeypatch.setattr(entry, 'run_screenshot_cli', run_spy)
    monkeypatch.setattr(sys, 'argv', ['2RPM.py', 'myconfig.yaml'])

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert exit_info.value.code == 0
    run_spy.assert_not_called()
    config_spy.assert_called_once()
    assert config_spy.call_args.args[0] == os.path.join(
        str(tmp_path), 'myconfig.yaml')


@pytest.mark.parametrize('argv', [
    pytest.param(['2RPM.py', '-c', 'screenshot'], id='short-option'),
    pytest.param(['2RPM.py', '--config', 'screenshot'], id='long-option'),
    pytest.param(['2RPM.py', 'screenshot.yaml'], id='positional-path'),
    pytest.param(['2RPM.py', 'screenshot'], id='bare-keyword'),
])
def test_named_screenshot_config_stays_reachable(monkeypatch, tmp_path, argv):
    """名为 screenshot 的配置（含裸用关键字）仍可经常规解析访问。"""
    entry, _ = _capture_entry_environment(monkeypatch, tmp_path)
    config_spy = _patch_entry_config(monkeypatch, entry)
    run_spy = Mock()
    monkeypatch.setattr(entry, 'run_screenshot_cli', run_spy)
    monkeypatch.setattr(sys, 'argv', argv)

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert exit_info.value.code == 0
    run_spy.assert_not_called()
    assert config_spy.call_args.args[0] == os.path.join(
        str(tmp_path), 'screenshot.yaml')
