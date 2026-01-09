#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
# Running-Runtime Process Monitoring

    - 本项目使用 GPT AI 生成，GPT 模型: o1-preview

- 版本: v1.10.3

## License

            DO WHAT THE FUCK YOU WANT TO PUBLIC LICENSE
                    Version 2, December 2004

 Copyright (C) 2004 Sam Hocevar <sam@hocevar.net>

 Everyone is permitted to copy and distribute verbatim or modified
 copies of this license document, and changing it is allowed as long
 as the name is changed.

            DO WHAT THE FUCK YOU WANT TO PUBLIC LICENSE
   TERMS AND CONDITIONS FOR COPYING, DISTRIBUTION AND MODIFICATION

  0. You just DO WHAT THE FUCK YOU WANT TO.

"""

import psutil
import time
import os
import sys
from datetime import datetime
import logging
from serverchan_sdk import sc_send as serverchan_sc_send

# ========================= 配置部分 =========================

# 日志输出设置
# 是否将日志输出到文件，True 为输出LOG文件，False 为不输出LOG文件
LOG_TO_FILE = True

# 自定义日志输出目录，例如 'C:\\path\\2rpm\\logs'
# 默认为程序目录下的'logs'文件夹
LOG_DIR = 'logs'

# 日志保留天数，默认3天
LOG_RETENTION_DAYS = 3

# 最大日志文件数量，默认15个
MAX_LOG_FILES = 15

# 超时警告间隔（以秒为单位，推送只推分钟），默认15分钟
ALERT_INTERVAL_SECONDS = 900
# 计算公式：60秒x分钟，下列为预设
# 60   - 1分钟
# 300  - 5分钟
# 600  - 10分钟
# 900  - 15分钟
# 1200 - 20分钟

# 监视循环的睡眠间隔（以毫秒为单位），默认每3秒检查一次
SLEEP_INTERVAL = 3000

# 通知密钥，请替换为您的ServerChan密钥
NOTIFICATION_KEY = 'your_ServerChan_key'

# 要监视的进程名称列表，示例：['notepad.exe', 'chrome.exe']
PROCESS_NAMES = ['your_process.exe']

# 配置推送错误重试
# 重试间隔，单位为秒
RETRY_INTERVAL_SECONDS = 1
# 最大重试次数
MAX_RETRIES = 3

# 等待进程启动的最长时间（秒），默认30秒
PROCESS_START_WAIT_TIME = 30

# 等待进程启动时的检查间隔（以毫秒为单位），默认每秒检查一次
PROCESS_CHECK_INTERVAL = 1000

# 通知模板可调用参数列表:
# {hostname} : 主机名
# {current_time} : 当前时间
# {short_current_time} : 当前时间（短格式）
# {process_name} : 进程名称
# {process_pid} : 进程PID
# {run_time} : 运行时间
# {total_minutes} : 总运行分钟数
# {process_list} : 未运行的进程列表
# {other_processes} : 其他被监视的进程状态
# {wait_time} : 等待的时间（格式为 HH:MM:SS）

# 各模板可用参数:
# 'send_no_process_found':
#   - {hostname}, {current_time}, {process_list}, {wait_time}
# 'send_process_end':
#   - {hostname}, {current_time}, {short_current_time},
#     {process_name}, {process_pid}, {run_time},
#     {other_processes}, {wait_time}
# 'send_alert':
#   - {hostname}, {current_time}, {process_name},
#     {process_pid}, {total_minutes}, {run_time}

# 配置通知模板
NOTIFICATION_TEMPLATES = {
    'send_no_process_found': {
        'title': '等待超时未运行报告',
        'template': (
            "**警告**：在等待 **{wait_time}** 后依旧未检测到指定进程：\n\n"
            "{process_list}\n\n"
            "**主机**：{hostname}\n\n"
            "**当前时间**：{current_time}\n\n"
        )
    },
    'send_process_end': {
        'title': '进程结束报告',
        'template': (
            "**{process_name}** (PID: {process_pid}) "
            "于 **{short_current_time}** 时运行结束。\n\n"
            "**主机**：{hostname}\n\n"
            "**当前时间**：{current_time}\n\n"
            "**等待运行时间**：{wait_time}\n\n"
            "**累计运行时间**：{run_time}\n\n"
            "### 其他进程状态：\n"
            "{other_processes}"
        )
    },
    'send_alert': {
        'title': '进程运行时间超过预期计划',
        'template': (
            "**注意**：**{process_name}** (PID: {process_pid}) "
            "已运行超过 **{total_minutes}** 分钟。\n\n"
            "**主机**：{hostname}\n\n"
            "**当前时间**：{current_time}\n\n"
            "**已运行时间**：{run_time}"
        )
    }
}

# ========================= 日志配置部分 =========================

# 设置输出为 UTF-8 编码，避免中文乱码
if sys.version_info >= (3, 7):
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

logger = logging.getLogger('ProcessMonitorLogger')
logger.setLevel(logging.INFO)  # 设置日志级别

# 日志格式设置
# 文件日志格式：'%(asctime)s | %(levelname)s │ %(message)s'
formatter_file = logging.Formatter(
    '%(asctime)s | %(levelname)s │ %(message)s'
)

# 控制台日志格式：'%(levelname)s │ %(asctime)s │ %(message)s'，
# 时间格式为 HH:MM:SS.SSS
formatter_console = logging.Formatter(
    '%(levelname)s │ %(asctime)s.%(msecs)03d │ %(message)s',
    datefmt='%H:%M:%S'
)

# 设置控制台日志处理器
handler_console = logging.StreamHandler()
handler_console.setLevel(logging.INFO)
handler_console.setFormatter(formatter_console)
logger.addHandler(handler_console)

# 设置文件日志处理器
if LOG_TO_FILE:
    # 获取日志输出目录，默认为脚本所在目录下的 logs 文件夹
    if not os.path.isabs(LOG_DIR):
        script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        log_dir = os.path.join(script_dir, LOG_DIR)
    else:
        log_dir = LOG_DIR

    # 确保日志目录存在
    os.makedirs(log_dir, exist_ok=True)

    # 日志文件名，包含时间戳（格式：2023-07-01_12-00-00）
    log_filename = f"2RPM_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
    # 日志文件路径
    log_file = os.path.join(log_dir, log_filename)

    # 创建文件处理器
    handler_file = logging.FileHandler(log_file, encoding='utf-8')
    handler_file.setLevel(logging.INFO)
    handler_file.setFormatter(formatter_file)
    logger.addHandler(handler_file)

    logger.info(f"日志将输出到文件：{log_file}")

    # 删除过期或超过数量限制的日志文件
    def clean_old_logs(log_dir, retention_days=LOG_RETENTION_DAYS,
                       max_files=MAX_LOG_FILES):
        """
        清理过期或超过数量限制的日志文件。

        Args:
            log_dir (str): 日志目录路径。
            retention_days (int): 保留天数。
            max_files (int): 最大日志文件数量。
        """
        now = time.time()
        log_files = []
        for filename in os.listdir(log_dir):
            file_path = os.path.join(log_dir, filename)
            if os.path.isfile(file_path):
                file_modified_time = os.path.getmtime(file_path)
                # 添加到列表中以便后续处理
                log_files.append((file_modified_time, file_path))
                # 删除超过 retention_days 天的日志文件
                if now - file_modified_time > retention_days * 86400:
                    os.remove(file_path)
                    logger.info(
                        f"日志清理：删除超过 {retention_days} 天的日志文件: {filename}"
                    )
                    log_files.remove((file_modified_time, file_path))

        # 按文件修改时间排序（从最旧到最新）
        log_files.sort()

        # 如果日志文件数量超过 max_files，删除最旧的文件
        while len(log_files) > max_files:
            oldest_file = log_files.pop(0)
            os.remove(oldest_file[1])
            filename = os.path.basename(oldest_file[1])
            logger.info(
                f"日志清理：删除超过最大数量限制的日志文件: {filename}"
            )

    clean_old_logs(log_dir)
else:
    logger.warning("已禁用日志文件输出")

# ========================= 函数定义部分 =========================


def get_current_time():
    """
    获取当前时间，格式为 'YYYY/MM/DD HH:MM:SS'

    Returns:
        str: 当前时间的字符串表示
    """
    return datetime.now().strftime('%Y/%m/%d %H:%M:%S')


def get_short_current_time():
    """
    获取当前时间的短格式，格式为 'HH:MM:SS'

    Returns:
        str: 当前时间的短格式字符串表示
    """
    return datetime.now().strftime('%H:%M:%S')


def get_hostname():
    """
    获取当前计算机的主机名

    Returns:
        str: 主机名字符串
    """
    return os.environ.get("COMPUTERNAME") or os.uname().nodename


def sc_send(title, desp, key=NOTIFICATION_KEY, options=None):
    """
    消息发送处理函数，通过 ServerChan 发送通知，包含错误重试机制

    Args:
        title (str): 通知标题
        desp (str): 通知内容
        key (str): 通知密钥
        options (dict, optional): 可选参数，如标签等

    Returns:
        dict: 发送结果
    """

    attempt = 0
    while attempt < MAX_RETRIES:
        try:
            response = serverchan_sc_send(key, title, desp, options or {})
            logger.info(f"推送成功：{title}")
            return response
        except Exception as e:
            attempt += 1
            logger.error(
                f"推送失败，重试：{attempt}/{MAX_RETRIES}；错误代码: {e}"
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_INTERVAL_SECONDS)
            else:
                logger.critical(
                    f"推送失败，已达到最大重试次数：{MAX_RETRIES}次；终止运行。"
                )
                sys.exit(1)  # 终止运行


def format_time(seconds):
    """
    将秒数转换为 'HH:MM:SS' 格式

    Args:
        seconds (float): 时间秒数

    Returns:
        str: 格式化后的时间字符串
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02}:{minutes:02}:{secs:02}"


def get_process_start_times(process_names):
    """
    获取要监视的进程的启动时间和名称

    Args:
        process_names (list): 要监视的进程名称列表

    Returns:
        dict: 包含进程信息的字典
    """
    process_start_times = {}
    for proc in psutil.process_iter(['pid', 'name', 'create_time']):
        try:
            if proc.info['name'] in process_names:
                process_start_times[proc.info['pid']] = {
                    # 进程创建时间
                    'create_time': proc.info['create_time'],
                    # 进程名称
                    'process_name': proc.info['name'],
                    # 用于记录最后一次发送警告的间隔次数
                    'last_alert_interval': 0
                }
                logger.info(
                    f"监视进程: {proc.info['name']} (PID: {proc.info['pid']})"
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            logger.error(
                f"无法访问进程: {proc.info['name']} (PID {proc.pid})"
            )
            continue
    return process_start_times


def send_process_end_notification(ended_process, monitored_processes, key,
                                  wait_time,
                                  template_info=NOTIFICATION_TEMPLATES[
                                   'send_process_end']):
    """
    发送进程结束的通知

    Args:
        ended_process (dict): 已结束的进程信息
        monitored_processes (dict): 当前被监视的进程状态
        key (str): 通知密钥
        wait_time (str): 等待时间（格式为 'HH:MM:SS'）
        template_info (dict): 通知模板信息
    """
    current_time = get_current_time()
    short_current_time = get_short_current_time()
    hostname = get_hostname()

    # 构建其他进程的状态
    other_lines = []
    for pid, proc in monitored_processes.items():
        if pid != ended_process['process_pid']:
            status = "运行中" if proc['running'] else "已结束"
            other_lines.append(
                f"- {proc['process_name']} (PID: {pid}) 状态：{status}"
            )
    other_processes = (
        "\n".join(other_lines)
        if other_lines else "无其他被监视的进程。"
    )

    # 填充模板
    desp = template_info['template'].format(
        hostname=hostname,                       # 主机名
        current_time=current_time,               # 当前时间
        short_current_time=short_current_time,   # 当前时间（短格式）
        process_name=ended_process['process_name'],    # 进程名称
        process_pid=ended_process['process_pid'],      # 进程PID
        run_time=ended_process['run_time'],      # 运行时间
        other_processes=other_processes,         # 其他被监视的进程状态
        wait_time=wait_time                      # 等待时间
    )

    # 发送通知
    ret = sc_send(
        title=template_info['title'],
        desp=desp,
        key=key
    )
    logger.info(
        f"推送结束通知: {ended_process['process_name']} "
        f"(PID: {ended_process['process_pid']})"
    )
    logger.info(f"推送结果: {ret}")


def send_no_process_found_notification(process_names, key, wait_time,
                                       template_info=NOTIFICATION_TEMPLATES[
                                        'send_no_process_found']):
    """
    发送未找到进程的通知并终止脚本

    Args:
        process_names (list): 未找到的进程名称列表
        key (str): 通知密钥
        wait_time (str): 等待时间（格式为 'HH:MM:SS'）
        template_info (dict): 通知模板信息
    """
    current_time = get_current_time()
    hostname = get_hostname()

    # 构建未找到的进程列表
    process_lines = [f"- {proc_name}" for proc_name in process_names]
    process_list = "\n".join(process_lines)

    # 填充模板
    desp = template_info['template'].format(
        hostname=hostname,           # 主机名
        current_time=current_time,   # 当前时间
        process_list=process_list,   # 未运行的进程列表
        wait_time=wait_time          # 等待时间
    )

    # 发送通知
    ret = sc_send(title=template_info['title'], desp=desp, key=key)
    logger.info("推送通知：未找到进程")
    logger.info(f"推送结果: {ret}")
    sys.exit(0)  # 终止运行


def send_alert_notification(process_name, process_pid, run_time_formatted,
                            total_minutes, key,
                            template_info=NOTIFICATION_TEMPLATES['send_alert']
                            ):
    """
    发送运行时间超过指定间隔的警告通知

    Args:
        process_name (str): 进程名称
        process_pid (int): 进程PID
        run_time_formatted (str): 格式化的运行时间
        total_minutes (int): 总运行分钟数
        key (str): 通知密钥
        template_info (dict): 通知模板信息
    """
    current_time = get_current_time()
    hostname = get_hostname()

    # 填充模板
    desp = template_info['template'].format(
        hostname=hostname,            # 主机名
        current_time=current_time,    # 当前时间
        process_name=process_name,    # 进程名称
        process_pid=process_pid,      # 进程PID
        total_minutes=total_minutes,  # 总运行分钟数
        run_time=run_time_formatted   # 运行时间
    )

    # 发送通知
    ret = sc_send(
        title=template_info['title'],
        desp=desp,
        key=key
    )
    logger.info(
        f"发送警告通知: {process_name} (PID: {process_pid}) "
        f"已运行超过 {total_minutes} 分钟"
    )
    logger.info(f"通知返回结果: {ret}")


def monitor_processes(process_start_times, key):
    """
    监视进程，并记录运行时间

    Args:
        process_start_times (dict): 进程启动时间信息
        key (str): 通知密钥
    """
    while True:
        current_time_epoch = time.time()  # 获取当前时间的时间戳
        for pid in list(process_start_times.keys()):
            try:
                proc = psutil.Process(pid)
                process_name = process_start_times[pid]['process_name']
                start_time = process_start_times[pid]['create_time']
                run_time = current_time_epoch - start_time

                # 检查运行时间是否超过 ALERT_INTERVAL_SECONDS
                if run_time > ALERT_INTERVAL_SECONDS:
                    # 计算已超过的 ALERT_INTERVAL_SECONDS 次数
                    intervals_passed = int(run_time // ALERT_INTERVAL_SECONDS)
                    last_notified_interval = (
                        process_start_times[pid].get(
                            'last_alert_interval', 0
                        )
                    )

                    if intervals_passed > last_notified_interval:
                        run_time_formatted = format_time(run_time)
                        total_minutes = (
                            intervals_passed * (ALERT_INTERVAL_SECONDS // 60)
                        )
                        # 发送警告通知
                        send_alert_notification(
                            process_name=process_name,
                            process_pid=pid,
                            run_time_formatted=run_time_formatted,
                            total_minutes=total_minutes,
                            key=key,
                            template_info=NOTIFICATION_TEMPLATES['send_alert']
                        )
                        # 更新已通知的时间
                        process_start_times[pid]['last_alert_interval'] = (
                            intervals_passed
                        )

                # 检查进程是否已结束
                if (not proc.is_running() or
                        proc.status() == psutil.STATUS_ZOMBIE):
                    short_current_time = get_short_current_time()
                    run_time_formatted = format_time(run_time)
                    logger.info(
                        f"进程：{process_name} (PID: {pid}) 已结束运行，"
                        f"运行时间: {run_time_formatted}"
                    )

                    # 添加到已结束进程信息
                    finished_process = {
                        'process_name': process_name,
                        'process_pid': pid,
                        'run_time': run_time_formatted,
                        'short_current_time': short_current_time
                    }

                    # 获取当前被监视进程的状态
                    monitored_processes_status = {}
                    for other_pid in process_start_times.keys():
                        if other_pid != pid:
                            other_proc_info = process_start_times[other_pid]
                            other_process_name = other_proc_info[
                                'process_name']
                            try:
                                other_proc = psutil.Process(other_pid)
                                is_running = (
                                    other_proc.is_running() and
                                    other_proc.status() != psutil.STATUS_ZOMBIE
                                )
                            except psutil.NoSuchProcess:
                                is_running = False
                            monitored_processes_status[other_pid] = {
                                'process_name': other_process_name,
                                'running': is_running
                            }

                    # 发送进程结束的通知
                    send_process_end_notification(
                        ended_process=finished_process,
                        monitored_processes=monitored_processes_status,
                        key=key,
                        wait_time=format_time(SLEEP_INTERVAL / 1000),
                        template_info=NOTIFICATION_TEMPLATES[
                            'send_process_end'
                        ]
                    )

                    # 从监视列表中移除该进程
                    process_start_times.pop(pid)

            except psutil.NoSuchProcess:
                # 进程已结束
                process_name = process_start_times[pid]['process_name']
                run_time = current_time_epoch - process_start_times[pid][
                    'create_time'
                ]
                run_time_formatted = format_time(run_time)
                short_current_time = get_short_current_time()
                logger.info(
                    f"进程：{process_name} (PID: {pid}) 已结束运行，"
                    f"运行时间: {run_time_formatted}"
                )

                # 添加到已结束进程信息
                finished_process = {
                    'process_name': process_name,
                    'process_pid': pid,
                    'run_time': run_time_formatted,
                    'short_current_time': short_current_time
                }

                # 获取当前被监视进程的状态
                monitored_processes_status = {}
                for other_pid in process_start_times.keys():
                    if other_pid != pid:
                        other_proc_info = process_start_times[other_pid]
                        other_process_name = other_proc_info['process_name']
                        try:
                            other_proc = psutil.Process(other_pid)
                            is_running = (
                                other_proc.is_running() and
                                other_proc.status() != psutil.STATUS_ZOMBIE
                            )
                        except psutil.NoSuchProcess:
                            is_running = False
                        monitored_processes_status[other_pid] = {
                            'process_name': other_process_name,
                            'running': is_running
                        }

                # 发送进程结束的通知
                send_process_end_notification(
                    ended_process=finished_process,
                    monitored_processes=monitored_processes_status,
                    key=key,
                    wait_time=format_time(SLEEP_INTERVAL / 1000),
                    template_info=NOTIFICATION_TEMPLATES[
                        'send_process_end'
                    ]
                )

                # 从监视列表中移除该进程
                process_start_times.pop(pid)

            except psutil.AccessDenied:
                # 无法访问进程信息，跳过
                logger.error(f"访问被拒绝：PID {pid}")
                continue

        # 检查是否所有进程都已结束
        if not process_start_times:
            logger.info("所有监视目标进程已结束运行。")
            sys.exit(0)  # 终止运行

        time.sleep(SLEEP_INTERVAL / 1000)


def print_info():
    """
    打印版本信息
    """
    print("+ " + " Running-Runtime Process Monitoring ".center(80, "="), "+")
    print("||" + "".center(80, " ") + "||")
    print("||" + "本项目使用 GPT AI，GPT 模型为：o1-preview".center(70, " ") + "||")
    print("|| " + "".center(78, "-") + " ||")
    print("||" + "Version: v1.10.3    License: WTFPL".center(80, " ") + "||")
    print("||" + "".center(80, " ") + "||")
    print("+ " + "".center(80, "=") + " +")


def main():
    """
    主函数，控制程序的执行流程
    """
    print_info()
    # 获取进程启动时间和名称
    process_start_times = get_process_start_times(PROCESS_NAMES)

    if not process_start_times:
        logger.warning("未发现目标进程，正在等待目标进程启动...")

        # 等待进程启动
        wait_time_elapsed = 0
        while wait_time_elapsed < PROCESS_START_WAIT_TIME:
            time.sleep(PROCESS_CHECK_INTERVAL / 1000)
            wait_time_elapsed += PROCESS_CHECK_INTERVAL / 1000

            # 再次检查进程是否启动
            process_start_times = get_process_start_times(PROCESS_NAMES)

            if process_start_times:
                formatted_wait_time = format_time(wait_time_elapsed)
                logger.info(
                    f"目标进程已启动，程序继续运行。等待时间："
                    f"{wait_time_elapsed} 秒"
                )
                break

        # 如果仍未找到进程，记录日志并终止程序
        if not process_start_times:
            formatted_wait_time = format_time(wait_time_elapsed)
            logger.error(
                f"等待超时，目标进程未启动，程序终止运行。"
                f"等待时间：{wait_time_elapsed} 秒"
            )
            # 发送通知，指定的进程未运行并终止脚本
            send_no_process_found_notification(
                PROCESS_NAMES,
                NOTIFICATION_KEY,
                wait_time=formatted_wait_time,
                template_info=NOTIFICATION_TEMPLATES[
                    'send_no_process_found'
                ]
            )
            sys.exit(0)

    logger.info("发现目标进程，正在监视...")

    # 监视进程
    monitor_processes(process_start_times, NOTIFICATION_KEY)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # 捕获 Ctrl+C 中断，正常退出
        logger.warning("捕获到 Ctrl+C 中断，程序终止运行。")
        logger.critical("用户手动终止。")
        sys.exit(0)
    except SystemExit:
        # 正常退出，或通过 sys.exit() 退出
        pass
    except Exception as e:
        logger.error(f"异常终止: {e}")
        logger.exception(f"{e}")
        sys.exit(1)
