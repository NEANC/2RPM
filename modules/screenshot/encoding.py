#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""提供截图像素到固定内部格式的纯内存编码。"""

from io import BytesIO
import struct

from PIL import Image


def window_image(pixels, width, height):
    """从顶向下 BGRX 像素生成不含 alpha 语义的 RGB 图像。"""
    return Image.frombytes(
        'RGB', (width, height), pixels, 'raw', 'BGRX', width * 4, 1)


def encode_image(image, image_format):
    """直接从采集像素编码固定格式，不做回读或缩放。"""
    if image_format == 'raw':
        return (struct.pack('<4sIIII', b'2RAW', 1, *image.size, 1)
                + image.convert('RGBA').tobytes())
    with BytesIO() as output:
        if image_format == 'jpeg':
            image.convert('RGB').save(
                output, format='JPEG', quality=75, optimize=False)
        elif image_format == 'webp':
            image.save(
                output, format='WEBP', lossless=True, quality=100,
                method=4, exact=True)
        elif image_format == 'png':
            image.save(output, format='PNG')
        else:
            raise ValueError('不支持的内部图像格式')
        return output.getvalue()
