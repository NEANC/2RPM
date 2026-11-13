#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""单请求 GUI 测试 worker，协议句柄与普通输出相隔离。"""

import ctypes
import msvcrt
import os
import sys


def _copy_standard_handle(kind):
    """复制继承的 Windows 标准句柄并独立拥有其寿命。"""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
    kernel.GetStdHandle.restype = ctypes.c_void_p
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.DuplicateHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.DuplicateHandle.restype = ctypes.c_int
    original = kernel.GetStdHandle(kind)
    if not original or original == ctypes.c_void_p(-1).value:
        raise OSError('标准句柄不可用')
    process = kernel.GetCurrentProcess()
    copied = ctypes.c_void_p()
    if not kernel.DuplicateHandle(process, original, process,
                                  ctypes.byref(copied), 0, 0, 2):
        raise OSError('标准句柄复制失败')
    try:
        return msvcrt.open_osfhandle(copied.value, os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(copied)
        raise


def open_protocol_streams():
    """保留专用输入/输出，再将 Python、CRT 和 OS 普通三流转至空设备。"""
    input_fd = _copy_standard_handle(-10)
    try:
        output_fd = _copy_standard_handle(-11)
    except BaseException:
        os.close(input_fd)
        raise
    try:
        for number, mode in ((0, os.O_RDONLY), (1, os.O_WRONLY),
                             (2, os.O_WRONLY)):
            null_fd = os.open(os.devnull, mode)
            try:
                os.dup2(null_fd, number)
            finally:
                os.close(null_fd)
            ctypes.windll.kernel32.SetStdHandle(
                ctypes.c_ulong(-10 + number),
                ctypes.c_void_p(msvcrt.get_osfhandle(number)))
        sys.stdin = open(os.devnull, 'r', encoding='utf-8')
        sys.stdout = open(os.devnull, 'w', encoding='utf-8')
        sys.stderr = open(os.devnull, 'w', encoding='utf-8')
        return (os.fdopen(input_fd, 'rb', closefd=True),
                os.fdopen(output_fd, 'wb', closefd=True))
    except BaseException:
        os.close(input_fd)
        os.close(output_fd)
        raise


def main() -> int:
    """隔离所有业务流后只处理一条请求并写出一条结果。"""
    try:
        source, destination = open_protocol_streams()
    except (OSError, ValueError):
        return 1
    try:
        from modules.gui.protocol import decode_request, encode_result
        from modules.gui.protocol import TestResult

        request = decode_request(source.read())
        try:
            from modules.gui.test_actions import run_test
            result = run_test(request)
        except Exception:
            result = TestResult(request.request_id, request.document_generation,
                                request.revision, 'failure', '测试执行失败', ())
        destination.write(encode_result(result))
        destination.flush()
        return 0
    except (OSError, ValueError):
        return 1
    finally:
        source.close()
        destination.close()


if __name__ == '__main__':
    raise SystemExit(main())
