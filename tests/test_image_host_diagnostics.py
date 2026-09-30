#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证纯诊断脱敏边界与上下文作用域隔离。"""

from contextvars import copy_context
from importlib import import_module

import pytest


SECRET = 'FAKE_OTHER_HOST_TOKEN_7319'
SECOND_SECRET = 'FAKE_PRIMARY_TOKEN_8420'
SYNTHETIC_JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature'


def diagnostics():
    """延迟加载实际模块，使缺少实现表现为测试失败。"""
    return import_module('modules.image_host.diagnostics')


@pytest.mark.parametrize('text', ['请先绑定手机号', '不存在的储存驱动'])
def test_normal_chinese_is_preserved(text):
    """保留可用中文诊断，避免用一律丢弃掩盖功能缺失。"""
    assert diagnostics().sanitize_message(text, ()) == text


@pytest.mark.parametrize('text', [None, False, 1, [], {}, b'error', '', '  '])
def test_invalid_or_empty_message_is_discarded(text):
    """非字符串和无可展示内容的输入不强制转换。"""
    assert diagnostics().sanitize_message(text, ()) is None


@pytest.mark.parametrize('length', [199, 200, 201, 4096, 4097])
def test_input_and_output_length_boundaries(length):
    """先限制原始输入，再将安全输出截断到两百字符。"""
    result = diagnostics().sanitize_message('甲' * length, ())
    assert result == (None if length > 4096 else '甲' * min(length, 200))


def test_oversized_input_is_rejected_before_cleaning():
    """超长原文即使脱敏后很短也直接舍弃。"""
    assert diagnostics().sanitize_message('x' * 4097, ('x' * 4097,)) is None


@pytest.mark.parametrize('text, removed', [
    ('上传失败 https://example.test/a?sig=private&token=value', 'sig=private'),
    ('上传失败 HTTP://example.test/a?signature=private', 'signature=private'),
    ('上传失败 ${OTHER_TOKEN}', 'OTHER_TOKEN'),
    ('上传失败 $OTHER_TOKEN', 'OTHER_TOKEN'),
    ('上传失败 %OTHER_TOKEN%', 'OTHER_TOKEN'),
    ('上传失败 $env:OTHER_TOKEN', 'OTHER_TOKEN'),
])
def test_url_and_environment_references_are_removed(text, removed):
    """移除整条链接及常见环境引用，不留下查询签名。"""
    result = diagnostics().sanitize_message(text, ())
    assert result is not None
    assert '上传失败' in result
    assert removed not in result
    assert 'http' not in result.lower()


@pytest.mark.parametrize('text', [
    '<html>请先绑定手机号</html>', '<!DOCTYPE html>', '<!-- private -->',
    '&lt;html&gt;private&lt;/html&gt;',
    'Authorization: Basic abc123', 'authorization = private',
    'Bearer FAKE_TOKEN', 'bearer\tFAKE_TOKEN',
    'token=private', 'api_key: private', 'password=private',
    '{"access_token": "private"}', 'Cookie: session=private',
    '-----BEGIN PRIVATE KEY-----',
    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature',
])
def test_html_and_obvious_credentials_are_discarded(text):
    """无法可靠展示的页面、认证头与凭证结构整体舍弃。"""
    assert diagnostics().sanitize_message(text, ()) is None


@pytest.mark.parametrize('credential', [
    'Authorization: Basic FAKE_VALUE', 'Bearer FAKE_VALUE',
    'token=FAKE_VALUE',
])
@pytest.mark.parametrize('separator', ['', '\u200b', '\x1b[31m'])
def test_chinese_adjacent_credentials_are_discarded(credential, separator):
    """中文紧邻认证标识及控制清理重建的认证结构均不得展示。"""
    text = '错误' + credential[:2] + separator + credential[2:]
    assert diagnostics().sanitize_message(text, ()) is None


@pytest.mark.parametrize('prefix', ['错误', '错误 ', 'failed '])
@pytest.mark.parametrize('separator', ['', '\u200b', '\x00', '\x1b[31m'])
@pytest.mark.parametrize('entrypoint', ['sanitize', 'core_after_sanitize'])
def test_jwt_boundaries_reject_credentials(prefix, separator, entrypoint):
    """中文紧邻或控制拆分的合成 JWT 不得经诊断链路展示。"""
    text = prefix + SYNTHETIC_JWT[:2] + separator + SYNTHETIC_JWT[2:]
    result = diagnostics().sanitize_message(text, ())
    if entrypoint == 'core_after_sanitize':
        module = import_module('modules.image_host.core')
        result = module.ImageHostError(
            'upload_failed', '', diagnostic=result).diagnostic
    assert result is None


@pytest.mark.parametrize('text', [
    'failed\nBea\trer FAKE_VALUE',
    'failed\nto\nken=FAKE_VALUE',
    'failed\nBea\u200b\trer FAKE_VALUE',
])
@pytest.mark.parametrize('entrypoint', ['sanitize', 'core_after_sanitize'])
def test_internal_control_whitespace_regressions(text, entrypoint):
    """精确复现内外控制空白同时存在时的认证字段泄漏。"""
    result = diagnostics().sanitize_message(text, ())
    if entrypoint == 'core_after_sanitize':
        module = import_module('modules.image_host.core')
        result = module.ImageHostError(
            'upload_failed', '', diagnostic=result).diagnostic
    assert result is None


@pytest.mark.parametrize('head, tail', [
    ('Bea', 'rer FAKE_VALUE'), ('to', 'ken=FAKE_VALUE'),
    ('Autho', 'rization: Basic FAKE_VALUE'),
    (SYNTHETIC_JWT[:2], SYNTHETIC_JWT[2:]),
])
@pytest.mark.parametrize('whitespace', ['\n', '\t', '\r\n'])
@pytest.mark.parametrize('control', [
    '', '\u200b', '\x1b[31m', '\n', '\t', '\u200b\t',
])
@pytest.mark.parametrize('entrypoint', ['sanitize', 'core_after_sanitize'])
def test_separator_boundary_survives_credential_reassembly(
        head, tail, whitespace, control, entrypoint):
    """在英文前缀与重建凭证之间保留检测所需的原始空白边界。"""
    text = 'failed' + whitespace + head + control + tail
    result = diagnostics().sanitize_message(text, ())
    if entrypoint == 'core_after_sanitize':
        module = import_module('modules.image_host.core')
        result = module.ImageHostError(
            'upload_failed', '', diagnostic=result).diagnostic
    assert result is None
    assert text == 'failed' + whitespace + head + control + tail


@pytest.mark.parametrize('text', [
    '普通中文', 'mytoken=ready', 'tokenization=complete',
    'BearerCount=2', 'release.v1.ready', 'preauthorization pending',
])
@pytest.mark.parametrize('whitespace', ['\n', '\t', '\r\n'])
@pytest.mark.parametrize('control', [
    '', '\u200b', '\x1b[31m', '\n', '\t', '\u200b\t',
])
def test_separator_boundary_preserves_cleaned_ordinary_words(
        text, whitespace, control):
    """检测视图不误拒普通词语，展示结果仍按原规则删除控制空白。"""
    message = 'failed' + whitespace + text[:4] + control + text[4:]
    result = diagnostics().sanitize_message(message, ())
    assert result == 'failed' + text
    module = import_module('modules.image_host.core')
    error = module.ImageHostError('upload_failed', '', diagnostic=result)
    assert error.diagnostic == result


@pytest.mark.parametrize('text', [
    '请先绑定手机号', '不存在的储存驱动', 'tokenization=complete',
    'mytoken=ready', 'token_count=3', 'preauthorization pending',
    'BearerCount=2', 'release.v1.ready', 'keyJoint.status.ready',
])
def test_ordinary_words_are_not_credentials(text):
    """保留正常中文及仅包含认证标识片段的普通英文词语。"""
    assert diagnostics().sanitize_message(text, ()) == text


@pytest.mark.parametrize('text', [
    '\x1b[31m请先绑定手机号\x1b[0m',
    '\x1b]0;hidden title\x07请先绑定手机号',
    '\x1b]8;;https://example.test/?sig=private\x1b\\请先绑定手机号'
    '\x1b]8;;\x1b\\',
    '\x9b31m请先绑定手机号\x9b0m',
    '\x9d0;hidden title\x9c请先绑定手机号',
    '\x1b(B请先绑定手机号',
    '请\x00先\u200b绑定\u202e手机\ud800号',
])
def test_terminal_sequences_and_unicode_controls_are_cleaned(text):
    """移除终端指令、隐藏载荷和 Unicode 控制字符后保留中文。"""
    assert diagnostics().sanitize_message(text, ()) == '请先绑定手机号'


@pytest.mark.parametrize('text', ['\x1b]unclosed private', '\x1b[31'])
def test_incomplete_terminal_sequences_are_discarded(text):
    """不可靠的未闭合终端指令不作为普通文本展示。"""
    assert diagnostics().sanitize_message(text, ()) is None


@pytest.mark.parametrize('text', ['\x00\u200b', '\x1b[0m'])
def test_cleaning_to_empty_returns_none(text):
    """清理后没有可展示内容时返回空诊断。"""
    assert diagnostics().sanitize_message(text, ()) is None


def test_secrets_are_removed_before_truncation():
    """跨截断边界的备用站令牌不得留下可识别片段。"""
    text = '上传失败 ' + '甲' * 190 + SECRET + ' https://example.test/a?sig=x'
    result = diagnostics().sanitize_message(text, (SECRET, '${OTHER_TOKEN}'))
    assert result is not None
    assert len(result) <= 200
    assert SECRET not in result
    assert 'FAKE_OTHER' not in result
    assert 'https://' not in result


def test_multiple_overlapping_secrets_and_inputs_are_preserved():
    """较长秘密先替换，整链多站令牌都脱敏且输入不变。"""
    secrets = [SECRET[:10], SECRET, SECOND_SECRET, '', None, 42]
    before = secrets.copy()
    text = '上传失败 ' + SECRET + ' ' + SECOND_SECRET
    result = diagnostics().sanitize_message(text, secrets)
    assert result is not None
    assert '上传失败' in result
    assert SECRET not in result
    assert SECRET[10:] not in result
    assert SECOND_SECRET not in result
    assert secrets == before
    assert text == '上传失败 ' + SECRET + ' ' + SECOND_SECRET


@pytest.mark.parametrize('separator', [
    '\x00', '\u200b', '\u202e', '\ud800', '\x1b[31m', '\x9b31m',
    '\x1b]0;hidden title\x07', '\x9d0;hidden title\x9c',
    '\x1bPhidden payload\x1b\\', '\x1b(B', '\n', '\t', '\u200b\t',
])
@pytest.mark.parametrize('prefix', ['', '上传失败 ', '甲' * 190])
@pytest.mark.parametrize('secret_has_controls', [False, True])
@pytest.mark.parametrize('long_first', [False, True])
def test_overlapping_secrets_split_by_controls_are_fully_redacted(
        separator, prefix, secret_has_controls, long_first):
    """包含关系与控制清理组合时完整脱敏，截断前不残留秘密后缀。"""
    short = 'FAKE_PREFIX'
    suffix = '_PRIVATE_SUFFIX'
    split_secret = short + separator + suffix
    long = split_secret if secret_has_controls else short + suffix
    secrets = [long, short] if long_first else [short, long]
    before = secrets.copy()
    text = prefix + split_secret
    result = diagnostics().sanitize_message(text, secrets)
    assert result == (prefix + '[已隐藏]')[:200]
    assert secrets == before
    assert text == prefix + split_secret


@pytest.mark.parametrize('separator', ['\x00', '\u200b', '\x1b[31m'])
def test_secret_split_by_controls_is_redacted_again(separator):
    """清理控制字符重新拼成的秘密仍需再次脱敏。"""
    text = '上传失败 ' + SECRET[:8] + separator + SECRET[8:]
    result = diagnostics().sanitize_message(text, (SECRET,))
    assert result is not None
    assert '上传失败' in result
    assert 'FAKE_OTHER' not in result
    assert SECRET not in result


@pytest.mark.parametrize('text', [
    '上传失败 ht\u200btps://example.test/a?sig=private',
    '上传失败 $\u200b{OTHER_TOKEN}',
])
def test_cleaning_cannot_reassemble_sensitive_references(text):
    """清理后的链接和环境引用重新检测并移除。"""
    result = diagnostics().sanitize_message(text, ())
    assert result is not None
    assert '上传失败' in result
    assert 'private' not in result
    assert 'OTHER_TOKEN' not in result
    assert 'https://' not in result


@pytest.mark.parametrize('text', [
    'Autho\u200brization: private', 'Bea\x00rer private',
    '<ht\u200bml>private</html>', 'to\x00ken=private',
])
def test_cleaning_cannot_reassemble_credentials_or_html(text):
    """控制字符清理后再次拒绝认证结构与页面内容。"""
    assert diagnostics().sanitize_message(text, ()) is None


def test_scope_default_disabled_and_explicit_empty_enabled():
    """默认关闭与显式无秘密的开启状态语义不同。"""
    module = diagnostics()
    assert module.safe_current_message('请先绑定手机号') is None
    with module.diagnostic_scope(()):
        assert module.safe_current_message('请先绑定手机号') == '请先绑定手机号'
    assert module.safe_current_message('请先绑定手机号') is None


def test_nested_scope_restores_secret_sets_and_disabled_state():
    """嵌套关闭与替换秘密集合后精确恢复外层作用域。"""
    module = diagnostics()
    text = '上传失败 ' + SECRET
    with module.diagnostic_scope((SECRET,)):
        outer = module.safe_current_message(text)
        assert outer is not None and SECRET not in outer
        with module.diagnostic_scope(None):
            assert module.safe_current_message(text) is None
        assert module.safe_current_message(text) == outer
        with module.diagnostic_scope(()):
            assert module.safe_current_message(text) == text
        assert module.safe_current_message(text) == outer
    assert module.safe_current_message(text) is None


def test_scope_snapshots_caller_secret_container():
    """外部可变容器的后续修改不更改已进入的上下文秘密。"""
    module = diagnostics()
    secrets = [SECRET]
    with module.diagnostic_scope(secrets):
        assert secrets == [SECRET]
        secrets.clear()
        result = module.safe_current_message('上传失败 ' + SECRET)
        assert result is not None and SECRET not in result


@pytest.mark.parametrize('error_type', [
    RuntimeError, KeyboardInterrupt, SystemExit,
])
def test_exception_exit_restores_context_and_preserves_object(error_type):
    """普通异常和两个控制信号原对象传播并恢复外层状态。"""
    module = diagnostics()
    error = error_type('stop')
    with module.diagnostic_scope(()):
        with pytest.raises(error_type) as caught:
            with module.diagnostic_scope(None):
                raise error
        assert caught.value is error
        assert module.safe_current_message('请先绑定手机号') == '请先绑定手机号'
    assert module.safe_current_message('请先绑定手机号') is None


def test_copied_contexts_are_isolated():
    """复制上下文保持各自状态且不能影响当前执行上下文。"""
    module = diagnostics()
    disabled = copy_context()
    with module.diagnostic_scope((SECRET,)):
        enabled = copy_context()
        assert disabled.run(module.safe_current_message, '普通中文') is None
    assert module.safe_current_message('普通中文') is None
    assert enabled.run(module.safe_current_message, '普通中文') == '普通中文'
    result = enabled.run(module.safe_current_message, '上传失败 ' + SECRET)
    assert result is not None and SECRET not in result

    def temporarily_disable():
        """仅在复制的上下文中临时关闭诊断。"""
        with module.diagnostic_scope(None):
            assert module.safe_current_message('普通中文') is None

    enabled.run(temporarily_disable)
    assert enabled.run(module.safe_current_message, '普通中文') == '普通中文'
    assert disabled.run(module.safe_current_message, '普通中文') is None
