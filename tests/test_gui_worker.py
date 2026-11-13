#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI worker 的入口分流及 Windows 真实管道验证。"""

import importlib.util
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from modules.gui.protocol import TestRequest as GuiRequest
from modules.gui.protocol import decode_result, encode_request


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('missing_streams', [False, True])
def test_real_worker_pipe_isolated(missing_streams, tmp_path):
    """真实三管道在缺流及超过缓冲区噪声后仍仅回一条结果。"""
    request = GuiRequest('req-1', 2, 3, 'notify', {}, str(tmp_path),
                         str(tmp_path), None, None, None, 'on_end')
    command = [sys.executable, str(ROOT / 'tests/fixtures/gui_worker_probe.py')]
    if missing_streams:
        command.append('--missing-streams')
    process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        stdout, stderr = process.communicate(encode_request(request), timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    assert process.returncode == 0
    assert stderr == b''
    assert b'secret-value' not in stdout + stderr
    assert stdout.count(b'protocol_version') == 1
    assert decode_result(stdout, request=request).reason == '中文完成'


def test_entry_routes_worker_before_business_and_tk(monkeypatch):
    """内部参数必须先于业务与 Tk 导入完成分流。"""
    spec = importlib.util.spec_from_file_location('gui_entry', ROOT / '2RPM_gui.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    worker = Mock(return_value=0)
    monkeypatch.setattr('modules.gui.worker.main', worker)
    assert module.main(['--internal-gui-worker']) == 0
    worker.assert_called_once_with()
    assert 'tkinter' not in module.__dict__


def test_real_worker_main_returns_one_result(tmp_path):
    """源码 worker 模块使用真实动作的安全失败路径写一条结果。"""
    request = GuiRequest('req-1', 2, 3, 'upload', {}, str(tmp_path),
                         str(tmp_path), None, 0, str(tmp_path / 'missing.jpg'), None)
    process = subprocess.Popen([sys.executable, '-m', 'modules.gui.worker'],
                               cwd=ROOT, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        stdout, stderr = process.communicate(encode_request(request), timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    assert process.returncode == 0
    assert stderr == b''
    assert stdout.count(b'protocol_version') == 1
    assert decode_result(stdout, request=request).status == 'failure'


@pytest.mark.parametrize('closed_fds', [(0,), (1,), (2,), (0, 1, 2)])
def test_worker_main_with_closed_standard_fds(closed_fds, tmp_path):
    """真实三管道在标准 fd 关闭后仍返回结果并先隔离业务导入。"""
    request = GuiRequest('closed-fds', 2, 3, 'upload', {}, str(tmp_path),
                         str(tmp_path), None, 0, str(tmp_path / 'missing.jpg'), None)
    marker = tmp_path / 'import-state.txt'
    command = [sys.executable, str(ROOT / 'tests/fixtures/gui_worker_closed_fds.py'),
               str(marker), *(str(fd) for fd in closed_fds)]
    process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        stdout, stderr = process.communicate(encode_request(request), timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    assert process.returncode == 0, (closed_fds, stdout, stderr,
                                     marker.read_text(encoding='utf-8') if marker.exists() else None)
    assert stderr == b''
    assert stdout.count(b'protocol_version') == 1
    assert decode_result(stdout, request=request).status == 'failure'
    assert marker.read_text(encoding='utf-8') == 'isolated'


def test_invalid_worker_request_exits_without_result():
    """非法快照不能转换为成功结果。"""
    process = subprocess.Popen([sys.executable, '-m', 'modules.gui.worker'],
                               cwd=ROOT, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        stdout, stderr = process.communicate(b'{}{}', timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    assert process.returncode != 0
    assert stdout == stderr == b''
