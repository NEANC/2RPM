#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import logging
import psutil
import subprocess

LOGGER = logging.getLogger(__name__)


def get_program_directory():
    """获取程序所在的目录，兼容打包后的可执行文件。

    Returns:
        str: 程序所在的目录路径。
    """
    if getattr(sys, 'frozen', False):
        # 如果是被打包的可执行文件，使用可执行文件所在目录
        program_dir = os.path.dirname(sys.executable)
    else:
        # 使用主脚本所在目录
        program_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    return program_dir


def format_time_ms(milliseconds):
    """将毫秒转换为 HH:MM:SS 格式的字符串（移除毫秒精度）。

    Args:
        milliseconds (float): 毫秒数。

    Returns:
        str: 格式化后的时间字符串。
    """
    LOGGER.debug(f"格式化时间: {milliseconds} 毫秒")
    total_seconds = int(milliseconds // 1000)
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    formatted_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    LOGGER.debug(f"格式化后的时间: {formatted_time}")
    return formatted_time


def run_external_program(program_path):
    """运行外部程序。

    Args:
        program_path (str): 外部程序的路径。
    """
    LOGGER.info(f"正在调用外部程序: {program_path}")
    
    # 获取程序所在目录
    try:
        # program_dir = os.path.dirname(program_path) if os.path.dirname(program_path) else os.getcwd()
        # 检查路径是否存在
        if os.path.exists(program_path):
            # 如果是文件，获取其所在目录
            if os.path.isfile(program_path):
                dir_name = os.path.dirname(program_path)
            # 如果是目录，直接使用该目录
            else:
                dir_name = program_path
        else:
            # 路径不存在，尝试从路径字符串中提取目录
            dir_name = os.path.dirname(program_path)
        
        # 如果目录为空，使用当前工作目录
        if not dir_name:
            dir_name = os.getcwd()
        
        # 返回绝对路径
        program_dir = os.path.abspath(dir_name)
    except Exception as e:
        # 处理异常情况，返回当前工作目录
        LOGGER.error(f"路径处理错误: {str(e)}")
        program_dir = os.getcwd()
    
    LOGGER.debug(f"使用工作目录: {program_dir}")
    
    try:
        # 检查文件扩展名
        ext = os.path.splitext(program_path)[1].lower()
        
        if ext == '.bat' or ext == '.cmd':
            # 对于批处理文件，使用 cmd.exe /c 执行
            subprocess.Popen(['cmd.exe', '/c', program_path], shell=False, cwd=program_dir)
        else:
            # 对于其他文件（包括EXE），使用subprocess.Popen
            # 这样可以更好地控制执行环境，避免os.startfile的潜在问题
            subprocess.Popen([program_path], shell=False, cwd=program_dir)
        LOGGER.info(f"成功调用外部程序: {program_path}")
    except Exception as e:
        LOGGER.error(f"调用外部程序失败: {e}")
        # 尝试使用os.startfile作为备选方案
        try:
            os.startfile(program_path)
            LOGGER.info(f"使用备选方案成功调用外部程序: {program_path}")
        except Exception as e2:
            LOGGER.error(f"备选方案调用外部程序也失败: {e2}")


def get_other_running_processes(processes, exclude_pid=None):
    """获取其他正在运行的进程信息。

    Args:
        processes (dict): 当前监视的进程信息。
        exclude_pid (int, optional): 要排除的进程 PID。默认为 None。

    Returns:
        str: 其他正在运行的进程信息字符串。
    """
    LOGGER.debug("获取其他正在运行的进程信息")
    other_processes = [
        f"{info['name']} (PID: {pid})"
        for pid, info in processes.items() if pid != exclude_pid
    ]
    result = ', '.join(other_processes) if other_processes else '无'
    LOGGER.info(f"其他正在运行的进程信息: {result}")
    return result


def parse_time_string(time_str):
    """解析时间字符串为毫秒。

    Args:
        time_str (str): 时间字符串，格式如 "1h", "15m", "30s"。

    Returns:
        int: 转换后的毫秒数。

    Raises:
        ValueError: 如果时间字符串格式无效。
    """
    LOGGER.debug(f"解析时间字符串: {time_str}")
    time_str = time_str.strip()
    if not time_str:
        LOGGER.error("时间字符串不能为空")
        raise ValueError("时间字符串不能为空")
    
    units = {
        'h': 3600000,  # 1小时 = 3600000毫秒
        'm': 60000,    # 1分钟 = 60000毫秒
        's': 1000      # 1秒 = 1000毫秒
    }
    
    if time_str[-1] in units:
        value = int(time_str[:-1])
        unit = time_str[-1]
        milliseconds = value * units[unit]
        LOGGER.debug(f"解析结果: {milliseconds} 毫秒")
        return milliseconds
    else:
        # 尝试直接解析为整数（毫秒）
        try:
            milliseconds = int(time_str)
            LOGGER.debug(f"直接解析为毫秒: {milliseconds}")
            return milliseconds
        except ValueError:
            LOGGER.error(f"无效的时间格式: {time_str}，请使用 '1h', '15m', '30s'")
            raise ValueError(f"无效的时间格式: {time_str}，请使用 '1h', '15m', '30s'")


def find_processes_by_name(process_name):
    """按进程名称查找正在运行的进程(优化版本,避免全量扫描)。

    Args:
        process_name (str): 要查找的进程名称。

    Returns:
        dict: 进程字典,键为 PID,值为包含进程信息的字典。
              格式: {pid: {'name': str, 'create_time': float}}
    """
    LOGGER.debug(f"查找进程: {process_name}")
    found_processes = {}
    
    # 检查进程名称是否有效
    if not process_name:
        LOGGER.warning("进程名称为空，返回空字典")
        return found_processes
    
    # 仅迭代进程并立即过滤，避免创建包含所有进程的大型字典
    try:
        for proc in psutil.process_iter(['pid', 'name', 'create_time']):
            try:
                # 立即过滤:仅保留匹配的进程
                if proc.info['name'] == process_name:
                    found_processes[proc.info['pid']] = {
                        'name': proc.info['name'],
                        'create_time': proc.info['create_time']
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # 忽略已结束或无权限访问的进程
                continue
    except Exception as e:
        LOGGER.error(f"查找进程时发生错误: {e}")
    
    LOGGER.debug(f"找到 {len(found_processes)} 个匹配的进程")
    return found_processes