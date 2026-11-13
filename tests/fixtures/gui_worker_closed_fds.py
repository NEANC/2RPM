#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""在真实三管道子进程内验证关闭标准 fd 后的 worker 引导。"""

import ctypes
import gc
import importlib.abc
import msvcrt
import os
from pathlib import Path
import sys


class ImportIsolationProbe(importlib.abc.MetaPathFinder):
    """在业务模块导入边界记录普通三流的隔离状态。"""

    def __init__(self, marker):
        """保存供父进程读取的导入状态路径。"""
        self.marker = Path(marker)

    def find_spec(self, fullname, path=None, target=None):
        """仅在业务导入时检查 Python 流及 CRT 标准描述符。"""
        if fullname == 'modules.gui.test_actions':
            streams = (sys.stdin, sys.stdout, sys.stderr)
            isolated = all(stream is not None and
                           stream.name == os.devnull for stream in streams)
            try:
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
                kernel.GetStdHandle.restype = ctypes.c_void_p
                kernel.GetFileType.argtypes = [ctypes.c_void_p]
                kernel.GetFileType.restype = ctypes.c_ulong
                isolated = isolated and all(
                    os.fstat(fd) and
                    kernel.GetFileType(msvcrt.get_osfhandle(fd)) == 2 and
                    kernel.GetFileType(kernel.GetStdHandle(-10 - fd)) == 2
                    for fd in range(3))
            except OSError:
                isolated = False
            self.marker.write_text('isolated' if isolated else 'not isolated',
                                   encoding='utf-8')
        return None


def main():
    """保留 Windows 标准管道，再关闭指定 CRT fd 并执行 worker.main。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from modules.gui import worker

    marker = sys.argv[1]
    sys.meta_path.insert(0, ImportIsolationProbe(marker))
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
    kernel.GetStdHandle.restype = ctypes.c_void_p
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetStdHandle.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
    retained = []
    for fd in map(int, sys.argv[2:]):
        kind = -10 - fd
        retained.append((kind, os.dup(fd)))
        os.close(fd)
    sys.stdin = sys.stdout = sys.stderr = None
    gc.collect()
    originals = (sys.__stdin__, sys.__stdout__, sys.__stderr__)
    for fd in map(int, sys.argv[2:]):
        if originals[fd] is not None:
            originals[fd].close()
    sys.__stdin__ = sys.__stdout__ = sys.__stderr__ = None
    for kind, saved_fd in retained:
        if not kernel.SetStdHandle(kind, ctypes.c_void_p(msvcrt.get_osfhandle(saved_fd))):
            raise OSError('无法恢复标准管道')
    return worker.main()


if __name__ == '__main__':
    raise SystemExit(main())
