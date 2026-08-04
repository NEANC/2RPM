#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""community-scripts Style GUI 独立复用模块测试。"""

import ast
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STYLE_GUI_DIR = PROJECT_ROOT / 'community-scripts Style GUI'
SPINNER_FILE = STYLE_GUI_DIR / 'spinner.py'
BANNER_FILE = STYLE_GUI_DIR / 'banner.py'
README_FILE = STYLE_GUI_DIR / 'README.md'


def test_modules_can_be_copied_next_to_entrypoint_and_imported(tmp_path):
    """调用方把模块复制到入口同级后，应无需修改 sys.path 即可导入。"""
    shutil.copy2(SPINNER_FILE, tmp_path / 'spinner.py')
    shutil.copy2(BANNER_FILE, tmp_path / 'banner.py')
    entrypoint = tmp_path / 'main.py'
    entrypoint.write_text(
        textwrap.dedent(
            """
            from spinner import spinner_phase, notify_fail
            from banner import print_info

            print_info(app_name='Demo', subtitle='Reusable UI', version='v1.2.3', license_name='MIT', banner_art='DEMO')
            with spinner_phase('初始化...') as sp:
                sp.write_done('准备完成')
                sp.done('结束')
            print('import ok')
            """
        ),
        encoding='utf-8',
    )

    result = subprocess.run(
        [sys.executable, str(entrypoint)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert 'import ok' in result.stdout
    assert 'v1.2.3' in result.stdout or 'v1.2.3' in result.stderr


def _import_names(path):
    """返回 Python 文件内直接 import 的顶层模块名。"""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split('.')[0])
    return names


def test_independent_modules_do_not_import_2rpm_internal_modules():
    """独立模块不得依赖 modules.* 等 2RPM 内部模块。"""
    for path in (SPINNER_FILE, BANNER_FILE):
        source = path.read_text(encoding='utf-8')
        assert 'from modules' not in source
        assert 'import modules' not in source
