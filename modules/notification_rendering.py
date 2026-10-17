#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""将受控截图聚合占位渲染为通道专用正文。"""

from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from string import Formatter

from modules.log_safety import extend_secrets
from modules.screenshot.pipeline import ScreenshotBatch
from modules.screenshot.pipeline import append_screenshot_notices


_AGGREGATE_VARIABLE = 'screenshot'
_DIRECT_CONVERSIONS = frozenset((None, 's'))
_FORMATTER = Formatter()
_MARKDOWN_CHANNELS = frozenset(
    ('dingtalk', 'pushplus', 'wechatworkapp', 'ntfy'))
_WECHATWORKBOT_MARKDOWN = {0: 1, 1: 1, 2: 2, 3: 1}
_ADAPTED_CHANNELS = frozenset(
    ('dingtalk', 'pushplus', 'wechatworkapp', 'wechatworkbot', 'pushdeer',
     'telegram', 'ntfy', 'bark', 'discord', 'smtp'))


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
    source = escape(destination, quote=True)
    if source != destination:
        extend_secrets((source,))
    return f'<img src="{source}" alt="{escape(name, quote=True)}">'


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


def _render_title(context: ScreenshotRenderContext, mode: str) -> str:
    """按通道模式渲染标题模板，不追加正文提示。"""
    return render_screenshot_template(
        context.raw_title, context.values, context.batch, mode)


def _smtp_header(name: str, value: str) -> str:
    """校验最终邮件头值，拒绝 CR/LF 而不清洗或折叠。

    Args:
        name: 固定头字段名，仅用于定位错误。
        value: 按默认 parser 语义回退后的最终头值。

    Returns:
        str: 可安全写入 EmailMessage 的头值。

    Raises:
        ValueError: 头值包含 CR 或 LF。
    """
    if value is None:
        value = ''
    if '\r' in value or '\n' in value:
        raise ValueError(f'邮件头 {name} 包含非法换行')
    return value


def build_smtp_message(params: dict, title: str, plain: str,
                       html: str) -> EmailMessage:
    """构造本次 SMTP 独立双格式消息，头字段遵循本地默认 parser。

    Args:
        params: 本次通道参数副本，只读取 subject、From、To 与 user。
        title: 纯文本渲染标题，作为 Subject 的回退值。
        plain: text/plain 正文。
        html: text/html 正文。

    Returns:
        EmailMessage: 本次通道独占的 multipart/alternative 消息。
    """
    subject = _smtp_header('Subject', params.get('subject') or title)
    sender = _smtp_header('From', params.get('From') or params.get('user'))
    recipient = _smtp_header('To', params.get('To') or params.get('user'))
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = recipient
    msg.set_content(plain)
    msg.add_alternative(html, subtype='html')
    return msg


def prepare_channel(channel: dict, title: str, content: str,
                    context: ScreenshotRenderContext | None
                    ) -> tuple[str, str, dict]:
    """按通道复制并覆盖本次参数，返回标题、正文与不含 provider 的副本。

    Args:
        channel: 标准通道字典，含 provider 及该渠道所需参数。
        title: 基准渲染标题，不触发或跳过时原样返回。
        content: 基准渲染正文，不触发或跳过时原样返回。
        context: 本次截图渲染上下文；None 表示未触发适配。

    Returns:
        tuple[str, str, dict]: 本次标题、正文与参数副本。
    """
    params = dict(channel)
    provider = params.pop('provider', '')

    # 守卫：未触发或没有本次模板拥有的截图来源时保持基准参数
    if context is None or not context.screenshot_owned:
        return title, content, params

    # 保留分支：provider 会改走自身消息种类，整条跳过全部适配
    if provider == 'wechatworkapp' and params.get('media_id') is not None:
        return title, content, params
    if provider == 'smtp' and params.get('msg') is not None:
        return title, content, params
    if provider == 'bark' and params.get('ciphertext'):
        return title, content, params

    # 守卫：不支持与排除名单完全走基线
    if provider not in _ADAPTED_CHANNELS:
        return title, content, params

    params.pop('title', None)
    params.pop('content', None)

    if provider == 'smtp':
        rendered_title = _render_title(context, 'plain')
        rendered_content = render_screenshot_content(context, 'plain')
        html_content = render_screenshot_content(context, 'html')
        params['msg'] = build_smtp_message(
            params, rendered_title, rendered_content, html_content)
        return rendered_title, rendered_content, params

    if provider == 'discord':
        return (
            _render_title(context, 'url'),
            render_screenshot_content(context, 'url'),
            params,
        )

    rendered_title = _render_title(context, 'markdown')
    rendered_content = render_screenshot_content(context, 'markdown')

    if provider in _MARKDOWN_CHANNELS:
        params['markdown'] = True
    elif provider == 'wechatworkbot':
        params['markdown'] = _WECHATWORKBOT_MARKDOWN.get(
            params.get('markdown'), 1)
    elif provider == 'pushdeer':
        params['type'] = 'markdown'
    elif provider == 'telegram':
        params.pop('text', None)
        if params.get('parse_mode') != 'MarkdownV2':
            params['parse_mode'] = 'Markdown'
    elif provider == 'bark':
        params.pop('body', None)
        params['markdown'] = rendered_content

    return rendered_title, rendered_content, params
