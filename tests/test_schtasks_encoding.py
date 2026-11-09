#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计划任务命令输出边界的解码回归测试。"""

import os
import subprocess
import sys
import textwrap

import pytest
from unittest.mock import patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

from modules import monitor, utils
from modules.utils import run_task_action, task_action_exists


CHINESE_ERROR = '错误: 无法运行任务'
CHINESE_STDOUT = '输出: 任务已触发'


def _completed(returncode, stdout=b'', stderr=b''):
    """构造携带原始字节输出的 CompletedProcess 替身。

    Args:
        returncode (int): 子进程返回码。
        stdout (bytes | None): 标准输出原始字节。
        stderr (bytes | None): 标准错误原始字节。

    Returns:
        subprocess.CompletedProcess: 用于打桩的完成结果。
    """
    return subprocess.CompletedProcess(
        args=['schtasks'], returncode=returncode,
        stdout=stdout, stderr=stderr)


def _launch_config():
    """构造只覆盖计划任务触发路径的最小配置。

    Returns:
        dict: 含 monitor.launch 的配置字典。
    """
    return {
        'monitor': {
            'mode': 'launch',
            'common': {'max_wait': '0s', 'check_interval': '1s'},
            'launch': {
                'type': 'task', 'path': None, 'args': None,
                'cwd': None, 'task_name': r'\Custom\MyTask',
            },
        },
        'external': {},
    }


def test_task_action_exists_queries_without_capturing_output():
    """存在性查询只依赖返回码，应丢弃标准输出与错误输出且不解码。"""
    with patch('modules.utils.subprocess.run',
               return_value=_completed(0)) as run_mock:
        assert task_action_exists(r'\Custom\MyTask') is True

    run_mock.assert_called_once_with(
        ['schtasks', '/query', '/tn', r'\Custom\MyTask'],
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_task_action_exists_returns_false_for_nonzero_code():
    """存在性查询遇到非 0 返回码时返回 False。"""
    with patch('modules.utils.subprocess.run',
               return_value=_completed(1)):
        assert task_action_exists(r'\Custom\MyTask') is False


def test_run_task_action_success_keeps_raw_output_untouched():
    """触发成功时不解码输出，只记录 INFO 日志。"""
    raw_stdout = CHINESE_STDOUT.encode('cp936')
    with patch('modules.utils.subprocess.run',
               return_value=_completed(0, stdout=raw_stdout)) as run_mock, \
            patch('modules.utils.LOGGER.info') as info_mock:
        run_task_action(r'\Custom\MyTask')

    run_mock.assert_called_once_with(
        ['schtasks', '/run', '/tn', r'\Custom\MyTask'],
        shell=False,
        capture_output=True,
    )
    info_mock.assert_called_once()


def test_run_task_action_success_tolerates_undecodable_output():
    """成功路径遇到非法字节也不抛解码异常。"""
    with patch('modules.utils.subprocess.run',
               return_value=_completed(0, stdout=b'\xb3\x00\xff')):
        run_task_action(r'\Custom\MyTask')


def test_run_task_action_failure_uses_cp936_stderr_message():
    """失败时 CP936 中文错误输出应解码为可读消息。"""
    raw_stderr = CHINESE_ERROR.encode('cp936')
    with patch('modules.utils.subprocess.run',
               return_value=_completed(1, stderr=raw_stderr)):
        with pytest.raises(RuntimeError) as excinfo:
            run_task_action(r'\Custom\MyTask')

    assert CHINESE_ERROR in str(excinfo.value)


def test_run_task_action_failure_falls_back_to_stdout_when_stderr_blank():
    """错误输出为空白时不得掩盖标准输出中的消息。"""
    raw_stdout = CHINESE_STDOUT.encode('cp936')
    with patch('modules.utils.subprocess.run',
               return_value=_completed(1, stdout=raw_stdout, stderr=b'  \r\n')):
        with pytest.raises(RuntimeError) as excinfo:
            run_task_action(r'\Custom\MyTask')

    assert CHINESE_STDOUT in str(excinfo.value)


def test_run_task_action_failure_without_output_uses_unknown_error():
    """标准错误与标准输出均为空时回退到未知错误文案。"""
    with patch('modules.utils.subprocess.run',
               return_value=_completed(1, stdout=None, stderr=None)):
        with pytest.raises(RuntimeError) as excinfo:
            run_task_action(r'\Custom\MyTask')

    assert '未知错误' in str(excinfo.value)


def test_run_task_action_failure_replaces_invalid_bytes():
    """非法字节按替换字符处理，不抛 UnicodeDecodeError。"""
    with patch('modules.utils.subprocess.run',
               return_value=_completed(1, stderr=b'\xb3\x00\xff')):
        with pytest.raises(RuntimeError) as excinfo:
            run_task_action(r'\Custom\MyTask')

    assert '\ufffd' in str(excinfo.value)


def test_launch_task_failure_logs_decoded_stderr():
    """触发计划任务失败时日志使用解码后的可读消息并退出。"""
    config = _launch_config()
    raw_stderr = CHINESE_ERROR.encode('cp936')
    error = subprocess.CalledProcessError(
        1, ['schtasks'], output=b'', stderr=raw_stderr)

    with patch('modules.monitor.subprocess.run', side_effect=error) as run_mock, \
            patch('modules.monitor.LOGGER.error') as log_mock, \
            patch('modules.monitor.notify_fail') as fail_mock:
        with pytest.raises(SystemExit) as exc_info:
            monitor._launch_task(config['monitor']['launch'], config)

    assert exc_info.value.code == 1
    run_mock.assert_called_once_with(
        ['schtasks', '/run', '/tn', config['monitor']['launch']['task_name']],
        capture_output=True,
        check=True,
    )
    log_mock.assert_called_once()
    assert CHINESE_ERROR in log_mock.call_args[0][0]
    fail_mock.assert_called_once_with(
        f"触发计划任务失败: {config['monitor']['launch']['task_name']}")


def test_launch_task_failure_without_output_does_not_crash():
    """异常未携带任何输出时日志回退到未知错误且不二次报错。"""
    config = _launch_config()
    error = subprocess.CalledProcessError(1, ['schtasks'])

    with patch('modules.monitor.subprocess.run', side_effect=error), \
            patch('modules.monitor.LOGGER.error') as log_mock, \
            patch('modules.monitor.notify_fail'):
        with pytest.raises(SystemExit) as exc_info:
            monitor._launch_task(config['monitor']['launch'], config)

    assert exc_info.value.code == 1
    log_mock.assert_called_once()
    assert '未知错误' in log_mock.call_args[0][0]


_DRIVER_TEMPLATE = textwrap.dedent(
    '''
    import subprocess
    import sys

    sys.path.insert(0, __PROJECT_ROOT__)

    from modules import utils

    _REAL_RUN = subprocess.run
    _FAIL_OUTPUT = __FAIL_OUTPUT__
    _SUCCESS_OUTPUT = __SUCCESS_OUTPUT__


    def _fake_run(cmd, *args, **kwargs):
        """把 schtasks 命令换成写固定字节的 Python 子进程，其余参数透传。"""
        kwargs['timeout'] = 30
        if cmd and str(cmd[0]).lower() == 'schtasks':
            if 'FailTask' in str(cmd[-1]):
                script = (
                    'import sys; sys.stderr.buffer.write(' + repr(_FAIL_OUTPUT) + ');'
                    ' sys.stderr.buffer.flush(); sys.exit(1)'
                )
            else:
                script = (
                    'import sys; sys.stdout.buffer.write(' + repr(_SUCCESS_OUTPUT) + ');'
                    ' sys.stdout.buffer.flush(); sys.exit(0)'
                )
            cmd = [sys.executable, '-c', script]
        return _REAL_RUN(cmd, *args, **kwargs)


    utils.subprocess.run = _fake_run

    try:
        utils.run_task_action(r'\\Custom\\FailTask')
    except RuntimeError as exc:
        print('FAIL_MESSAGE:' + str(exc))
    else:
        print('FAIL_MESSAGE:<missing>')

    utils.run_task_action(r'\\Custom\\OkTask')
    print('SUCCESS_OK')
    '''
)


def _build_driver():
    """构造真实管道回归用例的驱动脚本源码。

    Returns:
        str: 可在 UTF-8 模式下独立运行的脚本内容。
    """
    return (_DRIVER_TEMPLATE
            .replace('__PROJECT_ROOT__', repr(PROJECT_ROOT))
            .replace('__FAIL_OUTPUT__', repr(CHINESE_ERROR.encode('cp936')))
            .replace('__SUCCESS_OUTPUT__', repr(CHINESE_STDOUT.encode('cp936'))))


@pytest.mark.skipif(sys.platform != 'win32', reason='仅在 Windows 验证真实管道解码')
def test_real_pipe_decoding_has_no_reader_thread_error(tmp_path):
    """真实管道下 UTF-8 模式不得出现读取线程解码异常且消息可读。"""
    driver = tmp_path / 'schtasks_pipe_driver.py'
    driver.write_text(_build_driver(), encoding='utf-8')
    env = dict(os.environ)
    env['PYTHONUTF8'] = '1'

    proc = subprocess.run(
        [sys.executable, str(driver)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        timeout=60,
    )

    stderr = proc.stderr.decode('utf-8', errors='replace')
    stdout = proc.stdout.decode('utf-8', errors='replace')
    assert proc.returncode == 0, stderr
    assert 'UnicodeDecodeError' not in stderr
    assert 'Exception in thread' not in stderr
    assert 'FAIL_MESSAGE:<missing>' not in stdout
    assert CHINESE_ERROR in stdout
    assert 'SUCCESS_OK' in stdout
