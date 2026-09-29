#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""实现已核验契约的图床上传，不执行链接探测或自动重试。

Catbox 契约来源：https://catbox.moe/tools.php 和
https://catbox.moe/sharexcode.txt；只核验官方文档，未做真实上传。
"""

from collections.abc import Mapping
import logging

import requests

from .core import ImageHostError
from .core import validate_image_url


LOGGER = logging.getLogger(__name__)
CATBOX_ENDPOINT = 'https://catbox.moe/user/api.php'
WMIMG_ENDPOINT = 'https://wmimg.com/api/v1/upload'
BEEIMG_ENDPOINT = 'https://beeimg.com/api/upload/file/json/'
SUPERBED_ENDPOINT = 'https://api.superbed.cn/upload'
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 15


def upload_catbox(image_bytes, filename, token, options) -> str:
    """将内存 PNG 上传到 Catbox，返回经核心校验的完整候选链接。

    token 已由注册表解析，非空时原样用作 userhash。服务未确认
    支持私有上传，因此显式权限只接受公开值 1，其余安全拒绝。
    连接和读取超时不是整个操作的硬总时限；发生超时也不能保证
    服务端尚未保存图片。每次调用最多发送一次上传请求。
    """
    if 'permission' in options and options['permission'] != 1:
        raise ImageHostError('invalid_options', '')
    for name in ('album_id', 'strategy_id'):
        if name in options:
            LOGGER.warning('Catbox 不支持选项 %s，已忽略', name)
    if any(name not in {'permission', 'album_id', 'strategy_id'}
           for name in options):
        LOGGER.warning('Catbox 存在不支持的其他选项，已忽略')

    data = {'reqtype': 'fileupload'}
    if token:
        data['userhash'] = token
    try:
        with requests.Session() as session:
            with session.post(
                    CATBOX_ENDPOINT,
                    data=data,
                    files={'fileToUpload': (filename, image_bytes, 'image/png')},
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    verify=True,
                    stream=True,
                    allow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    raise ImageHostError('upload_failed', '')
                return validate_image_url(response.text)
    except requests.exceptions.RequestException:
        raise ImageHostError('upload_failed', '') from None


def upload_wmimg(image_bytes, filename, token, options) -> str:
    """依据 WMIMG 官方契约上传内存 PNG，返回经校验的完整直链。

    契约来源：https://wmimg.com/page/api-docs.html，未做真实上传。
    token 已由注册表解析，仅用作 Bearer 账号凭证；空值允许尝试
    游客上传，是否接受由服务器决定。项目默认显式发送公开权限，
    私有值不代表直链必然限制访问。每次调用最多发送一次请求。
    文档仅规定相册和策略 ID 为整数，显式值的有效性由服务端判断。
    连接和读取超时不构成总时限，也不能保证超时后服务端未保存。
    """
    permission = options.get('permission', 1)
    if (not isinstance(permission, int) or isinstance(permission, bool)
            or permission not in (0, 1)):
        raise ImageHostError('invalid_options', '')
    data = {'permission': permission}
    for name in ('album_id', 'strategy_id'):
        if name not in options:
            continue
        value = options[name]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ImageHostError('invalid_options', '')
        data[name] = value
    if any(name not in {'permission', 'album_id', 'strategy_id'}
           for name in options):
        LOGGER.warning('WMIMG 存在不支持的其他选项，已忽略')

    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        with requests.Session() as session:
            with session.post(
                    WMIMG_ENDPOINT,
                    data=data,
                    files={'file': (filename, image_bytes, 'image/png')},
                    headers=headers,
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    verify=True,
                    stream=True,
                    allow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    raise ImageHostError('upload_failed', '')
                payload = response.json()
                if (not isinstance(payload, Mapping)
                        or payload.get('status') is not True):
                    raise ImageHostError('upload_failed', '')
                result = payload.get('data')
                if not isinstance(result, Mapping):
                    raise ImageHostError('upload_failed', '')
                links = result.get('links')
                if not isinstance(links, Mapping):
                    raise ImageHostError('upload_failed', '')
                return validate_image_url(links.get('url'))
    except requests.exceptions.RequestException:
        raise ImageHostError('upload_failed', '') from None


def upload_beeimg(image_bytes, filename, token, options) -> str:
    """依据 BeeIMG.com 官方契约上传内存 PNG，不适用于 BeeIMG.cn。

    契约来源：https://beeimg.com/api/ 和 https://beeimg.com/faq，
    核验日期为 2026-09-29；仅核验文档，未做真实上传。
    token 已由注册表解析，非空时仅作为表单 apikey。公开权限转为
    privacy=public；private 只是持链可访问的非公开列出状态。
    truly-private 依赖 Premium 且到期后会降级，文档未明确无有效
    Premium 时的 API 行为，因此当前拒绝严格私有请求和原生 privacy。
    albumid 按文档接受五位相册或九位文件夹字符串，并要求账号凭证。
    每次最多发送一次请求；连接及读取超时不是整个操作的硬总时限，
    超时不能保证服务器尚未保存图片。
    """
    permission = options.get('permission', 1)
    if (not isinstance(permission, int) or isinstance(permission, bool)
            or permission != 1 or 'privacy' in options):
        raise ImageHostError('invalid_options', '')

    data = {'privacy': 'public'}
    if token:
        data['apikey'] = token
    if 'albumid' in options:
        albumid = options['albumid']
        if (not isinstance(albumid, str) or len(albumid) not in (5, 9)
                or not token):
            raise ImageHostError('invalid_options', '')
        data['albumid'] = albumid
    if any(name not in {'permission', 'albumid'} for name in options):
        LOGGER.warning('BeeIMG 存在不支持的其他选项，已忽略')

    try:
        with requests.Session() as session:
            with session.post(
                    BEEIMG_ENDPOINT,
                    data=data,
                    files={'file': (filename, image_bytes, 'image/png')},
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    verify=True,
                    stream=True,
                    allow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    raise ImageHostError('upload_failed', '')
                payload = response.json()
                if not isinstance(payload, Mapping):
                    raise ImageHostError('upload_failed', '')
                result = payload.get('files')
                if (not isinstance(result, Mapping)
                        or result.get('status') != 'Success'):
                    raise ImageHostError('upload_failed', '')
                return validate_image_url(result.get('url'))
    except ImageHostError:
        raise
    except Exception:
        pass
    # 在异常处理器之外抛出固定错误，不保留含敏感正文的原始异常链。
    raise ImageHostError('upload_failed', '')


def upload_superbed(image_bytes, filename, token, options) -> str:
    """依据 Superbed 官方客户端上传示例发送内存 PNG。

    契约来源：https://www.superbed.cn/help 的 PicGo 和 ShareX 示例；
    仅核验公开文档，未做真实上传。已解析的凭证原样进入表单 token，
    不混发通用说明中的 X-API-Key，也不推测匿名支持。categories
    为可选相册名称字符串。公开是项目约定，不代表已验证服务端权限；
    未确认私有能力，因此请求前拒绝私有要求及原生 privacy 参数。
    官方提取根级 url，不要求额外成功字段；明确失败标记防御性拒绝。
    每次最多一次请求，连接和读取超时不保证服务端尚未保存图片。
    """
    if not isinstance(token, str) or not token:
        raise ImageHostError('invalid_token', '')
    permission = options.get('permission', 1)
    if (not isinstance(permission, int) or isinstance(permission, bool)
            or permission != 1 or 'privacy' in options):
        raise ImageHostError('invalid_options', '')

    data = {'token': token}
    if 'categories' in options:
        categories = options['categories']
        if not isinstance(categories, str):
            raise ImageHostError('invalid_options', '')
        data['categories'] = categories
    if any(name not in {'permission', 'categories'} for name in options):
        LOGGER.warning('Superbed 存在不支持的其他选项，已忽略')

    try:
        with requests.Session() as session:
            with session.post(
                    SUPERBED_ENDPOINT,
                    data=data,
                    files={'file': (filename, image_bytes, 'image/png')},
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    verify=True,
                    stream=True,
                    allow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    raise ImageHostError('upload_failed', '')
                payload = response.json()
                if not isinstance(payload, Mapping):
                    raise ImageHostError('upload_failed', '')
                if (payload.get('success') is False
                        or payload.get('status') is False
                        or payload.get('error')):
                    raise ImageHostError('upload_failed', '')
                return validate_image_url(payload.get('url'))
    except ImageHostError:
        raise
    except Exception:
        pass
    # 离开异常处理器后再抛出，避免保存包含凭证或正文的异常链。
    raise ImageHostError('upload_failed', '')
