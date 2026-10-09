#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""通过合成窗口与 HTTP 边界验证 CLI 独立图床凭证作用域。"""

from copy import deepcopy
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


@pytest.fixture
def http_calls(monkeypatch, tmp_path):
    """拦截实际 HTTP 请求，阻止网络、账号读取并隔离本地缓存。"""
    calls = []
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))

    def forbidden(*args, **kwargs):
        """任何意外网络或账号访问均使测试立即失败。"""
        pytest.fail('禁止真实网络或账号访问')

    def request(session, method, url, **kwargs):
        """按站点返回最小合成成功响应并保留真实上传参数。"""
        calls.append((method, url, kwargs))
        response = requests.Response()
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
    monkeypatch.setattr(netrc, 'netrc', forbidden)
    monkeypatch.setattr(requests.sessions.Session, 'request', request)
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
        original = requests.sessions.Session.request

        def fail_first(session, method, url, **kwargs):
            """仅首项模拟携密异常，其余仍走真实适配器与 HTTP 替身。"""
            response = original(session, method, url, **kwargs)
            if len(http_calls) == 1:
                raise requests.ConnectionError(ENV_SECRET + DIRECT_SECRET)
            return response

        monkeypatch.setattr(requests.sessions.Session, 'request', fail_first)
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
    assert output.out.count('上传成功') == (3 if failure == 'http' else 4)
    assert output.out.count('上传失败') == (0 if failure is None else 1)
    assert ENV_SECRET not in output.out + output.err + caplog.text
    assert DIRECT_SECRET not in output.out + output.err + caplog.text
    assert not list(tmp_path.rglob('*.jpg'))
