#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""通过合成窗口与 HTTP 边界验证 CLI 独立图床凭证作用域。"""

from copy import deepcopy
from email import policy
from email.parser import BytesParser
import json
import netrc
import socket

import pytest
import requests

from modules.screenshot import cli
from modules.screenshot import window
from test_screenshot_window import TITLE
from test_screenshot_window import WindowEnvironment


URL = 'https://cdn.example.com/integration.jpg'
ENV_SECRET = '${CLI_LITERAL_NOT_EXPANDED}'
DIRECT_SECRET = 'DIRECT_TEST_SECRET_9251'


def assert_prepared_multipart(prepared, data, files, headers):
    """核对真实编码后的字段、认证及文件字节，不以原始参数代替线上载荷。"""
    assert isinstance(prepared, requests.PreparedRequest)
    assert prepared.method == 'POST'
    for name in ('Authorization', 'X-API-Key'):
        assert prepared.headers.get(name) == headers.get(name)
    message = BytesParser(policy=policy.default).parsebytes(
        ('Content-Type: ' + prepared.headers['Content-Type']
         + '\r\n\r\n').encode() + prepared.body)
    parts = list(message.iter_parts())
    assert len(parts) == len(data) + len(files)
    actual = {}
    for part in parts:
        name = part.get_param('name', header='content-disposition')
        assert name not in actual
        actual[name] = part
    assert set(actual) == set(data) | set(files)
    for name, value in data.items():
        assert actual[name].get_payload(decode=True) == str(value).encode()
    for name, (filename, content, mime) in files.items():
        assert actual[name].get_filename() == filename
        assert actual[name].get_content_type() == mime
        assert actual[name].get_payload(decode=True) == content


@pytest.fixture
def http_calls(monkeypatch, tmp_path):
    """拦截实际 HTTP 请求，阻止网络、账号读取并隔离本地缓存。"""
    calls = []
    pending = {}
    original_prepare = requests.sessions.Session.prepare_request
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))

    def forbidden(*args, **kwargs):
        """任何意外网络或账号访问均使测试立即失败。"""
        pytest.fail('禁止真实网络或账号访问')

    def prepare(session, request):
        """保留字节对象身份并执行 Requests 的真实请求编码。"""
        prepared = original_prepare(session, request)
        pending[id(prepared)] = {
            'data': request.data, 'files': request.files,
            'headers': request.headers}
        return prepared

    def send(session, prepared, **kwargs):
        """按站点返回最小合成成功响应并保留真实上传参数。"""
        raw = pending.pop(id(prepared))
        assert_prepared_multipart(prepared, **raw)
        assert kwargs['timeout'] == (5, 15)
        assert kwargs['verify'] is True
        assert kwargs['stream'] is True
        assert kwargs['allow_redirects'] is False
        calls.append((prepared.method, prepared.url, {
            **raw, 'prepared': prepared, 'settings': kwargs}))
        url = prepared.url
        response = requests.Response()
        response.request = prepared
        response.url = url
        response.status_code = 200
        response._content_consumed = True
        if 'catbox.moe' in url:
            response._content = URL.encode()
        else:
            response._content = json.dumps({
                'url': URL, 'files': {'status': 'Success', 'url': URL},
            }).encode()
        return response

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(netrc, 'netrc', forbidden)
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', forbidden)
    monkeypatch.setattr(requests.sessions.Session, 'prepare_request', prepare)
    monkeypatch.setattr(requests.sessions.Session, 'send', send)
    monkeypatch.setattr(requests.sessions, 'get_netrc_auth', lambda url: None)
    return calls


@pytest.mark.parametrize('failure', [None, 'prepare', 'http'])
def test_cli_real_multi_host_credentials_and_aggregate_exit(
        monkeypatch, tmp_path, capsys, caplog, http_calls, failure):
    """真实准备及注册表逐项执行，凭证不二次展开、不串项且全成功才为零。"""
    WindowEnvironment(window, monkeypatch)
    monkeypatch.setenv('CLI_INTEGRATION_TOKEN', ENV_SECRET)
    monkeypatch.delenv('CLI_LITERAL_NOT_EXPANDED', raising=False)
    monkeypatch.delenv('CLI_INTEGRATION_MISSING', raising=False)
    parts = [
        'provider: catbox, token: "${CLI_INTEGRATION_TOKEN}"',
        f'provider: beeimg, token: "{DIRECT_SECRET}"',
        'provider: catbox, token: "${CLI_INTEGRATION_MISSING}"',
        'provider: catbox',
    ]
    if failure == 'prepare':
        parts.insert(1, 'provider: beeimg, token: "${CLI_INTEGRATION_MISSING}", '
                     'options: {albumid: abcde}')
    if failure == 'http':
        original = requests.sessions.Session.send

        def fail_first(session, prepared, **kwargs):
            """仅首项模拟携密异常，其余仍走真实适配器与 HTTP 替身。"""
            response = original(session, prepared, **kwargs)
            if len(http_calls) == 1:
                raise requests.ConnectionError(ENV_SECRET + DIRECT_SECRET)
            return response

        monkeypatch.setattr(requests.sessions.Session, 'send', fail_first)
    args = cli.parse_screenshot_args([
        '--source', 'window:' + TITLE, '--upload', '; '.join(parts)])
    before = deepcopy(args.hosts)
    assert cli.run_screenshot_cli(args, str(tmp_path)) == (
        0 if failure is None else 1)
    assert args.hosts == before
    assert [call[1] for call in http_calls] == [
        'https://catbox.moe/user/api.php',
        'https://beeimg.com/api/upload/file/json/',
        'https://catbox.moe/user/api.php',
        'https://catbox.moe/user/api.php',
    ]
    assert [call[2]['data'] for call in http_calls] == [
        {'reqtype': 'fileupload', 'userhash': ENV_SECRET},
        {'privacy': 'public', 'apikey': DIRECT_SECRET},
        {'reqtype': 'fileupload'}, {'reqtype': 'fileupload'},
    ]
    files = [next(iter(call[2]['files'].values())) for call in http_calls]
    assert all(part[1] is files[0][1] for part in files)
    assert all(part[2] == 'image/jpeg' for part in files)
    output = capsys.readouterr()
    result_lines = [line for line in output.out.splitlines()
                    if line.startswith(('catbox：', 'beeimg：'))]
    assert sum('上传成功' in line for line in result_lines) == (
        3 if failure == 'http' else 4)
    assert sum('上传失败' in line for line in result_lines) == (
        0 if failure is None else 1)
    assert output.out.count('截图 1：准备上传') == 1
    assert output.out.count('截图 1 / 图床 1 catbox：正在上传') == 1
    assert ENV_SECRET not in output.out + output.err + caplog.text
    assert DIRECT_SECRET not in output.out + output.err + caplog.text
    assert not list(tmp_path.rglob('*.jpg'))
