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
        self.timeouts = []
        self.command = None

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
            self.pending.extend(b'OKAY' + block(env.state))
        elif command == 'host:version':
            self.pending.extend(b'OKAY' + block(env.version))
        elif command == f'host:tport:serial:{env.serial}':
            self.pending.extend(b'OKAY' + (1).to_bytes(8, 'little'))
        elif command == f'host:transport:{env.serial}':
            self.pending.extend(b'OKAY')
        elif command == 'shell:screencap -p':
            self.pending.extend(b'OKAY' + env.png)
        else:
            pytest.fail(f'不允许的 ADB 命令：{command!r}')

    def recv(self, count):
        """分片返回二进制，或在指定协议阶段抛出原始故障对象。"""
        assert not self.closed
        if self.command == self.environment.read_failure:
            raise self.environment.failure
        size = min(count, self.environment.chunk_size, len(self.pending))
        value = bytes(self.pending[:size])
        del self.pending[:size]
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
        self.version = b'0029'
        self.png = make_png()
        self.sockets = []
        self.addresses = []
        self.commands = []
        self.fail_responses = {}
        self.raw_responses = {}
        self.chunk_size = 3
        self.connect_failure = None
        self.setup_failure = None
        self.read_failure = None
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
    yield env
    forbidden.assert_not_called()
    env.assert_closed()


def assert_capture_error(backend, code, serial=SERIAL):
    """核对固定错误分类，禁止异常消息泄露协议或底层原文。"""
    with pytest.raises(backend.CaptureError) as caught:
        backend.capture_adb(serial)
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
    assert result.png_bytes == environment.png
    with Image.open(BytesIO(result.png_bytes)) as image:
        image.load()
        assert [image.getpixel((x, y)) for y in range(2)
                for x in range(2)] == COLORS
    transport = 'tport:serial' if int(version, 16) >= 41 else 'transport'
    assert environment.commands == [
        f'host-serial:{SERIAL}:get-state', 'host:version',
        f'host:{transport}:{SERIAL}', 'shell:screencap -p',
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
def test_unavailable_state_never_runs_shell(environment, state, code):
    """非在线状态明确失败，不能进行截图或回退。"""
    backend = load_backend()
    environment.state = state
    assert_capture_error(backend, code)
    assert environment.commands == [f'host-serial:{SERIAL}:get-state']


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
    assert_capture_error(backend, code)


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
    assert_capture_error(backend, code)
    assert len(environment.sockets) == index + 1


@pytest.mark.parametrize('index', [0, 1, 2])
def test_partial_connection_initialization_is_closed(environment, index):
    """已取得 socket 但超时初始化失败时仍关闭全部自有资源。"""
    backend = load_backend()
    environment.setup_failure = index
    assert_capture_error(backend, 'adb_unavailable')


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
    environment.read_failure = stage
    environment.failure = failure
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
    assert_capture_error(backend, 'adb_image_failed')


def test_real_black_png_is_not_rejected(environment):
    """真实完整黑图是合法截图，不与库的错误占位混淆。"""
    backend = load_backend()
    environment.png = make_png(colorful=False)
    assert backend.capture_adb(SERIAL).png_bytes == environment.png


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
    else:
        environment.read_failure = {
            'state': f'host-serial:{SERIAL}:get-state',
            'version': 'host:version',
            'shell': 'shell:screencap -p',
        }[stage]
    with pytest.raises(signal_type) as caught:
        backend.capture_adb(SERIAL)
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
    else:
        environment.read_failure = {
            'state': f'host-serial:{SERIAL}:get-state',
            'version': 'host:version',
            'shell': 'shell:screencap -p',
        }[stage]
    if failure_type is TimeoutError:
        assert_capture_error(backend, 'adb_timeout')
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
    assert len(environment.sockets) == 3
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
        assert result.png_bytes == environment.png
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
