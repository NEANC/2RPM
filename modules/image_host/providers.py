#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""实现已核验契约的图床上传，不执行链接探测或自动重试。

Catbox 契约来源：https://catbox.moe/tools.php 和
https://catbox.moe/sharexcode.txt；只核验官方文档，未做真实上传。
"""

import logging

import requests

from .core import ImageHostError
from .core import validate_image_url


LOGGER = logging.getLogger(__name__)
CATBOX_ENDPOINT = 'https://catbox.moe/user/api.php'
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
