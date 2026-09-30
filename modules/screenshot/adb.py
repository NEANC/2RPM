#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""通过已有本机 ADB Server 获取内存 PNG，不启动外部程序。

私有连接适配仅支持核验过的 adbutils 2.12.0。连接与每次读写有有限
超时，但连续收到数据时不保证整个截图操作的硬截止。
"""

from contextlib import ExitStack
from io import BytesIO
import socket
import sys

import adbutils
from adbutils._adb import AdbConnection
from adbutils.errors import AdbConnectionError
from adbutils.errors import AdbError
from adbutils.errors import AdbTimeout
from PIL import Image

from .models import CaptureError
from .models import CaptureResult


_ADBUTILS_VERSION = '2.12.0'
_SERVER_HOST = '127.0.0.1'
_SERVER_PORT = 5037
_SOCKET_TIMEOUT = 5.0
_MAX_PNG_RESPONSE_BYTES = 64 * 1024 * 1024
_PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
_PNG_END = b'\x00\x00\x00\x00IEND\xaeB`\x82'
_MESSAGES = {
    'invalid_serial': 'ADB 序列号必须为非空字符串',
    'adb_version_unsupported': 'ADB 依赖版本未经支持验证',
    'adb_unavailable': '无法连接已有本机 ADB Server',
    'adb_not_found': '未找到指定 ADB 设备',
    'adb_offline': '指定 ADB 设备处于离线状态',
    'adb_unauthorized': '指定 ADB 设备尚未授权',
    'adb_timeout': 'ADB 连接或读写等待超时',
    'adb_protocol_failed': 'ADB 设备通信失败',
    'adb_image_failed': 'ADB 未返回完整有效的 PNG 图像',
}


def _error(code):
    """返回固定安全错误，不携带底层异常或设备响应。"""
    return CaptureError(code, _MESSAGES[code])


class _SocketConnection(AdbConnection):
    """替换 2.12.0 自动启动路径，保留其协议和上下文接口。"""

    def __init__(self, host, port, timeout=_SOCKET_TIMEOUT):
        """在基类初始化前设置有限等待，连接失败即释放 socket。"""
        self._timeout = timeout
        super().__init__(host, port)

    def _safe_connect(self):
        """仅连接给定主机 socket，绝不调用基类的自动启动实现。"""
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            connection.settimeout(self._timeout)
            connection.connect((self._AdbConnection__host,
                                self._AdbConnection__port))
            return connection
        except BaseException:
            try:
                connection.close()
            except OSError:
                pass
            raise

    def close(self):
        """幂等关闭自有 socket，普通关闭错误不覆盖正在传播的主因。"""
        connection = self._AdbConnection__conn
        if connection is not None:
            self._AdbConnection__conn = None
            primary_error = sys.exc_info()[0]
            try:
                connection.close()
            except OSError:
                if primary_error is None:
                    raise

    def send_command(self, command):
        """完整发送长度前缀及 UTF-8 命令，避免 socket 部分发送。"""
        encoded = command.encode('utf-8')
        if len(encoded) > 0xffff:
            raise AdbError('command too long')
        self.conn.sendall(f'{len(encoded):04x}'.encode('ascii') + encoded)

    def read(self, count):
        """固定长度协议字段必须完整，截断时立即报告失败。"""
        return self.read_exact(count)

    def read_until_close(self, encoding='utf-8'):
        """接收压缩 PNG 响应并在进入缓存前限制累计字节数。"""
        chunks = []
        received = 0
        while True:
            remaining = _MAX_PNG_RESPONSE_BYTES - received
            chunk = self.recv(min(65536, remaining + 1))
            if not chunk:
                break
            if len(chunk) > remaining:
                raise _error('adb_image_failed')
            chunks.append(chunk)
            received += len(chunk)
        content = b''.join(chunks)
        return content.decode(encoding) if encoding else content


class _ServerClient(adbutils.AdbClient):
    """将设备状态、独立版本查询和 shell 连接绑定同一安全工厂。"""

    def __init__(self):
        """明确使用本机 Server，不读取设备或主机地址环境配置。"""
        super().__init__(host=_SERVER_HOST, port=_SERVER_PORT,
                         socket_timeout=_SOCKET_TIMEOUT)
        self._connections = ExitStack()

    def __enter__(self):
        """开始一次截图的连接资源作用域。"""
        return self

    def __exit__(self, exc_type, exc, traceback):
        """清理包括 transport 初始化失败时尚未交还调用者的连接。"""
        return self._connections.__exit__(exc_type, exc, traceback)

    def make_connection(self, timeout=None):
        """每次都使用无启动连接，并限制库默认的较长等待。"""
        wait = min(timeout, _SOCKET_TIMEOUT) if timeout else _SOCKET_TIMEOUT
        try:
            connection = _SocketConnection(self.host, self.port, wait)
        except TimeoutError:
            raise AdbTimeout('connection timeout') from None
        except OSError:
            raise AdbConnectionError('connection unavailable') from None
        self._connections.callback(connection.close)
        return connection


def _protocol_error(error, serial):
    """仅识别已知设备失败响应，对外始终使用固定原因。"""
    message = str(error)
    if message in ('device not found', f"device '{serial}' not found"):
        return _error('adb_not_found')
    if message == 'device offline':
        return _error('adb_offline')
    if message == 'device unauthorized' or message.startswith(
            'device unauthorized.'):
        return _error('adb_unauthorized')
    return _error('adb_protocol_failed')


def _png_dimensions(png_bytes):
    """验证完整 PNG 结构和校验和，再实际解码获取有效尺寸。"""
    try:
        if not (png_bytes.startswith(_PNG_SIGNATURE)
                and png_bytes.endswith(_PNG_END)):
            raise _error('adb_image_failed')
        with BytesIO(png_bytes) as stream:
            with Image.open(stream, formats=['PNG']) as image:
                image.verify()
        with BytesIO(png_bytes) as stream:
            with Image.open(stream, formats=['PNG']) as image:
                width, height = image.size
                if (width <= 0 or height <= 0
                        or width * height > Image.MAX_IMAGE_PIXELS):
                    raise _error('adb_image_failed')
                image.load()
                return width, height
    except CaptureError:
        raise
    except (OSError, ValueError, SyntaxError, EOFError,
            Image.DecompressionBombError):
        raise _error('adb_image_failed') from None


def capture_adb(serial: str) -> CaptureResult:
    """仅捕获明确 serial 的设备，不枚举、自动连接或回退。

    Args:
        serial: 非空设备序列号；有效字符串原样传给 ADB Server。

    Returns:
        含原始 PNG 字节、明确来源、序列号和尺寸的不可变结果。

    Raises:
        CaptureError: 输入、依赖版本、连接、设备状态或图像无效。

    超时限制单次连接和读写等待，不承诺整个操作的绝对硬截止。
    """
    if not isinstance(serial, str) or not serial.strip():
        raise _error('invalid_serial')
    if adbutils.__version__ != _ADBUTILS_VERSION:
        raise _error('adb_version_unsupported')
    try:
        with _ServerClient() as client:
            device = client.device(serial=serial)
            state = device.get_state()
            if state != 'device':
                code = {'offline': 'adb_offline',
                        'unauthorized': 'adb_unauthorized'}.get(
                            state, 'adb_protocol_failed')
                raise _error(code)
            png_bytes = device.shell(
                ['screencap', '-p'], encoding=None, timeout=_SOCKET_TIMEOUT)
    except CaptureError:
        raise
    except (AdbTimeout, TimeoutError):
        raise _error('adb_timeout') from None
    except AdbConnectionError:
        raise _error('adb_unavailable') from None
    except AdbError as error:
        raise _protocol_error(error, serial) from None
    except (OSError, ValueError, EOFError):
        raise _error('adb_protocol_failed') from None
    width, height = _png_dimensions(png_bytes)
    return CaptureResult(png_bytes, 'adb', serial, width, height)
