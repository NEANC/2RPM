#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证图床上传核心的凭证边界、安全结果与顺序故障转移。"""

from collections import UserDict
from copy import deepcopy
from dataclasses import FrozenInstanceError
from importlib import import_module
import os
import traceback

import pytest


IMAGE = b'fixed-nonempty-image'
FILENAME = 'screenshot.png'
URL = 'https://cdn.example.com/image?id=1&signature=a%2Fb%3D'
SECRET = 'FAKE_DIRECT_SECRET_7319'
ENV_SECRET = 'FAKE_ENV_SECRET_8420'
ENV_NAME = 'IMAGE_HOST_TEST_TOKEN'


def core():
    """延迟导入实际核心，使缺少实现表现为用例失败。"""
    return import_module('modules.image_host.core')


def registry():
    """读取真实静态注册表和顺序上传入口。"""
    return import_module('modules.image_host.registry')


def install(monkeypatch, provider, uploader):
    """临时注册本地适配器，并在用例结束后自动还原。"""
    monkeypatch.setitem(registry().UPLOADERS, provider, uploader)


def upload(hosts, image=IMAGE, filename=FILENAME):
    """执行真实顺序上传逻辑，不替换或伪造返回结果。"""
    return registry().upload_with_fallback(image, filename, hosts)


def succeed(image_bytes, filename, token, options):
    """提供不发送网络请求的成功适配器。"""
    assert image_bytes == IMAGE
    assert filename == FILENAME
    return URL


def test_models_are_frozen_and_public_exports_are_real():
    """公开入口仅包含实际实现，数据模型使用不可变字段。"""
    module = core()
    package = import_module('modules.image_host')
    failure = module.UploadFailure('first', 'upload_failed', '图床上传失败')
    result = module.UploadResult(False, None, None, ('first',), (failure,))
    assert result.attempts == ('first',)
    assert result.failures == (failure,)
    with pytest.raises(FrozenInstanceError):
        failure.message = 'changed'
    with pytest.raises(FrozenInstanceError):
        result.success = True
    for name in ('UploadFailure', 'UploadResult', 'ImageHostError',
                 'resolve_token', 'validate_image_url'):
        assert getattr(package, name) is getattr(module, name)
    assert package.upload_with_fallback is registry().upload_with_fallback
    assert isinstance(registry().UPLOADERS, dict)
    assert registry().UPLOADERS == {}


def test_first_failure_second_success_stops_before_third(monkeypatch):
    """首项失败后尝试第二项，成功即停且保留此前失败。"""
    calls = []

    def first(image_bytes, filename, token, options):
        """记录首项调用并抛出受控类别。"""
        calls.append(('first', image_bytes, filename, token, options))
        raise core().ImageHostError('upload_failed', SECRET)

    def second(image_bytes, filename, token, options):
        """记录次项调用并返回候选直链。"""
        calls.append(('second', image_bytes, filename, token, options))
        return URL

    def third(*args):
        """记录不应该发生的后续调用。"""
        calls.append(('third',))
        return URL

    for name, adapter in [('first', first), ('second', second),
                          ('third', third)]:
        install(monkeypatch, name, adapter)
    hosts = [{'provider': ' FIRST ', 'token': SECRET},
             {'provider': 'second', 'token': 'second-key'},
             {'provider': 'third'}]
    original = deepcopy(hosts)
    result = upload(hosts)
    assert result.success is True
    assert (result.provider, result.url) == ('second', URL)
    assert result.attempts == ('first', 'second')
    assert len(result.failures) == 1
    assert result.failures[0].provider == 'first'
    assert result.failures[0].code == 'upload_failed'
    assert [call[0] for call in calls] == ['first', 'second']
    assert calls[0][1:] == (IMAGE, FILENAME, SECRET, {})
    assert calls[1][1:] == (IMAGE, FILENAME, 'second-key', {})
    assert hosts == original
    assert SECRET not in repr(result)


def test_all_failures_return_ordered_independent_result(monkeypatch):
    """每张图片均从首站开始，全部失败不缓存或重试。"""
    calls = []

    def fail(image_bytes, filename, token, options):
        """通过不同凭证识别重复提供方的实际尝试顺序。"""
        calls.append(token)
        raise RuntimeError(SECRET)

    install(monkeypatch, 'same', fail)
    hosts = [{'provider': 'same', 'token': 'one'},
             {'provider': 'same', 'token': 'two'}]
    first = upload(hosts)
    second = upload(hosts)
    assert calls == ['one', 'two', 'one', 'two']
    assert first is not second
    assert first == second
    assert first.success is False
    assert first.provider is first.url is None
    assert first.attempts == ('same', 'same')
    assert len(first.failures) == 2
    assert all(item.code == 'upload_failed' for item in first.failures)


def test_each_image_restarts_even_after_previous_success(monkeypatch):
    """前一张在次站成功不改变下一张的起始站点。"""
    calls = []

    def first(*args):
        """记录首站失败。"""
        calls.append('first')
        raise RuntimeError(SECRET)

    def second(*args):
        """记录次站成功。"""
        calls.append('second')
        return URL

    install(monkeypatch, 'first', first)
    install(monkeypatch, 'second', second)
    hosts = [{'provider': 'first'}, {'provider': 'second'}]
    results = [upload(hosts), upload(hosts)]
    assert calls == ['first', 'second', 'first', 'second']
    assert all(result.success for result in results)
    assert results[0] is not results[1]


def test_success_never_reads_later_credentials(monkeypatch):
    """首站成功后不解析后续缺失环境引用或读取后续映射。"""
    class UnreadableHost(UserDict):
        """模拟任何读取都会失败的后续配置。"""

        def get(self, *args):
            """禁止在已成功后继续读取配置。"""
            raise AssertionError('后续项不应被读取')

    monkeypatch.delenv(ENV_NAME, raising=False)
    install(monkeypatch, 'first', succeed)
    result = upload([
        {'provider': 'first'},
        {'provider': 'first', 'token': '${' + ENV_NAME + '}'},
        UnreadableHost(),
    ])
    assert result.success
    assert result.attempts == ('first',)
    assert result.failures == ()


@pytest.mark.parametrize('value', [None, ''])
def test_empty_token_is_anonymous(value):
    """缺省语义的凭证允许匿名上传，不由核心统一拒绝。"""
    assert core().resolve_token(value) == ''


@pytest.mark.parametrize('value', [SECRET, '  key\t ', '$PLAIN', '%PLAIN%',
                                   '中文凭证', 'a=b+c/=='])
def test_direct_token_is_preserved(value):
    """直接凭证保持字符语义，不去空白或执行其他插值。"""
    assert core().resolve_token(value) == value


@pytest.mark.parametrize('value', [False, 0, 42, [], {}, b'key'])
def test_non_string_token_is_not_coerced(value):
    """非字符串凭证不通过真值判断或强制转换蒙混过关。"""
    with pytest.raises(core().ImageHostError) as caught:
        core().resolve_token(value)
    assert caught.value.code == 'invalid_token'


@pytest.mark.parametrize('value', [
    'Bearer ${NAME}', '${}', '${9NAME}', '${HAS-DASH}', '${中文}',
    '${NAME}suffix', ' ${NAME}', '${NAME} ', '${NAME}${OTHER}', '${NAME',
])
def test_partial_or_invalid_environment_reference_is_rejected(value):
    """仅允许完整 ASCII 环境变量引用，其他引用语法安全失败。"""
    with pytest.raises(core().ImageHostError) as caught:
        core().resolve_token(value)
    assert caught.value.code == 'invalid_environment_reference'
    assert value not in str(caught.value)


def test_environment_resolution_is_runtime_nonrecursive_and_readonly(
        monkeypatch):
    """运行时读取环境变量，原样返回其值且不递归、不写环境。"""
    reference = '${' + ENV_NAME + '}'
    monkeypatch.setenv(ENV_NAME, '  ' + ENV_SECRET + '  ')
    before = dict(os.environ)
    assert core().resolve_token(reference) == '  ' + ENV_SECRET + '  '
    assert dict(os.environ) == before
    monkeypatch.setenv(ENV_NAME, '${OTHER_TEST_TOKEN}')
    monkeypatch.setenv('OTHER_TEST_TOKEN', SECRET)
    before = dict(os.environ)
    assert core().resolve_token(reference) == '${OTHER_TEST_TOKEN}'
    assert dict(os.environ) == before


@pytest.mark.parametrize('value', [None, ''])
def test_missing_or_empty_environment_fails_safely(monkeypatch, value):
    """缺失或空环境值产生固定摘要，不暴露变量内容。"""
    if value is None:
        monkeypatch.delenv(ENV_NAME, raising=False)
    else:
        monkeypatch.setenv(ENV_NAME, value)
    before = dict(os.environ)
    with pytest.raises(core().ImageHostError) as caught:
        core().resolve_token('${' + ENV_NAME + '}')
    assert caught.value.code == 'missing_environment'
    assert ENV_NAME not in str(caught.value)
    assert dict(os.environ) == before


@pytest.mark.parametrize('value', [
    URL, 'http://example.com/image', 'https://example.com/',
    'https://example.com:8443/path?token=a%2Fb&x=1+2',
    'https://[2001:db8::1]:443/image', 'https://例子.测试/图片',
    'https://localhost/image',
])
def test_valid_urls_preserve_paths_and_signatures(value):
    """合理链接无需扩展名且保留签名查询，不进行网络探测。"""
    assert core().validate_image_url(value) == value
    assert core().validate_image_url(' \t\r\n' + value + '\r\n ') == value


@pytest.mark.parametrize('value', [
    None, False, 123, [], {}, b'https://example.com/image', '', '  ',
    '<html>response</html>', 'javascript:alert(1)',
    'data:image/png;base64,QQ==',
    'file:///image.png', '//example.com/image', 'https:///image',
    'https://', 'https://?query', 'https://user:password@example.com/image',
    'https://user@example.com/image', 'https://@example.com/image',
    'https://example.com:bad/image', 'https://example.com:65536/image',
    'https://example.com:-1/image', 'https://example.com:/image',
    'https://exa mple.com/image', 'https://example.com/a\nb',
    'https://example.com/a\tb', 'https://example.com/a\x00b',
    'https://example.com/a\x7fb', 'https://example.com/a\x85b',
    'https://[invalid]/image', 'https://example.com\\evil/image',
    'https://.example.com/image', 'https://example..com/image',
    'https://-example.com/image', 'https://example.com/<html>',
])
def test_invalid_urls_have_safe_errors(value):
    """网络返回边界拒绝危险或畸形链接且不返回响应原文。"""
    with pytest.raises(core().ImageHostError) as caught:
        core().validate_image_url(value)
    assert caught.value.code == 'invalid_url'
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_invalid_url_continues_to_next_provider(monkeypatch):
    """适配器返回值经过统一校验，非法链接仅影响当前项。"""
    calls = []

    def invalid(*args):
        """返回带合成密钥的错误响应。"""
        calls.append('invalid')
        return '<html>' + SECRET + '</html>'

    install(monkeypatch, 'bad', invalid)
    install(monkeypatch, 'good', succeed)
    result = upload([{'provider': 'bad'}, {'provider': 'good'}])
    assert result.success
    assert result.attempts == ('bad', 'good')
    assert result.failures[0].code == 'invalid_url'
    assert calls == ['invalid']
    assert SECRET not in repr(result)


@pytest.mark.parametrize('kind', ['ordinary', 'known', 'unknown', 'mutated'])
def test_exception_details_never_escape(monkeypatch, caplog, kind):
    """错误消息、动态类名和未知错误码均不得进入返回值或日志。"""
    monkeypatch.setenv(ENV_NAME, ENV_SECRET)
    raw_message = SECRET + '\n' + ENV_SECRET + '<html>full response</html>'
    if kind == 'ordinary':
        failure = type(SECRET, (Exception,), {})(raw_message)
    else:
        code = SECRET if kind == 'unknown' else 'upload_failed'
        failure = core().ImageHostError(code, raw_message)
        assert SECRET not in str(failure) + repr(failure)
        assert ENV_SECRET not in str(failure) + repr(failure)
        if kind == 'mutated':
            failure.message = raw_message
            failure.code = SECRET

    def fail(*args):
        """抛出携带合成敏感内容的适配器异常。"""
        raise failure

    install(monkeypatch, 'local', fail)
    hosts = [{'provider': 'local', 'token': SECRET},
             {'provider': 'local', 'token': '${' + ENV_NAME + '}'}]
    result = upload(hosts)
    assert not result.success
    assert len(result.failures) == 2
    assert all(item.code == 'upload_failed' for item in result.failures)
    assert all(item.message == '图床上传失败' for item in result.failures)
    output = repr(result) + caplog.text
    assert SECRET not in output
    assert ENV_SECRET not in output
    assert 'full response' not in output
    assert not caplog.records


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_control_signals_propagate_same_object(monkeypatch, signal_type):
    """控制信号原对象传播且不继续尝试其他项。"""
    signal = signal_type('stop')
    calls = []

    def stop(*args):
        """模拟进程控制信号。"""
        calls.append('stop')
        raise signal

    install(monkeypatch, 'stop', stop)
    install(monkeypatch, 'next', succeed)
    with pytest.raises(signal_type) as caught:
        upload([{'provider': 'stop'}, {'provider': 'next'}])
    assert caught.value is signal
    assert calls == ['stop']


def test_duplicate_providers_keep_credentials_and_deepcopy_options(
        monkeypatch):
    """重复站点不去重，每次仅收到当前凭证和独立深拷贝参数。"""
    calls = []
    shared = UserDict(nested={'values': [1]}, literal='${UNEXPANDED}')
    hosts = [UserDict(provider='same', token=SECRET, options=shared),
             UserDict(provider='same', token=ENV_SECRET, options=shared)]
    original = deepcopy(hosts)

    def adapter(image_bytes, filename, token, options):
        """修改本次参数以验证源配置和下次参数未被污染。"""
        assert options['nested']['values'] == [1]
        assert options['literal'] == '${UNEXPANDED}'
        assert set(options) == {'nested', 'literal'}
        calls.append((token, options))
        options['nested']['values'].append(2)
        if token == SECRET:
            raise RuntimeError(SECRET)
        return URL

    install(monkeypatch, 'same', adapter)
    result = upload(hosts)
    assert result.success
    assert result.attempts == ('same', 'same')
    assert [call[0] for call in calls] == [SECRET, ENV_SECRET]
    assert calls[0][1] is not calls[1][1]
    assert hosts == original
    assert hosts[0]['options'] is hosts[1]['options'] is shared
    assert SECRET not in repr(result)
    assert ENV_SECRET not in repr(result)


@pytest.mark.parametrize('fields', [{}, {'token': None}, {'token': ''},
                                     {'options': None}, {'options': {}}])
def test_missing_token_and_options_allow_anonymous_upload(monkeypatch, fields):
    """缺少凭证或参数与显式空值均使用匿名凭证和空参数。"""
    calls = []

    def anonymous(image_bytes, filename, token, options):
        """记录匿名调用的精确参数。"""
        calls.append((token, options))
        return URL

    install(monkeypatch, 'anonymous', anonymous)
    assert upload([{'provider': 'anonymous', **fields}]).success
    assert calls == [('', {})]


@pytest.mark.parametrize('hosts, code', [
    (None, 'not_configured'), ([], 'not_configured'),
    ({}, 'invalid_hosts'), ('secret-config', 'invalid_hosts'),
    (False, 'invalid_hosts'), (42, 'invalid_hosts'), ((), 'invalid_hosts'),
])
def test_missing_or_invalid_host_list_is_one_safe_failure(hosts, code):
    """未配置不回退默认图床，非法顶层配置仅产生一次安全失败。"""
    result = upload(hosts)
    assert not result.success
    assert result.provider is result.url is None
    assert result.attempts == ()
    assert len(result.failures) == 1
    assert result.failures[0].code == code
    assert 'secret-config' not in repr(result)


@pytest.mark.parametrize('item, name, code', [
    (None, '第1项', 'invalid_host'),
    (SECRET, '第1项', 'invalid_host'),
    ([], '第1项', 'invalid_host'),
    ({}, '第1项', 'invalid_provider'),
    ({'provider': 42}, '第1项', 'invalid_provider'),
    ({'provider': '  '}, '第1项', 'invalid_provider'),
    ({'provider': ' Unknown '}, 'unknown', 'unknown_provider'),
    ({'provider': 'bad\nname'}, '第1项', 'invalid_provider'),
    ({'provider': 'bad\x7fname'}, '第1项', 'invalid_provider'),
    ({'provider': 'bad\u202ename'}, '第1项', 'invalid_provider'),
    ({'provider': 'good', 'token': 42}, 'good', 'invalid_token'),
    ({'provider': 'good', 'token': '${}'}, 'good',
     'invalid_environment_reference'),
    ({'provider': 'good', 'token': '${IMAGE_HOST_TEST_TOKEN}'}, 'good',
     'missing_environment'),
    ({'provider': 'good', 'options': []}, 'good', 'invalid_options'),
    ({'provider': 'good', 'options': SECRET}, 'good', 'invalid_options'),
    ({'provider': 'good', 'options': False}, 'good', 'invalid_options'),
])
def test_invalid_item_retains_position_and_continues(
        monkeypatch, item, name, code):
    """配置失败保留尝试位置，记录安全类别后继续下一项。"""
    monkeypatch.delenv(ENV_NAME, raising=False)
    calls = []

    def good(*args):
        """记录唯一合法调用。"""
        calls.append(args)
        return URL

    install(monkeypatch, 'good', good)
    hosts = [item, {'provider': 'good'}]
    original = deepcopy(hosts)
    result = upload(hosts)
    assert result.success
    assert result.attempts == (name, 'good')
    assert result.failures[0].provider == name
    assert result.failures[0].code == code
    assert len(calls) == 1
    assert hosts == original
    assert SECRET not in repr(result)


@pytest.mark.parametrize('image, filename', [
    (None, FILENAME), ('not-bytes', FILENAME), (b'', FILENAME),
    (bytearray(IMAGE), FILENAME), (IMAGE, None), (IMAGE, 42),
    (IMAGE, ''), (IMAGE, '  '), (IMAGE, 'bad\nname.png'),
    (IMAGE, '../image.png'), (IMAGE, 'folder\\image.png'),
])
def test_invalid_shared_input_does_not_call_adapters(
        monkeypatch, image, filename):
    """共享输入错误不伪装成各站远程失败，也不解析站点配置。"""
    calls = []

    def unexpected(*args):
        """记录不应发生的上传。"""
        calls.append(args)
        return URL

    install(monkeypatch, 'local', unexpected)
    result = upload([{'provider': 'local'}, {'provider': 'local'}],
                    image, filename)
    assert not result.success
    assert result.provider is result.url is None
    assert result.attempts == ()
    assert len(result.failures) == 1
    assert result.failures[0].code == 'invalid_input'
    assert calls == []


def test_boundary_exception_text_and_chain_are_safe(caplog):
    """边界错误的格式化异常文本不包含凭证、响应或异常链。"""
    for function, value in [
        (core().resolve_token, 'Bearer ${' + SECRET + '}'),
        (core().validate_image_url, 'https://[' + SECRET + ']/image'),
        (core().validate_image_url, 'https://example.com:' + SECRET),
    ]:
        with pytest.raises(core().ImageHostError) as caught:
            function(value)
        text = ''.join(traceback.format_exception(caught.value))
        assert SECRET not in text + repr(caught.value) + caplog.text
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
    assert not caplog.records
