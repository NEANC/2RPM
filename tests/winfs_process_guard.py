#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""为原生 I/O 并发验收提供可终止的进程边界。"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import pytest


CHILD_NODE_ENV = 'TWO_RPM_WINFS_CHILD_NODE'
DEFAULT_TIMEOUT = 30
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_isolated(request, tmp_path, timeout=DEFAULT_TIMEOUT):
    """父进程点名运行并回收测试；精确匹配的子测试返回 False 执行正文。"""
    suffix = request.node.nodeid.split('::', 1)[1]
    node = f'{Path(request.node.path).resolve()}::{suffix}'
    if os.environ.get(CHILD_NODE_ENV) == node:
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
        command = [
            sys.executable, '-B', '-m', 'pytest', node, '-q', '-rs',
            '-o', 'addopts=', '-p', 'no:cacheprovider',
            f'--basetemp={Path(folder) / "tmp"}', f'--junitxml={report}',
        ]
        try:
            # run 在超时路径 kill 并 wait 后才抛出异常，随后才能清理持有文件。
            result = subprocess.run(
                command, cwd=PROJECT_ROOT, env=env, timeout=timeout,
                capture_output=True, text=True, encoding='utf-8', errors='replace',
            )
        except subprocess.TimeoutExpired as error:
            pytest.fail(f'子测试超时（{timeout} 秒）：{node}\n{error.stdout!r}',
                        pytrace=False)
        output = result.stdout + result.stderr
        if result.returncode != 0:
            pytest.fail(f'子测试退出码 {result.returncode}：{node}\n{output}',
                        pytrace=False)
        cases = ET.parse(report).findall('.//testcase')
        if len(cases) != 1:
            pytest.fail(f'子测试必须仅运行一个节点：{node}\n{output}', pytrace=False)
        skipped = cases[0].find('skipped')
        if skipped is not None:
            pytest.skip(skipped.get('message', '') + '\n' + output)
    return True
