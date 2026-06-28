# -*- mode: python ; coding: utf-8 -*-

"""
2RPM 项目 PyInstaller 配置文件
用于将 Python 脚本打包为可执行文件
"""

import os
import fnmatch


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


# 获取当前 spec 文件所在目录
current_dir = os.path.dirname(os.path.abspath(SPEC))

block_cipher = None

a = Analysis(
    ['2RPM.py'],
    pathex=[current_dir],
    binaries=[],
    datas=[('modules', 'modules')],
    hiddenimports=[
        'psutil',
        'ruamel.yaml',
        'onepush',
        'serverchan_sdk',
        'colorama',
        'pywin32',
        'win32evtlog',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'altgraph',
        'astroid',
        'atomicwrites',
        'attrs',
        'babel',
        'bcrypt',
        'black',
        'blinker',
        'boto',
        'boto3',
        'botocore',
        'cairo',
        'cffi',
        'cryptography',
        'curses',
        'distutils',
        'docutils',
        'easy_install',
        'faulthandler',
        'flask',
        'future',
        'gevent',
        'greenlet',
        'h5py',
        'idlelib',
        'ipykernel',
        'IPython',
        'isort',
        'jinja2',
        'jupyter',
        'lib2to3',
        'markupsafe',
        'matplotlib',
        'mock',
        'multiprocessing',
        'nacl',
        'numpy',
        'paramiko',
        'pexpect',
        'pip',
        'pkg_resources',
        'prompt_toolkit',
        'ptyprocess',
        'pyasn1',
        'pycodestyle',
        'pycparser',
        'pyflakes',
        'pygame',
        'pygments',
        'pylint',
        'pynacl',
        'PyQt4',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'pytest',
        'scipy',
        'setuptools',
        'shelve',
        'sphinx',
        'sqlalchemy',
        'tkinter',
        'toml',
        'tornado',
        'traitlets',
        'unittest',
        'wcwidth',
        'wheel',
        'wx',
        'xmlrpc',
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
    name='2RPM',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['python3*.dll', 'VCRUNTIME*.dll', 'api-ms-win-*.dll'],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
