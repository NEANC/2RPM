#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""将受控截图聚合占位渲染为通道专用正文。"""

from dataclasses import dataclass
from html import escape
from string import Formatter

from modules.screenshot.pipeline import ScreenshotBatch
from modules.screenshot.pipeline import append_screenshot_notices


_AGGREGATE_VARIABLE = 'screenshot'
_DIRECT_CONVERSIONS = frozenset((None, 's'))
_FORMATTER = Formatter()


@dataclass(frozen=True)
class ScreenshotRenderContext:
    """保存本次通知的原始模板、参数与受控截图批次。"""

    raw_title: str
    raw_content: str
    values: dict[str, object]
    batch: ScreenshotBatch
    screenshot_owned: bool


def _image_destination(name, value):
    """从受控单项目标的完整 Markdown 图片外壳中取出 destination。

    Args:
        name: 本次实际分配的截图输出名。
        value: 截图批次中该输出名对应的字符串。

    Returns:
        str | None: 仅当值恰好为 ``![name](destination)`` 时返回 destination。
    """
    prefix = f'![{name}]('
    if not value.startswith(prefix) or not value.endswith(')'):
        return None
    if len(value) <= len(prefix) + 1:
        return None
    return value[len(prefix):-1]


def _render_image(name, destination, mode):
    """按通道模式输出单个受控截图节点。"""
    if mode == 'markdown':
        return f'![{name}]({destination})'
    if mode == 'url':
        return destination
    if mode == 'plain':
        return f'{name}: {destination}'
    return (
        f'<img src="{escape(destination, quote=True)}" '
        f'alt="{escape(name, quote=True)}">')


def _render_text(text, mode):
    """输出普通文本；HTML 独立转义并把换行保留为 br。"""
    if mode != 'html':
        return text
    return escape(text, quote=True).replace('\n', '<br>\n')


def _render_controlled(name, value, mode):
    """转换单个受控输出值，非完整图片外壳按普通文本保留。"""
    destination = _image_destination(name, value)
    if destination is None:
        return _render_text(value, mode)
    return _render_image(name, destination, mode)


def _render_html_notice_value(name, value):
    """转换 HTML 追加项，换行留给追加分隔统一处理。"""
    destination = _image_destination(name, value)
    if destination is None:
        return escape(value, quote=True)
    return _render_image(name, destination, 'html')


def _render_aggregate(values, batch, mode):
    """按批次插入顺序组装单项，无单项时使用聚合提示。"""
    fragments = [
        _render_controlled(name, value, mode)
        for name, value in batch.values.items()
        if name != _AGGREGATE_VARIABLE
    ]
    if fragments:
        separator = '<br>\n<br>\n' if mode == 'html' else '\n\n'
        return separator.join(fragments)
    return _render_text(str(values.get(_AGGREGATE_VARIABLE, '')), mode)


def has_direct_screenshot(template: str) -> bool:
    """判断模板顶层是否直接引用聚合截图占位。

    Args:
        template: 待检查的原始模板字符串。

    Returns:
        bool: 字段名恰为 screenshot、转换为 None/s 且格式规格为空时为 True。
    """
    for _, name, specification, conversion in _FORMATTER.parse(template):
        if (
            name == _AGGREGATE_VARIABLE
            and conversion in _DIRECT_CONVERSIONS
            and not specification
        ):
            return True
    return False


def render_screenshot_template(template: str, values: dict[str, object],
                               batch: ScreenshotBatch, mode: str) -> str:
    """按顶层 formatter 结构渲染模板，仅转换直接聚合片段。

    Args:
        template: 原始模板字符串。
        values: 本次可用的普通变量及截图聚合值。
        batch: 本次截图批次，只读。
        mode: markdown、url、plain 或 html。

    Returns:
        str: 通道专用渲染结果。
    """
    parts = []
    for literal, name, specification, conversion in _FORMATTER.parse(template):
        if literal:
            parts.append(_render_text(literal, mode))
        if name is None:
            continue
        if (
            name == _AGGREGATE_VARIABLE
            and conversion in _DIRECT_CONVERSIONS
            and not specification
        ):
            parts.append(_render_aggregate(values, batch, mode))
            continue
        field, _ = _FORMATTER.get_field(name, (), values)
        field = _FORMATTER.convert_field(field, conversion)
        format_spec = _FORMATTER.vformat(specification, (), values)
        parts.append(
            _render_text(_FORMATTER.format_field(field, format_spec), mode))
    return ''.join(parts)


def render_screenshot_content(context: ScreenshotRenderContext,
                              mode: str) -> str:
    """渲染正文并追加一次既有截图提示与未引用改名结果。

    Args:
        context: 本次通知的原始正文、变量与截图批次。
        mode: markdown、url、plain 或 html。

    Returns:
        str: 含追加项的通道专用正文。
    """
    rendered = render_screenshot_template(
        context.raw_content, context.values, context.batch, mode)
    if mode != 'html':
        temporary = ScreenshotBatch(
            {
                name: _render_controlled(name, value, mode)
                for name, value in context.batch.values.items()
                if name != _AGGREGATE_VARIABLE
            },
            context.batch.warnings,
            context.batch.renamed_outputs,
        )
        return append_screenshot_notices(
            rendered, context.raw_content, temporary)

    temporary = ScreenshotBatch(
        {
            name: _render_html_notice_value(name, value)
            for name, value in context.batch.values.items()
            if name != _AGGREGATE_VARIABLE
        },
        tuple(escape(warning, quote=True) for warning in context.batch.warnings),
        context.batch.renamed_outputs,
    )
    suffix = append_screenshot_notices('', context.raw_content, temporary)
    if not suffix:
        return rendered
    suffix = suffix.replace('\n', '<br>\n')
    if not rendered:
        return suffix
    return rendered + '<br>\n<br>\n' + suffix
