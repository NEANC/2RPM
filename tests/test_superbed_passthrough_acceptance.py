from copy import deepcopy

import pytest

from modules.image_host import providers
from modules.image_host.core import ImageHostError
from test_image_host_superbed import FILENAME
from test_image_host_superbed import IMAGE
from test_image_host_superbed import SECRET
from test_image_host_superbed import URL
from test_image_host_superbed import UUID_TEXT
from test_image_host_superbed import assert_safe
from test_image_host_superbed import client
from test_image_host_superbed import endpoint
from test_superbed_failover_acceptance import arrange_outcome
from test_superbed_failover_acceptance import assert_lifecycle


@pytest.mark.parametrize('version,field,value', [
    ('v1', 'folder_id', UUID_TEXT),
    ('v1', 'folder_id', UUID_TEXT.lower()),
    ('v1', 'folder_id', UUID_TEXT.replace('-', '')),
    ('v1', 'folder_id', '{' + UUID_TEXT + '}'),
    ('legacy', 'categories', ''),
    ('legacy', 'categories', '中文分类'),
    ('legacy', 'categories', '  中文分类\t '),
])
@pytest.mark.parametrize('second', ['success', 'invalid_url'])
def test_raw_parameters_reach_both_domains(
        client, caplog, version, field, value, second):
    client.add(version=version)
    arrange_outcome(client, 0, version, 'timeout')
    arrange_outcome(client, 1, version, second)
    options = {'api_version': version, field: value}
    original = deepcopy(options)
    if second == 'success':
        assert providers.upload_superbed(IMAGE, FILENAME, SECRET, options) == URL
    else:
        with pytest.raises(ImageHostError) as caught:
            providers.upload_superbed(IMAGE, FILENAME, SECRET, options)
        assert_safe(caught.value, 'invalid_url', caplog.text)
    assert options == original
    for (url, kwargs), domain in zip(client.calls, ('cc', 'cn')):
        data = {field: value}
        if version == 'legacy':
            data['token'] = SECRET
        expected = {
            'data': data,
            'files': {('files' if version == 'v1' else 'file'):
                      (FILENAME, IMAGE, 'image/png')},
            'timeout': (5, 15), 'verify': True,
            'allow_redirects': False, 'stream': True,
        }
        if version == 'v1':
            expected['headers'] = {'X-API-Key': SECRET}
        assert url == endpoint(version, domain)
        assert kwargs == expected
    assert_lifecycle(client, version, ('timeout', second))
