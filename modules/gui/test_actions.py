#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""隔离 worker 的三种单次测试动作。"""

from copy import deepcopy
from pathlib import Path
import re

from modules.gui.forms import event_variables
from modules.gui.protocol import TestRequest, TestResult
from modules.image_host.registry import upload_with_fallback
from modules.notification import send_notification
from modules.screenshot.models import CaptureError, capture_failure_message
from modules.screenshot.retention import FORMAT_SUFFIX
from modules.screenshot.service import capture
from modules.screenshot.targets import allocate_targets


_NOT_SENT = '未发送：事件禁用、无有效通道或模板无效'


def prepare_notification_snapshot(config: dict, template_key: str) -> dict:
    """复制配置并仅关闭选中事件的截图，不更改事件启用状态。"""
    snapshot = deepcopy(config)
    snapshot['push']['templates'][template_key]['capture_screenshot'] = False
    return snapshot


def summarize_notification(results: list[tuple[str, bool]]) -> str:
    """将空结果与通道结果转换为发送终态。"""
    if not results or not any(ok for _, ok in results):
        return 'failure'
    if all(ok for _, ok in results):
        return 'success'
    return 'partial'


def _capture_preview(request: TestRequest) -> tuple[str, str, tuple[str, ...]]:
    """截取选中的单个目标并写入测试会话目录。"""
    if not re.fullmatch(r'[A-Za-z0-9_-]+', request.request_id):
        return 'failure', '测试执行失败', ()
    section = request.config['push']['screenshot']
    targets, _ = allocate_targets(section.get('targets'))
    target = targets[request.target_index]
    if target['error']:
        return 'failure', '截图目标配置无效', ()
    try:
        image = capture(target['provider'], target['target'],
                        image_format='jpeg', purpose='cli')
        path = Path(request.session_dir) / (
            request.request_id + FORMAT_SUFFIX[image.image_format])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image.image_bytes)
    except CaptureError as error:
        return 'failure', capture_failure_message(error), ()
    return 'success', '', (str(path),)


def _upload_image(request: TestRequest) -> tuple[str, str, tuple[str, ...]]:
    """只将指定本地文件上传到选中图床。"""
    path = Path(request.image_path)
    host = request.config['push']['screenshot']['image_host'][request.host_index]
    result = upload_with_fallback(path.read_bytes(), path.name, [host])
    if result.success:
        return 'success', '', ()
    return 'failure', '图床上传失败', ()


def _notify(request: TestRequest) -> tuple[str, str, tuple[str, ...]]:
    """以禁图配置及模拟事件变量调用一次现有通知发送。"""
    snapshot = prepare_notification_snapshot(request.config, request.template_key)
    results = send_notification(snapshot, request.template_key,
                                **event_variables(request.template_key))
    status = summarize_notification(results)
    return status, _NOT_SENT if not results else (
        '通知发送失败' if status == 'failure' else ''), ()


def run_test(request: TestRequest) -> TestResult:
    """执行一个测试动作，仅回传三元标识与固定安全结果。"""
    try:
        if request.action == 'capture':
            status, reason, previews = _capture_preview(request)
        elif request.action == 'upload':
            status, reason, previews = _upload_image(request)
        elif request.action == 'notify':
            status, reason, previews = _notify(request)
        else:
            status, reason, previews = 'failure', '未知测试动作', ()
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        status, reason, previews = 'failure', '测试执行失败', ()
    return TestResult(request.request_id, request.document_generation,
                      request.revision, status, reason, previews)
