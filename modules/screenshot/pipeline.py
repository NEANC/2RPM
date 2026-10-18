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

from modules.feedback_text import canonical_failure_code
from modules.feedback_text import canonical_upload_provider
from modules.feedback_text import format_failure_sentence
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
from modules.operation_trace import trace_warning
from modules.upload_status import make_status_consumer
from modules.upload_status import upload_feedback_scope
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
_AGGREGATE_NAME = 'screenshot'
_SHARED_CONFIG = '配置'
_UNKNOWN_PROVIDER = 'unknown'


@dataclass(frozen=True)
class ScreenshotFailureDetail:
    """保存单个输出名对应的不可变安全失败身份。"""

    output_name: str
    category: str
    provider: str
    code: str


@dataclass(frozen=True)
class ScreenshotBatch:
    """保存可跨推送通道复用的输出、独立告警及改名后的变量。"""

    values: dict[str, str]
    warnings: tuple[str, ...]
    renamed_outputs: tuple[str, ...]
    failure_details: tuple[ScreenshotFailureDetail, ...] = ()


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


def _image_shell(name, value):
    """复刻渲染层的图片外壳判定，不反向导入渲染模块。"""
    prefix = f'![{name}]('
    return (value.startswith(prefix) and value.endswith(')')
            and len(value) > len(prefix) + 1)


def _upload_failure_details(output_name, failures):
    """把上传失败列表规范为不可变的类别、安全 provider 与规范 code。

    Args:
        output_name: 该图片的输出变量名。
        failures: 上传结果中的失败元组，可能含非 UploadFailure 值。

    Returns:
        tuple[ScreenshotFailureDetail, ...]: 逐项去重前的详情；共享配置
        类归为 config 类别并保留 ``配置`` provider；无有效项时给出
        固定 ``upload`` / ``unknown`` / ``upload_failed`` 通用详情。
    """
    details = []
    for failure in failures:
        if not isinstance(failure, UploadFailure):
            continue
        provider = canonical_upload_provider(failure.provider)
        if provider == _SHARED_CONFIG:
            details.append(ScreenshotFailureDetail(
                output_name, 'config', provider,
                canonical_failure_code('config', failure.code)))
        else:
            details.append(ScreenshotFailureDetail(
                output_name, 'upload', provider,
                canonical_failure_code('upload', failure.code)))
    if not details:
        details.append(ScreenshotFailureDetail(
            output_name, 'upload', _UNKNOWN_PROVIDER, 'upload_failed'))
    return tuple(details)


def build_screenshot_fragments(batch, *, output_names=None):
    """按 values 插入顺序构建去重的安全失败片段，排除聚合自身。

    Args:
        batch: 含输出值与失败详情的冻结批次。
        output_names: 仅构建这些输出名的子集；None 表示全量聚合。

    Returns:
        tuple[tuple[str, str, str], ...]: 有序 (输出名, kind, 文本)；
        kind 为 image/text；同一次调用内首次出现的身份保留；子集为空
        返回空，不回退聚合；仅全量且无可用片段时读取聚合详情或旧值。
    """
    details_by_name = {}
    for detail in batch.failure_details:
        details_by_name.setdefault(detail.output_name, []).append(detail)
    allowed = None if output_names is None else set(output_names)
    fragments = []
    seen = set()
    for name, value in batch.values.items():
        if name == _AGGREGATE_NAME:
            continue
        if allowed is not None and name not in allowed:
            continue
        if value == '':
            continue
        details = details_by_name.get(name)
        if details:
            for detail in details:
                identity = (detail.category, detail.provider, detail.code)
                if identity in seen:
                    continue
                seen.add(identity)
                fragments.append((name, 'text', format_failure_sentence(
                    detail.category, detail.provider, detail.code)))
            continue
        kind = 'image' if _image_shell(name, value) else 'text'
        fragments.append((name, kind, value))
    if fragments or allowed is not None:
        return tuple(fragments)
    aggregate = details_by_name.get(_AGGREGATE_NAME)
    text = (format_failure_sentence(
        aggregate[0].category, aggregate[0].provider, aggregate[0].code)
        if aggregate else batch.values.get(_AGGREGATE_NAME, ''))
    if not text:
        return ()
    return ((_AGGREGATE_NAME, 'text', text),)


def _screenshot_aggregate(values, details):
    """按构建器生成默认聚合正文，保持空行分隔且不分号拼接。"""
    batch = ScreenshotBatch(dict(values), (), (), tuple(details))
    return '\n\n'.join(text for _, _, text in build_screenshot_fragments(batch))


def _host_providers(hosts):
    """按配置项顺序提取原始 provider，供消费者判定重复配置项后缀。"""
    if not isinstance(hosts, list):
        return ()
    return tuple(
        host.get('provider', '') if isinstance(host, Mapping) else ''
        for host in hosts)


def _prepare_target(item, hosts, warnings, diagnostics, *, context,
                    runtime, event, policy, trace, consumer):
    """执行单项目标，不重试、不切换后端，只记录固定安全失败类别。

    Returns:
        tuple[str, bool, bool, tuple[ScreenshotFailureDetail, ...]]:
        目标输出值、捕获是否成功、上传是否成功及不可变失败详情。
    """
    output_name = item['out']
    if item['error'] is not None:
        trace.finish_image(item['index'], 'failed')
        diagnostics.append(f'截图目标 {item["index"]}：截图配置失败')
        return (f'截图配置失败：{item["error"]}', False, False, (
            ScreenshotFailureDetail(
                output_name, 'config', '', 'target_invalid'),))

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
        return (failure, False, False, (ScreenshotFailureDetail(
            output_name, 'capture', '',
            canonical_failure_code('capture', error.code)),))
    except Exception:
        trace.finish_image(item['index'], 'failed')
        diagnostics.append(f'截图目标 {item["index"]}：截图失败')
        return ('截图失败', False, False, (ScreenshotFailureDetail(
            output_name, 'capture', '', 'capture_failed'),))

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
        with upload_feedback_scope(consumer):
            uploaded = upload_with_fallback(
                result.image_bytes, item['out'] + '.jpg', hosts, context=context)
        warnings.extend(uploaded.warnings)
        if uploaded.success:
            return markdown_image(item['out'], uploaded.url), True, True, ()
        failure = _upload_failure_summary(uploaded.failures)
        details = _upload_failure_details(output_name, uploaded.failures)
    except Exception:
        details = (ScreenshotFailureDetail(
            output_name, 'upload', _UNKNOWN_PROVIDER, 'upload_failed'),)
    diagnostics.append(f'截图目标 {item["index"]}：{failure}')
    return failure, True, False, details


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
    details = []
    if targets:
        hosts = section.get('image_host')
        planned_hosts = len(hosts) if isinstance(hosts, list) and hosts else 0
        consumer = make_status_consumer(
            images_planned=len(targets), slot_providers=_host_providers(hosts))
        with trace_scope(
                mode='automatic', images_planned=len(targets),
                slots_planned=len(targets) * planned_hosts,
                on_status=consumer) as trace:
            outcomes = []
            with ExitStack() as stack:
                context = None
                try:
                    for index, item in enumerate(targets, start=1):
                        if context is None and item['error'] is None:
                            context = stack.enter_context(
                                UploadContext(diagnostics=False))
                        with trace_item(image_index=index):
                            value, captured, succeeded, target_details = (
                                _prepare_target(
                                    item, hosts, warnings, diagnostics,
                                    context=context, runtime=runtime,
                                    event=event, policy=policy, trace=trace,
                                    consumer=consumer))
                            if not captured:
                                # 捕获或截图配置失败：保存阶段未执行，补齐带图片归因的跳过事实
                                trace_skipped('save', reason_code='capture_failed')
                            trace.skip_unstarted_slots(
                                index, planned_hosts,
                                reason_code=('first_success' if captured
                                             else 'capture_failed'))
                        values[item['out']] = value
                        details.extend(target_details)
                        outcome = trace_slot_outcome(index)
                        outcomes.append(
                            succeeded if outcome is None
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
            if warnings:
                trace_warning()
            trace.set_status(_resolve_status(outcomes))
    else:
        diagnostics.append(_CONFIG_FAILURE)
        details.append(ScreenshotFailureDetail(
            _AGGREGATE_NAME, 'config', '', 'no_targets'))
    values['screenshot'] = _screenshot_aggregate(values, details)

    unique_warnings = tuple(dict.fromkeys(warnings))
    messages = tuple(dict.fromkeys((*unique_warnings, *diagnostics)))
    if messages:
        LOGGER.warning('%s', '\n'.join(messages))
    return ScreenshotBatch(values, unique_warnings, renamed, tuple(details))


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


def build_screenshot_notice_fragments(batch, original_template):
    """构建追加片段：配置告警一次去重，未引用的改名结果在子集内去重。

    Args:
        batch: 本次截图批次，只读。
        original_template: 原始正文模板，用于解析实际字段引用。

    Returns:
        tuple[tuple[str, str, str], ...]: 有序 (输出名, kind, 文本) 追加片段；
        配置告警恒为单个 text 片段；仅当模板可解析且未引用聚合占位时，先
        选出未被引用的改名输出名，再在一次构建内对该子集去重；畸形模板只
        保留告警，不推断引用。
    """
    fragments = []
    warnings = tuple(dict.fromkeys(batch.warnings))
    if warnings:
        fragments.append(('', 'text', '截图配置提示\n' + '\n'.join(warnings)))
    fields = _referenced_fields(original_template)
    if fields is None or _AGGREGATE_NAME in fields:
        return tuple(fragments)
    eligible = [
        name for name in dict.fromkeys(batch.renamed_outputs)
        if name not in fields and batch.values.get(name, '')
    ]
    fragments.extend(build_screenshot_fragments(batch, output_names=eligible))
    return tuple(fragments)


def append_screenshot_notices(
        rendered_content, original_template, batch) -> str:
    """只追加去重提示及未引用的改名结果，绝不再次格式化或上传。

    畸形模板仍追加已有告警，但不推断引用或自动补附图片；普通模板
    校验由通知层负责。转义花括号中的文字不视为实际字段引用。
    """
    fragments = build_screenshot_notice_fragments(batch, original_template)
    if not fragments:
        return rendered_content
    suffix = '\n\n'.join(text for _, _, text in fragments)
    return rendered_content + '\n\n' + suffix if rendered_content else suffix
