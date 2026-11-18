#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 打包入口、构建身份与 CI 配对配置测试。"""

import ast
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

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


def _workflow(name):
    """分别解析独立工作流，防止跨文件拼接掩盖职责越界。"""
    return YAML(typ='safe').load((ROOT / '.github/workflows' / name).read_text(
        encoding='utf-8'))


def _runs(job):
    """返回一个 job 内全部命令和 action 参数供契约校验。"""
    return '\n'.join(str(step.get('run', '')) + str(step.get('with', ''))
                     for step in job['steps'])


def test_cli_workflow_keeps_release_and_excludes_gui():
    """CLI 的触发和发布维持独立，不能引入 GUI 依赖或阻断边。"""
    workflow = _workflow('build.yml')
    assert {'push', 'pull_request', 'workflow_dispatch'} <= set(workflow['on'])
    assert {'meta', 'build-nuitka', 'build-pyinstaller', 'release'} == set(workflow['jobs'])
    assert workflow['jobs']['release']['needs'] == [
        'meta', 'build-nuitka', 'build-pyinstaller']
    for job in workflow['jobs'].values():
        assert 'requirements-gui.txt' not in _runs(job)
        assert '2RPM-GUI' not in _runs(job)
        assert 'build_identity' not in _runs(job)
    assert 'pyinstaller 2RPM.spec' in _runs(workflow['jobs']['build-pyinstaller'])
    assert 'softprops/action-gh-release@v3' in str(workflow['jobs']['release'])


def test_gui_workflow_is_manual_artifact_only():
    """GUI 只响应手动触发，不具备发布或跨工作流权限。"""
    workflow = _workflow('build-gui.yml')
    assert set(workflow['on']) == {'workflow_dispatch'}
    assert workflow['permissions'] == {'contents': 'read'}
    assert set(workflow['jobs']) == {'build-nuitka', 'build-pyinstaller'}
    for job in workflow['jobs'].values():
        assert job['runs-on'] == 'windows-latest'
        assert 'needs' not in job
        steps = job['steps']
        assert any('actions/checkout@' in step.get('uses', '') for step in steps)
        assert any('pip install -r requirements-gui.txt' in step.get('run', '')
                   for step in steps)
        assert sum('actions/upload-artifact@' in step.get('uses', '')
                   for step in steps) == 1
        text = _runs(job)
        assert '2RPM.spec' not in text
        assert 'release/CHANGELOG.md' not in text
        assert 'download-artifact' not in str(job)
        assert 'action-gh-release' not in str(job)
        assert 'PIL._imagingtk' not in text
        assert '*.tkinter' not in text
        assert 'git rev-parse HEAD' in text
        assert 'sha256' in text.lower()
        assert 'release_names(' in text
        assert 'write_identity(' in text
        assert 'manifest.json' in text
        assert "source_sha = '${{ steps.version.outputs.source_sha }}'" in text
        assert 'build_tool_version = $toolVersion' in text
        assert 'gui_file = $name' in text
        assert "pairing_status = '未完成配对验收'" in text
        assert 'Get-FileHash "package/$name" -Algorithm SHA256' in text
        assert 'refs/tags/v*' in text
        assert 'git describe --tags --match "v*" HEAD' in text


@pytest.mark.parametrize('job_name', ('build-nuitka', 'build-pyinstaller'))
def test_gui_workflow_without_v_tag_uses_nonempty_fallback(tmp_path, job_name):
    """两个 GUI job 在无匹配 v 标签时均写出非空的提交版本。"""
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                    'commit', '-q', '--allow-empty', '-m', 'initial'],
                   cwd=tmp_path, check=True)
    short_sha = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'],
                                        cwd=tmp_path, text=True).strip()
    workflow = _workflow('build-gui.yml')
    step = next(step for step in workflow['jobs'][job_name]['steps']
                if step.get('id') == 'version')
    output = tmp_path / 'github_output'
    env = {**os.environ, 'GITHUB_REF': 'refs/heads/main',
           'GITHUB_REF_NAME': 'main', 'GITHUB_OUTPUT': str(output)}
    bash = Path(shutil.which('git')).resolve().parents[1] / 'bin' / 'bash.exe'
    result = subprocess.run([str(bash), '-e', '-c', step['run']], cwd=tmp_path,
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert f'tag=v0.0.1-build.{short_sha}' in output.read_text(encoding='utf-8')


@pytest.mark.parametrize('tool,command', (
    ('Nuitka', '2RPM_gui.py'),
    ('PyInstaller', 'pyinstaller 2RPM-GUI.spec --noconfirm'),
))
def test_gui_workflow_uploads_each_tool_with_manifest(tool, command):
    """每种构建工具仅上传自己的可执行文件和可校验的身份清单。"""
    workflow = _workflow('build-gui.yml')
    job = workflow['jobs'][f'build-{tool.lower()}']
    text = _runs(job)
    assert command in text
    assert f"'{tool}'" in text
    uploads = [step for step in job['steps']
               if step.get('uses', '').startswith('actions/upload-artifact@')]
    assert len(uploads) == 1
    assert f'2RPM-GUI-{tool}-' in uploads[0]['with']['name']
    assert 'manifest.json' in uploads[0]['with']['path']
    assert '.exe' in uploads[0]['with']['path']
    assert uploads[0]['with']['if-no-files-found'] == 'error'


def test_unit_workflow_checks_build_contract_without_gui_build_dependency():
    """静态构建契约由原单元测试执行，不依赖 GUI 构建。"""
    workflow = _workflow('unit_test.yml')
    assert 'tests/test_gui_build_config.py' in _runs(workflow['jobs']['test'])
    assert 'requirements-gui.txt' in _runs(workflow['jobs']['test'])
    assert 'needs' not in workflow['jobs']['test']
