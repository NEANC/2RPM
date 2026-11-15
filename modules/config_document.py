#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""无交互的 YAML 配置文档读取、局部补丁与冲突保护保存。"""

import copy
import hashlib
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarbool import ScalarBoolean
from ruamel.yaml.scalarfloat import ScalarFloat
from ruamel.yaml.scalarint import ScalarInt
from ruamel.yaml.scalarstring import ScalarString

from modules.config import _make_write_yaml, get_default_config
from modules.config_rules import Change
from modules.yaml_scalar_compat import normalize_config_scalars


class ConfigConflictError(Exception):
    """目标文件已与确认时的指纹不符。"""


class ConfigReadError(Exception):
    """配置读取或解析失败。"""


class ConfigSaveError(Exception):
    """配置保存失败。"""


@dataclass(frozen=True)
class ConfigDocument:
    """配置原始树及磁盘字节指纹。"""

    path: Path | None
    tree: CommentedMap
    fingerprint: str | None


def normalize_config_path(path: Path, *, program_dir: Path) -> Path:
    """按 CLI 区分大小写的尾串规则解析最终绝对配置路径。"""
    name = str(path)
    if not name.endswith(('.yaml', '.yml')):
        path = Path(name + '.yaml')
    return (program_dir / path).absolute()


def read_document(path: Path) -> ConfigDocument:
    """仅读取原始字节与保留引号注释的 YAML，不回写文件。"""
    try:
        data = path.read_bytes()
        yaml = YAML()
        yaml.preserve_quotes = True
        tree = yaml.load(normalize_config_scalars(data.decode('utf-8')))
        if not isinstance(tree, CommentedMap):
            raise ValueError('invalid root')
    except Exception:
        raise ConfigReadError('无法读取配置文档') from None
    return ConfigDocument(path, tree, hashlib.sha256(data).hexdigest())


def new_document() -> ConfigDocument:
    """仅在内存中生成带项目默认注释的配置。"""
    return ConfigDocument(None, get_default_config(for_file_creation=True), None)


def patch_document(document: ConfigDocument,
                   changes: tuple[Change, ...]) -> ConfigDocument:
    """复制原始树，仅按明确的变化路径更新或删除叶节点。"""
    tree = copy.deepcopy(document.tree)
    anchor_paths = {}
    anchor_names = set()
    scalar_types = (ScalarString, ScalarInt, ScalarFloat, ScalarBoolean)

    def collect_anchors(value, path):
        """记录每个锚点在 YAML 树中第一次出现的路径。"""
        if isinstance(value, scalar_types) and value.anchor.value:
            anchor_names.add(value.anchor.value)
            return
        if isinstance(value, (CommentedMap, CommentedSeq)):
            if value.anchor.value:
                if id(value) in anchor_paths:
                    return
                anchor_paths[id(value)] = path
                anchor_names.add(value.anchor.value)
            children = value.items() if isinstance(value, CommentedMap) else enumerate(value)
            for key, child in children:
                collect_anchors(child, (*path, key))

    collect_anchors(tree, ())

    def rename_nested_anchors(value, visited):
        """为复制分支内部的锚点分配唯一名称并保持内部别名关系。"""
        if not isinstance(value, (CommentedMap, CommentedSeq, *scalar_types)) or id(value) in visited:
            return
        visited.add(id(value))
        if value.anchor.value:
            name = value.anchor.value
            index = 1
            while f'{name}_{index}' in anchor_names:
                index += 1
            name = f'{name}_{index}'
            value.yaml_set_anchor(name, always_dump=value.anchor.always_dump)
            anchor_names.add(name)
        children = value.values() if isinstance(value, CommentedMap) else (
            value if isinstance(value, CommentedSeq) else ())
        for child in children:
            rename_nested_anchors(child, visited)

    def detach_alias(parent, key, path):
        """在修改别名分支前复制容器，避免回写到锚点定义。"""
        value = parent[key]
        if isinstance(value, (CommentedMap, CommentedSeq)) and value.anchor.value and anchor_paths[id(value)] != path:
            value.yaml_set_anchor(value.anchor.value, always_dump=True)
            parent[key] = copy.deepcopy(value)
            parent[key].yaml_set_anchor(None)
            children = parent[key].values() if isinstance(parent[key], CommentedMap) else parent[key]
            visited = set()
            for child in children:
                rename_nested_anchors(child, visited)
            collect_anchors(parent[key], path)

    def merge_list_items(existing, incoming):
        """仅在能唯一确认项目对应关系时保留原项目中的未知键。"""
        items = (*existing, *incoming)
        if any(isinstance(item, dict) for item in items) and not all(
                isinstance(item, dict) for item in items):
            raise ConfigSaveError('无法可靠匹配列表项目，配置未保存')
        if not all(isinstance(item, dict) for item in items):
            return copy.deepcopy(incoming)
        fields = ('name', 'id', 'provider') if change.path[-1] == 'image_host' else ('name', 'id')
        if any(field in item and not isinstance(item[field], (str, int, float, bool))
               for item in items for field in fields):
            raise ConfigSaveError('无法可靠匹配列表项目，配置未保存')
        identity = next((field for field in fields
                         if all(field in item for item in (*existing, *incoming))
                         and len({item[field] for item in existing}) == len(existing)
                         and len({item[field] for item in incoming}) == len(incoming)
                         and {item[field] for item in existing} == {item[field] for item in incoming}), None)
        if identity is not None:
            matches = {item[identity]: item for item in existing}
            result = copy.deepcopy(incoming)
            result.clear()
            for item in incoming:
                merged = copy.deepcopy(matches[item[identity]])
                merged.update(copy.deepcopy(item))
                if isinstance(item, CommentedMap) and item.fa.flow_style():
                    merged.fa.set_flow_style()
                result.append(merged)
            return result
        if change.path[-1] == 'image_host' and len(existing) == len(incoming):
            matched = []
            for item in incoming:
                candidates = [index for index, old in enumerate(existing)
                              if dict(old) == dict(item)]
                if len(candidates) != 1 or candidates[0] in matched:
                    break
                matched.append(candidates[0])
            else:
                result = copy.deepcopy(incoming)
                result.clear()
                for index in matched:
                    result.append(copy.deepcopy(existing[index]))
                return result
        if (len(existing) == len(incoming) == 1
                and not any(field in existing[0] and field in incoming[0]
                            and existing[0][field] != incoming[0][field]
                            for field in ('name', 'id'))):
            merged = copy.deepcopy(existing[0])
            merged.update(copy.deepcopy(incoming[0]))
            result = copy.deepcopy(existing)
            result[0] = merged
            return result
        if existing:
            raise ConfigSaveError('无法可靠匹配列表项目，配置未保存')
        return copy.deepcopy(incoming)

    for change in changes:
        if not change.path or change.path[0] == '_runtime':
            continue
        parent = tree
        for index, key in enumerate(change.path[:-1]):
            if isinstance(parent, CommentedMap) and key not in parent:
                parent[key] = CommentedMap()
            else:
                detach_alias(parent, key, change.path[:index + 1])
            parent = parent[key]
        key = change.path[-1]
        if change.operation == 'delete':
            del parent[key]
        elif change.operation == 'set':
            if isinstance(parent, CommentedMap) and isinstance(parent.get(key), CommentedMap) and isinstance(change.after, dict):
                detach_alias(parent, key, change.path)
                parent[key].update(copy.deepcopy(change.after))
            elif isinstance(parent[key], CommentedSeq) and isinstance(change.after, list):
                detach_alias(parent, key, change.path)
                parent[key] = merge_list_items(parent[key], change.after)
            else:
                parent[key] = copy.deepcopy(change.after)
    return ConfigDocument(document.path, tree, document.fingerprint)


def _fingerprint(path: Path) -> str | None:
    """读取目标当前字节指纹，不存在时返回 None。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _check_save_path(destination: Path) -> None:
    """拒绝目标和其现有祖先中的 Windows 重解析点。"""
    for path in (*reversed(destination.parents), destination):
        try:
            if path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ConfigSaveError('配置保存路径不可经过重解析点')
        except FileNotFoundError:
            continue
        except OSError:
            raise ConfigSaveError('无法检查配置保存路径') from None


def save_document(document: ConfigDocument, destination: Path, *,
                  expected_fingerprint: str | None) -> ConfigDocument:
    """校验目标指纹并在同目录关闭临时文件后原子替换。"""
    if not destination.is_absolute() or not str(destination).endswith(('.yaml', '.yml')):
        raise ConfigSaveError('保存目标必须是已规范化的绝对配置路径')
    _check_save_path(destination)
    try:
        current = _fingerprint(destination)
    except OSError:
        raise ConfigSaveError('无法检查配置目标') from None
    if current != expected_fingerprint:
        raise ConfigConflictError('配置目标已变化，请重新加载或另存为')

    tree = copy.deepcopy(document.tree)
    tree.pop('_runtime', None)
    temp_path = None
    temp_fd = None
    try:
        temp_fd, temp_path = tempfile.mkstemp(
            dir=destination.parent, prefix=f'.{destination.name}.', suffix='.tmp')
        stream = os.fdopen(temp_fd, 'w', encoding='utf-8')
        temp_fd = None
        try:
            _make_write_yaml().dump(tree, stream)
        finally:
            stream.close()
        data = Path(temp_path).read_bytes()
        _check_save_path(destination)
        if _fingerprint(destination) != expected_fingerprint:
            raise ConfigConflictError('配置目标已变化，请重新加载或另存为')
        os.replace(temp_path, destination)
        temp_path = None
    except ConfigConflictError:
        raise
    except Exception:
        raise ConfigSaveError('无法保存配置文档') from None
    finally:
        primary = sys.exception()
        try:
            if temp_fd is not None:
                os.close(temp_fd)
            if temp_path is not None:
                os.unlink(temp_path)
        except OSError:
            if primary is None:
                raise ConfigSaveError('无法清理配置临时文件') from None
    return ConfigDocument(destination, tree, hashlib.sha256(data).hexdigest())
