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
from modules.log_safety import extend_secrets
from modules.operation_trace import trace_item
from modules.operation_trace import trace_scope
from modules.operation_trace import trace_skipped
from modules.operation_trace import trace_slot_outcome
from modules.operation_trace import trace_slots_skipped
from modules.upload_status import make_status_consumer
from modules.utils import get_program_directory

from .models import CaptureError
from .models import capture_failure_message
from .retention import parse_policy
from .retention import runtime_context
from .retention import save_automatic
from .service import capture
from .targets import allocate_targets


LOGGER = logging.getLogger(__name__)
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
    extend_secrets((destination,))
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
                    runtime, event, policy, trace):
    """执行单项目标，不重试、不切换后端，只记录固定安全失败类别。

    Returns:
        tuple[str, bool]: 目标输出值与“捕获是否成功”的事实。
    """
    if item['error'] is not None:
        trace.finish_image(item['index'], 'failed')
        diagnostics.append(f'截图目标 {item["index"]}：截图配置失败')
        return f'截图配置失败：{item["error"]}', False

    try:
        result = capture(
            item['provider'], item['target'],
            image_format='jpeg', purpose='automatic')
    except (KeyboardInterrupt, SystemExit):
        trace.finish_image(item['index'], 'cancelled')
        trace_skipped('save', reason_code='batch_cancelled')
        raise
    except CaptureError as error:
        trace.finish_image(item['index'], 'failed')
        failure = capture_failure_message(error)
        diagnostics.append(f'截图目标 {item["index"]}：{failure}')
        return failure, False
    except Exception:
        trace.finish_image(item['index'], 'failed')
        diagnostics.append(f'截图目标 {item["index"]}：截图失败')
        return '截图失败', False

    trace.finish_image(item['index'], 'succeeded')
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
            return markdown_image(item['out'], uploaded.url), True
        failure = _upload_failure_summary(uploaded.failures)
    except Exception:
        pass
    diagnostics.append(f'截图目标 {item["index"]}：{failure}')
    return failure, True


def _resolve_status(outcomes):
    """按每张图片是否取得所需结果给出批次业务终态。"""
    if all(outcomes):
        return 'completed'
    if any(outcomes):
        return 'partial_failure'
    return 'failed'


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
        planned_hosts = len(hosts) if isinstance(hosts, list) and hosts else 0
        with trace_scope(
                mode='automatic', images_planned=len(targets),
                slots_planned=len(targets) * planned_hosts,
                on_status=make_status_consumer()) as trace:
            outcomes = []
            with ExitStack() as stack:
                context = None
                try:
                    for index, item in enumerate(targets, start=1):
                        if context is None and item['error'] is None:
                            context = stack.enter_context(
                                UploadContext(diagnostics=False))
                        with trace_item(image_index=index):
                            value, captured = _prepare_target(
                                item, hosts, warnings, diagnostics,
                                context=context, runtime=runtime, event=event,
                                policy=policy, trace=trace)
                            if not captured:
                                # 捕获或截图配置失败：保存阶段未执行，补齐带图片归因的跳过事实
                                trace_skipped('save', reason_code='capture_failed')
                            trace.skip_unstarted_slots(
                                index, planned_hosts,
                                reason_code=('first_success' if captured
                                             else 'capture_failed'))
                        values[item['out']] = value
                        outcome = trace_slot_outcome(index)
                        outcomes.append(
                            captured if outcome is None
                            else outcome == 'succeeded')
                except BaseException as exc:
                    reason = ('batch_cancelled'
                              if isinstance(exc, (KeyboardInterrupt, SystemExit))
                              else 'slot_unfinished')
                    for remaining in range(index + 1, len(targets) + 1):
                        trace.finish_image(remaining, 'skipped')
                        with trace_item(image_index=remaining):
                            trace_skipped('save', reason_code=reason)
                    for index in range(1, len(targets) + 1):
                        trace.skip_unstarted_slots(
                            index, planned_hosts, reason_code=reason)
                    raise
            trace.set_status(_resolve_status(outcomes))
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
