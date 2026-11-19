#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 文件操作与启动的无 Tk 事务控制器。"""

import copy
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
        self.on_edit = lambda: None
        self.on_saved = lambda: None
        self.on_conflict = lambda: None
        self.coordinator = None
        self.conflicted = False
        self.save_cancelled = False

    def _resolve_unsaved(self) -> bool:
        """保存、放弃或取消旧草稿，取消时不改变原模型。"""
        if not self.model.dirty and not self.model.input_errors:
            return True
        choice = self.confirm('unsaved', self.model.document.path)
        if choice == 'save':
            return self.save()
        return choice == 'discard'

    def _transaction(self, operation):
        """文件对话框期间暂停旧文档写入，失败时保留草稿。"""
        if self.coordinator is not None:
            self.coordinator.begin_transaction()
        before = self.model.document_generation
        try:
            return operation()
        finally:
            if self.coordinator is not None:
                self.coordinator.end_transaction(self.model.document_generation != before)

    def new(self, picker=None) -> bool:
        """在确认、选择及写盘均成功后才替换当前草稿。"""
        def operation():
            """先问旧草稿，再使用系统选择器。"""
            if not self._resolve_unsaved():
                return False
            if picker is None:
                self.model.replace_document(new_document())
                self.conflicted = False
                self.on_change()
                return True
            selected = picker()
            if selected is None:
                return False
            target = normalize_config_path(Path(selected), program_dir=self.program_dir)
            try:
                data = target.read_bytes()
            except FileNotFoundError:
                data = None
            except OSError:
                return False
            if data is not None and self.confirm('overwrite', str(target)) != 'confirm':
                return False
            expected = hashlib.sha256(data).hexdigest() if data is not None else None
            try:
                document = save_document(new_document(), target, expected_fingerprint=expected)
            except (ConfigConflictError, ConfigSaveError):
                return False
            self.model.replace_document(document)
            self.conflicted = False
            self.on_change()
            return True
        if self.runner.active is True:
            return False
        return self._transaction(operation)

    def open(self, path: Path | Callable[[], Path | None]) -> bool:
        """打开文件对话框选择的原路径，不套用 CLI 后缀转换。"""
        def operation():
            """选择放弃只有打开成功才生效。"""
            if not self._resolve_unsaved():
                return False
            selected = path() if callable(path) else path
            if selected is None:
                return False
            try:
                document = read_document(Path(selected).absolute())
            except ConfigReadError:
                return False
            self.model.replace_document(document)
            self.conflicted = False
            self.on_change()
            return True
        if self.runner.active is True:
            return False
        return self._transaction(operation)

    def save_as(self, picker) -> bool:
        """选定路径前不向原文件补存。"""
        if self.runner.active is True:
            return False
        return self._transaction(lambda: self.save(destination=picker(), as_new=True))

    def check_external(self) -> bool:
        """干净草稿自动重载有效磁盘版本，否则保留草稿并阻断覆盖。"""
        path = self.model.document.path
        if path is None:
            return False
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            data = None
        except OSError:
            self._mark_conflict()
            return True
        fingerprint = hashlib.sha256(data).hexdigest() if data is not None else None
        if fingerprint == self.model.document.fingerprint:
            if self.conflicted and not self.model.dirty and not self.model.input_errors:
                self.conflicted = False
                self.on_change()
            return self.conflicted
        if self.conflicted and (self.model.dirty or self.model.input_errors):
            return True
        if self.model.dirty or self.model.input_errors:
            self._mark_conflict()
            return True
        try:
            document = read_document(path)
        except ConfigReadError:
            self._mark_conflict()
            return True
        self.model.replace_document(document)
        self.conflicted = False
        self.on_change()
        return False

    def _mark_conflict(self):
        """只在首次进入冲突状态时提示。"""
        if not self.conflicted:
            self.conflicted = True
            self.on_conflict()

    def resolve_external(self, choice: str, destination: Path | None = None) -> bool:
        """解决外部冲突：重载、另存新路径或维持冻结。"""
        if choice == 'later':
            return False
        if choice == 'save_as':
            if destination is None or normalize_config_path(Path(destination), program_dir=self.program_dir) == self.model.document.path:
                return False
            return self.save(destination, as_new=True)
        if choice != 'reload' or self.model.document.path is None:
            return False
        try:
            document = read_document(self.model.document.path)
        except ConfigReadError:
            return False
        if (self.model.dirty or self.model.input_errors) and self.confirm('discard_external', None) != 'confirm':
            return False
        try:
            document = read_document(self.model.document.path)
        except ConfigReadError:
            return False
        self.model.replace_document(document)
        self.conflicted = False
        self.on_change()
        return True

    def save(self, destination: Path | None = None, *, as_new: bool = False) -> bool:
        """对最终规范化目标确认、检查指纹并原子保存。"""
        cancelled = [False]
        self.save_cancelled = False
        try:
            return self._save(destination, as_new=as_new, cancelled=cancelled)
        finally:
            self.save_cancelled = cancelled[0]

    def _save(self, destination: Path | None, *, as_new: bool, cancelled: list[bool]) -> bool:
        """执行保存事务，取消状态仅归属于当前调用。"""
        if self.model.input_errors:
            return False
        selected = destination if as_new else destination or self.model.document.path or self.choose_path()
        if selected is None:
            cancelled[0] = True
            return False
        target = normalize_config_path(Path(selected), program_dir=self.program_dir)
        previous = self.model.document.path
        same = target == previous
        if same and previous is not None and not self.conflicted:
            generation = self.model.document_generation
            if self.check_external():
                return False
            if self.model.document_generation != generation:
                return not self.model.dirty
        revision = self.model.revision
        document = self.model.materialize()
        if self.conflicted and same:
            return False
        if self.conflicted and not as_new and previous is not None:
            return False
        if not same and previous is not None and target.parent != previous.parent:
            mode = self.model.get(('monitor', 'mode'))
            monitor = self.model.get(('monitor', 'target'))
            cwd = self.model.get(('monitor', 'launch', 'cwd'))
            try:
                resolved = resolve_monitor_target(mode, monitor, str(target.parent), cwd)
                resolved_target, resolved_cwd = resolved.value, resolved.cwd
            except (ValueError, TypeError):
                resolved_target, resolved_cwd = '待校验', '待校验'
            detail = {'from': str(previous.parent), 'to': str(target.parent),
                      'target': str(target), 'resolved_target': resolved_target,
                      'resolved_cwd': resolved_cwd}
            if self.confirm('relocate', detail) != 'confirm':
                cancelled[0] = True
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
                cancelled[0] = True
                return False
        if same and previous is not None and not self.conflicted:
            generation = self.model.document_generation
            if self.check_external() or self.model.document_generation != generation:
                return False
        try:
            saved = save_document(document, target, expected_fingerprint=expected)
        except ConfigConflictError:
            if same:
                self._mark_conflict()
            return False
        except ConfigSaveError:
            return False
        newer = self.model.revision != revision
        changed_tree = saved.tree != document.tree
        if newer:
            draft = self.model.snapshot()
            raw_inputs = copy.deepcopy(self.model.raw_inputs)
            input_errors = copy.deepcopy(self.model.input_errors)
        self.model.accept_saved(saved)
        if newer:
            self.model._tree = draft
            self.model.raw_inputs = raw_inputs
            self.model.input_errors = input_errors
            self.model.dirty = True
            self.model.revision += 1
            self.model._targets = {draft['monitor']['mode']: copy.deepcopy(draft['monitor']['target'])}
        self.conflicted = False
        if same and not changed_tree:
            self.on_saved()
        else:
            self.on_change()
        return True

    def start_cli(self) -> LaunchResult | None:
        """先保存并复验磁盘候选，再创建独立 CLI 控制台。"""
        if self.conflicted or self.model.input_errors:
            return None
        if not self.cli_command:
            return LaunchResult(False, '未找到匹配的 CLI 程序')
        if self.model.dirty:
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
            self._mark_conflict()
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
            except ConfigConflictError:
                self._mark_conflict()
                return None
            except (ConfigReadError, ConfigSaveError):
                return None
            if disk.fingerprint != saved.fingerprint or disk.tree != saved.tree:
                return None
            self.model.accept_saved(saved)
            self.on_change()
            verified = self.model.validate(path.parent)
            if verified.changes or any(issue.severity == 'error' for issue in verified.issues):
                return None
        if self.launch_count:
            generation, revision = self.model.document_generation, self.model.revision
            if self.confirm('repeat_launch', str(path)) != 'confirm':
                return None
            if (self.model.document_generation != generation or self.model.revision != revision
                    or self.model.dirty or self.check_external()):
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
        def operation():
            """最终批准前不清理当前草稿或 worker。"""
            if not self._resolve_unsaved():
                return False
            if self.runner.active is True and self.confirm('exit_test', None) != 'confirm':
                return False
            if self.confirm('exit', None) != 'confirm':
                return False
            self.runner.close()
            return True
        return self._transaction(operation)

    def log_directory(self, program_dir: Path) -> Path:
        """按 CLI 程序目录解析日志目录，不读取或创建日志。"""
        try:
            value = self.model.get(('log', 'log_directory'))
        except (KeyError, IndexError, TypeError):
            value = ''
        return (program_dir / (value if isinstance(value, str) and value else 'logs')).absolute()
