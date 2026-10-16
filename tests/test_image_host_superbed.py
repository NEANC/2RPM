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
    if options is None:
        options = {}
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
            self.post = Mock(side_effect=self.transmit)
            self.close = Mock(side_effect=self.release)

        def prepare_request(self, request):
            return request

        def merge_environment_settings(self, url, proxies, stream, verify, cert):
            return {'stream': stream, 'verify': verify}

        def send(self, request, **kwargs):
            kwargs.update(data=request.data, files=request.files)
            if request.headers:
                kwargs['headers'] = request.headers
            return self.post(request.url, **kwargs)

        def transmit(self, url, **kwargs):
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


def test_upload_helper_defaults_to_v1(client):
    client.responses[0].body = json.dumps(payload('v1'))
    result = upload()
    assert result.success
    assert client.calls[0][0] == endpoint('v1', 'cc')


@pytest.mark.parametrize('options,version', [
    ({}, 'v1'),
    ({'api_version': 'v1'}, 'v1'),
    ({'api_version': 'legacy'}, 'legacy'),
])
@pytest.mark.parametrize('second', [False, True])
def test_exact_contract_request(client, options, version, second):
    if second:
        client.responses[0].status_code = 503
        client.add(version=version)
        client.responses[1].body = json.dumps(payload(version=version))
    else:
        client.responses[0].body = json.dumps(payload(version=version))
    original = deepcopy(options)
    result = upload(options=options)
    assert result.success and result.url == URL
    assert result.attempts == ('superbed',) and result.failures == ()
    assert options == original
    domains = ['cc', 'cn'] if second else ['cc']
    assert len(client.calls) == len(domains)
    for index, ((url, kwargs), domain) in enumerate(zip(client.calls, domains)):
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
        assert client.created[index] is client.sessions[index]
        assert client.responses[index] is client.sessions[index].response
    assert len({id(s) for s in client.created}) == len(domains)
    assert len({id(s) for s in client.sessions[:len(domains)]}) == len(domains)
    assert len({id(r) for r in client.responses[:len(domains)]}) == len(domains)
    assert not client.queue
    assert client.events == [event for index in range(len(domains)) for event in
                             [('create', index), ('response', index),
                              ('session', index)]]
    assert client.create.call_count == len(domains)


def test_failed_request_closes_resources_and_drains_queue(client):
    client.sessions[0].response.status_code = 503
    client.add(version='legacy')
    client.responses[1].status_code = 503
    result = upload(options={'api_version': 'legacy'})
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert not client.queue
    assert client.sessions[0].closed
    assert client.responses[0].closed
    assert client.events == [('create', 0), ('response', 0), ('session', 0),
                             ('create', 1), ('response', 1), ('session', 1)]


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
    client.responses[0].body = json.dumps(payload('legacy'))
    assert upload('${' + ENV_NAME + '}', {'api_version': 'legacy'}).success
    assert client.calls[0][1]['data'] == {'token': resolved}


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('resolved', [SECRET, '${NOT_EXPANDED_AGAIN}'])
def test_environment_value_is_frozen_across_domains(client, monkeypatch,
                                                     version, resolved):
    monkeypatch.setenv(ENV_NAME, resolved)
    first = client.sessions[0]
    first.response.status_code = 503
    client.add(version=version)
    original_send = first.post.side_effect

    def send(url, **kwargs):
        monkeypatch.setenv(ENV_NAME, 'CHANGED_LOCAL_VALUE')
        return original_send(url, **kwargs)

    first.post.side_effect = send
    result = upload('${' + ENV_NAME + '}', {'api_version': version})
    assert result.success
    for _, kwargs in client.calls:
        actual = (kwargs['headers']['X-API-Key'] if version == 'v1'
                  else kwargs['data']['token'])
        assert actual == resolved


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
    options = {'api_version': 'legacy', 'categories': categories}
    original = deepcopy(options)
    client.responses[0].body = json.dumps(payload('legacy'))
    assert upload(options=options).success
    assert client.calls[0][1]['data'] == {
        'token': SECRET, 'categories': categories}
    assert options == original


@pytest.mark.parametrize('categories', [None, False, True, 0, 123, 1.0, [], {}])
def test_invalid_categories_type_rejected(client, categories):
    result = upload(options={'api_version': 'legacy', 'categories': categories})
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
    options = {'api_version': 'legacy', 'album_id': 123, 'strategy_id': 456,
               'token': SECRET,
               SECRET + '\nforged warning': {'url': URL}}
    original = deepcopy(options)
    client.responses[0].body = json.dumps(payload('legacy'))
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
    client.add(version='legacy')
    client.responses[1].body = client.responses[0].body
    result = upload(options={'api_version': 'legacy'})
    assert not result.success
    assert result.failures[0].code == 'upload_failed'


@pytest.mark.parametrize('marker', [
    {'success': False}, {'status': False}, {'error': SECRET},
    {'error': True}, {'error': {'message': SECRET}},
])
def test_explicit_failure_marker_rejected(client, caplog, marker):
    client.responses[0].body = json.dumps({'url': URL, 'message': SECRET,
                                            **marker})
    client.add(version='legacy')
    client.responses[1].body = client.responses[0].body
    result = upload(options={'api_version': 'legacy'})
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert SECRET not in caplog.text + repr(result)
    assert client.responses[0].closed


@pytest.mark.parametrize('body', [None, [], True, 1, 'url'])
def test_invalid_json_structure_rejected(client, body):
    client.responses[0].body = json.dumps(body)
    client.add(version='v1')
    client.responses[1].body = client.responses[0].body
    result = upload(options={'api_version': 'v1'})
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert client.responses[0].closed


@pytest.mark.parametrize('body', [
    {}, {'data': {'url': URL}}, {'view_url': URL}, {'thumbnail_url': URL},
    {'files': {'status': 'Success', 'url': URL}},
])
def test_missing_root_url_never_uses_other_links(client, body):
    client.responses[0].body = json.dumps(body)
    client.add(version='legacy')
    client.responses[1].body = client.responses[0].body
    result = upload(options={'api_version': 'legacy'})
    assert not result.success
    assert result.failures[0].code == 'upload_failed'


@pytest.mark.parametrize('url', [
    None, True, 123, [], {}, '', 'ftp://example.com/a', '//example.com/a',
    'https://user:' + SECRET + '@example.com/a',
    'https://example.com:' + SECRET,
])
def test_invalid_url_uses_core_safe_error(client, caplog, url):
    client.responses[0].body = json.dumps(payload(version='legacy', url=url))
    client.add(version='legacy')
    client.responses[1].body = client.responses[0].body
    result = upload(options={'api_version': 'legacy'})
    assert not result.success
    assert result.failures[0].code == 'invalid_url'
    assert SECRET not in caplog.text + repr(result)


def test_direct_parser_invalid_url_returns_safe_result():
    assert providers._parse_superbed(
        'legacy', {'err': 0, 'url': 'invalid'}) == (None, 'invalid_url')


@pytest.mark.parametrize('body', ['', '<html>' + SECRET + '</html>',
                                  '{"url":', 'not-json-' + SECRET])
def test_bad_json_releases_resources(client, caplog, body):
    client.responses[0].body = body
    client.add(version='v1')
    client.responses[1].body = client.responses[0].body
    result = upload(options={'api_version': 'v1'})
    assert not result.success
    assert result.failures[0].code == 'upload_failed'
    assert client.responses[0].closed and client.sessions[0].closed
    assert SECRET not in repr(result) + caplog.text
    assert not caplog.records


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('stage', ['post', 'json', 'parse'])
@pytest.mark.parametrize('code', [
    'invalid_url', 'upload_failed', 'invalid_options', 'invalid_token', 'business_rejected',
    SECRET, None, [], {}, True, 1,
])
def test_untrusted_error_code_matrix(client, monkeypatch, caplog,
                                     version, stage, code):
    client.responses[0].body = json.dumps(payload(version))
    client.add(version=version)
    error = ImageHostError('upload_failed', '')
    error.code = code
    error.diagnostic = SECRET + URL + 'RAW_BODY_7319'
    if stage == 'parse':
        monkeypatch.setattr(providers, '_parse_superbed',
                            Mock(side_effect=error))
    else:
        for session, response in zip(client.sessions, client.responses):
            target = session.post if stage == 'post' else response.json
            target.side_effect = error
    with pytest.raises(ImageHostError) as caught:
        providers.upload_superbed(
            IMAGE, FILENAME, SECRET, {'api_version': version})
    assert_safe(caught.value, 'upload_failed', caplog.text)
    assert client.create.call_count == 2
    assert all(session.close.call_count == 1 for session in client.sessions)
    assert not caplog.records


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('last_code', ['upload_failed', 'invalid_url'])
def test_final_code_uses_last_domain_boundary_result(client, version, last_code):
    client.responses[0].body = json.dumps(payload(version, 'invalid'))
    body = (payload(version, 'invalid') if last_code == 'invalid_url'
            else {'error': 'RAW_BODY_7319'})
    client.add(body=body, version=version)
    with pytest.raises(ImageHostError) as caught:
        providers.upload_superbed(
            IMAGE, FILENAME, SECRET, {'api_version': version})
    assert_safe(caught.value, last_code)
    assert client.create.call_count == 2


@pytest.mark.parametrize('status', [199, 300, 301, 302, 303, 307, 308,
                                   401, 429, 500])
def test_non_2xx_fails_without_read_or_redirect(client, status):
    client.responses[0].status_code = status
    client.responses[0].body = SECRET
    client.add(version='v1')
    client.responses[1].status_code = status
    result = upload(options={'api_version': 'v1'})
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
    client.add(version='v1')
    client.sessions[1].post.side_effect = error_type(SECRET + URL)
    result = upload(options={'api_version': 'v1'})
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
    client.responses[0].body = json.dumps({'err': 0, 'url': signed})
    hosts = [{'provider': ' SUPERBED ', 'token': SECRET,
              'options': {'api_version': 'legacy', 'permission': 1,
                          'categories': '本地相册'}}]
    original = deepcopy(hosts)
    client.add(version='legacy')
    result = registry.upload_with_fallback(IMAGE, FILENAME, hosts)
    assert result.success
    assert result.url == signed
    assert hosts == original
    assert SECRET not in repr(result) + str(result) + caplog.text
    assert signed not in repr(result) + str(result) + caplog.text


@pytest.mark.parametrize('failure', [None, 'http', 'business', 'json',
                                     'structure', 'url', 'options', 'token'])
def test_serial_fallback_credentials_and_first_success_stop(client, failure):
    options = {'api_version': 'legacy'}
    if failure == 'options':
        options['permission'] = 0
    token = '' if failure == 'token' else SECRET
    first_body = {'url': URL}
    if failure == 'business':
        first_body = {'error': SECRET}
    elif failure == 'structure':
        first_body = []
    elif failure == 'url':
        first_body = {'url': 'invalid'}
    if failure is None:
        first_body = {'err': 0, 'url': URL}
    if failure in ('options', 'token'):
        client.add(version='legacy')
    elif failure is not None:
        client.add(version='legacy')
        client.add(version='legacy')
    calls = []

    client.responses[0].status_code = 500 if failure == 'http' else 200
    client.responses[0].body = (
        'not-json' if failure == 'json' else json.dumps(first_body))
    if failure in ('options', 'token'):
        client.responses[0].body = json.dumps({
            'status': True, 'data': {'links': {'url': URL}}})
    if failure not in (None, 'options', 'token'):
        client.responses[1].body = client.responses[0].body
        client.responses[1].status_code = client.responses[0].status_code
    if failure is not None:
        client.responses[-1].body = json.dumps({
            'status': True, 'data': {'links': {'url': URL}}})
    result = upload(token, options, [{'provider': 'wmimg',
                                      'token': 'FAKE_WMIMG_ONLY'}])
    assert result.success
    if failure is None:
        assert result.provider == 'superbed'
        assert result.attempts == ('superbed',)
        assert len(client.calls) == 1
    else:
        assert result.provider == 'wmimg'
        assert result.attempts == ('superbed', 'wmimg')
        assert len(result.failures) == 1
        assert len(client.calls) == (1 if failure in ('options', 'token') else 3)
        assert client.calls[-1][0] == 'https://wmimg.com/api/v1/upload'
        assert client.calls[-1][1]['headers']['Authorization'] == 'Bearer FAKE_WMIMG_ONLY'


@pytest.mark.parametrize('version', ['v1', 'legacy'])
def test_real_registry_success_stops_after_superbed(client, monkeypatch, version):
    later = Mock(side_effect=AssertionError('不应到达后续图床'))
    monkeypatch.setitem(registry.UPLOADERS, 'later', later)
    client.responses[0].body = json.dumps(payload(version))
    hosts = [
        {'provider': 'superbed', 'token': SECRET,
         'options': {'api_version': version}},
        {'provider': 'later'},
    ]
    result = registry.upload_with_fallback(IMAGE, FILENAME, hosts)
    assert result.success
    assert result.provider == 'superbed'
    assert result.attempts == ('superbed',)
    assert result.failures == ()
    later.assert_not_called()


@pytest.mark.parametrize('version', ['v1', 'legacy'])
def test_real_registry_uses_wmimg_after_both_domains_fail(client, version):
    for response in client.responses:
        response.status_code = 500
    client.add(status=500, version=version)
    client.add(status=200, body={
        'status': True, 'data': {'links': {'url': URL}}})
    result = upload(options={'api_version': version}, extra_hosts=[
        {'provider': 'wmimg', 'token': 'FAKE_WMIMG_ONLY'},
    ])
    assert result.success
    assert result.provider == 'wmimg'
    assert result.attempts == ('superbed', 'wmimg')
    assert len(result.failures) == 1
    assert result.failures[0].provider == 'superbed'
    assert result.failures[0].code == 'upload_failed'
    assert len(client.calls) == 3
    assert client.calls[-1][0] == providers.WMIMG_ENDPOINT
    assert client.calls[-1][1]['headers']['Authorization'] == (
        'Bearer FAKE_WMIMG_ONLY')


@pytest.mark.parametrize('version', ['v1', 'legacy'])
def test_registry_budget_restarts_from_cc_on_next_call(client, version):
    client.responses[0].status_code = 500
    for status in (500, 500, 200):
        client.add(status=status, version=version)
    hosts = [{'provider': 'superbed', 'token': SECRET,
              'options': {'api_version': version}}]
    first = registry.upload_with_fallback(IMAGE, FILENAME, hosts)
    second = registry.upload_with_fallback(IMAGE, FILENAME, hosts)
    assert not first.success
    assert second.success
    assert first.attempts == second.attempts == ('superbed',)
    assert len(first.failures) == 1
    assert second.failures == ()
    assert [url for url, _ in client.calls] == [
        endpoint(version, domain) for domain in ('cc', 'cn', 'cc', 'cn')]


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('stage', ['post', 'json'])
def test_registry_control_signal_does_not_reach_next_provider(
        client, monkeypatch, version, stage):
    signal = KeyboardInterrupt('local stop')
    client.responses[0].status_code = 503
    second, response = client.add(version=version)
    target = second.post if stage == 'post' else response.json
    target.side_effect = signal
    later = Mock(side_effect=AssertionError('不应到达后续图床'))
    monkeypatch.setitem(registry.UPLOADERS, 'later', later)
    with pytest.raises(KeyboardInterrupt) as caught:
        upload(options={'api_version': version}, extra_hosts=[
            {'provider': 'later'},
        ])
    assert caught.value is signal
    assert len(client.created) == 2
    assert second.close.call_count == 1
    assert response.close.call_count == int(stage == 'json')
    later.assert_not_called()


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('domain', [0, 1])
@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit,
                                        GeneratorExit])
@pytest.mark.parametrize('stage', ['post', 'json', 'parse'])
@pytest.mark.parametrize('cleanup', ['response', 'session', 'both'])
def test_cleanup_preserves_control_signal_matrix(
        client, monkeypatch, version, domain, signal_type, stage, cleanup):
    client.responses[0].body = json.dumps(payload(version))
    if domain:
        client.responses[0].status_code = 503
        client.add(version=version)
    session = client.sessions[domain]
    response = client.responses[domain]
    signal = signal_type('local stop')
    if stage == 'parse':
        monkeypatch.setattr(providers, '_parse_superbed',
                            Mock(side_effect=signal))
    else:
        target = session.post if stage == 'post' else response.json
        target.side_effect = signal
    if cleanup in ('response', 'both'):
        response.close.side_effect = RuntimeError('RAW_BODY_7319')
    if cleanup in ('session', 'both'):
        session.close.side_effect = RuntimeError(SECRET)
    later = Mock(return_value=URL)
    monkeypatch.setitem(registry.UPLOADERS, 'later', later)
    with pytest.raises(BaseException) as caught:
        upload(options={'api_version': version},
               extra_hosts=[{'provider': 'later'}])
    assert caught.value is signal
    assert len(client.created) == domain + 1
    assert session.post.call_count == 1
    assert response.close.call_count == int(stage != 'post')
    assert session.close.call_count == 1
    later.assert_not_called()


def test_standard_session_has_no_retries():
    with REAL_SESSION() as session:
        for scheme in ('http://', 'https://'):
            retry = session.get_adapter(scheme).max_retries
            assert retry.total == 0


BAD_OPTIONS = [
    {'api_version': value} for value in
    (None, '', 'V1', 'Legacy', ' v1', 'v1 ', True, 1, [], {})
] + [
    {'folder_id': value} for value in
    (None, '', 'bad', ' 123e4567-e89b-12d3-a456-426614174000 ',
     True, 1, [], {})
] + [
    {'api_version': 'legacy', 'categories': value}
    for value in (None, False, True, 0, 123, 1.0, [], {})
] + [
    {'permission': value}
    for value in (0, True, False, None, -1, 2, '1', 1.0, [], {})
] + [
    {'privacy': value} for value in (None, '', False, [], {}, 'public')
] + [
    {'categories': value} for value in (None, '', [], {}, 'a/b')
] + [
    {'api_version': 'legacy', 'folder_id': value}
    for value in (None, '', [], {}, UUID_TEXT)
] + [
    {'folder_id': UUID_TEXT, 'categories': ''},
    {'api_version': 'legacy', 'folder_id': UUID_TEXT, 'categories': ''},
]


@pytest.mark.parametrize('options', BAD_OPTIONS)
def test_prepare_invalid_options(client, options):
    before = deepcopy(options)
    assert providers._prepare_superbed(SECRET, options, FILENAME) == (
        None, 'invalid_options')
    assert options == before
    client.create.assert_not_called()


@pytest.mark.parametrize('token', [None, '', True, 123, [], {}, ' key',
                                   '\tkey', 'key\rvalue', 'key\nvalue',
                                   '中文', '\ud800'])
def test_prepare_invalid_token(client, token):
    assert providers._prepare_superbed(token, {}, FILENAME) == (
        None, 'invalid_token')
    client.create.assert_not_called()


@pytest.mark.parametrize('folder', [UUID_TEXT, UUID_TEXT.lower(),
                                    UUID_TEXT.replace('-', ''),
                                    '{' + UUID_TEXT + '}'])
def test_prepare_uuid_preserves_original(client, folder):
    options = {'folder_id': folder}
    before = deepcopy(options)
    materials, code = providers._prepare_superbed(SECRET, options, FILENAME)
    assert code is None
    assert materials == ('v1', {'X-API-Key': SECRET}, {'folder_id': folder},
                         'files', 'image/png')
    assert options == before
    client.create.assert_not_called()


@pytest.mark.parametrize('categories', ['', '截图相册', '  a/b/c  ', '123'])
@pytest.mark.parametrize('token', [SECRET, '  key  ', '中文'])
def test_prepare_legacy_preserves_strings(client, categories, token):
    options = {'api_version': 'legacy', 'categories': categories}
    materials, code = providers._prepare_superbed(token, options, FILENAME)
    assert code is None
    assert materials == ('legacy', None,
                         {'token': token, 'categories': categories},
                         'file', 'image/png')
    client.create.assert_not_called()


@pytest.mark.parametrize('token', ['key ', 'clé'])
def test_prepare_valid_header_without_trimming(client, token):
    materials, code = providers._prepare_superbed(token, {}, FILENAME)
    assert code is None
    assert materials[1] == {'X-API-Key': token}


def test_prepare_mime_and_warning_order(client, caplog):
    options = {SECRET: {'nested': URL}}
    with caplog.at_level(logging.WARNING):
        assert providers._prepare_superbed(SECRET, options, 'x.gif') == (
            None, 'invalid_input')
    assert not caplog.records
    client.create.assert_not_called()


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('stage', [
    'session', 'post', 'json', 'response_close', 'session_close',
])
def test_lifecycle_cannot_inject_invalid_url(client, caplog, version, stage):
    client.responses[0].status_code = 503
    session, response = client.add(version=version)
    error = ImageHostError('invalid_url', '')
    error.diagnostic = SECRET + URL + 'RAW_BODY_7319'
    if stage == 'session':
        client.create.side_effect = [client.sessions[0], error]
    else:
        target = {'post': session.post, 'json': response.json,
                  'response_close': response.close,
                  'session_close': session.close}[stage]
        target.side_effect = error
    with pytest.raises(ImageHostError) as caught:
        providers.upload_superbed(
            IMAGE, FILENAME, SECRET, {'api_version': version})
    assert_safe(caught.value, 'upload_failed', caplog.text)
    assert client.create.call_count == 2
    if stage != 'session':
        assert session.close.call_count == 1
        assert response.close.call_count == int(stage != 'post')
    assert not caplog.records


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('error_type', [Exception, ValueError, RuntimeError,
                                       ImageHostError])
def test_validator_exception_is_always_invalid_url(
        client, monkeypatch, caplog, version, error_type):
    client.responses[0].body = json.dumps(payload(version))
    client.add(version=version)
    error = (ImageHostError('invalid_options', '')
             if error_type is ImageHostError
             else error_type(SECRET + URL + 'RAW_BODY_7319'))
    validator = Mock(side_effect=error)
    monkeypatch.setattr(providers, 'validate_image_url', validator)
    with pytest.raises(ImageHostError) as caught:
        providers.upload_superbed(
            IMAGE, FILENAME, SECRET, {'api_version': version})
    assert_safe(caught.value, 'invalid_url', caplog.text)
    assert validator.call_count == client.create.call_count == 2
    assert all(s.close.call_count == 1 for s in client.sessions)
    assert all(r.close.call_count == 1 for r in client.responses)
    assert not caplog.records


@pytest.mark.parametrize('body', [
    {'success': 1, 'total': 1, 'succeed': 1, 'failed': 0,
     'files': [{'url': URL}]},
    {'success': True, 'total': True, 'succeed': 1, 'failed': 0,
     'files': [{'url': URL}]},
    {'success': True, 'total': 1, 'succeed': 1, 'failed': 0,
     'files': [{'url': URL}, {'url': URL}]},
])
def test_parse_v1_requires_exact_contract(body):
    assert providers._parse_superbed('v1', body) == (None, 'upload_failed')


@pytest.mark.parametrize('marker', [
    {'success': False}, {'status': False}, {'error': SECRET},
    {'error': True}, {'error': {'message': SECRET}},
])
def test_parse_v1_rejects_explicit_failure_markers(marker):
    body = payload()
    body.update(marker)
    assert providers._parse_superbed('v1', body) == (None, 'upload_failed')


def test_parse_v1_accepts_exact_contract():
    assert providers._parse_superbed('v1', payload()) == (URL, None)


def test_parse_legacy_requires_strict_zero_error():
    for body in ({'url': URL}, {'err': False, 'url': URL},
                 {'err': 0.0, 'url': URL}):
        assert providers._parse_superbed('legacy', body) == (
            None, 'upload_failed')


def test_parse_explicit_failure_precedes_legacy_success():
    assert providers._parse_superbed(
        'legacy', {'err': 0, 'success': False, 'url': URL}) == (
            None, 'upload_failed')


def test_legacy_tries_cc_then_cn(client):
    client.responses[0].status_code = 503
    client.add(version='legacy')
    client.responses[1].body = json.dumps(payload(version='legacy'))
    result = upload(options={'api_version': 'legacy'})
    assert result.success
    assert [call[0] for call in client.calls] == [
        endpoint('legacy', 'cc'), endpoint('legacy', 'cn')]


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('stage', [
    'session', 'post', 'json', 'http', 'structure', 'url',
    'response_close', 'session_close', 'both_close',
])
def test_final_exception_has_no_context_matrix(client, caplog, version, stage):
    client.responses[0].status_code = 503
    session, response = client.add(version=version)
    raw = RuntimeError(SECRET + URL + 'RAW_BODY_7319')
    if stage == 'session':
        client.create.side_effect = [client.sessions[0], raw]
    elif stage == 'post':
        session.post.side_effect = requests.exceptions.ReadTimeout(str(raw))
    elif stage == 'json':
        response.json.side_effect = raw
    elif stage == 'http':
        response.status_code = 503
    elif stage == 'structure':
        response.body = json.dumps({'error': str(raw)})
    elif stage == 'url':
        response.body = json.dumps(payload(version, 'invalid'))
    else:
        if stage in ('response_close', 'both_close'):
            response.close.side_effect = raw
        if stage in ('session_close', 'both_close'):
            session.close.side_effect = raw
    with pytest.raises(ImageHostError) as caught:
        providers.upload_superbed(
            IMAGE, FILENAME, SECRET, {'api_version': version})
    expected = 'invalid_url' if stage == 'url' else 'upload_failed'
    assert_safe(caught.value, expected, caplog.text)
    assert client.create.call_count == 2
    assert client.sessions[0].closed and client.responses[0].closed
    if stage != 'session':
        assert session.post.call_count == 1
        assert session.close.call_count == 1
        assert response.close.call_count == int(stage != 'post')
    assert not caplog.records
