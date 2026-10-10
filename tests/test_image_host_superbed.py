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

    def add(body=None, status=200, version='legacy'):
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
    state.add()
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
    pytest.param(None, 'v1', marks=pytest.mark.xfail(reason='生产端尚未实现 v1 契约')),
    pytest.param({}, 'v1', marks=pytest.mark.xfail(reason='生产端尚未实现 v1 契约')),
    pytest.param({'api_version': 'v1'}, 'v1',
                 marks=pytest.mark.xfail(reason='生产端尚未实现 v1 契约')),
    ({'api_version': 'legacy'}, 'legacy'),
])
def test_exact_contract_request(client, options, version):
    client.responses[0].body = json.dumps(payload(version=version))
    original = deepcopy(options)
    result = upload(options=options)
    assert result.success and result.url == URL
    assert result.attempts == ('superbed',) and result.failures == ()
    assert options == original
    expected = {
        'data': {} if version == 'v1' else {'token': SECRET},
        'files': {('files' if version == 'v1' else 'file'):
                  (FILENAME, IMAGE, 'image/png')},
        'timeout': (5, 15), 'verify': True,
        'allow_redirects': False, 'stream': True,
    }
    if version == 'v1':
        expected['headers'] = {'X-API-Key': SECRET}
    domain = 'cc' if version == 'v1' else 'cn'
    assert client.calls[0][0] == endpoint(version, domain)
    assert client.calls[0][1] == expected
    assert next(iter(expected['files'].values()))[1] is IMAGE
    assert len(client.created) == 1
    assert not client.queue
    assert client.events == [('create', 0), ('response', 0), ('session', 0)]


def test_failed_request_closes_resources_and_drains_queue(client):
    client.sessions[0].response.status_code = 503
    result = upload(options={'api_version': 'legacy'})
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert not client.queue
    assert client.sessions[0].closed
    assert client.responses[0].closed
    assert client.events == [('create', 0), ('response', 0), ('session', 0)]


@pytest.mark.parametrize('token', [None, ''])
def test_empty_credential_rejected_before_session(client, token):
    result = upload(token)
    assert not result.success
    assert result.failures[0].code == 'invalid_token'
    assert not client.created
    assert len(client.queue) == 1


@pytest.mark.parametrize('resolved', [SECRET, '${NOT_EXPANDED_AGAIN}'])
def test_environment_resolved_once(client, monkeypatch, resolved):
    monkeypatch.setenv(ENV_NAME, resolved)
    assert upload('${' + ENV_NAME + '}').success
    assert client.calls[0][1]['data'] == {'token': resolved}


@pytest.mark.parametrize('value', [None, ''])
def test_missing_environment_rejected(client, monkeypatch, value):
    if value is None:
        monkeypatch.delenv(ENV_NAME, raising=False)
    else:
        monkeypatch.setenv(ENV_NAME, value)
    result = upload('${' + ENV_NAME + '}')
    assert not result.success
    assert result.failures[0].code == 'missing_environment'
    assert not client.created


@pytest.mark.parametrize('categories', ['', '截图相册', '  原样相册  ', '123'])
def test_categories_string_preserved(client, categories):
    options = {'categories': categories}
    original = deepcopy(options)
    assert upload(options=options).success
    assert client.calls[0][1]['data'] == {
        'token': SECRET, 'categories': categories}
    assert options == original


@pytest.mark.parametrize('categories', [None, False, True, 0, 123, 1.0, [], {}])
def test_invalid_categories_type_rejected(client, categories):
    result = upload(options={'categories': categories})
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert not client.created


@pytest.mark.parametrize('permission', [
    0, True, False, None, -1, 2, '0', '1', 'private', 1.0, [], {},
])
def test_private_and_invalid_permission_rejected(client, permission):
    result = upload(options={'permission': permission})
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert not client.created


@pytest.mark.parametrize('privacy', ['private', 'public', None])
def test_unsupported_privacy_cannot_be_ignored(client, privacy):
    result = upload(options={'privacy': privacy})
    assert not result.success
    assert result.failures[0].code == 'invalid_options'
    assert not client.created


def test_unknown_nonsafety_options_emit_only_fixed_warning(client, caplog):
    options = {'album_id': 123, 'strategy_id': 456, 'token': SECRET,
               SECRET + '\nforged warning': {'url': URL}}
    original = deepcopy(options)
    with caplog.at_level(logging.WARNING):
        result = upload(options=options)
    assert result.success
    assert options == original
    assert client.calls[0][1]['data'] == {'token': SECRET}
    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (logging.WARNING, WARNING)]
    assert SECRET not in caplog.text + repr(result)
    assert URL not in caplog.text + repr(result)
    assert 'forged warning' not in caplog.text


@pytest.mark.parametrize('extra', [{}, {'message': 'local information'}])
def test_root_url_needs_no_invented_success_fields(client, extra):
    client.responses[0].body = json.dumps({'url': URL, **extra})
    result = upload()
    assert result.success and result.url == URL


@pytest.mark.parametrize('marker', [
    {'success': False}, {'status': False}, {'error': SECRET},
    {'error': True}, {'error': {'message': SECRET}},
])
def test_explicit_failure_marker_rejected(client, caplog, marker):
    client.responses[0].body = json.dumps({'url': URL, 'message': SECRET,
                                            **marker})
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert SECRET not in caplog.text + repr(result)
    assert client.responses[0].closed


@pytest.mark.parametrize('body', [None, [], True, 1, 'url'])
def test_invalid_json_structure_rejected(client, body):
    client.responses[0].body = json.dumps(body)
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert client.responses[0].closed


@pytest.mark.parametrize('body', [
    {}, {'data': {'url': URL}}, {'view_url': URL}, {'thumbnail_url': URL},
    {'files': {'status': 'Success', 'url': URL}},
])
def test_missing_root_url_never_uses_other_links(client, body):
    client.responses[0].body = json.dumps(body)
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'invalid_url'


@pytest.mark.parametrize('url', [
    None, True, 123, [], {}, '', 'ftp://example.com/a', '//example.com/a',
    'https://user:' + SECRET + '@example.com/a',
    'https://example.com:' + SECRET,
])
def test_invalid_url_uses_core_safe_error(client, caplog, url):
    client.responses[0].body = json.dumps({'url': url})
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'invalid_url'
    assert SECRET not in caplog.text + repr(result)


@pytest.mark.parametrize('body', ['', '<html>' + SECRET + '</html>',
                                  '{"url":', 'not-json-' + SECRET])
def test_bad_json_releases_resources(client, caplog, body):
    client.responses[0].body = body
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert client.responses[0].closed and client.sessions[0].closed
    assert SECRET not in repr(result) + caplog.text
    assert not caplog.records


@pytest.mark.parametrize('status', [199, 300, 301, 302, 303, 307, 308,
                                   401, 429, 500])
def test_non_2xx_fails_without_read_or_redirect(client, status):
    client.responses[0].status_code = status
    client.responses[0].body = SECRET
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert client.responses[0].json.call_count == 0
    assert client.sessions[0].post.call_count == 1
    assert client.responses[0].closed and client.sessions[0].closed


@pytest.mark.parametrize('error_type', [
    requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectionError, requests.exceptions.SSLError,
    requests.exceptions.ContentDecodingError, ValueError, RuntimeError,
])
@pytest.mark.parametrize('stage', ['post', 'json'])
def test_exception_sanitization_and_owned_resource_cleanup(
        client, caplog, error_type, stage):
    target = client.sessions[0].post if stage == 'post' else client.responses[0].json
    target.side_effect = error_type(SECRET + URL)
    result = upload()
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert client.sessions[0].closed
    assert client.responses[0].closed == (stage == 'json')
    assert SECRET not in repr(result) + caplog.text
    assert URL not in repr(result) + caplog.text


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('stage', ['post', 'json'])
def test_control_signal_identity_cleanup_and_no_fallback(
        client, signal_type, stage):
    signal = signal_type('local stop')
    target = client.sessions[0].post if stage == 'post' else client.responses[0].json
    target.side_effect = signal
    with pytest.raises(signal_type) as caught:
        upload(extra_hosts=[{'provider': 'wmimg'}])
    assert caught.value is signal
    assert client.sessions[0].closed
    assert client.responses[0].closed == (stage == 'json')


def test_signed_url_and_inputs_preserved(client, caplog):
    signed = URL + '&token=' + SECRET
    client.responses[0].body = json.dumps({'url': signed})
    hosts = [{'provider': ' SUPERBED ', 'token': SECRET,
              'options': {'permission': 1, 'categories': '本地相册'}}]
    original = deepcopy(hosts)
    result = registry.upload_with_fallback(IMAGE, FILENAME, hosts)
    assert result.success
    assert result.url == signed
    assert hosts == original
    assert SECRET not in repr(result) + str(result) + caplog.text
    assert signed not in repr(result) + str(result) + caplog.text


@pytest.mark.parametrize('failure', [None, 'http', 'business', 'json',
                                     'structure', 'url', 'options', 'token'])
def test_serial_fallback_credentials_and_first_success_stop(client, failure):
    options = {'permission': 0} if failure == 'options' else {}
    token = '' if failure == 'token' else SECRET
    first_body = {'url': URL}
    if failure == 'business':
        first_body = {'error': SECRET}
    elif failure == 'structure':
        first_body = []
    elif failure == 'url':
        first_body = {'url': 'invalid'}
    client.add()
    calls = []

    client.responses[0].status_code = 500 if failure == 'http' else 200
    client.responses[0].body = (
        'not-json' if failure == 'json' else json.dumps(first_body))
    client.responses[1].body = json.dumps({'status': True,
                                           'data': {'links': {'url': URL}}})

    def post(index, url, **kwargs):
        calls.append((url, kwargs))
        return client.responses[1] if 'wmimg.com' in url else client.responses[0]

    client.sessions[0].post.side_effect = lambda url, **kwargs: post(0, url, **kwargs)
    client.sessions[1].post.side_effect = lambda url, **kwargs: post(1, url, **kwargs)
    result = upload(token, options, [{'provider': 'wmimg',
                                      'token': 'FAKE_WMIMG_ONLY'}])
    assert result.success
    if failure is None:
        assert result.provider == 'superbed'
        assert result.attempts == ('superbed',)
        assert len(calls) == 1
    else:
        assert result.provider == 'wmimg'
        assert result.attempts == ('superbed', 'wmimg')
        assert len(result.failures) == 1
        assert len(calls) == (1 if failure in ('options', 'token') else 2)
        assert calls[-1][0] == 'https://wmimg.com/api/v1/upload'
        assert calls[-1][1]['headers']['Authorization'] == 'Bearer FAKE_WMIMG_ONLY'


def test_standard_session_has_no_retries():
    with REAL_SESSION() as session:
        for scheme in ('http://', 'https://'):
            retry = session.get_adapter(scheme).max_retries
            assert retry.total == 0
