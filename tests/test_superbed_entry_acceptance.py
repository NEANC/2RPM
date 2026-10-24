from copy import deepcopy
import json
import logging

import pytest

from modules.image_host import providers
from modules.image_host import registry
from modules.image_host.core import ImageHostError
from test_image_host_superbed import assert_safe
from test_image_host_superbed import BAD_OPTIONS
from test_image_host_superbed import client
from test_image_host_superbed import endpoint
from test_image_host_superbed import FILENAME
from test_image_host_superbed import IMAGE
from test_image_host_superbed import payload
from test_image_host_superbed import SECRET
from test_image_host_superbed import URL


@pytest.mark.parametrize('explicit_null', [False, True])
@pytest.mark.parametrize('second', [False, True])
def test_registry_null_and_missing_options(client, explicit_null, second):
    host = {'provider': 'superbed', 'token': SECRET}
    if explicit_null:
        host['options'] = None
    hosts = [host]
    before = deepcopy(hosts)
    client.responses[0].body = json.dumps(payload())
    if second:
        client.responses[0].status_code = 503
        client.add(version='v1')
    result = registry.upload_with_fallback(IMAGE, FILENAME, hosts)
    assert result.success and result.url == URL
    assert result.attempts == ('superbed',)
    assert result.failures == ()
    assert hosts == before
    assert client.create.call_count == 1 + second
    assert [url for url, _ in client.calls] == [
        endpoint('v1', domain) for domain in (['cc', 'cn'] if second else ['cc'])]
    for _, request in client.calls:
        assert request['headers'] == {'X-API-Key': SECRET}
        assert request['data'] == {}
        assert set(request['files']) == {'files'}
        assert request['files']['files'][1] is IMAGE


@pytest.mark.parametrize('options', BAD_OPTIONS + [None])
def test_adapter_invalid_options_entry_has_no_session_or_chain(
        client, caplog, options):
    before = deepcopy(options)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ImageHostError) as caught:
            providers.upload_superbed(IMAGE, FILENAME, SECRET, options)
    assert_safe(caught.value, 'invalid_options', caplog.text)
    assert options == before
    client.create.assert_not_called()
    assert client.calls == []
    assert not caplog.records


@pytest.mark.parametrize('token', [
    None, '', True, 123, [], {}, ' key', '\tkey', 'key\rvalue',
    'key\nvalue', '中文', '\ud800',
])
def test_adapter_invalid_token_entry_has_no_session_or_chain(
        client, caplog, token):
    with pytest.raises(ImageHostError) as caught:
        providers.upload_superbed(IMAGE, FILENAME, token, {})
    assert_safe(caught.value, 'invalid_token', caplog.text)
    client.create.assert_not_called()
    assert client.calls == []
    assert not caplog.records


@pytest.mark.parametrize('version', ['v1', 'legacy'])
def test_adapter_invalid_mime_entry_has_no_session_or_chain(
        client, caplog, version):
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ImageHostError) as caught:
            providers.upload_superbed(
                IMAGE, 'capture.gif', SECRET,
                {'api_version': version, SECRET: URL})
    assert_safe(caught.value, 'invalid_input', caplog.text)
    client.create.assert_not_called()
    assert client.calls == []
    assert not caplog.records
