from copy import deepcopy
import json

import pytest
import requests

from modules.image_host import providers
from modules.image_host.core import ImageHostError
from test_image_host_superbed import FILENAME
from test_image_host_superbed import IMAGE
from test_image_host_superbed import SECRET
from test_image_host_superbed import URL
from test_image_host_superbed import assert_safe
from test_image_host_superbed import client
from test_image_host_superbed import endpoint
from test_image_host_superbed import payload


EXCEPTIONS = {
    'timeout': requests.exceptions.Timeout,
    'connect_timeout': requests.exceptions.ConnectTimeout,
    'read_timeout': requests.exceptions.ReadTimeout,
    'ssl': requests.exceptions.SSLError,
    'connection': requests.exceptions.ConnectionError,
    'runtime': RuntimeError,
}
FAILURES = (*EXCEPTIONS, 'http', 'json', 'content_decoding', 'structure',
            'invalid_url', 'business')


def arrange_outcome(client, index, version, outcome):
    session = client.sessions[index]
    response = client.responses[index]
    response.body = json.dumps(payload(version))
    if outcome in EXCEPTIONS:
        def fail(url, **kwargs):
            client.calls.append((url, deepcopy(kwargs)))
            raise EXCEPTIONS[outcome](SECRET + URL + 'RAW_BODY_7319')

        session.post.side_effect = fail
    elif outcome == 'http':
        response.status_code = 503
    elif outcome == 'json':
        response.body = 'RAW_BODY_7319' + SECRET + URL
    elif outcome == 'content_decoding':
        response.json.side_effect = requests.exceptions.ContentDecodingError(
            SECRET + URL + 'RAW_BODY_7319')
    elif outcome == 'structure':
        response.body = json.dumps([SECRET, URL, 'RAW_BODY_7319'])
    elif outcome == 'invalid_url':
        response.body = json.dumps(payload(version, url='file:///RAW_BODY_7319'))
    elif outcome == 'business':
        body = payload(version)
        body.update(error='RAW_BODY_7319' + SECRET + URL)
        response.body = json.dumps(body)


def assert_lifecycle(client, version, outcomes):
    assert client.queue == []
    assert client.create.call_count == len(outcomes)
    assert client.created == client.sessions
    assert [url for url, _ in client.calls] == [
        endpoint(version, domain)
        for domain in ('cc', 'cn') * (len(outcomes) // 2)
    ]
    expected_events = []
    for index, outcome in enumerate(outcomes):
        session = client.sessions[index]
        response = client.responses[index]
        session.post.assert_called_once()
        session.close.assert_called_once_with()
        assert session.closed
        expected_events.append(('create', index))
        if outcome in EXCEPTIONS:
            response.close.assert_not_called()
            response.json.assert_not_called()
            assert not response.closed
        else:
            response.close.assert_called_once_with()
            assert response.closed
            assert response.json.call_count == (0 if outcome == 'http' else 1)
            expected_events.append(('response', index))
        expected_events.append(('session', index))
    assert client.events == expected_events


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('first', FAILURES)
@pytest.mark.parametrize('second', ('success', *FAILURES))
def test_failover_cross_matrix(client, caplog, version, first, second):
    client.add(version=version)
    arrange_outcome(client, 0, version, first)
    arrange_outcome(client, 1, version, second)
    options = {'api_version': version}
    original = deepcopy(options)
    if second == 'success':
        assert providers.upload_superbed(IMAGE, FILENAME, SECRET, options) == URL
    else:
        with pytest.raises(ImageHostError) as caught:
            providers.upload_superbed(IMAGE, FILENAME, SECRET, options)
        code = 'invalid_url' if second == 'invalid_url' else 'upload_failed'
        assert_safe(caught.value, code, caplog.text)
    assert options == original
    assert_lifecycle(client, version, (first, second))


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('status', [301, 302, 303, 307, 308, 401, 429, 500])
def test_non_2xx_never_decodes_json_on_both_domains(
        client, caplog, version, status):
    """验证两版非成功响应均不解析正文且按序释放双域资源。"""
    client.responses[0].body = json.dumps(payload(version))
    client.responses[0].status_code = status
    client.add(version=version, status=status)
    options = {'api_version': version}
    original = deepcopy(options)
    with pytest.raises(ImageHostError) as caught:
        providers.upload_superbed(IMAGE, FILENAME, SECRET, options)
    assert_safe(caught.value, 'upload_failed', caplog.text)
    assert options == original
    assert_lifecycle(client, version, ('http', 'http'))
    for response in client.responses:
        response.json.assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('data', {'url': URL}),
    ('urls', [URL]),
    ('files', [{'url': URL}]),
])
def test_legacy_rejects_nested_only_url_on_both_domains(
        client, caplog, field, value):
    body = {'err': 0, field: value}
    client.responses[0].body = json.dumps(body)
    client.add(body=body)
    with pytest.raises(ImageHostError) as caught:
        providers.upload_superbed(
            IMAGE, FILENAME, SECRET, {'api_version': 'legacy'})
    assert_safe(caught.value, 'invalid_url', caplog.text)
    assert_lifecycle(client, 'legacy', ('invalid_url', 'invalid_url'))


INVALID_URLS = {
    'scheme': 'file:///RAW_BODY_7319',
    'userinfo': f'https://{SECRET}@cdn.example.com/image',
    'port': 'https://cdn.example.com:99999/image',
    'control': 'https://cdn.example.com/image\nRAW_BODY_7319',
}


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('invalid_kind,invalid_url', INVALID_URLS.items())
def test_invalid_url_on_both_domains_fails_over_safely(
        client, caplog, version, invalid_kind, invalid_url):
    for response in client.responses:
        response.body = json.dumps(payload(version, url=invalid_url))
    client.add(version=version)
    client.responses[1].body = json.dumps(payload(version, url=invalid_url))

    with pytest.raises(ImageHostError) as caught:
        providers.upload_superbed(IMAGE, FILENAME, SECRET,
                                  {'api_version': version})

    assert_safe(caught.value, 'invalid_url', caplog.text)
    if invalid_kind == 'userinfo':
        userinfo_secret = invalid_url.split('@', 1)[0].rsplit(':', 1)[-1]
        assert userinfo_secret not in caplog.text + repr(caught.value)
    assert_lifecycle(client, version, ('invalid_url', 'invalid_url'))
    assert len(client.calls) == 2
