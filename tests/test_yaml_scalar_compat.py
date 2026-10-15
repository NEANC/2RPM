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


def test_backtick_and_environment_tokens_parse_through_cli_path():
    """内联解析整链接受反引号与环境引用，并保留相邻字段。"""
    host = parse_inline_hosts(
        'provider: beeimg_cn, token: ${IMAGE_TOKEN}, options: {storage_id: 1}')[0]
    assert host['token'] == '${IMAGE_TOKEN}'
    assert host['options'] == {'storage_id': 1}
    literal = parse_inline_hosts('provider: catbox, token: `a;b,#{}\\x`')[0]
    assert literal['token'] == 'a;b,#{}\\x'


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
