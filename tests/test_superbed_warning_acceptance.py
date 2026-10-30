from copy import deepcopy
import logging

import pytest

from modules.image_host import providers
from modules.image_host.core import ImageHostError
from test_image_host_superbed import FILENAME
from test_image_host_superbed import IMAGE
from test_image_host_superbed import SECRET
from test_image_host_superbed import URL
from test_image_host_superbed import WARNING
from test_image_host_superbed import assert_safe
from test_image_host_superbed import client
from test_superbed_failover_acceptance import arrange_outcome
from test_superbed_failover_acceptance import assert_lifecycle


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('seconds', [
    ('success', 'success'), ('success', 'invalid_url'),
    ('invalid_url', 'success'), ('invalid_url', 'invalid_url'),
])
def test_warning_once_per_call_across_failover(client, caplog, version, seconds):
    options = {'api_version': version, 'UNKNOWN_KEY_7319': SECRET,
               'OTHER_UNKNOWN_7319': {'RAW_BODY_7319': URL}}
    original = deepcopy(options)
    outcomes = []
    caplog.set_level(logging.WARNING, logger=providers.LOGGER.name)
    for invocation, second in enumerate(seconds):
        if invocation:
            client.add(version=version)
        client.add(version=version)
        arrange_outcome(client, invocation * 2, version, 'timeout')
        arrange_outcome(client, invocation * 2 + 1, version, second)
        before = len(caplog.records)
        if second == 'success':
            assert providers.upload_superbed(
                IMAGE, FILENAME, SECRET, options) == URL
        else:
            with pytest.raises(ImageHostError) as caught:
                providers.upload_superbed(IMAGE, FILENAME, SECRET, options)
            assert_safe(caught.value, 'invalid_url', caplog.text)
        records = caplog.records[before:]
        assert [(r.name, r.levelno, r.getMessage()) for r in records] == [
            (providers.LOGGER.name, logging.WARNING, WARNING)]
        assert options == original
        outcomes.extend(('timeout', second))
    for secret in ('UNKNOWN_KEY_7319', 'OTHER_UNKNOWN_7319',
                   'RAW_BODY_7319', SECRET, URL):
        assert secret not in caplog.text
    assert_lifecycle(client, version, outcomes)
