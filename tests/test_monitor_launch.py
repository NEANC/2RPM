#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件启动模块的组件测试。

全程使用替身：不启动真实进程、不读取真实配置、不触碰计划任务与网络。
重点锁定 G1 实测得到的单字符串命令行构造规则、解释器回退链、
启动失败不重试，以及身份必须来自 psutil 而不是 Popen 实例。
"""

import logging
import ntpath
import os
import sys
from unittest.mock import patch

import psutil
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.monitor_target import MonitorTarget

PWSH = r'C:\PS\pwsh.exe'
POWERSHELL = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
PYTHON = r'C:\Py\python.exe'
PY_LAUNCHER = r'C:\Py\py.exe'


class _FakePopen:
    """只暴露真实 Popen 的 pid/poll/wait，刻意不提供 create_time。"""

    def __init__(self, pid=4321):
        """初始化替身。

        Args:
            pid (int): 返回给调用方的进程号
        """
        self.pid = pid
        self.poll_calls = 0

    def poll(self):
        """模拟存活检查。

        Returns:
            None: 表示进程仍在运行
        """
        self.poll_calls += 1
        return None

    def wait(self, timeout=None):
        """模拟等待进程结束。

        Args:
            timeout (float | None): 等待秒数

        Returns:
            int: 退出码
        """
        return 0


class _FakePsutilProcess:
    """伪造 psutil.Process，可按用例抛出权限或不存在异常。"""

    def __init__(self, name='real.exe', create_time=1700000000.5,
                 create_time_error=None, name_error=None):
        """初始化替身。

        Args:
            name (str): 进程名
            create_time (float): 进程创建时间
            create_time_error (Exception | None): create_time 调用时抛出的异常
            name_error (Exception | None): name 调用时抛出的异常
        """
        self.pid = None
        self._name = name
        self._create_time = create_time
        self._create_time_error = create_time_error
        self._name_error = name_error

    def create_time(self):
        """返回创建时间，必要时抛出异常。

        Returns:
            float: 创建时间戳

        Raises:
            Exception: 构造时指定的异常
        """
        if self._create_time_error is not None:
            raise self._create_time_error
        return self._create_time

    def name(self):
        """返回进程名，必要时抛出异常。

        Returns:
            str: 进程名

        Raises:
            Exception: 构造时指定的异常
        """
        if self._name_error is not None:
            raise self._name_error
        return self._name


def _file_target(value, cwd=None):
    """构造文件类监视目标。

    Args:
        value (str): 目标文件绝对路径
        cwd (str | None): 显式工作目录

    Returns:
        MonitorTarget: 文件目标
    """
    return MonitorTarget(kind='file', value=value, cwd=cwd)


def _which_factory(mapping):
    """按解释器名生成 shutil.which 替身。

    Args:
        mapping (dict): 解释器名到返回路径（或 None）的映射

    Returns:
        callable: 与 shutil.which 同签名的替身
    """
    def fake_which(name):
        """查找解释器路径。

        Args:
            name (str): 解释器名

        Returns:
            str | None: 命中的路径
        """
        return mapping.get(name)

    return fake_which


@pytest.mark.parametrize('target,args,expected_executable,expected_command_line', [
    (
        _file_target(r'C:\prog\a.exe'),
        '--foo bar',
        r'C:\prog\a.exe',
        r'"C:\prog\a.exe" --foo bar',
    ),
    (
        _file_target(r'C:\prog\A.EXE'),
        None,
        r'C:\prog\A.EXE',
        r'"C:\prog\A.EXE"',
    ),
    (
        _file_target(r'C:\prog\run.bat'),
        '--x 1',
        'cmd.exe',
        r'cmd.exe /c ""C:\prog\run.bat" --x 1"',
    ),
    (
        _file_target(r'C:\prog\Run.Bat'),
        None,
        'cmd.exe',
        r'cmd.exe /c ""C:\prog\Run.Bat""',
    ),
    (
        _file_target(r'C:\prog\run.cmd'),
        '--y',
        'cmd.exe',
        r'cmd.exe /c ""C:\prog\run.cmd" --y"',
    ),
    (
        _file_target(r'C:\prog\A.CMD'),
        None,
        'cmd.exe',
        r'cmd.exe /c ""C:\prog\A.CMD""',
    ),
    (
        _file_target(r'C:\prog\a.ps1'),
        r'--path "D:\x y.txt"',
        PWSH,
        r'"C:\PS\pwsh.exe" -NoProfile -Command "& ""C:\prog\a.ps1"" '
        r'--path ""D:\x y.txt"""',
    ),
    (
        _file_target(r'C:\prog\a.PS1'),
        None,
        PWSH,
        r'"C:\PS\pwsh.exe" -NoProfile -Command "& ""C:\prog\a.PS1"""',
    ),
    (
        _file_target(r'C:\prog\s.py'),
        '--v 1',
        PYTHON,
        r'"C:\Py\python.exe" "C:\prog\s.py" --v 1',
    ),
    (
        _file_target(r'C:\prog\S.PY'),
        None,
        PYTHON,
        r'"C:\Py\python.exe" "C:\prog\S.PY"',
    ),
])
def test_command_line_per_extension(
        target, args, expected_executable, expected_command_line):
    """各扩展名的可执行文件与命令行必须精确匹配 G1 规则。"""
    from modules.monitor_launch import build_launch_command

    which = _which_factory({'pwsh.exe': PWSH, 'python.exe': PYTHON})
    with patch('modules.monitor_launch.shutil.which', side_effect=which):
        command = build_launch_command(target, args)

    assert command.executable == expected_executable
    assert command.command_line == expected_command_line


@pytest.mark.parametrize('args', [None, '', '   '])
def test_empty_args_no_trailing_space(args):
    """空参数不得产生尾随空格、None 或 null 文本。"""
    from modules.monitor_launch import build_launch_command

    which = _which_factory({'pwsh.exe': PWSH, 'python.exe': PYTHON})
    with patch('modules.monitor_launch.shutil.which', side_effect=which):
        exe_command = build_launch_command(_file_target(r'C:\prog\a.exe'), args)
        ps1_command = build_launch_command(_file_target(r'C:\prog\a.ps1'), args)

    for command in (exe_command, ps1_command):
        assert not command.command_line.endswith(' ')
        assert 'None' not in command.command_line
        assert 'null' not in command.command_line


def test_ps1_falls_back_to_windows_powershell():
    """pwsh 缺失时应回退到 powershell.exe。"""
    from modules.monitor_launch import build_launch_command

    which = _which_factory({'powershell.exe': POWERSHELL})
    with patch('modules.monitor_launch.shutil.which', side_effect=which):
        command = build_launch_command(_file_target(r'C:\prog\a.ps1'), '--x')

    assert command.executable == POWERSHELL
    assert command.command_line == (
        r'"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"'
        r' -NoProfile -Command "& ""C:\prog\a.ps1"" --x"'
    )


@pytest.mark.parametrize('environ,expected_root', [
    ({'SystemRoot': r'D:\Windows'}, r'D:\Windows'),
    ({}, r'C:\Windows'),
])
def test_ps1_system_candidate_uses_systemroot(environ, expected_root):
    """第三级候选应基于 SystemRoot 拼装，缺失时退回 C:\\Windows。"""
    from modules.monitor_launch import build_launch_command

    expected_path = ntpath.join(
        expected_root, r'System32\WindowsPowerShell\v1.0\powershell.exe')
    which = _which_factory({})
    with patch('modules.monitor_launch.shutil.which', side_effect=which):
        with patch.dict('modules.monitor_launch.os.environ', environ,
                        clear=True):
            with patch('modules.monitor_launch.os.path.isfile',
                       side_effect=lambda path: path == expected_path):
                command = build_launch_command(
                    _file_target(r'C:\prog\a.ps1'), None)

    assert command.executable == expected_path


def test_py_falls_back_to_py_launcher():
    """python.exe 缺失时应回退到 py.exe 并附加 -3。"""
    from modules.monitor_launch import build_launch_command

    which = _which_factory({'py.exe': PY_LAUNCHER})
    with patch('modules.monitor_launch.shutil.which', side_effect=which):
        command = build_launch_command(_file_target(r'C:\prog\s.py'), '--v 1')

    assert command.executable == PY_LAUNCHER
    assert command.command_line == (
        r'"C:\Py\py.exe" -3 "C:\prog\s.py" --v 1'
    )


def test_missing_interpreter_raises_without_popen():
    """解释器全部缺失时启动应报错且不得调用 Popen。"""
    from modules.monitor_launch import launch_file

    which = _which_factory({})
    with patch('modules.monitor_launch.shutil.which', side_effect=which):
        with patch('modules.monitor_launch.subprocess.Popen') as popen:
            with pytest.raises(RuntimeError):
                launch_file(_file_target(r'C:\prog\s.py'))

    assert popen.call_count == 0


def test_launch_failure_does_not_retry():
    """启动抛 OSError 时应直接失败，不换解释器重试。"""
    from modules.monitor_launch import launch_file

    which = _which_factory({'python.exe': PYTHON})
    with patch('modules.monitor_launch.shutil.which', side_effect=which):
        with patch('modules.monitor_launch.subprocess.Popen',
                   side_effect=OSError('拒绝访问')) as popen:
            with pytest.raises(RuntimeError):
                launch_file(_file_target(r'C:\prog\s.py'), '--v 1')

    assert popen.call_count == 1


def test_popen_without_create_time():
    """Popen 替身无 create_time 时仍应正常返回 psutil 身份。"""
    from modules.monitor_launch import launch_file

    popen_stub = _FakePopen(pid=999)
    fake_process = _FakePsutilProcess(name='a.exe', create_time=1234.5)
    which = _which_factory({'python.exe': PYTHON})
    with patch('modules.monitor_launch.shutil.which', side_effect=which):
        with patch('modules.monitor_launch.subprocess.Popen',
                   return_value=popen_stub):
            with patch('modules.monitor_launch.psutil.Process',
                       return_value=fake_process) as process_factory:
                info = launch_file(_file_target(r'C:\prog\a.exe'), '--foo')

    assert not hasattr(popen_stub, 'create_time')
    process_factory.assert_called_once_with(999)
    assert info['pid'] == 999
    assert info['name'] == 'a.exe'
    assert info['create_time'] == 1234.5
    assert info['timeout_count'] == 0
    assert info['start_time'] == info['last_warning_time']


def test_fast_exit_reports_immediately():
    """进程启动后立即消失应报“立即退出”。"""
    from modules.monitor_launch import launch_file

    fake_process = _FakePsutilProcess(
        create_time_error=psutil.NoSuchProcess(pid=999))
    which = _which_factory({'python.exe': PYTHON})
    with patch('modules.monitor_launch.shutil.which', side_effect=which):
        with patch('modules.monitor_launch.subprocess.Popen',
                   return_value=_FakePopen(pid=999)):
            with patch('modules.monitor_launch.psutil.Process',
                       return_value=fake_process):
                with pytest.raises(RuntimeError) as error:
                    launch_file(_file_target(r'C:\prog\a.exe'))

    assert '立即退出' in str(error.value)


def test_psutil_construction_failure_reports_immediately():
    """psutil.Process 构造即抛 NoSuchProcess 时应映射为“立即退出”。"""
    from modules.monitor_launch import launch_file

    which = _which_factory({'python.exe': PYTHON})
    with patch('modules.monitor_launch.shutil.which', side_effect=which):
        with patch('modules.monitor_launch.subprocess.Popen',
                   return_value=_FakePopen(pid=999)):
            with patch('modules.monitor_launch.psutil.Process',
                       side_effect=psutil.NoSuchProcess(pid=999)):
                with pytest.raises(RuntimeError) as error:
                    launch_file(_file_target(r'C:\prog\a.exe'))

    assert '立即退出' in str(error.value)


def test_access_denied_name_falls_back_to_executable_basename():
    """读取进程名权限不足时应回退到可执行文件名。"""
    from modules.monitor_launch import launch_file

    fake_process = _FakePsutilProcess(
        name_error=psutil.AccessDenied(pid=999))
    which = _which_factory({'python.exe': PYTHON})
    with patch('modules.monitor_launch.shutil.which', side_effect=which):
        with patch('modules.monitor_launch.subprocess.Popen',
                   return_value=_FakePopen(pid=999)):
            with patch('modules.monitor_launch.psutil.Process',
                       return_value=fake_process):
                info = launch_file(_file_target(r'C:\prog\a.exe'))

    assert info['name'] == 'a.exe'


def test_args_secret_not_logged(caplog):
    """成功与失败路径都不得把 args 写入日志。"""
    from modules.monitor_launch import launch_file

    secret = 'ARG_SECRET_123'
    which = _which_factory({'python.exe': PYTHON})
    with caplog.at_level(logging.DEBUG):
        with patch('modules.monitor_launch.shutil.which', side_effect=which):
            with patch('modules.monitor_launch.subprocess.Popen',
                       return_value=_FakePopen(pid=999)):
                with patch('modules.monitor_launch.psutil.Process',
                           return_value=_FakePsutilProcess()):
                    launch_file(_file_target(r'C:\prog\a.exe'), secret)

        with patch('modules.monitor_launch.shutil.which', side_effect=which):
            with patch('modules.monitor_launch.subprocess.Popen',
                       side_effect=OSError('拒绝访问')):
                with pytest.raises(RuntimeError):
                    launch_file(_file_target(r'C:\prog\a.exe'), secret)

    assert caplog.text
    assert secret not in caplog.text


@pytest.mark.parametrize('kind,value', [
    ('process', 'notepad'),
    ('task', r'\MyTask'),
])
def test_non_file_target_rejected(kind, value):
    """非文件目标必须以 ValueError 拒绝。"""
    from modules.monitor_launch import build_launch_command

    with pytest.raises(ValueError):
        build_launch_command(MonitorTarget(kind=kind, value=value, cwd=None))


def test_cwd_defaults_to_target_directory():
    """未指定 cwd 时应取目标文件所在目录。"""
    from modules.monitor_launch import build_launch_command

    which = _which_factory({'python.exe': PYTHON})
    with patch('modules.monitor_launch.shutil.which', side_effect=which):
        command = build_launch_command(
            _file_target(r'C:\prog\sub\a.exe'), None)

    assert command.cwd == r'C:\prog\sub'


def test_cwd_prefers_target_cwd():
    """指定 cwd 时应原样使用目标的工作目录。"""
    from modules.monitor_launch import build_launch_command

    which = _which_factory({'python.exe': PYTHON})
    with patch('modules.monitor_launch.shutil.which', side_effect=which):
        command = build_launch_command(
            _file_target(r'C:\prog\sub\a.exe', cwd=r'D:\work'), None)

    assert command.cwd == r'D:\work'
