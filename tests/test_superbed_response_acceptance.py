import json

import pytest

from modules.image_host import providers
from modules.image_host.core import ImageHostError
from test_image_host_superbed import assert_safe
from test_image_host_superbed import client
from test_image_host_superbed import endpoint
from test_image_host_superbed import FILENAME
from test_image_host_superbed import IMAGE
from test_image_host_superbed import payload
from test_image_host_superbed import SECRET
from test_image_host_superbed import URL


MISSING = object()
INVALID_V1 = [
    (field, value)
    for field, expected in [('total', 1), ('succeed', 1), ('failed', 0)]
    for value in [MISSING, None, True, False, float(expected), str(expected),
                  -1, expected + 1, [], {}]
] + [
    ('success', value)
    for value in [MISSING, None, False, 1, 1.0, 'true', [], {}]
] + [
    ('files', value)
    for value in [MISSING, None, {}, {'url': URL}, 'files', True, 1, [],
                  [{'url': URL}, {'url': URL}], [None], [True], [1],
                  ['url'], [[]]]
]
INVALID_LEGACY = [MISSING, None, True, False, 0.0, '0', -1, 1, [], {}]


def assert_rejected_on_both_domains(client, body, version, code, text):
    client.responses[0].body = json.dumps(body)
    client.add(body=body, version=version)
    with pytest.raises(ImageHostError) as caught:
        providers.upload_superbed(
            IMAGE, FILENAME, SECRET, {'api_version': version})
    assert_safe(caught.value, code, text)
    assert client.create.call_count == 2
    assert [url for url, _ in client.calls] == [
        endpoint(version, 'cc'), endpoint(version, 'cn')]
    assert not client.queue
    assert client.events == [event for index in range(2) for event in
                             [('create', index), ('response', index),
                              ('session', index)]]


@pytest.mark.parametrize('field,value', INVALID_V1)
def test_v1_response_field_matrix(client, caplog, field, value):
    body = payload()
    if value is MISSING:
        body.pop(field)
    else:
        body[field] = value
    body['url'] = URL
    assert_rejected_on_both_domains(
        client, body, 'v1', 'upload_failed', caplog.text)
    assert not caplog.records


@pytest.mark.parametrize('value', INVALID_LEGACY)
def test_legacy_error_type_matrix(client, caplog, value):
    body = payload('legacy')
    if value is MISSING:
        body.pop('err')
    else:
        body['err'] = value
    assert_rejected_on_both_domains(
        client, body, 'legacy', 'upload_failed', caplog.text)
    assert not caplog.records


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('marker', [
    {'success': False}, {'status': False}, {'error': SECRET},
    {'error': True}, {'error': 1}, {'error': [SECRET]},
    {'error': {'message': SECRET}},
])
def test_failure_markers_override_otherwise_valid_payload(
        client, caplog, version, marker):
    body = payload(version)
    body.update(marker)
    assert_rejected_on_both_domains(
        client, body, version, 'upload_failed', caplog.text)
    assert not caplog.records


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('error', [None, False, 0, '', [], {}])
def test_falsey_error_and_message_do_not_reject_success(client, version, error):
    body = payload(version)
    body.update(error=error, message='informational text')
    client.responses[0].body = json.dumps(body)
    assert providers.upload_superbed(
        IMAGE, FILENAME, SECRET, {'api_version': version}) == URL
    assert client.create.call_count == 1


@pytest.mark.parametrize('entry', [{}, {'url': None}, {'url': 'invalid'},
                                  {'view_url': URL}, {'thumbnail_url': URL}])
def test_v1_file_url_never_falls_back_to_root(client, caplog, entry):
    body = payload()
    body.update(files=[entry], url=URL)
    assert_rejected_on_both_domains(
        client, body, 'v1', 'invalid_url', caplog.text)
