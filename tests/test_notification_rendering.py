#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证截图聚合占位的直接来源识别、有限渲染与提示追加顺序。"""

from email import policy
from email.parser import BytesParser
from html import escape as html_escape
from importlib import import_module
from unittest.mock import Mock

import pytest
from onepush.providers.smtp import SMTP

from modules.image_host.core import UploadResult
from modules.notification_rendering import (
    ScreenshotRenderContext,
    has_direct_screenshot,
    prepare_channel,
    render_screenshot_content,
    render_screenshot_template,
)
from modules.screenshot.models import CaptureResult
from modules.screenshot.pipeline import ScreenshotBatch
from modules.screenshot.pipeline import ScreenshotFailureDetail
from modules.screenshot.pipeline import build_screenshot_variable_fragments
from modules.screenshot.pipeline import markdown_image


URL = 'https://cdn.example/a%28b%29.png?sig=x%2Fy&k=z'
IMAGE = f'![shot]({URL})'
BATCH = ScreenshotBatch({'shot': IMAGE, 'screenshot': IMAGE}, (), ())
VALUES = dict(BATCH.values)
HTML_IMAGE = (
    '<img src="https://cdn.example/a%28b%29.png?sig=x%2Fy&amp;k=z" alt="shot">')
NON_DIRECT_TEMPLATES = (
    '{{screenshot}}',
    '{ordinary}',
    '{screenshot!r}',
    '{screenshot!a}',
    '{screenshot:>20}',
    '{word:{screenshot}}',
    '{screenshot_1}',
    '{shot}',
)


@pytest.mark.parametrize('template', NON_DIRECT_TEMPLATES)
def test_direct_trigger(template):
    """只把顶层空格式的字符串化聚合字段视为直接引用。"""
    assert has_direct_screenshot('{screenshot}') is True
    assert has_direct_screenshot('{screenshot:}') is True
    assert has_direct_screenshot('{screenshot!s}') is True
    assert has_direct_screenshot('{screenshot!s:}') is True
    assert has_direct_screenshot(template) is False


def test_mixed_fields_keep_original_values():
    """混合直接引用时只转换聚合片段，其他字段保留原 format 语义。"""
    values = dict(VALUES)
    values.update(screenshot_1=IMAGE, ordinary=IMAGE, word='x', width=3)
    before_values = dict(values)
    template = (
        '{screenshot}|{screenshot!r}|{screenshot:.4}|{screenshot_1}|{shot}'
        '|{ordinary}|{word:>{width}}'
    )

    assert render_screenshot_template(template, values, BATCH, 'url') == (
        f'{URL}|{IMAGE!r}|{IMAGE[:4]}|{IMAGE}|{IMAGE}|{IMAGE}|  x')
    assert render_screenshot_template(
        '{screenshot}{screenshot!s}', values, BATCH, 'url') == URL + URL
    assert render_screenshot_template(
        '{screenshot}', values, BATCH, 'markdown') == IMAGE
    assert render_screenshot_template(
        '{screenshot}', values, BATCH, 'plain') == 'shot: ' + URL
    assert render_screenshot_template('<b>{screenshot}', values, BATCH, 'html') == (
        '&lt;b&gt;' + HTML_IMAGE)

    empty_batch = ScreenshotBatch({'screenshot': ''}, (), ())
    nested_values = {'screenshot': '', 'word': 'x'}
    assert render_screenshot_template(
        '{word:{screenshot}}|{screenshot}', nested_values, empty_batch,
        'url') == 'x|'

    assert values == before_values
    assert BATCH == ScreenshotBatch({'shot': IMAGE, 'screenshot': IMAGE}, (), ())


def test_render_modes():
    """同一受控截图片段按通道模式输出不同但等价的表达。"""
    assert render_screenshot_template(
        '{screenshot}', VALUES, BATCH, 'markdown') == IMAGE
    assert render_screenshot_template(
        '{screenshot}', VALUES, BATCH, 'url') == URL
    assert render_screenshot_template(
        '{screenshot}', VALUES, BATCH, 'plain') == 'shot: ' + URL
    assert render_screenshot_template(
        '{screenshot}', VALUES, BATCH, 'html') == HTML_IMAGE

    mixed_batch = ScreenshotBatch(
        {'shot': IMAGE, 'later': '截图失败', 'screenshot': IMAGE + '\n\n截图失败'},
        (), ())
    assert render_screenshot_template(
        '{screenshot}', dict(mixed_batch.values), mixed_batch, 'html') == (
        HTML_IMAGE + '<br>\n<br>\n截图失败')


AGGREGATE_MODES = ('markdown', 'url', 'plain', 'html')
DIRECT_AGGREGATE_TEMPLATES = (
    '{screenshot}', '{screenshot:}', '{screenshot!s}', '{screenshot!s:}')

_MIXED_REASON = '<原因>&'
_MIXED_IMAGE_VALUES = {
    'empty_before': '',
    'shot': IMAGE,
    'empty_middle': '',
    'failed': f'截图上传失败：{_MIXED_REASON}',
    'empty_after': '',
    'screenshot': IMAGE + f'\n\n截图上传失败：{_MIXED_REASON}',
}
_MIXED_FAILURE_VALUES = {
    'shot': '截图失败',
    'empty': '',
    'later': '截图上传失败',
    'screenshot': '截图失败\n\n截图上传失败',
}
_MIXED_VALUES = {
    'image': _MIXED_IMAGE_VALUES,
    'failure': _MIXED_FAILURE_VALUES,
}
_MIXED_EXPECTED = {
    'image': {
        'markdown': IMAGE + f'\n\n截图上传失败：{_MIXED_REASON}',
        'url': URL + f'\n\n截图上传失败：{_MIXED_REASON}',
        'plain': 'shot: ' + URL + f'\n\n截图上传失败：{_MIXED_REASON}',
        'html': HTML_IMAGE + '<br>\n<br>\n截图上传失败：&lt;原因&gt;&amp;',
    },
    'failure': {
        'markdown': '截图失败\n\n截图上传失败',
        'url': '截图失败\n\n截图上传失败',
        'plain': '截图失败\n\n截图上传失败',
        'html': '截图失败<br>\n<br>\n截图上传失败',
    },
}


@pytest.mark.parametrize('template', DIRECT_AGGREGATE_TEMPLATES)
@pytest.mark.parametrize('count', (0, 1, 2, 3))
@pytest.mark.parametrize('mode', AGGREGATE_MODES)
def test_empty_target_aggregate_modes(mode, count, template):
    """禁用截图的多个空目标不产生分隔符，且不修改输入。"""
    values = {f'empty_{index}': '' for index in range(1, count + 1)}
    values['screenshot'] = ''
    batch = ScreenshotBatch(dict(values), (), ())
    before_values = dict(values)
    before_batch = ScreenshotBatch(dict(values), (), ())

    assert render_screenshot_template(template, values, batch, mode) == ''
    assert render_screenshot_template(
        'A' + template + 'B', values, batch, mode) == 'AB'
    context = ScreenshotRenderContext(
        'T', 'A' + template + 'B', values, batch, True)
    assert render_screenshot_content(context, mode) == 'AB'
    assert values == before_values
    assert batch == before_batch


@pytest.mark.parametrize('case', ('image', 'failure'))
@pytest.mark.parametrize('mode', AGGREGATE_MODES)
def test_mixed_empty_target_aggregate_modes(mode, case):
    """空目标夹在成功图片与失败文字之间时只排除精确空项。"""
    values = dict(_MIXED_VALUES[case])
    batch = ScreenshotBatch(dict(values), (), ())
    before_values = dict(values)
    before_batch = ScreenshotBatch(dict(values), (), ())
    expected = _MIXED_EXPECTED[case][mode]

    assert render_screenshot_template(
        '{screenshot}', values, batch, mode) == expected
    assert render_screenshot_template(
        '{screenshot}|{screenshot!s}', values, batch, mode) == (
        expected + '|' + expected)
    assert values == before_values
    assert batch == before_batch


@pytest.mark.parametrize('mode', AGGREGATE_MODES)
def test_whitespace_target_is_preserved(mode):
    """仅含空白的受控值不是禁用空字符串，必须原样保留。

    聚合值取 `''` 且与空白项 `' \\t'` 不同：若按 strip()/isspace()
    过滤空白项，则无保留项并回退到 `''`，断言失败；只有精确排除
    `''` 时才会保留空白项并返回 `' \\t'`。
    """
    values = {'empty': '', 'space': ' \t', 'screenshot': ''}
    batch = ScreenshotBatch(dict(values), (), ())

    assert render_screenshot_template(
        '{screenshot}', values, batch, mode) == ' \t'


@pytest.mark.parametrize(
    'fallback', ('', '截图失败', '截图配置失败：<原因>&'))
@pytest.mark.parametrize('with_empty_targets', (False, True))
@pytest.mark.parametrize('mode', AGGREGATE_MODES)
def test_aggregate_fallback_modes(mode, with_empty_targets, fallback):
    """没有保留项时按聚合值回退，不被空目标片段顶替。"""
    values = {'screenshot': fallback}
    if with_empty_targets:
        values = {'empty_a': '', 'empty_b': '', 'screenshot': fallback}
    batch = ScreenshotBatch(dict(values), (), ())
    expected = (
        fallback if mode != 'html' else html_escape(fallback, quote=True))

    assert render_screenshot_template(
        '{screenshot}', values, batch, mode) == expected


@pytest.mark.parametrize(
    'fallback', ('', '截图失败', '截图配置失败：未配置有效截图目标'))
def test_notice_order_and_fallback(fallback):
    """提示块只在引用变量内部展开，未引用截图变量的正文保持原样。"""
    values = {'shot': IMAGE, 'screenshot': IMAGE}
    batch = ScreenshotBatch(values, ('W', 'W'), ('shot', 'shot'))
    before_values = dict(values)
    before_batch = ScreenshotBatch(dict(values), ('W', 'W'), ('shot', 'shot'))

    body_context = ScreenshotRenderContext(
        '{screenshot}', 'body', values, batch, True)
    assert render_screenshot_content(body_context, 'url') == 'body'

    direct_context = ScreenshotRenderContext(
        '{screenshot}', '{screenshot}', values, batch, True)
    assert render_screenshot_content(direct_context, 'url') == (
        f'{URL}\n\n---\n\n截图配置提示: W')

    fallback_batch = ScreenshotBatch({'screenshot': fallback}, (), ())
    fallback_values = {'screenshot': fallback}
    assert render_screenshot_template(
        '{screenshot}', fallback_values, fallback_batch, 'url') == fallback

    mixed_batch = ScreenshotBatch(
        {'shot': IMAGE, 'later': '截图失败', 'screenshot': IMAGE + '\n\n截图失败'},
        (), ())
    mixed_values = dict(mixed_batch.values)
    assert render_screenshot_template(
        '{screenshot}', mixed_values, mixed_batch, 'url') == (
        URL + '\n\n截图失败')

    assert values == before_values
    assert batch == before_batch


M = IMAGE
CONTEXT = ScreenshotRenderContext('T', '{screenshot}', VALUES, BATCH, True)
EMPTY_BATCH = ScreenshotBatch({'screenshot': ''}, (), ())
EMPTY_CONTEXT = ScreenshotRenderContext(
    'T', '{screenshot}', dict(EMPTY_BATCH.values), EMPTY_BATCH, True)
FAILED_BATCH = ScreenshotBatch({'screenshot': '截图失败'}, (), ())
FAILED_CONTEXT = ScreenshotRenderContext(
    'T', '{screenshot}', dict(FAILED_BATCH.values), FAILED_BATCH, True)
CONTEXT_CASES = (
    (CONTEXT, IMAGE),
    (EMPTY_CONTEXT, ''),
    (FAILED_CONTEXT, '截图失败'),
)
DISCORD_CONTEXT = ScreenshotRenderContext(
    'T', '![manual](u)\n{screenshot}', VALUES, BATCH, True)
DISCORD_EMPTY_CONTEXT = ScreenshotRenderContext(
    'T', '![manual](u)\n{screenshot}', dict(EMPTY_BATCH.values), EMPTY_BATCH,
    True)


def _params_without_provider(channel):
    """返回通道参数去掉 provider 后的完整副本，用于原样返回断言。"""
    return {key: value for key, value in channel.items() if key != 'provider'}


@pytest.mark.parametrize('context, expected', CONTEXT_CASES)
@pytest.mark.parametrize(
    'provider', ('dingtalk', 'pushplus', 'wechatworkapp', 'ntfy'))
def test_channel_policy_markdown_true(provider, context, expected):
    """普通 Markdown 通道覆盖 markdown=True，并移除 title/content。"""
    channel = {
        'provider': provider,
        'markdown': False,
        'title': 'old',
        'content': 'old',
        'proxies': {'https': 'http://127.0.0.1:7890'},
        'route': 'primary',
    }
    before = dict(channel)

    title, content, params = prepare_channel(channel, 'T', M, context)

    assert channel == before
    assert params['markdown'] is True
    assert 'title' not in params
    assert 'content' not in params
    assert title == 'T'
    assert content == expected
    for key in ('proxies', 'route'):
        assert params[key] == before[key]


@pytest.mark.parametrize(
    'markdown, expected', ((0, 1), (1, 1), (2, 2), (3, 1)))
def test_channel_policy_wechatworkbot(markdown, expected):
    """企业微信机器人只保留 2，其余 0/1/3 统一为 1。"""
    channel = {
        'provider': 'wechatworkbot',
        'markdown': markdown,
        'title': 'old',
        'content': 'old',
    }
    before = dict(channel)

    title, content, params = prepare_channel(channel, 'T', M, CONTEXT)

    assert channel == before
    assert params['markdown'] == expected
    assert 'title' not in params
    assert 'content' not in params
    assert title == 'T'
    assert content == M


def test_channel_policy_pushdeer():
    """PushDeer 由 text 切换为 markdown。"""
    channel = {
        'provider': 'pushdeer',
        'type': 'text',
        'title': 'old',
        'content': 'old',
    }
    before = dict(channel)

    title, content, params = prepare_channel(channel, 'T', M, CONTEXT)

    assert channel == before
    assert params['type'] == 'markdown'
    assert 'title' not in params
    assert 'content' not in params
    assert content == M


@pytest.mark.parametrize(
    'parse_mode, expected',
    ((None, 'Markdown'), ('HTML', 'Markdown'), ('MarkdownV2', 'MarkdownV2')))
def test_channel_policy_telegram(parse_mode, expected):
    """Telegram 只保留 MarkdownV2，移除显式 text，保留预览开关。"""
    channel = {
        'provider': 'telegram',
        'text': 'old',
        'title': 'old',
        'content': 'old',
        'disable_web_page_preview': True,
    }
    if parse_mode is not None:
        channel['parse_mode'] = parse_mode
    before = dict(channel)

    title, content, params = prepare_channel(channel, 'T', M, CONTEXT)

    assert channel == before
    assert params['parse_mode'] == expected
    assert 'text' not in params
    assert 'title' not in params
    assert 'content' not in params
    assert params['disable_web_page_preview'] is True
    assert content == M


@pytest.mark.parametrize('context, expected', CONTEXT_CASES)
def test_channel_policy_bark(context, expected):
    """Bark 删除 body，markdown 与正文都是本次渲染字符串。"""
    channel = {
        'provider': 'bark',
        'body': 'old',
        'markdown': True,
        'title': 'old',
        'content': 'old',
        'cipherkey': 'secret',
        'ciphermethod': 'cbc',
    }
    before = dict(channel)

    title, content, params = prepare_channel(channel, 'T', M, context)

    assert channel == before
    assert 'body' not in params
    assert params['markdown'] == expected
    assert content == expected
    assert 'title' not in params
    assert 'content' not in params
    for key in ('cipherkey', 'ciphermethod'):
        assert params[key] == before[key]


@pytest.mark.parametrize(
    'context, expected',
    ((DISCORD_CONTEXT, '![manual](u)\n' + URL),
     (DISCORD_EMPTY_CONTEXT, '![manual](u)\n')))
def test_channel_policy_discord(context, expected):
    """Discord 只把截图节点变成裸 URL，手写图片语法保持原样。"""
    channel = {
        'provider': 'discord',
        'title': 'old',
        'content': 'old',
        'username': 'bot',
        'avatar_url': 'https://example.test/a.png',
        'color': '16478873',
    }
    before = dict(channel)

    title, content, params = prepare_channel(channel, 'T', M, context)

    assert channel == before
    assert content == expected
    assert title == 'T'
    assert 'title' not in params
    assert 'content' not in params
    for key in ('username', 'avatar_url', 'color'):
        assert params[key] == before[key]


SKIP_CHANNELS = (
    {
        'provider': 'wechatworkapp',
        'media_id': '',
        'title': 'old',
        'content': 'old',
        'body': 'old',
        'text': 'old',
        'markdown': False,
    },
    {
        'provider': 'wechatworkapp',
        'media_id': 'id',
        'title': 'old',
        'content': 'old',
        'body': 'old',
        'text': 'old',
        'markdown': False,
    },
    {
        'provider': 'smtp',
        'msg': object(),
        'title': 'old',
        'content': 'old',
        'body': 'old',
        'text': 'old',
        'markdown': False,
    },
    {
        'provider': 'smtp',
        'msg': '',
        'title': 'old',
        'content': 'old',
        'body': 'old',
        'text': 'old',
        'markdown': False,
    },
    {
        'provider': 'bark',
        'ciphertext': 'sealed',
        'title': 'old',
        'content': 'old',
        'body': 'old',
        'text': 'old',
        'markdown': False,
    },
)


@pytest.mark.parametrize('channel', SKIP_CHANNELS)
def test_skip_precedes_overrides(channel):
    """保留分支原样返回完整参数副本，跳过全部适配。"""
    before = dict(channel)

    title, content, params = prepare_channel(channel, 'T', M, CONTEXT)

    assert channel == before
    assert (title, content, params) == (
        'T', M, _params_without_provider(before))
    if before['provider'] == 'smtp':
        assert params['msg'] is before['msg']


def test_skip_precedes_overrides_none_does_not_skip():
    """None 不触发 media_id/msg 跳过，空 ciphertext 不触发 Bark 跳过。"""
    app_channel = {
        'provider': 'wechatworkapp',
        'media_id': None,
        'markdown': False,
        'title': 'old',
        'content': 'old',
    }
    _, _, app_params = prepare_channel(app_channel, 'T', M, CONTEXT)
    assert app_params['media_id'] is None
    assert app_params['markdown'] is True
    assert 'title' not in app_params

    smtp_channel = {
        'provider': 'smtp',
        'msg': None,
        'markdown': False,
        'title': 'old',
        'content': 'old',
    }
    title, content, smtp_params = prepare_channel(smtp_channel, 'T', M, CONTEXT)
    assert title == 'T'
    assert content == 'shot: ' + URL
    assert smtp_params['msg'].get_content_type() == 'multipart/alternative'
    assert 'title' not in smtp_params
    assert 'content' not in smtp_params

    bark_channel = {
        'provider': 'bark',
        'ciphertext': '',
        'body': 'old',
        'markdown': True,
        'title': 'old',
        'content': 'old',
    }
    _, content, bark_params = prepare_channel(bark_channel, 'T', M, CONTEXT)
    assert 'body' not in bark_params
    assert bark_params['markdown'] == M
    assert content == M


NO_TRIGGER_CHANNELS = (
    {
        'provider': 'dingtalk',
        'markdown': False,
        'title': 'old',
        'content': 'old',
    },
    {
        'provider': 'wechatworkbot',
        'markdown': 0,
        'title': 'old',
        'content': 'old',
    },
    {
        'provider': 'bark',
        'body': 'old',
        'markdown': True,
        'title': 'old',
        'content': 'old',
    },
    {
        'provider': 'discord',
        'title': 'old',
        'content': 'old',
    },
)


@pytest.mark.parametrize('template', NON_DIRECT_TEMPLATES)
@pytest.mark.parametrize('channel', NO_TRIGGER_CHANNELS)
def test_no_trigger_and_unsupported_are_unchanged_not_owned(channel, template):
    """非直接模板未拥有截图来源时保持原三元组。"""
    context = ScreenshotRenderContext('T', template, VALUES, BATCH, False)
    before = dict(channel)

    title, content, params = prepare_channel(channel, 'T', M, context)

    assert channel == before
    assert (title, content, params) == (
        'T', M, _params_without_provider(before))
    assert 'title' in params
    assert 'content' in params


@pytest.mark.parametrize('channel', NO_TRIGGER_CHANNELS)
def test_no_trigger_and_unsupported_are_unchanged_context_none(channel):
    """缺少渲染上下文时保持原三元组。"""
    before = dict(channel)

    title, content, params = prepare_channel(channel, 'T', M, None)

    assert channel == before
    assert (title, content, params) == (
        'T', M, _params_without_provider(before))
    assert 'title' in params
    assert 'content' in params


@pytest.mark.parametrize('channel', NO_TRIGGER_CHANNELS)
def test_no_trigger_and_unsupported_are_unchanged_screenshot_owned_false(
        channel):
    """截图来源不属于本次模板时保持原三元组。"""
    context = ScreenshotRenderContext('T', '{screenshot}', VALUES, BATCH, False)
    before = dict(channel)

    title, content, params = prepare_channel(channel, 'T', M, context)

    assert channel == before
    assert (title, content, params) == (
        'T', M, _params_without_provider(before))
    assert 'title' in params
    assert 'content' in params


@pytest.mark.parametrize(
    'provider',
    ('gotify', 'serverchan', 'serverchanturbo', 'gocqhttp', 'custom', 'qmsg',
     'lark', 'wpush'))
def test_no_trigger_and_unsupported_are_unchanged_unsupported(provider):
    """不支持与排除名单完全走基线，不移除 title/content。"""
    channel = {
        'provider': provider,
        'markdown': False,
        'type': 'text',
        'body': 'old',
        'text': 'old',
        'title': 'old',
        'content': 'old',
    }
    before = dict(channel)

    title, content, params = prepare_channel(channel, 'T', M, CONTEXT)

    assert channel == before
    assert (title, content, params) == (
        'T', M, _params_without_provider(before))
    assert 'title' in params
    assert 'content' in params


SMTP_CONTEXT = ScreenshotRenderContext('T', '{screenshot}', VALUES, BATCH, True)
SMTP_TITLE_ONLY_CONTEXT = ScreenshotRenderContext(
    '{screenshot}', 'BODY', VALUES, BATCH, True)
TWO_BATCH = ScreenshotBatch(
    {
        'first': f'![first]({URL})',
        'second': f'![second]({URL})',
        'screenshot': f'![first]({URL})\n\n![second]({URL})',
    },
    (), ())
SMTP_URL_CASES = (
    'https://cdn.example/a(b).png?sig=x/y&k=z',
    'https://cdn.example/a b.png?q=1 2',
    'https://cdn.example/a%28b%29.png?sig=100%25',
    'https://[2001:db8::1]/shot.png?sig=x/y',
)
HEADER_NAMES = {'subject': 'Subject', 'From': 'From', 'To': 'To'}


def _smtp_channel(**overrides):
    """返回可携带指定覆盖项的 SMTP 通道字典。"""
    channel = {
        'provider': 'smtp',
        'msg': None,
        'subject': 'T',
        'From': 'sender@example.test',
        'To': 'recipient@example.test',
        'user': 'user@example.test',
        'host': 'smtp.example.test',
        'password': 'secret',
        'title': 'old',
        'content': 'old',
    }
    channel.update(overrides)
    return channel


def test_smtp_mime():
    """SMTP 构造独立 multipart/alternative，两份正文与头语义稳定。"""
    channel = _smtp_channel()
    before = dict(channel)

    title, content, params = prepare_channel(
        channel, 'T', '{screenshot}', SMTP_CONTEXT)
    msg = params['msg']

    assert channel == before
    assert title == 'T'
    assert content == 'shot: ' + URL
    assert msg.get_content_type() == 'multipart/alternative'
    assert [part.get_content_type() for part in msg.iter_parts()] == [
        'text/plain', 'text/html']
    assert msg.get_body(preferencelist=('plain',)).get_content() == (
        'shot: ' + URL + '\n')
    assert msg.get_body(preferencelist=('html',)).get_content() == (
        '<img src="https://cdn.example/a%28b%29.png?sig=x%2Fy&amp;k=z" '
        'alt="shot">\n')
    assert str(msg['Subject']) == 'T'
    assert str(msg['From']) == 'sender@example.test'
    assert str(msg['To']) == 'recipient@example.test'
    assert 'title' not in params
    assert 'content' not in params
    for key in ('host', 'password', 'user', 'subject', 'From', 'To'):
        assert params[key] == before[key]

    parsed = BytesParser(policy=policy.default).parsebytes(msg.as_bytes())
    assert parsed.get_content_type() == 'multipart/alternative'
    assert [part.get_content_type() for part in parsed.iter_parts()] == [
        'text/plain', 'text/html']
    assert parsed.get_body(preferencelist=('plain',)).get_content() == (
        'shot: ' + URL + '\n')
    assert parsed.get_body(preferencelist=('html',)).get_content() == (
        '<img src="https://cdn.example/a%28b%29.png?sig=x%2Fy&amp;k=z" '
        'alt="shot">\n')
    assert str(parsed['Subject']) == 'T'
    assert str(parsed['From']) == 'sender@example.test'
    assert str(parsed['To']) == 'recipient@example.test'

    text_context = ScreenshotRenderContext(
        'T', '<script>&"\n', VALUES, BATCH, True)
    text_msg = prepare_channel(
        _smtp_channel(), 'T', '', text_context)[2]['msg']
    assert text_msg.get_body(
        preferencelist=('plain',)).get_content() == '<script>&"\n'
    assert text_msg.get_body(
        preferencelist=('html',)).get_content() == (
        '&lt;script&gt;&amp;&quot;<br>\n')

    markdown_context = ScreenshotRenderContext(
        'T', '![manual](u)\n{screenshot}', VALUES, BATCH, True)
    markdown_msg = prepare_channel(
        _smtp_channel(), 'T', M, markdown_context)[2]['msg']
    assert markdown_msg.get_body(
        preferencelist=('html',)).get_content() == (
        '![manual](u)<br>\n'
        '<img src="https://cdn.example/a%28b%29.png?sig=x%2Fy&amp;k=z" '
        'alt="shot">\n')


@pytest.mark.parametrize(
    'header, explicit, expected',
    (
        ('subject', 'S', 'S'),
        ('subject', None, 'shot: ' + URL),
        ('subject', '', 'shot: ' + URL),
        ('From', 'from@example.test', 'from@example.test'),
        ('From', None, 'user@example.test'),
        ('From', '', 'user@example.test'),
        ('To', 'to@example.test', 'to@example.test'),
        ('To', None, 'user@example.test'),
        ('To', '', 'user@example.test'),
    ),
)
def test_smtp_header_fallback(header, explicit, expected):
    """Subject/From/To 分别采用显式值或按默认 parser 语义回退。"""
    channel = _smtp_channel(**{header: explicit})

    msg = prepare_channel(
        channel, 'T', 'BODY', SMTP_TITLE_ONLY_CONTEXT)[2]['msg']

    assert str(msg[HEADER_NAMES[header]]) == expected


def test_smtp_title_only_and_invalid_headers():
    """仅标题占位只输出纯文本；非法头换行直接拒绝且不折叠。"""
    title, content, params = prepare_channel(
        _smtp_channel(subject=None), 'T', 'BODY', SMTP_TITLE_ONLY_CONTEXT)
    msg = params['msg']

    assert title == 'shot: ' + URL
    assert content == 'BODY'
    assert str(msg['Subject']) == 'shot: ' + URL
    assert str(msg['From']) == 'sender@example.test'
    assert str(msg['To']) == 'recipient@example.test'
    assert msg.get_body(preferencelist=('plain',)).get_content() == 'BODY\n'
    assert '<img' not in msg.get_body(
        preferencelist=('html',)).get_content()
    assert 'title' not in params
    assert 'content' not in params

    two_context = ScreenshotRenderContext(
        '{screenshot}', 'BODY', dict(TWO_BATCH.values), TWO_BATCH, True)
    with pytest.raises(ValueError):
        prepare_channel(
            _smtp_channel(subject=None), 'T', 'BODY', two_context)

    safe = prepare_channel(
        _smtp_channel(subject='SAFE'), 'T', 'BODY', two_context)
    assert str(safe[2]['msg']['Subject']) == 'SAFE'

    for header in ('subject', 'From', 'To'):
        for value in ('a\rb', 'a\nb', 'a\r\nb'):
            with pytest.raises(ValueError):
                prepare_channel(
                    _smtp_channel(**{header: value}), 'T', 'BODY', CONTEXT)


def test_smtp_message_identity():
    """每次准备产生新消息；显式 msg 原样保留且不触碰 parser。"""
    original_parser = SMTP._message_parser
    channel = _smtp_channel()

    first = prepare_channel(channel, 'T', '{screenshot}', SMTP_CONTEXT)[2]
    second = prepare_channel(channel, 'T', '{screenshot}', SMTP_CONTEXT)[2]

    assert first['msg'] is not second['msg']

    original = object()
    skipped = prepare_channel(
        _smtp_channel(msg=original), 'T', '{screenshot}', SMTP_CONTEXT)[2]
    assert skipped['msg'] is original
    assert SMTP._message_parser is original_parser


@pytest.mark.parametrize('url', SMTP_URL_CASES)
def test_smtp_html_attribute_escape(url):
    """HTML img src 只等于受控 destination 的属性转义结果。"""
    image = markdown_image('shot', url)
    destination = image[len('![shot]('):-1]
    batch = ScreenshotBatch(
        {'shot': image, 'screenshot': image}, (), ())
    context = ScreenshotRenderContext(
        '{screenshot}', '{screenshot}', dict(batch.values), batch, True)

    msg = prepare_channel(
        _smtp_channel(subject='T'), 'T', 'BODY', context)[2]['msg']

    assert msg.get_body(preferencelist=('html',)).get_content() == (
        f'<img src="{html_escape(destination, quote=True)}" alt="shot">\n')


def test_html_notice_only_inside_referenced_variable():
    """HTML 提示块位于引用变量内部，转义一次并用 hr 分隔。"""
    batch = ScreenshotBatch(
        {'shot': M, 'screenshot': M}, ('W<&>',), ('shot',))
    values = dict(batch.values)
    context = ScreenshotRenderContext('T', '{screenshot}', values, batch, True)
    before_values = dict(values)
    before_batch = ScreenshotBatch(
        dict(batch.values), batch.warnings, batch.renamed_outputs)
    destination = M[len('![shot]('):-1]
    image = (
        f'<img src="{html_escape(destination, quote=True)}" alt="shot">')

    rendered = render_screenshot_content(context, 'html')

    assert rendered == (
        image + '<br>\n<br>\n<hr><br>\n<br>\n'
        '截图配置提示: W&lt;&amp;&gt;')
    assert 'W&lt;&amp;&gt;' in rendered
    assert rendered.count('<img') == 1
    assert '&lt;img' not in rendered
    assert context.values == before_values
    assert batch == before_batch


def test_body_without_reference_stays_verbatim():
    """正文没有引用截图变量时，提示不会出现在变量之外。"""
    batch = ScreenshotBatch(
        {'shot': M, 'screenshot': M}, ('W<&>',), ('shot',))
    context = ScreenshotRenderContext(
        'T', '纯正文\n\n', dict(batch.values), batch, True)

    assert render_screenshot_content(context, 'html') == '纯正文<br>\n<br>\n'
    assert render_screenshot_content(context, 'markdown') == '纯正文\n\n'


_REASON_A = '截图上传失败：通道 BeeIMG.CN 图床网络传输失败。'
_REASON_B = '截图上传失败：通道 Catbox 储存驱动不可用。'


def test_failure_fragments_all_render_modes():
    """四模式按唯一构建器同序输出失败句与图片，且只读不改批次。"""
    batch = ScreenshotBatch(
        {'a': '旧A', 'shot': IMAGE, 'b': '旧B', 'screenshot': '旧聚合'}, (), (),
        (ScreenshotFailureDetail('a', 'upload', 'beeimg_cn', 'transport_failed'),
         ScreenshotFailureDetail('b', 'upload', 'catbox', 'storage_unavailable')))
    values = dict(batch.values)
    before_values = dict(values)
    before_batch = ScreenshotBatch(
        dict(batch.values), (), (),
        (ScreenshotFailureDetail('a', 'upload', 'beeimg_cn', 'transport_failed'),
         ScreenshotFailureDetail('b', 'upload', 'catbox', 'storage_unavailable')))

    assert render_screenshot_template(
        '{screenshot}', values, batch, 'markdown') == (
        _REASON_A + '\n\n' + IMAGE + '\n\n' + _REASON_B)
    assert render_screenshot_template(
        '{screenshot}', values, batch, 'url') == (
        _REASON_A + '\n\n' + URL + '\n\n' + _REASON_B)
    assert render_screenshot_template(
        '{screenshot}', values, batch, 'plain') == (
        _REASON_A + '\n\n' + 'shot: ' + URL + '\n\n' + _REASON_B)
    assert render_screenshot_template(
        '{screenshot}', values, batch, 'html') == (
        _REASON_A + '<br>\n<br>\n' + HTML_IMAGE + '<br>\n<br>\n' + _REASON_B)
    assert values == before_values
    assert batch == before_batch


def test_variable_subset_keeps_referenced_failure():
    """每个变量只携带自己的失败片段，同一身份在不同变量内各自保留。"""
    batch = ScreenshotBatch(
        {'a': '旧A', 'b': '旧B', 'screenshot': '旧聚合'}, (), ('b',),
        (ScreenshotFailureDetail('a', 'upload', 'beeimg_cn', 'transport_failed'),
         ScreenshotFailureDetail('b', 'upload', 'beeimg_cn', 'transport_failed')))

    assert build_screenshot_variable_fragments(batch, 'a') == (
        ('a', 'text', _REASON_A),)
    assert build_screenshot_variable_fragments(batch, 'b') == (
        ('b', 'text', _REASON_A),)

    warned = ScreenshotBatch(
        dict(batch.values), ('W', 'W'), ('b',), batch.failure_details)
    assert build_screenshot_variable_fragments(warned, 'a') == (
        ('a', 'text', _REASON_A),
        ('', 'separator', '---'),
        ('', 'text', '截图配置提示: W'))


def test_html_notice_escaped_once():
    """HTML 变量内提示只转义一次，旧无详情值不被当作新失败详情。"""
    legacy = '<旧原因>&'
    batch = ScreenshotBatch({'screenshot': legacy}, ('W',), ())
    values = dict(batch.values)
    before_values = dict(values)
    before_batch = ScreenshotBatch(dict(batch.values), ('W',), ())
    context = ScreenshotRenderContext('T', '{screenshot}', values, batch, True)

    html_notice = render_screenshot_content(context, 'html')

    assert html_notice.count('&lt;旧原因&gt;&amp;') == 1
    assert '&amp;lt;' not in html_notice
    assert html_notice == (
        '&lt;旧原因&gt;&amp;<br>\n<br>\n<hr><br>\n<br>\n'
        '截图配置提示: W')
    assert values == before_values
    assert batch == before_batch
    assert batch.failure_details == ()


def test_owned_aggregate_and_user_text_unchanged(monkeypatch, tmp_path):
    """手写内容、编号与自定义 out、覆盖路径沿用既有所有权，不被清洗。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    module = import_module('modules.screenshot.pipeline')
    capture = Mock(side_effect=lambda source, name, **kwargs: CaptureResult(
        b'png-' + name.encode(), source, name, 2, 3))
    upload = Mock(return_value=UploadResult(True, 'catbox', URL, ('catbox',), ()))
    monkeypatch.setattr(module, 'capture', capture)
    monkeypatch.setattr(module, 'upload_with_fallback', upload)

    batch = module.prepare_screenshots(
        {'targets': [{'provider': 'window', 'target': '窗口', 'out': 'custom'}],
         'image_host': [{'provider': 'catbox'}]}, True, ())
    assert capture.call_count == upload.call_count == 1
    assert batch.values['custom'] == f'![custom]({URL})'

    numbered = ScreenshotBatch(
        {'custom': _REASON_A, 'screenshot': '旧聚合'}, (), (),
        (ScreenshotFailureDetail(
            'custom', 'upload', 'beeimg_cn', 'transport_failed'),))
    assert numbered.values['custom'] == _REASON_A
    numbered_values = dict(numbered.values)
    assert render_screenshot_template(
        '{screenshot}', numbered_values, numbered, 'url') == _REASON_A
    assert render_screenshot_template(
        '{custom}', numbered_values, numbered, 'url') == _REASON_A

    handwritten = ScreenshotBatch(
        {'a': _REASON_A, 'b': _REASON_A, 'screenshot': '旧聚合'}, (), ())
    handwritten_values = dict(handwritten.values)
    assert render_screenshot_template(
        '{screenshot}', handwritten_values, handwritten, 'markdown') == (
        _REASON_A + '\n\n' + _REASON_A)
    literal = _REASON_A + ' BeeIMG.cn 保持小写'
    assert render_screenshot_template(
        literal, handwritten_values, handwritten, 'markdown') == literal
    assert render_screenshot_template(
        '{screenshot}|{a}', handwritten_values, handwritten, 'markdown') == (
        _REASON_A + '\n\n' + _REASON_A + '|' + _REASON_A)

    assert render_screenshot_template(
        '{{screenshot}}', dict(BATCH.values), BATCH, 'url') == '{screenshot}'
    assert render_screenshot_template(
        '{screenshot!r}', dict(BATCH.values), BATCH, 'url') == repr(IMAGE)
    short = ScreenshotBatch({'shot': 's', 'screenshot': 's'}, (), ())
    assert render_screenshot_template(
        '{screenshot:>20}', {'shot': 's', 'screenshot': 's'}, short, 'url') == (
        format('s', '>20'))

    dingtalk = {
        'provider': 'dingtalk', 'markdown': False, 'title': 'old',
        'content': 'old'}
    assert prepare_channel(dingtalk, 'T', 'BASE', None) == (
        'T', 'BASE', _params_without_provider(dingtalk))
    original = object()
    smtp = {
        'provider': 'smtp', 'msg': original, 'title': 'old', 'content': 'old'}
    assert prepare_channel(smtp, 'T', 'BASE', CONTEXT) == (
        'T', 'BASE', _params_without_provider(smtp))


def test_smtp_failure_fragments_dual_format():
    """SMTP 双格式失败集合与顺序等价，图片分别为纯文本标签与 img。"""
    batch = ScreenshotBatch(
        {'a': '旧A', 'shot': IMAGE, 'b': '旧B', 'screenshot': '旧聚合'}, (), (),
        (ScreenshotFailureDetail('a', 'upload', 'beeimg_cn', 'transport_failed'),
         ScreenshotFailureDetail('b', 'upload', 'catbox', 'storage_unavailable')))
    context = ScreenshotRenderContext(
        'T', '{screenshot}', dict(batch.values), batch, True)
    before_batch = ScreenshotBatch(
        dict(batch.values), (), (), batch.failure_details)

    msg = prepare_channel(
        _smtp_channel(subject='T'), 'T', 'BODY', context)[2]['msg']

    assert [part.get_content_type() for part in msg.iter_parts()] == [
        'text/plain', 'text/html']
    plain = msg.get_body(preferencelist=('plain',)).get_content()
    html = msg.get_body(preferencelist=('html',)).get_content()
    assert plain == _REASON_A + '\n\n' + 'shot: ' + URL + '\n\n' + _REASON_B + '\n'
    assert html == (
        _REASON_A + '<br>\n<br>\n' + HTML_IMAGE + '<br>\n<br>\n'
        + _REASON_B + '\n')
    assert batch == before_batch

    two_context = ScreenshotRenderContext(
        '{screenshot}', 'BODY', dict(TWO_BATCH.values), TWO_BATCH, True)
    with pytest.raises(ValueError):
        prepare_channel(_smtp_channel(subject=None), 'T', 'BODY', two_context)
