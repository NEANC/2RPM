#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 独立入口与内部测试 worker 早期分流。"""

import sys


def main(argv: list[str] | None = None) -> int:
    """在导入图形界面及业务模块前分流内部 worker。"""
    args = sys.argv[1:] if argv is None else argv
    if args == ['--internal-gui-worker']:
        from modules.gui.worker import main as worker_main
        return worker_main()
    raise SystemExit('GUI 窗口入口将在任务 8 实现')


if __name__ == '__main__':
    raise SystemExit(main())
