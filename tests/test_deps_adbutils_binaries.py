#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""校验已安装的 adbutils 不携带 ADB 可执行工具，避免混入发布产物。"""

from pathlib import Path

import adbutils


ADB_BINARY_NAMES = frozenset({
    'adb',
    'adb.exe',
    'adbwinapi.dll',
    'adbwinusbapi.dll',
})


def _adb_binary_files(package_dir):
    """列出包目录内文件名命中已知 ADB 工具名的文件。

    Args:
        package_dir: 已安装 adbutils 包的目录。

    Returns:
        list: 相对包目录的文件名列表，按名称排序。
    """
    return sorted(
        str(path.relative_to(package_dir))
        for path in package_dir.rglob('*')
        if path.is_file() and path.name.lower() in ADB_BINARY_NAMES
    )


def test_installed_adbutils_ships_no_adb_binary():
    """源码安装的 adbutils 包目录内不得出现已知 ADB 可执行工具。"""
    package_dir = Path(adbutils.__file__).resolve().parent
    assert package_dir.is_dir()
    assert _adb_binary_files(package_dir) == []
