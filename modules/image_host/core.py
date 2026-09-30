#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""定义上传结果、凭证解析及图片链接的安全边界。"""

from dataclasses import dataclass
from dataclasses import field
import ipaddress
import os
import re
import unicodedata
from urllib.parse import urlsplit


_ENV_REFERENCE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')
_HOST_LABEL = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?')
_ERROR_MESSAGES = {
    'invalid_input': '图片数据或文件名无效',
    'not_configured': '未配置图床',
    'invalid_hosts': '图床配置必须为列表',
    'invalid_host': '图床项必须为映射',
    'invalid_provider': '图床名称无效',
    'unknown_provider': '图床尚未注册',
    'invalid_token': '图床凭证必须为字符串',
    'invalid_environment_reference': '图床凭证环境引用格式无效',
    'missing_environment': '图床凭证环境变量缺失或为空',
    'invalid_options': '图床参数无效或不受该图床支持',
    'invalid_url': '图床返回的图片链接无效',
    'upload_failed': '图床上传失败',
}


@dataclass(frozen=True)
class UploadFailure:
    """保存图床标识及不包含敏感原文的失败类别和摘要。"""

    provider: str
    code: str
    message: str


@dataclass(frozen=True)
class UploadResult:
    """保存单张图片的独立上传结果及按配置排列的尝试记录。"""

    success: bool
    provider: str | None
    url: str | None = field(repr=False)
    attempts: tuple[str, ...]
    failures: tuple[UploadFailure, ...]


class ImageHostError(Exception):
    """通过固定错误码表达失败，不保存调用方提供的原始消息。"""

    def __init__(self, code, message):
        """忽略不可信消息，将未知错误码归入通用上传失败。"""
        self.code = (
            code if type(code) is str and code in _ERROR_MESSAGES
            else 'upload_failed'
        )
        self.message = _ERROR_MESSAGES[self.code]
        super().__init__(self.message)


def _has_control(value):
    """识别不可用于链接、文件名或诊断标签的控制字符。"""
    return any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs'}
               for char in value)


def resolve_token(value) -> str:
    """解析直接凭证或完整环境引用，不修改环境或递归展开。

    缺失及空字符串表示匿名凭证；是否允许匿名由适配器决定。
    环境变量名仅允许 ASCII 标识符，直接凭证不去除空白。
    """
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ImageHostError('invalid_token', '')
    reference = _ENV_REFERENCE.fullmatch(value)
    if reference is not None:
        token = os.environ.get(reference.group(1))
        if token is None or token == '':
            raise ImageHostError('missing_environment', '')
        return token
    if '${' in value:
        raise ImageHostError('invalid_environment_reference', '')
    return value


def _valid_host(host):
    """校验 IP 或国际化域名的基本语法，不查询网络或限制域名。"""
    if not host:
        return False
    if ':' in host:
        try:
            ipaddress.IPv6Address(host)
            return True
        except ValueError:
            return False
    try:
        ascii_host = host.encode('idna').decode('ascii')
    except UnicodeError:
        return False
    domain = ascii_host.rstrip('.') if ascii_host.endswith('.') else ascii_host
    if not domain or len(domain) > 253 or ascii_host.endswith('..'):
        return False
    return all(_HOST_LABEL.fullmatch(label) is not None
               for label in domain.split('.'))


def validate_image_url(value) -> str:
    """校验候选 HTTP 图片直链，保留路径与签名，不保证永久可达。

    只移除两端普通空白；拒绝控制字符、用户信息、非法端口和
    无效主机。不要求图片扩展名，也不发送 HEAD 或 GET 请求。
    """
    if not isinstance(value, str):
        raise ImageHostError('invalid_url', '')
    candidate = value.strip(' \t\r\n\v\f')
    if (not candidate or _has_control(candidate)
            or any(char.isspace() for char in candidate)
            or any(char in candidate for char in '<>"\\')):
        raise ImageHostError('invalid_url', '')

    valid = False
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
        port = parts.port
        authority = parts.netloc
        if authority.startswith('['):
            valid_authority = re.fullmatch(
                r'\[[^\]]+\](?::[0-9]+)?', authority) is not None
        else:
            valid_authority = re.fullmatch(
                r'[^:]+(?::[0-9]+)?', authority) is not None
        valid = (
            parts.scheme in {'http', 'https'}
            and parts.username is None and parts.password is None
            and valid_authority and _valid_host(host)
            and (port is None or 0 <= port <= 65535)
        )
    except (ValueError, UnicodeError):
        pass
    # 在处理器之外抛出固定异常，避免保留解析器的敏感异常链。
    if not valid:
        raise ImageHostError('invalid_url', '')
    return candidate
