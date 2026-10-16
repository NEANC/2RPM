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


@pytest.mark.parametrize('provider,options,version,endpoint,file_key,auth', [
    ('catbox', {}, None, providers.CATBOX_ENDPOINT, 'fileToUpload',
     ('data', 'reqtype', 'fileupload')),
    ('wmimg', {}, None, providers.WMIMG_ENDPOINT, 'file',
     ('headers', 'Authorization', 'Bearer SYNTHETIC_TOKEN')),
    ('beeimg', {}, None, providers.BEEIMG_ENDPOINT, 'file',
     ('data', 'apikey', 'SYNTHETIC_TOKEN')),
    ('beeimg_cn', {'storage_id': 1}, None,
     'https://www.beeimg.cn/api/v2/upload', 'file',
     ('headers', 'Authorization', 'Bearer SYNTHETIC_TOKEN')),
    ('boltp', {'storage_id': 1}, None,
     'https://www.boltp.com/api/v2/upload', 'file',
     ('headers', 'Authorization', 'Bearer SYNTHETIC_TOKEN')),
    ('superbed', {}, 'v1',
     'https://api.superbed.cc/api/v1/upload/', 'files',
     ('headers', 'X-API-Key', 'SYNTHETIC_TOKEN')),
    ('superbed', {'api_version': 'legacy'}, 'legacy',
     'https://api.superbed.cc/upload', 'file',
     ('data', 'token', 'SYNTHETIC_TOKEN'))])
@pytest.mark.parametrize('suffix,mime', [
    ('jpg', 'image/jpeg'), ('jpeg', 'image/jpeg'),
    ('png', 'image/png'), ('webp', 'image/webp')])
def test_remaining_providers_multipart_contract(
        monkeypatch, http_calls, provider, options, version, endpoint, file_key,
        auth, suffix, mime):
    """每个适配器断言路径、认证、文件契约、MIME 和同一字节对象。"""
    original = requests.sessions.Session.request
    original_prepare = requests.sessions.Session.prepare_request

    def response_for_provider(session, method, url, **kwargs):
        """只替换 HTTP 响应，同时保留实际 v2 流式 JSON 解码。"""
        response = original(session, method, url, **kwargs)
        if provider == 'catbox':
            response._content = URL.encode()
            return response
        payload = {
            'status': 'success' if provider in ('beeimg_cn', 'boltp') else True,
            'data': {'links': {'url': URL}, 'public_url': URL},
            'files': {'status': 'Success', 'url': URL}, 'url': URL,
        }
        if provider == 'superbed' and version == 'v1':
            payload = {
                'success': True, 'total': 1, 'succeed': 1, 'failed': 0,
                'files': [{'url': URL}],
            }
        elif provider == 'superbed':
            payload = {'err': 0, 'url': URL}
        response._content = json.dumps(payload).encode()
        return response

    if provider in ('beeimg_cn', 'boltp'):
        def prepare(session, request):
            """在真实准备前记录 v2 请求的原始 multipart 参数。"""
            http_calls.append((request.method, request.url, {
                'headers': request.headers, 'data': request.data,
                'files': request.files}))
            return original_prepare(session, request)

        def send(adapter, request, **kwargs):
            """在适配器边界返回 v2 合成响应。"""
            response = requests.Response()
            response.request = request
            response.url = request.url
            response.status_code = 200
            response._content = json.dumps({
                'status': 'success',
                'data': {'public_url': URL},
            }).encode()
            response._content_consumed = True
            return response

        monkeypatch.setattr(requests.sessions.Session, 'prepare_request', prepare)
        monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    else:
        monkeypatch.setattr(
            requests.sessions.Session, 'request', response_for_provider)
    data = bytes(bytearray(b'format-contract-payload'))
    filename = 'capture.' + suffix
    uploader = getattr(providers, 'upload_' + provider)
    assert uploader(data, filename, 'SYNTHETIC_TOKEN', options) == URL
    assert len(http_calls) == 1
    part = http_calls[0][2]['files'][file_key]
    assert http_calls[0][1] == endpoint
    assert part[0] == filename
    assert part[1] is data
    assert part[2] == mime
    auth_location, auth_name, auth_value = auth
    assert http_calls[0][2][auth_location][auth_name] == auth_value
    if provider == 'catbox':
        assert http_calls[0][2]['data']['userhash'] == 'SYNTHETIC_TOKEN'


@pytest.mark.parametrize('source', ['window', 'adb'])
@pytest.mark.parametrize('suffix,format_name,mime', [
    ('jpg', 'JPEG', 'image/jpeg'), ('jpeg', 'JPEG', 'image/jpeg'),
    ('png', 'PNG', 'image/png'),
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


@pytest.mark.parametrize('source', ['window', 'adb'])
def test_webp_saved_from_real_source_preserves_every_pixel(
        monkeypatch, tmp_path, http_calls, source):
    """window 和 ADB 的真实 CLI 编码保存后逐像素保留 RGBA。"""
    expected = bytes([
        255, 0, 0, 255, 0, 255, 0, 255,
        0, 0, 255, 255, 255, 255, 255, 255,
    ])
    if source == 'window':
        WindowEnvironment(window, monkeypatch)
    else:
        expected = bytes([
            17, 83, 149, 0, 31, 97, 163, 128,
            47, 113, 179, 255, 61, 127, 193, 0,
        ])
        raw = struct.pack('<III', 2, 2, 1) + expected
        read = Mock(return_value=raw)
        monkeypatch.setattr(adb, '_read_capture', read)
    target = tmp_path / 'capture.webp'
    args = cli.parse_screenshot_args([
        '--source', source + ':' + TITLE, '--output', str(target)])
    assert cli.run_screenshot_cli(args, str(tmp_path)) == 0
    saved = target.read_bytes()
    assert saved.startswith(b'RIFF')
    with Image.open(BytesIO(saved)) as image:
        image.load()
        assert image.format == 'WEBP'
        assert image.size == (2, 2)
        assert image.convert('RGBA').tobytes() == expected
    if source == 'adb':
        read.assert_called_once_with(TITLE, False)
    assert http_calls == []


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
