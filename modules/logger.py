#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import os
import time
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import colorama

from modules.utils import get_program_directory

LOGGER = logging.getLogger(__name__)

colorama.init(autoreset=True)

# 日志格式常量
_LOG_CONSOLE_FORMAT = "%(levelname)s | %(asctime)s.%(msecs)03d | %(message)s"
_LOG_CONSOLE_DATEFMT = "%H:%M:%S"
_LOG_FILE_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s"
_LOG_FILE_DATEFMT = "%Y-%m-%d %H:%M:%S"

# 文件日志滚动大小（10MB）
_LOG_MAX_BYTES = 10 * 1024 * 1024


class ColoredConsoleFormatter(logging.Formatter):
    """带颜色的控制台日志格式化器"""

    LEVEL_COLORS = {
        'DEBUG': colorama.Fore.CYAN,
        'INFO': colorama.Fore.WHITE,
        'WARNING': colorama.Fore.YELLOW,
        'ERROR': colorama.Fore.RED,
        'CRITICAL': colorama.Back.RED + colorama.Fore.BLACK + colorama.Style.BRIGHT,
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelname, colorama.Fore.WHITE)
        result = super().format(record)
        return f"{color}{result}{colorama.Style.RESET_ALL}"


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


def _merge_default_log(log_dir: str, log_filename: str,
                        default_log_file: str) -> Optional[str]:
    """将 default.log 内容合并到正式日志文件中。

    在 setup_logging 首次创建日志文件时调用，确保程序启动阶段的
    临时日志不丢失。

    Args:
        log_dir: 日志目录。
        log_filename: 正式日志文件名（不含扩展名）。
        default_log_file: 临时日志文件路径。

    Returns:
        str or None: 合并后的正式日志文件路径（None 表示无需合并）。
    """
    if not os.path.exists(default_log_file):
        return None

    try:
        with open(default_log_file, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError:
        LOGGER.debug("读取临时日志文件失败", exc_info=True)
        return None
    except Exception:
        LOGGER.debug("读取临时日志文件异常", exc_info=True)
        return None

    if not content.strip():
        return None

    log_file = os.path.join(log_dir, _make_log_filename(log_filename))

    with open(log_file, 'w', encoding='utf-8') as f:
        f.write(content)

    # 删除临时日志文件，最多重试 3 次
    for attempt in range(3):
        try:
            os.remove(default_log_file)
            break
        except OSError:
            if attempt < 2:
                time.sleep(0.5)
        except Exception:
            LOGGER.debug("删除临时日志文件异常", exc_info=True)
            break

    return log_file


def setup_default_logging() -> None:
    """设置程序启动阶段的默认日志配置。

    仅在配置文件加载前使用，提供控制台彩色输出和临时文件记录。
    配置文件加载后由 setup_logging() 接管。
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # 检查是否已有 StreamHandler（避免重复初始化）
    has_stream_handler = any(
        isinstance(h, logging.StreamHandler) for h in root_logger.handlers
    )

    if has_stream_handler and root_logger.handlers:
        return

    # 控制台彩色输出
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = ColoredConsoleFormatter(
        _LOG_CONSOLE_FORMAT, datefmt=_LOG_CONSOLE_DATEFMT)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # 临时文件记录（default.log），程序启动阶段的日志
    program_dir = get_program_directory()
    default_log_file = os.path.join(program_dir, 'default.log')

    # 处理上次未合并的 default.log
    if os.path.exists(default_log_file):
        try:
            os.remove(default_log_file)
        except OSError:
            pass
        except Exception:
            LOGGER.debug("删除残留临时日志文件失败", exc_info=True)

    # 文件记录失败时降级为仅控制台输出，不中断程序
    try:
        file_handler = logging.FileHandler(
            default_log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(_LOG_FILE_FORMAT)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
    except OSError as e:
        LOGGER.warning(f"无法创建临时日志文件，仅启用控制台输出: {e}")


def setup_logging(config: dict) -> None:
    """根据配置设置正式日志系统。

    清除 setup_default_logging() 建立的临时处理器，按配置文件
    重新建立控制台和文件日志通道。若启用文件日志，会将启动阶段的
    default.log 内容合并到正式日志文件中。

    文件日志始终输出 DEBUG 级别，不受配置影响。
    配置中的 log_level 仅控制控制台输出级别。

    Args:
        config: 配置字典。
    """
    log_config = config.get('log_settings', {})
    enable_log_file = log_config.get('enable_log_file', True)
    log_level_str = log_config.get('log_level', 'INFO')
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)
    log_dir_name = log_config.get('log_directory', 'logs')
    max_files = log_config.get('max_log_files', 15)
    max_days = log_config.get('log_retention_days', 3)
    log_filename = log_config.get('log_filename', '2RPM')

    program_dir = get_program_directory()
    log_dir = os.path.join(program_dir, log_dir_name)
    default_log_file = os.path.join(program_dir, 'default.log')

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # 根 logger 始终 DEBUG，由各 handler 独立控制级别

    # 清除 setup_default_logging 建立的处理器
    for handler in root_logger.handlers[:]:
        try:
            handler.close()
        except Exception:
            LOGGER.debug("关闭旧日志处理器失败", exc_info=True)
        root_logger.removeHandler(handler)

    # 控制台处理器（级别由配置文件控制）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_formatter = ColoredConsoleFormatter(
        _LOG_CONSOLE_FORMAT, datefmt=_LOG_CONSOLE_DATEFMT)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    if not enable_log_file:
        if os.path.exists(default_log_file):
            try:
                os.remove(default_log_file)
            except OSError:
                pass
            except Exception:
                LOGGER.debug("删除临时日志文件失败", exc_info=True)
        return

    # 文件日志（始终 DEBUG，不受配置级别影响）
    # 文件 IO 失败时降级为仅控制台输出，不中断程序
    try:
        os.makedirs(log_dir, exist_ok=True)

        # 合并 default.log 到正式日志文件
        merged_file = _merge_default_log(
            log_dir, log_filename, default_log_file)

        # 创建正式日志文件处理器
        if merged_file is None:
            merged_file = os.path.join(
                log_dir, _make_log_filename(log_filename))

        file_handler = RotatingFileHandler(
            merged_file,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=max_files,
            encoding='utf-8',
        )
        file_handler.setLevel(logging.DEBUG)  # 文件日志始终 DEBUG
        file_formatter = logging.Formatter(
            _LOG_FILE_FORMAT, datefmt=_LOG_FILE_DATEFMT)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
    except OSError as e:
        LOGGER.warning(f"无法创建日志文件，仅启用控制台输出: {e}")
        return

    # 日志自清洁
    _cleanup_old_logs(log_dir, max_files, max_days)
