#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""解析截图调试参数并执行单次截图，不读写配置或图床设置。"""

import argparse

from .models import CaptureError
from .service import capture


# 支持的截图来源，同时作为 --source 的内联前缀
SOURCE_PROVIDERS = ('adb', 'window')

# 进程退出码：参数错误与截图失败分别对应固定分类
ARGUMENT_ERROR_CODE = 2
CAPTURE_FAILURE_CODE = 1


def _build_parser():
    """构建截图调试子命令的参数解析器。

    Returns:
        argparse.ArgumentParser: 已注册来源、目标及预留选项的解析器。
    """
    parser = argparse.ArgumentParser(
        prog='2RPM.py screenshot',
        description='截图调试：截取指定窗口或 ADB 设备并输出尺寸',
    )
    parser.add_argument(
        '--source', action='append', default=None, metavar='SOURCE',
        help='截图来源，支持 "adb:序列号"、"window:窗口标题" 或来源关键字 adb/window',
    )
    parser.add_argument(
        '--target', default=None, metavar='TARGET',
        help='与 adb/window 来源关键字搭配使用的目标，也可作为位置参数提供',
    )
    parser.add_argument(
        'positional_targets', nargs='*', default=[], metavar='TARGET',
        help=argparse.SUPPRESS,
    )
    parser.add_argument('--output', default=None, help='预留：截图输出路径')
    parser.add_argument('--upload', default=None, help='预留：是否上传图床')
    parser.add_argument(
        '--image-host', dest='image_host', default=None, help='预留：图床名称')
    parser.add_argument(
        '-c', '--config', dest='config', default=None, help='预留：指定配置文件')
    return parser


def _find_dash_source(argv):
    """识别 '-- source' 误写，避免其被当作位置目标接受。

    Args:
        argv (list): 已去掉 screenshot 关键字后的参数列表。

    Returns:
        str | None: 命中的错误写法的原始参数，未命中时为 None。
    """
    for token in argv:
        if not isinstance(token, str):
            continue
        if token == '--' or token.strip() == '-- source':
            return token
    return None


def _clean(parser, value, message):
    """校验取值并为非空字符串，参数错误时以退出码 2 中止。

    Args:
        parser (argparse.ArgumentParser): 用于报告参数错误的解析器。
        value: 待校验的原始取值。
        message (str): 参数错误提示。

    Returns:
        str: 去掉首尾空白后的非空取值。
    """
    if not isinstance(value, str) or not value.strip():
        parser.error(message)
    return value.strip()


def _split_prefix(token):
    """识别取值起始的 adb: 或 window: 内联前缀。

    Args:
        token (str): 已去掉首尾空白的 --source 取值。

    Returns:
        tuple: (来源, 内联目标)；无内联前缀时为 (None, None)。
    """
    for provider in SOURCE_PROVIDERS:
        prefix = f'{provider}:'
        if token.startswith(prefix):
            return provider, token[len(prefix):].strip()
    return None, None


def _normalize(parser, parsed):
    """把多种 --source 写法归一到同一核心输入。

    Args:
        parser (argparse.ArgumentParser): 用于报告参数错误的解析器。
        parsed (argparse.Namespace): 原始解析结果。

    Returns:
        tuple: (来源, 目标)，来源为 window 或 adb，目标为非空字符串。
    """
    sources = parsed.source or []
    if not sources:
        parser.error('必须提供 --source')
    if len(sources) > 1:
        parser.error('--source 只能提供一次')

    positionals = parsed.positional_targets
    if len(positionals) > 1:
        parser.error('只允许一个目标位置参数')

    token = _clean(parser, sources[0], '--source 的值不能为空')

    separate = None
    if positionals:
        separate = _clean(parser, positionals[0], '位置目标不能为空')
    if parsed.target is not None:
        if separate is not None:
            parser.error('位置目标与 --target 不能同时提供')
        separate = _clean(parser, parsed.target, '--target 不能为空')

    provider, inline_target = _split_prefix(token)
    if provider is not None:
        if not inline_target:
            parser.error('内联目标不能为空')
        if separate is not None:
            parser.error('内联目标与位置目标或 --target 不能重复提供')
        return provider, inline_target

    if token in SOURCE_PROVIDERS:
        if separate is None:
            parser.error(f'--source {token} 需要另外提供非空目标')
        return token, separate

    if separate is not None:
        parser.error('窗口标题已包含在 --source 中，不能再提供目标')
    return 'window', token


def parse_screenshot_args(argv):
    """解析截图调试参数，参数错误统一以 SystemExit(2) 表达。

    支持 "adb:序列号"、"window:窗口标题"、裸窗口标题、来源关键字加位置目标、
    来源关键字加 --target 五种形态，并接受尚未实现的 --output、--upload、
    --image-host 与 --config/-c 预留选项。目标按用户输入使用，不做外形判定。

    Args:
        argv (list[str]): 截图子命令之后的参数列表；允许包含开头的
            'screenshot' 关键字。

    Returns:
        argparse.Namespace: 至少含 source（window/adb）与 target（非空字符串），
            并携带 output、upload、image_host、config 预留选项。
    """
    remaining = list(argv)
    if remaining and remaining[0] == 'screenshot':
        remaining = remaining[1:]

    parser = _build_parser()
    if _find_dash_source(remaining) is not None:
        parser.error("参数应写作 --source，不支持 '-- source' 形式")

    parsed = parser.parse_args(remaining)
    source, target = _normalize(parser, parsed)
    return argparse.Namespace(
        source=source,
        target=target,
        output=parsed.output,
        upload=parsed.upload,
        image_host=parsed.image_host,
        config=parsed.config,
    )


def _safe_code(error):
    """只保留字符串分类，避免输出后端附加内容。

    Args:
        error (CaptureError): 截图失败异常。

    Returns:
        str: 安全可输出的失败分类。
    """
    return error.code if isinstance(error.code, str) else 'unknown'


def run_screenshot_cli(args, program_dir):
    """执行一次截图并输出来源、目标与尺寸，返回进程退出码。

    本步不保存文件也不上传图床，成功时只输出截图基本信息。

    Args:
        args (argparse.Namespace): parse_screenshot_args 的解析结果。
        program_dir (str): 程序根目录；预留给后续输出路径解析，本步未使用。

    Returns:
        int: 全部请求操作成功为 0，参数错误为 2，截图失败为 1。
    """
    source = getattr(args, 'source', None)
    target = getattr(args, 'target', None)
    if source not in SOURCE_PROVIDERS or not isinstance(target, str) \
            or not target.strip():
        print('截图参数无效：请提供有效的 --source 与目标')
        return ARGUMENT_ERROR_CODE

    try:
        result = capture(source, target)
    except CaptureError as error:
        print(f'截图失败：{_safe_code(error)}')
        return CAPTURE_FAILURE_CODE
    except Exception:
        print('截图失败：未知错误')
        return CAPTURE_FAILURE_CODE

    print(
        f'截图成功：来源 {result.source}，目标 {result.target}，'
        f'尺寸 {result.width}x{result.height}')
    return 0
