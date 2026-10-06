#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""解析截图调试参数并执行单次截图并保存。

未带 --upload 时不读取任何配置也不涉及图床；带 --upload 时只读解析
配置文件中的 push.screenshot.image_host，并按列表顺序尝试上传。
"""

import argparse
from collections.abc import Mapping
from datetime import datetime
from io import BytesIO
import os

from PIL import Image
from ruamel.yaml import YAML

from modules.image_host.context import UploadContext
from modules.image_host.core import ImageHostError
from modules.image_host.registry import upload_with_fallback

from .models import CaptureError
from .pipeline import markdown_image
from .service import capture


# 支持的截图来源，同时作为 --source 的内联前缀
SOURCE_PROVIDERS = ('adb', 'window')

# 进程退出码：参数错误与截图失败分别对应固定分类
ARGUMENT_ERROR_CODE = 2
CAPTURE_FAILURE_CODE = 1

# 默认输出子目录名，始终相对程序根目录
DEFAULT_OUTPUT_DIRNAME = 'screenshot'

# 视为显式文件的后缀，比较时不区分大小写
IMAGE_FILE_SUFFIXES = ('.png', '.jpg', '.jpeg')

# 尾随这些分隔符即表示目录
DIRECTORY_SEPARATORS = ('\\', '/')

# 自动命名冲突时的最大尝试次数
UNIQUE_NAME_ATTEMPTS = 100

# JPEG 编码质量
JPEG_QUALITY = 90

# 保存失败时的固定安全提示，不携带底层异常内容
SAVE_FAILURE_MESSAGE = '截图保存失败：无法写入输出路径'

# 目标文件已存在且不覆盖时的专用固定提示
SAVE_EXISTS_MESSAGE = '截图保存失败：目标文件已存在，未覆盖'

# 未指定 --config/-c 时相对程序根目录的默认配置文件名
DEFAULT_CONFIG_FILENAME = 'config.yaml'

# 上传相关固定安全提示，均不携带凭证、响应原文或完整配置
UPLOAD_MISSING_CONFIG_MESSAGE = '上传失败：未找到配置文件'
UPLOAD_INVALID_CONFIG_MESSAGE = '上传失败：配置文件解析失败'
UPLOAD_NOT_CONFIGURED_MESSAGE = '上传失败：未配置图床'
UPLOAD_FILTER_INVALID_MESSAGE = '上传失败：--image-host 未提供有效名称'


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
    parser.add_argument('--output', default=None, help='截图输出路径')
    parser.add_argument(
        '--upload', action='store_true',
        help='读取配置文件并尝试上传截图到图床')
    parser.add_argument(
        '--image-host', dest='image_host', default=None,
        help='逗号分隔的图床名称筛选，仅在 --upload 时有效')
    parser.add_argument(
        '-c', '--config', dest='config', default=None,
        help='上传所用的配置文件，默认 program_dir/config.yaml')
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
    来源关键字加 --target 五种形态，并解析 --output、--upload、
    --image-host 与 --config/-c 选项。目标按用户输入使用，不做外形判定。

    Args:
        argv (list[str]): 截图子命令之后的参数列表；允许包含开头的
            'screenshot' 关键字。

    Returns:
        argparse.Namespace: 至少含 source（window/adb）与 target（非空字符串），
            并携带 output、upload、image_host、config 选项。
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


def _timestamp_token():
    """返回带微秒精度的时间戳令牌，用于生成唯一文件名。

    Returns:
        str: 形如 20260101_000000_123456 的时间戳令牌。
    """
    return datetime.now().strftime('%Y%m%d_%H%M%S_%f')


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


def _discard_partial(path):
    """尽力删除本次写入的半成品文件，忽略清理失败。

    Args:
        path (str): 本次创建的半成品文件路径。
    """
    try:
        os.remove(path)
    except OSError:
        pass


def _write_exclusive_file(path, data):
    """以排他方式创建文件并写入字节，失败时只清理本次创建的半成品。

    Args:
        path (str): 目标文件绝对路径。
        data (bytes): 待写入的完整字节内容。

    Raises:
        FileExistsError: 目标文件已存在，不覆盖。
        OSError: 创建或写入失败。
    """
    created = False
    try:
        with open(path, 'xb') as handle:
            created = True
            handle.write(data)
    except BaseException:
        if created:
            _discard_partial(path)
        raise


def _encode_jpeg(png_bytes):
    """把 PNG 字节转码为真实 JPEG 字节。

    JPEG 不支持 alpha 通道，因此按黑底合成：先把图像转为 RGBA，
    再叠加到纯黑背景上并丢弃 alpha，最后编码输出，以满足 JPEG
    无 alpha 的契约。

    Args:
        png_bytes (bytes): 原始 PNG 字节。

    Returns:
        bytes: JPEG 编码后的字节。
    """
    with Image.open(BytesIO(png_bytes)) as image:
        image.load()
        rgba = image.convert('RGBA')
        background = Image.new('RGB', rgba.size, (0, 0, 0))
        background.paste(rgba, mask=rgba.getchannel('A'))
        buffer = BytesIO()
        background.save(buffer, format='JPEG', quality=JPEG_QUALITY)
    return buffer.getvalue()


def _encode_image(png_bytes, path):
    """按目标扩展名选择编码方式，PNG 原样写入，其余转码为 JPEG。

    Args:
        png_bytes (bytes): 原始 PNG 字节。
        path (str): 目标文件路径。

    Returns:
        bytes: 实际写入文件的字节内容。
    """
    if os.path.splitext(path)[1].lower() == '.png':
        return png_bytes
    return _encode_jpeg(png_bytes)


def _save_to_directory(png_bytes, directory):
    """在目录内以带时间戳的唯一名称排他保存 PNG。

    Args:
        png_bytes (bytes): 原始 PNG 字节。
        directory (str): 输出目录绝对路径。

    Returns:
        str: 实际保存的完整文件路径。

    Raises:
        OSError: 目录创建或文件写入失败。
        FileExistsError: 无法生成未占用的文件名。
    """
    os.makedirs(directory, exist_ok=True)
    token = _timestamp_token()
    for sequence in range(UNIQUE_NAME_ATTEMPTS):
        suffix = '' if sequence == 0 else f'_{sequence}'
        candidate = os.path.join(
            directory, f'screenshot_{token}{suffix}.png')
        try:
            _write_exclusive_file(candidate, png_bytes)
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError('无法生成唯一截图文件名')


def _save_capture(result, output, program_dir):
    """把截图写入目标路径，返回实际保存的完整路径。

    Args:
        result (CaptureResult): 截图结果。
        output: --output 的原始取值。
        program_dir (str): 程序根目录。

    Returns:
        str: 实际保存的完整文件路径。

    Raises:
        OSError: 目录创建或文件写入失败。
    """
    target, is_file = _resolve_output(output, program_dir)
    if not is_file:
        return _save_to_directory(result.png_bytes, target)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    _write_exclusive_file(target, _encode_image(result.png_bytes, target))
    return target


def _read_yaml_config(config_path):
    """以只读 safe 模式解析 YAML 文件并返回顶层对象。

    只做解析，不展开环境变量、不补全字段、不迁移也不回写文件。

    Args:
        config_path (str): 配置文件绝对路径。

    Returns:
        解析后的顶层对象；文件缺失或解析失败时抛出异常。
    """
    yaml = YAML(typ='safe')
    with open(config_path, 'r', encoding='utf-8') as handle:
        return yaml.load(handle)


def _extract_image_hosts(data):
    """从只读配置对象中取出 push.screenshot.image_host 列表。

    Args:
        data: _read_yaml_config 解析得到的顶层对象。

    Returns:
        list: 图床项列表；缺失、非列表或为空列表时返回 None。
    """
    if not isinstance(data, Mapping):
        return None
    push = data.get('push')
    if not isinstance(push, Mapping):
        return None
    screenshot = push.get('screenshot')
    if not isinstance(screenshot, Mapping):
        return None
    hosts = screenshot.get('image_host')
    if not isinstance(hosts, list) or not hosts:
        return None
    return hosts


def _load_image_hosts(config_path):
    """只读加载图床配置，返回图床项列表或固定安全提示。

    Args:
        config_path (str): 配置文件绝对路径。

    Returns:
        tuple: (图床项列表, None) 或 (None, 固定安全提示)。
    """
    if not os.path.isfile(config_path):
        return None, UPLOAD_MISSING_CONFIG_MESSAGE
    try:
        data = _read_yaml_config(config_path)
    except Exception:
        return None, UPLOAD_INVALID_CONFIG_MESSAGE
    hosts = _extract_image_hosts(data)
    if hosts is None:
        return None, UPLOAD_NOT_CONFIGURED_MESSAGE
    return hosts, None


def _resolve_config_path(config, program_dir):
    """把 --config/-c 归一为绝对路径，缺省时用程序根目录下的配置文件。

    显式相对路径相对当前工作目录解析，绝对路径原样使用。

    Args:
        config: --config/-c 的原始取值。
        program_dir (str): 程序根目录。

    Returns:
        str: 配置文件绝对路径。
    """
    if config is None or not isinstance(config, str) or not config.strip():
        return os.path.join(program_dir, DEFAULT_CONFIG_FILENAME)
    raw = config.strip()
    return os.path.normpath(
        raw if os.path.isabs(raw) else os.path.abspath(raw))


def _provider_name(host):
    """取图床项规范化后的 provider 名称，无法识别时返回 None。

    Args:
        host: 配置中的单个图床项。

    Returns:
        str | None: 去空白并转小写后的名称。
    """
    if not isinstance(host, Mapping):
        return None
    provider = host.get('provider')
    if not isinstance(provider, str):
        return None
    return provider.strip().lower()


def _select_image_hosts(hosts, raw_names):
    """按 --image-host 名称筛选并重排图床项，各项保留自身凭证与参数。

    每个名称必须唯一对应一个完整图床项；未找到或对应多项时返回安全提示。

    Args:
        hosts (list): 配置中的图床项列表。
        raw_names (str): 逗号分隔的图床名称。

    Returns:
        tuple: (选中图床项列表, None) 或 (None, 固定安全提示)。
    """
    names = [part.strip().lower() for part in raw_names.split(',')]
    names = [name for name in names if name]
    if not names:
        return None, UPLOAD_FILTER_INVALID_MESSAGE
    selected = []
    for name in names:
        matched = [host for host in hosts if _provider_name(host) == name]
        if not matched:
            return None, f'上传失败：未找到图床 {name}'
        if len(matched) > 1:
            return None, f'上传失败：图床 {name} 对应多项配置'
        selected.append(matched[0])
    return selected, None


def _upload_failure_message(result):
    """把失败结果压缩为固定安全分类，不携带凭证或响应原文。

    Args:
        result (UploadResult): 顺序上传的最终结果。

    Returns:
        str: 形如 "上传失败：图床：固定分类" 的安全提示。
    """
    details = '；'.join(
        f'{item.provider}：{ImageHostError(item.code, "").message}'
        for item in result.failures)
    return f'上传失败：{details}' if details else '上传失败：图床未返回有效链接'


def _print_upload_warnings(warnings):
    """按上传结果顺序输出去重后的固定告警。"""
    seen = set()
    for warning in warnings or ():
        if warning in seen:
            continue
        seen.add(warning)
        print(f'提示：{warning}')


def _print_upload_failures(result):
    """按尝试顺序输出固定失败分类及已有安全诊断。"""
    for failure in result.failures:
        message = ImageHostError(failure.code, '').message
        details = f'上传尝试：{failure.provider}：{message}'
        if failure.stage:
            details += f'（阶段 {failure.stage}'
            if failure.http_status is not None:
                details += f'，HTTP {failure.http_status}'
            details += '）'
        if failure.diagnostic:
            details += f'；诊断：{failure.diagnostic}'
        print(details)


def _upload_debug_image(args, png_bytes, saved_path, program_dir):
    """读取图床配置并执行一次顺序上传，返回退出码。

    本地调试图片已落盘，无论上传成败均保留；失败时只输出固定安全分类。

    Args:
        args (argparse.Namespace): 解析结果，携带 config 与 image_host。
        png_bytes (bytes): 待上传的原始 PNG 字节。
        saved_path (str): 已保存的调试图片路径。
        program_dir (str): 程序根目录。

    Returns:
        int: 上传成功为 0，配置或上传失败为 1。
    """
    config_path = _resolve_config_path(
        getattr(args, 'config', None), program_dir)
    hosts, message = _load_image_hosts(config_path)
    if message is not None:
        print(message)
        return CAPTURE_FAILURE_CODE
    raw_names = getattr(args, 'image_host', None)
    if raw_names is not None:
        hosts, message = _select_image_hosts(hosts, raw_names)
        if message is not None:
            print(message)
            return CAPTURE_FAILURE_CODE
    filename = os.path.splitext(os.path.basename(saved_path))[0] + '.png'
    with UploadContext(diagnostics=True) as context:
        uploaded = upload_with_fallback(
            png_bytes, filename, hosts, context=context)
    _print_upload_warnings(uploaded.warnings)
    if not uploaded.success:
        _print_upload_failures(uploaded)
        print('上传失败：所有图床尝试均未成功' if uploaded.failures
              else _upload_failure_message(uploaded))
        return CAPTURE_FAILURE_CODE
    if uploaded.failures:
        _print_upload_failures(uploaded)
    markdown = markdown_image(os.path.splitext(filename)[0], uploaded.url)
    print(f'上传成功：图床 {uploaded.provider}')
    print(f'图片地址：{uploaded.url}')
    print(f'Markdown：{markdown}')
    return 0


def run_screenshot_cli(args, program_dir):
    """执行一次截图并保存，输出来源、目标、尺寸与保存路径，返回退出码。

    未带 --upload 时不读取配置也不上传图床；带 --upload 时只读解析
    配置文件并尝试按顺序上传，保存与上传失败均返回 1。

    Args:
        args (argparse.Namespace): parse_screenshot_args 的解析结果。
        program_dir (str): 程序根目录，默认输出目录相对该目录解析。

    Returns:
        int: 全部请求操作成功为 0，参数错误为 2，截图、保存或上传失败为 1。
    """
    source = getattr(args, 'source', None)
    target = getattr(args, 'target', None)
    if source not in SOURCE_PROVIDERS or not isinstance(target, str) \
            or not target.strip():
        print('截图参数无效：请提供有效的 --source 与目标')
        return ARGUMENT_ERROR_CODE

    upload = bool(getattr(args, 'upload', None))
    if getattr(args, 'image_host', None) is not None and not upload:
        print('参数错误：--image-host 仅在 --upload 时有效')
        return ARGUMENT_ERROR_CODE

    try:
        result = capture(source, target)
    except CaptureError as error:
        print(f'截图失败：{_safe_code(error)}')
        return CAPTURE_FAILURE_CODE
    except Exception:
        print('截图失败：未知错误')
        return CAPTURE_FAILURE_CODE

    try:
        saved_path = _save_capture(result, getattr(args, 'output', None),
                                   program_dir)
    except FileExistsError:
        print(SAVE_EXISTS_MESSAGE)
        return CAPTURE_FAILURE_CODE
    except OSError:
        print(SAVE_FAILURE_MESSAGE)
        return CAPTURE_FAILURE_CODE

    print(
        f'截图成功：来源 {result.source}，目标 {result.target}，'
        f'尺寸 {result.width}x{result.height}')
    print(f'已保存：{saved_path}')
    for warning in result.warnings or ():
        print(f'提示：{warning}')
    if not upload:
        return 0
    return _upload_debug_image(
        args, result.image_bytes, saved_path, program_dir)
