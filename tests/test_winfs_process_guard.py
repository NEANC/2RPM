#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""子进程隔离的期限、回收及 pytest 结果传播回归。"""

import importlib
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def isolation():
    """加载待实现辅助模块，缺失时给出明确失败。"""
    try:
        return importlib.import_module('winfs_process_guard')
    except ModuleNotFoundError:
        pytest.fail('缺少 winfs_process_guard 子进程隔离实现')


def make_probe(tmp_path, body):
    """生成只允许点名执行的探针，额外节点执行即失败。"""
    source = tmp_path / 'probe_case.py'
    source.write_text(
        'import pytest\n'
        'from winfs_process_guard import run_isolated\n\n'
        'def test_probe(request, tmp_path):\n'
        '    """子进程验证精确标记并执行探针。"""\n'
        '    assert not run_isolated(request, tmp_path)\n'
        + ''.join('    ' + line + '\n' for line in body.splitlines())
        + '\ndef test_not_selected():\n'
        '    """此节点不得被隔离器选中。"""\n'
        '    pytest.fail("unexpected whole-file execution")\n',
        encoding='utf-8',
    )
    return SimpleNamespace(node=SimpleNamespace(
        nodeid=str(source) + '::test_probe', path=source))


def test_hung_child_is_reaped_before_temp_cleanup(isolation, tmp_path, monkeypatch):
    """故意挂起持有文件的子进程，期限后确认回收且专属目录清空。"""
    ready = tmp_path / 'ready.txt'
    request = make_probe(tmp_path, '\n'.join([
        'from pathlib import Path',
        'import time',
        'held = (tmp_path / "held.bin").open("wb")',
        f'Path({str(ready)!r}).write_text("ready")',
        'time.sleep(300)',
    ]))
    processes = []
    original = isolation.subprocess.Popen

    def record_process(*args, **kwargs):
        """记录真实子进程以检查超时路径已经 wait 回收。"""
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(isolation.subprocess, 'Popen', record_process)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    started = time.monotonic()
    with pytest.raises(pytest.fail.Exception, match='超时'):
        isolation.run_isolated(request, workspace, timeout=5)
    assert time.monotonic() - started < 15
    assert ready.read_text() == 'ready'
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert list(workspace.iterdir()) == []


@pytest.mark.parametrize('body, outcome, message', [
    ('assert False, "probe failure"', pytest.fail.Exception, 'probe failure'),
    ('pytest.skip("probe skip")', pytest.skip.Exception, 'probe skip'),
])
def test_child_result_is_visible(isolation, tmp_path, body, outcome, message):
    """子测试普通失败和跳过均保留原始原因，不伪装为通过。"""
    request = make_probe(tmp_path, body)
    with pytest.raises(outcome, match=message):
        isolation.run_isolated(request, tmp_path, timeout=15)
    assert not list(tmp_path.glob('winfs-isolated-*'))


def test_only_requested_node_runs_without_recursion(isolation, tmp_path, monkeypatch):
    """标记精确到节点，成功探针只执行一次且忽略外部附加选项。"""
    request = make_probe(tmp_path, 'assert True')
    monkeypatch.setenv(isolation.CHILD_NODE_ENV, 'another-node')
    monkeypatch.setenv('PYTEST_ADDOPTS', 'not_a_real_test.py')
    assert isolation.run_isolated(request, tmp_path, timeout=15)
    assert not list(tmp_path.glob('winfs-isolated-*'))
