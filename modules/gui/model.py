#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""不持有控件的 GUI 配置草稿。"""

import copy
import ntpath
import re
from collections.abc import Mapping
from pathlib import Path

from modules.config import get_default_config
from modules.config_document import ConfigDocument
from modules.config_rules import Candidate, Issue, analyze_config
from modules.gui.forms import provider_fields
from modules.monitor_target import resolve_monitor_target
from modules.utils import parse_push_channels


class EditorModel:
    """维护当前文档、编辑代次与模式分支草稿。"""

    def __init__(self, document: ConfigDocument):
        """以已有文档建立独立的内存草稿。"""
        self.document_generation = 0
        self.revision = 0
        self.on_edit = lambda: None
        self.before_list_edit = lambda: True
        self.on_list_edit = lambda: None
        self.replace_document(document)
        self.document_generation = 0

    def get(self, path: tuple[str | int, ...]) -> object:
        """读取草稿字段而不暴露可变内部对象。"""
        value = self._tree
        for key in path:
            value = value[key]
        return copy.deepcopy(value)

    def set(self, path: tuple[str | int, ...], value: object) -> None:
        """仅在字段发生实际改变时递增修订号。"""
        self.raw_inputs.pop(path, None)
        had_input_error = self.input_errors.pop(path, None) is not None
        node = self._tree
        for key in path[:-1]:
            if not isinstance(node, Mapping):
                raise TypeError('配置结构异常，无法写入字段')
            if key not in node:
                node[key] = {}
            node = node[key]
            if not isinstance(node, Mapping):
                raise TypeError('配置结构异常，无法写入字段')
        leaf = path[-1]
        if leaf in node and node[leaf] == value:
            if had_input_error:
                self.revision += 1
                self.on_edit()
            return
        node[leaf] = copy.deepcopy(value)
        self.revision += 1
        self.dirty = True
        if path == ('monitor', 'target'):
            monitor = self._tree.get('monitor')
            mode = monitor.get('mode') if isinstance(monitor, Mapping) else None
            if mode in ('watch', 'launch'):
                self._targets[mode] = copy.deepcopy(value)
        self.on_edit()

    def unset(self, path: tuple[str | int, ...]) -> None:
        """仅删除已存在的叶键，缺失时不做任何改变。"""
        node = self._tree
        for key in path[:-1]:
            if not isinstance(node, Mapping) or key not in node:
                return
            node = node[key]
        if not isinstance(node, Mapping) or path[-1] not in node:
            return
        del node[path[-1]]
        self.raw_inputs.pop(path, None)
        self.input_errors.pop(path, None)
        self.revision += 1
        self.dirty = True
        self.on_edit()

    def set_input(self, path: tuple[str | int, ...], raw: str, *, kind: str = 'text') -> None:
        """保留原始输入，仅将可以表示的字段值写入编辑树。"""
        value = raw
        if kind == 'int':
            try:
                value = int(raw.strip()) if re.fullmatch(r'[+-]?\d+', raw.strip()) else None
            except ValueError:
                value = None
        if kind == 'int' and value is None:
            if self.raw_inputs.get(path) == raw:
                return
            self.raw_inputs[path] = raw
            self.input_errors[path] = Issue(path, 'incomplete_input', 'error', '输入尚未完成')
            self.revision += 1
            self.dirty = True
            self.on_edit()
            return
        self.set(path, value)
        if kind == 'int':
            self.raw_inputs[path] = raw

    def get_input(self, path: tuple[str | int, ...]) -> str:
        """优先返回尚未转换的用户原文。"""
        if path in self.raw_inputs:
            return self.raw_inputs[path]
        value = self.get(path)
        return '' if value is None else str(value)

    def switch_mode(self, mode: str) -> None:
        """切换模式时只在内存中保存与恢复各分支目标。"""
        if mode not in ('watch', 'launch'):
            raise ValueError('无效的监视模式')
        monitor = self._tree.get('monitor')
        if not isinstance(monitor, Mapping):
            monitor = {}
            self._tree['monitor'] = monitor
        current = monitor.get('mode')
        if mode == current:
            return
        if current in ('watch', 'launch'):
            self._targets[current] = monitor.get('target')
        target = self._targets.get(mode, self._original_target)
        monitor['mode'] = mode
        if target is not None:
            monitor['target'] = target
        else:
            monitor.pop('target', None)
        self.revision += 1
        self.dirty = True
        self.on_edit()

    def materialize(self) -> ConfigDocument:
        """返回不携带 GUI 专属数据的独立文档候选。"""
        return ConfigDocument(self.document.path, copy.deepcopy(self._tree),
                              self.document.fingerprint)

    def validate(self, config_dir: Path):
        """以当前草稿运行无文件写入的共享配置诊断。"""
        candidate = analyze_config(self._tree, config_dir=config_dir,
                                   defaults=get_default_config())
        push = self._tree.get('push')
        push = push if isinstance(push, Mapping) else {}
        channel_settings = push.get('push_channel_settings')
        channels = (channel_settings.get('channels')
                    if isinstance(channel_settings, Mapping) else None)
        issues = list(candidate.issues)
        issues.extend(self.input_errors.values())
        monitor = self._tree.get('monitor')
        if isinstance(monitor, Mapping):
            mode = monitor.get('mode')
            target_type = self._gui_target_types.get(mode) if mode in ('watch', 'launch') else None
            target = monitor.get('target')
            if target_type and isinstance(target, str) and target.strip():
                try:
                    resolved = resolve_monitor_target(mode, target, str(config_dir))
                except (ValueError, TypeError):
                    resolved = None
                if target_type == 'task' and (resolved is None or resolved.kind != 'task'):
                    issues.append(Issue(('monitor', 'target'), 'gui_task_path', 'error',
                                        '计划任务须填写完整任务路径'))
                if target_type == 'file' and (resolved is None or resolved.kind != 'file'
                        or not ntpath.isabs(target) or
                        (mode == 'watch' and ntpath.splitext(target)[1].lower() != '.exe')):
                    issues.append(Issue(('monitor', 'target'), 'gui_file_path', 'error',
                                        '程序文件须填写绝对路径；等待模式仅支持 EXE'))
        targets = push.get('screenshot')
        targets = targets.get('targets') if isinstance(targets, Mapping) else None
        if isinstance(targets, list):
            for index, item in enumerate(targets):
                path = ('push', 'screenshot', 'targets', index, 'target')
                raw = self.raw_inputs.get(path)
                if (isinstance(item, Mapping) and item.get('provider') == 'adb'
                        and path not in self.input_errors and raw is not None and raw.isdecimal()
                        and not 1 <= int(raw) <= 65535):
                    issues.append(Issue(path, 'port_range', 'error', 'ADB 端口必须在 1 至 65535 之间'))
        for index, channel in enumerate(parse_push_channels(channels)):
            if not provider_fields(channel.get('provider', '')):
                issues.append(Issue(('push', 'push_channel_settings', 'channels', index),
                                    'unknown_provider', 'error', '未知通知通道'))
        return Candidate(candidate.tree, tuple(issues), candidate.changes)

    def snapshot(self) -> dict:
        """返回可独立修改的草稿快照。"""
        return copy.deepcopy(self._tree)

    def accept_saved(self, document: ConfigDocument) -> None:
        """保存成功后接受磁盘基线并保留另一模式的目标草稿。"""
        changed_path = document.path != self.document.path
        changed_tree = document.tree != self._tree
        raw_inputs = self.raw_inputs if not changed_tree else {}
        gui_target_types = self._gui_target_types
        targets = self._targets.copy()
        if changed_path:
            self.document_generation += 1
            self.revision = 0
        elif changed_tree:
            self.revision += 1
        self._adopt(document)
        self.raw_inputs = raw_inputs
        self._gui_target_types = gui_target_types
        if isinstance(self._tree.get('monitor'), Mapping):
            mode = self._tree['monitor'].get('mode')
            self._targets.update({key: value for key, value in targets.items() if key != mode})

    def replace_document(self, document: ConfigDocument) -> None:
        """新建或打开成功后替换同一模型实例的文档。"""
        self.document_generation += 1
        self.revision = 0
        self._adopt(document)

    def _adopt(self, document: ConfigDocument) -> None:
        """从文档重建编辑树和两个模式的原始目标。"""
        self.document = document
        self.input_errors = {}
        self.raw_inputs = {}
        self._gui_target_types = {}
        self._tree = copy.deepcopy(document.tree)
        monitor = self._tree.get('monitor')
        if isinstance(monitor, Mapping):
            self._original_target = monitor.get('target')
            mode = monitor.get('mode')
            self._targets = {mode: self._original_target} if mode in ('watch', 'launch') else {}
        else:
            self._original_target = None
            self._targets = {}
        self.dirty = False
