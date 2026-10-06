#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""截图本地保留策略、命名与日期目录清理。"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import ntpath
import re

from . import winfs


_RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$', re.I)
_DATE_NAME = re.compile(r'[0-9]{4}_[0-9]{2}_[0-9]{2}')


@dataclass(frozen=True)
class RetentionPolicy:
    """保存已校验的保留开关、天数及安全告警。"""

    enabled: bool = False
    max_days: int = 14
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SaveOutcome:
    """保存结果与失败时仍可用于上传的候选文件名。"""

    path: str | None
    filename: str
    warnings: tuple[str, ...] = ()


def parse_policy(value):
    """保留非法原值的含义，不把 bool 当作天数整数。"""
    if value is None:
        return RetentionPolicy()
    if not isinstance(value, Mapping):
        return RetentionPolicy(warnings=('截图保留配置无效，已关闭',))
    enabled = value.get('enabled')
    if enabled is not None and type(enabled) is not bool:
        return RetentionPolicy(warnings=('截图保留配置无效，已关闭',))
    days = value.get('max_days', 14)
    if type(days) is not int or days <= 0:
        return RetentionPolicy(enabled is True, 14, ('截图保留天数无效，已使用 14 天',))
    return RetentionPolicy(enabled is True, days)


def sanitize_component(value: str, fallback: str) -> str:
    """按 UTF-16 单元净化为最多八十单元的 Windows 单组件。"""
    text = ''.join('_' if ord(char) < 32 or char in '<>:"/\\|?*' else char
                   for char in value)
    text = text.strip().rstrip('.').strip()
    if not text or text in ('.', '..'):
        text = fallback
    if _RESERVED.fullmatch(text.split('.')[0]):
        text = '_' + text
    text = text.encode('utf-16-le')[:160].decode('utf-16-le', errors='ignore')
    text = text.strip().rstrip('.').strip() or fallback
    if _RESERVED.fullmatch(text.split('.')[0]):
        text = '_' + text
        text = text.encode('utf-16-le')[:160].decode('utf-16-le', errors='ignore')
        text = text.rstrip().rstrip('.').rstrip()
    return text


def runtime_context(program_dir: str, config_file: str) -> dict[str, str]:
    """只保存根和净化的配置 stem，不持有完整配置路径。"""
    stem = ntpath.splitext(ntpath.basename(config_file))[0]
    return {'program_dir': program_dir, 'config_stem': sanitize_component(stem, 'config')}


def _directory_date(name):
    """严格 ASCII 日期名称，不使用 mtime。"""
    if _DATE_NAME.fullmatch(name) is None:
        return None
    try:
        return date(*map(int, name.split('_')))
    except ValueError:
        return None


def cli_filename(result, target, is_file, today, sequence=0):
    """生成 CLI 文件名或目录输出的序列候选名。"""
    extension = {'jpeg': '.jpg', 'png': '.png', 'webp': '.webp', 'raw': '.raw'}[result.image_format]
    if is_file:
        stem, old_extension = ntpath.splitext(target)
        if old_extension.casefold() in ('.png', '.jpg', '.jpeg') and result.image_format == 'png':
            return stem + extension
        return target
    return f'screenshot_{today:%Y%m%d}_{sequence:04d}{extension}'


def save_cli(result, target, is_file, *, today=None):
    """排他保存 CLI 结果，不覆盖现存目标。"""
    today = date.today() if today is None else today
    filename = cli_filename(result, target, is_file, today)
    if is_file:
        path = target
        try:
            winfs.write_exclusive(path, result.image_bytes)
            return SaveOutcome(path, ntpath.basename(path))
        except Exception:
            return SaveOutcome(None, ntpath.basename(path), ('截图保存失败：无法写入输出路径',))
    sequence = 0
    while True:
        filename = cli_filename(result, target, False, today, sequence)
        path = ntpath.join(target, filename)
        try:
            winfs.write_exclusive(path, result.image_bytes)
            return SaveOutcome(path, filename)
        except FileExistsError:
            sequence += 1
        except Exception:
            return SaveOutcome(None, filename, ('截图保存失败：无法写入输出路径',))


def cleanup_retention(program_dir, policy, *, today=None):
    """只清理过期日期树；一树失败继续下一树。"""
    if not policy.enabled:
        return ()
    today = date.today() if today is None else today
    root = ntpath.join(program_dir, 'screenshot')
    try:
        names = winfs.list_names(root)
    except FileNotFoundError:
        return ()
    except Exception:
        return ('截图清理失败：无法安全枚举受管目录',)
    warnings = []
    for name in names:
        folder_date = _directory_date(name)
        if folder_date is None or (today - folder_date).days < policy.max_days:
            continue
        try:
            winfs.remove_tree(ntpath.join(root, name))
        except FileNotFoundError:
            continue
        except Exception:
            warnings.append('截图清理失败：已跳过不安全或无法删除的日期目录')
    return tuple(dict.fromkeys(warnings))


def cli_target_is_managed(path: str, is_file: bool, program_dir: str) -> bool:
    """按组件边界识别受管日期目录中的 CLI 目标。"""
    root = ntpath.normcase(ntpath.abspath(ntpath.join(program_dir, 'screenshot')))
    destination = ntpath.dirname(path) if is_file else path
    destination = ntpath.normcase(ntpath.abspath(destination))
    try:
        if ntpath.commonpath((root, destination)) != root:
            return False
    except ValueError:
        return False
    relative = ntpath.relpath(destination, root)
    return _directory_date(relative.split('\\')[0]) is not None


def save_automatic(result, runtime, event, policy, *, today=None):
    """按事件最大序号排他保存自动截图。"""
    if not policy.enabled:
        return SaveOutcome(None, '', policy.warnings)
    name = 'event_01.jpg'
    try:
        today = date.today() if today is None else today
        event = sanitize_component(event, 'event')
        folder = ntpath.join(runtime['program_dir'], 'screenshot', today.strftime('%Y_%m_%d'),
                             runtime['config_stem'])
        try:
            names = winfs.list_names(folder)
        except FileNotFoundError:
            names = ()
        pattern = re.compile(re.escape(event) + r'_([0-9]+)\.jpg', re.I)
        maximum = max((int(match.group(1)) for item in names
                       if (match := pattern.fullmatch(item))), default=0)
        sequence = maximum + 1
        while True:
            name = f'{event}_{sequence:02d}.jpg'
            path = ntpath.join(folder, name)
            try:
                winfs.write_exclusive(path, result.image_bytes)
                return SaveOutcome(path, name, policy.warnings)
            except FileExistsError:
                sequence += 1
    except Exception:
        return SaveOutcome(None, name, policy.warnings + ('截图保存失败：无法写入输出路径',))
