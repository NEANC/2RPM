#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""把安全上传阶段事件显示为终端进度，不参与任何文件记录或业务判定。"""

import sys

from modules.image_host.core import ImageHostError
from modules.spinner import current_spinner


# 已裁决为可行动的固定原因，终端须呈现固定中文提示而非裸代码
_ACTIONABLE_REASONS = frozenset({
    'profile_forbidden', 'profile_forbidden_invalid_storage',
})


def _failure_text(reason_code):
    """已裁决可行动原因渲染固定中文提示，其余沿用裸代码。"""
    if reason_code in _ACTIONABLE_REASONS:
        return ImageHostError(reason_code, '').message
    return reason_code


def _slot_text(event):
    """按图片序号与配置槽位序号构造正在上传的安全文案。"""
    slot = '' if event.slot_index is None else f' / 图床 {event.slot_index + 1}'
    provider = f' {event.provider}' if event.provider else ''
    return f'截图 {event.image_index}{slot}{provider}：正在上传'


def make_status_consumer(*, spinner=None, stream=None):
    """构造终端状态消费者，只消费安全阶段事件且不改变文件记录。

    Args:
        spinner: 已存在的外层 spinner 句柄；缺省时复用当前活动 TTY 实例。
        stream: 非 TTY 普通行的输出流；缺省使用 sys.stdout。

    Returns:
        Callable[[StageEvent], None]: 消费单个安全阶段事件的显示回调。
    """
    handle = spinner if spinner is not None else current_spinner()
    prepared = set()
    failovers = set()

    def show(message):
        """优先复用外层 spinner 的安全写入，否则输出无 ANSI 的普通行。"""
        if handle is not None:
            handle.write(message)
            return
        target = sys.stdout if stream is None else stream
        target.write(message + '\n')

    def consume(event):
        """只消费准备上传、上传槽位开始/终态与图床故障转移。"""
        if event.stage == 'slot' and event.action == 'upload':
            if event.status == 'started':
                # 每张逻辑图片只提示一次进入上传编排，后续只报站点尝试
                if event.image_index not in prepared:
                    prepared.add(event.image_index)
                    show(f'截图 {event.image_index}：准备上传')
                show(_slot_text(event))
            elif event.status == 'succeeded':
                show('上传成功')
            elif event.status == 'failed':
                show('上传失败：' + _failure_text(event.reason_code)
                     if event.reason_code else '上传失败')
        elif (event.stage == 'fallback' and event.fallback_kind == 'host_failover'
              and event.status == 'started'):
            key = (event.batch_id, event.operation_id, event.image_index,
                   event.slot_index, event.reason_code)
            if key in failovers:
                return
            failovers.add(key)
            show('上传失败，尝试备用域名' if event.reason_code == 'domain_failed'
                 else '上传失败，尝试下一图床')

    return consume
