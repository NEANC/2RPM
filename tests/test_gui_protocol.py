#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""单消息 GUI 测试协议的边界验证。"""

import json

import pytest

from modules.gui.protocol import TestRequest as GuiRequest
from modules.gui.protocol import TestResult as GuiResult
from modules.gui.protocol import (decode_request, decode_result, encode_request,
                                  encode_result)


@pytest.fixture
def gui_request(tmp_path):
    """建立包含中文与敏感配置的单次请求。"""
    return GuiRequest('req-1', 2, 3, 'notify', {'token': 'secret-value'},
                      str(tmp_path), str(tmp_path / '会话'), None, None,
                      None, 'on_end')


def test_round_trip_one_utf8_document(gui_request):
    """一条 UTF-8 消息承载身份和中文路径。"""
    encoded = encode_request(gui_request)
    assert decode_request(encoded + b'\n') == gui_request
    result = GuiResult('req-1', 2, 3, 'success', '完成', ('预览.jpg',))
    assert decode_result(encode_result(result) + b'\n', request=gui_request) == result
    assert json.loads(encoded)['protocol_version'] == 1


@pytest.mark.parametrize('payload', [b'', b'{', b'{}{}', b'{}\n{}',
                                     b'\xff', b'[]', b'{"protocol_version":1}'])
def test_rejects_empty_truncated_multiple_or_incomplete_request(payload):
    """仅完整单个对象可被解码。"""
    with pytest.raises(ValueError):
        decode_request(payload)


@pytest.mark.parametrize('field,value', [
    ('protocol_version', 2), ('request_id', ''), ('document_generation', True),
    ('revision', -1), ('action', 'run'), ('template_key', None),
    ('target_index', 0), ('config', {'bad': object()}),
])
def test_request_rejects_invalid_fields(gui_request, field, value):
    """版本、类型、动作专属字段及非 JSON 值均拒绝。"""
    from dataclasses import replace

    if field == 'protocol_version':
        data = json.loads(encode_request(gui_request))
        data[field] = value
        payload = json.dumps(data).encode()
        with pytest.raises(ValueError):
            decode_request(payload)
    else:
        with pytest.raises(ValueError):
            encode_request(replace(gui_request, **{field: value}))


@pytest.mark.parametrize('field,value', [
    ('request_id', 'wrong'), ('document_generation', 4), ('revision', 5),
    ('status', 'unknown'), ('preview_paths', ['ok', 123]),
])
def test_result_rejects_identity_and_invalid_fields(gui_request, field, value):
    """结果不可越过原请求三元标识。"""
    result = GuiResult('req-1', 2, 3, 'success', '', ())
    data = json.loads(encode_result(result))
    data[field] = value
    with pytest.raises(ValueError):
        decode_result(json.dumps(data).encode(), request=gui_request)


def test_rejects_duplicate_keys_and_trailing_noise(gui_request):
    """重复字段和尾部噪声不能伪装为唯一结果。"""
    payload = encode_result(GuiResult('req-1', 2, 3, 'success', '', ()))
    for bad in (payload + b'noise', payload + b'\n' + payload,
                payload.replace(b'"status":', b'"status":"failure","status":')):
        with pytest.raises(ValueError):
            decode_result(bad, request=gui_request)
