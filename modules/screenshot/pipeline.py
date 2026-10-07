#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""串行编排独立截图上传，并向已渲染正文追加必要提示。"""

from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
import logging
from string import Formatter
from urllib.parse import quote
from urllib.parse import urlsplit

from modules.image_host.context import UploadContext
from modules.image_host.core import ImageHostError
from modules.image_host.core import UploadFailure
from modules.image_host.registry import upload_with_fallback
from modules.utils import get_program_directory

from .models import CaptureError
from .retention import parse_policy
from .retention import runtime_context
from .retention import save_automatic
from .service import capture
from .targets import allocate_targets


LOGGER = logging.getLogger(__name__)
_CAPTURE_MESSAGES = {
    'invalid_source': '截图来源必须为 window 或 adb',
    'invalid_title': '窗口标题必须为非空字符串',
    'window_not_found': '未找到完整标题匹配的窗口',
    'window_ambiguous': '存在多个完整标题匹配的窗口',
    'window_lookup_failed': '无法枚举目标窗口',
    'window_gone': '目标窗口已失效或标题已变化',
    'window_state_failed': '无法读取或恢复目标窗口状态',
    'invalid_dimensions': '目标窗口尺寸无效',
    'dpi_failed': '无法设置局部线程 DPI 上下文',
    'gdi_failed': '无法分配或访问窗口图像资源',
    'print_failed': '窗口图像绘制失败',
    'image_failed': '无法构造有效窗口图像',
    'encode_failed': '窗口图像 PNG 编码或校验失败',
    'invalid_serial': 'ADB 序列号必须为非空字符串',
    'adb_version_unsupported': 'ADB 依赖版本未经支持验证',
    'adb_unavailable': '无法连接已有本机 ADB Server',
    'adb_not_found': '未找到指定 ADB 设备',
    'adb_offline': '指定 ADB 设备处于离线状态',
    'adb_unauthorized': '指定 ADB 设备尚未授权',
    'adb_timeout': 'ADB 连接或读写等待超时',
    'adb_protocol_failed': 'ADB 设备通信失败',
    'adb_image_failed': 'ADB 未返回完整有效的 PNG 图像',
}
_MARKDOWN_DELIMITERS = frozenset('[]()<>"\\')
_CONFIG_FAILURE = '截图配置失败：未配置有效截图目标'


@dataclass(frozen=True)
class ScreenshotBatch:
    """保存可跨推送通道复用的输出、独立告警及改名后的变量。"""

    values: dict[str, str]
    warnings: tuple[str, ...]
    renamed_outputs: tuple[str, ...]


def markdown_image(name, url):
    """保留主机方括号，仅转义结构字符，不重组查询或已有百分号转义。"""
    authority_start = url.index('://') + 3
    authority_end = authority_start + len(urlsplit(url).netloc)
    destination = ''.join(
        quote(char, safe='')
        if (char in _MARKDOWN_DELIMITERS or char.isspace())
        and not (char in '[]' and authority_start <= index < authority_end)
        else char
        for index, char in enumerate(url)
    )
    return f'![{name}]({destination})'


def _upload_failure_summary(failures):
    """只按核心认可的代码重建固定摘要，不展示动态标签或诊断。"""
    messages = []
    for failure in failures:
        if not isinstance(failure, UploadFailure):
            continue
        safe = ImageHostError(failure.code, '')
        if safe.code != 'upload_failed':
            messages.append(safe.message)
    summary = '；'.join(dict.fromkeys(messages))
    return '截图上传失败：' + summary if summary else '截图上传失败'


def _prepare_target(item, hosts, warnings, diagnostics, *, context,
                    runtime, event, policy):
    """执行单项目标，不重试、不切换后端，只记录固定安全失败类别。"""
    if item['error'] is not None:
        diagnostics.append(f'截图目标 {item["index"]}：截图配置失败')
        return f'截图配置失败：{item["error"]}'

    try:
        result = capture(
            item['provider'], item['target'],
            image_format='jpeg', purpose='automatic')
    except CaptureError as error:
        message = (
            _CAPTURE_MESSAGES.get(error.code)
            if type(error.code) is str else None
        )
        failure = f'截图失败：{message}' if message else '截图失败'
        diagnostics.append(f'截图目标 {item["index"]}：{failure}')
        return failure
    except Exception:
        diagnostics.append(f'截图目标 {item["index"]}：截图失败')
        return '截图失败'

    warnings.extend(result.warnings)
    try:
        outcome = save_automatic(result, runtime, event, policy)
    except Exception:
        warnings.append('截图保存失败：无法写入输出路径')
    else:
        warnings.extend(outcome.warnings)
    failure = '截图上传失败'
    try:
        uploaded = upload_with_fallback(
            result.image_bytes, item['out'] + '.jpg', hosts, context=context)
        warnings.extend(uploaded.warnings)
        if uploaded.success:
            return markdown_image(item['out'], uploaded.url)
        failure = _upload_failure_summary(uploaded.failures)
    except Exception:
        pass
    diagnostics.append(f'截图目标 {item["index"]}：{failure}')
    return failure


def prepare_screenshots(section, enabled, reserved_names, *,
                        runtime=None, event='event') -> ScreenshotBatch:
    """先分配所有名称再串行处理，禁用时不读取任何图床设置。

    Args:
        section: screenshot 配置映射，不是整个推送配置。
        enabled: 是否执行截图上传；关闭时输出变量全部置空。
        reserved_names: 调用方已有的保留变量名集合。
        runtime: 运行上下文（程序根与净化的配置 stem）；缺省时按程序目录回退。
        event: 本次事件的真实模板键；用于自动截图的事件序号。

    Returns:
        包含实际输出名、顺序聚合、去重告警及改名输出的冻结结果。
    """
    valid_section = isinstance(section, Mapping)
    raw_targets = section.get('targets') if valid_section else None
    targets, warnings = allocate_targets(raw_targets, reserved_names)
    values = {item['out']: '' for item in targets}
    if not enabled:
        values['screenshot'] = ''
        return ScreenshotBatch(values, (), ())

    policy = parse_policy(section.get('retention') if valid_section else None)
    warnings.extend(policy.warnings)
    if runtime is None:
        runtime = runtime_context(get_program_directory(), 'config.yaml')

    diagnostics = []
    if not valid_section:
        warnings.append('截图配置必须为映射，已忽略该配置')
    renamed = tuple(
        item['out'] for item in targets if item['renamed_from'] is not None
    )
    if targets:
        hosts = section.get('image_host')
        with ExitStack() as stack:
            context = None
            for item in targets:
                if context is None and item['error'] is None:
                    context = stack.enter_context(
                        UploadContext(diagnostics=False))
                values[item['out']] = _prepare_target(
                    item, hosts, warnings, diagnostics, context=context,
                    runtime=runtime, event=event, policy=policy)
        values['screenshot'] = '\n\n'.join(values.values())
    else:
        values['screenshot'] = _CONFIG_FAILURE
        diagnostics.append(_CONFIG_FAILURE)

    unique_warnings = tuple(dict.fromkeys(warnings))
    messages = tuple(dict.fromkeys((*unique_warnings, *diagnostics)))
    if messages:
        LOGGER.warning('%s', '\n'.join(messages))
    return ScreenshotBatch(values, unique_warnings, renamed)


def _referenced_fields(template):
    """递进解析正文及嵌套格式字段，畸形模板返回未知引用状态。"""
    formatter = Formatter()
    pending = [template]
    fields = set()
    try:
        while pending:
            for _, name, specification, _ in formatter.parse(pending.pop()):
                if name is None:
                    continue
                fields.add(name)
                if specification:
                    pending.append(specification)
    except ValueError:
        return None
    return fields


def append_screenshot_notices(
        rendered_content, original_template, batch) -> str:
    """只追加去重提示及未引用的改名结果，绝不再次格式化或上传。

    畸形模板仍追加已有告警，但不推断引用或自动补附图片；普通模板
    校验由通知层负责。转义花括号中的文字不视为实际字段引用。
    """
    additions = []
    warnings = tuple(dict.fromkeys(batch.warnings))
    if warnings:
        additions.append('截图配置提示\n' + '\n'.join(warnings))
    fields = _referenced_fields(original_template)
    if fields is not None and 'screenshot' not in fields:
        for name in dict.fromkeys(batch.renamed_outputs):
            value = batch.values.get(name, '')
            if name not in fields and value:
                additions.append(value)
    if not additions:
        return rendered_content
    suffix = '\n\n'.join(additions)
    return rendered_content + '\n\n' + suffix if rendered_content else suffix
