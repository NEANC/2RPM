#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""同目录 CLI 精确定位与独立控制台进程创建。"""

import subprocess
from dataclasses import dataclass
from pathlib import Path


class CliLocationError(Exception):
    """GUI 与 CLI 无法精确配对。"""


@dataclass(frozen=True)
class LaunchResult:
    """仅表示进程是否已创建，不表示 CLI 运行状态。"""

    created: bool
    message: str


def resolve_cli_command(*, executable: Path, frozen: bool, project_dir: Path,
                        build_tool: str, build_tag: str) -> tuple[str, ...]:
    """按源码解释器或冻结文件名及身份选择唯一 CLI 命令。"""
    executable = executable.resolve()
    if not frozen:
        if executable.name.lower() not in ('python.exe', 'pythonw.exe'):
            raise CliLocationError('未找到匹配的 CLI 程序')
        python = executable.with_name('python.exe') if executable.name.lower() == 'pythonw.exe' else executable
        entry = project_dir.resolve() / '2RPM.py'
        if build_tool != 'source' or not python.is_file() or not entry.is_file():
            raise CliLocationError('未找到匹配的 CLI 程序')
        return str(python), str(entry)

    if build_tool not in ('Nuitka', 'PyInstaller') or not build_tag:
        raise CliLocationError('GUI 构建身份无效')
    local_name = '2RPM-GUI.exe'
    release_name = f'2RPM-GUI-{build_tool}-{build_tag}.exe'
    if executable.name == local_name:
        cli_name = '2RPM.exe'
    elif executable.name == release_name:
        cli_name = f'2RPM-{build_tool}-{build_tag}.exe'
    else:
        raise CliLocationError('GUI 文件名与构建身份不符')
    cli = executable.parent / cli_name
    if not cli.is_file():
        raise CliLocationError('未找到匹配的 CLI 程序')
    return (str(cli),)


def launch_cli(command: tuple[str, ...], config_path: Path) -> LaunchResult:
    """只创建拥有独立控制台的 CLI，不跟踪其退出或接管标准流。"""
    try:
        subprocess.Popen([*command, '-c', str(config_path.resolve())],
                         shell=False, creationflags=subprocess.CREATE_NEW_CONSOLE,
                         close_fds=True, stdin=None, stdout=None, stderr=None)
    except OSError:
        return LaunchResult(False, '无法创建 CLI 进程')
    return LaunchResult(True, '已创建 CLI 进程')
