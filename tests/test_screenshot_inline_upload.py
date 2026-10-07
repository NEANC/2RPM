#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证截图 CLI 内联图床配置的严格语法与凭证快照。"""

import traceback

import pytest

from modules.screenshot.inline_upload import InlineUploadError
from modules.screenshot.inline_upload import parse_inline_hosts
from modules.screenshot.inline_upload import prepare_cli_host


def test_quoted_delimiters_and_nested_options():
    """顶层分号分段不破坏引号值与嵌套映射。"""
    hosts = parse_inline_hosts(
        'provider: beeimg, token: "a;b,c"; '
        '{provider: boltp, token: "001234", options: {storage_id: 7}}')
    assert [host['provider'] for host in hosts] == ['beeimg', 'boltp']
    assert hosts[0]['token'] == 'a;b,c'
    assert hosts[1]['token'] == '001234'
    assert hosts[1]['options']['storage_id'] == 7


@pytest.mark.parametrize('text', [
    '', ';', 'provider: catbox;',
    'provider: catbox;;provider: catbox',
    'provider: catbox, provider: beeimg',
    'provider: catbox, options: []',
    'provider: beeimg, token: 123',
    'provider: beeimg, token: false',
    'provider: beeimg, token: ""',
    'provider: missing',
    'provider: catbox, options: {',
    'provider: catbox, provider: beeimg',
    'provider: catbox, options: {[}]',
    'provider: catbox, token: "abc\\',
])
def test_invalid_inline_is_parameter_error(text):
    """结构、重复键或类型错误整体拒绝。"""
    with pytest.raises(InlineUploadError):
        parse_inline_hosts(text)


def test_nested_duplicate_key_is_rejected_without_parser_context():
    """嵌套映射重复键不能被静默覆盖或保留解析器异常链。"""
    with pytest.raises(InlineUploadError) as caught:
        parse_inline_hosts(
            'provider: boltp, options: {storage_id: 1, storage_id: 2}')
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_error_does_not_retain_secret_or_parser_context():
    """诊断只包含固定分类，不保留敏感原文或异常链。"""
    secret = 'INLINE_SECRET_7e39'
    with pytest.raises(InlineUploadError) as caught:
        parse_inline_hosts(f'provider: catbox, token: "{secret};')
    error = caught.value
    assert secret not in ''.join(traceback.format_exception(error))
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize('value', [None, ''])
def test_album_blocks_missing_credential(monkeypatch, value):
    """合法环境引用缺失时保留相册选项并返回单项失败。"""
    if value is None:
        monkeypatch.delenv('JPEG_TEST_TOKEN', raising=False)
    else:
        monkeypatch.setenv('JPEG_TEST_TOKEN', value)
    host = parse_inline_hosts(
        'provider: beeimg, token: "${JPEG_TEST_TOKEN}", '
        'options: {albumid: abcde}')[0]
    prepared, error = prepare_cli_host(host)
    assert prepared is None and error == 'missing_environment'
    assert host['options'] == {'albumid': 'abcde'}


def test_environment_value_is_not_resolved_twice(monkeypatch):
    """环境值包含占位符时仍按原值传递。"""
    monkeypatch.setenv('JPEG_TEST_TOKEN', '${LITERAL}')
    host = parse_inline_hosts('provider: catbox, token: "${JPEG_TEST_TOKEN}"')[0]
    prepared, error = prepare_cli_host(host)
    assert error is None
    assert prepared['token'] == '${LITERAL}'


def test_anonymous_provider_can_omit_token():
    """支持匿名的图床可省略凭证。"""
    assert prepare_cli_host(parse_inline_hosts('provider: catbox')[0])[0]['token'] == ''


def test_non_anonymous_provider_requires_token():
    """不支持匿名的图床省略凭证时拒绝。"""
    with pytest.raises(InlineUploadError):
        parse_inline_hosts('provider: wmimg')
