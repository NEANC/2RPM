#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import colorama

# 版本号：发版前手动修改
VERSION = "v4.0.0"

# ASCII art 横幅
_BANNER_ART = r"""  ____   ____   ____   __  __ 
 |___ \ |  _ \ |  _ \ |  \/  |
   __) || |_) || |_) || |\/| |
  / __/ |  _ < |  __/ | |  | |
 |_____||_| \_\|_|    |_|  |_|"""

# 副标题与分隔线
_BANNER_SUBTITLE = " Running Runtime Process Monitoring"
_BANNER_DIVIDER = " " + "\u2500" * 49


def print_info() -> None:
    """打印 ASCII art 启动横幅。

    青色 art + 灰白色副标题 + 灰色分隔线 + 蓝色版本与许可证行，配色对齐
    pixi_alas_install.sh。colorama 已在控制台日志格式化器中初始化，
    此处直接使用颜色常量。
    """
    colorama.init(autoreset=True)
    print()
    print(colorama.Fore.CYAN + _BANNER_ART + colorama.Style.RESET_ALL)
    print()
    print(colorama.Fore.WHITE + _BANNER_SUBTITLE + colorama.Style.RESET_ALL)
    print(colorama.Fore.LIGHTBLACK_EX + _BANNER_DIVIDER + colorama.Style.RESET_ALL)
    print(colorama.Fore.BLUE + f" Version: {VERSION}     License: WTFPL" + colorama.Style.RESET_ALL)
    print()


def print_exit_info(exit_code: int) -> None:
    """打印程序结束时的简短状态提示（状态 + 退出码）。

    退出码为 0 时无输出（spinner 已提供完成反馈），非 0 显示红色异常提示。

    Args:
        exit_code (int): 程序退出码。
    """
    if exit_code == 0:
        return
    colorama.init(autoreset=True)
    print(colorama.Fore.RED + f"\u274c  监控异常退出 (exit {exit_code})" + colorama.Style.RESET_ALL)
