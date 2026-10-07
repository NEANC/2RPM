#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""定义与截图后端无关的截图结果。"""

from dataclasses import dataclass


_CAPTURE_MESSAGES = {
    'invalid_source': '截图来源必须为 window 或 adb',
    'invalid_title': '窗口标题必须为非空字符串',
    'window_not_found': '未找到完整标题匹配的窗口',
    'window_ambiguous': '存在多个完整标题匹配的窗口',
    'window_lookup_failed': '无法枚举目标窗口',
    'window_gone': '目标窗口已失效或标题已变化',
    'window_state_failed': '无法读取或恢复目标窗口状态',
    'invalid_dimensions': '目标窗口尺寸无效',
    'dpi_failed': '无法设置局部线程 DPI 上下文',
    'gdi_failed': '无法分配或访问窗口图像资源',
    'print_failed': '窗口图像绘制失败',
    'image_failed': '无法构造有效窗口图像',
    'encode_failed': '窗口图像编码失败',
    'invalid_serial': 'ADB 序列号必须为非空字符串',
    'adb_version_unsupported': 'ADB 依赖版本未经支持验证',
    'adb_unavailable': '无法连接已有本机 ADB Server',
    'adb_not_found': '未找到指定 ADB 设备',
    'adb_offline': '指定 ADB 设备处于离线状态',
    'adb_unauthorized': '指定 ADB 设备尚未授权',
    'adb_timeout': 'ADB 连接或读写等待超时',
    'adb_protocol_failed': 'ADB 设备通信失败',
    'adb_image_failed': 'ADB 未返回完整有效的图像数据',
    'adb_encode_failed': 'ADB 图像编码失败',
}


def capture_failure_message(error):
    """仅按可信字符串分类重建提示，不展示异常消息或未知代码。"""
    message = (
        _CAPTURE_MESSAGES.get(error.code)
        if type(error.code) is str else None
    )
    return f'截图失败：{message}' if message else '截图失败'


class CaptureError(Exception):
    """保存截图失败分类和固定安全消息。"""

    def __init__(self, code, message):
        """记录分类，不附加底层异常或用户敏感数据。"""
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class CaptureResult:
    """保存一次截图的实际载荷与不可变元数据。"""

    image_bytes: bytes
    source: str
    target: str
    width: int
    height: int
    warnings: tuple[str, ...] = ()
    image_format: str = 'jpeg'
