import pytest

from modules.image_host import providers
from test_image_host_superbed import client
from test_image_host_superbed import endpoint
from test_image_host_superbed import SECRET
from test_image_host_superbed import URL


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('suffix,mime', [
    ('jpg', 'image/jpeg'), ('jpeg', 'image/jpeg'),
    ('png', 'image/png'), ('webp', 'image/webp'),
])
def test_second_domain_preserves_format_and_bytes_identity(
        client, version, suffix, mime):
    data = bytes(bytearray(b'second-domain-format-contract'))
    filename = '本地截图.' + suffix
    client.responses[0].status_code = 503
    client.add(version=version)
    assert providers.upload_superbed(
        data, filename, SECRET, {'api_version': version}) == URL
    assert len(client.calls) == client.create.call_count == 2
    assert [url for url, _ in client.calls] == [
        endpoint(version, 'cc'), endpoint(version, 'cn')]
    key = 'files' if version == 'v1' else 'file'
    for _, request in client.calls:
        assert set(request['files']) == {key}
        part = request['files'][key]
        assert part[0] == filename
        assert part[1] is data
        assert part[2] == mime
        assert request['data'] == ({} if version == 'v1' else {'token': SECRET})
        if version == 'v1':
            assert request['headers'] == {'X-API-Key': SECRET}
        else:
            assert 'headers' not in request
    assert client.created[0] is not client.created[1]
    assert not client.queue
    assert client.events == [event for index in range(2) for event in
                             [('create', index), ('response', index),
                              ('session', index)]]
