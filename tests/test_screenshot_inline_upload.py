#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证截图 CLI 内联图床配置的严格语法与凭证快照。"""

import traceback

import pytest
from ruamel.yaml import YAML

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


@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('body', [
    '<<: {provider: catbox}, token: FIRST_SECRET, token: SECOND_SECRET',
    'provider: catbox, options: {<<: {mode: default}, mode: first, mode: second}',
    '<<: {provider: catbox, token: FIRST_SECRET, token: SECOND_SECRET}',
    'provider: catbox, options: {<<: [{mode: first, mode: second}]}',
])
def test_merge_cannot_hide_explicit_duplicate_keys(body, wrapped):
    """合并前的显式重复键必须拒绝，且异常不保留输入。"""
    text = '{' + body + '}' if wrapped else body
    with pytest.raises(InlineUploadError) as caught:
        parse_inline_hosts(text)
    assert caught.value.code == 'mapping_syntax'
    assert 'SECRET' not in ''.join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


@pytest.mark.parametrize('body', [
    '<<: {provider: beeimg, token: default}, provider: catbox, token: override',
    '<<: [{provider: catbox, token: first}, {token: second}], token: override',
    'provider: catbox, token: override, options: {<<: &defaults {mode: default}, mode: custom}',
])
def test_merge_defaults_allow_explicit_overrides(body):
    """正常合并默认值及显式覆盖仍遵循安全 YAML 语义。"""
    host = parse_inline_hosts(body)[0]
    assert host['provider'] == 'catbox'
    assert host['token'] == 'override'
    if host['options']:
        assert host['options']['mode'] == 'custom'


@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize(('value', 'expected'), [
    ('&credential "a;b"', 'a;b'),
    ('&a;b "x;y"', 'x;y'),
    ('&a;b "};["', '};['),
    ("&a;b '};['", '};['),
    ('&a;b !!str "x;y"', 'x;y'),
    ('!!str &a;b "x;y"', 'x;y'),
    ('!!str "a;b"', 'a;b'),
    ('&credential !!str "a;b"', 'a;b'),
    ('!!str &credential "a;b"', 'a;b'),
    ('!<tag:yaml.org,2002:str> "a;b"', 'a;b'),
    ('&credential "a\\\";b"', 'a";b'),
    ("&credential 'a'';b'", "a';b"),
    ("&credential don't", "don't"),
    ("don't", "don't"),
])
def test_node_properties_preserve_quoted_delimiters(value, expected, wrapped):
    """节点属性后的引号仍保护分号，普通撇号不改变分段。"""
    body = 'provider: catbox, token: ' + value
    body = '{' + body + '}' if wrapped else body
    hosts = parse_inline_hosts(body + '; provider: catbox')
    assert len(hosts) == 2
    assert hosts[0]['token'] == expected


@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('separator', ['', ' ; provider: catbox'])
@pytest.mark.parametrize('body', [
    'provider: &a;b catbox, token: *a;b',
    'provider: &a;b catbox, token: *a;b, options: {copy: *a;b}',
    'provider: &a;b catbox, options: {copies: [*a;b, {copy: *a;b}]}, token: *a;b',
])
def test_semicolon_alias_preserves_safe_yaml_semantics(body, wrapped, separator):
    """别名名称中的分号不是分隔符，空白终止后的分号仍可分项。"""
    expected = YAML(typ='safe').load('{' + body + '}')
    expected.setdefault('options', {})
    text = '{' + body + '}' if wrapped else body
    hosts = parse_inline_hosts(text + separator)
    assert hosts[0] == expected
    assert len(hosts) == (2 if separator else 1)
    if separator:
        assert hosts[1] == {'provider': 'catbox', 'options': {}}


def test_nested_property_quotes_and_aliases():
    """嵌套属性值中的括号不参与状态机，别名保持原值。"""
    host = parse_inline_hosts(
        'provider: catbox, token: &credential "a;b", '
        'options: {copy: *credential, value: !!str "};["}')[0]
    assert host['options'] == {'copy': 'a;b', 'value': '};['}


@pytest.mark.parametrize('value', [
    '&credential "SECRET;a', '!!str \'SECRET;a',
    '&a;b "SECRET;};[', "&a;b 'SECRET;};[",
])
def test_unclosed_property_quote_is_private(value):
    """属性后未闭合引号只产生固定错误且不保留敏感异常链。"""
    with pytest.raises(InlineUploadError) as caught:
        parse_inline_hosts('provider: catbox, token: ' + value)
    assert 'SECRET' not in ''.join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


@pytest.mark.parametrize('options', [
    '{=: value}',
    '{<<: {=: default}, =: value}',
    '{<<: [{=: value}]}',
])
def test_value_tag_key_preserves_safe_mapping_semantics(options):
    """等号键遵循安全构造器的字符串转换与合并覆盖语义。"""
    host = parse_inline_hosts('provider: catbox, options: ' + options)[0]
    assert host['options'] == {'=': 'value'}


@pytest.mark.parametrize('options', [
    '{=: FIRST_SECRET, "=": SECOND_SECRET}',
    '{<<: {mode: default}, =: FIRST_SECRET, "=": SECOND_SECRET}',
    '{<<: {=: FIRST_SECRET, "=": SECOND_SECRET}}',
])
def test_duplicate_value_tag_key_is_private(options):
    """等号键规范化后仍拒绝重复键且不泄露敏感值。"""
    with pytest.raises(InlineUploadError) as caught:
        parse_inline_hosts('provider: catbox, options: ' + options)
    assert caught.value.code == 'mapping_syntax'
    assert 'SECRET' not in ''.join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('separator', ['', '; provider: catbox'])
@pytest.mark.parametrize('key', [
    '? "x]"', "? 'x;[}'", '?\n "x]"', '? &key "x]"',
    '? !!str "x]"', '?name', 'what?',
])
def test_explicit_mapping_key_preserves_boundaries(key, wrapped, separator):
    """显式键标记保持节点起始，普通问号仍属于标量。"""
    body = 'provider: catbox, options: {' + key + ': value}'
    expected = YAML(typ='safe').load('{' + body + '}')
    text = '{' + body + '}' if wrapped else body
    hosts = parse_inline_hosts(text + separator)
    assert hosts[0] == expected
    assert len(hosts) == (2 if separator else 1)


@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('separator', ['', '; provider: catbox'])
@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
@pytest.mark.parametrize('body', [
    'provider: catbox, token: abc # ]{;\'"',
    'provider: catbox, token: "abc" # ]{;\'"',
    'provider: catbox, # ]{;\'"{nl} token: abc',
    'provider: catbox, options: {? # ]{;\'"{nl} "x]": value}, token: abc',
])
def test_yaml_comments_preserve_boundaries(body, newline, wrapped, separator):
    """注释中的结构字符无效，换行后恢复节点和分项边界。"""
    body = body.replace('{nl}', newline) + newline
    expected = YAML(typ='safe').load('{' + body + '}')
    expected.setdefault('options', {})
    text = '{' + body + '}' if wrapped else body
    hosts = parse_inline_hosts(text + separator)
    assert hosts[0] == expected
    assert len(hosts) == (2 if separator else 1)


@pytest.mark.parametrize('value', ['abc#def', 'abc#def"', 'abc#def\''])
def test_plain_hash_does_not_hide_item_separator(value):
    """普通标量内非分隔井号不能吞掉后续分项。"""
    hosts = parse_inline_hosts('provider: catbox, token: ' + value + '; provider: catbox')
    assert hosts[0]['token'] == value
    assert len(hosts) == 2


def test_non_anonymous_provider_requires_token():
    """不支持匿名的图床省略凭证时拒绝。"""
    with pytest.raises(InlineUploadError):
        parse_inline_hosts('provider: wmimg')


@pytest.mark.parametrize(('body', 'expected'), [
    ('provider: catbox, token: `a;b,#{}\\x`', 'a;b,#{}\\x'),
    ('{provider: catbox, token: `a;b`}', 'a;b'),
    ('provider: catbox, token: ${ENV_NAME}', '${ENV_NAME}'),
    ('{provider: catbox, token: ${IMAGE_TOKEN}}', '${IMAGE_TOKEN}'),
    ('provider: beeimg_cn, token: `a`; provider: catbox', None),
])
def test_backtick_and_environment_tokens_are_literal(body, expected):
    """顶层 token 的成对反引号与裸环境引用保留为字面字符串。"""
    hosts = parse_inline_hosts(body)
    if expected is None:
        assert len(hosts) == 2 and hosts[0]['token'] == 'a'
    else:
        assert len(hosts) == 1 and hosts[0]['token'] == expected


@pytest.mark.parametrize('separator', [
    ', options: {storage_id: 1}', ' # comment\n', '; provider: catbox',
])
def test_backtick_token_terminators_do_not_shift(separator):
    """反引号标量后的合法终止边界不串位到相邻字段或分项。"""
    hosts = parse_inline_hosts('provider: catbox, token: `a`' + separator)
    assert hosts[0]['token'] == 'a'
    if separator.startswith(';'):
        assert len(hosts) == 2
    else:
        assert len(hosts) == 1


@pytest.mark.parametrize('body', [
    'provider: catbox, token: `unclosed',
    'provider: catbox, token: `multi\nline`',
    'provider: catbox, token: `a`b',
])
def test_broken_backtick_token_is_private_parameter_error(body):
    """缺失闭合、跨行与尾随垃圾只产生固定错误且不回显原文。"""
    with pytest.raises(InlineUploadError) as caught:
        parse_inline_hosts(body)
    assert caught.value.code == 'mapping_syntax'
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_backtick_outside_token_is_not_a_wrapper():
    """非白名单位置的反引号不成为包裹符，交由 YAML 拒绝。"""
    with pytest.raises(InlineUploadError):
        parse_inline_hosts('provider: catbox, options: {note: `x`}')


def test_merge_default_allows_backtick_override():
    """合并默认值被反引号显式键覆盖时保留覆盖语义。"""
    host = parse_inline_hosts(
        '<<: {provider: catbox, token: base}, token: `override`')[0]
    assert host['provider'] == 'catbox'
    assert host['token'] == 'override'


_BARE_TOKEN_VALUES = (
    ('abc', 'abc'),
    ('123|offline-token', '123|offline-token'),
    ('abc#def', 'abc#def'),
    ('abc！def', 'abc！def'),
    ('abc%def', 'abc%def'),
    ('abc&def', 'abc&def'),
    ('abc@def', 'abc@def'),
)

_WRAPPED_TOKEN_VALUES = (
    '|abc', '#abc', '!abc', '！abc', '%abc', '&abc', '@abc',
    'abc # def', 'a,b:c;d', ' abc ', 'a\\b',
)

_TOKEN_FORMS = ('bare', 'single', 'double', 'backtick', 'brace', 'bracket')

_WRAPPER_SECRET = 'SECRET_WRAPPER_9c31'

_HOST_OPTIONS = {'storage_id': 1}


def _yaml_double(text):
    """按 YAML 双引号规则编码字面内容，仅用于构造测试源。"""
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _yaml_single(text):
    """按 YAML 单引号规则编码字面内容，仅用于构造测试源。"""
    return "'" + text.replace("'", "''") + "'"


def _token_source(form, text):
    """返回 token 值使用的 YAML 源片段。"""
    if form == 'single':
        return _yaml_single(text)
    if form == 'double':
        return _yaml_double(text)
    if form == 'backtick':
        return '`' + text + '`'
    if form == 'brace':
        return '{' + text + '}'
    if form == 'bracket':
        return '[' + text + ']'
    return text


def _token_expectations(form):
    """按包裹形式返回参与的固定值与期望字面值。"""
    pairs = list(_BARE_TOKEN_VALUES)
    if form != 'bare':
        pairs += [(value, value) for value in _WRAPPED_TOKEN_VALUES]
    return pairs


def _matrix_text(outer, form):
    """构造覆盖全部矩阵值的 CLI 内联文本，逐项带固定其他字段。"""
    items = []
    for value, _ in _token_expectations(form):
        body = ('provider: catbox, token: ' + _token_source(form, value)
                + ', options: {storage_id: 1}')
        items.append(('{' + body + '}') if outer else body)
    return '; '.join(items)


@pytest.mark.parametrize('outer', [False, True])
@pytest.mark.parametrize('form', _TOKEN_FORMS)
def test_token_wrapper_matrix(outer, form):
    """显式整项与自动补整项两条入口下五种包裹与合法裸写得到同一字面值。"""
    hosts = parse_inline_hosts(_matrix_text(outer, form))
    expected = [value for _, value in _token_expectations(form)]
    assert [host['token'] for host in hosts] == expected
    assert [host['provider'] for host in hosts] == ['catbox'] * len(expected)
    assert [host['options'] for host in hosts] == [
        dict(_HOST_OPTIONS)] * len(expected)
    for host, token in zip(hosts, expected):
        prepared, error = prepare_cli_host(host)
        assert error is None
        assert prepared['token'] == token


_CLI_WRAPPER_BOUNDARIES = (
    ('provider: catbox, token: {a;b}; provider: catbox', ['a;b', None]),
    ('{provider: catbox, token: {a;b}}; provider: catbox', ['a;b', None]),
    ('provider: catbox, token: abc #def\n', ['abc']),
    ('provider: catbox, token: {' + _WRAPPER_SECRET, None),
    ('provider: catbox, token: [' + _WRAPPER_SECRET, None),
    ('provider: catbox, token: {' + _WRAPPER_SECRET + ']', None),
    ('provider: catbox, token: [' + _WRAPPER_SECRET + '}', None),
    ('provider: catbox, token: {' + _WRAPPER_SECRET + '}x', None),
    ('provider: catbox, token: [' + _WRAPPER_SECRET + ']x', None),
    ('provider: catbox, token: {' + _WRAPPER_SECRET + '\n}', None),
    ('{provider: catbox, token: {' + _WRAPPER_SECRET + '\n}}', None),
    ('provider: catbox, token: {}', None),
    ('provider: catbox, token: []', None),
    ('provider: catbox, token: %abc', None),
    ('provider: catbox, token: @abc', None),
    ('provider: catbox, token: !abc', None),
    ('provider: catbox, token: |abc', None),
    ('provider: catbox, token: &abc', None),
    ('provider: catbox, token: #abc', None),
)


@pytest.mark.parametrize('text, expected', _CLI_WRAPPER_BOUNDARIES)
def test_token_wrapper_boundaries(text, expected):
    """外壳分项边界、注释边界与非法裸起始符保持固定结果且不回显原文。"""
    if expected is None:
        with pytest.raises(InlineUploadError) as caught:
            parse_inline_hosts(text)
        rendered = ''.join(traceback.format_exception(caught.value))
        # 整条命令与其中秘密都必须缺席，后者才能独立证明凭证未被回显。
        assert text not in rendered
        assert _WRAPPER_SECRET not in rendered
        return
    hosts = parse_inline_hosts(text)
    assert [host.get('token') for host in hosts] == expected


@pytest.mark.parametrize('outer', [False, True])
@pytest.mark.parametrize('source, expected', (
    ('{abc}', 'abc'),
    ('[abc]', 'abc'),
    ('{[abc]}', '[abc]'),
    ("'{abc}'", '{abc}'),
    ('"[abc]"', '[abc]'),
    ('{123}', '123'),
    ('{ abc }', ' abc '),
    ('${IMAGE_TOKEN}', '${IMAGE_TOKEN}'),
    ('{${IMAGE_TOKEN}}', '${IMAGE_TOKEN}'),
))
def test_token_wrapper_preserves_yaml_semantics(outer, source, expected):
    """单层包裹、已引用括号、数字与环境引用保持既有 YAML 语义。"""
    body = 'provider: catbox, token: ' + source
    text = ('{' + body + '}') if outer else body
    assert parse_inline_hosts(text)[0]['token'] == expected


@pytest.mark.parametrize('source, expected', (
    ("{a'b}", "a'b"),
    ('{a"b}', 'a"b'),
    ('{a`b}', 'a`b'),
    ("[a'b]", "a'b"),
    ('[a"b]', 'a"b'),
    ('[a`b]', 'a`b'),
))
def test_token_wrapper_keeps_inner_quotes_literal(source, expected):
    """内联外壳内部的单双引号与反引号是字面字符，不触发 YAML 转义。"""
    hosts = parse_inline_hosts('provider: catbox, token: ' + source)
    assert [host.get('token') for host in hosts] == [expected]


def test_token_wrapper_keeps_other_containers():
    """非 token 字段的花括号仍按 YAML 容器解释，整项外层括号不被消费。"""
    host = parse_inline_hosts(
        'provider: catbox, options: {note: {abc}}, token: plain')[0]
    assert host['options'] == {'note': {'abc': None}}
    assert host['token'] == 'plain'
    hosts = parse_inline_hosts(
        '{provider: catbox, token: plain}; provider: catbox')
    assert hosts[0] == {'provider': 'catbox', 'token': 'plain', 'options': {}}
    assert len(hosts) == 2
