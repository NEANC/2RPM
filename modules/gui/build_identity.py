#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 构建身份；冻结阶段由构建流程写入同工具的完整版本。"""

from pathlib import Path

from modules.version import VERSION


BUILD_TOOL = 'source'
BUILD_TAG = VERSION


def release_names(build_tool: str, build_tag: str) -> tuple[str, str]:
    """返回同工具、同完整版本的 GUI 与 CLI 发布文件名。

    Args:
        build_tool: 冻结工具名称，取值为 ``Nuitka`` 或 ``PyInstaller``。
        build_tag: 写入构建标识的完整版本。

    Returns:
        依次为 GUI 与 CLI 发布文件的文件名。
    """
    return (f'2RPM-GUI-{build_tool}-{build_tag}.exe',
            f'2RPM-{build_tool}-{build_tag}.exe')


def write_identity(path: Path, build_tool: str, build_tag: str) -> None:
    """把冻结工具与完整版本写入身份模块，供构建流程调用。

    Args:
        path: 身份模块源文件路径。
        build_tool: 冻结工具名称，取值为 ``Nuitka`` 或 ``PyInstaller``。
        build_tag: 写入构建标识的完整版本。
    """
    path.write_text(
        '#!/usr/bin/env python3\n'
        '# -_- coding: utf-8 -_-\n\n'
        '"""GUI 构建身份；冻结阶段由构建流程写入同工具的完整版本。"""\n\n'
        'from modules.version import VERSION\n\n\n'
        f'BUILD_TOOL = {build_tool!r}\n'
        f'BUILD_TAG = {build_tag!r}\n',
        encoding='utf-8')
