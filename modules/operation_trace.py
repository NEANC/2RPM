#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""结构化阶段事件、关联身份与批次真实操作计数。"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import logging
import time
import uuid


LOGGER = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({'succeeded', 'failed', 'cancelled'})
_IMAGE_TERMINAL_STATUSES = frozenset(
    {'succeeded', 'failed', 'cancelled', 'skipped'})
_BATCH_STATUSES = frozenset(
    {'completed', 'partial_failure', 'failed', 'cancelled'})

_INT_FIELDS = frozenset({
    'image_index', 'slot_index', 'attempt_index', 'http_status', 'storage_id',
    'candidate_count', 'width', 'height', 'byte_count', 'duration_ms',
})
_STR_FIELDS = frozenset({
    'event_id', 'batch_id', 'operation_id', 'request_id', 'stage', 'action',
    'status', 'reason_code', 'provider', 'source', 'cache_layer',
    'selection_source', 'source_operation_id', 'fallback_kind', 'image_format',
})
_EVENT_FIELDS = (
    'event_id', 'batch_id', 'image_index', 'slot_index', 'operation_id',
    'request_id', 'attempt_index', 'stage', 'action', 'status', 'reason_code',
    'provider', 'source', 'cache_layer', 'selection_source',
    'source_operation_id', 'fallback_kind', 'http_status', 'storage_id',
    'candidate_count', 'width', 'height', 'image_format', 'byte_count',
    'duration_ms',
)

_FALLBACK_COUNTERS = {
    'capture_retry': 'capture_retries',
    'capture_format': 'capture_format_fallbacks',
    'preupload_refresh': 'preupload_refreshes',
    'postupload_recovery': 'postupload_recoveries',
    'boltp_candidate': 'boltp_candidate_switches',
    'host_failover': 'host_failovers',
}

# provider 白名单在事件模块内独立定义，避免与 image_host 注册表循环导入；
# 一致性测试会断言它与 registry.UPLOADERS 的键集合完全相等。
_PROVIDER_WHITELIST = frozenset({
    'catbox', 'wmimg', 'beeimg', 'beeimg_cn', 'boltp', 'superbed',
})
_UNKNOWN_CATEGORY = 'unknown'
_MODES = frozenset({'automatic', 'cli'})

_STATUS_WORDS = {
    'started': '开始',
    'succeeded': '成功',
    'failed': '失败',
    'cancelled': '取消',
    'skipped': '已跳过',
    'completed': '完成',
    'partial_failure': '部分失败',
}
_MODE_LABELS = {'automatic': '自动', 'cli': '命令行'}
_STAGE_LABELS = {
    'capture': '截图',
    'window_capture': '窗口截图全流程',
    'encode': '编码',
    'save': '保存',
    'metadata_cache': '缓存',
    'metadata_query': '元数据查询',
    'storage_select': '储存选择',
    'upload_attempt': '上传请求',
    'fallback': '回退',
    'slot': '上传',
}
_ACTION_LABELS = {
    ('metadata_cache', 'load'): '缓存读取',
    ('metadata_cache', 'save'): '缓存写入',
    ('metadata_cache', 'invalidate'): '缓存失效',
    ('metadata_query', 'group'): '分组查询',
    ('metadata_query', 'profile'): '资料查询',
}
_FALLBACK_LABELS = {
    'capture_retry': '重新截图',
    'capture_format': '格式回退',
    'preupload_refresh': '上传前刷新',
    'postupload_recovery': '上传后恢复',
    'boltp_candidate': '候选切换',
    'host_failover': '域名回退',
}
_FALLBACK_CLAUSE = (
    ('capture_retries', '重新截图'),
    ('capture_format_fallbacks', '格式回退'),
    ('preupload_refreshes', '上传前刷新'),
    ('postupload_recoveries', '上传后恢复'),
    ('boltp_candidate_switches', '候选切换'),
    ('host_failovers', '域名回退'),
)
_REASON_LABELS = {
    'unknown': '未知原因',
    'adb_raw_invalid': 'ADB 原始图像无效',
    'batch_cancelled': '批次已取消',
    'slot_unfinished': '上传槽位未完成',
    'recoverable_warning': '可恢复告警',
    'budget_used': '已用尽重试预算',
    'manual_replaced': '已替换手填值',
    'reused_failure': '复用既有失败',
    'reused_metadata': '复用既有元数据',
    'domain_failed': '当前域名失败',
    'window_ready_timeout': '窗口几何等待预算耗尽',
    'operation_failed': '操作失败',
    'slot_failed': '上传槽位失败',
    'storage_rejected': '储存驱动被拒绝',
    'delete_failed': '删除失败',
    'disabled': '已禁用',
    'forbidden': '无访问权限',
    'invalid': '数据无效',
    'invalid_identity': '缓存标识无效',
    'io_failed': '读写失败',
    'save_failed': '保存失败',
    'save_conflict': '保存冲突',
    'unavailable': '不可用',
    'capture_failed': '截图失败',
    'encoding_failed': '编码失败',
    'encoding_reused': '复用已编码图像',
    'response_too_large': '响应过大',
    'first_success': '首个成功结果',
}
_UNKNOWN_STAGE = '未知阶段'
_UNKNOWN_REASON = '未知原因'
_UNKNOWN_PLANNED = '未知'
_CLASSIFICATION_WHITELISTS = {
    'reason_code': frozenset({
        '', 'invalid_input', 'not_configured', 'invalid_hosts', 'invalid_host',
        'invalid_provider', 'unknown_provider', 'invalid_token',
        'invalid_environment_reference', 'missing_environment',
        'invalid_options', 'invalid_url', 'upload_failed', 'config_error',
        'storage_lookup_failed', 'storage_unavailable', 'transport_failed',
        'http_failed', 'business_rejected', 'profile_forbidden',
        'profile_forbidden_invalid_storage', 'invalid_response',
        'invalid_source', 'invalid_title', 'window_not_found',
        'window_ambiguous', 'window_lookup_failed', 'window_gone',
        'window_state_failed', 'window_ready_timeout', 'invalid_dimensions',
        'dpi_failed',
        'gdi_failed', 'print_failed', 'image_failed', 'encode_failed',
        'invalid_serial', 'adb_version_unsupported', 'adb_unavailable',
        'adb_not_found', 'adb_offline', 'adb_unauthorized', 'adb_timeout',
        'adb_protocol_failed', 'adb_image_failed', 'adb_encode_failed',
        'adb_raw_invalid', 'batch_cancelled', 'slot_unfinished',
        'recoverable_warning', 'budget_used', 'manual_replaced',
        'reused_failure', 'reused_metadata', 'domain_failed',
        'operation_failed', 'slot_failed', 'storage_rejected',
        'delete_failed', 'disabled', 'forbidden', 'invalid',
        'invalid_identity', 'io_failed', 'save_failed', 'unavailable',
        'capture_failed', 'encoding_failed', 'encoding_reused',
        'response_too_large', 'first_success', 'save_conflict',
    }),
    'image_format': frozenset({'jpeg', 'png', 'webp', 'raw'}),
    'stage': frozenset({
        'capture', 'window_capture', 'encode', 'save', 'metadata_cache',
        'metadata_query', 'storage_select', 'upload_attempt', 'fallback',
        'slot', 'batch',
    }),
    'action': frozenset({
        'read', 'write', 'encode', 'select', 'load', 'save', 'invalidate',
        'refresh', 'recover', 'switch', 'failover', 'capture_retry',
        'capture_format', 'post', 'group', 'profile', 'upload', 'total',
        'automatic', 'cli',
    }),
    'status': frozenset({
        'started', 'succeeded', 'failed', 'cancelled', 'skipped',
        'completed', 'partial_failure',
    }),
    'provider': _PROVIDER_WHITELIST,
    'source': frozenset({
        'window', 'adb', 'hit', 'miss', 'expired', 'future',
    }),
    'cache_layer': frozenset({'event', 'disk'}),
    'selection_source': frozenset({
        'manual', 'default', 'group_first', 'candidate',
    }),
    'fallback_kind': frozenset(_FALLBACK_COUNTERS),
}

_DEBUG_STAGES = frozenset({
    'metadata_query', 'storage_select',
})
_INFO_STAGES = frozenset({
    'capture', 'window_capture', 'encode', 'save', 'upload_attempt', 'slot',
})

_REQUEST_STAGES = {
    'group': 'metadata_query', 'profile': 'metadata_query',
    'upload_attempt': 'upload_attempt',
}

_TRACE = ContextVar('operation_trace_batch', default=None)
_EVENT = ContextVar('operation_trace_event', default=None)
_ITEM = ContextVar('operation_trace_item', default=None)
_REQUEST = ContextVar('operation_trace_request', default=None)
_FALLBACK = ContextVar('operation_trace_fallback', default=None)


@dataclass(frozen=True)
class StageEvent:
    """保存一条安全阶段事实，字段固定为白名单。"""

    event_id: str = None
    batch_id: str = None
    image_index: int = None
    slot_index: int = None
    operation_id: str = None
    request_id: str = None
    attempt_index: int = None
    stage: str = None
    action: str = None
    status: str = None
    reason_code: str = None
    provider: str = None
    source: str = None
    cache_layer: str = None
    selection_source: str = None
    source_operation_id: str = None
    fallback_kind: str = None
    http_status: int = None
    storage_id: int = None
    candidate_count: int = None
    width: int = None
    height: int = None
    image_format: str = None
    byte_count: int = None
    duration_ms: int = None


def _new_id() -> str:
    """生成不含业务数据的随机关联标识。"""
    return uuid.uuid4().hex


@contextmanager
def notification_event_scope():
    token = _EVENT.set(_new_id())
    try:
        yield
    finally:
        _EVENT.reset(token)


def current_event_id():
    return _EVENT.get()


def _milliseconds(delta: float) -> int:
    """把单调秒差固定为非负毫秒整数。"""
    return max(0, int(delta * 1000))


def _valid_storage_id(provider, value):
    """按 provider 分支校验存储编号，非法值不予输出。"""
    if type(value) is not int:
        return None
    if provider == 'boltp':
        return value if value >= 0 else None
    if provider == 'beeimg_cn':
        return value if value > 0 else None
    return None


def _create_event(**fields) -> StageEvent:
    """只保留已知字段及合法简单值，未知动态分类归入固定 unknown。"""
    values = {}
    for name in _EVENT_FIELDS:
        value = fields.get(name)
        if value is None:
            continue
        if name in _INT_FIELDS:
            if type(value) is not int:
                continue
            if name == 'http_status' and not 100 <= value <= 599:
                continue
            if name in ('width', 'height') and value < 0:
                continue
        elif name in _STR_FIELDS:
            if type(value) is not str:
                continue
            whitelist = _CLASSIFICATION_WHITELISTS.get(name)
            if whitelist is not None and value not in whitelist:
                value = _UNKNOWN_CATEGORY
        values[name] = value
    if 'storage_id' in values:
        storage_id = _valid_storage_id(
            values.get('provider'), values['storage_id'])
        if storage_id is None:
            del values['storage_id']
        else:
            values['storage_id'] = storage_id
    return StageEvent(**values)


def _upload_provider_label(provider, slot_index=None) -> str:
    """经共享安全名称函数返回不会回显原文的通道展示名。"""
    from modules.feedback_text import upload_provider_name
    return upload_provider_name(provider, slot_index=slot_index)


def _reason_text(event) -> str:
    """经共享安全原因函数与本地表返回不含自由文本的固定原因。"""
    from modules.feedback_text import canonical_failure_code
    from modules.feedback_text import safe_reason_text
    code = event.reason_code
    if not code:
        return ''
    if canonical_failure_code('capture', code) == code:
        return safe_reason_text('capture', code)
    if canonical_failure_code('upload', code) == code:
        return safe_reason_text('upload', code)
    return _REASON_LABELS.get(code, _UNKNOWN_REASON)


def _context_prefix(event) -> str:
    """按图片与槽位身份生成中文前缀，缺失身份时不捏造。"""
    if event.image_index is None:
        return ''
    if event.slot_index is None:
        return f'截图 {event.image_index}：'
    return f'截图 {event.image_index} / 图床 {event.slot_index + 1}：'


def _stage_body(event) -> str:
    """按阶段、动作与状态生成固定中文主体，未知类别安全回退。"""
    stage = event.stage
    status = event.status
    word = _STATUS_WORDS.get(status, _UNKNOWN_STAGE)
    if stage == 'upload_attempt':
        name = _upload_provider_label(event.provider, event.slot_index)
        attempt = (f'第 {event.attempt_index} 次'
                   if event.attempt_index is not None else '')
        return f'通道 [{name}] {attempt}上传请求{word}'
    if stage == 'slot':
        name = _upload_provider_label(event.provider, event.slot_index)
        if status == 'skipped':
            return f'通道 [{name}] 已跳过上传'
        return f'通道 [{name}] 上传{word}'
    if stage == 'fallback':
        label = _FALLBACK_LABELS.get(event.fallback_kind, '回退')
    else:
        label = _ACTION_LABELS.get((stage, event.action))
        if label is None:
            label = _STAGE_LABELS.get(stage, _UNKNOWN_STAGE)
    if status == 'skipped':
        return f'已跳过{label}'
    if (stage == 'metadata_cache' and event.action == 'load'
            and event.source == 'hit'):
        return '缓存命中'
    return f'{label}{word}'


def _diagnostic_suffix(event) -> str:
    """按存在值追加 HTTP、耗时与安全原因，缺失值不捏造也不写零。"""
    text = ''
    if event.http_status is not None:
        text += f'，HTTP {event.http_status}'
    if event.duration_ms is not None:
        text += f'，耗时 {event.duration_ms} ms'
    reason = _reason_text(event)
    if reason:
        text += f'，原因：{reason}'
    return text


def _identity_suffix(event) -> str:
    """按存在值追加简短英文关联标识，不铺开全部字段。"""
    pairs = []
    if event.batch_id is not None:
        pairs.append(f'batch_id={event.batch_id}')
    if event.operation_id is not None:
        pairs.append(f'operation_id={event.operation_id}')
    if event.request_id is not None:
        pairs.append(f'request_id={event.request_id}')
    if event.source_operation_id is not None:
        pairs.append(f'source_operation_id={event.source_operation_id}')
    if not pairs:
        return ''
    return '（' + '，'.join(pairs) + '）'


def _planned_text(summary, key) -> str:
    """把计划量渲染为固定文本，未知保留 None 显示未知。"""
    value = None if not summary else summary.get(key)
    return _UNKNOWN_PLANNED if value is None else str(value)


def _fallback_clause(summary) -> str:
    """把非零的回退与恢复计数渲染为一条简短中文子句。"""
    return '、'.join(
        f'{label} {summary.get(key, 0)}'
        for key, label in _FALLBACK_CLAUSE if summary.get(key, 0))


def _batch_final_body(event, summary, image_upload_stats) -> str:
    """渲染唯一批次终态的中文摘要，覆盖计划量、四类图片与真实请求计数。"""
    data = summary or {}
    stats = image_upload_stats or {
        'succeeded': 0, 'cancelled': 0, 'failed': 0, 'not_executed': 0}
    clauses = [
        f'生成成功 {data.get("images_succeeded", 0)}/'
        f'{_planned_text(data, "images_planned")}',
        f'上传取得至少一个成功结果的图片 {stats["succeeded"]}，'
        f'上传取消的图片 {stats["cancelled"]}，'
        f'已执行但无成功结果的图片 {stats["failed"]}，'
        f'未执行上传的图片 {stats["not_executed"]}',
        f'上传槽位成功 {data.get("slots_succeeded", 0)}、'
        f'失败 {data.get("slots_failed", 0)}、'
        f'取消 {data.get("slots_cancelled", 0)}、'
        f'跳过 {data.get("slots_skipped", 0)}',
        f'分组查询尝试 {data.get("group_get_attempts", 0)}、'
        f'成功 {data.get("group_get_succeeded", 0)}、'
        f'失败 {data.get("group_get_failed", 0)}、'
        f'取消 {data.get("group_get_cancelled", 0)}',
        f'资料查询尝试 {data.get("profile_get_attempts", 0)}、'
        f'成功 {data.get("profile_get_succeeded", 0)}、'
        f'失败 {data.get("profile_get_failed", 0)}、'
        f'取消 {data.get("profile_get_cancelled", 0)}',
        f'上传请求尝试 {data.get("upload_post_attempts", 0)}、'
        f'成功 {data.get("upload_post_succeeded", 0)}、'
        f'失败 {data.get("upload_post_failed", 0)}、'
        f'取消 {data.get("upload_post_cancelled", 0)}',
    ]
    fallbacks = _fallback_clause(data)
    if fallbacks:
        clauses.append(fallbacks)
    duration = event.duration_ms if event.duration_ms is not None else 0
    clauses.append(f'耗时 {duration} ms')
    word = _STATUS_WORDS.get(event.status, _UNKNOWN_STAGE)
    text = f'截图批次{word}：' + '；'.join(clauses)
    reason = _reason_text(event)
    if reason:
        text += f'，原因：{reason}'
    return text


def _format_event(event, summary=None, *, image_upload_stats=None) -> str:
    """把安全字段渲染为中文单行记录，可附加批次中文摘要与派生投影。"""
    if event.stage == 'batch':
        if event.status == 'started':
            body = (
                f'截图批次开始：模式 {_MODE_LABELS.get(event.action, _UNKNOWN_STAGE)}'
                f'，计划图片 {_planned_text(summary, "images_planned")}'
                f'，计划槽位 {_planned_text(summary, "slots_planned")}')
        else:
            body = _batch_final_body(event, summary, image_upload_stats)
    else:
        body = (_context_prefix(event) + _stage_body(event)
                + _diagnostic_suffix(event))
    suffix = _identity_suffix(event)
    if suffix:
        body += suffix
    return body


def _event_level(event) -> int:
    """按阶段与结果选择唯一日志级别。"""
    stage = event.stage
    status = event.status
    if status in ('skipped', 'cancelled'):
        return logging.INFO
    if stage == 'batch':
        # 完成但带可恢复告警的批次按 WARNING 记录唯一终态
        if status == 'completed' and event.reason_code:
            return logging.WARNING
        return {
            'completed': logging.INFO,
            'cancelled': logging.INFO,
            'partial_failure': logging.WARNING,
            'failed': logging.ERROR,
        }.get(status, logging.INFO)
    if status == 'started':
        if stage == 'upload_attempt':
            return logging.INFO
        return logging.DEBUG
    if status == 'failed':
        # 槽位最终失败表示该图片/上传项已无可用结果，按规格使用 ERROR
        if stage == 'slot':
            return logging.ERROR
        if stage == 'metadata_query':
            return logging.WARNING
        if stage in _DEBUG_STAGES:
            return logging.DEBUG
        # 缓存 IO 降级（如 metadata_cache 读写/失效失败）已不在 DEBUG 阶段集合
        return logging.WARNING
    if stage == 'fallback':
        return logging.WARNING
    if stage in _INFO_STAGES:
        return logging.INFO
    return logging.DEBUG


class Operation:
    """表示一次已开始阶段，首次终态由调用方显式给出。"""

    def __init__(self, trace, stage, action, event, started_at):
        """保存所属批次引用、开始事件与单调起点。"""
        self._trace = trace
        self._stage = stage
        self._action = action
        self._event = event
        self._started_at = started_at
        self._ended_at = None
        self._finished = False
        self._transport_started = False

    def mark_transport_started(self):
        """在实际传输调用的紧邻边界登记一次请求。"""
        if self._transport_started or self._trace is None:
            return
        if self._stage == 'upload_attempt':
            counter = 'upload_post_attempts'
        elif self._stage == 'metadata_query' and self._action in ('group', 'profile'):
            counter = f'{self._action}_get_attempts'
        else:
            return
        self._transport_started = True
        self._trace._named[counter] = self._trace._named.get(counter, 0) + 1

    def mark_end(self):
        """在传输结束时冻结耗时终点，使后续解析不计入请求耗时。"""
        if self._ended_at is None and self._trace is not None:
            self._ended_at = self._trace._clock()

    def finish(self, status, *, reason_code='', **safe_fields) -> None:
        """仅接受终态一次，重复调用与额外字段被安全忽略。"""
        if self._finished or self._trace is None:
            return
        if status not in _TERMINAL_STATUSES:
            return
        self._finished = True
        self._trace._finish_operation(self, status, reason_code, safe_fields)

    def _auto_finish(self, status) -> None:
        """在调用方未显式收尾时补充固定终态。"""
        if self._finished or self._trace is None:
            return
        reason_code = (self._event.reason_code
                       if self._stage == 'fallback' else '')
        self.finish(status, reason_code=reason_code)


class BatchTrace:
    """保存一个批次内的事件事实、阶段计数与终态。"""

    def __init__(self, *, mode, images_planned, slots_planned, clock,
                 on_status):
        """初始化唯一批次身份、单调起点与空计数。"""
        self._mode = (mode if type(mode) is str and mode in _MODES
                      else _UNKNOWN_CATEGORY)
        self._images_planned = images_planned
        self._slots_planned = slots_planned
        self._clock = clock
        self._on_status = on_status
        self._batch_id = _new_id()
        self._event_id = current_event_id() or _new_id()
        self._started_at = clock()
        self._stages = {}
        self._named = {}
        self._images = {}
        self._slots = {}
        self._slot_order = []
        self._attempts = {}
        self._last_capture_operation = None
        self._status = None
        self._cancelled = False
        self._failed = False
        self._finalized = False
        self._recoverable_warning = False

    def _count(self, stage, key, amount=1) -> None:
        """按阶段累计一个固定计数值。"""
        bucket = self._stages.setdefault(stage, {})
        bucket[key] = bucket.get(key, 0) + amount

    def _build_event(self, **fields) -> StageEvent:
        """补充当前图片上下文、批次身份与事件标识。"""
        payload = dict(fields)
        item = _ITEM.get()
        if item is not None:
            payload.setdefault('image_index', item[0])
            payload.setdefault('slot_index', item[1])
        payload.setdefault('batch_id', self._batch_id)
        payload.setdefault('event_id', self._event_id)
        payload.setdefault('source_operation_id', _FALLBACK.get())
        return _create_event(**payload)

    def _record_event(self, event, summary=None) -> None:
        """先写文件事实，再独立尝试终端消费，互不影响。

        结构化 extra 只暴露安全事件、摘要副本与批次投影；普通格式化器
        不序列化这些字段，仅使用其中的中文消息。
        """
        stats = (self._image_upload_stats()
                 if event.stage == 'batch' and event.status != 'started'
                 else None)
        extra = {
            'stage_event': event,
            'stage_summary': dict(summary) if summary is not None else None,
            'image_upload_stats': stats,
        }
        try:
            LOGGER.log(
                _event_level(event), '%s',
                _format_event(event, summary, image_upload_stats=stats),
                extra=extra)
        except Exception:
            pass
        if self._on_status is not None:
            try:
                self._on_status(event)
            except Exception:
                pass

    def _start_operation(self, stage, action, safe_fields) -> Operation:
        """开始一次阶段操作并记录开始事件。

        开始事件若因控制信号写出中断，仍为该操作补记唯一取消终态后
        原样传播控制信号；普通异常由事件写出路径自行吞掉，不影响业务。
        """
        self._count(stage, 'started')
        payload = dict(safe_fields)
        payload.update(
            operation_id=_new_id(), stage=stage, action=action,
            status='started')
        event = self._build_event(**payload)
        operation = Operation(self, stage, action, event, self._clock())
        try:
            self._record_event(event)
        except (KeyboardInterrupt, SystemExit):
            operation._auto_finish('cancelled')
            raise
        return operation

    def _next_attempt_index(self) -> int:
        """按当前图片槽位累计真实上传尝试序号。"""
        key = _ITEM.get()
        count = self._attempts.get(key, 0) + 1
        self._attempts[key] = count
        return count

    def _start_request(self, provider, stage) -> Operation:
        """进入一次真实请求调用前分配请求身份与实际尝试序号。"""
        event_stage = _REQUEST_STAGES[stage]
        action = 'post' if stage == 'upload_attempt' else stage
        fields = {'provider': provider, 'request_id': _new_id()}
        if stage == 'upload_attempt':
            fields['attempt_index'] = self._next_attempt_index()
        operation = self._start_operation(event_stage, action, fields)
        _REQUEST.set(operation._event.operation_id)
        return operation

    def _finish_operation(self, operation, status, reason_code,
                          safe_fields) -> None:
        """产出同一 operation_id 的唯一终态与完整耗时。"""
        self._count(operation._stage, status)
        started = operation._event
        if started.stage in ('capture', 'encode'):
            self._last_capture_operation = started.operation_id
        if started.stage == 'fallback':
            counter = _FALLBACK_COUNTERS.get(started.fallback_kind)
            if counter is not None:
                self._named[counter] = self._named.get(counter, 0) + 1
        elif started.stage == 'upload_attempt':
            if operation._transport_started:
                terminal = f'upload_post_{status}'
                self._named[terminal] = self._named.get(terminal, 0) + 1
        elif operation._transport_started and started.stage == 'metadata_query' and started.action in (
                'group', 'profile'):
            terminal = f'{started.action}_get_{status}'
            self._named[terminal] = self._named.get(terminal, 0) + 1
        payload = {}
        for name in _EVENT_FIELDS:
            value = getattr(started, name)
            if value is not None:
                payload[name] = value
        payload.update(safe_fields)
        ended_at = operation._ended_at
        if ended_at is None:
            ended_at = self._clock()
        payload.update(
            event_id=self._event_id, status=status, reason_code=reason_code,
            duration_ms=_milliseconds(ended_at - operation._started_at))
        self._record_event(_create_event(**payload))

    def _record_skipped(self, stage, reason_code, safe_fields) -> None:
        """记录一次未执行的跳过，不伪造开始与耗时。"""
        self._count(stage, 'skipped')
        payload = dict(safe_fields)
        payload.update(
            operation_id=_new_id(), stage=stage, status='skipped',
            reason_code=reason_code)
        self._record_event(self._build_event(**payload))

    def start_slot(self, image_index: int, slot_index: int, *,
                   provider: str) -> None:
        """为逻辑槽位生成唯一开始事件，重复开始不再补发。"""
        key = (image_index, slot_index)
        if key in self._slots:
            return
        record = {
            'operation_id': _new_id(),
            'provider': provider,
            'started_at': self._clock(),
            'status': None,
            'image_index': image_index,
            'slot_index': slot_index,
        }
        self._slots[key] = record
        self._slot_order.append(key)
        self._record_event(self._build_event(
            image_index=image_index, slot_index=slot_index,
            operation_id=record['operation_id'], stage='slot', action='upload',
            status='started', provider=provider))

    def finish_slot(self, image_index: int, slot_index: int, status: str, *,
                    reason_code: str = '') -> None:
        """作为槽位终态的唯一生产者，首次完成即冻结。"""
        if status not in ('succeeded', 'failed', 'cancelled', 'skipped'):
            return
        key = (image_index, slot_index)
        record = self._slots.get(key)
        if record is None:
            record = {
                'operation_id': _new_id(),
                'provider': None,
                'started_at': None,
                'status': None,
                'image_index': image_index,
                'slot_index': slot_index,
            }
            self._slots[key] = record
            self._slot_order.append(key)
        if record['status'] is not None:
            return
        record['status'] = status
        self._count('slot', status)
        duration_ms = None
        if record['started_at'] is not None:
            duration_ms = _milliseconds(
                self._clock() - record['started_at'])
        self._record_event(self._build_event(
            image_index=image_index, slot_index=slot_index,
            operation_id=record['operation_id'], stage='slot', action='upload',
            status=status, reason_code=reason_code, provider=record['provider'],
            duration_ms=duration_ms))

    def finish_image(self, index: int, status: str) -> None:
        """只保存逻辑图片状态，不产生额外事件或阶段计数。"""
        if status in _IMAGE_TERMINAL_STATUSES and index not in self._images:
            self._images[index] = status

    def skip_unstarted_slots(self, image_index: int, count: int, *,
                             reason_code: str = '') -> None:
        """把未开始的计划槽位补记为跳过，不触碰已开始或已完成的槽位。"""
        for slot_index in range(count):
            if (image_index, slot_index) not in self._slots:
                self.finish_slot(image_index, slot_index, 'skipped',
                                 reason_code=reason_code)

    def slot_outcome(self, image_index: int):
        """汇总某逻辑图片的真实槽位结果，只有跳过时不作为结果。"""
        statuses = [
            record['status'] for (index, _), record in self._slots.items()
            if index == image_index
            and record['status'] in ('succeeded', 'failed', 'cancelled')]
        if not statuses:
            return None
        if 'succeeded' in statuses:
            return 'succeeded'
        if 'failed' in statuses:
            return 'failed'
        return 'cancelled'

    def _has_recoverable_warnings(self) -> bool:
        """判断已完成批次是否带有可恢复告警，用于唯一汇总级别。"""
        if self._recoverable_warning:
            return True
        for counter in (
                'capture_retries', 'capture_format_fallbacks',
                'preupload_refreshes', 'postupload_recoveries',
                'boltp_candidate_switches', 'host_failovers'):
            if self._named.get(counter, 0):
                return True
        for stage in ('capture', 'encode', 'save'):
            if self._stages.get(stage, {}).get('failed', 0):
                return True
        return False

    def _image_upload_stats(self) -> dict:
        """以图片身份为基集合，从槽位事实派生只读上传分组计数。

        每张图按已记录槽位状态归类，优先级为成功、取消、失败、未执行；
        没有任何实际槽位或仅跳过的图片计入未执行上传。
        """
        stats = {
            'succeeded': 0, 'cancelled': 0, 'failed': 0, 'not_executed': 0}
        for image_index in self._images:
            statuses = {
                record['status'] for (index, _), record in self._slots.items()
                if index == image_index and record['status'] is not None}
            if 'succeeded' in statuses:
                stats['succeeded'] += 1
            elif 'cancelled' in statuses:
                stats['cancelled'] += 1
            elif 'failed' in statuses:
                stats['failed'] += 1
            else:
                stats['not_executed'] += 1
        return stats

    def set_status(self, status: str) -> None:
        """由编排层显式指定唯一批次终态。"""
        if status in _BATCH_STATUSES:
            self._status = status

    def _final_status(self) -> str:
        """按已记录事实推断批次终态，未指定时才推断。"""
        if self._status is not None:
            return self._status
        if self._cancelled:
            return 'cancelled'
        if self._failed:
            return 'failed'
        statuses = list(self._images.values())
        if not statuses or all(item == 'succeeded' for item in statuses):
            return 'completed'
        if any(item == 'succeeded' for item in statuses):
            return 'partial_failure'
        return 'failed'

    def _finalize(self) -> None:
        """收尾未完成槽位并输出唯一批次汇总事件。"""
        if self._finalized:
            return
        self._finalized = True
        fallback = 'cancelled' if self._cancelled else 'failed'
        reason = 'batch_cancelled' if self._cancelled else 'slot_unfinished'
        for key in self._slot_order:
            record = self._slots[key]
            if record['status'] is None:
                self.finish_slot(
                    record['image_index'], record['slot_index'], fallback,
                    reason_code=reason)
        final_status = self._final_status()
        self._record_event(_create_event(
            event_id=self._event_id, batch_id=self._batch_id, stage='batch',
            action=self._mode, status=final_status,
            reason_code=('recoverable_warning'
                         if final_status == 'completed'
                         and self._has_recoverable_warnings() else ''),
            duration_ms=_milliseconds(self._clock() - self._started_at)),
            summary=self.summary())

    def summary(self):
        """汇总已知阶段的真实计数、计划量与批次终态。"""
        stages = self._stages

        def counted(stage, key):
            """读取某阶段的固定计数，缺省为零。"""
            return stages.get(stage, {}).get(key, 0)

        def images(status):
            """按逻辑图片首次终态计数。"""
            return sum(item == status for item in self._images.values())

        named = self._named
        return {
            'mode': self._mode,
            'status': self._final_status(),
            'images_planned': self._images_planned,
            'images_processed': len(self._images) - images('skipped'),
            'images_succeeded': images('succeeded'),
            'images_failed': images('failed'),
            'images_cancelled': images('cancelled'),
            'images_skipped': images('skipped'),
            'capture_started': counted('capture', 'started'),
            'capture_succeeded': counted('capture', 'succeeded'),
            'capture_failed': counted('capture', 'failed'),
            'capture_cancelled': counted('capture', 'cancelled'),
            'capture_skipped': counted('capture', 'skipped'),
            'capture_retries': named.get('capture_retries', 0),
            'capture_format_fallbacks': named.get(
                'capture_format_fallbacks', 0),
            'encode_started': counted('encode', 'started'),
            'encode_succeeded': counted('encode', 'succeeded'),
            'encode_failed': counted('encode', 'failed'),
            'encode_cancelled': counted('encode', 'cancelled'),
            'encode_skipped': counted('encode', 'skipped'),
            'save_started': counted('save', 'started'),
            'save_succeeded': counted('save', 'succeeded'),
            'save_failed': counted('save', 'failed'),
            'save_cancelled': counted('save', 'cancelled'),
            'save_skipped': counted('save', 'skipped'),
            'group_get_attempts': named.get('group_get_attempts', 0),
            'group_get_succeeded': named.get('group_get_succeeded', 0),
            'group_get_failed': named.get('group_get_failed', 0),
            'group_get_cancelled': named.get('group_get_cancelled', 0),
            'profile_get_attempts': named.get('profile_get_attempts', 0),
            'profile_get_succeeded': named.get('profile_get_succeeded', 0),
            'profile_get_failed': named.get('profile_get_failed', 0),
            'profile_get_cancelled': named.get('profile_get_cancelled', 0),
            'upload_post_attempts': named.get('upload_post_attempts', 0),
            'upload_post_succeeded': named.get('upload_post_succeeded', 0),
            'upload_post_failed': named.get('upload_post_failed', 0),
            'upload_post_cancelled': named.get('upload_post_cancelled', 0),
            'preupload_refreshes': named.get('preupload_refreshes', 0),
            'postupload_recoveries': named.get('postupload_recoveries', 0),
            'boltp_candidate_switches': named.get(
                'boltp_candidate_switches', 0),
            'host_failovers': named.get('host_failovers', 0),
            'fallbacks_skipped': counted('fallback', 'skipped'),
            'slots_planned': self._slots_planned,
            'slots_succeeded': counted('slot', 'succeeded'),
            'slots_failed': counted('slot', 'failed'),
            'slots_cancelled': counted('slot', 'cancelled'),
            'slots_skipped': counted('slot', 'skipped'),
        }


@contextmanager
def trace_scope(*, mode, images_planned=None, slots_planned=None,
                clock=time.monotonic, on_status=None):
    """建立唯一批次作用域，收尾时输出汇总并传播控制信号。"""
    trace = BatchTrace(
        mode=mode, images_planned=images_planned, slots_planned=slots_planned,
        clock=clock, on_status=on_status)
    trace_token = _TRACE.set(trace)
    item_token = _ITEM.set(None)
    request_token = _REQUEST.set(None)
    try:
        trace._record_event(trace._build_event(
            stage='batch', action=trace._mode, status='started'), summary={
                'mode': trace._mode, 'images_planned': images_planned,
                'slots_planned': slots_planned})
        yield trace
    except (KeyboardInterrupt, SystemExit):
        trace._cancelled = True
        raise
    except BaseException:
        trace._failed = True
        raise
    finally:
        try:
            trace._finalize()
        finally:
            try:
                _REQUEST.reset(request_token)
            finally:
                try:
                    _ITEM.reset(item_token)
                finally:
                    _TRACE.reset(trace_token)


@contextmanager
def trace_item(*, image_index, slot_index=None):
    """为当前逻辑图片绑定稳定序号，仅影响关联字段。"""
    token = _ITEM.set((image_index, slot_index))
    try:
        yield
    finally:
        _ITEM.reset(token)


@contextmanager
def trace_operation(stage, action, **safe_fields):
    """在活动作用域内开始一次阶段操作，无作用域时为空操作。"""
    trace = _TRACE.get()
    if trace is None:
        yield Operation(None, stage, action, None, None)
        return
    operation = trace._start_operation(stage, action, safe_fields)
    try:
        yield operation
    except (KeyboardInterrupt, SystemExit):
        operation._auto_finish('cancelled')
        raise
    except BaseException:
        operation._auto_finish('failed')
        raise
    else:
        operation._auto_finish('succeeded')


def trace_skipped(stage, *, reason_code='', **safe_fields):
    """在活动作用域内记录一次预期跳过，无作用域时不产生任何输出。"""
    trace = _TRACE.get()
    if trace is None:
        return
    trace._record_skipped(stage, reason_code, safe_fields)


@contextmanager
def trace_fallback(kind, action, *, reason_code, **fields):
    fields.setdefault('source_operation_id', current_request_id())
    with trace_operation('fallback', action, fallback_kind=kind,
                         reason_code=reason_code, **fields) as operation:
        token = _FALLBACK.set(
            operation._event.operation_id if operation._event else None)
        try:
            yield operation
        finally:
            _FALLBACK.reset(token)


@contextmanager
def trace_request(provider: str, stage: str):
    """在活动作用域内记录一次真实请求，无作用域或未知阶段时为空操作。"""
    trace = _TRACE.get()
    if trace is None or stage not in _REQUEST_STAGES:
        yield Operation(None, None, None, None, None)
        return
    operation = trace._start_request(provider, stage)
    try:
        yield operation
    except (KeyboardInterrupt, SystemExit):
        operation._auto_finish('cancelled')
        raise
    except BaseException:
        operation._auto_finish('failed')
        raise
    else:
        operation._auto_finish('succeeded')


class SlotHandle:
    """表示一次逻辑上传槽位，只公开唯一的终态入口。"""

    def __init__(self, trace, image_index, slot_index):
        """保存所属批次与已绑定的图片及槽位序号。"""
        self._trace = trace
        self._image_index = image_index
        self._slot_index = slot_index

    @property
    def operation_id(self):
        if self._trace is None:
            return None
        return self._trace._slots[(self._image_index, self._slot_index)]['operation_id']

    @contextmanager
    def scope(self):
        if self._trace is None:
            yield
            return
        with trace_item(image_index=self._image_index, slot_index=self._slot_index):
            yield

    def finish(self, status, *, reason_code='') -> None:
        """把槽位终态交给唯一生产者，重复完成被忽略。"""
        if self._trace is None:
            return
        self._trace.finish_slot(
            self._image_index, self._slot_index, status,
            reason_code=reason_code)


def trace_slot(provider: str, fallback_index: int = 0) -> SlotHandle:
    """开始当前逻辑槽位；编排未绑定槽位序号时回退到配置项序号。"""
    trace = _TRACE.get()
    if trace is None:
        return SlotHandle(None, None, None)
    item = _ITEM.get()
    image_index = 0 if item is None else item[0]
    slot_index = (fallback_index if item is None or item[1] is None
                  else item[1])
    trace.start_slot(image_index, slot_index, provider=provider)
    return SlotHandle(trace, image_index, slot_index)


def trace_warning():
    trace = _TRACE.get()
    if trace is not None:
        trace._recoverable_warning = True


def current_request_id():
    """返回当前作用域内最近一次真实请求的关联标识，无请求时为空。"""
    return _REQUEST.get()


def current_capture_operation_id():
    trace = _TRACE.get()
    return trace._last_capture_operation if trace is not None else None


def trace_slots_skipped(image_index: int, count: int, *,
                        reason_code: str = '') -> None:
    """在活动作用域内把未开始的计划槽位补记为跳过，无作用域时不输出。"""
    trace = _TRACE.get()
    if trace is None:
        return
    trace.skip_unstarted_slots(image_index, count, reason_code=reason_code)


def trace_slot_outcome(image_index: int):
    """返回活动作用域内某逻辑图片的槽位综合结果，无槽位事实时为空。"""
    trace = _TRACE.get()
    if trace is None:
        return None
    return trace.slot_outcome(image_index)
