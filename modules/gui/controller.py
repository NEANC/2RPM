#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 文件操作与启动的无 Tk 事务控制器。"""

import copy
import hashlib
import math
import re
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Callable

from modules.config_document import (ConfigConflictError, ConfigReadError,
                                     ConfigSaveError, new_blank_document,
                                     new_document, normalize_config_path,
                                     patch_document, read_document,
                                     save_document)
from modules.gui.launcher import LaunchResult, launch_cli
from modules.gui.model import EditorModel
from modules.gui.test_runner import TestRunner
from modules.monitor_target import RELATIVE_PATH_PREFIXES, resolve_monitor_target


class SaveResult(Enum):
    """单次保存调用的成功、取消、失败、冲突、被重入成功取代及用户重载丢弃。"""

    SAVED = 'saved'
    CANCELLED = 'cancelled'
    FAILED = 'failed'
    CONFLICT = 'conflict'
    SUPERSEDED = 'superseded'
    RELOADED = 'reloaded'


LAUNCH_FAILURE_MESSAGE = '未能启动 CLI，请检查配置、磁盘文件或保存状态。'


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
        self.unsaved_input_blocked = False
        self.cli_cancelled = False
        self._saved_identity = 0
        self._saved_document_identity = None
        self._reload_count = 0

    def _resolve_unsaved(self) -> bool:
        """保存、放弃或取消旧草稿，取消时不改变原模型。"""
        self.unsaved_input_blocked = False
        if not self.model.dirty and not self.model.input_errors:
            return True
        choice = self.confirm('unsaved', self.model.document.path)
        if choice == 'save':
            saved = self.save()
            self.unsaved_input_blocked = not saved and bool(self.model.input_errors)
            return saved
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
                self.model.replace_document(new_blank_document())
                self.conflicted = False
                self.on_change()
                return True
            selected = picker()
            if selected is None:
                return False
            target = normalize_config_path(Path(selected), program_dir=self.program_dir)
            if target == self.model.document.path:
                generation = self.model.document_generation
                if self.check_external() or self.model.document_generation != generation:
                    return False
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
        return self._open_result(path) is SaveResult.SAVED

    def _open_result(self, path: Path | Callable[[], Path | None]) -> SaveResult:
        """返回本次打开的成功、取消或读取失败结果。"""
        def operation():
            """选择放弃只有打开成功才生效。"""
            if not self._resolve_unsaved():
                return SaveResult.CANCELLED
            selected = path() if callable(path) else path
            if selected is None:
                return SaveResult.CANCELLED
            try:
                document = read_document(Path(selected).absolute())
            except ConfigReadError:
                return SaveResult.FAILED
            self.model.replace_document(document)
            self.conflicted = False
            self.on_change()
            return SaveResult.SAVED
        if self.runner.active is True:
            return SaveResult.FAILED
        return self._transaction(operation)

    def save_as(self, picker) -> bool:
        """选定路径前不向原文件补存。"""
        return self._save_as_result(picker) in (SaveResult.SAVED, SaveResult.SUPERSEDED)

    def _save_as_result(self, picker) -> SaveResult:
        """返回本次另存的局部结果，不读取重入调用的共享取消标志。"""
        if self.runner.active is True:
            return SaveResult.FAILED
        if self.model.input_errors:
            self.save_cancelled = False
            return SaveResult.FAILED

        def operation():
            """在事务内执行路径选择与写盘。"""
            return self._save_result(destination=picker(), as_new=True)

        result = SaveResult.FAILED
        try:
            result = self._transaction(operation)
            return result
        finally:
            self.save_cancelled = result is SaveResult.CANCELLED

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
        self._reload_count += 1
        self.on_change()
        return True

    def save(self, destination: Path | None = None, *, as_new: bool = False) -> bool:
        """对最终规范化目标确认、检查指纹并原子保存。"""
        return self._save_result(destination, as_new=as_new) in (SaveResult.SAVED, SaveResult.SUPERSEDED)

    def _save_result(self, destination: Path | None = None, *, as_new: bool = False) -> SaveResult:
        """以本次调用的结果驱动兼容性取消标志。"""
        self.save_cancelled = False
        result = SaveResult.FAILED
        try:
            result = self._save(destination, as_new=as_new)
            return result
        finally:
            self.save_cancelled = result is SaveResult.CANCELLED

    def _save(self, destination: Path | None, *, as_new: bool) -> SaveResult:
        """执行保存事务并直接返回本次调用的结果。"""
        reloads = self._reload_count
        if self.model.input_errors:
            return SaveResult.FAILED
        selected = destination if as_new else destination or self.model.document.path or self.choose_path()
        if selected is None:
            return SaveResult.CANCELLED
        target = normalize_config_path(Path(selected), program_dir=self.program_dir)
        previous = self.model.document.path
        same = target == previous
        if same and previous is not None and not self.conflicted:
            generation = self.model.document_generation
            saved_identity = self._saved_identity
            dirty = self.model.dirty

            def inner_save_is_current():
                """仅识别仍为当前文档基线的内层保存。"""
                current = self.model.document
                return (self._saved_identity != saved_identity and not self.model.dirty
                        and (self.model.document_generation, current.path, current.fingerprint)
                        == self._saved_document_identity)

            if self.check_external():
                if self._reload_count != reloads and not self.model.dirty:
                    return SaveResult.RELOADED
                if self.model.document_generation != generation and not self.model.dirty:
                    return (SaveResult.SUPERSEDED if inner_save_is_current()
                            else SaveResult.FAILED if dirty else SaveResult.SAVED)
                return SaveResult.CONFLICT if self.conflicted else SaveResult.FAILED
            if self.model.document_generation != generation:
                if self._reload_count != reloads and not self.model.dirty:
                    return SaveResult.RELOADED
                if self.model.dirty:
                    return SaveResult.FAILED
                return (SaveResult.SUPERSEDED if inner_save_is_current()
                        else SaveResult.FAILED if dirty else SaveResult.SAVED)
        revision = self.model.revision
        document = self.model.materialize()
        if self.conflicted and same:
            return SaveResult.CONFLICT
        if self.conflicted and not as_new and previous is not None:
            return SaveResult.CONFLICT
        if same:
            expected = self.model.document.fingerprint
            existing = None
        else:
            try:
                existing = target.read_bytes()
            except FileNotFoundError:
                existing = None
            except OSError:
                return SaveResult.FAILED
            expected = hashlib.sha256(existing).hexdigest() if existing is not None else None
        if not same and previous is not None and target.parent != previous.parent:
            relative = self._relative_paths(document.tree)
            if relative is not None:
                try:
                    resolved = resolve_monitor_target(relative['mode'], relative['target'],
                                                      str(target.parent), relative['cwd'])
                    resolved_target, resolved_cwd = resolved.value, resolved.cwd
                except (ValueError, TypeError):
                    resolved_target, resolved_cwd = '待校验', '待校验'
                detail = {'from': str(previous.parent), 'to': str(target.parent),
                          'target': str(target), 'resolved_target': resolved_target,
                          'resolved_cwd': resolved_cwd}
                if self.confirm('relocate', detail) != 'confirm':
                    return SaveResult.CANCELLED
        if not same and existing is not None and self.confirm('overwrite', str(target)) != 'confirm':
            return SaveResult.CANCELLED
        if same and previous is not None and not self.conflicted:
            generation = self.model.document_generation
            if self.check_external() or self.model.document_generation != generation:
                if self._reload_count != reloads and not self.model.dirty:
                    return SaveResult.RELOADED
                return (SaveResult.SUPERSEDED if inner_save_is_current()
                        else SaveResult.CONFLICT if self.conflicted else SaveResult.FAILED)
        try:
            saved = save_document(document, target, expected_fingerprint=expected)
        except ConfigConflictError:
            if same:
                self._mark_conflict()
                if self._reload_count != reloads and not self.model.dirty:
                    return SaveResult.RELOADED
                return SaveResult.CONFLICT
            return SaveResult.FAILED
        except ConfigSaveError:
            return SaveResult.FAILED
        newer = self.model.revision != revision
        changed_tree = saved.tree != document.tree
        if newer:
            draft = self.model.snapshot()
            raw_inputs = copy.deepcopy(self.model.raw_inputs)
            input_errors = copy.deepcopy(self.model.input_errors)
        self.model.accept_saved(saved)
        self._saved_identity += 1
        self._saved_document_identity = (self.model.document_generation, saved.path,
                                         saved.fingerprint)
        if newer:
            self.model._tree = draft
            self.model.raw_inputs = raw_inputs
            self.model.input_errors = input_errors
            self.model.dirty = True
            self.model.revision += 1
            monitor = draft.get('monitor') if isinstance(draft, Mapping) else None
            if isinstance(monitor, Mapping) and monitor.get('mode') in ('watch', 'launch'):
                self.model._targets = {
                    monitor['mode']: copy.deepcopy(monitor.get('target'))}
        self.conflicted = False
        if same and not changed_tree:
            self.on_saved()
        else:
            self.on_change()
        return SaveResult.SAVED

    @staticmethod
    def _relative_paths(tree) -> dict | None:
        """返回受配置目录影响的相对目标或工作目录，没有时返回 None。"""
        monitor = tree.get('monitor') if isinstance(tree, Mapping) else None
        if not isinstance(monitor, Mapping):
            return None
        mode = monitor.get('mode')
        target = monitor.get('target')
        launch = monitor.get('launch')
        cwd = launch.get('cwd') if isinstance(launch, Mapping) else None
        relative_target = (isinstance(target, str)
                           and target.startswith(RELATIVE_PATH_PREFIXES))
        relative_cwd = (mode == 'launch' and isinstance(cwd, str)
                        and cwd.startswith(RELATIVE_PATH_PREFIXES))
        if not relative_target and not relative_cwd:
            return None
        return {'mode': mode, 'target': target, 'cwd': cwd}

    def start_cli(self) -> LaunchResult | None:
        """先保存并复验磁盘候选，再创建独立 CLI 控制台。"""
        self.cli_cancelled = False
        if self.conflicted or self.model.input_errors:
            return None
        if not self.cli_command:
            return LaunchResult(False, '未找到匹配的 CLI 程序')
        if self.model.dirty:
            save_result = self._save_result()
            self.cli_cancelled = save_result is SaveResult.CANCELLED
            if save_result not in (SaveResult.SAVED, SaveResult.SUPERSEDED):
                return None
        path = self.model.document.path
        if path is None or normalize_config_path(path, program_dir=self.program_dir) != path:
            save_result = self._save_result()
            self.cli_cancelled = save_result is SaveResult.CANCELLED
            if save_result not in (SaveResult.SAVED, SaveResult.SUPERSEDED):
                return None
            path = self.model.document.path
        try:
            disk = read_document(path)
        except ConfigReadError:
            return None
        if disk.fingerprint != self.model.document.fingerprint:
            self._mark_conflict()
            return None
        candidate = EditorModel(disk).validate(path.parent)
        if (any(issue.severity == 'error' for issue in candidate.issues)
                or any(issue.severity == 'error' for issue in self.model.validate(path.parent).issues)):
            return None
        if candidate.changes:
            summary = tuple({'path': change.path, 'operation': change.operation,
                             'before': self._display_change(change.path, change.before),
                             'after': self._display_change(change.path, change.after)}
                            for change in candidate.changes)
            generation, revision = self.model.document_generation, self.model.revision
            confirmed = self.confirm('normalize', summary) == 'confirm'
            self.cli_cancelled = not confirmed
            if not confirmed:
                return None
            if (self.model.document_generation != generation or self.model.revision != revision
                    or self.model.dirty or self.conflicted
                    or self.model.document.path != path
                    or self.model.document.fingerprint != disk.fingerprint):
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
            verified = EditorModel(disk).validate(path.parent)
            if verified.changes or any(issue.severity == 'error' for issue in verified.issues):
                return None
        if self.launch_count:
            generation, revision = self.model.document_generation, self.model.revision
            confirmed = self.confirm('repeat_launch', str(path)) == 'confirm'
            self.cli_cancelled = not confirmed
            if not confirmed:
                return None
            external = self.check_external()
            if self.model.document_generation != generation:
                if (self.model.document.path == path and not self.model.dirty
                        and any(issue.severity == 'error' for issue in
                                self.model.validate(path.parent).issues)):
                    return LaunchResult(False, LAUNCH_FAILURE_MESSAGE)
                return None
            if external or self.model.revision != revision or self.model.dirty:
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
