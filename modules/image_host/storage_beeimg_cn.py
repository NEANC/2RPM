#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""BeeIMG.CN 站点专用的组查询、解析与选择规则。

本模块只导入共享模型与 HTTP 基础设施，绝不请求账号资料，也不持有
缓存或上传状态；与另一站点互不共享任何解析器或选择状态。
"""

from collections.abc import Mapping

from . import v2_http
from .core import ImageHostError
from .storage import _fetch_time
from .storage import _positive_integer
from .storage import StorageMetadata
from .storage import StorageSelection


def parse_group(payload: Mapping) -> tuple[tuple[int, ...], int | None]:
    """严格校验组信息，保序去重且仅保留存储编号与可选期限。

    缺项、非列表 storages、非法条目或负/布尔期限均按查询失败处理，
    不静默跳过坏项；合法但空列表归为无可用存储。
    """
    data = payload.get('data')
    if not isinstance(data, Mapping):
        raise ImageHostError('storage_lookup_failed', '', stage='group')
    group = data.get('group')
    if not isinstance(group, Mapping):
        raise ImageHostError('storage_lookup_failed', '', stage='group')
    options = group.get('options')
    storages = data.get('storages')
    if not isinstance(options, Mapping) or not isinstance(storages, list):
        raise ImageHostError('storage_lookup_failed', '', stage='group')
    expire = options.get('file_expire_seconds')
    if expire is not None and (
            not isinstance(expire, int) or isinstance(expire, bool)
            or expire < 0):
        raise ImageHostError('storage_lookup_failed', '', stage='group')
    storage_ids = []
    seen = set()
    for entry in storages:
        if not isinstance(entry, Mapping):
            raise ImageHostError('storage_lookup_failed', '', stage='group')
        storage_id = entry.get('id')
        if not _positive_integer(storage_id):
            raise ImageHostError('storage_lookup_failed', '', stage='group')
        if storage_id not in seen:
            seen.add(storage_id)
            storage_ids.append(storage_id)
    if not storage_ids:
        raise ImageHostError('storage_unavailable', '', stage='storage')
    return tuple(storage_ids), expire


def fetch_metadata(token: str, *, now: float) -> StorageMetadata:
    """只查询组信息，绝不请求账号资料，也不保留完整响应。

    凭证由调用方解析，本入口不展开或修剪。零期限不代表永久保存，
    此处不计算过期时间；普通边界异常不保留敏感底层异常链。
    """
    fetched_at = _fetch_time(now)
    try:
        storage_ids, expire = parse_group(
            v2_http.request_json('beeimg_cn', 'group', token))
        return StorageMetadata(storage_ids, None, expire, fetched_at,
                               'not_requested')
    except ImageHostError:
        raise
    except Exception:
        pass
    raise ImageHostError('storage_lookup_failed', '', stage='group')


def select_storage(metadata: StorageMetadata, manual_present: bool,
                   manual_value: object, *,
                   excluded_id: int | None = None) -> StorageSelection:
    """按组顺序取首个候选，忽略手填优先级与默认编号。

    本选择器只服务自动选择与恢复：手填值不参与优先级，replaced_manual
    只反映调用方是否声明了手填；excluded_id 用于恢复时排除已拒绝候选。
    """
    candidates = [storage_id for storage_id in metadata.storage_ids
                  if storage_id != excluded_id]
    if not candidates:
        raise ImageHostError('storage_unavailable', '', stage='storage')
    return StorageSelection(candidates[0], manual_present)
