#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 文件操作与启动的无 Tk 事务控制器。"""

import hashlib
import math
import re
from pathlib import Path
from typing import Callable

from modules.config_document import (ConfigConflictError, ConfigReadError,
                                     ConfigSaveError, new_document,
                                     normalize_config_path, patch_document,
                                     read_document, save_document)
from modules.gui.launcher import LaunchResult, launch_cli
from modules.gui.model import EditorModel
from modules.gui.test_runner import TestRunner
from modules.monitor_target import resolve_monitor_target


class Controller:
    """持有唯一草稿，只有磁盘事务成功才接受新基线。"""

    def __init__(self, model: EditorModel, *, confirm: Callable[[str, object], str],
                 choose_path: Callable[[], Path | None], cli_command: tuple[str, ...],
                 runner: TestRunner, program_dir: Path):
        """注入确认及路径选择接口，不在构造时读取或创建配置。"""
        self.model = model
        self.confirm = confirm
        self.choose_path = choose_path
        self.cli_command = cli_command
        self.runner = runner
        self.program_dir = program_dir.resolve()
        self.launch_count = 0
        self.on_change = lambda: None

    def _resolve_unsaved(self) -> bool:
        """保存、放弃或取消旧草稿，取消时不改变原模型。"""
        if not self.model.dirty and not self.model.input_errors:
            return True
        choice = self.confirm('unsaved', self.model.document.path)
        if choice == 'save':
            return self.save()
        return choice == 'discard'

    def new(self) -> bool:
        """确认旧草稿后在原模型实例上建立新文档。"""
        if not self._resolve_unsaved():
            return False
        self.model.replace_document(new_document())
        self.on_change()
        return True

    def open(self, path: Path) -> bool:
        """打开文件对话框选择的原路径，不套用 CLI 后缀转换。"""
        try:
            document = read_document(path.absolute())
        except ConfigReadError:
            return False
        if not self._resolve_unsaved():
            return False
        self.model.replace_document(document)
        self.on_change()
        return True

    def save(self, destination: Path | None = None) -> bool:
        """对最终规范化目标确认、检查指纹并原子保存。"""
        if self.model.input_errors:
            return False
        selected = destination or self.model.document.path or self.choose_path()
        if selected is None:
            return False
        target = normalize_config_path(Path(selected), program_dir=self.program_dir)
        previous = self.model.document.path
        same = target == previous
        if not same and previous is not None and target.parent != previous.parent:
            mode = self.model.get(('monitor', 'mode'))
            monitor = self.model.get(('monitor', 'target'))
            cwd = self.model.get(('monitor', 'launch', 'cwd'))
            try:
                resolved = resolve_monitor_target(mode, monitor, str(target.parent), cwd)
            except ValueError:
                return False
            detail = {'from': str(previous.parent), 'to': str(target.parent),
                      'target': str(target), 'resolved_target': resolved.value,
                      'resolved_cwd': resolved.cwd}
            if self.confirm('relocate', detail) != 'confirm':
                return False
        if same:
            expected = self.model.document.fingerprint
        else:
            try:
                data = target.read_bytes()
            except FileNotFoundError:
                data = None
            except OSError:
                return False
            expected = hashlib.sha256(data).hexdigest() if data is not None else None
            if data is not None and self.confirm('overwrite', str(target)) != 'confirm':
                return False
        document = self.model.materialize()
        try:
            saved = save_document(document, target, expected_fingerprint=expected)
        except (ConfigConflictError, ConfigSaveError):
            return False
        self.model.accept_saved(saved)
        self.on_change()
        return True

    def start_cli(self) -> LaunchResult | None:
        """先保存并复验磁盘候选，再创建独立 CLI 控制台。"""
        if not self.cli_command:
            return LaunchResult(False, '未找到匹配的 CLI 程序')
        base = self.model.document.path.parent if self.model.document.path else self.program_dir
        if any(issue.severity == 'error' for issue in self.model.validate(base).issues):
            return None
        if self.model.dirty:
            if self.confirm('unsaved', self.model.document.path) != 'save':
                return None
            if not self.save():
                return None
        path = self.model.document.path
        if path is None or normalize_config_path(path, program_dir=self.program_dir) != path:
            if not self.save():
                return None
            path = self.model.document.path
        try:
            disk = read_document(path)
        except ConfigReadError:
            return None
        if disk.fingerprint != self.model.document.fingerprint:
            return None
        candidate = self.model.validate(path.parent)
        if any(issue.severity == 'error' for issue in candidate.issues):
            return None
        if candidate.changes:
            summary = tuple({'path': change.path, 'operation': change.operation,
                             'before': self._display_change(change.path, change.before),
                             'after': self._display_change(change.path, change.after)}
                            for change in candidate.changes)
            if self.confirm('normalize', summary) != 'confirm':
                return None
            try:
                normalized = patch_document(disk, candidate.changes)
                saved = save_document(normalized, path, expected_fingerprint=disk.fingerprint)
                disk = read_document(path)
            except (ConfigConflictError, ConfigReadError, ConfigSaveError):
                return None
            if disk.fingerprint != saved.fingerprint or disk.tree != saved.tree:
                return None
            self.model.accept_saved(saved)
            self.on_change()
            verified = self.model.validate(path.parent)
            if verified.changes or any(issue.severity == 'error' for issue in verified.issues):
                return None
        if self.launch_count and self.confirm('repeat_launch', str(path)) != 'confirm':
            return None
        result = launch_cli(self.cli_command, path.resolve())
        if result.created:
            self.launch_count += 1
        return result

    @staticmethod
    def _display_change(path, value):
        """仅允许已知非敏感的标量显示原值，其余一律遮挡。"""
        safe_paths = {('monitor', 'common', 'max_wait'),
                      ('monitor', 'common', 'max_run'),
                      ('monitor', 'common', 'loop_interval'),
                      ('monitor', 'common', 'timeout_threshold')}
        if value is None:
            return '（无）'
        if path in safe_paths and not isinstance(value, bool):
            if path[-1] == 'timeout_threshold':
                if isinstance(value, int) or isinstance(value, str) and re.fullmatch(r'-?\d+', value):
                    return str(value)
            elif (isinstance(value, (int, float)) and math.isfinite(value)
                  or isinstance(value, str) and re.fullmatch(r'-?\d+(?:\.\d+)?[hms]', value, re.I)
                  or isinstance(value, str) and re.fullmatch(r'-?\d+', value)):
                return str(value)
        return '（已遮挡）'

    def close(self) -> bool:
        """关闭仅清理测试 worker，不控制已创建的 CLI。"""
        if not self._resolve_unsaved():
            return False
        self.runner.close()
        return True

    def log_directory(self, program_dir: Path) -> Path:
        """按 CLI 程序目录解析日志目录，不读取或创建日志。"""
        value = self.model.get(('log', 'log_directory'))
        return (program_dir / (value or 'logs')).absolute()
