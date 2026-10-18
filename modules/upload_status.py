#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""把安全上传阶段事件显示为终端进度，不参与任何文件记录或业务判定。"""

import contextlib
import sys
import threading

from modules.feedback_text import canonical_upload_provider
from modules.feedback_text import upload_provider_name
from modules.spinner import current_spinner
from modules.spinner import spinner_phase
from modules.spinner import temporary_text


# 上传阶段统一文案，进入与恢复都只围绕该文案
_PHASE_TEXT = '正在上传截图'
# 终态定格图标：成功 ✔️、失败 ❌（单空格后接安全通道名）
_ICON_DONE = '\u2714\ufe0f'
_ICON_FAIL = '\u274c'
# 只登记终态身份、不显示任何 UI 的槽位状态
_SILENT_STATUSES = frozenset({'cancelled', 'skipped'})
# 需要登记并可能显示唯一终态的槽位状态
_TERMINAL_STATUSES = frozenset({'succeeded', 'failed', 'cancelled', 'skipped'})
# 借用句柄必须具备的公开属性，缺任一即判定不可借用
_BORROW_ATTRS = ('closed', 'owner_thread_id', 'stream')


class _ActivePhase:
    """保存一次活动上传阶段借用的资源栈与写入句柄。"""

    __slots__ = ('stack', 'handle')

    def __init__(self, stack, handle):
        """记录资源栈与可写入句柄，句柄为空表示普通行阶段。

        Args:
            stack (contextlib.ExitStack): 阶段退出时统一关闭的资源栈。
            handle: 可写入的 TTY 句柄，普通行阶段为 None。
        """
        self.stack = stack
        self.handle = handle


class _UploadStatusConsumer:
    """批次稳定上传消费者，按槽位身份去重并显示唯一安全终态。

    内部只持有所有者线程、终态身份集合、UI 停止标记与活动阶段栈；
    不换 trace 回调、不重传、不参与业务判定。

    Attributes:
        _spinner: 显式传入、优先借用的外层句柄。
        _stream: 显式选定的反馈流；为空时回退 sys.stdout。
        _images_planned: 编排已知的逻辑图片数，用于多图后缀判定。
        _slot_providers: 净化后的槽位 provider 序列，用于配置项后缀判定。
    """

    def __init__(self, *, spinner, stream, images_planned, slot_providers):
        """保存借用句柄、反馈流、计划量与净化槽位序列。

        Args:
            spinner: 显式外层 TTY 句柄，缺省时再退化到当前活动句柄。
            stream: 显式反馈流，用于限制可借用句柄并承载普通行。
            images_planned: 逻辑图片计划数；仅 >1 的整数才追加截图后缀。
            slot_providers: 编排层净化后的 provider 序列。
        """
        self._spinner = spinner
        self._stream = stream
        self._images_planned = images_planned
        self._slot_providers = tuple(
            canonical_upload_provider(provider) for provider in slot_providers)
        self._owner_thread_id = threading.get_ident()
        self._identities = set()
        self._stopped = False
        self._phases = []
        self._active = None

    def __call__(self, event):
        """消费单个安全阶段事件，跨线程或已停 UI 时只做必要登记。"""
        if threading.get_ident() != self._owner_thread_id:
            return
        if self._stopped:
            self._register(event)
            return
        try:
            self._consume(event)
        except (KeyboardInterrupt, SystemExit):
            # 先停止后续 UI 分发，再原样传播控制信号，trace 核算已先行落账
            self._stopped = True
            raise
        except Exception:
            # 普通显示故障隔离在显示边界内，绝不冒充上传失败
            pass

    def _consume(self, event):
        """只处理 slot/upload 的 started 与终态，其余事件完全忽略。"""
        if event.stage != 'slot' or event.action != 'upload':
            return
        status = event.status
        if status == 'started':
            self._ensure_phase()
            return
        if status not in _TERMINAL_STATUSES:
            return
        identity = (event.batch_id, event.image_index, event.slot_index)
        if identity in self._identities:
            return
        # 身份先登记再尝试输出，输出异常与重复终态都不再重播
        self._identities.add(identity)
        if status in _SILENT_STATUSES:
            return
        self._emit(self._terminal_line(event))

    def _register(self, event):
        """UI 已停时仍登记终态身份，保证 trace 侧终态不被伪造。"""
        if event.stage != 'slot' or event.action != 'upload':
            return
        if event.status not in _TERMINAL_STATUSES:
            return
        self._identities.add(
            (event.batch_id, event.image_index, event.slot_index))

    def _enter_phase(self):
        """在所有者线程压入一个阶段帧，跨线程返回空标记不入栈。"""
        if threading.get_ident() != self._owner_thread_id:
            return None
        frame = _ActivePhase(None, None)
        self._phases.append(frame)
        return frame

    def _exit_phase(self, frame):
        """弹出阶段帧；最外层退出时关闭自己创建的全部资源。"""
        if frame is None:
            return
        if self._phases and self._phases[-1] is frame:
            self._phases.pop()
        elif frame in self._phases:
            self._phases.remove(frame)
        if self._phases:
            return
        self._close_active()

    def _close_active(self):
        """关闭活动阶段资源：借用句柄只恢复文案，自有 spinner 只关自己。"""
        active = self._active
        self._active = None
        if active is None:
            return
        try:
            active.stack.close()
        except (KeyboardInterrupt, SystemExit):
            self._stopped = True
            raise
        except Exception:
            pass

    def _ensure_phase(self):
        """首个实际 slot.started 且在活动 scope 内时懒启动一次阶段。"""
        if not self._phases or self._active is not None:
            return
        self._active = self._build_phase()

    def _build_phase(self):
        """按可用句柄选择借用、自有动画或普通行，并返回活动阶段。"""
        stack = contextlib.ExitStack()
        handle = self._select_handle()
        if handle is not None:
            stack.enter_context(temporary_text(handle, _PHASE_TEXT))
            return _ActivePhase(stack, handle)
        target = self._target_stream()
        if target.isatty():
            phase_handle = stack.enter_context(
                spinner_phase(_PHASE_TEXT, stream=target, report_exit=False))
            return _ActivePhase(stack, phase_handle)
        # 非 TTY 阶段最多一行普通行并 flush，无 ANSI/回车动画
        self._write_plain(target, _PHASE_TEXT)
        return _ActivePhase(stack, None)

    def _select_handle(self):
        """优先显式句柄，其次当前活动句柄，均须同线程未关闭且为 TTY。"""
        if self._spinner is not None:
            return self._spinner if self._borrowable(self._spinner) else None
        handle = current_spinner()
        if handle is None:
            return None
        return handle if self._borrowable(handle) else None

    def _borrowable(self, handle):
        """判断句柄能否被借用：同线程、未关闭、TTY，且流与显式流一致。

        借用需要句柄公开 ``closed``/``owner_thread_id``/``stream`` 三项属性；
        非 TTY 降级句柄 ``_LogSpinner`` 三者皆无，此处先行守卫判定不可借用，
        由调用方落到普通行分支，绝不因取属性抛异常而被上层静默吞掉。

        Args:
            handle: 待判定的候选句柄；可为 None 或任意对象。

        Returns:
            bool: 仅同线程、未关闭且 TTY 的句柄返回 True。
        """
        if handle is None:
            return False
        if not all(hasattr(handle, name) for name in _BORROW_ATTRS):
            return False
        if handle.closed:
            return False
        if handle.owner_thread_id != self._owner_thread_id:
            return False
        stream = handle.stream
        if self._stream is not None and stream is not self._stream:
            return False
        return bool(stream.isatty())

    def _emit(self, line):
        """有句柄时插入完整终态行，否则写选定流的普通行并 flush。"""
        active = self._active
        if active is not None and active.handle is not None:
            active.handle.write(line)
            return
        self._write_plain(self._target_stream(), line)

    def _write_plain(self, target, line):
        """写一行无 ANSI 的普通行并立即 flush。"""
        target.write(line + '\n')
        target.flush()

    def _target_stream(self):
        """返回显式反馈流，缺省时回退 sys.stdout。"""
        return sys.stdout if self._stream is None else self._stream

    def _terminal_line(self, event):
        """构造安全终态行：图标、通道名与按计划量派生的后缀。"""
        slot_index = event.slot_index if type(event.slot_index) is int else None
        name = upload_provider_name(event.provider, slot_index=slot_index)
        success = event.status == 'succeeded'
        icon = _ICON_DONE if success else _ICON_FAIL
        action = '上传成功' if success else '上传失败'
        line = f'{icon} 通道 [{name}] {action}'
        if (type(self._images_planned) is int and self._images_planned > 1
                and type(event.image_index) is int):
            line += f'（截图 {event.image_index}）'
        if slot_index is not None and self._provider_repeats(event.provider):
            line += f'（配置项 {slot_index + 1}）'
        return line

    def _provider_repeats(self, provider):
        """该 provider 在净化槽位序列中是否出现多于一次。"""
        canonical = canonical_upload_provider(provider)
        seen = 0
        for entry in self._slot_providers:
            if entry == canonical:
                seen += 1
                if seen > 1:
                    return True
        return False


def make_status_consumer(*, spinner=None, stream=None, images_planned=None,
                         slot_providers=()):
    """构造批次稳定的可调用上传消费者，只消费安全阶段事件。

    Args:
        spinner: 已存在的显式外层 TTY 句柄；缺省时复用当前活动句柄。
        stream: 显式反馈流；用于限制可借用句柄并承载非 TTY 普通行。
        images_planned: 编排已知的逻辑图片数；仅 >1 的整数才加截图后缀。
        slot_providers: 编排层净化后的 provider 序列；用于配置项后缀判定。

    Returns:
        _UploadStatusConsumer: 供 trace 与阶段作用域稳定复用的显示消费者。
    """
    return _UploadStatusConsumer(
        spinner=spinner, stream=stream, images_planned=images_planned,
        slot_providers=slot_providers)


@contextlib.contextmanager
def upload_feedback_scope(consumer):
    """为同一消费者绑定局部上传阶段，只绑定/解除，不换消费者。

    Args:
        consumer (_UploadStatusConsumer): 批次稳定消费者，非该类型不受支持。

    Yields:
        _UploadStatusConsumer: 传入的消费者，便于调用方使用。
    """
    frame = consumer._enter_phase()
    try:
        yield consumer
    finally:
        consumer._exit_phase(frame)
