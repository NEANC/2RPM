#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import colorama

from modules.utils import get_program_directory

LOGGER = logging.getLogger(__name__)

# 日志格式常量
_LOG_CONSOLE_FORMAT = "%(levelname)s | %(asctime)s.%(msecs)03d | %(message)s"
_LOG_CONSOLE_DATEFMT = "%H:%M:%S"
_LOG_FILE_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s"
_LOG_FILE_DATEFMT = "%Y-%m-%d %H:%M:%S"

# 文件日志滚动大小（10MB）
_LOG_MAX_BYTES = 10 * 1024 * 1024

# 启动阶段建立的文件处理器，供 setup_logging 复用
_FILE_HANDLER = None


class ColoredConsoleFormatter(logging.Formatter):
    """带颜色的控制台日志格式化器"""

    LEVEL_COLORS = {
        'DEBUG': colorama.Fore.CYAN,
        'INFO': colorama.Fore.WHITE,
        'WARNING': colorama.Fore.YELLOW,
        'ERROR': colorama.Fore.RED,
        'CRITICAL': colorama.Back.RED + colorama.Fore.BLACK + colorama.Style.BRIGHT,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        colorama.init(autoreset=True)

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelname, colorama.Fore.WHITE)
        result = self._format_without_traceback(record)
        return f"{color}{result}{colorama.Style.RESET_ALL}"

    def _format_without_traceback(self, record: logging.LogRecord) -> str:
        """格式化日志且不输出异常堆栈，仅保留异常摘要。

        控制台只展示异常类型与消息（由调用方写入 message），完整 traceback
        交由文件处理器输出。临时屏蔽 record 的异常字段进行格式化，结束后还原，
        避免影响其他处理器对同一条记录的格式化。

        Args:
            record (logging.LogRecord): 待格式化的日志记录。

        Returns:
            str: 不含 traceback 的日志文本。
        """
        saved_exc_info = record.exc_info
        saved_exc_text = record.exc_text
        saved_stack_info = record.stack_info
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        try:
            return super().format(record)
        finally:
            record.exc_info = saved_exc_info
            record.exc_text = saved_exc_text
            record.stack_info = saved_stack_info


def _cleanup_old_logs(log_dir: str, max_files: int, max_days: int) -> None:
    """清理过期和超量的日志文件。

    先按天数清理（超过 max_days 天的文件删除），再按数量清理
    （保留最新的 max_files 个文件）。

    Args:
        log_dir: 日志文件夹路径。
        max_files: 保留的最大日志文件数量。
        max_days: 日志文件最大保留天数。
    """
    if not os.path.exists(log_dir):
        return

    files = sorted(
        Path(log_dir).glob("*.log"),
        key=os.path.getmtime,
    )
    now = time.time()

    # 按天数清理
    for file_path in list(files):
        if now - file_path.stat().st_mtime > max_days * 86400:
            try:
                file_path.unlink()
                files.remove(file_path)
            except OSError:
                pass
            except Exception:
                LOGGER.debug(f"清理过期日志文件失败: {file_path}", exc_info=True)

    # 按数量清理
    if len(files) > max_files:
        for file_path in files[:len(files) - max_files]:
            try:
                file_path.unlink()
            except OSError:
                pass
            except Exception:
                LOGGER.debug(f"清理超量日志文件失败: {file_path}", exc_info=True)


def _make_log_filename(log_filename: str) -> str:
    """生成带毫秒时间戳的日志文件名，避免同秒碰撞。

    Args:
        log_filename: 日志文件名前缀（不含扩展名）。

    Returns:
        str: 完整的日志文件名。
    """
    now = time.time()
    ms = int((now - int(now)) * 1000)
    timestamp = time.strftime('%Y-%m-%d_%H-%M-%S', time.localtime(now))
    return f"{log_filename}_{timestamp}.{ms:03d}.log"


def _resolve_log_dir(log_directory: str) -> str:
    """将日志目录配置解析为绝对路径。

    相对路径基于程序目录拼接，绝对路径原样保留。

    Args:
        log_directory: 已清洗的目录配置。

    Returns:
        str: 日志目录的绝对路径。
    """
    if os.path.isabs(log_directory):
        return log_directory
    return os.path.join(get_program_directory(), log_directory)


def _create_file_handler(log_dir: str, prefix: str):
    """创建滚动文件日志处理器，IO 失败时返回 None。

    在指定目录下生成带毫秒时间戳的日志文件，文件级别恒为 DEBUG。
    任何文件 IO 异常都在内部降级处理，不向上层抛出。

    Args:
        log_dir: 日志目录的绝对路径。
        prefix: 日志文件名前缀。

    Returns:
        RotatingFileHandler | None: 成功返回处理器，IO 失败返回 None。
    """
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, _make_log_filename(prefix))
        handler = RotatingFileHandler(
            log_file,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=0,
            encoding='utf-8',
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter(_LOG_FILE_FORMAT, datefmt=_LOG_FILE_DATEFMT))
        return handler
    except OSError as e:
        LOGGER.warning(f"无法创建日志文件，仅启用控制台输出: {e}")
        return None


def setup_default_logging() -> None:
    """设置程序启动阶段的默认日志配置。

    在配置文件加载前调用，建立控制台彩色输出与文件日志通道。文件
    直接写入默认目录下的 logs/2RPM_时间戳.毫秒.log，处理器保存到
    模块级变量供 setup_logging() 复用。文件 IO 失败时降级为仅控制台输出。

    双轨制 UI：控制台 handler 自启动起即静音（提级到 CRITICAL+1），
    控台仅由 banner、spinner 与 notify_fail 的 ❌ 行直接呈现。如此
    spinner_phase 进入时捕获的 saved_level 本就是静音态，其 finally
    还原后控台仍保持静音，不会回落到可泄漏的级别。
    """
    global _FILE_HANDLER

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # 守卫：已初始化过则不重复建立
    if root_logger.handlers:
        return

    # 控制台 handler：双轨制下持久静音（提级到 CRITICAL+1），
    # 保留 ColoredConsoleFormatter 仅为兼容潜在的级别临时调整场景
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.CRITICAL + 1)
    console_handler.setFormatter(
        ColoredConsoleFormatter(_LOG_CONSOLE_FORMAT, datefmt=_LOG_CONSOLE_DATEFMT))
    root_logger.addHandler(console_handler)

    # 文件日志：启动阶段早于配置加载，前缀固定为 '2RPM'
    default_dir = _resolve_log_dir('logs')
    _FILE_HANDLER = _create_file_handler(default_dir, '2RPM')
    if _FILE_HANDLER is not None:
        root_logger.addHandler(_FILE_HANDLER)


def _set_console_level(root_logger: logging.Logger, level: int) -> None:
    """调整控制台处理器级别，不存在时新建一个。

    Args:
        root_logger: 根日志记录器。
        level: 控制台输出级别。
    """
    # RotatingFileHandler 是 FileHandler 子类，需排除以定位控制台处理器
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and \
                not isinstance(handler, logging.FileHandler):
            handler.setLevel(level)
            return

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(
        ColoredConsoleFormatter(_LOG_CONSOLE_FORMAT, datefmt=_LOG_CONSOLE_DATEFMT))
    root_logger.addHandler(console_handler)


def _reopen_file_handler(path: str, backup_count: int):
    """在指定路径重新打开滚动文件处理器。

    Args:
        path: 日志文件完整路径。
        backup_count: 滚动备份数量。

    Returns:
        RotatingFileHandler: 重新打开的文件处理器。
    """
    handler = RotatingFileHandler(
        path, maxBytes=_LOG_MAX_BYTES, backupCount=backup_count, encoding='utf-8')
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(_LOG_FILE_FORMAT, datefmt=_LOG_FILE_DATEFMT))
    return handler


def _apply_log_path(handler, target_dir: str, target_prefix: str):
    """将启动日志文件移动到配置目录并重命名为目标前缀。

    根据旧文件名拼接新的目录和前缀。目标路径与当前路径相同时
    不做处理。移动失败时沿用原文件，确保日志通道始终可用。

    Args:
        handler: 启动阶段创建的文件处理器。
        target_dir: 配置解析出的目标目录绝对路径。
        target_prefix: 目标文件名前缀（如 'myapp'）。

    Returns:
        RotatingFileHandler: 应用配置后的文件处理器（可能为新实例）。
    """
    old_path = handler.baseFilename
    old_name = os.path.basename(old_path)

    # 将旧前缀 '2RPM' 替换为目标前缀，仅替换首次出现
    new_name = old_name.replace('2RPM', target_prefix, 1)
    new_path = os.path.join(target_dir, new_name)

    # 守卫：路径未变化
    if os.path.normcase(os.path.normpath(old_path)) == \
            os.path.normcase(os.path.normpath(new_path)):
        return handler

    backup_count = handler.backupCount

    handler.close()
    try:
        os.makedirs(target_dir, exist_ok=True)
        os.replace(old_path, new_path)
        return _reopen_file_handler(new_path, backup_count)
    except OSError:
        LOGGER.warning("应用日志配置失败，沿用默认启动文件")
        return _reopen_file_handler(old_path, backup_count)


def setup_logging(config: dict, config_file: str = 'config.yaml') -> None:
    """根据配置接管日志系统。

    复用 setup_default_logging() 建立的处理器：将日志文件移动到配置
    目录并按配置文件名重命名前缀、设置滚动备份数量并清理过期日志。

    双轨制 UI：文件日志始终强制开启并输出 DEBUG 全量级别；控制台
    日志处理器被静音（提级到 CRITICAL+1），控台仅由 banner、spinner
    与 notify_fail 的 ❌ 行直接呈现，不经日志处理器穿透。
    前缀推导规则：config.yaml → '2RPM'，其它 → 配置文件基底名。

    Args:
        config: 配置字典。
        config_file: 配置文件路径，用于推导日志文件名前缀。
    """
    global _FILE_HANDLER

    log_config = config.get('log', {})
    max_files = log_config.get('max_log_files', 15)
    max_days = log_config.get('retention_days', 3)
    raw_dir = log_config.get('log_directory', 'logs')
    if not isinstance(raw_dir, str) or not raw_dir.strip():
        raw_dir = 'logs'
    log_dir = _resolve_log_dir(raw_dir.strip())

    # 从配置文件名推导日志前缀：config 或空一律用 '2RPM'，其它取配置文件基底名
    config_basename = os.path.splitext(os.path.basename(config_file))[0].strip()
    target_prefix = config_basename if config_basename and \
        config_basename.lower() != 'config' else '2RPM'

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # 根 logger 始终 DEBUG，由各 handler 独立控制级别

    # 双轨制 UI：控制台日志处理器静音（提级到 CRITICAL+1），
    # 控台仅由 banner、spinner 与 notify_fail 的 ❌ 行直接呈现
    _set_console_level(root_logger, logging.CRITICAL + 1)

    # 强制启用文件日志：启动阶段文件创建失败时尝试补建，否则复用并迁移到配置目录
    if _FILE_HANDLER is None:
        _FILE_HANDLER = _create_file_handler(log_dir, target_prefix)
        if _FILE_HANDLER is not None:
            root_logger.addHandler(_FILE_HANDLER)
    else:
        # 先摘除再调用 _apply_log_path，避免内部 close 时 handler 仍挂在 logger 上
        root_logger.removeHandler(_FILE_HANDLER)
        _FILE_HANDLER = _apply_log_path(_FILE_HANDLER, log_dir, target_prefix)
        root_logger.addHandler(_FILE_HANDLER)

    # 统一设置滚动备份数量与清理（使用文件真实所在目录）
    if _FILE_HANDLER is not None:
        _FILE_HANDLER.backupCount = max_files
        actual_log_dir = os.path.dirname(_FILE_HANDLER.baseFilename)
        _cleanup_old_logs(actual_log_dir, max_files, max_days)
