#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""解析截图 CLI 参数并执行一次截图、可选保存和上传。"""

import argparse
from contextlib import nullcontext
from datetime import date
import os
import sys

from modules.image_host.context import UploadContext
from modules.image_host.core import ImageHostError
from modules.image_host.registry import upload_with_fallback
from modules.operation_trace import trace_item
from modules.operation_trace import trace_scope
from modules.operation_trace import trace_skipped
from modules.operation_trace import trace_slot
from modules.operation_trace import trace_slots_skipped
from modules.operation_trace import trace_warning
from modules.upload_status import make_status_consumer
from modules.upload_status import upload_feedback_scope

from .inline_upload import InlineUploadError
from .inline_upload import parse_inline_hosts
from .inline_upload import prepare_cli_host
from .models import CaptureError
from .models import capture_failure_message
from .retention import cli_filename
from .retention import cli_target_is_managed
from .retention import save_cli
from .service import capture
from .title_match import validate_capture_target


# 支持的截图来源，同时作为 --source 的内联前缀
SOURCE_PROVIDERS = ('adb', 'window')

# 进程退出码：参数错误与截图失败分别对应固定分类
ARGUMENT_ERROR_CODE = 2
CAPTURE_FAILURE_CODE = 1

# 默认输出子目录名，始终相对程序根目录
DEFAULT_OUTPUT_DIRNAME = 'screenshot'

# 视为显式文件的后缀，比较时不区分大小写
IMAGE_FILE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.webp', '.raw')

# 尾随这些分隔符即表示目录
DIRECTORY_SEPARATORS = ('\\', '/')

# 显式文件后缀到请求格式的映射
SUFFIX_FORMATS = {
    '.jpg': 'jpeg', '.jpeg': 'jpeg', '.png': 'png',
    '.webp': 'webp', '.raw': 'raw',
}


class SafeArgumentParser(argparse.ArgumentParser):
    """参数错误不回显用户原始输入。"""

    def error(self, message):
        """输出固定参数错误并以退出码 2 结束。"""
        if message.startswith('duplicate_option:'):
            option = message.partition(':')[2]
            self.exit(ARGUMENT_ERROR_CODE, f'参数错误：{option} 重复参数\n')
        self.exit(ARGUMENT_ERROR_CODE, '截图参数无效\n')


class OnceAction(argparse.Action):
    """限制单个选项最多出现一次。"""

    def __call__(self, parser, namespace, values, option_string=None):
        """记录选项出现状态，拒绝重复参数。"""
        marker = '_seen_' + self.dest
        if getattr(namespace, marker, False):
            parser.error(f'duplicate_option:{option_string}')
        setattr(namespace, marker, True)
        setattr(namespace, self.dest, values)


def _build_parser():
    """构建截图调试子命令的参数解析器。

    Returns:
        argparse.ArgumentParser: 已注册来源、目标、输出与上传选项的解析器。
    """
    parser = SafeArgumentParser(
        prog='2RPM.py screenshot',
        description='截图调试：截取指定窗口或 ADB 设备并输出尺寸',
        epilog=(
            'window 无标记时按完整窗口标题精确匹配，区分大小写。'
            '非空应用名末尾仅允许一次 @{version}、@{VERSION} 或 @{Version}，'
            '启用版本匹配，例如 --source window --target "AppName@{Version}"。'
            '版本自由尾部可能接纳 Launcher、Settings 等辅助标题；'
            '多个匹配时拒绝截图，不自动选取窗口。ADB 目标不支持版本标记。'
        ),
        allow_abbrev=False,
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
    parser.add_argument('--output', nargs='?', const='', default=None,
                        action=OnceAction, help='截图输出路径')
    parser.add_argument(
        '--upload', default=None, action=OnceAction,
        help='使用内联图床配置上传截图',
    )
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
        str: 原始非空取值；仅使用首尾空白进行非空校验。
    """
    if not isinstance(value, str) or not value.strip():
        parser.error(message)
    return value


def _split_prefix(token):
    """识别取值起始的 adb: 或 window: 内联前缀。

    Args:
        token (str): 待识别的 --source 原始取值。

    Returns:
        tuple: (来源, 内联目标)；无内联前缀时为 (None, None)。
    """
    candidate = token.lstrip()
    for provider in SOURCE_PROVIDERS:
        prefix = f'{provider}:'
        if candidate.startswith(prefix):
            return provider, candidate[len(prefix):]
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
        if not inline_target.strip():
            parser.error('内联目标不能为空')
        if separate is not None:
            parser.error('内联目标与位置目标或 --target 不能重复提供')
        if provider == 'adb':
            inline_target = inline_target.strip()
        return provider, inline_target

    source_keyword = token.strip()
    if source_keyword in SOURCE_PROVIDERS:
        if separate is None:
            parser.error(f'--source {source_keyword} 需要另外提供非空目标')
        if source_keyword == 'adb':
            separate = separate.strip()
        return source_keyword, separate

    if separate is not None:
        parser.error('窗口标题已包含在 --source 中，不能再提供目标')
    return 'window', token


def parse_screenshot_args(argv):
    """解析截图调试参数，参数错误统一以 SystemExit(2) 表达。

    支持 "adb:序列号"、"window:窗口标题"、裸窗口标题、来源关键字加位置目标、
    来源关键字加 --target 五种形态，并解析 --output 与 --upload 选项。
    window 目标保留原文并校验版本表达式，ADB 目标拒绝版本标记候选。

    Args:
        argv (list[str]): 截图子命令之后的参数列表；允许包含开头的
            'screenshot' 关键字。

    Returns:
        argparse.Namespace: 含 source（window/adb）、target（非空字符串）、
            output 与 hosts（None 或已校验图床列表）。
    """
    remaining = list(argv)
    if remaining and remaining[0] == 'screenshot':
        remaining = remaining[1:]

    parser = _build_parser()
    if _find_dash_source(remaining) is not None:
        parser.error("参数应写作 --source，不支持 '-- source' 形式")

    parsed = parser.parse_args(remaining)
    source, target = _normalize(parser, parsed)
    try:
        validate_capture_target(source, target)
    except ValueError:
        parser.error('截图参数无效')
    hosts = None
    if parsed.upload is not None:
        try:
            hosts = parse_inline_hosts(parsed.upload)
        except InlineUploadError as error:
            parser.exit(ARGUMENT_ERROR_CODE, str(error) + '\n')
    if parsed.output is None and hosts is None:
        parser.error('需要输出或上传')
    return argparse.Namespace(
        source=source,
        target=target,
        output=parsed.output,
        hosts=hosts,
    )


def _resolve_output(output, program_dir):
    """把 --output 归一为绝对路径并判定目标是文件还是目录。

    未提供或为空白时使用程序根目录下的 screenshot 子目录；显式相对路径
    相对当前工作目录解析；已存在目录与尾随分隔符优先按目录处理。

    Args:
        output: --output 的原始取值。
        program_dir (str): 程序根目录。

    Returns:
        tuple: (绝对路径, 是否输出到单个文件)。
    """
    if output is None or not isinstance(output, str) or not output.strip():
        default_dir = os.path.join(program_dir, DEFAULT_OUTPUT_DIRNAME)
        return os.path.abspath(default_dir), False

    raw = output.strip()
    source_path = raw if os.path.isabs(raw) else os.path.abspath(raw)
    absolute = os.path.normpath(source_path)
    if raw.endswith(DIRECTORY_SEPARATORS) or os.path.isdir(absolute):
        return absolute, False
    if os.path.splitext(absolute)[1].lower() in IMAGE_FILE_SUFFIXES:
        return absolute, True
    return absolute, False


def _host_providers(hosts):
    """按声明顺序提取全部图床 provider，供消费者判定重复配置项后缀。"""
    if not hosts:
        return ()
    return tuple(host.get('provider', '') for host in hosts)


def _upload_all(data, filename, hosts):
    """独立凭证作用域，所有声明项串行执行且全部成功才成功。"""
    all_success = True
    try:
        for index, host in enumerate(hosts):
            # 绑定真实配置项序号，避免每次单站调用的局部序号折叠日志槽位
            with trace_item(image_index=1, slot_index=index):
                slot = trace_slot(host.get('provider', ''), index)
                prepared, prepare_error = prepare_cli_host(host)
                success = False
                permission_message = ''
                if prepared is not None:
                    try:
                        with UploadContext(
                                diagnostics=False,
                                resolved_tokens={0: prepared['token']}) as context:
                            uploaded = upload_with_fallback(
                                data, filename, [prepared], context=context)
                        success = uploaded.success
                        if not success and uploaded.failures:
                            code = uploaded.failures[-1].code
                            if code in ('profile_forbidden',
                                        'profile_forbidden_invalid_storage'):
                                permission_message = ImageHostError(code, '').message
                    except Exception:
                        success = False
                if not success:
                    # 已由上传链收尾的槽位不被改写，仅补齐前置失败与异常槽位
                    slot.finish('failed',
                                reason_code=prepare_error or 'upload_failed')
                if success:
                    print(f'{host["provider"]}：上传成功 {uploaded.url}')
                if not success:
                    print(f'{host["provider"]}：上传失败')
                    if permission_message:
                        print(permission_message)
                all_success = all_success and success
    except BaseException as exc:
        trace_slots_skipped(
            1, len(hosts),
            reason_code=('batch_cancelled'
                         if isinstance(exc, (KeyboardInterrupt, SystemExit))
                         else 'slot_unfinished'))
        raise
    return all_success


def _upload_status(stats):
    """按所有声明上传槽位的结果给出 CLI 批次业务终态。"""
    if stats['slots_failed'] and stats['slots_succeeded']:
        return 'partial_failure'
    if stats['slots_failed']:
        return 'failed'
    if stats['slots_succeeded']:
        return 'completed'
    return None


def run_screenshot_cli(args, program_dir, *, logging_scope=None):
    """先完成无副作用校验，再进入可选日志作用域执行截图、保存与上传。"""
    source = getattr(args, 'source', None)
    source_target = getattr(args, 'target', None)
    output = getattr(args, 'output', None)
    hosts = getattr(args, 'hosts', None)
    if (source not in SOURCE_PROVIDERS or not isinstance(source_target, str)
            or not source_target.strip()):
        print('截图参数无效')
        return ARGUMENT_ERROR_CODE
    try:
        validate_capture_target(source, source_target)
    except ValueError:
        print('截图参数无效')
        return ARGUMENT_ERROR_CODE

    target, is_file = (None, False)
    if output is not None:
        target, is_file = _resolve_output(output, program_dir)
        if cli_target_is_managed(target, is_file, program_dir):
            print('截图参数无效')
            return ARGUMENT_ERROR_CODE

    requested = 'jpeg'
    if is_file:
        requested = SUFFIX_FORMATS[os.path.splitext(target)[1].lower()]
    if requested == 'raw' and hosts is not None:
        print('截图参数无效')
        return ARGUMENT_ERROR_CODE

    today = date.today()
    slots_planned = len(hosts) if hosts else 0
    consumer = make_status_consumer(
        stream=sys.stderr, images_planned=1, slot_providers=_host_providers(hosts))
    with (logging_scope if logging_scope is not None else nullcontext()), \
            trace_scope(mode='cli', images_planned=1, slots_planned=slots_planned,
                        on_status=consumer) as trace:
        with trace_item(image_index=1):
            try:
                result = capture(
                    source, source_target, image_format=requested, purpose='cli')
            except (KeyboardInterrupt, SystemExit):
                trace.finish_image(1, 'cancelled')
                trace_skipped('save', reason_code='batch_cancelled')
                trace_slots_skipped(
                    1, slots_planned, reason_code='batch_cancelled')
                raise
            except CaptureError as error:
                trace.finish_image(1, 'failed')
                trace_skipped('save', reason_code='capture_failed')
                trace_slots_skipped(
                    1, slots_planned, reason_code='capture_failed')
                print(capture_failure_message(error))
                return CAPTURE_FAILURE_CODE
            except Exception:
                trace.finish_image(1, 'failed')
                trace_skipped('save', reason_code='capture_failed')
                trace_slots_skipped(
                    1, slots_planned, reason_code='capture_failed')
                print('截图失败')
                return CAPTURE_FAILURE_CODE
            trace.finish_image(1, 'succeeded')

            for warning in result.warnings:
                trace_warning()
                print(warning)
            filename = cli_filename(result, target, is_file, today)
            outcome = None
            try:
                if target is not None:
                    outcome = save_cli(result, target, is_file, today=today)
                    filename = outcome.filename
                    for warning in outcome.warnings:
                        trace_warning()
                        print(warning)
                    if outcome.path is not None:
                        print(outcome.path)
                else:
                    trace_skipped('save', reason_code='not_configured')
                if hosts is not None:
                    with upload_feedback_scope(consumer):
                        all_success = _upload_all(
                            result.image_bytes, filename, hosts)
                    trace.set_status(_upload_status(trace.summary()))
                    return 0 if all_success else 1
            except (KeyboardInterrupt, SystemExit):
                trace_slots_skipped(
                    1, slots_planned, reason_code='batch_cancelled')
                raise
            saved = outcome is not None and outcome.path is not None
            trace.set_status('completed' if saved else 'failed')
            return 0 if saved else 1
