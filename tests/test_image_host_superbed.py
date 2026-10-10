from copy import deepcopy
from inspect import signature
import json
import logging
import os
import socket
import traceback
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from modules.image_host import providers
from modules.image_host import registry
from modules.image_host.context import UploadContext
from modules.image_host.core import ImageHostError


IMAGE = b'local-synthetic-image'
FILENAME = '本地截图.png'
SECRET = 'FAKE_SUPERBED_SECRET_7319'
ENV_NAME = 'SUPERBED_LOCAL_TEST_TOKEN'
URL = 'https://cdn.example.com/image?signature=a%2Fb%3D&x=1+2'
WARNING = 'Superbed 存在不支持的其他选项，已忽略'
UUID_TEXT = '123E4567-E89B-12D3-A456-426614174000'
REAL_SESSION = requests.sessions.Session


def payload(version='v1', url=URL):
    if version == 'legacy':
        return {'err': 0, 'url': url}
    return {'success': True, 'total': 1, 'succeed': 1, 'failed': 0,
            'files': [{'url': url}]}


def endpoint(version, domain):
    path = '/api/v1/upload/' if version == 'v1' else '/upload'
    return 'https://api.superbed.' + domain + path


def upload(token=SECRET, options=None, extra_hosts=(), context=None):
    hosts = [{'provider': 'superbed', 'token': token, 'options': options}]
    return registry.upload_with_fallback(
        IMAGE, FILENAME, hosts + list(extra_hosts), context=context)


def assert_safe(error, code, text=''):
    assert type(error) is ImageHostError
    assert error.code == code
    assert error.__context__ is None
    assert error.__cause__ is None
    assert error.diagnostic is None
    assert error.stage == ''
    assert error.http_status is None
    output = ''.join(traceback.format_exception(error)) + repr(error) + text
    assert SECRET not in output
    assert URL not in output
    assert 'RAW_BODY_7319' not in output


@pytest.fixture(autouse=True)
def client(monkeypatch, tmp_path):
    state = SimpleNamespace(queue=[], sessions=[], responses=[], calls=[],
                            events=[], created=[])

    def forbidden(*args, **kwargs):
        pytest.fail('测试禁止真实网络及意外请求')

    class Response:
        def __init__(self, index, body, status):
            self.index = index
            self.body = json.dumps(body)
            self.status_code = status
            self.closed = False
            self.headers = {'Location': 'https://redirect.example.invalid/'}
            self.json = Mock(side_effect=self.decode)
            self.close = Mock(side_effect=self.release)

        def decode(self):
            assert not self.closed
            assert not state.sessions[self.index].closed
            actual = requests.Response()
            actual._content = self.body.encode('utf-8')
            actual.encoding = 'utf-8'
            return actual.json()

        def release(self):
            assert not self.closed
            self.closed = True
            state.events.append(('response', self.index))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()
            return False

    class Session:
        def __init__(self, index, response):
            self.index = index
            self.response = response
            self.closed = False
            self.acquired = False
            self.cookies = {}
            self.post = Mock(side_effect=self.send)
            self.close = Mock(side_effect=self.release)

        def send(self, url, **kwargs):
            assert not self.closed
            assert not self.cookies
            self.cookies['local'] = self.index
            state.calls.append((url, kwargs))
            self.acquired = True
            return self.response

        def release(self):
            assert not self.closed
            if self.acquired:
                assert self.response.close.call_count == 1
            self.closed = True
            state.events.append(('session', self.index))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()
            return False

    def add(body=None, status=200, version='v1'):
        index = len(state.sessions)
        response = Response(index, payload(version) if body is None else body,
                            status)
        session = Session(index, response)
        state.sessions.append(session)
        state.responses.append(response)
        state.queue.append(session)
        return session, response

    def create():
        if state.created:
            previous = state.created[-1]
            assert previous.close.call_count == 1
            if previous.acquired:
                assert previous.response.close.call_count == 1
        if not state.queue:
            pytest.fail('额外创建会话或隐藏重试')
        session = state.queue.pop(0)
        state.created.append(session)
        state.events.append(('create', session.index))
        return session

    state.add = add
    state.create = Mock(side_effect=create)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(REAL_SESSION, 'request', forbidden)
    monkeypatch.setattr(requests, 'Session', state.create)
    return state


def test_registration_and_signature():
    assert registry.UPLOADERS == {
        'catbox': providers.upload_catbox, 'wmimg': providers.upload_wmimg,
        'beeimg': providers.upload_beeimg,
        'beeimg_cn': providers.upload_beeimg_cn,
        'boltp': providers.upload_boltp, 'superbed': providers.upload_superbed,
    }
    params = signature(providers.upload_superbed)
    assert list(params.parameters) == ['image_bytes', 'filename', 'token',
                                       'options']
    assert params.return_annotation is str


@pytest.mark.parametrize('options,version', [
    (None, 'v1'), ({}, 'v1'), ({'api_version': 'v1'}, 'v1'),
    ({'api_version': 'legacy'}, 'legacy'),
])
@pytest.mark.parametrize('second', [False, True])
def test_exact_contract(client, options, version, second):
    if second:
        client.add(status=503, version=version)
    client.add(version=version)
    original = deepcopy(options)
    result = upload(options=options)
    assert result.success and result.url == URL
    assert result.attempts == ('superbed',) and result.failures == ()
    assert options == original
    domains = ['cc', 'cn'] if second else ['cc']
    assert len(client.calls) == len(domains)
    for (url, kwargs), domain in zip(client.calls, domains):
        expected = {
            'data': {} if version == 'v1' else {'token': SECRET},
            'files': {('files' if version == 'v1' else 'file'):
                      (FILENAME, IMAGE, 'image/png')},
            'timeout': (5, 15), 'verify': True,
            'allow_redirects': False, 'stream': True,
        }
        if version == 'v1':
            expected['headers'] = {'X-API-Key': SECRET}
        assert url == endpoint(version, domain)
        assert kwargs == expected
        assert next(iter(kwargs['files'].values()))[1] is IMAGE
    assert len({id(s) for s in client.created}) == len(domains)
    assert client.events == [event for i in range(len(domains)) for event in
                             [('create', i), ('response', i), ('session', i)]]


def test_standard_session_has_no_retries():
    with REAL_SESSION() as session:
        for scheme in ('http://', 'https://'):
            retry = session.get_adapter(scheme).max_retries
            assert retry.total == 0
