#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 与 CLI 精确配对及独立控制台启动测试。"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from modules.gui.build_identity import BUILD_TAG, BUILD_TOOL
from modules.gui.launcher import (CliLocationError, launch_cli,
                                  resolve_cli_command)
from modules.version import VERSION


def _resolve(folder, name, tool, tag):
    """以临时冻结入口模拟定位，不实际执行文件。"""
    return resolve_cli_command(executable=folder / name, frozen=True,
                               project_dir=folder, build_tool=tool,
                               build_tag=tag)


@pytest.mark.parametrize('tool', ('Nuitka', 'PyInstaller'))
@pytest.mark.parametrize('tag', ('v4.0.0', 'v4.0.0-rc.1',
                                 'v4.0.0-build.abc'))
def test_exact_release_pair(tmp_path, tool, tag):
    """仅选同工具、同完整版本的同层 CLI。"""
    folder = tmp_path / '发布 中文 & ! % ^'
    folder.mkdir()
    (folder / f'2RPM-GUI-{tool}-{tag}.exe').touch()
    cli = folder / f'2RPM-{tool}-{tag}.exe'
    cli.touch()
    (folder / '2RPM.exe').touch()
    assert _resolve(folder, f'2RPM-GUI-{tool}-{tag}.exe', tool, tag) == (str(cli),)


@pytest.mark.parametrize('tool', ('Nuitka', 'PyInstaller'))
def test_no_fallback_to_other_tool_or_version(tmp_path, tool):
    """精确项缺失时不退化到其它版本、工具或旧本地名。"""
    other = 'PyInstaller' if tool == 'Nuitka' else 'Nuitka'
    for name in ('2RPM.exe', f'2RPM-{other}-v4.0.0-rc.1.exe',
                 f'2RPM-{tool}-v4.0.0.exe',
                 f'2RPM-{tool}-v4.0.0-rc.1-build.abc.exe'):
        (tmp_path / name).touch()
    with pytest.raises(CliLocationError):
        _resolve(tmp_path, f'2RPM-GUI-{tool}-v4.0.0-rc.1.exe',
                 tool, 'v4.0.0-rc.1')


@pytest.mark.parametrize('name,tool,tag', [
    ('2RPM-GUI-Nuitka-v4.0.0.exe', 'PyInstaller', 'v4.0.0'),
    ('2RPM-GUI-Nuitka-v4.0.0-rc.1.exe', 'Nuitka', 'v4.0.0'),
    ('renamed.exe', 'Nuitka', 'v4.0.0'),
    ('2RPM-GUI.exe', 'source', 'v4.0.0'),
])
def test_frozen_identity_mismatch_rejected(tmp_path, name, tool, tag):
    """冻结程序名与嵌入身份不符时不查找 CLI。"""
    (tmp_path / '2RPM.exe').touch()
    (tmp_path / '2RPM-Nuitka-v4.0.0.exe').touch()
    with pytest.raises(CliLocationError):
        _resolve(tmp_path, name, tool, tag)


@pytest.mark.parametrize('tool', ('Nuitka', 'PyInstaller'))
def test_local_pair_only_uses_sibling_cli(tmp_path, tool):
    """已知本地构建名配对同目录的原名 CLI。"""
    cli = tmp_path / '2RPM.exe'
    cli.touch()
    assert _resolve(tmp_path, '2RPM-GUI.exe', tool, 'v4.0.0') == (str(cli),)
    cli.unlink()
    (tmp_path / f'2RPM-{tool}-v4.0.0.exe').touch()
    with pytest.raises(CliLocationError):
        _resolve(tmp_path, '2RPM-GUI.exe', tool, 'v4.0.0')


def test_pythonw_uses_sibling_python(tmp_path):
    """pythonw 启动时选同虚拟环境控制台解释器而非 GUI 本身。"""
    scripts = tmp_path / 'venv 中文' / 'Scripts'
    scripts.mkdir(parents=True)
    python = scripts / 'python.exe'
    python.touch()
    project = tmp_path / '项目 & ! % ^'
    project.mkdir()
    entry = project / '2RPM.py'
    entry.touch()
    command = resolve_cli_command(executable=scripts / 'pythonw.exe',
                                  frozen=False, project_dir=project,
                                  build_tool='source', build_tag=VERSION)
    assert command == (str(python), str(entry))


def test_source_rejects_non_python_interpreter(tmp_path):
    """源码模式不借任意可执行文件启动 CLI。"""
    (tmp_path / '2RPM.py').touch()
    (tmp_path / 'renamed.exe').touch()
    with pytest.raises(CliLocationError):
        resolve_cli_command(executable=tmp_path / 'renamed.exe', frozen=False,
                            project_dir=tmp_path, build_tool='source',
                            build_tag=VERSION)


@pytest.mark.parametrize('missing', ('python', 'entry'))
def test_source_missing_files_rejected(tmp_path, missing):
    """源码入口及同环境解释器缺失时不回退 PATH。"""
    python = tmp_path / 'python.exe'
    entry = tmp_path / '2RPM.py'
    python.touch()
    entry.touch()
    (python if missing == 'python' else entry).unlink()
    with pytest.raises(CliLocationError):
        resolve_cli_command(executable=tmp_path / 'pythonw.exe', frozen=False,
                            project_dir=tmp_path, build_tool='source',
                            build_tag=VERSION)


def test_source_build_identity_uses_version():
    """源码身份固定为 source 且版本来自现有版本模块。"""
    assert (BUILD_TOOL, BUILD_TAG) == ('source', VERSION)


def test_launch_never_manages_cli(monkeypatch, tmp_path):
    """只创建新控制台，不读取终端输出或管理子进程生命周期。"""
    import modules.gui.launcher as launcher

    process = MagicMock()
    for method in ('poll', 'wait', 'terminate', 'kill', 'communicate'):
        getattr(process, method).side_effect = AssertionError(method)
    popen = MagicMock(return_value=process)
    monkeypatch.setattr(launcher.subprocess, 'Popen', popen)
    config_path = tmp_path / '配置 中文 & ! % ^.yaml'
    result = launch_cli((str(tmp_path / '2RPM.exe'),), config_path)
    args, kwargs = popen.call_args
    assert args[0] == [str(tmp_path / '2RPM.exe'), '-c',
                       str(config_path.resolve())]
    assert kwargs['shell'] is False
    assert kwargs['cwd'] == str((tmp_path / '2RPM.exe').parent.resolve())
    assert kwargs['creationflags'] == subprocess.CREATE_NEW_CONSOLE
    assert all(kwargs.get(k) is None for k in ('stdin', 'stdout', 'stderr'))
    assert kwargs['close_fds'] is True
    assert result.created is True
    assert result.message == '已创建 CLI 进程'
    assert 'TOKEN' not in repr(args)
    for method in ('poll', 'wait', 'terminate', 'kill', 'communicate'):
        getattr(process, method).assert_not_called()


def test_launch_failure_is_safe(monkeypatch, tmp_path):
    """进程创建失败只返回安全结果而不包含系统异常的敏感信息。"""
    import modules.gui.launcher as launcher

    def fail(*_args, **_kwargs):
        """模拟包含私密路径的创建失败。"""
        raise OSError('TOKEN=private-value')

    monkeypatch.setattr(launcher.subprocess, 'Popen', fail)
    result = launch_cli((str(tmp_path / '2RPM.exe'),), Path('config.yaml'))
    assert result.created is False
    assert 'TOKEN' not in result.message
    assert 'private-value' not in result.message
