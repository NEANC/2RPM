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


def _load_module_from_path(module_name, path):
    """按文件路径加载独立模块，避免依赖包名。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_banner_print_info_outputs_all_custom_fields(capsys):
    """banner 应输出所有非空自定义字段。"""
    banner = _load_module_from_path('style_gui_banner_custom', BANNER_FILE)

    banner.print_info(
        app_name='Demo App',
        subtitle='Reusable Terminal UI',
        version='v1.2.3',
        license_name='MIT',
        banner_art='DEMO ART',
    )

    output = capsys.readouterr().out
    assert 'DEMO ART' in output
    assert 'Demo App' in output
    assert 'Reusable Terminal UI' in output
    assert 'Version: v1.2.3' in output
    assert 'License: MIT' in output


def test_banner_print_info_omits_empty_fields(capsys):
    """banner 参数为空时应省略对应输出字段。"""
    banner = _load_module_from_path('style_gui_banner_empty', BANNER_FILE)

    banner.print_info(
        app_name='',
        subtitle='',
        version='',
        license_name='',
        banner_art='',
    )

    output = capsys.readouterr().out
    assert 'Application' not in output
    assert 'Terminal Application' not in output
    assert 'Version:' not in output
    assert 'License:' not in output


def test_banner_divider_uses_dynamic_minimum_width(capsys):
    """分隔线宽度应根据可见文本计算，且至少 32 个字符。"""
    banner = _load_module_from_path('style_gui_banner_divider', BANNER_FILE)

    banner.print_info(
        app_name='Short',
        subtitle='A much longer reusable subtitle',
        version='v9.9.9',
        license_name='Apache-2.0',
        banner_art='',
    )

    output = capsys.readouterr().out
    divider_lines = [line for line in output.splitlines() if '─' in line]
    assert len(divider_lines) == 1
    divider_width = divider_lines[0].count('─')
    assert divider_width >= 32
    assert divider_width >= len('A much longer reusable subtitle')
