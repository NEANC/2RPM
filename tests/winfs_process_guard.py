#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""为原生 I/O 并发验收提供可终止的进程边界。"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

import pytest


CHILD_NODE_ENV = 'TWO_RPM_WINFS_CHILD_NODE'
CHILD_READY_ENV = 'TWO_RPM_WINFS_CHILD_READY'
DEFAULT_TIMEOUT = 30
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_isolated(
    request, tmp_path, timeout=DEFAULT_TIMEOUT, startup_timeout=DEFAULT_TIMEOUT,
):
    """父进程点名运行并回收测试；精确匹配的子测试返回 False 执行正文。"""
    suffix = request.node.nodeid.split('::', 1)[1]
    node = f'{Path(request.node.path).resolve()}::{suffix}'
    if os.environ.get(CHILD_NODE_ENV) == node:
        Path(os.environ[CHILD_READY_ENV]).touch()
        return False

    env = os.environ.copy()
    env[CHILD_NODE_ENV] = node
    env['PYTEST_ADDOPTS'] = ''
    env['PYTHONPATH'] = os.pathsep.join([
        str(PROJECT_ROOT), str(PROJECT_ROOT / 'tests'),
        env.get('PYTHONPATH', ''),
    ])
    with tempfile.TemporaryDirectory(prefix='winfs-isolated-', dir=tmp_path) as folder:
        report = Path(folder) / 'result.xml'
        ready = Path(folder) / 'started'
        env[CHILD_READY_ENV] = str(ready)
        command = [
            sys.executable, '-B', '-m', 'pytest', node, '-q', '-rs',
            '-o', 'addopts=', '-p', 'no:cacheprovider',
            f'--basetemp={Path(folder) / "tmp"}', f'--junitxml={report}',
            f'--confcutdir={Path(request.node.path).resolve().parent}',
        ]
        with subprocess.Popen(
            command, cwd=PROJECT_ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace',
        ) as process:
            phase, limit = '启动', startup_timeout
            deadline = time.monotonic() + startup_timeout
            try:
                while not ready.exists() and process.poll() is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, startup_timeout)
                    try:
                        process.communicate(timeout=min(0.1, remaining))
                    except subprocess.TimeoutExpired:
                        pass
                phase, limit = '执行', timeout
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                pytest.fail(
                    f'子测试{phase}超时（{limit} 秒）：{node}\n{stdout}{stderr}',
                    pytrace=False,
                )
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        output = stdout + stderr
        if process.returncode != 0:
            pytest.fail(f'子测试退出码 {process.returncode}：{node}\n{output}',
                        pytrace=False)
        cases = ET.parse(report).findall('.//testcase')
        if len(cases) != 1:
            pytest.fail(f'子测试必须仅运行一个节点：{node}\n{output}', pytrace=False)
        skipped = cases[0].find('skipped')
        if skipped is not None:
            pytest.skip(skipped.get('message', '') + '\n' + output)
    return True
