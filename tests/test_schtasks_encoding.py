#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计划任务命令输出边界的解码回归测试。"""

import ctypes
import os
import subprocess
import sys
import textwrap

import pytest
from unittest.mock import patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

from modules import monitor, utils
from modules.monitor_target import MonitorTarget
from modules.utils import run_task_action, task_action_exists

# 触发计划任务使用的目标
TASK_TARGET = MonitorTarget(kind='task', value=r'\Custom\MyTask', cwd=None)


def _get_oem_codec():
    """获取当前 Windows OEM 代码页对应的 Python 编码名。

    Returns:
        str: 形如 ``cp936`` / ``cp437`` 的编码名。
    """
    if sys.platform != 'win32':
        return 'cp437'
    return f'cp{ctypes.windll.kernel32.GetOEMCP()}'


def _oem_can_encode(text):
    """判断文本能否用当前 OEM 代码页表示。

    Args:
        text (str): 待判断文本。

    Returns:
        bool: 可编码时返回 True，否则返回 False。
    """
    try:
        text.encode(OEM_CODEC)
    except UnicodeEncodeError:
        return False
    return True


OEM_CODEC = _get_oem_codec()

CHINESE_ERROR = '错误: 无法运行任务'
CHINESE_STDOUT = '输出: 任务已触发'
ASCII_ERROR = 'ERROR: cannot run task'
ASCII_STDOUT = 'OUTPUT: task triggered'
# 当前 OEM 代码页无法表示中文时退化为 ASCII 文案，保证解码可逆
READABLE_ERROR = CHINESE_ERROR if _oem_can_encode(CHINESE_ERROR) else ASCII_ERROR
READABLE_STDOUT = CHINESE_STDOUT if _oem_can_encode(CHINESE_STDOUT) else ASCII_STDOUT
# 真实管道用例使用的 UTF-8 非法字节
INVALID_UTF8_BYTES = b'\xb3\x81'


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


def _failed_run(returncode=1, stdout=b'', stderr=b''):
    """构造返回非 0 的完成结果替身。

    Args:
        returncode (int): 子进程返回码
        stdout (bytes | None): 标准输出原始字节
        stderr (bytes | None): 标准错误原始字节

    Returns:
        subprocess.CompletedProcess: 用于打桩的完成结果
    """
    return _completed(returncode, stdout=stdout, stderr=stderr)


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
    raw_stdout = READABLE_STDOUT.encode(OEM_CODEC)
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


def test_run_task_action_failure_uses_oem_stderr_message():
    """失败时按当前 OEM 代码页解码错误输出为可读消息。"""
    raw_stderr = READABLE_ERROR.encode(OEM_CODEC)
    with patch('modules.utils.subprocess.run',
               return_value=_completed(1, stderr=raw_stderr)):
        with pytest.raises(RuntimeError) as excinfo:
            run_task_action(r'\Custom\MyTask')

    assert READABLE_ERROR in str(excinfo.value)


@pytest.mark.skipif(not _oem_can_encode(CHINESE_ERROR),
                    reason='当前 OEM 代码页无法表示中文')
def test_run_task_action_failure_reads_chinese_error_in_oem():
    """当前 OEM 可表示中文时，中文错误输出应解码为可读消息。"""
    raw_stderr = CHINESE_ERROR.encode(OEM_CODEC)
    with patch('modules.utils.subprocess.run',
               return_value=_completed(1, stderr=raw_stderr)):
        with pytest.raises(RuntimeError) as excinfo:
            run_task_action(r'\Custom\MyTask')

    assert CHINESE_ERROR in str(excinfo.value)


def test_run_task_action_failure_falls_back_to_stdout_when_stderr_blank():
    """错误输出为空白时不得掩盖标准输出中的消息。"""
    raw_stdout = READABLE_STDOUT.encode(OEM_CODEC)
    with patch('modules.utils.subprocess.run',
               return_value=_completed(1, stdout=raw_stdout, stderr=b'  \r\n')):
        with pytest.raises(RuntimeError) as excinfo:
            run_task_action(r'\Custom\MyTask')

    assert READABLE_STDOUT in str(excinfo.value)


def test_run_task_action_failure_without_output_uses_unknown_error():
    """标准错误与标准输出均为空时回退到未知错误文案。"""
    with patch('modules.utils.subprocess.run',
               return_value=_completed(1, stdout=None, stderr=None)):
        with pytest.raises(RuntimeError) as excinfo:
            run_task_action(r'\Custom\MyTask')

    assert '未知错误' in str(excinfo.value)


def test_run_task_action_failure_replaces_invalid_bytes():
    """非法字节按替换字符处理，不抛 UnicodeDecodeError。"""
    raw = b'\xb3\x00\xff'
    expected = raw.decode(OEM_CODEC, 'replace')
    with patch('modules.utils.subprocess.run',
               return_value=_completed(1, stderr=raw)):
        with pytest.raises(RuntimeError) as excinfo:
            run_task_action(r'\Custom\MyTask')

    message = str(excinfo.value)
    assert message.strip()
    if '\ufffd' in expected:
        assert '\ufffd' in message


def test_trigger_task_failure_raises_decoded_stderr():
    """触发计划任务失败时抛出含解码后可读消息的 RuntimeError。"""
    raw_stderr = READABLE_ERROR.encode(OEM_CODEC)

    with patch('modules.monitor.subprocess.run',
               return_value=_failed_run(stderr=raw_stderr)) as run_mock:
        with pytest.raises(RuntimeError) as exc_info:
            monitor._trigger_task(TASK_TARGET)

    run_mock.assert_called_once_with(
        ['schtasks', '/run', '/tn', TASK_TARGET.value],
        capture_output=True,
        check=False,
    )
    assert READABLE_ERROR in str(exc_info.value)


def test_trigger_task_failure_without_output_does_not_crash():
    """返回码非 0 且无任何输出时使用未知错误且不二次报错。"""
    with patch('modules.monitor.subprocess.run',
               return_value=_failed_run()):
        with pytest.raises(RuntimeError) as exc_info:
            monitor._trigger_task(TASK_TARGET)

    assert '未知错误' in str(exc_info.value)


def test_trigger_task_failure_falls_back_to_stdout_when_stderr_blank():
    """错误输出为空白时应改用标准输出中的消息。"""
    with patch('modules.monitor.subprocess.run',
               return_value=_failed_run(
                   stdout=READABLE_STDOUT.encode(OEM_CODEC),
                   stderr=b'  \r\n')):
        with pytest.raises(RuntimeError) as exc_info:
            monitor._trigger_task(TASK_TARGET)

    assert READABLE_STDOUT in str(exc_info.value)


def test_trigger_task_failure_handles_invalid_bytes():
    """失败输出为非法字节时按替换字符解码，不抛解码异常。"""
    raw_stderr = b'\xb3\x00\xff'
    expected = raw_stderr.decode(OEM_CODEC, 'replace')

    with patch('modules.monitor.subprocess.run',
               return_value=_failed_run(stderr=raw_stderr)):
        with pytest.raises(RuntimeError) as exc_info:
            monitor._trigger_task(TASK_TARGET)

    message = str(exc_info.value)
    assert message.strip()
    assert '触发计划任务失败' in message
    if '\ufffd' in expected:
        assert '\ufffd' in message


def test_trigger_task_success_does_not_decode_undecodable_output():
    """触发成功时完全不解码原始输出，只确认请求已提交。"""
    with patch('modules.monitor.subprocess.run',
               return_value=_completed(0, stdout=b'\xb3\x00\xff')) as run_mock, \
            patch('modules.monitor.decode_process_output') as decode_mock:
        result = monitor._trigger_task(TASK_TARGET)

    assert result is None
    run_mock.assert_called_once_with(
        ['schtasks', '/run', '/tn', TASK_TARGET.value],
        capture_output=True,
        check=False,
    )
    decode_mock.assert_not_called()


_DRIVER_TEMPLATE = textwrap.dedent(
    '''
    import subprocess
    import sys

    sys.path.insert(0, __PROJECT_ROOT__)

    from modules import utils

    _REAL_RUN = subprocess.run
    _INVALID_OUTPUT = __INVALID_OUTPUT__
    _READABLE_OUTPUT = __READABLE_OUTPUT__
    _SUCCESS_OUTPUT = __SUCCESS_OUTPUT__


    def _fake_run(cmd, *args, **kwargs):
        """把 schtasks 命令换成写固定字节的 Python 子进程，其余参数透传。"""
        kwargs['timeout'] = 30
        if cmd and str(cmd[0]).lower() == 'schtasks':
            target = str(cmd[-1])
            if 'InvalidFailTask' in target:
                output, stream = _INVALID_OUTPUT, 'stderr'
            elif 'ReadableFailTask' in target:
                output, stream = _READABLE_OUTPUT, 'stderr'
            else:
                output, stream = _SUCCESS_OUTPUT, 'stdout'
            exit_code = 0 if stream == 'stdout' else 1
            script = (
                'import sys; sys.' + stream + '.buffer.write(' + repr(output) + ');'
                ' sys.' + stream + '.buffer.flush(); sys.exit(' + str(exit_code) + ')'
            )
            cmd = [sys.executable, '-c', script]
        return _REAL_RUN(cmd, *args, **kwargs)


    utils.subprocess.run = _fake_run

    try:
        utils.run_task_action(r'\\Custom\\InvalidFailTask')
    except RuntimeError as exc:
        print('INVALID_MESSAGE:' + str(exc))
    else:
        print('INVALID_MESSAGE:<missing>')

    try:
        utils.run_task_action(r'\\Custom\\ReadableFailTask')
    except RuntimeError as exc:
        print('READABLE_MESSAGE:' + str(exc))
    else:
        print('READABLE_MESSAGE:<missing>')

    utils.run_task_action(r'\\Custom\\OkTask')
    print('SUCCESS_OK')

    from modules import monitor
    from modules.monitor_target import MonitorTarget


    monitor.notify_fail = lambda *args, **kwargs: None

    try:
        monitor._trigger_task(MonitorTarget(
            kind='task', value=r'\\Custom\\InvalidFailTask', cwd=None))
    except RuntimeError as exc:
        print('MONITOR_MESSAGE:' + str(exc))
    else:
        print('MONITOR_MESSAGE:<missing>')
    '''
)


def _build_driver():
    """构造真实管道回归用例的驱动脚本源码。

    Returns:
        str: 可在 UTF-8 模式下独立运行的脚本内容。
    """
    return (_DRIVER_TEMPLATE
            .replace('__PROJECT_ROOT__', repr(PROJECT_ROOT))
            .replace('__INVALID_OUTPUT__', repr(INVALID_UTF8_BYTES))
            .replace('__READABLE_OUTPUT__', repr(READABLE_ERROR.encode(OEM_CODEC)))
            .replace('__SUCCESS_OUTPUT__', repr(READABLE_STDOUT.encode(OEM_CODEC))))


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
    assert 'readerthread' not in stderr.lower()
    assert 'INVALID_MESSAGE:<missing>' not in stdout
    assert 'READABLE_MESSAGE:<missing>' not in stdout
    invalid_line = next(
        line for line in stdout.splitlines()
        if line.startswith('INVALID_MESSAGE:'))
    assert invalid_line[len('INVALID_MESSAGE:'):].strip()
    assert READABLE_ERROR in stdout
    assert 'SUCCESS_OK' in stdout
    assert 'MONITOR_MESSAGE:<missing>' not in stdout
    monitor_line = next(
        line for line in stdout.splitlines()
        if line.startswith('MONITOR_MESSAGE:'))
    assert monitor_line[len('MONITOR_MESSAGE:'):].strip()
