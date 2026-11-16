# -*- mode: python ; coding: utf-8 -*-

"""2RPM GUI 独立 PyInstaller 配置。

与 CLI 的 ``2RPM.spec`` 分离：保留 Tk/Tcl、CustomTkinter 与 ctk-kit
的运行时数据、二进制，不沿用 CLI 排除 ``tkinter`` 的约定。
"""

import fnmatch
import os
import tkinter
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


def _filter_binaries(toc, patterns):
    """从 TOC 中移除文件名匹配 patterns 的二进制文件（仅排除来自非系统的路径）。

    Args:
        toc: PyInstaller TOC 列表。
        patterns: 文件名 glob 匹配模式列表。

    Returns:
        list: 过滤后的 TOC 列表。
    """
    filtered = []
    for item in toc:
        dest_name = os.path.basename(item[0]).lower()
        src_path = item[1]
        if any(fnmatch.fnmatch(dest_name, p) for p in patterns):
            if 'Java' in src_path or 'temurin' in src_path.lower():
                continue
        filtered.append(item)
    return filtered


current_dir = os.path.dirname(os.path.abspath(SPEC))

block_cipher = None

# 显式收集随 Python 安装的 Tcl/Tk 运行库，供无控制台 GUI 启动使用。
tcl_root = Path(tkinter.Tcl().eval('info library')).parent
tcl_tk_datas = [
    (str(tcl_root / f'tcl{tkinter.TclVersion}'), '_tcl_data'),
    (str(tcl_root / f'tk{tkinter.TkVersion}'), '_tk_data'),
]

tkinter_datas, tkinter_binaries, tkinter_hidden = collect_all('tkinter')
customtkinter_datas, customtkinter_binaries, customtkinter_hidden = collect_all('customtkinter')
ctk_kit_datas, ctk_kit_binaries, ctk_kit_hidden = collect_all('ctk_kit')

a = Analysis(
    ['2RPM_gui.py'],
    pathex=[current_dir],
    binaries=tkinter_binaries + customtkinter_binaries + ctk_kit_binaries,
    datas=tcl_tk_datas + tkinter_datas + customtkinter_datas + ctk_kit_datas,
    hiddenimports=[
        '_tkinter',
        'ctk_kit',
        'customtkinter',
        'psutil',
        'ruamel.yaml',
        'onepush',
        'colorama',
        'win32evtlog',
        'tkinter',
        'tkinter.filedialog',
        'tkinter.messagebox',
    ] + tkinter_hidden + customtkinter_hidden + ctk_kit_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'adbutils.__main__',
        'adbutils.pidcat',
        'apkutils',
        'altgraph',
        'astroid',
        'babel',
        'black',
        'boto',
        'boto3',
        'botocore',
        'cairo',
        'cryptography',
        'curses',
        'docutils',
        'flask',
        'gevent',
        'greenlet',
        'ipykernel',
        'IPython',
        'isort',
        'jinja2',
        'jupyter',
        'lib2to3',
        'markupsafe',
        'matplotlib',
        'numpy',
        'paramiko',
        'pexpect',
        'pip',
        'prompt_toolkit',
        'pytest',
        'pygame',
        'pygments',
        'pylint',
        'PyQt4',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'scipy',
        'setuptools',
        'sphinx',
        'sqlalchemy',
        'tornado',
        'unittest',
        'wheel',
        'wx',
        'zmq',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 过滤 runner 环境泄漏的 DLL（api-ms-win-*, ucrtbase）
a.binaries = _filter_binaries(
    a.binaries,
    ['api-ms-win-*.dll', 'ucrtbase.dll'],
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='2RPM-GUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['python3*.dll', 'VCRUNTIME*.dll', 'api-ms-win-*.dll'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
