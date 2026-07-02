#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import xml.etree.ElementTree as ET
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
            'pid': int or None,          # 任务创建的最新进程 PID
            'process_name': str or None, # 进程名
            'event_time': str or None,   # 事件生成时间
            'state': str,                # 'running' | 'not_found' | 'error'
        }
    """
    result = {
        'pid': None,
        'process_name': None,
        'event_time': None,
        'state': 'not_found',
    }

    # 查询 Event 129（任务进程已创建）
    event_129 = _get_latest_matching_event(task_name, 129, lookback_minutes)
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
