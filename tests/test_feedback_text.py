#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证统一反馈的安全通道名称、失败原因与正文模板，不访问设备或网络。"""

import socket

import pytest

from modules import feedback_text
from modules.feedback_text import canonical_failure_code
from modules.feedback_text import canonical_upload_provider
from modules.feedback_text import format_failure_sentence
from modules.feedback_text import safe_reason_text
from modules.feedback_text import upload_provider_name
from modules.image_host.core import ImageHostError
from modules.screenshot.models import CaptureError
from modules.screenshot.models import capture_failure_message


SECRET_NAME = 'token=FAKE_SECRET'
SECRET_URL = 'https://user:pass@evil.example.com/a?token=FAKE_SECRET'


class _Hostile:
    """任何字符串化都立即失败的合成对象。"""

    def __str__(self):
        raise AssertionError('不得字符串化原始存储值')

    def __repr__(self):
        raise AssertionError('不得字符串化原始存储值')


@pytest.mark.parametrize(('storage_id', 'reason', 'expected'), [
    (7, 'invalid_manual',
     '预设的 options.storage_id 格式无效，本次已自动选择储存编号：7，'
     '未修改配置文件。'),
    (23, 'invalid_manual',
     '预设的 options.storage_id 格式无效，本次已自动选择储存编号：23，'
     '未修改配置文件。'),
    (23, 'rejected_manual',
     '预设的 options.storage_id 被图床拒绝，'
     '本次改用分组列表中的储存编号：23 重试，未修改配置文件。'),
])
def test_storage_notice_uses_selected_positive_integer(
        storage_id, reason, expected):
    """提示只接受已校验正整数，并精确匹配两种固定模板。"""
    assert feedback_text.format_storage_selection_notice(
        storage_id, reason=reason) == expected


@pytest.mark.parametrize('storage_id', [
    True, False, 0, -1, '7', 7.0, None, [], {}, _Hostile(),
])
def test_storage_notice_rejects_untrusted_values(storage_id):
    """非正整数与非 int 对象一律不产出提示，也不转换原对象。"""
    assert feedback_text.format_storage_selection_notice(
        storage_id, reason='invalid_manual') == ''
    assert feedback_text.format_storage_selection_notice(
        storage_id, reason='rejected_manual') == ''


@pytest.mark.parametrize('reason', ['unknown', '', None, 1, _Hostile()])
def test_storage_notice_rejects_unknown_reason(reason):
    """未知原因不猜测文案。"""
    assert feedback_text.format_storage_selection_notice(
        7, reason=reason) == ''


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch):
    """纯函数测试不得建立任何真实网络连接。"""

    def _blocked(*args, **kwargs):
        raise AssertionError('本测试禁止真实网络连接')

    monkeypatch.setattr(socket, 'create_connection', _blocked)
    monkeypatch.setattr(socket.socket, 'connect', _blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', _blocked)


@pytest.mark.parametrize(('provider', 'canonical', 'expected'), [
    ('beeimg_cn', 'beeimg_cn', 'BeeIMG.CN'),
    ('beeimg', 'beeimg', 'BeeIMG'),
    ('catbox', 'catbox', 'Catbox'),
    ('wmimg', 'wmimg', 'WMImg'),
    ('boltp', 'boltp', 'Boltp'),
    ('superbed', 'superbed', 'SuperBed'),
    ('配置', '配置', '配置'),
])
def test_provider_names(provider, canonical, expected):
    """六个内部键与共享配置错误映射为固定展示名。"""
    assert canonical_upload_provider(provider) == canonical
    assert upload_provider_name(provider) == expected


def test_shared_provider_constants():
    """共享配置与未知 provider 常量公开，且与归一函数返回值一致。"""
    assert feedback_text.SHARED_CONFIG_PROVIDER == '配置'
    assert feedback_text.UNKNOWN_UPLOAD_PROVIDER == 'unknown'
    assert (feedback_text.canonical_upload_provider('配置')
            == feedback_text.SHARED_CONFIG_PROVIDER)
    assert (feedback_text.canonical_upload_provider('unregistered')
            == feedback_text.UNKNOWN_UPLOAD_PROVIDER)


@pytest.mark.parametrize('provider', [
    SECRET_NAME,
    SECRET_URL,
    'evil.example.com',
    'unknown_provider',
    ['catbox'],
    {'provider': 'beeimg_cn'},
    None,
    7,
])
def test_unknown_provider_never_echoes_input(provider):
    """未知或非字符串 provider 不回显原文，仅显示未知图床。"""
    assert canonical_upload_provider(provider) == 'unknown'
    name = upload_provider_name(provider)
    assert name == '未知图床'
    for leak in (SECRET_NAME, SECRET_URL, 'evil.example.com', 'FAKE_SECRET'):
        assert leak not in name


def test_unknown_provider_slot_suffix():
    """未知 provider 带整数槽位时补安全序号，序号为索引加一。"""
    name = upload_provider_name(SECRET_NAME, slot_index=2)
    assert name == '未知图床 第 3 项'
    assert SECRET_NAME not in name
    assert upload_provider_name(SECRET_NAME, slot_index=0) == '未知图床 第 1 项'


@pytest.mark.parametrize(('category', 'code', 'expected'), [
    ('upload', 'malicious', 'upload_failed'),
    ('upload', 'profile_forbidden', 'profile_forbidden'),
    ('upload', 123, 'upload_failed'),
    ('upload', ['upload_failed'], 'upload_failed'),
    ('capture', 'window_not_found', 'window_not_found'),
    ('capture', 'adb_timeout', 'adb_timeout'),
    ('capture', 'malicious', 'capture_failed'),
    ('capture', 'upload_failed', 'capture_failed'),
    ('config', 'no_targets', 'no_targets'),
    ('config', 'target_invalid', 'target_invalid'),
    ('config', 'invalid_hosts', 'invalid_hosts'),
    ('config', 'malicious', 'config_error'),
    ('config', 'window_not_found', 'config_error'),
])
def test_failure_namespaces(category, code, expected):
    """三个命名空间各自白名单归一，未知固定到本类别兜底码。"""
    assert canonical_failure_code(category, code) == expected


@pytest.mark.parametrize(('category', 'code', 'expected'), [
    ('upload', 'upload_failed', '图床上传失败'),
    ('upload', 'malicious', '图床上传失败'),
    ('upload', 'profile_forbidden',
     '账号资料接口无访问权限，请为令牌授权资料读取权限，'
     '或填写该账号可用的 options.storage_id。'),
    ('upload', 'profile_forbidden_invalid_storage',
     '手填储存驱动无效，请授权资料读取权限或填写当前账号可用的 '
     'options.storage_id。'),
    ('capture', 'window_not_found', '未找到匹配的窗口'),
    ('capture', 'malicious', '截图失败'),
    ('config', 'no_targets', '未配置有效截图目标'),
    ('config', 'target_invalid', '截图目标配置无效'),
    ('config', 'invalid_hosts', '图床配置必须为列表'),
    ('config', 'malicious', '图床配置无效'),
])
def test_safe_reason_text(category, code, expected):
    """原因文案不含类别前缀，权限原因去掉旧 BeeIMG.cn 前缀。"""
    reason = safe_reason_text(category, code)
    assert reason == expected
    assert 'BeeIMG.cn' not in reason


def test_permission_sentence_exact():
    """beeimg_cn 权限句逐字固定，无半角拼写，无双句号。"""
    sentence = format_failure_sentence('upload', 'beeimg_cn', 'profile_forbidden')
    assert sentence == (
        '截图上传失败：通道 BeeIMG.CN 的账号资料接口无访问权限，'
        '请为令牌授权资料读取权限，或填写该账号可用的 options.storage_id。')
    assert 'BeeIMG.cn' not in sentence
    assert '。。' not in sentence
    assert sentence.count('。') == 1


def test_invalid_storage_is_distinct():
    """手填储存驱动无效是独立原因，不折叠为普通权限句。"""
    forbidden = format_failure_sentence('upload', 'beeimg_cn', 'profile_forbidden')
    invalid_storage = format_failure_sentence(
        'upload', 'beeimg_cn', 'profile_forbidden_invalid_storage')
    assert invalid_storage == (
        '截图上传失败：通道 BeeIMG.CN 手填储存驱动无效，'
        '请授权资料读取权限或填写当前账号可用的 options.storage_id。')
    assert invalid_storage != forbidden
    assert invalid_storage.count('。') == 1
    assert 'BeeIMG.cn' not in invalid_storage


def test_general_upload_sentence():
    """一般上传失败使用固定原因，并以单个句号收尾。"""
    assert format_failure_sentence('upload', 'catbox', 'upload_failed') == (
        '截图上传失败：通道 Catbox 图床上传失败。')
    sentence = format_failure_sentence('upload', 'catbox', 'profile_forbidden')
    assert sentence.startswith('截图上传失败：通道 Catbox ')
    assert 'BeeIMG.cn' not in sentence
    assert '。。' not in sentence
    assert sentence.count('。') == 1


@pytest.mark.parametrize('provider', [SECRET_NAME, SECRET_URL])
def test_unknown_failure_sentence_hides_dynamic_input(provider):
    """未知 provider 的整句不得包含动态原文、URL 或合成秘密。"""
    sentence = format_failure_sentence('upload', provider, 'malicious')
    assert sentence == '截图上传失败：通道 未知图床 图床上传失败。'
    for leak in (SECRET_NAME, SECRET_URL, 'evil.example.com', 'FAKE_SECRET'):
        assert leak not in sentence


@pytest.mark.parametrize(('code', 'expected'), [
    ('no_targets', '截图配置失败：未配置有效截图目标'),
    ('target_invalid', '截图配置失败：截图目标配置无效'),
    ('invalid_hosts', '截图配置失败：图床配置必须为列表'),
    ('malicious', '截图配置失败：图床配置无效'),
])
def test_config_sentences(code, expected):
    """配置失败使用独立前缀，不冒充任何图床上传失败。"""
    sentence = format_failure_sentence('config', '配置', code)
    assert sentence == expected
    assert '上传失败' not in sentence


@pytest.mark.parametrize(('code', 'expected'), [
    ('window_not_found', '截图失败：未找到匹配的窗口'),
    ('malicious', '截图失败'),
])
def test_capture_sentences(code, expected):
    """截图失败复用既有安全映射，未知代码只显示类别。"""
    assert format_failure_sentence('capture', '配置', code) == expected


NOTIFY_CAPTURE_OVERRIDES = (
    ('dpi_failed', '无法设置局部线程 DPI'),
    ('invalid_serial', 'ADB 设备标识无效'),
)


@pytest.mark.parametrize(('code', 'reason'), NOTIFY_CAPTURE_OVERRIDES)
def test_notification_capture_reason_overrides(code, reason):
    """通知反馈层对两个捕获码使用更准确的固定原因。"""
    assert safe_reason_text('capture', code) == reason
    assert format_failure_sentence('capture', '', code) == '截图失败：' + reason


NOTIFY_STORAGE_REASONS = (
    ('storage_lookup_failed', 'storage_id 查询失败'),
    ('storage_unavailable', 'storage_id 不可用'),
)


@pytest.mark.parametrize(('code', 'reason'), NOTIFY_STORAGE_REASONS)
def test_notification_storage_reason_overrides(code, reason):
    """通知反馈层用 storage_id 语义展示两类 storage 失败。"""
    assert safe_reason_text('upload', code) == reason
    assert format_failure_sentence('upload', 'catbox', code) == (
        '截图上传失败：通道 Catbox ' + reason + '。')


def test_storage_override_does_not_change_backend_mapping():
    """后端与 CLI 仍使用原有 storage 文案，覆盖只作用于通知反馈层。"""
    assert ImageHostError('storage_lookup_failed', '').message == '储存驱动查询失败'
    assert ImageHostError('storage_unavailable', '').message == '储存驱动不可用'


def test_notification_override_does_not_change_backend_mapping():
    """后端与 CLI 仍使用原有捕获文案，覆盖只作用于通知反馈层。"""
    assert capture_failure_message(
        CaptureError('dpi_failed', '')) == '截图失败：无法设置局部线程 DPI 上下文'
    assert capture_failure_message(
        CaptureError('invalid_serial', '')) == '截图失败：ADB 序列号无效'


CONFIG_CODE_REASONS = (
    ('invalid_host', '图床项必须为映射'),
    ('invalid_hosts', '图床配置必须为列表'),
    ('invalid_input', '图片数据或文件名无效'),
    ('invalid_environment_reference', '图床凭证环境引用格式无效'),
    ('invalid_options', '图床参数无效或不受该图床支持'),
    ('invalid_provider', '图床名称无效'),
    ('invalid_token', '图床凭证必须为字符串'),
    ('missing_environment', '图床凭证环境变量缺失或为空'),
    ('no_targets', '未配置有效截图目标'),
    ('not_configured', '未配置图床'),
    ('target_invalid', '截图目标配置无效'),
    ('unknown_provider', '图床尚未注册'),
    ('config_error', '图床配置无效'),
)


@pytest.mark.parametrize(('code', 'reason'), CONFIG_CODE_REASONS)
def test_each_config_code_has_config_sentence(code, reason):
    """每个配置码都给出类别前缀加固定安全原因。"""
    assert safe_reason_text('config', code) == reason
    assert format_failure_sentence('config', '配置', code) == '截图配置失败：' + reason


@pytest.mark.parametrize('code', ['malicious', '', None, 1, ['x'], {'k': 'v'}])
def test_unknown_config_code_uses_fixed_fallback(code):
    """未知配置码严格使用本类别兜底，不回显原值。"""
    assert format_failure_sentence('config', '配置', code) == (
        '截图配置失败：图床配置无效')


UPLOAD_CODE_REASONS = (
    ('transport_failed', '图床网络传输失败'),
    ('http_failed', '图床 HTTP 请求失败'),
    ('business_rejected', '图床拒绝上传'),
    ('invalid_response', '图床响应无效'),
    ('invalid_url', '图床返回的图片链接无效'),
    ('storage_lookup_failed', 'storage_id 查询失败'),
    ('storage_unavailable', 'storage_id 不可用'),
    ('upload_failed', '图床上传失败'),
)


@pytest.mark.parametrize(('code', 'reason'), UPLOAD_CODE_REASONS)
def test_each_upload_code_has_sentence(code, reason):
    """每个上传码都给出固定原因与单句号完整上传失败句。"""
    assert safe_reason_text('upload', code) == reason
    assert format_failure_sentence('upload', 'catbox', code) == (
        '截图上传失败：通道 Catbox ' + reason + '。')


def test_upload_code_table_covers_backend_codes():
    """字面量表覆盖后端全部非配置、非历史权限上传码。"""
    from modules.image_host.core import _ERROR_MESSAGES
    expected = set(_ERROR_MESSAGES) - set(feedback_text._CONFIG_CODES) - {
        'profile_forbidden', 'profile_forbidden_invalid_storage'}
    assert {code for code, _ in UPLOAD_CODE_REASONS} == expected


CAPTURE_CODE_REASONS = (
    ('invalid_source', '截图来源必须为 window 或 adb'),
    ('invalid_title', '窗口标题或匹配表达式无效'),
    ('window_not_found', '未找到匹配的窗口'),
    ('window_ambiguous', '存在多个匹配的窗口'),
    ('window_lookup_failed', '无法枚举目标窗口'),
    ('window_gone', '目标窗口已失效或标题已变化'),
    ('window_state_failed', '无法读取或恢复目标窗口状态'),
    ('invalid_dimensions', '目标窗口尺寸无效'),
    ('dpi_failed', '无法设置局部线程 DPI'),
    ('gdi_failed', '无法分配或访问窗口图像资源'),
    ('print_failed', '窗口图像绘制失败'),
    ('image_failed', '无法构造有效窗口图像'),
    ('encode_failed', '窗口图像编码失败'),
    ('invalid_serial', 'ADB 设备标识无效'),
    ('adb_version_unsupported', 'ADB 依赖版本未经支持验证'),
    ('adb_unavailable', '无法连接已有本机 ADB Server'),
    ('adb_not_found', '未找到指定 ADB 设备'),
    ('adb_offline', '指定 ADB 设备处于离线状态'),
    ('adb_unauthorized', '指定 ADB 设备尚未授权'),
    ('adb_timeout', 'ADB 连接或读写等待超时'),
    ('adb_protocol_failed', 'ADB 设备通信失败'),
    ('adb_image_failed', 'ADB 未返回完整有效的图像数据'),
    ('adb_encode_failed', 'ADB 图像编码失败'),
)


@pytest.mark.parametrize(('code', 'reason'), CAPTURE_CODE_REASONS)
def test_each_capture_code_has_sentence(code, reason):
    """23 个捕获码各自给出固定原因与单前缀完整句。"""
    assert safe_reason_text('capture', code) == reason
    assert format_failure_sentence('capture', '', code) == '截图失败：' + reason


def test_capture_code_table_is_complete():
    """字面量表键集合必须与捕获表完全一致且恰好 23 项。"""
    from modules.screenshot.models import _CAPTURE_MESSAGES
    assert len(CAPTURE_CODE_REASONS) == 23
    assert {code for code, _ in CAPTURE_CODE_REASONS} == set(_CAPTURE_MESSAGES)
