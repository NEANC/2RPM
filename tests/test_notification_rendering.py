#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证截图聚合占位的直接来源识别、有限渲染与提示追加顺序。"""

import pytest

from modules.notification_rendering import (
    ScreenshotRenderContext,
    has_direct_screenshot,
    render_screenshot_content,
    render_screenshot_template,
)
from modules.screenshot.pipeline import ScreenshotBatch


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
