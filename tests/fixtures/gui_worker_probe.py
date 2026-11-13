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
