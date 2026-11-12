#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""真实解释器的目标文件字面路径回显测试。"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.monitor_launch import build_launch_command
from modules.monitor_launch import launch_file
from modules.monitor_target import MonitorTarget


@pytest.mark.skipif(os.name != 'nt', reason='需要 Windows 解释器')
@pytest.mark.parametrize('extension', ['bat', 'cmd'])
@pytest.mark.parametrize('args', [None, 'hello world'])
def test_batch_target_is_literal(tmp_path, monkeypatch, extension, args):
    """实际 cmd 执行字面文件，参数可按 cmd 自身规则解释。"""
    monkeypatch.setenv('MONITOR_LITERAL_NAME', 'EXPANDED')
    directory = tmp_path / "space & (group) ! it's" / '%MONITOR_LITERAL_NAME%'
    directory.mkdir(parents=True)
    literal = directory / f'run.{extension}'
    literal.write_bytes(b'@echo off\r\necho LITERAL %*\r\n')
    expanded = Path(str(literal).replace('%MONITOR_LITERAL_NAME%', 'EXPANDED'))
    expanded.parent.mkdir(parents=True)
    expanded.write_bytes(b'@echo off\r\necho EXPANDED %*\r\n')
    command = build_launch_command(
        MonitorTarget(kind='file', value=str(literal), cwd=str(tmp_path)), args)
    assert command.command_line.startswith(
        f'"{command.executable}" /d /v:off /c ')
    env = os.environ.copy()
    env.update(command.env or {})
    result = subprocess.run(
        command.command_line,
        cwd=command.cwd, env=env, shell=False, executable=command.executable,
        capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == b'LITERAL' + (b' hello world' if args else b'')


@pytest.mark.skipif(os.name != 'nt', reason='需要 Windows 解释器')
@pytest.mark.parametrize('fragment', ['$name', '$(1+2)', '`mark', "it's"])
@pytest.mark.parametrize('args', [None, "'hello world'"])
def test_ps1_target_is_literal(tmp_path, fragment, args):
    """PowerShell 以原文件名执行含表达式符号的脚本。"""
    interpreter = shutil.which('pwsh.exe') or shutil.which('powershell.exe')
    if interpreter is None:
        pytest.skip('未安装 PowerShell')
    path = tmp_path / f'file {fragment}.ps1'
    path.write_bytes(b'[Console]::WriteLine("LITERAL " + ($args -join " "))\r\n')
    target = MonitorTarget(kind='file', value=str(path), cwd=str(tmp_path))
    with patch('modules.monitor_launch._resolve_powershell', return_value=interpreter):
        command = build_launch_command(target, args)
    result = subprocess.run(command.command_line, cwd=command.cwd, shell=False,
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == b'LITERAL' + (b' hello world' if args else b'')


def test_launch_file_passes_literal_batch_environment(monkeypatch):
    """启动入口必须把命令构造所需的目标变量传给子进程。"""
    target = MonitorTarget(kind='file', value=r'C:\%NAME%\run.cmd', cwd='C:\\')
    seen = {}

    def fake_popen(command_line, **kwargs):
        """捕获启动命令及环境，避免运行外部进程。"""
        seen.update(kwargs)
        return type('Process', (), {'pid': 123})()

    monkeypatch.setattr('modules.monitor_launch.subprocess.Popen', fake_popen)
    monkeypatch.setattr('modules.monitor_launch._resolve_identity',
                        lambda pid, exe: (123.0, 'cmd.exe'))
    launch_file(target)
    assert target.value in seen['env'].values()


@pytest.mark.skipif(os.name != 'nt', reason='需要 Windows 解释器')
@pytest.mark.parametrize('extension', ['bat', 'cmd'])
def test_launch_file_preserves_paired_bang_in_target(tmp_path, monkeypatch, extension):
    """经真实启动入口执行成对感叹号目录中的批处理文件。"""
    monkeypatch.setenv('NAME', 'EXPANDED')
    real_popen = subprocess.Popen

    def delayed_popen(command_line, **kwargs):
        """确认生产命令显式关闭延迟扩展并运行真实子进程。"""
        assert command_line.startswith(f'"{kwargs["executable"]}" /d /v:off /c ')
        return real_popen(command_line, **kwargs)

    monkeypatch.setattr('modules.monitor_launch.subprocess.Popen', delayed_popen)
    literal_dir = tmp_path / '!NAME!'
    expanded_dir = tmp_path / 'EXPANDED'
    literal_dir.mkdir()
    expanded_dir.mkdir()
    marker = tmp_path / 'result.txt'
    (literal_dir / f'run.{extension}').write_bytes(
        b'@echo off\r\necho LITERAL> "' + os.fsencode(marker) + b'"\r\n'
        b'ping -n 3 127.0.0.1 >nul\r\n')
    (expanded_dir / f'run.{extension}').write_bytes(
        b'@echo off\r\necho EXPANDED> "' + os.fsencode(marker) + b'"\r\n'
        b'ping -n 3 127.0.0.1 >nul\r\n')
    target = MonitorTarget(kind='file', value=str(literal_dir / f'run.{extension}'),
                           cwd=str(tmp_path))
    launch_file(target)
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.read_text().strip() == 'LITERAL'
