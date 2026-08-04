#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from banner import print_info
from spinner import notify_fail, spinner_phase


BANNER_ART = r""" ____  _         _        ____ _   _ ___ 
/ ___|| |_ _   _| | ___  / ___| | | |_ _|
\___ \| __| | | | |/ _ \| |  _| | | || |
 ___) | |_| |_| | |  __/| |_| | |_| || |
|____/ \__|\__, |_|\___| \____|\___/|___|
            |___/"""


def main():
    print_info(
        app_name="Style GUI Demo",
        subtitle="Reusable Terminal UI",
        version="v1.0.0",
        license_name="MIT",
        banner_art=BANNER_ART,
    )
    try:
        with spinner_phase("程序正在初始化...") as sp:
            sp.text("正在加载配置...")
            sp.write_done("配置加载完成")
            sp.done("程序初始化完成")
    except Exception as exc:
        notify_fail(f"程序出现异常: {exc}", exc_info=True)
        raise


if __name__ == '__main__':
    main()
