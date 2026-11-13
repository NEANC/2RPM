#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""无文件与交互副作用的配置诊断和候选生成。"""

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import ScalarString, SingleQuotedScalarString

from modules.monitor_target import resolve_monitor_target
from modules.utils import (build_push_channel_node, parse_push_channels,
                           parse_time_string, push_channel_signature)


FieldPath = tuple[str | int, ...]
LEGACY_TOP_LEVEL_KEYS = ('task', 'launch', 'wait')
LEGACY_MONITOR_KEYS = ('monitor_mode', 'process_name', 'timeout_interval',
                       'loop_interval', 'psutil', 'task_scheduler')
LEGACY_LAUNCH_KEYS = ('type', 'path', 'task_name')


@dataclass(frozen=True)
class Issue:
    """不包含输入原值的配置问题。"""

    path: FieldPath
    code: str
    severity: Literal['error', 'warning']
    message: str


@dataclass(frozen=True)
class Change:
    """仅供内存使用的配置变化。"""

    path: FieldPath
    before: object
    after: object
    operation: Literal['set', 'delete']


@dataclass(frozen=True)
class Candidate:
    """独立的规范化候选及诊断。"""

    tree: CommentedMap
    issues: tuple[Issue, ...]
    changes: tuple[Change, ...]


def layout_issues(tree: object, config_dir: Path, *,
                  resolver=resolve_monitor_target,
                  strict_shared=True) -> tuple[Issue, ...]:
    """按 CLI 的拒绝顺序生成结构与监视目标问题。"""
    issues = []

    def error(path, code, message):
        """添加不泄露配置值的诊断。"""
        issues.append(Issue(path, code, 'error', message))

    if not isinstance(tree, Mapping):
        error((), 'root_type', '配置根节点必须是映射结构')
        return tuple(issues)
    monitor = tree.get('monitor')
    if monitor is not None and not isinstance(monitor, Mapping):
        error(('monitor',), 'mapping', '必须是映射结构')
        return tuple(issues)
    if isinstance(monitor, Mapping):
        for key in ('common', 'launch', 'watch'):
            if key in monitor and not isinstance(monitor[key], Mapping):
                error(('monitor', key), 'mapping', '必须是映射结构')
    external = tree.get('external')
    if external is not None and not isinstance(external, Mapping):
        error(('external',), 'mapping', '必须是映射结构')
    for key in LEGACY_TOP_LEVEL_KEYS:
        if key in tree:
            error((key,), 'legacy', '旧监视布局字段已不再支持')
    if isinstance(monitor, Mapping):
        for key in LEGACY_MONITOR_KEYS:
            if key in monitor:
                error(('monitor', key), 'legacy', '旧监视布局字段已不再支持')
        launch = monitor.get('launch')
        if isinstance(launch, Mapping):
            for key in LEGACY_LAUNCH_KEYS:
                if key in launch:
                    error(('monitor', 'launch', key), 'legacy',
                          '旧监视布局字段已不再支持')
    if isinstance(external, Mapping) and 'timeout_threshold' in external:
        error(('external', 'timeout_threshold'), 'legacy',
              '旧监视布局字段已不再支持')
    if strict_shared:
        for section_name in ('push', 'log'):
            section = tree.get(section_name)
            if section is not None and not isinstance(section, Mapping):
                error((section_name,), 'mapping', '必须是映射结构')
        push = tree.get('push')
        if isinstance(push, Mapping):
            for key in ('retry', 'screenshot', 'templates', 'push_channel_settings'):
                value = push.get(key)
                if value is not None and not isinstance(value, Mapping):
                    error(('push', key), 'mapping', '必须是映射结构')
    if monitor is None:
        error(('monitor',), 'required', '缺少监视配置节')
    if not isinstance(monitor, Mapping):
        return tuple(issues)
    target = monitor.get('target')
    if not isinstance(target, str) or not target.strip():
        error(('monitor', 'target'), 'required', '必须是非空字符串')
    mode = monitor.get('mode', 'watch')
    if mode not in ('watch', 'launch'):
        error(('monitor', 'mode'), 'choice', '必须是 watch 或 launch')
    launch = monitor.get('launch', {})
    if isinstance(launch, Mapping):
        for key in ('args', 'cwd'):
            if key in launch and launch[key] is not None and not isinstance(launch[key], str):
                error(('monitor', 'launch', key), 'type', '必须是字符串或空值')
    if not issues:
        try:
            resolver(mode, target, str(config_dir), launch.get('cwd'))
        except ValueError:
            error(('monitor', 'target'), 'target', '目标或工作目录语法无效')
    return tuple(issues)


def normalize_numeric_value(raw: object, default: object,
                            kind: Literal['time', 'integer', 'lookback']) -> object:
    """按 CLI 既有时间、整数与回溯规则计算无副作用的规范化值。"""
    if raw is None and kind != 'lookback':
        return None
    if raw is None or (isinstance(raw, bool) and kind != 'time'):
        return default
    try:
        if isinstance(raw, str):
            text = raw.strip()
            if text.startswith('-'):
                text = text.lstrip('-').strip()
            if not text:
                return default
            if kind == 'time':
                parse_time_string(text)
                return text
            value = int(text)
            return str(abs(value)) if kind == 'lookback' else value
        if not isinstance(raw, (int, float)) or not math.isfinite(raw):
            return default
        if kind == 'time':
            value = str(abs(int(raw)))
            parse_time_string(value)
            return value
        return int(abs(raw))
    except (ValueError, TypeError, AttributeError, OverflowError):
        return default


def _normalize_values(tree: CommentedMap, defaults: CommentedMap) -> None:
    """在候选中执行现有可写字段的值规范化。"""
    fields = (('monitor', 'common', 'timeout_interval'),
              ('monitor', 'common', 'loop_interval'),
              ('monitor', 'common', 'max_wait'),
              ('monitor', 'common', 'check_interval'),
              ('monitor', 'common', 'timeout_threshold'),
              ('monitor', 'watch', 'lookback_minutes'),
              ('push', 'retry', 'interval'), ('push', 'retry', 'max_count'),
              ('log', 'max_log_files'), ('log', 'retention_days'))
    for path in fields:
        section_name = path[0]
        key = path[-1]
        section = tree.get(section_name)
        if not isinstance(section, Mapping):
            continue
        group = section if len(path) == 2 else section.get(path[1])
        if not isinstance(group, Mapping) or key not in group:
            continue
        raw = group[key]
        default_group = (defaults[section_name] if len(path) == 2
                         else defaults[section_name][path[1]])
        default = default_group[key]
        kind = ('lookback' if key == 'lookback_minutes' else
                'integer' if key in ('timeout_threshold', 'max_count',
                                     'max_log_files', 'retention_days') else 'time')
        value = normalize_numeric_value(raw, default, kind)
        if raw is not None or kind == 'lookback':
            group[key] = value


def _complete_and_clean(tree: CommentedMap, defaults: CommentedMap) -> None:
    """按默认树补足字段并清理非自由格式的未知字段。"""
    monitor = tree.setdefault('monitor', CommentedMap())
    for name in ('common', 'launch', 'watch'):
        monitor.setdefault(name, CommentedMap())
    tree.setdefault('external', CommentedMap())
    for name in defaults:
        tree.setdefault(name, CommentedMap())

    def complete(section, template):
        """递归完成默认值并保留自由格式容器。"""
        for key in list(section):
            if key not in template:
                del section[key]
            elif (isinstance(section[key], Mapping)
                  and isinstance(template[key], Mapping) and template[key]):
                complete(section[key], template[key])
        for key, value in template.items():
            if key not in section:
                section[key] = copy.deepcopy(value)

    complete(tree, defaults)


def _differences(before: object, after: object, path: FieldPath = ()) -> list[Change]:
    """比较候选与源树并记录字段变化。"""
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return [] if before == after else [Change(path, before, after, 'set')]
    changes = []
    for key in before:
        current = path + (key,)
        if key not in after:
            changes.append(Change(current, before[key], None, 'delete'))
        else:
            changes.extend(_differences(before[key], after[key], current))
    for key in after:
        if key not in before:
            changes.append(Change(path + (key,), None, after[key], 'set'))
    return changes


def _format_screenshot_node(value: object, block_sequence: bool = False) -> object:
    """复制截图节点并使用现有 CLI 的列表、映射和令牌格式。"""
    if isinstance(value, dict):
        node = (copy.deepcopy(value) if isinstance(value, CommentedMap)
                else CommentedMap(copy.deepcopy(value)))
        node.fa.set_flow_style()
        for key, item in list(node.items()):
            node[key] = _format_screenshot_node(item)
        return node
    if isinstance(value, list):
        node = (copy.deepcopy(value) if isinstance(value, CommentedSeq)
                else CommentedSeq(copy.deepcopy(value)))
        if block_sequence:
            node.fa.set_block_style()
        else:
            node.fa.set_flow_style()
        for index, item in enumerate(node):
            node[index] = _format_screenshot_node(item)
        return node
    if isinstance(value, str) and '${' in value and not isinstance(value, ScalarString):
        return SingleQuotedScalarString(value)
    return copy.deepcopy(value)


def _format_screenshot(tree: CommentedMap) -> None:
    """对候选中的截图节点应用与 CLI 相同的 YAML 风格。"""
    push = tree.get('push', {})
    screenshot = push.get('screenshot', {}) if isinstance(push, Mapping) else {}
    if isinstance(screenshot, Mapping):
        for key in ('targets', 'image_host'):
            if isinstance(screenshot.get(key), list):
                screenshot[key] = _format_screenshot_node(screenshot[key], True)


def analyze_config(tree: Mapping, *, config_dir: Path,
                   defaults: CommentedMap) -> Candidate:
    """生成不改变输入、不访问文件且不退出进程的配置候选。"""
    issues = layout_issues(tree, config_dir)
    if not isinstance(tree, Mapping) or any(issue.severity == 'error' for issue in issues):
        return Candidate(copy.deepcopy(tree), issues, ())
    result = copy.deepcopy(tree)
    if not isinstance(result, CommentedMap):
        result = CommentedMap(result)
    _normalize_values(result, defaults)
    _complete_and_clean(result, defaults)
    push = result.get('push', {})
    settings = push.get('push_channel_settings', {})
    if isinstance(settings, Mapping):
        raw = settings.get('channels')
        if raw is not None and not (isinstance(raw, (dict, str)) and not raw):
            channels = parse_push_channels(raw)
            if channels:
                normalized = build_push_channel_node(channels)
                if push_channel_signature(raw) != push_channel_signature(normalized):
                    settings['channels'] = normalized
    _format_screenshot(result)
    return Candidate(result, issues, tuple(_differences(tree, result)))
