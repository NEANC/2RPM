#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import colorama

VERSION = "v0.0.0"
DEFAULT_BANNER_ART = r"""  ____  _         _        ____ _   _ ___ 
 / ___|| |_ _   _| | ___  / ___| | | |_ _|
 \___ \| __| | | | |/ _ \| |  _| | | || |
  ___) | |_| |_| | |  __/| |_| | |_| || |
 |____/ \__|\__, |_|\___| \____|\___/|___|
             |___/"""
_MIN_DIVIDER_WIDTH = 32


def _build_info_line(version, license_name):
    parts = []
    if version:
        parts.append(f"Version: {version}")
    if license_name:
        parts.append(f"License: {license_name}")
    return "     ".join(parts)


def _divider_width(app_name, subtitle, info_line):
    return max(_MIN_DIVIDER_WIDTH, len(app_name or ''), len(subtitle or ''), len(info_line or ''))


def print_info(
    app_name="Application",
    subtitle="Terminal Application",
    version=VERSION,
    license_name="",
    banner_art=DEFAULT_BANNER_ART,
):
    """打印可配置启动横幅。"""
    colorama.just_fix_windows_console()
    info_line = _build_info_line(version, license_name)
    width = _divider_width(app_name, subtitle, info_line)

    print()
    if banner_art:
        print(colorama.Fore.CYAN + banner_art + colorama.Style.RESET_ALL)
        print()
    if app_name:
        print(colorama.Fore.WHITE + app_name + colorama.Style.RESET_ALL)
    if subtitle:
        print(colorama.Fore.WHITE + subtitle + colorama.Style.RESET_ALL)
    if app_name or subtitle or info_line:
        print(colorama.Fore.LIGHTBLACK_EX + " " + "\u2500" * width + colorama.Style.RESET_ALL)
    if info_line:
        print(colorama.Fore.BLUE + f" {info_line}" + colorama.Style.RESET_ALL)
    print()
