#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import logging
import sys

from modules.utils import (
    format_time_ms,
    run_external_program,
    get_other_running_processes,
    parse_time_string,
    find_processes_by_name
)
from modules.notification import send_notification
from modules.config import DEFAULT_VALUES

LOGGER = logging.getLogger(__name__)


def monitor_processes(config):
    """监视进程列表。

    等待指定的进程启动，监视其运行状态，并在进程结束或超时时发送通知。

    Args:
        config (dict): 配置信息。
    """
    monitor_settings = config.get('monitor_settings', {})
    wait_settings = config.get('wait_process_settings', {})
    external_settings = config.get('external_program_settings', {})
    push_settings = config.get('push_settings', {})

    process_name = monitor_settings.get('process_name', DEFAULT_VALUES['monitor_settings']['process_name'])
    timeout_warning_interval_ms = parse_time_string(monitor_settings.get(
        'timeout_warning_interval', DEFAULT_VALUES['monitor_settings']['timeout_warning_interval']))
    monitor_loop_interval_ms = parse_time_string(monitor_settings.get(
        'monitor_loop_interval', DEFAULT_VALUES['monitor_settings']['monitor_loop_interval']))

    max_wait_time_ms = parse_time_string(wait_settings.get('max_wait_time', DEFAULT_VALUES['wait_process_settings']['max_wait_time']))
    wait_process_check_interval_ms = parse_time_string(wait_settings.get(
        'wait_process_check_interval', DEFAULT_VALUES['wait_process_settings']['wait_process_check_interval']))

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

    try:
        # 等待进程启动
        while True:
            LOGGER.debug("执行等待进程启动循环")
            waited_time_ms = time.perf_counter() * 1000 - start_time_ms
            # 检查是否超时
            if waited_time_ms > max_wait_time_ms:
                LOGGER.debug("已等待超时，正在尝试发送通知")
                break

            # 使用优化的进程查找函数
            found_processes = find_processes_by_name(process_name)

            for pid, info in found_processes.items():
                if pid not in processes:
                    start_time_offset_ms = time.perf_counter() * 1000 - int(
                        (time.time() - info['create_time']) * 1000
                    )
                    processes[pid] = {
                        'name': info['name'],
                        'start_time_ms': start_time_offset_ms,
                        'last_warning_time_ms': start_time_offset_ms,
                        'timeout_count': 0,
                    }
                    LOGGER.info(f"检测到进程启动: {info['name']} (PID: {pid})")

            # 检查是否进程已启动
            if processes:
                LOGGER.info("目标监视进程已启动")
                break
            else:
                LOGGER.info(
                    f"正在等待目标进程运行，已等待时间: "
                    f"{format_time_ms(waited_time_ms)}"
                )
                time.sleep(wait_process_check_interval_ms / 1000)
    except KeyboardInterrupt:
        LOGGER.critical("捕捉到 Ctrl+C，程序被手动终止")
        return

    # 超过等待时间或进程已启动
    if not processes:
        # 超过等待时间且进程未启动
        LOGGER.debug("执行等待进程启动超时报告与推送")
        waited_time_ms = time.perf_counter() * 1000 - start_time_ms
        formatted_waited_time = format_time_ms(waited_time_ms)
        send_notification(
            config,
            'process_wait_timeout_warning',
            process_name=process_name,
            process_wait_time=formatted_waited_time,
            other_running_processes=get_other_running_processes(
                processes),
            process_list=[process_name]
        )
        LOGGER.error(f"等待超时，进程未运行: {process_name}")

        # 执行外部程序
        if external_program_on_wait_timeout_path:
            LOGGER.info("等待进程启动超时，正在执行外部程序...")
            try:
                run_external_program(external_program_on_wait_timeout_path)
                LOGGER.info(
                    f"外部程序 {external_program_on_wait_timeout_path} 执行成功")
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
            
            # 使用优化的进程查找函数
            current_processes = find_processes_by_name(process_name)
            current_pids = set(current_processes.keys())
            monitored_pids = set(processes.keys())

            # 检查进程结束
            ended_pids = monitored_pids - current_pids
            for pid in ended_pids:
                process_info = processes[pid]
                process_name_local = process_info['name']
                run_time_ms = current_time_ms - process_info['start_time_ms']
                formatted_run_time = format_time_ms(run_time_ms)

                send_notification(
                    config,
                    'process_end_notification',
                    process_name=process_name_local,
                    process_pid=pid,
                    process_run_time=formatted_run_time,
                    other_running_processes=get_other_running_processes(
                        processes, exclude_pid=pid),
                    process_list=[]
                )
                LOGGER.info(
                    f"进程结束: {process_name_local} (PID: {pid}) "
                    f"运行时间: {formatted_run_time}"
                )

                # 进程结束时调用外部程序
                if external_program_path:
                    LOGGER.info(
                        f"检测到进程 {process_name_local} 结束，正在调用外部程序..."
                    )
                    try:
                        run_external_program(external_program_path)
                        LOGGER.info(f"成功调用外部程序 {external_program_path}")
                    except Exception as e:
                        LOGGER.error(
                            f"调用外部程序 {external_program_path} 时发生错误: {e}",
                            exc_info=True
                        )

                # 从监视列表中移除
                del processes[pid]
                LOGGER.info(f"已删除进程记录: {pid}")

            # 更新运行时间，检查超时警告
            for pid, process_info in processes.items():
                run_time_ms = current_time_ms - process_info['start_time_ms']
                last_warning_time_ms = process_info.get(
                    'last_warning_time_ms', process_info['start_time_ms'])
                time_since_last_warning_ms = (
                    current_time_ms - last_warning_time_ms
                )

                if time_since_last_warning_ms >= timeout_warning_interval_ms:
                    formatted_run_time = format_time_ms(run_time_ms)
                    send_notification(
                        config,
                        'process_timeout_warning',
                        process_name=process_info['name'],
                        process_pid=pid,
                        process_run_time=formatted_run_time,
                        other_running_processes=get_other_running_processes(
                            processes, exclude_pid=pid),
                        process_list=[]
                    )
                    LOGGER.warning(
                        f"进程 {process_info['name']} (PID: {pid}) "
                        f"已运行超时 {formatted_run_time}"
                    )
                    process_info['last_warning_time_ms'] = current_time_ms
                    process_info['timeout_count'] += 1

                    # 检查是否需要执行外部程序
                    if (another_external_program_path and
                            process_info['timeout_count'] %
                            timeout_count_threshold == 0):
                        LOGGER.info(
                            f"进程 {process_info['name']} (PID: {pid})，\r\n"
                            f"超时次数达到阈值 {timeout_count_threshold}，"
                            f"正在调用外部程序..."
                        )
                        try:
                            run_external_program(
                                another_external_program_path)
                            LOGGER.info(
                                f"外部程序 {another_external_program_path} "
                                f"执行成功"
                            )
                            # 默认退出程序
                            LOGGER.critical(
                                "外部程序执行完成，正在结束运行"
                            )
                            sys.exit(0)
                        except Exception as e:
                            LOGGER.error(
                                f"调用外部程序 {another_external_program_path} "
                                f"时发生错误: {e}",
                                exc_info=True
                            )

            if not processes:
                LOGGER.info("所有被监视进程已结束运行。")
                break

            # 计算下一次循环的时间点，确保循环间隔精确
            now_ms = time.perf_counter() * 1000
            sleep_time_ms = next_loop_time_ms - now_ms
            if sleep_time_ms < 0:
                sleep_time_ms = 0
                next_loop_time_ms = now_ms
            time.sleep(sleep_time_ms / 1000)
            next_loop_time_ms += monitor_loop_interval_ms
    except KeyboardInterrupt:
        LOGGER.critical("捕捉到 Ctrl+C，正在结束监视循环")
        return