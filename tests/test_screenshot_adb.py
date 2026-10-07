#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""用合成 PNG 和假 socket 验证真实 adbutils 协议链，不接触用户设备。"""

import builtins
from importlib import import_module
from importlib.util import find_spec
import inspect
from io import BytesIO
from pathlib import Path
import socket
import struct
import subprocess
from unittest.mock import Mock

import adbutils
from adbutils import _adb
from adbutils import _device_base
from adbutils import _utils
from PIL import Image
import pytest


SERIAL = '127.0.0.1:16384'
COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]


def make_png(colorful=True):
    """在内存生成可实际解码的彩色或纯黑 PNG。"""
    with Image.new('RGB', (2, 2)) as image:
        if colorful:
            image.putdata(COLORS)
        with BytesIO() as output:
            image.save(output, format='PNG')
            return output.getvalue()


def block(value):
    """生成 ADB 协议的十六进制长度前缀。"""
    return f'{len(value):04x}'.encode('ascii') + value


def load_backend():
    """让缺失后端表现为断言失败而非收集错误。"""
    assert find_spec('modules.screenshot.adb') is not None, (
        '尚未实现 ADB 内存截图后端')
    return import_module('modules.screenshot.adb')


class FakeSocket:
    """模拟正常 ADB Server 应答、分片接收和可发生的网络故障。"""

    def __init__(self, environment, index):
        """登记连接顺序和资源状态，不创建真实网络连接。"""
        self.environment = environment
        self.index = index
        self.pending = bytearray()
        self.closed = False
        self.close_count = 0
        self.recv_calls = 0
        self.timeouts = []
        self.command = None
        self.shell_handshake = bytearray()
        self.payload_reads = []

    def setsockopt(self, *args):
        """接受不产生外部副作用的 socket 参数设置。"""

    def settimeout(self, timeout):
        """记录连接及读写超时，并允许模拟初始化阶段失败。"""
        if self.index == self.environment.setup_failure:
            raise OSError('secret-token setup')
        assert timeout is not None and 0 < timeout <= 10
        self.timeouts.append(timeout)

    def connect(self, address):
        """只记录主机 Server 地址，不执行真实 connect。"""
        assert self.timeouts
        self.environment.addresses.append(address)
        if self.index == self.environment.connect_failure:
            raise self.environment.failure

    def send(self, data):
        """兼容原库的 send，并记录全部协议命令。"""
        self.sendall(data)
        return len(data)

    def sendall(self, data):
        """按协议命令产生目标状态、Server 版本和截图数据。"""
        assert not self.closed
        length = int(data[:4], 16)
        assert length == len(data[4:])
        command = data[4:].decode('utf-8')
        self.command = command
        env = self.environment
        env.commands.append(command)
        if command in env.fail_responses:
            self.pending.extend(b'FAIL' + block(env.fail_responses[command]))
        elif command in env.raw_responses:
            self.pending.extend(env.raw_responses[command])
        elif command == f'host-serial:{env.serial}:get-state':
            state = env.states.pop(0) if env.states else env.state
            self.pending.extend(b'OKAY' + block(state))
        elif command == 'host:version':
            self.pending.extend(b'OKAY' + block(env.version))
        elif command == f'host:tport:serial:{env.serial}':
            self.pending.extend(b'OKAY' + (1).to_bytes(8, 'little'))
        elif command == f'host:transport:{env.serial}':
            self.pending.extend(b'OKAY')
        elif command in ('shell:screencap -p', 'shell:screencap'):
            if command in env.shell_responses:
                responses = env.shell_responses[command]
                assert responses, f'{command} 的响应队列已耗尽'
                response = responses.pop(0)
            else:
                response = env.png if command.endswith(' -p') else env.raw
            self.pending.extend(b'OKAY' + response)
        else:
            pytest.fail(f'不允许的 ADB 命令：{command!r}')

    def recv(self, count):
        """区分 shell 握手与负载，记录分片边界并注入读取故障。"""
        assert not self.closed
        self.recv_calls += 1
        env = self.environment
        read_failures = (env.read_failure if isinstance(
            env.read_failure, (tuple, list)) else (env.read_failure,))
        if self.command in read_failures:
            raise env.failure
        is_shell = self.command is not None and self.command.startswith('shell:')
        is_payload = is_shell and self.shell_handshake == b'OKAY'
        chunk_size = env.chunk_size
        if is_payload and env.payload_plan is not None:
            index = len(self.payload_reads)
            assert index < len(env.payload_plan), '出现计划外负载读取'
            step = env.payload_plan[index]
            if isinstance(step, BaseException):
                self.payload_reads.append((count, None))
                raise step
            chunk_size = step
        if is_payload and env.continuous_payload:
            value = b'x' * min(count, chunk_size)
        else:
            size = min(count, chunk_size, len(self.pending))
            value = bytes(self.pending[:size])
            del self.pending[:size]
        if is_payload:
            self.payload_reads.append((count, len(value)))
        elif is_shell:
            self.shell_handshake.extend(value)
        return value

    def shutdown(self, direction):
        """模拟可选 shutdown，不改变连接资源的所有权。"""

    def close(self):
        """登记实际关闭，重复关闭也计数以检测资源管理错误。"""
        self.closed = True
        self.close_count += 1


class AdbEnvironment:
    """让真实 adbutils 客户端使用隔离的可控 socket。"""

    def __init__(self):
        """准备一台明确 serial 的模拟设备和安全故障入口。"""
        self.serial = SERIAL
        self.state = b'device'
        self.states = []
        self.version = b'0029'
        self.png = make_png()
        self.raw = struct.pack('<III', 2, 2, 1) + bytes(
            channel for color in COLORS for channel in (*color, 255))
        self.sockets = []
        self.addresses = []
        self.commands = []
        self.fail_responses = {}
        self.raw_responses = {}
        self.shell_responses = {}
        self.chunk_size = 3
        self.payload_plan = None
        self.continuous_payload = False
        self.connect_failure = None
        self.setup_failure = None
        self.read_failure = ()
        self.failure = ConnectionRefusedError('secret-token')

    def create_socket(self, *args, **kwargs):
        """仅分配假 socket，支持任意阶段的连接工厂验证。"""
        connection = FakeSocket(self, len(self.sockets))
        self.sockets.append(connection)
        return connection

    def assert_closed(self):
        """确认所有已取得的 socket 恰好关闭一次。"""
        assert all(item.closed for item in self.sockets)
        assert all(item.close_count == 1 for item in self.sockets)


@pytest.fixture
def environment(monkeypatch):
    """封锁真实网络及外部执行，并在每个用例结束核对禁止调用记录。"""
    env = AdbEnvironment()
    monkeypatch.setattr(socket, 'socket', env.create_socket)
    forbidden = Mock(side_effect=AssertionError('禁止外部执行或设备扫描'))
    for owner in (adbutils, _adb, _device_base, _utils):
        monkeypatch.setattr(owner, 'adb_path', forbidden)
    monkeypatch.setattr(_utils, '_is_valid_exe', forbidden)
    monkeypatch.setattr(_device_base.BaseDevice, 'adb_output', forbidden)
    monkeypatch.setattr(_adb.AdbConnection, '_safe_connect', forbidden)
    for name in ('Popen', 'run', 'call', 'check_call', 'check_output'):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in ('list', 'iter_device', 'device_list', 'connect',
                 'disconnect', 'server_kill', 'wait_for'):
        monkeypatch.setattr(adbutils.AdbClient, name, forbidden)
    window = import_module('modules.screenshot.window')
    monkeypatch.setattr(window, 'capture_window', forbidden)
    yield env
    forbidden.assert_not_called()
    env.assert_closed()


def assert_capture_error(backend, code, serial=SERIAL, **kwargs):
    """核对固定错误分类，禁止异常消息泄露协议或底层原文。"""
    with pytest.raises(backend.CaptureError) as caught:
        backend.capture_adb(serial, **kwargs)
    assert caught.value.code == code
    assert 'secret-token' not in str(caught.value)
    if isinstance(serial, str) and serial:
        assert serial not in str(caught.value)
    return caught.value


@pytest.mark.parametrize('version', [b'0027', b'0028', b'0029'])
def test_real_protocol_returns_exact_serial_color_png(environment, version):
    """真实状态、独立版本连接和二进制 shell 链返回原始 PNG。"""
    backend = load_backend()
    environment.version = version
    result = backend.capture_adb(SERIAL)
    assert isinstance(result, backend.CaptureResult)
    assert (result.source, result.target) == ('adb', SERIAL)
    assert (result.width, result.height, result.warnings) == (2, 2, ())
    assert result.image_format == 'jpeg'
    assert result.image_bytes.startswith(b'\xff\xd8')
    with Image.open(BytesIO(environment.png)) as image:
        image.load()
        assert [image.getpixel((x, y)) for y in range(2)
                for x in range(2)] == COLORS
    transport = 'tport:serial' if int(version, 16) >= 41 else 'transport'
    assert environment.commands == [
        f'host-serial:{SERIAL}:get-state', 'host:version',
        f'host:{transport}:{SERIAL}', 'shell:screencap',
    ]
    assert environment.addresses == [('127.0.0.1', 5037)] * 3
    environment.assert_closed()


@pytest.mark.parametrize('serial', [None, False, 123, '', ' \t ', [], {}])
def test_invalid_serial_never_connects(environment, serial):
    """无效类型或空白 serial 不强转也不选择默认设备。"""
    backend = load_backend()
    assert_capture_error(backend, 'invalid_serial', serial)
    assert environment.sockets == []


def test_serial_preserved_and_host_environment_ignored(environment, monkeypatch):
    """保留显式 serial，不将设备端口或环境变量用作主机地址。"""
    backend = load_backend()
    environment.serial = ' emulator-5554 '
    monkeypatch.setenv('ANDROID_SERIAL', 'other-device')
    monkeypatch.setenv('ANDROID_ADB_SERVER_HOST', '192.0.2.1')
    monkeypatch.setenv('ANDROID_ADB_SERVER_PORT', '65530')
    result = backend.capture_adb(environment.serial)
    assert result.target == environment.serial
    assert environment.addresses == [('127.0.0.1', 5037)] * 3


@pytest.mark.parametrize(('state', 'code'), [
    (b'offline', 'adb_offline'),
    (b'unauthorized', 'adb_unauthorized'),
    (b'bootloader', 'adb_protocol_failed'),
    (b'secret-token', 'adb_protocol_failed'),
])
@pytest.mark.parametrize('purpose', ['automatic', 'cli'])
def test_unavailable_state_exhausts_attempts(environment, state, code, purpose):
    """持续非在线状态耗尽两次 raw 和唯一 PNG，且不执行 shell。"""
    backend = load_backend()
    environment.state = state
    assert_capture_error(backend, code, purpose=purpose)
    assert environment.commands == [f'host-serial:{SERIAL}:get-state'] * 3


@pytest.mark.parametrize(('response', 'code'), [
    (b'device not found', 'adb_not_found'),
    (f"device '{SERIAL}' not found".encode(), 'adb_not_found'),
    (b'device offline', 'adb_offline'),
    (b'device unauthorized.\nsecret-token', 'adb_unauthorized'),
    (b'secret-token unexpected response', 'adb_protocol_failed'),
])
@pytest.mark.parametrize('stage', ['state', 'transport', 'shell'])
def test_protocol_fail_is_safe_at_each_stage(environment, response, code, stage):
    """设备在状态检查后失效时仍安全分类并清理未完成 transport。"""
    backend = load_backend()
    commands = {
        'state': f'host-serial:{SERIAL}:get-state',
        'transport': f'host:tport:serial:{SERIAL}',
        'shell': 'shell:screencap -p',
    }
    environment.fail_responses[commands[stage]] = response
    if stage == 'shell':
        environment.fail_responses['shell:screencap'] = response
    assert_capture_error(backend, code)
    assert environment.commands.count(f'host-serial:{SERIAL}:get-state') == 3
    if stage == 'shell':
        assert environment.commands.count('shell:screencap') == 2
        assert environment.commands.count('shell:screencap -p') == 1


@pytest.mark.parametrize('index', [0, 1, 2])
@pytest.mark.parametrize(('failure', 'code'), [
    (ConnectionRefusedError('secret-token'), 'adb_unavailable'),
    (TimeoutError('secret-token'), 'adb_timeout'),
])
def test_each_connection_failure_never_starts_adb(
        environment, index, failure, code):
    """初次、检查后和独立版本连接失败均不进入自动启动路径。"""
    backend = load_backend()
    environment.connect_failure = index
    environment.failure = failure
    environment.read_failure = ()
    assert_capture_error(
        backend, code, purpose='cli', image_format='png')


@pytest.mark.parametrize('index', [0, 1, 2])
def test_partial_connection_initialization_is_closed(environment, index):
    """已取得 socket 但超时初始化失败时仍关闭全部自有资源。"""
    backend = load_backend()
    environment.setup_failure = index
    environment.connect_failure = index
    environment.failure = ConnectionRefusedError('secret-token')
    environment.read_failure = ()
    assert_capture_error(
        backend, 'adb_unavailable', purpose='cli', image_format='png')
    environment.assert_closed()


@pytest.mark.parametrize('stage', [
    f'host-serial:{SERIAL}:get-state', 'host:version',
    f'host:tport:serial:{SERIAL}', 'shell:screencap -p',
])
@pytest.mark.parametrize(('failure', 'code'), [
    (TimeoutError('secret-token'), 'adb_timeout'),
    (ConnectionResetError('secret-token'), 'adb_protocol_failed'),
])
def test_read_failures_close_every_connection(environment, stage, failure, code):
    """每条协议读取路径都有有限等待，失败后没有遗留 socket。"""
    backend = load_backend()
    environment.read_failure = (stage,)
    environment.failure = failure
    if stage == 'shell:screencap -p':
        environment.read_failure = ('shell:screencap -p', 'shell:screencap')
    assert_capture_error(backend, code)


@pytest.mark.parametrize('response', [
    b'', b'NOPE', b'OKAYzzzz', b'OKAY0008device', b'FAIL0008oops',
])
def test_incomplete_or_malformed_protocol_fails(environment, response):
    """协议长度截断或非法应答不能被误判为在线。"""
    backend = load_backend()
    environment.raw_responses[f'host-serial:{SERIAL}:get-state'] = response
    assert_capture_error(backend, 'adb_protocol_failed')
    assert 'shell:screencap -p' not in environment.commands


@pytest.mark.parametrize('encoding', ['utf-8', None])
def test_shell_response_limit_applies_only_to_binary(
        environment, monkeypatch, encoding):
    """真实 shell 文本响应不受 PNG 限额影响，二进制仍拒绝超限。"""
    backend = load_backend()
    monkeypatch.setattr(backend, '_MAX_RESPONSE_BYTES', 8)
    text = '文本响应超过八字节\n'
    environment.raw_responses['shell:echo text'] = (
        b'OKAY' + text.encode('utf-8'))
    with backend._ServerClient() as client:
        device = client.device(serial=SERIAL)
        if encoding is None:
            with pytest.raises(backend.ResponseTooLarge):
                device.shell('echo text', encoding=None, timeout=5.0)
        else:
            result = device.shell(
                'echo text', encoding=encoding, timeout=5.0, rstrip=False)
            assert result == text
    environment.assert_closed()


def test_adb_response_limit_is_64_mib(environment):
    """生产截图响应上限固定为 64 MiB。"""
    backend = load_backend()
    assert backend._MAX_RESPONSE_BYTES == 64 * 1024 * 1024


@pytest.mark.parametrize('colorful', [True, False])
def test_exact_adb_response_limit_waits_for_eof(
        environment, monkeypatch, colorful):
    """彩色和黑色合法 PNG 恰好达限后读取 EOF，原字节和像素不变。"""
    backend = load_backend()
    environment.png = make_png(colorful=colorful)
    limit = len(environment.png)
    monkeypatch.setattr(backend, '_MAX_RESPONSE_BYTES', limit)
    environment.payload_plan = [limit, 1]
    result = backend.capture_adb(
        SERIAL, purpose='cli', image_format='png')
    assert result.image_bytes == environment.png
    assert (result.width, result.height) == (2, 2)
    with Image.open(BytesIO(result.image_bytes)) as image:
        image.load()
        assert [image.getpixel((x, y)) for y in range(2)
                for x in range(2)] == (COLORS if colorful else [(0, 0, 0)] * 4)
    connection = environment.sockets[1]
    assert connection.shell_handshake == b'OKAY'
    assert connection.payload_reads == [(limit + 1, limit), (1, 0)]
    assert len(environment.sockets) == 3
    environment.assert_closed()


def test_exact_adb_response_limit_without_eof_times_out(
        environment, monkeypatch):
    """合法 PNG 达限但未读到 EOF 时仍等待，并将读取超时分类。"""
    backend = load_backend()
    limit = len(environment.png)
    monkeypatch.setattr(backend, '_MAX_RESPONSE_BYTES', limit)
    environment.payload_plan = [limit, TimeoutError('secret-token')]
    assert_capture_error(
        backend, 'adb_timeout', purpose='cli', image_format='png')
    connection = environment.sockets[1]
    assert connection.shell_handshake == b'OKAY'
    assert connection.payload_reads == [(limit + 1, len(environment.png)),
                                        (limit + 1 - len(environment.png), None)]
    assert len(environment.sockets) == 3
    environment.assert_closed()


@pytest.mark.parametrize(('plan', 'expected'), [
    ([9], [(9, 9)]),
    ([7, 2], [(9, 7), (2, 2)]),
])
def test_oversized_adb_response_is_rejected_during_receive(
        environment, monkeypatch, plan, expected):
    """相同负载按不同计划单块或累计超限，握手字节不计入额度。"""
    backend = load_backend()
    monkeypatch.setattr(backend, '_MAX_RESPONSE_BYTES', 8)
    environment.png = b'x' * 9
    environment.payload_plan = plan
    error = assert_capture_error(
        backend, 'adb_image_failed', purpose='cli', image_format='png')
    assert str(error) == backend._MESSAGES['adb_image_failed']
    connection = environment.sockets[1]
    assert connection.shell_handshake == b'OKAY'
    assert connection.payload_reads == expected
    assert connection.pending == b''
    assert len(environment.sockets) == 3
    assert environment.commands.count('shell:screencap -p') == 1
    environment.assert_closed()


def test_continuous_oversized_adb_response_stops_with_bounded_reads(
        environment, monkeypatch):
    """无 EOF 的持续输出在累计上限加一字节后停止并清理。"""
    backend = load_backend()
    monkeypatch.setattr(backend, '_MAX_RESPONSE_BYTES', 8)
    environment.png = b''
    environment.continuous_payload = True
    assert_capture_error(
        backend, 'adb_image_failed', purpose='cli', image_format='png')
    connection = environment.sockets[1]
    assert connection.shell_handshake == b'OKAY'
    assert connection.payload_reads == [(9, 3), (6, 3), (3, 3)]
    assert sum(size for _, size in connection.payload_reads) == 9
    assert len(environment.sockets) == 3
    assert environment.commands.count('shell:screencap -p') == 1
    environment.assert_closed()


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_limit_cleanup_preserves_control_signal(
        environment, monkeypatch, signal_type):
    """完成握手和首块 PNG 读取后，限额循环中的信号原对象传播。"""
    backend = load_backend()
    monkeypatch.setattr(backend, '_MAX_RESPONSE_BYTES', 8)
    signal = signal_type('control-signal')
    environment.payload_plan = [4, signal]
    with pytest.raises(signal_type) as caught:
        backend.capture_adb(SERIAL, purpose='cli')
    assert caught.value is signal
    connection = environment.sockets[1]
    assert connection.shell_handshake == b'OKAY'
    assert connection.payload_reads == [(9, 4), (5, None)]
    assert connection.pending == environment.raw[4:]
    assert len(environment.sockets) == 3
    environment.assert_closed()


def test_oversized_response_preserves_primary_error_during_close(
        environment, monkeypatch):
    """负载超限叠加普通关闭错误时仍保留固定图像失败主因。"""
    backend = load_backend()
    monkeypatch.setattr(backend, '_MAX_RESPONSE_BYTES', 8)
    environment.png = b'x' * 9
    environment.payload_plan = [7, 2]
    original_close = FakeSocket.close

    def close_with_error(connection):
        """仅在负载连接释放后模拟普通关闭错误。"""
        original_close(connection)
        if connection.command == 'shell:screencap -p':
            raise OSError('secret-token cleanup')

    monkeypatch.setattr(FakeSocket, 'close', close_with_error)
    error = assert_capture_error(backend, 'adb_image_failed')
    assert str(error) == backend._MESSAGES['adb_image_failed']
    connection = environment.sockets[1]
    assert connection.shell_handshake == b'OKAY'
    assert connection.payload_reads == [(9, 7), (2, 2)]
    assert len(environment.sockets) == 6
    environment.assert_closed()


@pytest.mark.parametrize('kind', [
    'empty', 'text', 'signature', 'truncated', 'no_iend', 'crc',
])
def test_invalid_png_never_becomes_placeholder(environment, kind):
    """错误文本、截断和 CRC 损坏必须失败，不能返回库的黑图占位。"""
    backend = load_backend()
    png = environment.png
    samples = {
        'empty': b'',
        'text': b'error: secret-token',
        'signature': b'\x89PNG\r\n\x1a\n',
        'truncated': png[:len(png) // 2],
        'no_iend': png[:-12],
        'crc': png[:29] + bytes([png[29] ^ 1]) + png[30:],
    }
    environment.png = samples[kind]
    assert_capture_error(backend, 'adb_image_failed', purpose='cli', image_format='png')


def test_real_black_png_is_not_rejected(environment):
    """真实完整黑图是合法截图，不与库的错误占位混淆。"""
    backend = load_backend()
    environment.png = make_png(colorful=False)
    result = backend.capture_adb(SERIAL, purpose='cli', image_format='png')
    assert result.image_bytes == environment.png


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('stage', ['connect', 'state', 'version', 'shell', 'decode'])
def test_control_signals_propagate_same_object_after_cleanup(
        environment, monkeypatch, signal_type, stage):
    """初始化、协议读取及解码控制信号均在清理后原对象传播。"""
    backend = load_backend()
    signal = signal_type('control-signal')
    environment.failure = signal
    if stage == 'connect':
        environment.connect_failure = 2
    elif stage == 'decode':
        monkeypatch.setattr(Image, 'open', Mock(side_effect=signal))
    elif stage == 'state':
        environment.read_failure = (f'host-serial:{SERIAL}:get-state',)
    elif stage == 'version':
        environment.read_failure = ('host:version',)
    elif stage == 'shell':
        environment.read_failure = ('shell:screencap',)
    with pytest.raises(signal_type) as caught:
        backend.capture_adb(SERIAL, **(
            {'purpose': 'cli', 'image_format': 'png'}
            if stage == 'decode' else {}))
    assert caught.value is signal
    environment.assert_closed()


@pytest.mark.parametrize('stage', ['connect', 'state', 'version', 'shell'])
@pytest.mark.parametrize('failure_type', [KeyboardInterrupt, SystemExit,
                                         TimeoutError])
def test_close_error_does_not_mask_primary_failure(
        environment, monkeypatch, stage, failure_type):
    """真实关闭错误不能覆盖连接或读取主因，仍清理其他已获连接。"""
    backend = load_backend()
    failure = failure_type('secret-token primary')
    environment.failure = failure
    failing_index = {'connect': 2, 'state': 0, 'version': 2, 'shell': 1}[stage]
    original_close = FakeSocket.close

    def close_with_error(connection):
        """模拟 socket 已释放但关闭操作报告普通系统错误。"""
        original_close(connection)
        if connection.index == failing_index:
            raise OSError('secret-token cleanup')

    monkeypatch.setattr(FakeSocket, 'close', close_with_error)
    if stage == 'connect':
        environment.connect_failure = failing_index
    elif stage == 'shell':
        environment.read_failure = ('shell:screencap', 'shell:screencap -p')
    else:
        environment.read_failure = {
            'state': f'host-serial:{SERIAL}:get-state',
            'version': 'host:version',
        }[stage]
    if failure_type is TimeoutError:
        assert_capture_error(
            backend, 'adb_timeout', purpose='cli', image_format='png')
    else:
        with pytest.raises(failure_type) as caught:
            backend.capture_adb(SERIAL)
        assert caught.value is failure
    environment.assert_closed()


@pytest.mark.parametrize('response', [
    b'FAIL' + block(b'secret-token'), b'OKAY000400', b'OKAY0004zzzz',
])
def test_version_response_failure_closes_pending_transport(environment, response):
    """版本查询失败时清理独立查询连接及已分配但未建立的传输。"""
    backend = load_backend()
    environment.raw_responses['host:version'] = response
    assert_capture_error(backend, 'adb_protocol_failed')
    assert len(environment.sockets) == 9
    assert 'shell:screencap -p' not in environment.commands


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_signal_during_version_close_cleans_pending_transport(
        environment, monkeypatch, signal_type):
    """关闭版本连接收到控制信号时，后续普通清理错误不覆盖它。"""
    backend = load_backend()
    signal = signal_type('control-signal')
    original_close = FakeSocket.close

    def close_with_signal(connection):
        """版本连接释放后发出信号，待清理的传输报告普通关闭错误。"""
        original_close(connection)
        if connection.index == 2:
            raise signal
        if connection.index == 1:
            raise OSError('secret-token cleanup')

    monkeypatch.setattr(FakeSocket, 'close', close_with_signal)
    with pytest.raises(signal_type) as caught:
        backend.capture_adb(SERIAL)
    assert caught.value is signal
    environment.assert_closed()


def test_capture_has_no_file_upload_or_window_side_effects(
        environment, monkeypatch):
    """保留真实解码并禁止文件、上传和任何窗口捕获副作用。"""
    backend = load_backend()
    window = import_module('modules.screenshot.window')
    requests = import_module('requests')
    Image.init()
    forbidden = Mock(side_effect=AssertionError('禁止文件上传或窗口回退'))
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(Path, 'open', forbidden)
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    monkeypatch.setattr(window, 'capture_window', forbidden)
    for name in ('ShowWindow', 'SetWindowPlacement', 'EnumWindows'):
        monkeypatch.setattr(window.win32gui, name, forbidden)
    monkeypatch.setattr(window._user32, 'PrintWindow', forbidden)
    try:
        result = backend.capture_adb(SERIAL)
        assert result.image_bytes.startswith(b'\xff\xd8')
        with Image.open(BytesIO(environment.png)) as image:
            image.load()
            assert [image.getpixel((x, y)) for y in range(2)
                    for x in range(2)] == COLORS
    finally:
        forbidden.assert_not_called()


def test_pinned_private_interface_and_factory_compatibility(environment):
    """绑定真实固定版本与继承接口，工厂必须返回项目私有连接。"""
    backend = load_backend()
    assert adbutils.__version__ == '2.12.0'
    assert issubclass(backend._SocketConnection, _adb.AdbConnection)
    assert issubclass(backend._ServerClient, adbutils.AdbClient)
    assert list(inspect.signature(_adb.AdbConnection.__init__).parameters) == [
        'self', 'host', 'port']
    assert list(inspect.signature(_adb.BaseClient.make_connection).parameters) == [
        'self', 'timeout']
    with backend._ServerClient() as client:
        connection = client.make_connection(timeout=600)
        assert isinstance(connection, backend._SocketConnection)
        assert client.device(serial=SERIAL).serial == SERIAL
        assert client.server_version() == 41
    environment.assert_closed()


def test_unverified_version_fails_before_connection(environment, monkeypatch):
    """运行时版本不匹配时不冒险使用私有 API。"""
    backend = load_backend()
    monkeypatch.setattr(adbutils, '__version__', '0.0.0-unverified')
    assert_capture_error(backend, 'adb_version_unsupported')
    assert environment.sockets == []


def test_raw_parser_accepts_rgba_header_variants():
    """raw 响应支持 12/16 字节头并保留像素。"""
    import struct

    backend = load_backend()
    pixels = bytes([11, 22, 33, 0, 44, 55, 66, 128])
    for header_size in (12, 16):
        header = struct.pack('<III', 2, 1, 1)
        if header_size == 16:
            header += struct.pack('<I', 7)
        with backend._parse_raw(header + pixels) as image:
            assert image.size == (2, 1)
            assert image.tobytes() == pixels


@pytest.mark.parametrize(('purpose', 'raw_calls'), [('automatic', 2), ('cli', 1)])
def test_timeout_policy_uses_png_fallback(monkeypatch, purpose, raw_calls):
    """CLI 超时立即回退，自动采集最多尝试两次 raw。"""
    backend = load_backend()
    png = make_png()
    read = Mock(side_effect=[backend._error('adb_timeout')] * raw_calls + [png])
    monkeypatch.setattr(backend, '_read_capture', read)
    result = backend.capture_adb(SERIAL, purpose=purpose)
    assert read.call_args_list[-1].args == (SERIAL, True)
    assert read.call_count == raw_calls + 1
    assert result.image_format == ('png' if purpose == 'cli' else 'jpeg')
    if purpose == 'cli':
        assert result.image_bytes == png


@pytest.mark.parametrize(('purpose', 'queue'), [
    ('automatic', ['fail', 'success']),
    ('cli', ['fail', 'fail', 'png']),
    ('cli', ['fail', 'timeout', 'png']),
    ('cli', ['fail', 'large', 'png']),
])
def test_raw_failure_queue_follows_retry_policy(
        monkeypatch, purpose, queue):
    """普通 raw 错误按固定次数重试或进入 PNG 回退。"""
    backend = load_backend()
    png = make_png()
    outcomes = {
        'fail': backend._error('adb_raw_invalid'),
        'timeout': backend._error('adb_timeout'),
        'large': backend.ResponseTooLarge(),
        'success': struct.pack('<III', 1, 1, 1) + bytes((1, 2, 3, 255)),
        'png': png,
    }
    read = Mock(side_effect=[outcomes[item] for item in queue])
    monkeypatch.setattr(backend, '_read_capture', read)
    result = backend.capture_adb(SERIAL, purpose=purpose)
    assert read.call_count == len(queue)
    assert result.image_format == ('jpeg' if purpose == 'automatic'
                                   else 'png')


def test_read_capture_propagates_response_too_large(environment, monkeypatch):
    """二进制超限信号从采集层原样传递。"""
    backend = load_backend()
    limit = 8
    monkeypatch.setattr(backend, '_MAX_RESPONSE_BYTES', limit)
    environment.png = b'x' * 9
    environment.payload_plan = [9]
    with pytest.raises(backend.ResponseTooLarge):
        backend._read_capture(SERIAL, False)


@pytest.mark.parametrize('purpose', ['automatic', 'cli'])
@pytest.mark.parametrize('state', [b'offline', b'unauthorized', b'bootloader'])
@pytest.mark.parametrize('failures', [1, 2])
def test_device_state_recovers_within_capture_budget(
        environment, purpose, state, failures):
    """非在线状态可在第二次 raw 或唯一 PNG 前恢复。"""
    backend = load_backend()
    environment.states = [state] * failures + [b'device']
    result = backend.capture_adb(SERIAL, purpose=purpose)
    assert environment.commands.count(f'host-serial:{SERIAL}:get-state') == (
        failures + 1)
    assert environment.commands.count('shell:screencap') == (failures == 1)
    assert environment.commands.count('shell:screencap -p') == (failures == 2)
    expected = 'png' if purpose == 'cli' and failures == 2 else 'jpeg'
    assert result.image_format == expected


@pytest.mark.parametrize('purpose', ['automatic', 'cli'])
@pytest.mark.parametrize('code', [
    'adb_not_found', 'adb_offline', 'adb_unauthorized',
    'adb_unavailable', 'adb_protocol_failed',
])
@pytest.mark.parametrize('fallback', ['timeout', 'invalid', 'large'])
def test_fallback_failure_is_final_classification(
        environment, monkeypatch, purpose, code, fallback):
    """最终错误只取唯一 PNG 阶段，不保留 raw 错误或尝试第三次 raw。"""
    backend = load_backend()
    outcomes = {
        'timeout': backend._error('adb_timeout'),
        'invalid': b'not-png',
        'large': backend.ResponseTooLarge(),
    }
    read = Mock(side_effect=[backend._error(code), backend._error(code),
                             outcomes[fallback]])
    monkeypatch.setattr(backend, '_read_capture', read)
    expected = 'adb_timeout' if fallback == 'timeout' else 'adb_image_failed'
    assert_capture_error(backend, expected, purpose=purpose)
    assert [item.args for item in read.call_args_list] == [
        (SERIAL, False), (SERIAL, False), (SERIAL, True)]


@pytest.mark.parametrize('purpose', ['automatic', 'cli'])
def test_raw_limit_immediately_uses_unique_png(environment, monkeypatch, purpose):
    """raw 超限不重试，自动最终 JPEG，CLI 保留 PNG。"""
    backend = load_backend()
    read = Mock(side_effect=[backend.ResponseTooLarge(), environment.png])
    monkeypatch.setattr(backend, '_read_capture', read)
    result = backend.capture_adb(SERIAL, purpose=purpose)
    assert [item.args for item in read.call_args_list] == [
        (SERIAL, False), (SERIAL, True)]
    assert result.image_format == ('jpeg' if purpose == 'automatic' else 'png')


@pytest.mark.parametrize(('purpose', 'image_format', 'failures', 'calls'), [
    ('automatic', 'jpeg', 1, [False, False]),
    ('automatic', 'jpeg', 2, [False, False, True]),
    ('automatic', 'jpeg', 3, [False, False, True]),
    ('cli', 'jpeg', 1, [False]),
    ('cli', 'webp', 1, [False]),
    ('cli', 'png', 0, [True]),
])
def test_encoding_failure_policy_matrix(
        environment, monkeypatch, purpose, image_format, failures, calls):
    """自动编码失败可重采及回退，CLI 编码失败终止且 PNG 不编码。"""
    backend = load_backend()
    original_encode = backend.encode_image
    encode_calls = []

    def encode_with_failures(image, target_format):
        """仅注入前若干次编码失败，其余使用实际编码器。"""
        encode_calls.append(target_format)
        if len(encode_calls) <= failures:
            raise ValueError('secret-token')
        return original_encode(image, target_format)

    read = Mock(side_effect=[environment.png if png else environment.raw
                             for png in calls])
    monkeypatch.setattr(backend, '_read_capture', read)
    monkeypatch.setattr(backend, 'encode_image', encode_with_failures)
    if failures == 3 or (purpose == 'cli' and failures):
        assert_capture_error(backend, 'adb_encode_failed', purpose=purpose,
                             image_format=image_format)
    else:
        result = backend.capture_adb(SERIAL, purpose=purpose,
                                     image_format=image_format)
        assert result.image_format == image_format
    assert [item.args for item in read.call_args_list] == [
        (SERIAL, png) for png in calls]
    assert len(encode_calls) == (0 if image_format == 'png' else len(calls))


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('stage', ['raw_parse', 'raw_encode', 'png_read',
                                  'png_decode', 'png_encode'])
def test_state_machine_preserves_control_signal_identity(
        environment, monkeypatch, signal_type, stage):
    """raw 和回退各阶段的控制信号原对象传播且不重采。"""
    backend = load_backend()
    signal = signal_type('control-signal')
    fallback = stage.startswith('png_')
    outcomes = [backend._error('adb_raw_invalid')] * 2 if fallback else []
    outcomes.append(signal if stage == 'png_read' else (
        environment.png if fallback else environment.raw))
    read = Mock(side_effect=outcomes)
    monkeypatch.setattr(backend, '_read_capture', read)
    target = {'raw_parse': '_parse_raw', 'raw_encode': 'encode_image',
              'png_decode': '_decode_png', 'png_encode': 'encode_image'}
    if stage in target:
        monkeypatch.setattr(backend, target[stage], Mock(side_effect=signal))
    with pytest.raises(signal_type) as caught:
        backend.capture_adb(SERIAL)
    assert caught.value is signal
    assert [item.args for item in read.call_args_list] == (
        [(SERIAL, False), (SERIAL, False), (SERIAL, True)] if fallback
        else [(SERIAL, False)])
