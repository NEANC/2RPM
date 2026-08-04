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


def print_info(app_name="Application", subtitle="Terminal Application", version=VERSION, license_name="", banner_art=DEFAULT_BANNER_ART):
    colorama.just_fix_windows_console()
    if banner_art:
        print(colorama.Fore.CYAN + banner_art + colorama.Style.RESET_ALL)
    if app_name:
        print(colorama.Fore.WHITE + app_name + colorama.Style.RESET_ALL)
    if subtitle:
        print(colorama.Fore.WHITE + subtitle + colorama.Style.RESET_ALL)
    info_parts = []
    if version:
        info_parts.append(f"Version: {version}")
    if license_name:
        info_parts.append(f"License: {license_name}")
    if info_parts:
        print(colorama.Fore.BLUE + "     ".join(info_parts) + colorama.Style.RESET_ALL)
