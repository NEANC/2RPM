from copy import deepcopy
from unittest.mock import Mock

import pytest

from modules.image_host import registry
from modules.image_host.context import UploadContext
from modules.image_host.core import ImageHostError
from test_image_host_superbed import client
from test_image_host_superbed import endpoint
from test_image_host_superbed import FILENAME
from test_image_host_superbed import IMAGE
from test_image_host_superbed import SECRET
from test_image_host_superbed import URL


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('first_success', [False, True])
def test_same_event_restarts_domain_budget(client, version, first_success):
    hosts = [{'provider': 'superbed', 'token': SECRET,
              'options': {'api_version': version}}]
    before = deepcopy(hosts)
    client.responses[0].status_code = 503
    client.add(status=200 if first_success else 503, version=version)
    client.add(status=503, version=version)
    client.add(version=version)
    with UploadContext() as context:
        first = registry.upload_with_fallback(
            IMAGE, FILENAME, hosts, context=context)
        assert first.success is first_success
        assert first.attempts == ('superbed',)
        assert [failure.code for failure in first.failures] == (
            [] if first_success else ['upload_failed'])
        assert client.create.call_count == 2
        second = registry.upload_with_fallback(
            IMAGE, FILENAME, hosts, context=context)
        assert second.success and second.url == URL
        assert second.attempts == ('superbed',)
        assert second.failures == ()
    assert [url for url, _ in client.calls] == [
        endpoint(version, domain) for domain in ['cc', 'cn', 'cc', 'cn']]
    assert client.create.call_count == 4
    assert len({id(session) for session in client.created}) == 4
    assert not client.queue
    assert hosts == before


@pytest.mark.parametrize('version', ['v1', 'legacy'])
def test_duplicate_slots_preserve_prior_failure_and_own_budget(
        client, monkeypatch, version):
    prior = Mock(side_effect=ImageHostError('upload_failed', ''))
    later = Mock(side_effect=pytest.fail)
    monkeypatch.setitem(registry.UPLOADERS, 'catbox', prior)
    monkeypatch.setitem(registry.UPLOADERS, 'wmimg', later)
    host = {'provider': 'superbed', 'token': SECRET,
            'options': {'api_version': version}}
    hosts = [{'provider': 'catbox'}, host, deepcopy(host), {'provider': 'wmimg'}]
    before = deepcopy(hosts)
    client.responses[0].status_code = 503
    client.add(status=503, version=version)
    client.add(status=503, version=version)
    client.add(version=version)
    with UploadContext() as context:
        result = registry.upload_with_fallback(
            IMAGE, FILENAME, hosts, context=context)
    assert result.success and result.url == URL
    assert result.provider == 'superbed'
    assert result.attempts == ('catbox', 'superbed', 'superbed')
    assert [(f.provider, f.code) for f in result.failures] == [
        ('catbox', 'upload_failed'), ('superbed', 'upload_failed')]
    prior.assert_called_once()
    later.assert_not_called()
    assert [url for url, _ in client.calls] == [
        endpoint(version, domain) for domain in ['cc', 'cn', 'cc', 'cn']]
    assert client.create.call_count == 4
    assert not client.queue
    assert hosts == before


@pytest.mark.parametrize('version', ['v1', 'legacy'])
@pytest.mark.parametrize('success', [False, True])
def test_superbed_never_enters_storage_recovery(
        client, monkeypatch, tmp_path, version, success):
    forbidden = Mock(side_effect=pytest.fail)
    hosts = [{'provider': 'superbed', 'token': SECRET, 'expiration': '1d',
              'options': {'api_version': version, 'storage_id': 123}}]
    before = deepcopy(hosts)
    client.responses[0].status_code = 503
    client.add(version=version)
    if not success:
        client.sessions[1].post.side_effect = ImageHostError(
            'storage_unavailable', '', stage='upload', http_status=200)
    with UploadContext() as context:
        for name in ('prepare_storage', 'get_metadata', 'begin_recovery',
                     'storage_state', 'record_expiration',
                     'get_boltp_retention', '_cache_call'):
            monkeypatch.setattr(context, name, forbidden)
        result = registry.upload_with_fallback(
            IMAGE, FILENAME, hosts, context=context)
        assert result.success is success
        assert result.attempts == ('superbed',)
        assert [f.code for f in result.failures] == (
            [] if success else ['upload_failed'])
        assert result.warnings == ('该图床不支持保存期限，已忽略',)
        forbidden.assert_not_called()
    assert client.create.call_count == 2
    assert all(session.post.call_count == 1 for session in client.sessions)
    assert all(session.closed for session in client.sessions)
    assert not client.queue
    assert not (tmp_path / 'local').exists()
    assert hosts == before
