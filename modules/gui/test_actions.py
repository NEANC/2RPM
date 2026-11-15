#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""隔离 worker 的三种单次测试动作。"""

from copy import deepcopy
from pathlib import Path
import re
from urllib.parse import unquote

from modules.gui.protocol import ChannelTestResult, TestRequest, TestResult
from modules.image_host.core import ImageHostError, validate_image_url
from modules.image_host.registry import upload_with_fallback
from modules.notification import send_notification
from modules.screenshot.models import CaptureError, capture_failure_message
from modules.screenshot.retention import FORMAT_SUFFIX
from modules.screenshot.service import capture
from modules.screenshot.targets import allocate_targets
from modules.utils import parse_push_channels


_NOT_SENT = '请先添加推送通道'
_TEST_TITLE = '2RPM推送测试'
_TEST_CONTENT = '这是一个测试，当你收到时表明一切正常。'


def prepare_notification_snapshot(config: dict, template_key: str) -> dict:
    """复制配置并强制固定测试模板，杜绝事件截图和模板影响。"""
    snapshot = deepcopy(config)
    snapshot['push'].setdefault('templates', {})[template_key] = {
        'enable': True, 'capture_screenshot': False,
        'title': _TEST_TITLE, 'content': _TEST_CONTENT}
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
    if request.capture_provider is not None:
        provider, target_value = request.capture_provider, request.capture_target
    else:
        section = request.config['push']['screenshot']
        targets, _ = allocate_targets(section.get('targets'))
        target = targets[request.target_index]
        if target['error']:
            return 'failure', '截图目标配置无效', ()
        provider, target_value = target['provider'], target['target']
    try:
        image = capture(provider, target_value,
                        image_format='jpeg', purpose='cli')
        path = Path(request.session_dir) / (
            request.request_id + FORMAT_SUFFIX[image.image_format])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image.image_bytes)
    except CaptureError as error:
        return 'failure', capture_failure_message(error), ()
    return 'success', '', (str(path),)


def _upload_image(request: TestRequest) -> tuple[str, str, tuple[str, ...], str]:
    """只将指定本地文件上传到选中图床。"""
    path = Path(request.image_path)
    host = request.config['push']['screenshot']['image_host'][request.host_index]
    result = upload_with_fallback(path.read_bytes(), path.name, [host])
    if result.success:
        try:
            url = validate_image_url(result.url)
            secrets = [value for key, value in host.items()
                       if key.lower().replace('_', '').replace('-', '') in
                       {'token', 'apikey', 'key', 'secret', 'password', 'accesskey'}
                       and type(value) is str and value]
            decoded = url
            while True:
                if any(secret in decoded for secret in secrets):
                    return 'failure', '图床回调地址无效', (), ''
                next_decoded = unquote(decoded)
                if next_decoded == decoded:
                    break
                decoded = next_decoded
            return 'success', '', (), url
        except ImageHostError:
            return 'failure', '图床回调地址无效', (), ''
    return 'failure', '图床上传失败', (), ''


def _notify(request: TestRequest) -> tuple[str, str, tuple[ChannelTestResult, ...]]:
    """每个原始通道独立发送固定消息并收集有序安全结果。"""
    snapshot = prepare_notification_snapshot(request.config, request.template_key)
    channels = snapshot['push'].get('push_channel_settings', {}).get('channels') or []
    if not isinstance(channels, list) or not channels:
        return 'failure', _NOT_SENT, ()
    records = []
    for index, channel in enumerate(channels):
        parsed = parse_push_channels(channel) if isinstance(channel, dict) else []
        provider = parsed[0].get('provider') if parsed else None
        safe_provider = (provider if isinstance(provider, str)
                         and re.fullmatch(r'[A-Za-z0-9_-]+', provider)
                         else 'unknown')
        if safe_provider == 'unknown':
            records.append(ChannelTestResult(index, safe_provider, 'failure', '通道配置无效'))
            continue
        snapshot['push']['push_channel_settings']['channels'] = [channel]
        try:
            sent = send_notification(snapshot, request.template_key)
            ok = bool(sent) and all(success for _, success in sent)
        except Exception:
            ok = False
        records.append(ChannelTestResult(index, safe_provider,
                                         'success' if ok else 'failure',
                                         '' if ok else '发送失败'))
    status = summarize_notification([(item.provider, item.status == 'success')
                                     for item in records])
    return status, '通知发送失败' if status == 'failure' else '', tuple(records)


def run_test(request: TestRequest) -> TestResult:
    """执行单次测试并只回传经筛选的真实结果。"""
    upload_url = ''
    channels = ()
    try:
        if request.action == 'capture':
            status, reason, previews = _capture_preview(request)
        elif request.action == 'upload':
            status, reason, previews, upload_url = _upload_image(request)
        elif request.action == 'notify':
            status, reason, channels = _notify(request)
            previews = ()
        else:
            status, reason, previews = 'failure', '未知测试动作', ()
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        status, reason, previews = 'failure', '测试执行失败', ()
    return TestResult(request.request_id, request.document_generation,
                      request.revision, status, reason, previews,
                      upload_url, channels)
