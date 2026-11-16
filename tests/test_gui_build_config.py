#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 打包入口、构建身份与 CI 配对配置测试。"""

import ast
from pathlib import Path

import pytest

from modules.gui.build_identity import (release_names, write_identity)
from modules.gui.launcher import CliLocationError, resolve_cli_command
from modules.version import VERSION

ROOT = Path(__file__).resolve().parents[1]
GUI_SPEC = '2RPM-GUI.spec'
CLI_SPEC = '2RPM.spec'


def _parse(relative):
    """读取仓库文件并解析为 AST，避免依赖 PyInstaller 运行时。"""
    return ast.parse((ROOT / relative).read_text(encoding='utf-8'))


def _literal_names(node):
    """收集表达式中的全部字符串字面量。"""
    return {item.value for item in ast.walk(node)
            if isinstance(item, ast.Constant) and isinstance(item.value, str)}


def _analysis_keyword(relative, keyword):
    """取出 spec 中 Analysis 调用的指定关键字表达式。"""
    for node in ast.walk(_parse(relative)):
        if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'Analysis':
            for item in node.keywords:
                if item.arg == keyword:
                    return item.value
    raise AssertionError(f'{relative} 缺少 Analysis.{keyword}')


def _exe_keyword(relative, keyword):
    """取出 spec 中 EXE 调用的指定关键字表达式。"""
    for node in ast.walk(_parse(relative)):
        if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'EXE':
            for item in node.keywords:
                if item.arg == keyword:
                    return item.value
    raise AssertionError(f'{relative} 缺少 EXE.{keyword}')


def _collect_all_packages(relative):
    """收集 spec 调用 collect_all 时传入的包名。"""
    packages = set()
    for node in ast.walk(_parse(relative)):
        if (isinstance(node, ast.Call)
                and getattr(node.func, 'id', None) == 'collect_all' and node.args):
            argument = node.args[0]
            if isinstance(argument, ast.Constant):
                packages.add(argument.value)
    return packages


def test_gui_bundle_includes_tk_and_ctk_resources():
    """GUI spec 收集 Tk/Tcl、CustomTkinter 与 ctk-kit 资源和二进制。"""
    spec = _parse(GUI_SPEC)
    assert '2RPM_gui.py' in _literal_names(spec)
    assert {'tkinter', 'customtkinter', 'ctk_kit'} <= _collect_all_packages(GUI_SPEC)

    datas = ast.unparse(_analysis_keyword(GUI_SPEC, 'datas'))
    binaries = ast.unparse(_analysis_keyword(GUI_SPEC, 'binaries'))
    hiddenimports = ast.unparse(_analysis_keyword(GUI_SPEC, 'hiddenimports'))
    for package in ('tkinter', 'customtkinter', 'ctk_kit'):
        assert f'{package}_datas' in datas
        assert f'{package}_binaries' in binaries
        assert f'{package}_hidden' in hiddenimports
    assert {'_tcl_data', '_tk_data'} <= _literal_names(spec)

    excludes = _literal_names(_analysis_keyword(GUI_SPEC, 'excludes'))
    assert not {'tkinter', 'customtkinter', 'ctk_kit'} & excludes

    assert _literal_names(_exe_keyword(GUI_SPEC, 'name')) == {'2RPM-GUI'}
    assert _exe_keyword(GUI_SPEC, 'console').value is False


def test_cli_spec_still_excludes_tk():
    """CLI spec 保持控制台入口并继续排除 Tk 与 GUI 依赖。"""
    excludes = _literal_names(_analysis_keyword(CLI_SPEC, 'excludes'))
    assert 'tkinter' in excludes
    assert not {'customtkinter', 'ctk_kit'} & _literal_names(_parse(CLI_SPEC))
    assert _literal_names(_exe_keyword(CLI_SPEC, 'name')) == {'2RPM'}
    assert _exe_keyword(CLI_SPEC, 'console').value is True


@pytest.mark.parametrize('tool', ('Nuitka', 'PyInstaller'))
@pytest.mark.parametrize('tag', ('v4.0.0', 'v4.0.0-rc.1', 'v4.0.0-build.abc'))
def test_gui_cli_pair_identity_matches(tmp_path, tool, tag):
    """发布命名按同工具、同完整版本生成可定位的 GUI/CLI 配对。"""
    gui_name, cli_name = release_names(tool, tag)
    assert gui_name == f'2RPM-GUI-{tool}-{tag}.exe'
    assert cli_name == f'2RPM-{tool}-{tag}.exe'
    (tmp_path / gui_name).touch()
    (tmp_path / cli_name).touch()
    assert resolve_cli_command(executable=tmp_path / gui_name, frozen=True,
                               project_dir=tmp_path, build_tool=tool,
                               build_tag=tag) == (str(tmp_path / cli_name),)


def test_gui_pair_requires_matching_cli(tmp_path):
    """GUI 冻结名存在但配对 CLI 缺失或工具不符时不得退化。"""
    gui_name, _ = release_names('Nuitka', 'v4.0.0')
    (tmp_path / gui_name).touch()
    (tmp_path / '2RPM-PyInstaller-v4.0.0.exe').touch()
    with pytest.raises(CliLocationError):
        resolve_cli_command(executable=tmp_path / gui_name, frozen=True,
                            project_dir=tmp_path, build_tool='Nuitka',
                            build_tag='v4.0.0')


@pytest.mark.parametrize('tool', ('Nuitka', 'PyInstaller'))
def test_build_identity_written_for_frozen_tool(tmp_path, tool):
    """构建流程写入的工具与完整版本可被身份模块直接读取。"""
    target = tmp_path / 'build_identity.py'
    write_identity(target, tool, 'v4.0.0-build.abc')
    scope = {}
    exec(compile(target.read_text(encoding='utf-8'), str(target), 'exec'), scope)
    assert (scope['BUILD_TOOL'], scope['BUILD_TAG']) == (tool, 'v4.0.0-build.abc')
    assert scope['VERSION'] == VERSION


def test_worker_dispatch_precedes_ui_import():
    """入口先按 worker 参数分流，任何 UI 导入与窗口初始化都在其后。"""
    tree = _parse('2RPM_gui.py')
    top_level = [node for node in tree.body
                 if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert top_level == [node for node in top_level
                         if not _imports_ui(node)]

    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == 'main')
    dispatch = next(node for node in ast.walk(main)
                    if isinstance(node, ast.If)
                    and '--internal-gui-worker' in _literal_names(node.test))
    worker_imports = [node for node in ast.walk(dispatch.body[0])
                      if isinstance(node, ast.ImportFrom)
                      and node.module == 'modules.gui.worker']
    ui_imports = [node for node in ast.walk(main)
                  if isinstance(node, (ast.Import, ast.ImportFrom))
                  and _imports_ui(node)]
    assert worker_imports
    assert ui_imports
    assert all(node.lineno > dispatch.lineno for node in ui_imports)
    dispatch_nodes = {id(node) for node in ast.walk(dispatch)}
    assert not any(id(node) in dispatch_nodes for node in ui_imports)


def _imports_ui(node):
    """判断导入语句是否触发 Tk 或 ctk-kit 的界面导入。"""
    if isinstance(node, ast.Import):
        return any(alias.name.split('.')[0] in ('tkinter', 'ctk_kit', 'customtkinter')
                   for alias in node.names)
    module = node.module or ''
    return module.split('.')[0] in ('tkinter', 'ctk_kit', 'customtkinter')

