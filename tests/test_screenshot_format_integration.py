#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证图床格式矩阵及来源到本地文件和 HTTP 的真实字节链路。"""

from io import BytesIO
import json
import struct
from unittest.mock import Mock

from PIL import Image
import pytest
import requests

from modules.image_host import providers
from modules.screenshot import adb
from modules.screenshot import cli
from modules.screenshot import window
from modules.screenshot.models import CaptureError
from test_screenshot_cli_host_integration import http_calls
from test_screenshot_cli_host_integration import URL
from test_screenshot_window import TITLE
from test_screenshot_window import WindowEnvironment


@pytest.mark.parametrize('provider', [
    'wmimg', 'beeimg', 'beeimg_cn', 'boltp', 'superbed'])
@pytest.mark.parametrize('suffix,mime', [
    ('jpg', 'image/jpeg'), ('png', 'image/png'), ('webp', 'image/webp')])
def test_remaining_providers_multipart_contract(
        monkeypatch, http_calls, provider, suffix, mime):
    """每个适配器独立断言后缀、固定 MIME 以及同一字节对象。"""
    original = requests.sessions.Session.request

    def response_for_provider(session, method, url, **kwargs):
        """只替换 HTTP 响应，同时保留实际 v2 流式 JSON 解码。"""
        response = original(session, method, url, **kwargs)
        payload = {
            'status': 'success' if provider in ('beeimg_cn', 'boltp') else True,
            'data': {'links': {'url': URL}, 'public_url': URL},
            'files': {'status': 'Success', 'url': URL}, 'url': URL,
        }
        response._content = json.dumps(payload).encode()
        return response

    monkeypatch.setattr(requests.sessions.Session, 'request', response_for_provider)
    data = bytes(bytearray(b'format-contract-payload'))
    filename = 'capture.' + suffix
    uploader = getattr(providers, 'upload_' + provider)
    assert uploader(data, filename, 'SYNTHETIC_TOKEN', {'storage_id': 1}
                    if provider in ('beeimg_cn', 'boltp') else {}) == URL
    assert len(http_calls) == 1
    part = http_calls[0][2]['files']['file']
    assert part[0] == filename
    assert part[1] is data
    assert part[2] == mime


@pytest.mark.parametrize('source', ['window', 'adb'])
@pytest.mark.parametrize('suffix,format_name,mime', [
    ('jpg', 'JPEG', 'image/jpeg'), ('png', 'PNG', 'image/png'),
    ('webp', 'WEBP', 'image/webp')])
def test_cli_source_encoding_save_upload_shared_bytes(
        monkeypatch, tmp_path, http_calls, source, suffix, format_name, mime):
    """真实来源解析和编码后，保存调用与 HTTP multipart 共享同一载荷。"""
    if source == 'window':
        WindowEnvironment(window, monkeypatch)
    else:
        buffer = BytesIO()
        Image.new('RGBA', (2, 2), (30, 70, 120, 255)).save(buffer, format='PNG')
        raw = struct.pack('<III', 2, 2, 1) + bytes([30, 70, 120, 255]) * 4
        read = Mock(return_value=buffer.getvalue() if suffix == 'png' else raw)
        monkeypatch.setattr(adb, '_read_capture', read)
    saved = []
    original = cli.save_cli

    def save(result, *args, **kwargs):
        """记录字节身份，同时执行真实排他文件写入。"""
        saved.append(result.image_bytes)
        return original(result, *args, **kwargs)

    monkeypatch.setattr(cli, 'save_cli', save)
    target = tmp_path / ('capture.' + suffix)
    args = cli.parse_screenshot_args([
        '--source', source + ':' + TITLE, '--output', str(target),
        '--upload', 'provider: catbox'])
    assert cli.run_screenshot_cli(args, str(tmp_path)) == 0
    assert len(saved) == len(http_calls) == 1
    part = http_calls[0][2]['files']['fileToUpload']
    assert part[0] == target.name
    assert part[1] is saved[0]
    assert part[2] == mime
    assert target.read_bytes() == part[1]
    with Image.open(BytesIO(part[1])) as image:
        assert image.format == format_name
        assert image.size == (2, 2)
        image.load()
    if source == 'adb':
        read.assert_called_once_with(TITLE, suffix == 'png')


@pytest.mark.parametrize('save_conflict', [False, True])
def test_adb_fallback_actual_and_failed_candidate_names(
        monkeypatch, tmp_path, http_calls, save_conflict):
    """raw 超时回退 PNG，真实保存及排他冲突均上传实际 PNG 候选名。"""
    stream = BytesIO()
    Image.new('RGB', (2, 3), (30, 70, 120)).save(stream, format='PNG')
    png = stream.getvalue()
    read = Mock(side_effect=[CaptureError('adb_timeout', ''), png])
    monkeypatch.setattr(adb, '_read_capture', read)
    actual = tmp_path / 'fallback.png'
    if save_conflict:
        actual.write_bytes(b'occupied')
    args = cli.parse_screenshot_args([
        '--source', 'adb:synthetic', '--output', str(tmp_path / 'fallback.webp'),
        '--upload', 'provider: catbox'])
    assert cli.run_screenshot_cli(args, str(tmp_path)) == 0
    assert [call.args for call in read.call_args_list] == [
        ('synthetic', False), ('synthetic', True)]
    part = http_calls[0][2]['files']['fileToUpload']
    assert part[0] == 'fallback.png'
    assert part[1] is png
    assert part[2] == 'image/png'
    assert actual.read_bytes() == (b'occupied' if save_conflict else png)
    assert not (tmp_path / 'fallback.webp').exists()
