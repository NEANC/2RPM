#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""通过已有本机 ADB Server 获取内存 PNG，不启动外部程序。

私有连接适配仅支持核验过的 adbutils 2.12.0。连接与每次读写有有限
超时，但连续收到数据时不保证整个截图操作的硬截止。
"""

from contextlib import ExitStack
from io import BytesIO
import socket
import struct
import sys

import adbutils
from adbutils._adb import AdbConnection
from adbutils.errors import AdbConnectionError
from adbutils.errors import AdbError
from adbutils.errors import AdbTimeout
from PIL import Image

from .encoding import encode_image
from .models import CaptureError
from .models import CaptureResult


_ADBUTILS_VERSION = '2.12.0'
_SERVER_HOST = '127.0.0.1'
_SERVER_PORT = 5037
_SOCKET_TIMEOUT = 5.0
_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
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
    'adb_image_failed': 'ADB 未返回完整有效的图像',
    'adb_raw_invalid': 'ADB 未返回有效的 raw 图像',
    'adb_encode_failed': '无法编码 ADB 截图',
}


class ResponseTooLarge(Exception):
    """单次二进制响应超限。"""


class _ConnectionTimeout(AdbTimeout):
    """建立 ADB socket 时发生的超时。"""


class _ConnectionUnavailable(AdbConnectionError):
    """建立 ADB socket 时不可用。"""


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
        self.command = command
        encoded = command.encode('utf-8')
        if len(encoded) > 0xffff:
            raise AdbError('command too long')
        self.conn.sendall(f'{len(encoded):04x}'.encode('ascii') + encoded)

    def read(self, count):
        """固定长度协议字段必须完整，截断时立即报告失败。"""
        return self.read_exact(count)

    def read_until_close(self, encoding='utf-8'):
        """仅限制二进制 PNG 接收，保留文本读取及严格解码行为。

        超限最多额外接收一字节，不代表总内存上限；分片与拼接结果
        可同时存在，图像解码还需额外内存。
        """
        chunks = []
        received = 0
        limited = encoding is None and self.command.startswith('shell:')
        while True:
            remaining = _MAX_RESPONSE_BYTES - received
            count = 65536 if not limited else min(65536, remaining + 1)
            chunk = self.recv(count)
            if not chunk:
                break
            if limited and len(chunk) > remaining:
                try:
                    self.close()
                finally:
                    raise _error('adb_image_failed') from None
            chunks.append(chunk)
            received += len(chunk)
        content = b''.join(chunks)
        if limited and received > _MAX_RESPONSE_BYTES:
            raise ResponseTooLarge()
        if encoding is None:
            return content
        return content.decode(encoding)


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
            raise _ConnectionTimeout('connection timeout') from None
        except OSError:
            raise _ConnectionUnavailable('connection unavailable') from None
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


def _parse_raw(data):
    """只接受合法头部及精确长度的 RGBA_8888 raw 响应。"""
    if len(data) < 12:
        raise _error('adb_raw_invalid')
    width, height, pixel_format = struct.unpack_from('<III', data)
    if (width == 0 or height == 0 or width * height > 89478485
            or pixel_format != 1):
        raise _error('adb_raw_invalid')
    offset = len(data) - width * height * 4
    if offset not in (12, 16):
        raise _error('adb_raw_invalid')
    try:
        return Image.frombytes('RGBA', (width, height), data[offset:])
    except Exception:
        raise _error('adb_raw_invalid') from None


def _decode_png(data):
    """校验 PNG 并加载一次，返回调用方拥有的图像副本。"""
    try:
        if not (data.startswith(_PNG_SIGNATURE) and data.endswith(_PNG_END)):
            raise _error('adb_image_failed')
        with Image.open(BytesIO(data), formats=['PNG']) as image:
            image.verify()
        with Image.open(BytesIO(data), formats=['PNG']) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > 89478485:
                raise _error('adb_image_failed')
            image.load()
            return image.copy()
    except CaptureError:
        raise
    except Exception:
        raise _error('adb_image_failed') from None


def _read_capture(serial, png):
    """每次尝试独占 adbutils 连接并返回二进制截图响应。"""
    try:
        with _ServerClient() as client:
            device = client.device(serial=serial)
            state = device.get_state()
            if state != 'device':
                raise _error({'offline': 'adb_offline',
                              'unauthorized': 'adb_unauthorized'}.get(
                                  state, 'adb_protocol_failed'))
            return device.shell(['screencap', '-p'] if png else ['screencap'],
                                encoding=None, timeout=_SOCKET_TIMEOUT)
    except ResponseTooLarge:
        raise _error('adb_image_failed') from None
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


def _encoded_result(image, serial, image_format, warnings=()):
    """编码图像并生成结果，固定分类编码错误。"""
    try:
        data = encode_image(image, image_format)
    except Exception:
        raise _error('adb_encode_failed') from None
    return CaptureResult(data, 'adb', serial, *image.size, warnings, image_format)


def _png_result(serial, automatic, warnings=()):
    """执行唯一 PNG 回退，CLI 保留收到的原始字节。"""
    try:
        data = _read_capture(serial, True)
    except ResponseTooLarge:
        raise _error('adb_image_failed') from None
    with _decode_png(data) as image:
        if automatic:
            return _encoded_result(image, serial, 'jpeg', warnings)
        return CaptureResult(data, 'adb', serial, *image.size, warnings, 'png')


def capture_adb(serial: str, *, image_format='jpeg',
                purpose='automatic') -> CaptureResult:
    """自动采集最多两次 raw；CLI 超时回退，PNG 仅作终态回退。"""
    if not isinstance(serial, str) or not serial.strip():
        raise _error('invalid_serial')
    if adbutils.__version__ != _ADBUTILS_VERSION:
        raise _error('adb_version_unsupported')
    automatic = purpose == 'automatic'
    if not automatic and image_format == 'png':
        return _png_result(serial, False)
    reason = '采集或解析失败'
    for _ in range(2):
        try:
            image = _parse_raw(_read_capture(serial, False))
        except CaptureError as error:
            if error.code not in ('adb_timeout', 'adb_raw_invalid'):
                raise
            reason = '超时' if error.code == 'adb_timeout' else '采集或解析失败'
            if not automatic and error.code == 'adb_timeout':
                break
            continue
        except ResponseTooLarge:
            reason = '响应超限'
            break
        with image:
            if not automatic:
                return _encoded_result(image, serial, image_format)
            try:
                return _encoded_result(image, serial, 'jpeg')
            except CaptureError:
                reason = '采集或编码失败'
    warnings = (() if automatic else (f'ADB raw {reason}，已回退 PNG',))
    return _png_result(serial, automatic, warnings)
