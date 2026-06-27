#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import asyncio
import logging
import sys
import psutil

from modules.utils import (
    format_time_ms,
    run_external_program,
    parse_time_string
)
from modules.notification import send_notification
from modules.config import DEFAULT_VALUES
from modules.task_monitor import query_task_pid

LOGGER = logging.getLogger(__name__)


# 用于 asyncio.to_thread 中获取程序名（os.path.basename 在线程中安全）
def _get_program_name(program_path):
    """获取程序的文件名部分。

    Args:
        program_path (str): 程序完整路径。

    Returns:
        str: 程序文件名。
    """
    return os.path.basename(program_path)


async def _handle_process_end(config, process_name, pid, run_time_ms,
                               external_program_path):
    """处理进程结束：发送通知并可选调用外部程序。

    Args:
        config (dict): 配置信息。
        process_name (str): 进程名。
        pid (int): 进程 PID。
        run_time_ms (float): 进程运行时间（毫秒）。
        external_program_path (str): 进程结束时调用的外部程序路径。
    """
    formatted_run_time = format_time_ms(run_time_ms)

    LOGGER.info(
        f"进程结束: {process_name} (PID: {pid}) "
        f"运行时间: {formatted_run_time}"
    )
    await send_notification(
        config,
        'process_end_notification',
        process_name=process_name,
        process_pid=pid,
        process_run_time=formatted_run_time
    )

    # 进程结束时调用外部程序
    if external_program_path:
        LOGGER.info(
            f"检测到进程 {process_name} 结束，正在调用外部程序..."
        )
        try:
            await asyncio.to_thread(run_external_program, external_program_path)
            LOGGER.info(f"成功调用外部程序 {external_program_path}")
            # 发送外部程序执行通知
            await send_notification(
                config,
                'external_program_execution_notification',
                external_program_name=_get_program_name(external_program_path),
                external_program_path=external_program_path,
                process_name=process_name,
                process_pid=pid,
            )
        except Exception as e:
            LOGGER.error(
                f"调用外部程序 {external_program_path} 时发生错误: {e}",
                exc_info=True
            )


async def _check_process_timeout(config, process_info, pid, current_time_ms,
                                  timeout_warning_interval_ms,
                                  another_external_program_path,
                                  timeout_count_threshold):
    """检查进程超时，发送警告并在达到阈值时触发外部程序。

    Args:
        config (dict): 配置信息。
        process_info (dict): 进程监视信息，需包含 start_time_ms / last_warning_time_ms / timeout_count。
        pid (int): 进程 PID。
        current_time_ms (float): 当前时间（毫秒）。
        timeout_warning_interval_ms (int): 超时警告间隔（毫秒）。
        another_external_program_path (str): 超时后触发的外部程序路径。
        timeout_count_threshold (int): 触发外部程序所需的超时累计次数阈值。
    """
    run_time_ms = current_time_ms - process_info['start_time_ms']
    time_since_last_warning_ms = (
        current_time_ms - process_info['last_warning_time_ms']
    )

    if time_since_last_warning_ms < timeout_warning_interval_ms:
        return

    formatted_run_time = format_time_ms(run_time_ms)
    process_name = process_info['name']
    LOGGER.warning(
        f"进程 {process_name} (PID: {pid}) "
        f"已运行超时 {formatted_run_time}"
    )
    await send_notification(
        config,
        'process_timeout_warning',
        process_name=process_name,
        process_pid=pid,
        process_run_time=formatted_run_time
    )
    process_info['last_warning_time_ms'] = current_time_ms
    process_info['timeout_count'] += 1

    # 检查是否需要执行外部程序
    if (another_external_program_path and
            process_info['timeout_count'] % timeout_count_threshold == 0):
        LOGGER.info(
            f"进程 {process_name} (PID: {pid})，"
            f"超时次数达到阈值 {timeout_count_threshold}，"
            f"正在调用外部程序..."
        )
        try:
            await asyncio.to_thread(
                run_external_program, another_external_program_path)
            LOGGER.info(
                f"外部程序 {another_external_program_path} "
                f"执行成功"
            )
            # 发送外部程序执行通知
            await send_notification(
                config,
                'external_program_execution_notification',
                external_program_name=_get_program_name(
                    another_external_program_path),
                external_program_path=another_external_program_path,
                process_name=process_name,
                process_pid=pid,
            )
            LOGGER.critical("外部程序执行完成，正在结束运行")
            sys.exit(0)
        except Exception as e:
            LOGGER.error(
                f"调用外部程序 {another_external_program_path} "
                f"时发生错误: {e}",
                exc_info=True
            )



def _collect_matching_processes(process_name):
    """遍历系统进程，收集与指定名称匹配的 PID 及其 create_time。

    Args:
        process_name (str): 要匹配的进程名。

    Returns:
        dict: {pid: {'name', 'create_time'}}
    """
    current_processes = {}
    for p in psutil.process_iter(['pid', 'name', 'create_time']):
        info = p.info
        if info['name'] == process_name:
            current_processes[p.pid] = {
                'name': info['name'],
                'create_time': info['create_time'],
            }
    return current_processes


def _detect_ended_pids(monitored_pids, current_processes, processes):
    """检测已结束或 PID 被复用的进程。

    通过 PID 是否还在系统进程中及 create_time 是否匹配双重判断。

    Args:
        monitored_pids (set): 当前监视的 PID 集合。
        current_processes (dict): 当前系统中与目标进程名匹配的进程信息。
        processes (dict): 监视列表，值需包含 create_time。

    Returns:
        set: 已结束或被复用的 PID 集合。
    """
    current_pids = set(current_processes.keys())
    # PID 已从系统中消失
    ended_pids = monitored_pids - current_pids
    # PID 仍存在但 create_time 不匹配（原进程退出，新进程复用同一个 PID）
    for pid in monitored_pids & current_pids:
        if current_processes[pid]['create_time'] != processes[pid]['create_time']:
            ended_pids.add(pid)
            LOGGER.debug(f"PID {pid} create_time 不匹配，判定为原进程已结束")
    return ended_pids


def _add_new_process(processes, pid, name, create_time):
    """将新检测到的进程加入监视列表。

    Args:
        processes (dict): 监视列表。
        pid (int): 进程 PID。
        name (str): 进程名。
        create_time (float): 进程创建时间戳。
    """
    if pid in processes:
        return
    start_time_offset_ms = (
        time.perf_counter() * 1000
        - (time.time() - create_time) * 1000
    )
    processes[pid] = {
        'name': name,
        'create_time': create_time,
        'start_time_ms': start_time_offset_ms,
        'last_warning_time_ms': start_time_offset_ms,
        'timeout_count': 0,
    }
    LOGGER.info(f"检测到进程启动: {name} (PID: {pid})")


async def monitor_processes(config):
    """监视进程列表。

    等待指定的进程启动，监视其运行状态，并在进程结束或超时时发送通知。

    Args:
        config (dict): 配置信息。
    """
    monitor_settings = config.get('monitor_settings', {})
    wait_settings = config.get('wait_process_settings', {})
    external_settings = config.get('external_program_settings', {})

    # 检查监视模式
    monitor_mode = monitor_settings.get('monitor_mode', 'psutil')

    if monitor_mode == 'task_scheduler':
        LOGGER.info("读取计划任务来获取 PID 进行监视")
        await monitor_via_task_scheduler(config)
        return

    process_name = monitor_settings.get(
        'process_name',
        DEFAULT_VALUES['monitor_settings']['process_name'])
    timeout_warning_interval_ms = parse_time_string(monitor_settings.get(
        'timeout_warning_interval',
        DEFAULT_VALUES['monitor_settings']['timeout_warning_interval']))
    monitor_loop_interval_ms = parse_time_string(monitor_settings.get(
        'monitor_loop_interval',
        DEFAULT_VALUES['monitor_settings']['monitor_loop_interval']))

    max_wait_time_ms = parse_time_string(wait_settings.get(
        'max_wait_time',
        DEFAULT_VALUES['wait_process_settings']['max_wait_time']))
    wait_process_check_interval_ms = parse_time_string(wait_settings.get(
        'wait_process_check_interval',
        DEFAULT_VALUES['wait_process_settings']['wait_process_check_interval']))

    # 外部程序调用设置
    external_program_path = external_settings.get('external_program_path', '')
    another_external_program_path = external_settings.get(
        'another_external_program_path', '')
    timeout_count_threshold = external_settings.get(
        'timeout_count_threshold', 3)
    external_program_on_wait_timeout_path = external_settings.get(
        'external_program_on_wait_timeout_path', '')

    LOGGER.debug("初始化监视参数")
    processes = {}

    # 检查 process_name 是否有效
    if not process_name:
        LOGGER.critical("未设置要监视的进程，请检查配置文件！")
        sys.exit(1)

    LOGGER.info(
        f"等待监视进程启动，每 {wait_process_check_interval_ms} ms 检查一次"
    )
    start_time_ms = time.perf_counter() * 1000
    # 等待提示仅打印一次，避免进程未启动时刷屏
    waiting_logged = False

    try:
        # 等待进程启动
        while True:
            LOGGER.debug("执行等待进程启动循环")
            waited_time_ms = time.perf_counter() * 1000 - start_time_ms
            if waited_time_ms > max_wait_time_ms:
                LOGGER.debug("已等待超时，正在尝试发送通知")
                break

            current_processes = _collect_matching_processes(process_name)

            for pid, info in current_processes.items():
                _add_new_process(
                    processes, pid, info['name'], info['create_time'])

            # 检查是否进程已启动
            if processes:
                LOGGER.info("目标监视进程已启动")
                break
            else:
                # 等待提示仅打印一次，避免进程未启动时刷屏
                if not waiting_logged:
                    LOGGER.info("正在等待目标进程运行")
                    waiting_logged = True
                await asyncio.sleep(wait_process_check_interval_ms / 1000)
    except asyncio.CancelledError:
        LOGGER.critical("任务被取消，退出等待进程启动循环")
        return

    # 超过等待时间或进程已启动
    if not processes:
        # 超过等待时间且进程未启动
        LOGGER.debug("执行等待进程启动超时报告与推送")
        waited_time_ms = time.perf_counter() * 1000 - start_time_ms
        formatted_waited_time = format_time_ms(waited_time_ms)
        LOGGER.error(f"等待超时，进程未运行: {process_name}")
        await send_notification(
            config,
            'process_wait_timeout_warning',
            process_name=process_name,
            process_wait_time=formatted_waited_time
        )

        # 执行外部程序
        if external_program_on_wait_timeout_path:
            LOGGER.info("等待进程启动超时，正在执行外部程序...")
            try:
                await asyncio.to_thread(
                    run_external_program,
                    external_program_on_wait_timeout_path)
                LOGGER.info(
                    f"外部程序 {external_program_on_wait_timeout_path} "
                    f"执行成功")
            except Exception as e:
                LOGGER.error(
                    f"执行外部程序 {external_program_on_wait_timeout_path} "
                    f"时发生错误: {e}",
                    exc_info=True
                )
    else:
        LOGGER.info("所有监视进程均已启动")

    # 如果没有任何进程需要监视，退出程序
    if not processes:
        LOGGER.critical("未检测到任意目标进程，程序终止运行。")
        sys.exit(1)

    # 监视已启动的进程
    LOGGER.info(
        f"已进入监视循环，每 {monitor_loop_interval_ms} ms 循环一次"
    )
    next_loop_time_ms = time.perf_counter() * 1000
    try:
        while processes:
            LOGGER.debug("执行监视循环")
            current_time_ms = time.perf_counter() * 1000
            current_processes = _collect_matching_processes(process_name)
            monitored_pids = set(processes.keys())

            # 检查进程结束（含 PID 复用检测）
            ended_pids = _detect_ended_pids(
                monitored_pids, current_processes, processes)

            for pid in ended_pids:
                process_info = processes[pid]
                run_time_ms = current_time_ms - process_info['start_time_ms']
                await _handle_process_end(
                    config,
                    process_info['name'],
                    pid,
                    run_time_ms,
                    external_program_path,
                )
                # 从监视列表中移除
                del processes[pid]
                LOGGER.info(f"已删除进程记录: {pid}")

            # 检查超时警告
            for pid, process_info in list(processes.items()):
                await _check_process_timeout(
                    config,
                    process_info,
                    pid,
                    current_time_ms,
                    timeout_warning_interval_ms,
                    another_external_program_path,
                    timeout_count_threshold,
                )

            if not processes:
                LOGGER.info("所有被监视进程已结束运行。")
                break

            # 精确节拍补偿
            now_ms = time.perf_counter() * 1000
            sleep_time_ms = next_loop_time_ms - now_ms
            if sleep_time_ms < 0:
                sleep_time_ms = 0
                next_loop_time_ms = now_ms
            await asyncio.sleep(sleep_time_ms / 1000)
            next_loop_time_ms += monitor_loop_interval_ms
    except asyncio.CancelledError:
        LOGGER.critical("任务被取消，正在结束监视循环")
        return


async def monitor_via_task_scheduler(config):
    """通过计划任务事件日志获取 PID 后监视进程。

    查询 Windows 事件日志 Event 129（进程创建）获取 PID，
    通过 psutil.Process(pid) + create_time 校验精确监视该 PID，
    避免同名进程的误判问题。

    阶段1: 等待计划任务触发（轮询 Event 129）
    阶段2: 监视 PID 存活状态，处理超时和结束

    Args:
        config (dict): 配置信息。
    """
    task_monitor = config.get('task_monitor_settings', {})
    external_settings = config.get('external_program_settings', {})
    monitor_settings = config.get('monitor_settings', {})
    wait_settings = config.get('wait_process_settings', {})

    task_name = task_monitor.get('task_name', '')
    lookback_minutes = task_monitor.get('lookback_minutes', 10)
    wait_check_interval_str = wait_settings.get(
        'wait_process_check_interval', '1s')
    monitor_loop_interval_str = monitor_settings.get(
        'monitor_loop_interval', '1s')
    timeout_warning_interval_str = monitor_settings.get(
        'timeout_warning_interval', '15m')

    wait_check_interval_ms = parse_time_string(wait_check_interval_str)
    monitor_loop_interval_ms = parse_time_string(monitor_loop_interval_str)
    timeout_warning_interval_ms = parse_time_string(
        timeout_warning_interval_str)
    # 最长等待时间：与 psutil 模式一致，使其对 task_scheduler 模式同样生效
    max_wait_time_ms = parse_time_string(wait_settings.get(
        'max_wait_time',
        DEFAULT_VALUES['wait_process_settings']['max_wait_time']))

    external_program_path = external_settings.get('external_program_path', '')
    another_external_program_path = external_settings.get(
        'another_external_program_path', '')
    timeout_count_threshold = external_settings.get(
        'timeout_count_threshold', 3)
    external_program_on_wait_timeout_path = external_settings.get(
        'external_program_on_wait_timeout_path', '')

    if not task_name:
        LOGGER.critical("未设置要监视的计划任务名称，请检查配置文件！")
        sys.exit(1)

    LOGGER.info(
        f"等待计划任务 '{task_name}' 触发，"
        f"每 {wait_check_interval_ms}ms 检查一次"
    )

    pid_info = None
    # 等待提示仅打印一次，避免无任务触发时刷屏
    waiting_logged = False
    wait_start_time_ms = time.perf_counter() * 1000

    # 阶段1: 等待计划任务启动（通过事件日志查询 Event 129）
    try:
        while True:
            # 等待超时判断：超过最长等待时间则退出等待
            waited_time_ms = time.perf_counter() * 1000 - wait_start_time_ms
            if waited_time_ms > max_wait_time_ms:
                LOGGER.debug("等待计划任务触发已超时")
                break

            # 将同步的事件日志查询放入线程池，避免阻塞事件循环
            result = await asyncio.to_thread(
                query_task_pid, task_name, lookback_minutes)

            if result['state'] == 'error':
                LOGGER.warning("查询事件日志出错，将在下次循环重试")
                await asyncio.sleep(wait_check_interval_ms / 1000)
                continue

            if result['state'] == 'running':
                pid = result['pid']
                process_name = result['process_name'] or task_name
                # 用 psutil.Process 记录 create_time，防止 PID 复用误判
                try:
                    proc = psutil.Process(pid)
                    create_time = proc.create_time()
                    start_time_ms = (
                        time.perf_counter() * 1000
                        - (time.time() - create_time) * 1000
                    )
                except psutil.NoSuchProcess:
                    # 进程在检测到和获取 create_time 之间已退出，重试
                    LOGGER.warning(f"PID {pid} 在获取进程信息前已退出，重试")
                    await asyncio.sleep(wait_check_interval_ms / 1000)
                    continue
                current_time_ms = time.perf_counter() * 1000
                pid_info = {
                    'pid': pid,
                    'name': process_name,
                    'create_time': create_time,
                    'start_time_ms': start_time_ms,
                    'last_warning_time_ms': current_time_ms,
                    'timeout_count': 0,
                }
                LOGGER.info(
                    f"检测到计划任务进程: {process_name} (PID: {pid})"
                )
                break

            # state == 'not_found': 任务尚未触发，继续等待（提示仅打印一次）
            if not waiting_logged:
                LOGGER.info(
                    f"等待计划任务 '{task_name}' 触发中..."
                )
                waiting_logged = True
            await asyncio.sleep(wait_check_interval_ms / 1000)
    except asyncio.CancelledError:
        LOGGER.critical("任务被取消，退出等待计划任务循环")
        return

    if pid_info is None:
        # 等待超时且未捕获到计划任务进程
        waited_time_ms = time.perf_counter() * 1000 - wait_start_time_ms
        formatted_waited_time = format_time_ms(waited_time_ms)
        LOGGER.error(f"等待超时，计划任务未触发: {task_name}")
        await send_notification(
            config,
            'process_wait_timeout_warning',
            process_name=task_name,
            process_wait_time=formatted_waited_time
        )

        # 执行等待超时外部程序
        if external_program_on_wait_timeout_path:
            LOGGER.info("等待计划任务触发超时，正在执行外部程序...")
            try:
                await asyncio.to_thread(
                    run_external_program,
                    external_program_on_wait_timeout_path)
                LOGGER.info(
                    f"外部程序 {external_program_on_wait_timeout_path} "
                    f"执行成功")
            except Exception as e:
                LOGGER.error(
                    f"执行外部程序 {external_program_on_wait_timeout_path} "
                    f"时发生错误: {e}",
                    exc_info=True
                )

        LOGGER.critical("未能获取有效的 PID 信息，程序终止运行。")
        sys.exit(1)

    # 阶段2: 监视 PID 存活状态
    LOGGER.info(
        f"已进入监视循环，每 {monitor_loop_interval_ms}ms 检查一次 PID"
    )

    next_loop_time_ms = time.perf_counter() * 1000
    try:
        while True:
            # 精确节拍补偿
            now_ms = time.perf_counter() * 1000
            sleep_time_ms = next_loop_time_ms - now_ms
            if sleep_time_ms < 0:
                sleep_time_ms = 0
                next_loop_time_ms = now_ms
            await asyncio.sleep(sleep_time_ms / 1000)
            next_loop_time_ms += monitor_loop_interval_ms

            pid = pid_info['pid']
            current_time_ms = time.perf_counter() * 1000

            # 检查 PID 是否存活，并校验 create_time 防止 PID 复用
            alive = False
            try:
                proc = psutil.Process(pid)
                if proc.create_time() == pid_info['create_time']:
                    alive = True
            except psutil.NoSuchProcess:
                alive = False

            if alive:
                # PID 仍在运行，检查超时
                await _check_process_timeout(
                    config,
                    pid_info,
                    pid,
                    current_time_ms,
                    timeout_warning_interval_ms,
                    another_external_program_path,
                    timeout_count_threshold,
                )
            else:
                # PID 已不存在，进程已结束
                run_time_ms = current_time_ms - pid_info['start_time_ms']
                await _handle_process_end(
                    config,
                    pid_info['name'],
                    pid,
                    run_time_ms,
                    external_program_path,
                )
                LOGGER.info("被监视进程已结束运行。")
                break

    except asyncio.CancelledError:
        LOGGER.critical("任务被取消，正在结束监视循环")
        return
