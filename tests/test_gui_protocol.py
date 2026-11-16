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


@pytest.mark.parametrize('provider,target', [
    ('window', '完整窗口标题'), ('adb', '127.0.0.1:16384')])
def test_capture_accepts_window_and_adb_input(tmp_path, provider, target):
    """直接目标请求往返不依赖配置目标序号。"""
    request = GuiRequest('req-1', 2, 3, 'capture', {}, str(tmp_path),
                         str(tmp_path), None, None, None, None,
                         capture_provider=provider, capture_target=target)
    assert decode_request(encode_request(request)) == request


def test_saved_quoted_scalars_encode_as_plain_json(tmp_path):
    """保存并重开的引号标量必须降级为协议可接受的 JSON 基础类型。"""
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString

    from modules.config_document import new_document, read_document, save_document
    from modules.gui.app import App
    from modules.gui.model import EditorModel

    document = new_document()
    document.tree['push']['templates']['on_end']['content'] = DoubleQuotedScalarString(
        '内容 {process_name}')
    document.tree['push']['screenshot']['image_host'][0]['token'] = (
        DoubleQuotedScalarString('token-value'))
    saved = save_document(document, tmp_path / 'quoted.yaml',
                          expected_fingerprint=None)
    model = EditorModel(read_document(saved.path))
    request = GuiRequest('req-1', model.document_generation, model.revision,
                         'capture', App._plain_value(model.snapshot()),
                         str(tmp_path), str(tmp_path), 0, None, None, None)
    assert type(request.config['push']['templates']['on_end']['content']) is str
    assert type(request.config['push']['screenshot']['image_host'][0]['token']) is str
    encode_request(request)


def test_result_round_trip_channels_and_legacy_defaults(gui_request):
    """结果嵌套记录可恢复类型，旧消息缺省新字段可读。"""
    from modules.gui.protocol import ChannelTestResult

    result = GuiResult('req-1', 2, 3, 'partial', '', (),
                       channel_results=(ChannelTestResult(0, 'bark', 'success', ''),
                                        ChannelTestResult(1, 'smtp', 'failure', '发送失败')))
    assert decode_result(encode_result(result), request=gui_request) == result
    legacy = json.loads(encode_result(result))
    del legacy['upload_url'], legacy['channel_results']
    assert decode_result(json.dumps(legacy).encode(), request=gui_request).channel_results == ()


def test_result_redacts_secrets(gui_request):
    """拒绝带凭证的通道结果及不可信地址，不传出秘密。"""
    from modules.gui.protocol import ChannelTestResult

    result = GuiResult('req-1', 2, 3, 'failure', '', (),
                       channel_results=(ChannelTestResult(0, 'bark', 'failure',
                                                          'secret-value'),))
    with pytest.raises(ValueError):
        encode_result(result)


@pytest.mark.parametrize('provider,target', [
    ('invalid', 'device'), ('adb', ''), ('window', None), (None, 'window')])
def test_capture_rejects_invalid_direct_input(tmp_path, provider, target):
    """直传目标只允许完整合法的来源及非空目标。"""
    request = GuiRequest('req-1', 2, 3, 'capture', {}, str(tmp_path),
                         str(tmp_path), None, None, None, None,
                         capture_provider=provider, capture_target=target)
    with pytest.raises(ValueError):
        encode_request(request)


def test_legacy_optional_fields_preserve_single_document_boundary(gui_request):
    """旧格式可读但不得接受第二消息或未知字段。"""
    legacy = json.loads(encode_request(gui_request))
    del legacy['capture_provider'], legacy['capture_target']
    payload = json.dumps(legacy).encode()
    assert decode_request(payload).capture_target is None
    with pytest.raises(ValueError):
        decode_request(payload + b'\n' + payload)
    legacy['unknown'] = 'value'
    with pytest.raises(ValueError):
        decode_request(json.dumps(legacy).encode())
