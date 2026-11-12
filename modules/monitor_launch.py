#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""文件启动命令构造与启动身份取得。

只负责把文件类监视目标转换为单字符串命令行并启动，不接入监视循环。
命令行构造严格遵循 G1 实测结论：单字符串交由 Windows 自行解析，
args 不做拆分、转义或补引号；日志只记录目标、解释器与 cwd。
"""

import ctypes
import ctypes.wintypes
import logging
import ntpath
import os
import shutil
import subprocess
import time
from dataclasses import dataclass

import psutil

from modules.monitor_target import MonitorTarget

LOGGER = logging.getLogger(__name__)
SYSTEM_DIRECTORY_BUFFER_SIZE = 32768

# 系统盘缺失时的兜底根目录
SYSTEM_ROOT_FALLBACK = r'C:\Windows'

# 系统自带 Windows PowerShell 相对系统盘的路径
SYSTEM_POWERSHELL_RELATIVE = (
    r'System32\WindowsPowerShell\v1.0\powershell.exe'
)

# 直接执行的扩展名
EXE_EXTENSIONS = frozenset({'.exe'})

# 需要经 cmd.exe 执行的批处理扩展名
BATCH_EXTENSIONS = frozenset({'.bat', '.cmd'})

# 需要经 PowerShell 执行的扩展名
POWERSHELL_EXTENSIONS = frozenset({'.ps1'})

# 需要经 Python 解释器执行的扩展名
PYTHON_EXTENSIONS = frozenset({'.py'})


@dataclass(frozen=True)
class LaunchCommand:
    """启动命令

    Attributes:
        executable (str): 实际启动的可执行文件
        command_line (str): 传给 Popen 的单个命令行字符串
        cwd (str): 启动时的工作目录
    """

    executable: str
    command_line: str
    cwd: str
    env: dict | None = None


def build_launch_command(target, args=None):
    """构造启动命令

    只做命令构造，不访问文件系统以外的资源，也不启动进程。

    Args:
        target (MonitorTarget): 文件类监视目标
        args (str | None): 用户原始参数串，空值视为无参数

    Returns:
        LaunchCommand: 可执行文件、命令行与工作目录

    Raises:
        ValueError: 目标类型不是文件
        RuntimeError: 缺少可用解释器
    """
    if target.kind != 'file':
        raise ValueError(f"仅支持文件目标: {target.kind!r}")

    extension = ntpath.splitext(target.value)[1].lower()
    normalized_args = _normalize_args(args)
    cwd = target.cwd if target.cwd is not None else ntpath.dirname(target.value)

    executable, command_line = _compose_command(
        target.value, extension, normalized_args)

    return LaunchCommand(
        executable=executable,
        command_line=command_line,
        cwd=cwd,
        env={**os.environ, '2RPM_LITERAL_TARGET': target.value}
        if extension in BATCH_EXTENSIONS and '%' in target.value else None,
    )


def launch_file(target, args=None):
    """启动文件并返回进程身份信息

    Args:
        target (MonitorTarget): 文件类监视目标
        args (str | None): 用户原始参数串，空值视为无参数

    Returns:
        dict: 含 pid/name/create_time/start_time/last_warning_time/timeout_count

    Raises:
        ValueError: 目标类型不是文件
        RuntimeError: 缺少解释器、启动失败、进程立即退出或权限不足
    """
    command = build_launch_command(target, args)

    LOGGER.info(
        f"准备启动文件: {target.value}，解释器: {command.executable}，"
        f"cwd: {command.cwd}")

    try:
        options = {'cwd': command.cwd, 'shell': False,
                   'executable': command.executable}
        if command.env is not None:
            options['env'] = command.env
        proc = subprocess.Popen(command.command_line, **options)
    except OSError as error:
        raise RuntimeError(
            f"启动文件失败: {target.value}: {error}") from error

    pid = proc.pid
    create_time, name = _resolve_identity(pid, command.executable)

    current_time = time.time()
    LOGGER.info(f"文件已启动: {target.value} (PID: {pid})")
    return {
        'pid': pid,
        'name': name,
        'create_time': create_time,
        'start_time': current_time,
        'last_warning_time': current_time,
        'timeout_count': 0,
    }


def _normalize_args(args):
    """判定是否携带有效参数

    Args:
        args (str | None): 用户原始参数串

    Returns:
        str | None: 非空白参数原样返回，否则返回 None
    """
    if not isinstance(args, str) or not args.strip():
        return None
    return args


def _compose_command(path, extension, args):
    """按扩展名生成可执行文件与命令行

    Args:
        path (str): 目标文件绝对路径
        extension (str): 小写扩展名
        args (str | None): 规范化后的参数串

    Returns:
        tuple: (executable, command_line)

    Raises:
        ValueError: 扩展名不在支持范围
        RuntimeError: 缺少可用解释器
    """
    if extension in EXE_EXTENSIONS:
        command_line = f'"{path}"'
        if args is not None:
            command_line = f'{command_line} {args}'
        return path, command_line

    if extension in BATCH_EXTENSIONS:
        interpreter = _resolve_cmd()
        batch_path = '%2RPM_LITERAL_TARGET%' if '%' in path else path
        if args is not None:
            command_line = f'"{interpreter}" /d /v:off /c ""{batch_path}" {args}"'
        else:
            command_line = f'"{interpreter}" /d /v:off /c ""{batch_path}""'
        return interpreter, command_line

    if extension in POWERSHELL_EXTENSIONS:
        interpreter = _resolve_powershell()
        quoted_path = path.replace("'", "''")
        command_body = f"& '{quoted_path}'"
        if args is not None:
            command_body = f'{command_body} {args}'
        command_line = subprocess.list2cmdline(
            [interpreter, '-NoProfile', '-Command', command_body])
        return interpreter, command_line

    if extension in PYTHON_EXTENSIONS:
        interpreter, needs_version = _resolve_python()
        prefix = f'"{interpreter}" -3' if needs_version else f'"{interpreter}"'
        command_line = f'{prefix} "{path}"'
        if args is not None:
            command_line = f'{command_line} {args}'
        return interpreter, command_line

    raise ValueError(f"不支持的文件类型: {path!r}")


def _resolve_cmd() -> str:
    """通过 Win32 系统目录 API 定位本机 cmd，查询失败时拒绝启动。"""
    try:
        function = ctypes.WinDLL('kernel32', use_last_error=True).GetSystemDirectoryW
        function.argtypes = [ctypes.c_wchar_p, ctypes.wintypes.UINT]
        function.restype = ctypes.wintypes.UINT
        buffer = ctypes.create_unicode_buffer(SYSTEM_DIRECTORY_BUFFER_SIZE)
        length = function(buffer, len(buffer))
    except OSError as error:
        raise RuntimeError('无法获取系统目录') from error
    if not 0 < length < len(buffer):
        raise RuntimeError('无法获取系统目录')
    return ntpath.join(buffer.value, 'cmd.exe')


def _resolve_powershell():
    """按 G1 顺序查找 PowerShell 解释器

    Returns:
        str: 解释器路径

    Raises:
        RuntimeError: 三种候选都不存在
    """
    for name in ('pwsh.exe', 'powershell.exe'):
        path = shutil.which(name)
        if path:
            return path

    system_root = os.environ.get('SystemRoot') or SYSTEM_ROOT_FALLBACK
    system_powershell = ntpath.join(system_root, SYSTEM_POWERSHELL_RELATIVE)
    if os.path.isfile(system_powershell):
        return system_powershell

    raise RuntimeError("未找到 PowerShell 解释器：pwsh.exe 与 powershell.exe 均不可用")


def _resolve_python():
    """按 G1 顺序查找 Python 解释器

    Returns:
        tuple: (解释器路径, 是否需要附加 -3)

    Raises:
        RuntimeError: 两种候选都不存在
    """
    path = shutil.which('python.exe')
    if path:
        return path, False

    path = shutil.which('py.exe')
    if path:
        return path, True

    raise RuntimeError("未找到 Python 解释器：python.exe 与 py.exe 均不可用")


def _resolve_identity(pid, executable):
    """通过 psutil 取得进程身份

    Args:
        pid (int): 进程号
        executable (str): 实际启动的可执行文件，用于名称回退

    Returns:
        tuple: (create_time, name)

    Raises:
        RuntimeError: 进程立即退出或权限不足
    """
    try:
        process = psutil.Process(pid)
        create_time = process.create_time()
    except psutil.NoSuchProcess as error:
        raise RuntimeError(f"程序启动后立即退出，PID: {pid}") from error
    except psutil.AccessDenied as error:
        raise RuntimeError(
            f"无法访问进程信息（权限不足），PID: {pid}") from error

    try:
        name = process.name()
    except psutil.AccessDenied:
        name = ntpath.basename(executable)
    except psutil.NoSuchProcess as error:
        raise RuntimeError(f"程序启动后立即退出，PID: {pid}") from error

    return create_time, name
