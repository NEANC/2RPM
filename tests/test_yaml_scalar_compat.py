#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""验证白名单字段的受限词法兼容与原始重复键语义。"""

import os
import sys
import traceback

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import config as config_module
from modules.screenshot.inline_upload import InlineUploadError
from modules.screenshot.inline_upload import parse_inline_hosts
from modules.yaml_scalar_compat import ScalarSyntaxError
from modules.yaml_scalar_compat import normalize_config_scalars
from modules.yaml_scalar_compat import normalize_inline_upload


PUSH_HEAD = 'push:\n  screenshot:\n'
TARGETS_HEAD = PUSH_HEAD + '    targets:\n'
HOSTS_HEAD = PUSH_HEAD + '    image_host:\n'
MONITOR_HEAD = 'monitor:\n  mode: psutil\n  psutil:\n    process_name: ok.exe\n'


def _write_raw(tmp_path, text):
    """把原始 YAML 文本写入独立临时文件，供真实加载流程使用。"""
    path = tmp_path / 'raw-config.yaml'
    path.write_text(text, encoding='utf-8')
    return path


def test_whitelisted_backticks_and_flow_tokens(tmp_path):
    """反引号字面值与 flow 裸花括号标量经真实加载得到字符串原值。"""
    text = (
        MONITOR_HEAD
        + PUSH_HEAD
        + '    targets:\n'
        + '      - {provider: window, target: App@{version}, out: shot}\n'
        + '      - {provider: adb, target: `127.0.0.1:16384`, out: device}\n'
        + '    image_host:\n'
        + '      - {provider: beeimg_cn, token: ${IMAGE_TOKEN}, options: {storage_id: 1}}\n'
        + '      - {provider: catbox, token: `a;b,#{}\\x`}\n'
    )
    path = _write_raw(tmp_path, text)
    screenshot = config_module.load_config(str(path))['push']['screenshot']
    assert [item['target'] for item in screenshot['targets']] == [
        'App@{version}', '127.0.0.1:16384',
    ]
    assert [item['out'] for item in screenshot['targets']] == ['shot', 'device']
    assert screenshot['image_host'][0]['token'] == '${IMAGE_TOKEN}'
    assert screenshot['image_host'][0]['options'] == {'storage_id': 1}
    assert screenshot['image_host'][1]['token'] == 'a;b,#{}\\x'


@pytest.mark.parametrize('marker', ['@{version}', '@{VERSION}', '@{Version}'])
def test_flow_and_block_version_markers_keep_original_value(marker):
    """flow 与块映射中的三种版本标记都保留为字符串原值。"""
    flow = normalize_config_scalars(
        TARGETS_HEAD + f'      - {{provider: window, target: App{marker}, out: shot}}\n'
    )
    assert f"target: 'App{marker}', out: shot" in flow
    block = (
        TARGETS_HEAD
        + '      - provider: window\n'
        + f'        target: App{marker}\n'
        + '        out: shot\n'
    )
    assert normalize_config_scalars(block) == block


@pytest.mark.parametrize('key', ['"target"', "'target'", 'target'])
def test_quoted_whitelist_target_key_keeps_version_semantics(key):
    """引号包裹的白名单 target 键与裸键获得同样的版本标记规范化。"""
    text = (TARGETS_HEAD + '      - {provider: window, ' + key
            + ': App@{version}, out: shot}\n')
    normalized = normalize_config_scalars(text)
    assert key + ": 'App@{version}', out: shot" in normalized


@pytest.mark.parametrize('key', ['"token"', "'token'", 'token'])
def test_quoted_whitelist_token_key_normalizes_like_bare_key(key):
    """引号包裹的白名单 token 键与裸键同样规范化反引号字面值。"""
    text = HOSTS_HEAD + '      - {provider: catbox, ' + key + ': `a;b`}\n'
    assert normalize_config_scalars(text) == (
        HOSTS_HEAD + '      - {provider: catbox, ' + key + ": 'a;b'}\n")


@pytest.mark.parametrize('key', ['"to\\u006ben"', '"\\u0074oken"'])
def test_flow_double_quoted_token_key_uses_yaml_scalar_semantics(key):
    """双引号键按 YAML 转义语义命中 token，键文本保持原样。"""
    text = HOSTS_HEAD + '      - {provider: catbox, ' + key + ': `a;b`}\n'
    assert normalize_config_scalars(text) == (
        HOSTS_HEAD + '      - {provider: catbox, ' + key + ": 'a;b'}\n")


@pytest.mark.parametrize('key', ['"to\\u006benx"', '"\\u0074oken "', "'to''ken'"])
def test_flow_quoted_key_with_different_yaml_value_is_not_token(key):
    """转义解码后不等于 token 的单双引号键不获得宽松语法。"""
    text = HOSTS_HEAD + '      - {provider: catbox, ' + key + ': `a;b`}\n'
    assert normalize_config_scalars(text) == text


def test_flow_single_quoted_token_key_uses_yaml_single_quote_semantics():
    """单引号键按 YAML 双单引号规则判定并逐字保留。"""
    text = (HOSTS_HEAD
            + "      - {provider: catbox, 'token': `a;b`, 'to''ken': keep}\n")
    assert normalize_config_scalars(text) == (
        HOSTS_HEAD
        + "      - {provider: catbox, 'token': 'a;b', 'to''ken': keep}\n")


@pytest.mark.parametrize('key', ['"to\\u006ben"', "'token'"])
def test_block_quoted_token_key_uses_yaml_scalar_semantics(key):
    """块映射引号键按 YAML 语义命中 token，同时保持键原文。"""
    text = (HOSTS_HEAD + '      - provider: catbox\n'
            + f'        {key}: `a;b`\n')
    assert normalize_config_scalars(text) == (
        HOSTS_HEAD + '      - provider: catbox\n'
        + f"        {key}: 'a;b'\n")


@pytest.mark.parametrize('key', ['"to\\u006benx"', "'to''ken'"])
def test_block_quoted_non_token_key_keeps_original_value(key):
    """块映射引号键解码后非 token 时不获得宽松值语法。"""
    text = (HOSTS_HEAD + '      - provider: catbox\n'
            + f'        {key}: `a;b`\n')
    assert normalize_config_scalars(text) == text


def test_quoted_non_whitelist_key_stays_unmodified():
    """引号包裹的非白名单键不获得宽松词法，反引号保持原样。"""
    text = HOSTS_HEAD + '      - {provider: catbox, "note": `x`}\n'
    assert normalize_config_scalars(text) == text


def test_quoted_whitelist_keys_load_to_same_values(tmp_path):
    """引号包裹的白名单键经真实加载得到与裸键相同的语义值。"""
    text = (
        MONITOR_HEAD
        + PUSH_HEAD
        + '    targets:\n'
        + '      - {provider: window, "target": App@{version}, out: shot}\n'
        + "      - {provider: adb, 'target': `127.0.0.1:16384`, out: device}\n"
        + '    image_host:\n'
        + '      - {provider: beeimg_cn, "token": ${IMAGE_TOKEN}, '
        + 'options: {storage_id: 1}}\n'
        + "      - {provider: catbox, 'token': `a;b,#{}\\x`}\n"
    )
    path = _write_raw(tmp_path, text)
    screenshot = config_module.load_config(str(path))['push']['screenshot']
    assert [item['target'] for item in screenshot['targets']] == [
        'App@{version}', '127.0.0.1:16384',
    ]
    assert [item['out'] for item in screenshot['targets']] == ['shot', 'device']
    assert screenshot['image_host'][0]['token'] == '${IMAGE_TOKEN}'
    assert screenshot['image_host'][0]['options'] == {'storage_id': 1}
    assert screenshot['image_host'][1]['token'] == 'a;b,#{}\\x'


def test_quoted_non_whitelist_key_is_rejected_overall(tmp_path):
    """非白名单引号键的反引号值仍使整份配置加载失败。"""
    path = _write_raw(
        tmp_path,
        MONITOR_HEAD
        + HOSTS_HEAD
        + '      - {provider: catbox, "note": `x`}\n',
    )
    with pytest.raises(SystemExit) as error:
        config_module.load_config(str(path))
    assert error.value.code == 1


def test_non_whitelist_and_block_scalars_unchanged():
    """白名单之外的字段与块标量正文保持逐字节不变。"""
    text = (
        'push:\n  screenshot:\n    image_host:\n'
        '      - {provider: catbox, token: plain, options: {note: `keep`, id: 1}}\n'
        '    templates:\n      on_end:\n        content: |\n'
        '          token: `not-a-pair`\n'
        '          target: App@{version}\n'
    )
    assert normalize_config_scalars(text) == text


def test_non_whitelist_same_name_key_unchanged():
    """同名键只有在精确白名单路径下才获得宽松词法。"""
    text = (
        'push:\n  other:\n    targets:\n'
        '      - {provider: window, target: App@{version}, out: shot}\n'
    )
    assert normalize_config_scalars(text) == text


def test_normalize_inline_upload_transforms_token_only():
    """内联文本只转换顶层 token 的反引号与环境引用。"""
    assert normalize_inline_upload('provider: catbox, token: `a;b`') == (
        "provider: catbox, token: 'a;b'")
    assert normalize_inline_upload('provider: catbox, token: ${ENV_NAME}') == (
        "provider: catbox, token: '${ENV_NAME}'")
    assert normalize_inline_upload(
        'provider: catbox, token: `a;b`; provider: catbox') == (
        "provider: catbox, token: 'a;b'; provider: catbox")
    assert normalize_inline_upload('provider: catbox, token: plain') == (
        'provider: catbox, token: plain')
    assert normalize_inline_upload(
        'provider: catbox, options: {note: `x`}') == (
        'provider: catbox, options: {note: `x`}')


@pytest.mark.parametrize('text, code', [
    ('provider: catbox, token: `unclosed', 'backtick_unterminated'),
    ('provider: catbox, token: `multi\nline`', 'backtick_multiline'),
    ('provider: catbox, token: `a`b', 'backtick_trailing'),
])
def test_normalize_inline_upload_rejects_broken_backticks(text, code):
    """缺失闭合、跨行与尾随垃圾只产生固定类别错误。"""
    with pytest.raises(ScalarSyntaxError) as caught:
        normalize_inline_upload(text)
    assert caught.value.code == code
    assert text not in ''.join(traceback.format_exception(caught.value))


@pytest.mark.parametrize('text, index', [
    ('provider: catbox, token: `unclosed; provider: catbox, token: ok', 1),
    ('provider: catbox, token: a; provider: catbox, token: `unclosed', 2),
    ('provider: catbox, token: a; provider: catbox, token: b; '
     'provider: catbox, token: `x`y', 3),
])
def test_inline_lexical_error_index_matches_real_item(text, index):
    """内联词法错误序号指向真实出错项，整体拒绝且不回显原文。"""
    with pytest.raises(InlineUploadError) as caught:
        parse_inline_hosts(text)
    assert caught.value.code == 'mapping_syntax'
    assert caught.value.index == index
    assert text not in ''.join(traceback.format_exception(caught.value))


def test_inline_lexical_error_index_ignores_backtick_separators():
    """前项反引号内的分隔符不改变后续出错项序号。"""
    with pytest.raises(InlineUploadError) as caught:
        parse_inline_hosts(
            'provider: catbox, token: `a;b`; provider: catbox, token: `unclosed')
    assert caught.value.index == 2


def test_backtick_with_separators_is_single_item():
    """反引号内含分号、逗号与右花括号时不得被误切分为多项或被拒绝。"""
    hosts = parse_inline_hosts('provider: catbox, token: `a;b,c}d`')
    assert len(hosts) == 1
    assert hosts[0]['token'] == 'a;b,c}d'


def test_backtick_and_environment_tokens_parse_through_cli_path():
    """内联解析整链接受反引号与环境引用，并保留相邻字段。"""
    host = parse_inline_hosts(
        'provider: beeimg_cn, token: ${IMAGE_TOKEN}, options: {storage_id: 1}')[0]
    assert host['token'] == '${IMAGE_TOKEN}'
    assert host['options'] == {'storage_id': 1}
    literal = parse_inline_hosts('provider: catbox, token: `a;b,#{}\\x`')[0]
    assert literal['token'] == 'a;b,#{}\\x'


@pytest.mark.parametrize('key', ['"token"', "'token'", 'token'])
@pytest.mark.parametrize('value', ['`a;b,c}d`', '${IMAGE_TOKEN}'])
def test_inline_top_level_token_key_forms_share_relaxed_values(key, value):
    """CLI 顶层单双引号 token 键与裸键共享受限宽松值语法。"""
    host = parse_inline_hosts(f'provider: catbox, {key}: {value}')[0]
    assert host == {
        'provider': 'catbox',
        'token': value[1:-1] if value.startswith('`') else value,
        'options': {},
    }


@pytest.mark.parametrize('key', ['"to\\u006ben"', "'token'"])
def test_inline_quoted_token_key_uses_yaml_scalar_semantics(key):
    """CLI 引号键按 YAML 语义命中 token。"""
    host = parse_inline_hosts(
        f'provider: catbox, {key}: `a;b,c}}d`')[0]
    assert host['token'] == 'a;b,c}d'


@pytest.mark.parametrize('key', ['"to\\u006benx"', "'to''ken'"])
def test_inline_quoted_non_token_key_rejects_relaxed_value(key):
    """CLI 引号键解码后非 token 时仍拒绝宽松值语法。"""
    with pytest.raises(InlineUploadError) as caught:
        parse_inline_hosts(f'provider: catbox, {key}: `secret`')
    assert caught.value.code == 'mapping_syntax'
    assert caught.value.index == 1


@pytest.mark.parametrize('body', [
    'provider: catbox, x.token: `secret`',
    'provider: catbox, x.token: ${IMAGE_TOKEN}',
    'provider: catbox, options: {token: `secret`}',
    'provider: catbox, options: {"token": ${IMAGE_TOKEN}}',
])
def test_inline_non_top_level_token_names_reject_relaxed_values(body):
    """CLI 非顶层同名键不能取得 token 的宽松值语法。"""
    with pytest.raises(InlineUploadError) as caught:
        parse_inline_hosts(body)
    assert caught.value.code == 'mapping_syntax'
    assert caught.value.index == 1
    assert body not in ''.join(traceback.format_exception(caught.value))


def test_inline_rejected_item_index_ignores_quoted_token_separators():
    """引号 token 的反引号分隔符不干扰后续拒绝项序号。"""
    text = ('provider: catbox, "token": `a;b,c}d`; '
            'provider: catbox, x.token: `secret`')
    with pytest.raises(InlineUploadError) as caught:
        parse_inline_hosts(text)
    assert caught.value.code == 'mapping_syntax'
    assert caught.value.index == 2
    assert text not in ''.join(traceback.format_exception(caught.value))


@pytest.mark.parametrize('key', ['" token "', "' token '"])
@pytest.mark.parametrize('value', ['`secret`', '${IMAGE_TOKEN}'])
def test_config_quoted_whitespace_token_key_is_not_whitelisted(
        tmp_path, caplog, key, value):
    """配置 flow 引号键按 YAML 值精确匹配，不折叠键内空白。"""
    text = (MONITOR_HEAD + HOSTS_HEAD
            + f'      - {{provider: catbox, {key}: {value}}}\n')
    path = _write_raw(tmp_path, text)
    with pytest.raises(SystemExit) as caught:
        config_module.load_config(str(path))
    assert caught.value.code == 1
    assert path.read_text(encoding='utf-8') == text
    assert text not in caplog.text + ''.join(
        traceback.format_exception(caught.value))


@pytest.mark.parametrize('value', ['`secret`', '${IMAGE_TOKEN}'])
def test_config_nested_token_key_is_not_whitelisted(tmp_path, value):
    """配置 flow 嵌套 options.token 不继承图床顶层 token 白名单。"""
    text = (MONITOR_HEAD + HOSTS_HEAD
            + '      - {provider: catbox, token: ok, '
            + f'options: {{token: {value}}}}}\n')
    path = _write_raw(tmp_path, text)
    with pytest.raises(SystemExit) as caught:
        config_module.load_config(str(path))
    assert caught.value.code == 1
    assert path.read_text(encoding='utf-8') == text


@pytest.mark.parametrize('body', [
    'provider: catbox, token: a, token: b',
    '<<: {provider: catbox}, <<: {token: base}',
    'provider: catbox, options: {<<: {mode: first, mode: second}}',
    'provider: catbox, options: {<<: [{mode: first, mode: second}]}',
])
def test_raw_duplicates_rejected_but_merge_override_allowed(body):
    """原始映射重复键固定拒绝，merge 默认值可被显式键覆盖。"""
    with pytest.raises(InlineUploadError) as caught:
        parse_inline_hosts(body)
    assert caught.value.code == 'mapping_syntax'
    assert 'SECRET' not in ''.join(traceback.format_exception(caught.value))
    host = parse_inline_hosts(
        '<<: {provider: catbox, token: base}, token: `override`')[0]
    assert host['token'] == 'override'
    assert host['provider'] == 'catbox'
    merged = parse_inline_hosts(
        '<<: [{provider: catbox, token: first}, {token: second}]')[0]
    assert merged['token'] == 'first'


def test_lexical_pass_does_not_read_environment(monkeypatch):
    """词法阶段只保留引用文本，不读取环境变量。"""
    class Forbidden(dict):
        """任何环境读取都直接失败。"""

        def __getitem__(self, key):
            """禁止下标读取环境。"""
            raise AssertionError('environment read during lexical pass')

        def get(self, key, default=None):
            """禁止带默认值读取环境。"""
            raise AssertionError('environment read during lexical pass')

    monkeypatch.setattr(os, 'environ', Forbidden())
    assert normalize_inline_upload('provider: catbox, token: ${ENV_NAME}') == (
        "provider: catbox, token: '${ENV_NAME}'")
    assert normalize_config_scalars(
        'push:\n  screenshot:\n    image_host:\n'
        '      - {provider: catbox, token: ${ENV_NAME}}\n') == (
        'push:\n  screenshot:\n    image_host:\n'
        "      - {provider: catbox, token: '${ENV_NAME}'}\n")


def test_syntax_failure_never_writes_config(tmp_path, caplog):
    """词法预处理失败时保留原文件，不写回也不写入安全标记。"""
    secret = 'CONFIG_BACKTICK_SECRET'
    path = _write_raw(
        tmp_path,
        MONITOR_HEAD
        + HOSTS_HEAD
        + f'      - {{provider: catbox, token: `{secret}}}\n',
    )
    original = path.read_bytes()
    with pytest.raises(SystemExit) as error:
        config_module.load_config(str(path))
    assert error.value.code == 1
    assert path.read_bytes() == original
    assert secret not in caplog.text
    assert '[已隐藏]' not in path.read_text(encoding='utf-8')
    assert 'stage=parse' in caplog.text
