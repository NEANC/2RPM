#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""真实入口的无预挂日志处理器离线回归。"""

from importlib import import_module
import logging
import sys
from unittest.mock import Mock

import pytest

from modules.screenshot.models import CaptureError
from modules.screenshot.models import CaptureResult
from modules.screenshot import winfs


@pytest.fixture
def entry_environment(monkeypatch, tmp_path):
    """隔离 root，不替换真实入口、参数解析、日志或上传编排。"""
    entry = import_module('2RPM')
    root = logging.getLogger()
    monkeypatch.setattr(root, 'handlers', [])
    monkeypatch.setattr(root, 'level', logging.WARNING)
    monkeypatch.setattr(entry, 'get_program_directory', lambda: str(tmp_path))
    monkeypatch.setattr(entry, 'load_config', Mock(side_effect=AssertionError))
    cli = import_module('modules.screenshot.cli')
    monkeypatch.setattr(cli, 'make_status_consumer', lambda: None)
    yield entry, cli, root
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()


@pytest.mark.parametrize('failed', [False, True])
def test_main_persists_without_status_callback(
        entry_environment, monkeypatch, tmp_path, capsys, failed):
    """关闭状态回调后仍落盘，且 URL stdout 和退出码不变。"""
    entry, cli, root = entry_environment
    url = 'https://example.invalid/private/result?key=secret'
    registry = import_module('modules.image_host.registry')

    def upload(*args):
        """在真实安全作用域中注入含凭证和链接的日志。"""
        logging.getLogger(__name__).warning('private-token %s', url)
        return url

    monkeypatch.setitem(registry.UPLOADERS, 'catbox', upload)
    capture = Mock(
        side_effect=CaptureError('window_not_found', 'private-error')
        if failed else None,
        return_value=CaptureResult(b'image', 'window', 'test', 2, 3))
    monkeypatch.setattr(cli, 'capture', capture)
    monkeypatch.setattr(sys, 'argv', [
        '2RPM.py', 'screenshot', '--source', 'window:test',
        '--upload', '{provider: catbox, token: private-token}'])
    monkeypatch.setattr(root, 'handlers', [])
    assert root.handlers == []
    with pytest.raises(SystemExit) as caught:
        entry.main()
    assert caught.value.code == int(failed)
    output = capsys.readouterr()
    assert output.err == ''
    assert (url in output.out) is not failed
    files = list((tmp_path / 'logs').glob('2RPM_*.log'))
    assert len(files) == 1
    text = files[0].read_text(encoding='utf-8')
    assert 'batch' in text
    assert ('failed' if failed else 'completed') in text
    assert url not in text
    assert 'private-error' not in text
    assert 'private-token' not in text
    if not failed:
        assert '[已隐藏]' in text
        assert '[链接已隐藏]' in text
    assert root.handlers == []
    assert root.level == logging.WARNING
    entry.load_config.assert_not_called()


def test_main_invalid_args_do_not_create_logs(
        entry_environment, monkeypatch, tmp_path, capsys):
    """非法参数仍在任何文件副作用和截图前退出。"""
    entry, cli, _ = entry_environment
    capture = Mock(side_effect=AssertionError)
    monkeypatch.setattr(cli, 'capture', capture)
    monkeypatch.setattr(sys, 'argv', [
        '2RPM.py', 'screenshot', '--source', 'window:test', '--config', 'x'])
    with pytest.raises(SystemExit) as caught:
        entry.main()
    assert caught.value.code == 2
    assert list(tmp_path.iterdir()) == []
    capture.assert_not_called()
    assert capsys.readouterr().err == '截图参数无效\n'


@pytest.mark.parametrize('failure', ['blocked', 'collision', 'reparse'])
def test_main_log_failure_keeps_cli_result(
        entry_environment, monkeypatch, tmp_path, capsys, failure):
    """不安全或不可写的日志路径不覆盖文件、不阻断截图、不泄露异常。"""
    entry, cli, root = entry_environment
    logger = import_module('modules.logger')
    logs = tmp_path / 'logs'
    if failure == 'blocked':
        logs.write_text('keep', encoding='utf-8')
    elif failure == 'collision':
        logs.mkdir()
        (logs / 'fixed.log').write_text('keep', encoding='utf-8')
        monkeypatch.setattr(logger, '_make_log_filename', lambda prefix: 'fixed.log')
    else:
        monkeypatch.setattr(winfs, '_open_path', Mock(
            side_effect=winfs.UnsafeObjectError('private-path')))
    capture = Mock(side_effect=CaptureError('window_not_found', 'private-error'))
    monkeypatch.setattr(cli, 'capture', capture)
    monkeypatch.setattr(sys, 'argv', [
        '2RPM.py', 'screenshot', '--source', 'window:test', '--output'])
    monkeypatch.setattr(root, 'handlers', [])
    with pytest.raises(SystemExit) as caught:
        entry.main()
    assert caught.value.code == 1
    capture.assert_called_once()
    output = capsys.readouterr()
    assert output.err == '截图日志文件无法创建，继续执行截图\n'
    assert 'private-' not in output.out + output.err
    if failure == 'blocked':
        assert logs.read_text(encoding='utf-8') == 'keep'
    elif failure == 'collision':
        assert (logs / 'fixed.log').read_text(encoding='utf-8') == 'keep'
    assert root.handlers == []
