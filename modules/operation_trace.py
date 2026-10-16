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
_CLASSIFICATION_WHITELISTS = {
    'stage': frozenset({
        'capture', 'encode', 'save', 'metadata_cache', 'metadata_query',
        'storage_select', 'upload_attempt', 'fallback', 'slot', 'batch',
    }),
    'action': frozenset({
        'read', 'write', 'encode', 'select', 'load', 'save', 'invalidate',
        'refresh', 'recover', 'switch', 'failover', 'capture_retry',
        'capture_format', 'post', 'group', 'profile', 'upload',
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
    'capture', 'encode', 'save', 'upload_attempt', 'slot',
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


def _format_event(event, summary=None) -> str:
    """把安全字段渲染为不含自由文本的单行记录，可附加批次汇总。"""
    parts = []
    for name in _EVENT_FIELDS:
        value = getattr(event, name)
        if value is None or value == '':
            continue
        parts.append(f'{name}={value}')
    line = 'stage_event ' + ' '.join(parts)
    if summary:
        rendered = ' '.join(
            f'{key}={"unavailable" if value is None else value}'
            for key, value in summary.items())
        label = 'plan' if event.status == 'started' else 'summary'
        line = f'{line} {label} {rendered}'
    return line


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
        self._mode = mode
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
        """先写文件事实，再独立尝试终端消费，互不影响。"""
        try:
            LOGGER.log(
                _event_level(event), '%s', _format_event(event, summary))
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
            stage='batch', action=mode, status='started'), summary={
                'mode': mode, 'images_planned': images_planned,
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
