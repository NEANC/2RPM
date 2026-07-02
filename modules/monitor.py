#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import shlex
import subprocess
import time
import datetime
import logging
import sys
import psutil

from modules.utils import (
    run_external_program,
    parse_time_string
)
from modules.notification import send_notification
from modules.config import DEFAULT_VALUES
from modules.task_monitor import query_task_pid
from modules.spinner import spinner_phase, notify_fail

LOGGER = logging.getLogger(__name__)


class ProcessTimeoutExitRequested(Exception):
    """超时阈值到达并成功执行外部程序后，要求停止当前进程监视。"""

    def __init__(self, message: str, pid: int = None):
        super().__init__(message)
        self.pid = pid


def _get_timeout_count_threshold(raw_threshold):
    """规范化外部程序触发阈值，确保为正整数。

    将配置值转换为 int 后，保证阈值 >= 1。
    当阈值无效或小于 1 时使用默认值，避免 modulo 零除与异常行为。

    Args:
        raw_threshold: 配置读取到的原始阈值。

    Returns:
        int: 合法化后的阈值。
    """
    default_threshold = DEFAULT_VALUES['external']['timeout_threshold']
    try:
        threshold = int(raw_threshold)
    except (TypeError, ValueError):
        LOGGER.warning(
            "external.timeout_threshold 配置无效，"
            f"将使用默认值 {default_threshold}"
        )
        return default_threshold

    if threshold < 1:
        LOGGER.warning(
            "external.timeout_threshold 配置必须为 >= 1，"
            f"当前值 {threshold} 无效，已使用默认值 {default_threshold}"
        )
        return default_threshold

    return threshold


def _parse_time_or_default(raw_value, default_value):
    """解析时间字符串为秒，解析失败时回退默认值。

    当配置项存在但值非法（如非时间格式字符串）时，parse_time_string 会
    抛出 ValueError。本函数捕获该异常并回退到默认值，避免单个时间项填错
    导致整个程序退出。

    Args:
        raw_value: 配置读取到的原始时间值。
        default_value (str): 解析失败时回退的默认时间字符串。

    Returns:
        int: 解析后的秒数；解析失败时返回默认值对应的秒数。
    """
    try:
        return parse_time_string(raw_value)
    except (ValueError, AttributeError, TypeError):
        LOGGER.warning(
            f"时间配置值无效: {raw_value!r}，已回退默认值 {default_value!r}"
        )
        return parse_time_string(default_value)


def _get_program_name(program_path):
    """获取程序的文件名部分。

    Args:
        program_path (str): 程序完整路径。

    Returns:
        str: 程序文件名。
    """
    return os.path.basename(program_path)


def _render_push_results(results, sp):
    """将各通道推送结果逐条内联渲染到 spinner，并判定是否全部失败。

    每个通道独立输出一条 ✔️/❌（不定格 spinner）；空结果（禁用、
    缺变量、无有效通道）不输出任何内容。

    Args:
        results (list[tuple[str, bool]]): send_notification 返回的各通道结果，
            元素为 (provider, 是否成功)。
        sp: spinner 句柄，用于内联反馈各通道成败（不定格）。

    Returns:
        bool: 结果非空且所有通道均失败时为 True，否则为 False。
    """
    if not results:
        return False
    for provider, ok in results:
        if ok:
            sp.write_done(f"通道 [{provider}] 推送成功")
        else:
            sp.write_fail(f"通道 [{provider}] 推送失败")
    return all(not ok for _, ok in results)


def _handle_process_end(config, process_name, pid, run_time,
                         external_on_end_path, sp):
    """处理进程结束：发送通知并可选调用外部程序

    Args:
        config (dict): 配置信息
        process_name (str): 进程名
        pid (int): 进程 PID
        run_time (float): 进程运行时间（秒）
        external_on_end_path (str): 进程结束时调用的外部程序路径
        sp: spinner 句柄，用于内联反馈各通道及外部程序成败（不定格）

    Returns:
        bool: 结束通知（on_end）所有通道均失败时为 True，否则为 False。
            供监视循环决定最终定格使用 sp.done 还是 sp.fail。
    """
    formatted_run_time = str(datetime.timedelta(seconds=int(run_time)))

    LOGGER.info(
        f"进程结束: {process_name} (PID: {pid}) "
        f"运行时间: {formatted_run_time}"
    )
    end_results = send_notification(
        config,
        'on_end',
        process_name=process_name,
        process_pid=pid,
        process_run_time=formatted_run_time
    )
    all_failed = _render_push_results(end_results, sp)

    # 进程结束时调用外部程序
    if external_on_end_path:
        LOGGER.info(
            f"检测到进程 {process_name} 结束，正在调用外部程序..."
        )
        try:
            run_external_program(external_on_end_path)
            sp.write_done("外部程序执行完成")
            LOGGER.info(f"成功调用外部程序 {external_on_end_path}")
            # 发送外部程序执行通知
            ext_results = send_notification(
                config,
                'on_external',
                external_program_name=_get_program_name(external_on_end_path),
                external_program_path=external_on_end_path,
                process_name=process_name,
                process_pid=pid,
            )
            _render_push_results(ext_results, sp)
        except Exception as e:
            sp.write_fail("外部程序执行失败。")
            LOGGER.error(
                f"调用外部程序 {external_on_end_path} 时发生错误: {e}",
                exc_info=True
            )

    return all_failed


def _check_process_timeout(config, process_info, pid, current_time,
                            timeout_interval,
                            external_on_timeout_path,
                            timeout_threshold):
    """检查进程超时，发送警告并在达到阈值时触发外部程序。

    Args:
        config (dict): 配置信息
        process_info (dict): 进程监视信息，需包含 start_time / last_warning_time / timeout_count
        pid (int): 进程 PID
        current_time (float): 当前时间（秒，time.time()）
        timeout_interval (int): 超时警告间隔（秒）
        external_on_timeout_path (str): 超时后触发的外部程序路径
        timeout_threshold (int): 触发外部程序所需的超时累计次数阈值
    """
    run_time = current_time - process_info['start_time']
    time_since_last_warning = current_time - process_info['last_warning_time']

    if time_since_last_warning < timeout_interval:
        return

    formatted_run_time = str(datetime.timedelta(seconds=int(run_time)))
    process_name = process_info['name']
    LOGGER.warning(
        f"进程 {process_name} (PID: {pid}) "
        f"已运行超时 {formatted_run_time}"
    )
    # TODO: 本函数不持有 spinner 句柄且每轮循环重复触发，故丢弃返回值不做内联渲染，避免刷屏
    send_notification(
        config,
        'on_timeout',
        process_name=process_name,
        process_pid=pid,
        process_run_time=formatted_run_time
    )
    process_info['last_warning_time'] = current_time
    process_info['timeout_count'] += 1

    # 检查是否需要执行外部程序
    if (external_on_timeout_path and
            process_info['timeout_count'] % timeout_threshold == 0):
        LOGGER.info(
            f"进程 {process_name} (PID: {pid})，"
            f"超时次数达到阈值 {timeout_threshold}，"
            f"正在调用外部程序..."
        )
        try:
            run_external_program(external_on_timeout_path)
            LOGGER.info(
                f"外部程序 {external_on_timeout_path} "
                f"执行成功"
            )
            # 发送外部程序执行通知
            # TODO: 同上，本函数不持 spinner 句柄，丢弃返回值不做内联渲染
            send_notification(
                config,
                'on_external',
                external_program_name=_get_program_name(
                    external_on_timeout_path),
                external_program_path=external_on_timeout_path,
                process_name=process_name,
                process_pid=pid,
            )
            LOGGER.info("外部程序执行完成，已请求停止该进程监视")
            raise ProcessTimeoutExitRequested(
                f"进程 {process_name} (PID: {pid}) 超时次数达到阈值 {timeout_threshold}，已停止该进程监视",
                pid,
            )
        except ProcessTimeoutExitRequested:
            raise
        except Exception as e:
            LOGGER.error(
                f"调用外部程序 {external_on_timeout_path} "
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
            LOGGER.info(f"PID {pid} create_time 不匹配，判定为原进程已结束")
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
    processes[pid] = {
        'name': name,
        'create_time': create_time,
        'start_time': time.time(),
        'last_warning_time': time.time(),
        'timeout_count': 0,
    }
    LOGGER.info(f"检测到进程启动: {name} (PID: {pid})")


def _split_args(args_str):
    """将参数字符串拆分为列表，适配 subprocess.Popen。

    使用 shlex.split 正确处理引号包裹的参数。
    示例: '--path "C:\\Program Files\\tool.exe"' → ['--path', 'C:\\Program Files\\tool.exe']

    Args:
        args_str: 命令行参数字符串，可为 None 或空。

    Returns:
        list[str]: 拆分后的参数列表。
    """
    if not args_str or not args_str.strip():
        return []
    return shlex.split(args_str.strip())


def _resolve_cwd(config_cwd, program_path):
    """解析工作目录，用户未配置时从程序路径推导。

    Args:
        config_cwd: 用户配置的工作目录，可为 None 或空字符串。
        program_path (str): 程序完整路径。

    Returns:
        str: 解析后的工作目录绝对路径。
    """
    cwd = str(config_cwd).strip() if config_cwd else ''
    if cwd:
        return cwd
    return os.path.dirname(os.path.abspath(program_path)) or os.getcwd()


def _monitor_single_pid(pid_info, config, sp):
    """监视单个 PID 的存活状态，处理超时和结束。

    从 monitor_via_task_scheduler 阶段2 提取，供 launch 和
    task_scheduler 两种模式复用。

    Args:
        pid_info (dict): 包含 pid/name/create_time/start_time/
            last_warning_time/timeout_count 的字典。
        config (dict): 配置信息。
        sp: spinner 句柄。
    """
    monitor_section = config.get('monitor', {})
    external_section = config.get('external', {})

    loop_interval = _parse_time_or_default(
        monitor_section.get(
            'loop_interval',
            DEFAULT_VALUES['monitor']['loop_interval']),
        DEFAULT_VALUES['monitor']['loop_interval'])
    timeout_interval = _parse_time_or_default(
        monitor_section.get(
            'timeout_interval',
            DEFAULT_VALUES['monitor']['timeout_interval']),
        DEFAULT_VALUES['monitor']['timeout_interval'])
    external_on_timeout_path = external_section.get('on_timeout', '')
    external_on_end_path = external_section.get('on_end', '')
    timeout_threshold = _get_timeout_count_threshold(
        external_section.get('timeout_threshold', 3)
    )

    LOGGER.info(
        f"已进入监视循环，每 {loop_interval} 秒检查一次 PID"
    )

    while True:
        time.sleep(loop_interval)

        pid = pid_info['pid']
        current_time = time.time()

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
            try:
                _check_process_timeout(
                    config,
                    pid_info,
                    pid,
                    current_time,
                    timeout_interval,
                    external_on_timeout_path,
                    timeout_threshold,
                )
            except ProcessTimeoutExitRequested as e:
                LOGGER.info(
                    "on_timeout 外部程序已执行，停止监视 PID %d: %s",
                    pid, e
                )
                sp.write_done("已停止监视该进程")
                sp.done("超时外部程序已执行")
                break
        else:
            # PID 已不存在，进程已结束，进入通知推送阶段
            sp.write_done("进程已退出运行")
            sp.text("正在执行通知推送...")
            run_time = current_time - pid_info['start_time']
            all_failed = _handle_process_end(
                config,
                pid_info['name'],
                pid,
                run_time,
                external_on_end_path,
                sp,
            )
            LOGGER.info("被监视进程已结束运行。")
            if all_failed:
                sp.fail("任务进程已退出，但推送全部失败")
            else:
                sp.done("任务进程已退出")
            break


def _launch_program(launch_section):
    """拉起可执行程序并返回 PID 信息。

    Args:
        launch_section (dict): launch 配置节。

    Returns:
        dict: pid_info 字典。

    Raises:
        SystemExit: 如果拉起失败。
    """
    path = launch_section.get('path', '')
    if not path:
        notify_fail("launch.path 不能为空，请检查配置文件！")
        sys.exit(1)
    if not os.path.isfile(path):
        notify_fail(f"目标程序不存在: {path}")
        sys.exit(1)

    args_str = launch_section.get('args', '')
    cwd = _resolve_cwd(launch_section.get('cwd'), path)
    cmd = [path] + _split_args(args_str)

    LOGGER.info(f"正在拉起程序: {cmd}, cwd={cwd}")
    try:
        proc = subprocess.Popen(cmd, cwd=cwd)
    except Exception as e:
        notify_fail(f"拉起程序失败: {path}: {e}")
        sys.exit(1)

    pid = proc.pid
    try:
        create_time = proc.create_time()
    except psutil.AccessDenied:
        notify_fail(f"无法访问进程信息（权限不足），PID: {pid}")
        sys.exit(1)
    except psutil.NoSuchProcess:
        notify_fail(f"程序 {path} 启动后立即退出，PID: {pid}")
        sys.exit(1)

    current_time = time.time()
    process_name = os.path.basename(path)
    LOGGER.info(f"程序已拉起: {process_name} (PID: {pid})")
    return {
        'pid': pid,
        'name': process_name,
        'create_time': create_time,
        'start_time': current_time,
        'last_warning_time': current_time,
        'timeout_count': 0,
    }


def _launch_task(launch_section, config):
    """触发计划任务并等待 PID 出现。

    Args:
        launch_section (dict): launch 配置节。
        config (dict): 配置信息。

    Returns:
        dict: pid_info 字典。

    Raises:
        SystemExit: 如果触发失败或等待超时。
    """
    task_name = launch_section.get('task_name', '')
    if not task_name:
        notify_fail("type=task 时 launch.task_name 不能为空，请检查配置文件！")
        sys.exit(1)

    # 触发计划任务
    LOGGER.info(f"正在触发计划任务: {task_name}")
    try:
        subprocess.run(
            ['schtasks', '/run', '/tn', task_name],
            capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as e:
        LOGGER.error(f"触发计划任务失败: {e.stderr}")
        notify_fail(f"触发计划任务失败: {task_name}")
        sys.exit(1)

    # 等待 PID 出现
    wait_section = config.get('wait', {})
    max_wait = _parse_time_or_default(
        wait_section.get('max_wait', DEFAULT_VALUES['wait']['max_wait']),
        DEFAULT_VALUES['wait']['max_wait'])
    check_interval = _parse_time_or_default(
        wait_section.get('check_interval', DEFAULT_VALUES['wait']['check_interval']),
        DEFAULT_VALUES['wait']['check_interval'])

    LOGGER.info(
        f"等待计划任务进程启动，每 {check_interval} 秒检查一次"
    )
    wait_start_time = time.time()
    waiting_logged = False

    try:
        with spinner_phase("等待目标进程启动...") as sp:
            while True:
                waited_time = time.time() - wait_start_time
                if waited_time > max_wait:
                    LOGGER.info("等待计划任务进程超时")
                    sp.fail("等待超时，任务进程未启动")
                    break

                result = query_task_pid(task_name, lookback_minutes=1)

                if result['state'] == 'running':
                    pid = result['pid']
                    try:
                        proc = psutil.Process(pid)
                        create_time = proc.create_time()
                    except psutil.NoSuchProcess:
                        LOGGER.warning(
                            f"检测到 PID {pid} 但进程已退出，将重试"
                        )
                        time.sleep(check_interval)
                        continue
                    current_time = time.time()
                    pid_info = {
                        'pid': pid,
                        'name': result['process_name'] or task_name,
                        'create_time': create_time,
                        'start_time': current_time,
                        'last_warning_time': current_time,
                        'timeout_count': 0,
                    }
                    LOGGER.info(f"检测到任务进程: {result['process_name']} (PID: {pid})")
                    sp.done("任务进程已启动")
                    return pid_info

                if not waiting_logged:
                    LOGGER.info(f"等待计划任务 '{task_name}' 进程启动...")
                    waiting_logged = True
                sp.text("等待目标进程启动...")
                time.sleep(check_interval)
    except KeyboardInterrupt:
        notify_fail("任务被取消，退出等待循环")
        raise

    # 等待超时
    with spinner_phase("正在执行通知推送...") as sp:
        waited_time = time.time() - wait_start_time
        formatted_waited_time = str(datetime.timedelta(seconds=int(waited_time)))
        LOGGER.error(f"等待超时，计划任务进程未启动: {task_name}")
        wait_results = send_notification(
            config,
            'on_wait_timeout',
            process_name=task_name,
            process_wait_time=formatted_waited_time
        )
        wait_all_failed = _render_push_results(wait_results, sp)

        external_section = config.get('external', {})
        external_program_on_wait_timeout_path = external_section.get('on_wait_timeout', '')
        if external_program_on_wait_timeout_path:
            sp.text("正在执行外部程序...")
            LOGGER.info("等待进程启动超时，正在执行外部程序...")
            try:
                run_external_program(external_program_on_wait_timeout_path)
                sp.write_done("外部程序执行成功")
            except Exception as e:
                sp.write_fail("外部程序执行失败")
                LOGGER.error(f"执行外部程序失败: {e}", exc_info=True)

        if wait_all_failed:
            sp.fail("通知推送失败")
        else:
            sp.done("通知推送完成")

    notify_fail("未能获取有效的 PID 信息。")
    sys.exit(1)


def monitor_via_launch(config):
    """主动拉起目标程序或计划任务并监视其进程。

    根据 launch.type 决定拉起方式：
    - program: 用 subprocess.Popen 启动可执行文件
    - task: 触发 Windows 计划任务后轮询事件日志获取 PID

    Args:
        config (dict): 配置信息。
    """
    launch_section = config.get('launch', {})
    launch_type = launch_section.get('type', 'program')

    if launch_type not in ('program', 'task'):
        notify_fail(f"launch.type 无效: {launch_type}，仅支持 program/task")
        sys.exit(1)

    if launch_type == 'program':
        pid_info = _launch_program(launch_section)
    else:
        pid_info = _launch_task(launch_section, config)

    # 进入单 PID 监视循环
    try:
        with spinner_phase("监视进程运行中...") as sp:
            _monitor_single_pid(pid_info, config, sp)
    except KeyboardInterrupt:
        notify_fail("任务被取消，正在结束监视循环")
        raise


def monitor_processes(config):
    """监视进程列表。

    等待指定的进程启动，监视其运行状态，并在进程结束或超时时发送通知。

    Args:
        config (dict): 配置信息。
    """
    monitor_section = config.get('monitor', {})
    wait_section = config.get('wait', {})
    external_section = config.get('external', {})

    # 检查监视模式
    monitor_mode = monitor_section.get('monitor_mode', 'psutil')

    if monitor_mode == 'task_scheduler':
        LOGGER.info("读取计划任务来获取 PID 进行监视")
        monitor_via_task_scheduler(config)
        return

    if monitor_mode == 'launch':
        LOGGER.info("主动拉起目标程序或任务进行监视")
        monitor_via_launch(config)
        return

    process_name = monitor_section.get(
        'process_name',
        DEFAULT_VALUES['monitor']['process_name'])
    timeout_interval = _parse_time_or_default(
        monitor_section.get(
            'timeout_interval',
            DEFAULT_VALUES['monitor']['timeout_interval']),
        DEFAULT_VALUES['monitor']['timeout_interval'])
    loop_interval = _parse_time_or_default(
        monitor_section.get(
            'loop_interval',
            DEFAULT_VALUES['monitor']['loop_interval']),
        DEFAULT_VALUES['monitor']['loop_interval'])

    max_wait = _parse_time_or_default(
        wait_section.get(
            'max_wait',
            DEFAULT_VALUES['wait']['max_wait']),
        DEFAULT_VALUES['wait']['max_wait'])
    check_interval = _parse_time_or_default(
        wait_section.get(
            'check_interval',
            DEFAULT_VALUES['wait']['check_interval']),
        DEFAULT_VALUES['wait']['check_interval'])

    # 外部程序调用设置
    external_on_end_path = external_section.get('on_end', '')
    external_on_timeout_path = external_section.get(
        'on_timeout', '')
    timeout_threshold = _get_timeout_count_threshold(
        external_section.get('timeout_threshold', 3)
    )
    external_program_on_wait_timeout_path = external_section.get(
        'on_wait_timeout', '')

    LOGGER.info("初始化监视参数")
    processes = {}

    # 检查 process_name 是否有效
    if not process_name:
        notify_fail("未设置要监视的进程，请检查配置文件！")
        sys.exit(1)

    LOGGER.info(
        f"等待监视进程启动，每 {check_interval} 秒检查一次"
    )
    start_time = time.time()
    # 等待提示仅打印一次，避免进程未启动时刷屏
    waiting_logged = False

    try:
        with spinner_phase("等待目标进程启动...") as sp:
            # 等待进程启动
            while True:
                LOGGER.info("执行等待进程启动循环")
                waited_time = time.time() - start_time
                if waited_time > max_wait:
                    LOGGER.info("已等待超时，正在尝试发送通知")
                    sp.fail("等待超时，被监视进程未运行。")
                    break

                current_processes = _collect_matching_processes(process_name)

                for pid, info in current_processes.items():
                    _add_new_process(
                        processes, pid, info['name'], info['create_time'])

                # 检查是否进程已启动
                if processes:
                    LOGGER.info("目标监视进程已启动")
                    sp.done("进程已启动")
                    break
                else:
                    # 等待提示仅打印一次，避免进程未启动时刷屏
                    if not waiting_logged:
                        LOGGER.info("正在等待目标进程运行")
                        waiting_logged = True
                    sp.text("等待目标进程启动...")
                    time.sleep(check_interval)
    except KeyboardInterrupt:
        notify_fail("任务被取消，退出等待进程启动循环")
        raise

    # 超过等待时间或进程已启动
    if not processes:
        # 超过等待时间且进程未启动，包裹 spinner 避免报告/推送泄漏控台
        with spinner_phase("正在执行通知推送...") as sp:
            LOGGER.info("执行等待进程启动超时报告与推送")
            waited_time = time.time() - start_time
            formatted_waited_time = str(datetime.timedelta(seconds=int(waited_time)))
            LOGGER.error(f"等待超时，进程未运行: {process_name}")
            wait_results = send_notification(
                config,
                'on_wait_timeout',
                process_name=process_name,
                process_wait_time=formatted_waited_time
            )
            wait_all_failed = _render_push_results(wait_results, sp)

            # 执行外部程序
            if external_program_on_wait_timeout_path:
                sp.text("正在执行外部程序...")
                LOGGER.info("等待进程启动超时，正在执行外部程序...")
                try:
                    run_external_program(external_program_on_wait_timeout_path)
                    sp.write_done("外部程序执行成功。")
                    LOGGER.info(
                        f"外部程序 {external_program_on_wait_timeout_path} "
                        f"执行成功")
                except Exception as e:
                    sp.write_fail("外部程序执行失败。")
                    LOGGER.error(
                        f"执行外部程序 {external_program_on_wait_timeout_path} "
                        f"时发生错误: {e}",
                        exc_info=True
                    )
            # 推送全失败时最终定格降级为 fail
            if wait_all_failed:
                sp.fail("通知推送失败")
            else:
                sp.done("通知推送完成")
    else:
        LOGGER.info("所有监视进程均已启动")

    # 如果没有任何进程需要监视，退出程序
    if not processes:
        notify_fail("未检测到任意目标进程。")
        sys.exit(1)

    # 监视已启动的进程
    LOGGER.info(
        f"已进入监视循环，每 {loop_interval} 秒循环一次"
    )
    try:
        with spinner_phase("监视进程运行中...") as sp:
            while processes:
                LOGGER.info("执行监视循环")
                current_time = time.time()
                current_processes = _collect_matching_processes(process_name)
                monitored_pids = set(processes.keys())

                # 检查进程结束（含 PID 复用检测）
                ended_pids = _detect_ended_pids(
                    monitored_pids, current_processes, processes)

                # 进程退出后进入通知推送阶段，先输出定格行再切换文案
                if ended_pids:
                    sp.write_done("进程已退出运行")
                    sp.text("正在执行通知推送...")

                # 收集本轮各进程结束推送的全失败标志，供最终定格兜底
                batch_all_failed = []
                for pid in ended_pids:
                    process_info = processes[pid]
                    run_time = current_time - process_info['start_time']
                    batch_all_failed.append(_handle_process_end(
                        config,
                        process_info['name'],
                        pid,
                        run_time,
                        external_on_end_path,
                        sp,
                    ))
                    # 从监视列表中移除
                    del processes[pid]
                    LOGGER.info(f"已删除进程记录: {pid}")

                # 存活进程数变化时刷新 spinner 文案
                if ended_pids:
                    sp.text(f"监视进程运行中（存活 {len(processes)}）...")

                # 检查超时警告
                for pid, process_info in list(processes.items()):
                    try:
                        _check_process_timeout(
                            config,
                            process_info,
                            pid,
                            current_time,
                            timeout_interval,
                            external_on_timeout_path,
                            timeout_threshold,
                        )
                    except ProcessTimeoutExitRequested as e:
                        LOGGER.info(
                            "on_timeout 外部程序已执行，停止监视 PID %d: %s",
                            e.pid or pid, e
                        )
                        processes.pop(pid, None)

                if not processes:
                    LOGGER.info("所有被监视进程已结束运行。")
                    # 本轮所有结束推送均全失败时，最终定格降级为 fail
                    if batch_all_failed and all(batch_all_failed):
                        sp.fail("进程已全部退出，但推送全部失败")
                    else:
                        sp.done("进程已全部退出")
                    break

                # 朴素 sleep，无节拍补偿
                time.sleep(loop_interval)
    except KeyboardInterrupt:
        notify_fail("任务被取消，正在结束监视循环")
        raise


def monitor_via_task_scheduler(config):
    """通过计划任务事件日志获取 PID 后监视进程。

    查询 Windows 事件日志 Event 129（进程创建）获取 PID，
    通过 psutil.Process(pid) + create_time 校验精确监视该 PID，
    避免同名进程的误判问题。

    阶段1: 等待计划任务触发（轮询 Event 129）
    阶段2: 监视 PID 存活状态，处理超时和结束

    Args:
        config (dict): 配置信息。
    """
    task_section = config.get('task', {})
    external_section = config.get('external', {})
    monitor_section = config.get('monitor', {})
    wait_section = config.get('wait', {})

    task_name = task_section.get('task_name', '')
    lookback_minutes = task_section.get('lookback_minutes', 10)
    check_interval = _parse_time_or_default(
        wait_section.get(
            'check_interval',
            DEFAULT_VALUES['wait']['check_interval']),
        DEFAULT_VALUES['wait']['check_interval'])
    loop_interval = _parse_time_or_default(
        monitor_section.get(
            'loop_interval',
            DEFAULT_VALUES['monitor']['loop_interval']),
        DEFAULT_VALUES['monitor']['loop_interval'])
    timeout_interval = _parse_time_or_default(
        monitor_section.get(
            'timeout_interval',
            DEFAULT_VALUES['monitor']['timeout_interval']),
        DEFAULT_VALUES['monitor']['timeout_interval'])
    max_wait = _parse_time_or_default(
        wait_section.get(
            'max_wait',
            DEFAULT_VALUES['wait']['max_wait']),
        DEFAULT_VALUES['wait']['max_wait'])

    external_on_end_path = external_section.get('on_end', '')
    external_on_timeout_path = external_section.get('on_timeout', '')
    timeout_threshold = _get_timeout_count_threshold(
        external_section.get('timeout_threshold', 3)
    )
    external_program_on_wait_timeout_path = external_section.get(
        'on_wait_timeout', '')

    if not task_name:
        notify_fail("未设置要监视的计划任务名称，请检查配置文件！")
        sys.exit(1)

    LOGGER.info(
        f"等待计划任务 '{task_name}' 触发，"
        f"每 {check_interval} 秒检查一次"
    )

    pid_info = None
    # 等待提示仅打印一次，避免无任务触发时刷屏
    waiting_logged = False
    wait_start_time = time.time()

    # 阶段1: 等待计划任务启动（通过事件日志查询 Event 129）
    try:
        with spinner_phase("等待计划任务触发...") as sp:
            while True:
                # 等待超时判断：超过最长等待时间则退出等待
                waited_time = time.time() - wait_start_time
                if waited_time > max_wait:
                    LOGGER.info("等待计划任务触发已超时")
                    sp.fail("等待超时，计划任务未触发")
                    break

                # 直接同步调用事件日志查询
                result = query_task_pid(task_name, lookback_minutes)

                if result['state'] == 'error':
                    LOGGER.warning("查询事件日志出错，将在下次循环重试")
                    time.sleep(check_interval)
                    continue

                if result['state'] == 'running':
                    pid = result['pid']
                    process_name = result['process_name'] or task_name
                    # 用 psutil.Process 记录 create_time，防止 PID 复用误判
                    try:
                        proc = psutil.Process(pid)
                        create_time = proc.create_time()
                    except psutil.NoSuchProcess:
                        # 进程在检测到和获取 create_time 之间已退出，重试
                        LOGGER.warning(f"PID {pid} 在获取进程信息前已退出，重试")
                        time.sleep(check_interval)
                        continue
                    current_time = time.time()
                    pid_info = {
                        'pid': pid,
                        'name': process_name,
                        'create_time': create_time,
                        'start_time': current_time,
                        'last_warning_time': current_time,
                        'timeout_count': 0,
                    }
                    LOGGER.info(
                        f"检测到计划任务进程: {process_name} (PID: {pid})"
                    )
                    sp.done("任务已触发")
                    break

                # state == 'not_found': 任务尚未触发，继续等待（提示仅打印一次）
                if not waiting_logged:
                    LOGGER.info(
                        f"等待计划任务 '{task_name}' 触发中..."
                    )
                    waiting_logged = True
                sp.text("等待计划任务触发...")
                time.sleep(check_interval)
    except KeyboardInterrupt:
        notify_fail("任务被取消，退出等待计划任务触发循环")
        raise

    if pid_info is None:
        # 等待超时且未捕获到计划任务进程，包裹 spinner 避免报告/推送泄漏控台
        with spinner_phase("正在执行通知推送...") as sp:
            waited_time = time.time() - wait_start_time
            formatted_waited_time = str(datetime.timedelta(seconds=int(waited_time)))
            LOGGER.error(f"等待超时，计划任务未触发: {task_name}")
            wait_results = send_notification(
                config,
                'on_wait_timeout',
                process_name=task_name,
                process_wait_time=formatted_waited_time
            )
            wait_all_failed = _render_push_results(wait_results, sp)

            # 执行等待超时外部程序
            if external_program_on_wait_timeout_path:
                sp.text("正在执行外部程序...")
                LOGGER.info("等待计划任务触发超时，正在执行外部程序...")
                try:
                    run_external_program(external_program_on_wait_timeout_path)
                    sp.write_done("外部程序执行成功。")
                    LOGGER.info(
                        f"外部程序 {external_program_on_wait_timeout_path} "
                        f"执行成功")
                except Exception as e:
                    sp.write_fail("外部程序执行失败。")
                    LOGGER.error(
                        f"执行外部程序 {external_program_on_wait_timeout_path} "
                        f"时发生错误: {e}",
                        exc_info=True
                    )
            # 推送全失败时最终定格降级为 fail
            if wait_all_failed:
                sp.fail("通知推送失败")
            else:
                sp.done("通知推送完成")

        notify_fail("未能获取有效的 PID 信息。")
        sys.exit(1)

    # 阶段2: 监视 PID 存活状态（复用 _monitor_single_pid）
    try:
        with spinner_phase("监视任务进程中...") as sp:
            _monitor_single_pid(pid_info, config, sp)
    except KeyboardInterrupt:
        notify_fail("任务被取消，正在结束监视循环")
        raise
