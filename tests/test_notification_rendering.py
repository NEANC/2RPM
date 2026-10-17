#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证截图聚合占位的直接来源识别、有限渲染与提示追加顺序。"""

from email import policy
from email.parser import BytesParser
from html import escape as html_escape

import pytest
from onepush.providers.smtp import SMTP

from modules.notification_rendering import (
    ScreenshotRenderContext,
    has_direct_screenshot,
    prepare_channel,
    render_screenshot_content,
    render_screenshot_template,
)
from modules.screenshot.pipeline import ScreenshotBatch
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


@pytest.mark.parametrize(
    'fallback', ('', '截图失败', '截图配置失败：未配置有效截图目标'))
def test_notice_order_and_fallback(fallback):
    """追加项按既有顺序产生，无单项目标时直接使用聚合提示。"""
    values = {'shot': IMAGE, 'screenshot': IMAGE}
    batch = ScreenshotBatch(values, ('W', 'W'), ('shot', 'shot'))
    before_values = dict(values)
    before_batch = ScreenshotBatch(dict(values), ('W', 'W'), ('shot', 'shot'))

    body_context = ScreenshotRenderContext(
        '{screenshot}', 'body', values, batch, True)
    assert render_screenshot_content(body_context, 'url') == (
        f'body\n\n截图配置提示\nW\n\n{URL}')

    direct_context = ScreenshotRenderContext(
        '{screenshot}', '{screenshot}', values, batch, True)
    assert render_screenshot_content(direct_context, 'url') == (
        f'{URL}\n\n截图配置提示\nW')

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
