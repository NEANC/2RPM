#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""无交互的 YAML 配置文档读取、局部补丁与冲突保护保存。"""

import copy
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

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
    for change in changes:
        if not change.path or change.path[0] == '_runtime':
            continue
        parent = tree
        for key in change.path[:-1]:
            if key not in parent:
                parent[key] = CommentedMap()
            parent = parent[key]
        key = change.path[-1]
        if change.operation == 'delete':
            del parent[key]
        elif change.operation == 'set':
            parent[key] = copy.deepcopy(change.after)
    return ConfigDocument(document.path, tree, document.fingerprint)


def _fingerprint(path: Path) -> str | None:
    """读取目标当前字节指纹，不存在时返回 None。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def save_document(document: ConfigDocument, destination: Path, *,
                  expected_fingerprint: str | None) -> ConfigDocument:
    """校验目标指纹并在同目录关闭临时文件后原子替换。"""
    if not destination.is_absolute() or not str(destination).endswith(('.yaml', '.yml')):
        raise ConfigSaveError('保存目标必须是已规范化的绝对配置路径')
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
        os.replace(temp_path, destination)
        temp_path = None
    except Exception:
        raise ConfigSaveError('无法保存配置文档') from None
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        if temp_path is not None:
            os.unlink(temp_path)
    return ConfigDocument(destination, tree, hashlib.sha256(data).hexdigest())
