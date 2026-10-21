#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""提供无副作用的安全通道名称、失败原因与失败正文模板。

本模块为纯函数集合：只使用静态映射与既有安全错误规范化，不读取配置、
不写日志或 UI、不进行任何 IO，也不持有全局去重状态。
"""

from modules.image_host.core import ImageHostError
from modules.screenshot.models import CaptureError
from modules.screenshot.models import _CAPTURE_MESSAGES
from modules.screenshot.models import capture_failure_message


_KNOWN_PROVIDERS = frozenset({
    'beeimg', 'beeimg_cn', 'boltp', 'catbox', 'superbed', 'wmimg',
})
_DISPLAY_NAMES = {
    'beeimg': 'BeeIMG',
    'beeimg_cn': 'BeeIMG.CN',
    'boltp': 'Boltp',
    'catbox': 'Catbox',
    'superbed': 'SuperBed',
    'wmimg': 'WMImg',
}
SHARED_CONFIG_PROVIDER = '配置'
UNKNOWN_UPLOAD_PROVIDER = 'unknown'
_UNKNOWN_NAME = '未知图床'
_CAPTURE_FALLBACK = 'capture_failed'
_CONFIG_CODES = frozenset({
    'config_error', 'invalid_hosts', 'invalid_input', 'no_targets',
    'not_configured', 'target_invalid',
})
_CONFIG_REASONS = {
    'no_targets': '未配置有效截图目标',
    'target_invalid': '截图目标配置无效',
}
_BEEIMG_CN_PREFIX = 'BeeIMG.cn '
_STORAGE_SELECTION_NOTICES = {
    'invalid_manual': (
        '预设的 options.storage_id 格式无效，'
        '本次已自动选择储存编号：{storage_id}，未修改配置文件。'
    ),
    'rejected_manual': (
        '预设的 options.storage_id 被图床拒绝，'
        '本次改用分组列表中的储存编号：{storage_id} 重试，未修改配置文件。'
    ),
}
_BEEIMG_CN_SENTENCES = {
    'profile_forbidden': (
        '截图上传失败：通道 BeeIMG.CN 的账号资料接口无访问权限，'
        '请为令牌授权资料读取权限，或填写该账号可用的 options.storage_id。'
    ),
    'profile_forbidden_invalid_storage': (
        '截图上传失败：通道 BeeIMG.CN 手填储存驱动无效，'
        '请授权资料读取权限或填写当前账号可用的 options.storage_id。'
    ),
}


def canonical_upload_provider(provider: object) -> str:
    """将上传 provider 归一为六个内部键或共享/未知固定值。

    参数:
        provider: 待归一的原始 provider 值，可能为任意对象。

    返回:
        已知内部键、共享配置值 ``配置``，其余一律 ``unknown``。
    """
    if type(provider) is not str:
        return UNKNOWN_UPLOAD_PROVIDER
    if provider in _KNOWN_PROVIDERS:
        return provider
    if provider == SHARED_CONFIG_PROVIDER:
        return SHARED_CONFIG_PROVIDER
    return UNKNOWN_UPLOAD_PROVIDER


def upload_provider_name(provider: object, *, slot_index: int | None = None) -> str:
    """按静态映射返回安全展示名，未知时可补安全槽位序号。

    参数:
        provider: 原始 provider 值。
        slot_index: 槽位索引；仅整数时补 ``第 (slot_index + 1) 项``。

    返回:
        已知 provider 的固定展示名、``配置`` 或 ``未知图床``；
        未知带整数槽位时为 ``未知图床 第 N 项``，绝不回显原始名称。
    """
    canonical = canonical_upload_provider(provider)
    if canonical != UNKNOWN_UPLOAD_PROVIDER:
        return _DISPLAY_NAMES.get(canonical, canonical)
    if type(slot_index) is int:
        return f'{_UNKNOWN_NAME} 第 {slot_index + 1} 项'
    return _UNKNOWN_NAME


def canonical_failure_code(category: str, code: object) -> str:
    """按类别白名单归一失败码，未知固定到本类别兜底码。

    参数:
        category: 失败类别，取值 ``upload`` / ``capture`` / ``config``。
        code: 待归一的原始失败码。

    返回:
        upload 经 ImageHostError 规范化；capture/config 各自命名空间白名单，
        未知分别固定为 ``capture_failed`` / ``config_error``，不跨类别归一。
    """
    if category == 'capture':
        if type(code) is str and code in _CAPTURE_MESSAGES:
            return code
        return _CAPTURE_FALLBACK
    if category == 'config':
        if type(code) is str and code in _CONFIG_CODES:
            return code
        return 'config_error'
    return ImageHostError(code, '').code


def safe_reason_text(category: str, code: object) -> str:
    """返回不含类别前缀的固定安全原因。

    参数:
        category: 失败类别。
        code: 原始失败码。

    返回:
        白名单内的固定原因；capture 未知为 ``截图失败``，
        config/upload 未知回落到本类别兜底原因。
        upload 权限原因去掉旧 ``BeeIMG.cn`` 前缀；
        BeeIMG.CN 完整权限句由 :func:`format_failure_sentence` 负责。
    """
    canonical = canonical_failure_code(category, code)
    if category == 'capture':
        return _CAPTURE_MESSAGES.get(canonical, '截图失败')
    if category == 'config':
        fixed = _CONFIG_REASONS.get(canonical)
        if fixed is not None:
            return fixed
        return ImageHostError(canonical, '').message
    reason = ImageHostError(canonical, '').message
    if reason.startswith(_BEEIMG_CN_PREFIX):
        return reason[len(_BEEIMG_CN_PREFIX):]
    return reason


def format_failure_sentence(category: str, provider: str, code: str) -> str:
    """构建带类别前缀且只保留单个句号的完整安全失败句。

    参数:
        category: 失败类别。
        provider: 原始 provider；capture/config 不使用该值。
        code: 原始失败码。

    返回:
        capture 复用既有安全映射；config 使用 ``截图配置失败：`` 前缀；
        upload 使用 ``截图上传失败：通道 {名称} {固定原因}。``，
        BeeIMG.CN 权限类使用固定完整模板，未知 provider 不猜最后一个通道。
    """
    canonical = canonical_failure_code(category, code)
    if category == 'capture':
        return capture_failure_message(CaptureError(canonical, ''))
    if category == 'config':
        return f'截图配置失败：{safe_reason_text("config", canonical)}'
    if canonical_upload_provider(provider) == 'beeimg_cn':
        fixed = _BEEIMG_CN_SENTENCES.get(canonical)
        if fixed is not None:
            return fixed
    name = upload_provider_name(provider)
    reason = safe_reason_text('upload', canonical).rstrip('。')
    return f'截图上传失败：通道 {name} {reason}。'


def format_storage_selection_notice(storage_id: object, *, reason: str) -> str:
    """构建手填储存编号被替代时的固定提示。

    参数:
        storage_id: 本次上传准备实际选中的编号。
        reason: ``invalid_manual`` 或 ``rejected_manual``。

    返回:
        str: 含已校验正整数的完整提示；编号不是正整数或原因未知时为空。
        不转换原始对象，也不回显旧手填值或响应内容。
    """
    if type(reason) is not str:
        return ''
    template = _STORAGE_SELECTION_NOTICES.get(reason)
    if template is None:
        return ''
    # 守卫：只有已校验的正整数可以进入提示文本
    if type(storage_id) is not int or storage_id <= 0:
        return ''
    return template.format(storage_id=storage_id)
