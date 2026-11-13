#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""只验证子进程协议句柄与普通流隔离的探针。"""

import ctypes
import logging
import os
import sys
from pathlib import Path


def main():
    """在引导前移除 Python 流，再向所有普通输出层写噪声。"""
    if '--unread-stdin' in sys.argv:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_wchar_p]
        kernel.CreateEventW.restype = ctypes.c_void_p
        ready = kernel.CreateEventW(None, 1, 0, sys.argv[-1])
        if not ready:
            return 2
        kernel.SetEvent(ctypes.c_void_p(ready))
        kernel.WaitForSingleObject(ctypes.c_void_p(ready), 0xffffffff)
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    if '--missing-streams' in sys.argv:
        sys.stdin = sys.stdout = sys.stderr = None
    from modules.gui.protocol import TestResult, encode_result
    from modules.gui.worker import open_protocol_streams

    request_stream, result_stream = open_protocol_streams()
    try:
        request_stream.read()
        print('secret-value', file=sys.stdout)
        logging.warning('secret-value')
        os.write(1, b'secret-value' * 100000)
        os.write(2, b'secret-value')
        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        written = ctypes.c_ulong()
        ctypes.windll.kernel32.WriteFile(handle, b'secret-value', 12,
                                         ctypes.byref(written), None)
        result_stream.write(encode_result(TestResult(
            'req-1', 2, 3, 'success', '中文完成', ())))
        result_stream.flush()
    finally:
        request_stream.close()
        result_stream.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
