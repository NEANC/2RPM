#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 测试请求与结果的数据契约。"""

from dataclasses import asdict, dataclass
import json
from typing import Literal


@dataclass(frozen=True)
class TestRequest:
    """保存单次独立测试的配置快照与草稿身份。"""

    request_id: str
    document_generation: int
    revision: int
    action: Literal['capture', 'upload', 'notify']
    config: dict
    config_dir: str
    session_dir: str
    target_index: int | None
    host_index: int | None
    image_path: str | None
    template_key: str | None


@dataclass(frozen=True)
class TestResult:
    """仅传递安全终态及本地预览图片路径。"""

    request_id: str
    document_generation: int
    revision: int
    status: Literal['success', 'failure', 'partial']
    reason: str
    preview_paths: tuple[str, ...]


def _object_pairs(pairs):
    """拒绝含重复键的消息，避免身份字段被覆盖。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('重复协议字段')
        result[key] = value
    return result


def _json_value(value):
    """检查快照只含可安全编码的 JSON 基础类型。"""
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        import math
        return math.isfinite(value)
    if type(value) is list:
        return all(_json_value(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _json_value(item)
                   for key, item in value.items())
    return False


def _identity(data):
    """验证请求与结果共有的身份类型。"""
    return (type(data['request_id']) is str and bool(data['request_id'])
            and type(data['document_generation']) is int
            and data['document_generation'] >= 0
            and type(data['revision']) is int and data['revision'] >= 0)


def _validate_request(data):
    """校验动作枚举、字段类型及动作专属参数。"""
    if not _identity(data) or data['action'] not in ('capture', 'upload', 'notify'):
        raise ValueError('无效请求身份或动作')
    if type(data['action']) is not str or not _json_value(data['config']) or type(data['config']) is not dict:
        raise ValueError('无效配置快照')
    for name in ('config_dir', 'session_dir'):
        if type(data[name]) is not str or not data[name]:
            raise ValueError('无效请求路径')
    for name in ('target_index', 'host_index'):
        if data[name] is not None and (type(data[name]) is not int or data[name] < 0):
            raise ValueError('无效目标索引')
    for name in ('image_path', 'template_key'):
        if data[name] is not None and (type(data[name]) is not str or not data[name]):
            raise ValueError('无效请求字段')
    required = {'capture': ('target_index',),
                'upload': ('host_index', 'image_path'),
                'notify': ('template_key',)}[data['action']]
    for name in ('target_index', 'host_index', 'image_path', 'template_key'):
        if (name in required) != (data[name] is not None):
            raise ValueError('动作字段不匹配')


def _validate_result(data):
    """校验安全结果的固定字段。"""
    if not _identity(data) or type(data['status']) is not str or data['status'] not in ('success', 'failure', 'partial'):
        raise ValueError('无效测试结果')
    if type(data['reason']) is not str or type(data['preview_paths']) not in (list, tuple):
        raise ValueError('无效结果字段')
    if not all(type(path) is str for path in data['preview_paths']):
        raise ValueError('无效预览路径')


def _encode(data, validator):
    """先验证再编码单条 UTF-8 JSON 消息。"""
    validator(data)
    return json.dumps({'protocol_version': 1, **data}, ensure_ascii=False,
                      allow_nan=False, separators=(',', ':')).encode('utf-8')


def _decode(payload, fields, validator):
    """只接受完整单份 JSON 对象和一个可选换行。"""
    try:
        text = payload.decode('utf-8')
        decoder = json.JSONDecoder(object_pairs_hook=_object_pairs,
                                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非法数值')))
        data, end = decoder.raw_decode(text)
        if text[end:] not in ('', '\n', '\r\n') or type(data) is not dict:
            raise ValueError('协议必须为单份 JSON')
        if data.get('protocol_version') != 1 or type(data['protocol_version']) is not int:
            raise ValueError('协议版本不匹配')
        if set(data) != fields | {'protocol_version'}:
            raise ValueError('协议字段不匹配')
        data.pop('protocol_version')
        validator(data)
        return data
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError('无效协议消息') from error


def encode_request(request: TestRequest) -> bytes:
    """编码一次请求。"""
    return _encode(asdict(request), _validate_request)


def decode_request(payload: bytes) -> TestRequest:
    """解码并验证一次请求。"""
    data = _decode(payload, set(TestRequest.__dataclass_fields__), _validate_request)
    return TestRequest(**data)


def encode_result(result: TestResult) -> bytes:
    """编码一次测试结果。"""
    return _encode(asdict(result), _validate_result)


def decode_result(payload: bytes, *, request: TestRequest) -> TestResult:
    """解码并匹配原始请求身份。"""
    data = _decode(payload, set(TestResult.__dataclass_fields__), _validate_result)
    if any(data[name] != getattr(request, name) for name in
           ('request_id', 'document_generation', 'revision')):
        raise ValueError('测试结果身份不匹配')
    data['preview_paths'] = tuple(data['preview_paths'])
    return TestResult(**data)
