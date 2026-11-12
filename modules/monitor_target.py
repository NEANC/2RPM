#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""统一监视目标的纯函数解析。

把配置中的 monitor.target 按 Windows 语法分类为进程名、文件路径或计划任务路径，
不访问文件系统、不查询进程、不读取配置。
"""

import ntpath
from dataclasses import dataclass

# 支持的监视模式
WATCH_MODE = 'watch'
LAUNCH_MODE = 'launch'
VALID_MODES = frozenset({WATCH_MODE, LAUNCH_MODE})

# watch 模式允许的文件扩展名
WATCH_FILE_EXTENSIONS = frozenset({'.exe'})

# launch 模式允许的文件扩展名
LAUNCH_FILE_EXTENSIONS = frozenset({'.exe', '.bat', '.cmd', '.ps1', '.py'})

# 设备路径前缀（不在支持范围）
DEVICE_PATH_PREFIXES = ('\\\\?\\', '\\\\.\\')

# 显式相对路径前缀
RELATIVE_PATH_PREFIXES = ('.\\', './', '..\\', '../')


@dataclass(frozen=True)
class MonitorTarget:
    """监视目标解析结果

    Attributes:
        kind (str): 目标类型，仅 'process' | 'file' | 'task'
        value (str): 裸进程名 / 规范化绝对文件路径 / 完整任务路径
        cwd (str | None): 仅 launch 文件目标有值
    """

    kind: str
    value: str
    cwd: str | None


def resolve_monitor_target(mode, target, config_dir, cwd=None):
    """解析监视目标

    按 Windows 语法对 target 分类，不依赖文件是否存在，也不受当前工作目录影响。

    Args:
        mode (str): 监视模式，仅接受 'watch' 或 'launch'
        target (str): 配置中的 monitor.target 原始值
        config_dir (str): 配置文件所在目录，必须是绝对目录
        cwd (str | None): launch 文件目标的显式工作目录，为空时使用目标文件所在目录

    Returns:
        MonitorTarget: 目标类型、规范化取值与工作目录

    Raises:
        ValueError: mode、target、config_dir 或 cwd 不符合规则
    """
    validated_mode = _validate_mode(mode)
    validated_config_dir = _validate_config_dir(config_dir)
    _validate_target_type(target)

    if _is_device_path(target):
        raise ValueError(f"不支持设备路径: {target!r}")

    kind, value = _classify_target(target, validated_config_dir, validated_mode)
    resolved_cwd = None
    if kind == 'file' and validated_mode == LAUNCH_MODE:
        resolved_cwd = _resolve_cwd(cwd, validated_config_dir, value)

    return MonitorTarget(kind=kind, value=value, cwd=resolved_cwd)


def _validate_mode(mode):
    """校验监视模式并返回原值

    Args:
        mode: 待校验的模式值

    Returns:
        str: 合法的模式字符串

    Raises:
        ValueError: 模式不是 'watch' 或 'launch'
    """
    if not isinstance(mode, str) or mode not in VALID_MODES:
        raise ValueError(f"不支持的监视模式: {mode!r}")
    return mode


def _validate_config_dir(config_dir):
    """校验配置目录并做路径规范化

    只检查是否为绝对目录，不检查目录是否存在。

    Args:
        config_dir: 待校验的配置目录

    Returns:
        str: 规范化后的绝对目录

    Raises:
        ValueError: 配置目录不是绝对目录
    """
    if not isinstance(config_dir, str) or not config_dir.strip():
        raise ValueError(f"配置目录必须是非空字符串: {config_dir!r}")

    if _is_device_path(config_dir):
        raise ValueError(f"不支持设备路径作为配置目录: {config_dir!r}")

    if not ntpath.isabs(config_dir):
        raise ValueError(f"配置目录必须是绝对路径: {config_dir!r}")

    drive, _ = ntpath.splitdrive(config_dir)
    if not drive:
        raise ValueError(f"配置目录缺少盘符或 UNC 前缀: {config_dir!r}")

    return ntpath.normpath(config_dir)


def _validate_target_type(target):
    """校验目标是非空字符串

    Args:
        target: 待校验的目标值

    Raises:
        ValueError: 目标不是非空字符串
    """
    if not isinstance(target, str) or not target.strip():
        raise ValueError(f"监视目标必须是非空字符串: {target!r}")


def _classify_target(target, config_dir, mode):
    """按 Windows 语法判定目标类型

    Args:
        target (str): 非空目标字符串
        config_dir (str): 规范化的绝对配置目录
        mode (str): 合法的监视模式

    Returns:
        tuple: (kind, value)

    Raises:
        ValueError: 目标语法不在支持范围
    """
    drive, rest = ntpath.splitdrive(target)

    # UNC 文件路径必须先于计划任务路径判断
    if _is_unc_drive(drive):
        return 'file', _validate_file_target(ntpath.normpath(target), mode)

    if drive:
        if rest.startswith(('\\', '/')):
            return 'file', _validate_file_target(ntpath.normpath(target), mode)
        raise ValueError(f"不支持盘符相对路径: {target!r}")

    if target.startswith('\\\\') or target.startswith('//'):
        raise ValueError(f"UNC 路径缺少共享名: {target!r}")

    if target.startswith('\\'):
        return 'task', _validate_task_path(target)

    if target.startswith(RELATIVE_PATH_PREFIXES):
        absolute = ntpath.normpath(ntpath.join(config_dir, target))
        return 'file', _validate_file_target(absolute, mode)

    if '\\' in target or '/' in target:
        raise ValueError(f"相对路径必须带显式相对前缀: {target!r}")

    if mode == LAUNCH_MODE:
        raise ValueError(f"launch 模式不接受裸名称: {target!r}")

    return 'process', target


def _validate_file_target(path, mode):
    """校验文件目标扩展名

    Args:
        path (str): 规范化后的绝对文件路径
        mode (str): 合法的监视模式

    Returns:
        str: 原样返回文件路径

    Raises:
        ValueError: 扩展名不被当前模式允许
    """
    extension = ntpath.splitext(path)[1].lower()
    allowed = WATCH_FILE_EXTENSIONS if mode == WATCH_MODE else LAUNCH_FILE_EXTENSIONS
    if extension not in allowed:
        raise ValueError(f"{mode} 模式不支持的文件类型: {path!r}")
    return path


def _validate_task_path(target):
    """校验完整计划任务路径

    Args:
        target (str): 以单个反斜杠开头的目标

    Returns:
        str: 规范化后的任务路径

    Raises:
        ValueError: 任务路径缺少任务名
    """
    if target.endswith(('\\', '/')):
        raise ValueError(f"任务路径缺少任务名: {target!r}")

    normalized = ntpath.normpath(target)
    segments = [segment for segment in normalized.split('\\') if segment]
    if not segments:
        raise ValueError(f"任务路径缺少任务名: {target!r}")
    return normalized


def _resolve_cwd(cwd, config_dir, target_path):
    """解析 launch 文件目标的工作目录

    Args:
        cwd (str | None): 显式工作目录，为空时使用目标文件所在目录
        config_dir (str): 规范化的绝对配置目录
        target_path (str): 已解析的绝对目标文件路径

    Returns:
        str: 规范化的绝对目录

    Raises:
        ValueError: cwd 语法不被支持
    """
    default_cwd = ntpath.dirname(target_path)

    if cwd is None:
        return default_cwd

    if not isinstance(cwd, str):
        raise ValueError(f"cwd 必须是字符串: {cwd!r}")

    stripped = cwd.strip()
    if not stripped:
        return default_cwd

    if _is_device_path(stripped):
        raise ValueError(f"不支持设备路径作为 cwd: {cwd!r}")

    drive, rest = ntpath.splitdrive(stripped)
    if _is_unc_drive(drive):
        return ntpath.normpath(stripped)

    if drive:
        if rest.startswith(('\\', '/')):
            return ntpath.normpath(stripped)
        raise ValueError(f"cwd 不支持盘符相对路径: {cwd!r}")

    if stripped.startswith('\\\\') or stripped.startswith('//'):
        raise ValueError(f"cwd 的 UNC 路径缺少共享名: {cwd!r}")

    if stripped.startswith(('\\', '/')):
        raise ValueError(f"cwd 不支持根相对路径: {cwd!r}")

    if stripped.startswith(RELATIVE_PATH_PREFIXES):
        return ntpath.normpath(ntpath.join(config_dir, stripped))

    raise ValueError(f"cwd 相对路径必须带显式相对前缀: {cwd!r}")


def _is_unc_drive(drive):
    """判断 splitdrive 结果是否为 UNC 盘符

    Args:
        drive (str): ntpath.splitdrive 返回的盘符部分

    Returns:
        bool: 是否为 UNC 形式
    """
    return drive.startswith('\\\\') or drive.startswith('//')


def _is_device_path(path):
    r"""判断是否为 Windows 设备路径

    Args:
        path (str): 待判断的路径

    Returns:
        bool: 是否为 \\?\ 或 \\.\ 开头的设备路径
    """
    normalized = path.replace('/', '\\')
    return normalized.startswith(DEVICE_PATH_PREFIXES)
