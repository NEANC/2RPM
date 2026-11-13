#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 测试请求与结果的数据契约。"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TestRequest:
    """保存单次独立测试的配置快照与草稿身份。"""

    request_id: str
    document_generation: int
    revision: int
    action: Literal['capture', 'upload', 'notify']
    config: dict
    config_dir: str
    session_dir: str
    target_index: int | None
    host_index: int | None
    image_path: str | None
    template_key: str | None


@dataclass(frozen=True)
class TestResult:
    """仅传递安全终态及本地预览图片路径。"""

    request_id: str
    document_generation: int
    revision: int
    status: Literal['success', 'failure', 'partial']
    reason: str
    preview_paths: tuple[str, ...]
