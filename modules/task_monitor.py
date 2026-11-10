#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime
import logging
import ntpath
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import psutil
import win32evtlog

LOGGER = logging.getLogger(__name__)

# 计划任务操作日志通道
TASK_LOG_CHANNEL = 'Microsoft-Windows-TaskScheduler/Operational'

# 事件日志 XML 命名空间
_EVT_NS = {'e': 'http://schemas.microsoft.com/win/2004/08/events/event'}


def query_task_pid(task_name, lookback_minutes=10):
    """通过 Windows 事件日志查询计划任务创建的最新进程 PID

    使用 pywin32 EvtAPI 读取 Event 129（任务进程已创建）获取 PID
    进程是否存活由调用方通过 psutil.Process + create_time 校验判断

    Args:
        task_name (str): 计划任务名称（完整路径或部分匹配），
            如 "LaunchMyProgram" 或 "\\Custom\\LaunchMyProgram"
        lookback_minutes (int): 事件回溯时间（分钟）

    Returns:
        dict: {
            'pid': int or None,          # 进程 PID
            'process_name': str or None, # 进程名
            'event_time': str or None,   # 事件生成时间
            'state': str,                # 'running' | 'not_found' | 'error'
        }
    """
    try:
        normalized = abs(int(lookback_minutes))
    except (TypeError, ValueError):
        LOGGER.warning(
            f"query_task_pid 参数 lookback_minutes 无效 ({lookback_minutes!r})，"
            f"已回退为 10"
        )
        normalized = 10

    result = {
        'pid': None,
        'process_name': None,
        'event_time': None,
        'state': 'not_found',
    }

    # 查询 Event 129（任务进程已创建）
    event_129 = _get_latest_matching_event(task_name, 129, normalized)
    if event_129 is None:
        return result

    pid = event_129.get('pid')
    if pid is None:
        LOGGER.warning(f"Event 129 中未找到 PID: {event_129}")
        result['state'] = 'error'
        return result

    result['pid'] = pid
    result['process_name'] = event_129.get('process_name')
    result['event_time'] = event_129.get('time')
    result['state'] = 'running'
    return result


def _get_latest_matching_event(task_name, event_id, lookback_minutes):
    """使用 EvtAPI 查询指定事件 ID 且匹配任务名称的最近一条记录

    通过 XPath 在查询端进行事件 ID 与时间过滤，倒序返回最新事件

    Args:
        task_name (str): 计划任务名称（支持部分匹配）
        event_id (int): 事件 ID（129 或 130）
        lookback_minutes (int): 回溯时间（分钟）

    Returns:
        dict or None: {
            'pid': int,
            'process_name': str,
            'task_name': str,
            'time': str,
        }，未找到返回 None
    """
    lookback_ms = max(1, int(lookback_minutes) * 60 * 1000)
    xpath = (
        f"*[System[EventID={event_id} and "
        f"TimeCreated[timediff(@SystemTime) <= {lookback_ms}]]]"
    )
    flags = (
        win32evtlog.EvtQueryChannelPath
        | win32evtlog.EvtQueryReverseDirection
    )

    try:
        h_query = win32evtlog.EvtQuery(
            TASK_LOG_CHANNEL, flags, xpath, None
        )
    except Exception as e:
        LOGGER.error(
            f"查询事件日志失败 (EventID={event_id}): {e}"
        )
        return None

    try:
        while True:
            try:
                events = win32evtlog.EvtNext(h_query, 50)
            except Exception:
                # EvtNext 在无更多结果时可能抛出 ERROR_NO_MORE_ITEMS
                break
            if not events:
                break

            for evt_handle in events:
                parsed = _parse_event_xml(evt_handle)
                if not parsed:
                    continue
                event_task = parsed.get('task_name') or ''
                if task_name.lower() not in event_task.lower():
                    continue
                LOGGER.info(
                    f"匹配 Event {event_id}: "
                    f"PID={parsed['pid']}, Time={parsed['time']}"
                )
                return parsed

        return None
    except Exception as e:
        LOGGER.error(f"读取事件日志异常 (EventID={event_id}): {e}")
        return None
    finally:
        try:
            win32evtlog.EvtClose(h_query)
        except Exception:
            pass


def _parse_event_xml(evt_handle):
    """渲染事件为 XML 并解析关键字段

    Args:
        evt_handle: EvtAPI 事件句柄

    Returns:
        dict or None: 包含 pid / process_name / task_name / time 的字典
    """
    try:
        xml_str = win32evtlog.EvtRender(
            evt_handle, win32evtlog.EvtRenderEventXml
        )
    except Exception as e:
        LOGGER.warning(f"渲染事件 XML 失败: {e}")
        return None

    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        LOGGER.warning(f"事件 XML 解析失败: {e}")
        return None

    # 提取 System/TimeCreated
    time_elem = root.find('e:System/e:TimeCreated', _EVT_NS)
    time_str = (
        time_elem.get('SystemTime', '') if time_elem is not None else ''
    )

    # 提取 EventData/Data
    event_data = root.find('e:EventData', _EVT_NS)
    if event_data is None:
        return None

    # 支持按 Name 属性提取，也支持按位置提取（部分事件无 Name 属性）
    data_dict = {}
    for index, data in enumerate(event_data.findall('e:Data', _EVT_NS)):
        key = data.get('Name') or f'_pos{index}'
        data_dict[key] = data.text or ''

    task_name_val = data_dict.get('TaskName') or data_dict.get('_pos0') or ''
    pid_val = (
        data_dict.get('ProcessId')
        or data_dict.get('ProcessID')
        or data_dict.get('PID')
        or _find_first_integer_value(data_dict)
    )
    process_name_val = (
        data_dict.get('ProcessName')
        or data_dict.get('ActionName')
        or data_dict.get('_pos2')
        or None
    )

    try:
        pid = int(pid_val) if pid_val else None
    except (ValueError, TypeError):
        pid = None

    return {
        'pid': pid,
        'process_name': process_name_val,
        'task_name': task_name_val,
        'time': time_str,
    }


def _find_first_integer_value(data_dict):
    """从事件数据中查找第一个整数值

    Args:
        data_dict (dict): 事件数据字典

    Returns:
        str or None: 第一个可转换为整数的值，未找到返回 None
    """
    for value in data_dict.values():
        if not value:
            continue
        try:
            int(value)
            return value
        except (ValueError, TypeError):
            continue
    return None


# 进程创建时间与事件时间的最大允许差值（秒），工程容差而非实例级关联
BIND_TOLERANCE_SECONDS = 1.0

# Event 129 表示调度器已创建任务进程
_EVENT_ID_TASK_PROCESS_CREATED = 129

# lookback_minutes 非法时的默认回溯时长（分钟）
_DEFAULT_LOOKBACK_MINUTES = 10

# ERROR_NO_MORE_ITEMS，表示枚举已到末尾
_ERROR_NO_MORE_ITEMS = 259

# 单次 EvtNext 最多取出的事件数
_EVENT_BATCH_SIZE = 50


@dataclass(frozen=True)
class TaskEvent:
    """Event 129 中的计划任务进程创建记录

    Attributes:
        task_path (str): 事件中的完整任务路径
        pid (int): 调度器创建的进程 PID
        event_time (str): 原始 UTC ISO 文本，如 2026-11-10T04:30:28.1800210Z
        record_id (int): 事件记录号，缺失时为 0
        instance_id (str | None): 129 恒为 None；保留字段但不伪造
        path (str): 调度器启动的可执行文件路径，缺失时为空串
    """

    task_path: str
    pid: int
    event_time: str
    record_id: int
    instance_id: str | None
    path: str = ''


class TaskQueryError(Exception):
    """事件日志查询失败（通道、枚举、渲染）。"""


def _is_no_more_items(error):
    """判断异常是否为“没有更多结果”

    同时兼容 pywintypes.error 的 winerror 属性与仅带参数的自定义异常。

    Args:
        error (Exception): EvtNext 抛出的异常

    Returns:
        bool: 是 ERROR_NO_MORE_ITEMS 时返回 True
    """
    if getattr(error, 'winerror', None) == _ERROR_NO_MORE_ITEMS:
        return True
    if error.args and error.args[0] == _ERROR_NO_MORE_ITEMS:
        return isinstance(error.args[0], int)
    return False


def _close_handle(handle):
    """关闭事件日志句柄，异常不得掩盖主流程

    Args:
        handle: EvtQuery 或 EvtNext 返回的句柄，可为 None
    """
    close = getattr(handle, 'Close', None)
    if close is None:
        return
    try:
        close()
    except Exception as e:
        LOGGER.debug(f"关闭事件日志句柄失败: {e}")


def _normalize_lookback(lookback_minutes):
    """把回溯时长规范为非负整数分钟

    Args:
        lookback_minutes (int | str): 原始回溯时长

    Returns:
        int: 非负分钟数，非法值回退为默认值
    """
    try:
        return abs(int(lookback_minutes))
    except (TypeError, ValueError):
        LOGGER.warning(
            f"lookback_minutes 参数无效 ({lookback_minutes!r})，"
            f"已回退为 {_DEFAULT_LOOKBACK_MINUTES}"
        )
        return _DEFAULT_LOOKBACK_MINUTES


def _render_event_xml(handle):
    """把事件句柄渲染为 XML 文本

    Args:
        handle: EvtNext 返回的事件句柄

    Returns:
        str: 事件 XML 文本

    Raises:
        TaskQueryError: 渲染失败
    """
    try:
        return win32evtlog.EvtRender(handle, win32evtlog.EvtRenderEventXml)
    except Exception as e:
        raise TaskQueryError(f"渲染事件 XML 失败: {e}") from e


def _parse_event_root(xml_text):
    """解析事件 XML 文本

    Args:
        xml_text (str | bytes): 事件 XML

    Returns:
        xml.etree.ElementTree.Element: 事件根节点

    Raises:
        TaskQueryError: XML 无法解析
    """
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise TaskQueryError(f"解析事件 XML 失败: {e}") from e


def _read_event_meta(root):
    """读取事件的系统级字段

    Args:
        root: 事件根节点

    Returns:
        tuple: (event_time, record_id, instance_id)
    """
    time_elem = root.find('e:System/e:TimeCreated', _EVT_NS)
    event_time = (
        time_elem.get('SystemTime', '') if time_elem is not None else ''
    )

    record_elem = root.find('e:System/e:EventRecordID', _EVT_NS)
    record_id = 0
    if record_elem is not None and record_elem.text:
        try:
            record_id = int(record_elem.text.strip())
        except ValueError:
            record_id = 0

    corr_elem = root.find('e:System/e:Correlation', _EVT_NS)
    instance_id = None
    if corr_elem is not None:
        instance_id = corr_elem.get('ActivityID') or None

    return event_time, record_id, instance_id


def _read_event_data(root):
    """按 Name 属性读取 EventData 中的键值

    Args:
        root: 事件根节点

    Returns:
        dict: 带 Name 的数据项，缺失 Name 的项被忽略
    """
    event_data = root.find('e:EventData', _EVT_NS)
    if event_data is None:
        return {}
    return {
        data.get('Name'): (data.text or '')
        for data in event_data.findall('e:Data', _EVT_NS)
        if data.get('Name')
    }


def _extract_task_event(root, task_path):
    """从事件根节点提取匹配的 TaskEvent

    任务路径按完整路径精确比较（忽略大小写）；PID 只取 ProcessID，
    缺失、非整数或非正数时跳过该候选。

    Args:
        root: 事件根节点
        task_path (str): 期望的完整任务路径

    Returns:
        TaskEvent | None: 未匹配或 PID 不可用时返回 None
    """
    data = _read_event_data(root)
    event_task_path = data.get('TaskName', '')
    if ntpath.normcase(event_task_path) != ntpath.normcase(task_path):
        return None

    pid_text = (data.get('ProcessID') or '').strip()
    if not pid_text:
        LOGGER.debug(f"Event 129 缺少 ProcessID，跳过候选: {event_task_path}")
        return None

    try:
        pid = int(pid_text)
    except ValueError:
        LOGGER.debug(f"Event 129 的 ProcessID 非整数，跳过候选: {pid_text!r}")
        return None

    if pid <= 0:
        LOGGER.debug(f"Event 129 的 ProcessID 非正数，跳过候选: {pid}")
        return None

    event_time, record_id, instance_id = _read_event_meta(root)
    return TaskEvent(
        task_path=event_task_path,
        pid=pid,
        event_time=event_time,
        record_id=record_id,
        instance_id=instance_id,
        path=data.get('Path', ''),
    )


def query_task_events(task_path, lookback_minutes):
    """查询指定任务在回溯窗口内的进程创建事件

    只查询 Event 129，按时间倒序枚举；任务路径按完整路径精确比较。
    查询句柄与每个事件句柄都在 finally 中通过 .Close() 关闭。

    Args:
        task_path (str): 完整任务路径，如 \\Folder\\Task
        lookback_minutes (int): 回溯时长（分钟），非法时回退为 10 分钟

    Returns:
        list[TaskEvent]: 从新到旧排列的事件列表，无匹配时为空列表

    Raises:
        TaskQueryError: 查询、枚举或渲染失败
    """
    lookback_ms = max(1, _normalize_lookback(lookback_minutes) * 60 * 1000)
    xpath = (
        f"*[System[EventID={_EVENT_ID_TASK_PROCESS_CREATED} and "
        f"TimeCreated[timediff(@SystemTime) <= {lookback_ms}]]]"
    )
    flags = (
        win32evtlog.EvtQueryChannelPath
        | win32evtlog.EvtQueryReverseDirection
    )

    try:
        query_handle = win32evtlog.EvtQuery(
            TASK_LOG_CHANNEL, flags, xpath, None
        )
    except Exception as e:
        raise TaskQueryError(f"查询事件日志失败: {e}") from e

    events = []
    try:
        while True:
            try:
                handles = win32evtlog.EvtNext(query_handle, _EVENT_BATCH_SIZE)
            except Exception as e:
                if _is_no_more_items(e):
                    break
                raise TaskQueryError(f"枚举事件日志失败: {e}") from e

            if not handles:
                break

            for handle in handles:
                try:
                    xml_text = _render_event_xml(handle)
                    event = _extract_task_event(
                        _parse_event_root(xml_text), task_path
                    )
                finally:
                    _close_handle(handle)

                if event is not None:
                    events.append(event)
    finally:
        _close_handle(query_handle)

    return events


def get_task_event_boundary():
    """读取当前最新一条事件的事件记录号

    调用方用该值作为触发前的边界，只接受边界之后的事件。

    Returns:
        int: 最新事件的 EventRecordID，没有事件或查询失败时返回 0
    """
    flags = (
        win32evtlog.EvtQueryChannelPath
        | win32evtlog.EvtQueryReverseDirection
    )
    query_handle = None
    try:
        query_handle = win32evtlog.EvtQuery(
            TASK_LOG_CHANNEL, flags, '*', None
        )
        handles = win32evtlog.EvtNext(query_handle, 1)
        if not handles:
            return 0

        handle = handles[0]
        try:
            xml_text = _render_event_xml(handle)
            record_elem = _parse_event_root(xml_text).find(
                'e:System/e:EventRecordID', _EVT_NS
            )
            if record_elem is None or not record_elem.text:
                return 0
            return int(record_elem.text.strip())
        finally:
            _close_handle(handle)
    except Exception as e:
        LOGGER.debug(f"读取事件日志边界失败: {e}")
        return 0
    finally:
        _close_handle(query_handle)


def _normalize_exe_path(path):
    """规范化可执行文件路径用于比较

    Args:
        path (str): 原始路径

    Returns:
        str: 统一分隔符并忽略大小写后的路径
    """
    if not path:
        return ''
    return ntpath.normcase(ntpath.normpath(str(path).replace('/', '\\')))


def _parse_event_time(event_time):
    """把事件时间文本解析为 POSIX 时间戳

    Args:
        event_time (str): 事件 SystemTime 文本，如 2026-11-10T04:30:28.1800210Z

    Returns:
        float: UTC 时间戳

    Raises:
        ValueError: 文本为空或格式非法
    """
    text = str(event_time or '').strip()
    if not text:
        raise ValueError('事件时间为空')
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


def bind_task_event(event):
    """用 PID、可执行路径与创建时间做弱身份绑定

    任一校验不通过都返回 None；该方法不证明进程实例来自本次触发。

    Args:
        event (TaskEvent): 待绑定的查询结果

    Returns:
        dict | None: {'pid': int, 'name': str, 'create_time': float}，
            校验失败返回 None
    """
    try:
        pid = int(event.pid)
    except (TypeError, ValueError):
        LOGGER.warning(f"事件中的 PID 无效: {getattr(event, 'pid', None)!r}")
        return None

    if pid <= 0:
        return None

    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        LOGGER.debug(f"PID {pid} 不存在，跳过绑定")
        return None
    except psutil.AccessDenied:
        LOGGER.warning(f"PID {pid} 无法读取进程身份（AccessDenied）")
        return None

    try:
        exe = proc.exe()
    except psutil.AccessDenied:
        LOGGER.warning(f"PID {pid} 无法读取可执行路径（AccessDenied）")
        return None
    except psutil.NoSuchProcess:
        LOGGER.debug(f"PID {pid} 已退出，跳过绑定")
        return None

    if not exe:
        LOGGER.warning(f"PID {pid} 无法获取可执行路径")
        return None

    if '%' in exe:
        LOGGER.warning(f"PID {pid} 的可执行路径含未展开变量: {exe!r}")
        return None

    event_path = getattr(event, 'path', '') or ''
    if _normalize_exe_path(exe) != _normalize_exe_path(event_path):
        LOGGER.warning(
            f"PID {pid} 的可执行路径与事件不一致: {exe!r} != {event_path!r}"
        )
        return None

    try:
        event_timestamp = _parse_event_time(event.event_time)
    except (TypeError, ValueError):
        LOGGER.warning(f"事件时间格式非法: {getattr(event, 'event_time', None)!r}")
        return None

    try:
        create_time = proc.create_time()
    except psutil.Error:
        LOGGER.warning(f"PID {pid} 无法读取创建时间")
        return None

    if abs(create_time - event_timestamp) > BIND_TOLERANCE_SECONDS:
        LOGGER.warning(
            f"PID {pid} 创建时间与事件时间相差 "
            f"{abs(create_time - event_timestamp):.3f} 秒，拒绝绑定"
        )
        return None

    try:
        name = proc.name()
    except psutil.AccessDenied:
        name = ntpath.basename(event_path)
    except psutil.NoSuchProcess:
        LOGGER.debug(f"PID {pid} 读取进程名时已退出，跳过绑定")
        return None

    return {'pid': pid, 'name': name, 'create_time': float(create_time)}
