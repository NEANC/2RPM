#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证截图调试上传：只读配置、顺序故障转移、安全输出与退出码。

测试只把截图边界替换为内存合成的 CaptureResult，图床适配器为本地桩件，
配置写入临时目录，绝不执行真实截图、ADB、窗口、网络或真实上传。
"""

import os
from argparse import Namespace
from importlib import import_module
from importlib.util import find_spec
from io import BytesIO
from unittest.mock import Mock

from PIL import Image
import pytest

from modules.screenshot.models import CaptureResult


URL = 'https://cdn.example.com/image.png'
SECRET = 'FAKE_UPLOAD_SECRET_6120'
ENV_NAME = 'SCREENSHOT_UPLOAD_TEST_TOKEN'


def cli_module():
    """导入截图 CLI，模块缺失时让测试明确失败。"""
    assert find_spec('modules.screenshot.cli') is not None, '尚未实现截图 CLI'
    return import_module('modules.screenshot.cli')


def registry_module():
    """读取真实静态注册表与顺序上传入口。"""
    return import_module('modules.image_host.registry')


def make_png(size=(4, 3)):
    """用真实 Pillow 生成可解码的 PNG 字节（含 alpha 通道）。"""
    image = Image.new('RGBA', size, (9, 8, 7, 255))
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def patch_capture(monkeypatch, result):
    """替换服务层与 CLI 的截图调用，返回可直接断言的 Mock。"""
    mock = Mock(return_value=result)
    service = import_module('modules.screenshot.service')
    monkeypatch.setattr(service, 'capture', mock)
    monkeypatch.setattr(cli_module(), 'capture', mock, raising=False)
    return mock


def install(monkeypatch, provider, adapter):
    """临时注册本地图床适配器，用例结束后自动还原。"""
    monkeypatch.setitem(registry_module().UPLOADERS, provider, adapter)


def config_with_hosts(*items, targets=None):
    """构造含指定图床项的配置文本，各项为流式映射写法。"""
    lines = ['push:', '  screenshot:']
    if targets is None:
        lines.append('    targets: []')
    else:
        lines.append('    targets:')
        lines.extend(f'      - {target}' for target in targets)
    lines.append('    image_host:')
    lines.extend(f'      - {item}' for item in items)
    return '\n'.join(lines) + '\n'


def write_config(tmp_path, text, name='config.yaml'):
    """把配置文本写入临时目录并返回绝对路径。"""
    path = tmp_path / name
    path.write_text(text, encoding='utf-8')
    return str(path)


def args_for(output=None, upload=True, image_host=None, config=None):
    """构造 run_screenshot_cli 所需的完整参数命名空间。"""
    return Namespace(
        source='window', target='MuMu模拟器 1', output=output,
        upload=upload, image_host=image_host, config=config)


def run_cli(monkeypatch, tmp_path, config=None, image_host=None,
            upload=True, output=None, size=(4, 3)):
    """在隔离目录内执行一次上传调试，返回退出码、合成 PNG 与程序根目录。"""
    program_dir = tmp_path / 'program'
    program_dir.mkdir(exist_ok=True)
    png = make_png(size=size)
    result = CaptureResult(png, 'window', 'MuMu模拟器 1', size[0], size[1])
    patch_capture(monkeypatch, result)
    code = cli_module().run_screenshot_cli(
        args_for(output, upload, image_host, config), str(program_dir))
    return code, png, program_dir


def saved_pngs(program_dir):
    """列出默认截图目录内的调试产物。"""
    return sorted((program_dir / 'screenshot').glob('*.png'))


def test_upload_reuses_shared_fallback_entry():
    """上传复用通知路径使用的同一顺序故障转移入口。"""
    assert cli_module().upload_with_fallback is \
        registry_module().upload_with_fallback


def test_upload_creates_diagnostic_context_and_closes_it(
        monkeypatch, tmp_path):
    """上传链使用诊断上下文，并在调用结束后关闭上下文。"""
    cli = cli_module()
    config = write_config(tmp_path, config_with_hosts('{provider: local}'))
    events = []

    class Context:
        """记录 CLI 创建、传递及关闭上下文的测试替身。"""

        def __init__(self, *, diagnostics):
            """记录诊断开关。"""
            events.append(('init', diagnostics))

        def __enter__(self):
            """记录上下文进入。"""
            events.append('enter')
            return self

        def collect_secrets(self, hosts):
            """记录整条链秘密收集。"""
            events.append(('collect', hosts is not None))

        def __exit__(self, exc_type, exc_value, traceback):
            """记录上下文退出。"""
            events.append(('exit', exc_type))
            return False

    def uploader(png_bytes, filename, hosts, *, context=None):
        """记录外部上下文并返回成功结果。"""
        from modules.image_host.core import UploadResult
        events.append(('upload', context is not None))
        return UploadResult(True, 'local', URL, ('local',), ())

    monkeypatch.setattr(cli, 'UploadContext', Context)
    monkeypatch.setattr(cli, 'upload_with_fallback', uploader)
    run_cli(monkeypatch, tmp_path, config=config)

    assert events == [
        ('init', True), 'enter', ('collect', True),
        ('upload', True), ('exit', None)]


def test_cli_markdown_helper_is_pipeline_public_function():
    """CLI 使用的链接生成函数与 pipeline 公开接口是同一对象。"""
    module = import_module('modules.screenshot.pipeline')
    assert cli_module().markdown_image is module.markdown_image


def test_without_upload_reads_no_config_and_calls_no_uploader(
        monkeypatch, tmp_path):
    """不带 --upload 时不读取任何配置，也不触发任何图床调用。"""
    cli = cli_module()
    reader = Mock(side_effect=AssertionError('不应读取配置文件'))
    uploader = Mock(side_effect=AssertionError('不应调用上传入口'))
    monkeypatch.setattr(cli, '_read_yaml_config', reader)
    monkeypatch.setattr(cli, 'upload_with_fallback', uploader)
    load_spy = Mock()
    monkeypatch.setattr(
        import_module('modules.config'), 'load_config', load_spy)

    code, _, program_dir = run_cli(
        monkeypatch, tmp_path, config=str(tmp_path / 'absent.yaml'),
        upload=False)

    assert code == 0
    assert len(saved_pngs(program_dir)) == 1
    reader.assert_not_called()
    uploader.assert_not_called()
    load_spy.assert_not_called()


def test_image_host_without_upload_is_argument_error(monkeypatch, tmp_path):
    """未带 --upload 时提供 --image-host 按参数错误处理且不截图。"""
    program_dir = tmp_path / 'program'
    program_dir.mkdir()
    capture = patch_capture(
        monkeypatch, CaptureResult(make_png(), 'window', 'x', 4, 3))

    code = cli_module().run_screenshot_cli(
        args_for(output=None, upload=False, image_host='local'),
        str(program_dir))

    assert code == 2
    capture.assert_not_called()
    assert list(program_dir.iterdir()) == []


def test_upload_success_keeps_local_image_and_reports_details(
        monkeypatch, tmp_path, capsys):
    """带 --upload 成功时保留调试图片，并输出图床、地址与 Markdown 语法。"""
    calls = []

    def adapter(image_bytes, filename, token, options):
        """记录调用并返回候选直链。"""
        calls.append((image_bytes, filename, token, options))
        return URL

    install(monkeypatch, 'local', adapter)
    config = write_config(tmp_path, config_with_hosts(
        "{provider: local, token: '%s'}" % SECRET))

    code, png, program_dir = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 0
    saved = saved_pngs(program_dir)
    assert len(saved) == 1
    assert calls[0][0] == png
    assert calls[0][1].endswith('.png')
    assert calls[0][2] == SECRET
    out = capsys.readouterr().out
    assert 'local' in out
    assert URL in out
    assert f'![{saved[0].stem}]({URL})' in out


def test_upload_filename_matches_png_content_with_jpeg_output(
        monkeypatch, tmp_path, capsys):
    """显式 .jpg 输出时上传文件名与 PNG 内容一致，本地仍是真实 JPEG。"""
    calls = []

    def adapter(image_bytes, filename, token, options):
        """记录上传载荷与实际使用的文件名。"""
        calls.append((image_bytes, filename))
        return URL

    install(monkeypatch, 'local', adapter)
    config = write_config(tmp_path, config_with_hosts('{provider: local}'))
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)

    code, png, _ = run_cli(
        monkeypatch, tmp_path, config=config, output='shot.jpg')

    assert code == 0
    assert calls[0][0] == png
    assert calls[0][1] == 'shot.png'
    target = work / 'shot.jpg'
    assert target.read_bytes().startswith(b'\xff\xd8')
    with Image.open(target) as image:
        image.load()
        assert image.format == 'JPEG'
    assert f'![shot]({URL})' in capsys.readouterr().out


def test_missing_config_file_returns_one_without_uploading(
        monkeypatch, tmp_path, capsys):
    """配置文件缺失时明确报错返回 1，且不暗中调用任何图床。"""
    cli = cli_module()
    uploader = Mock(side_effect=AssertionError('缺配置时不应上传'))
    monkeypatch.setattr(cli, 'upload_with_fallback', uploader)

    code, _, program_dir = run_cli(
        monkeypatch, tmp_path, config=str(tmp_path / 'absent.yaml'))

    assert code == 1
    assert len(saved_pngs(program_dir)) == 1
    assert str(tmp_path / 'absent.yaml') not in capsys.readouterr().out
    uploader.assert_not_called()


def test_invalid_yaml_returns_one(monkeypatch, tmp_path, capsys):
    """YAML 解析失败时明确报错返回 1，并保留已保存的调试图片。"""
    config = write_config(tmp_path, 'push: [unterminated\n')

    code, _, program_dir = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 1
    assert len(saved_pngs(program_dir)) == 1
    assert '失败' in capsys.readouterr().out


def test_missing_image_host_section_returns_one(monkeypatch, tmp_path, capsys):
    """缺少 image_host 时不改用 targets，也不回退默认图床。"""
    config = write_config(tmp_path, config_with_hosts(
        targets=['{provider: window, target: MuMu模拟器 1}']))

    code, _, program_dir = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 1
    assert len(saved_pngs(program_dir)) == 1
    assert '失败' in capsys.readouterr().out


def test_empty_image_host_list_returns_one(monkeypatch, tmp_path, capsys):
    """image_host 为空列表时明确报错返回 1。"""
    config = write_config(
        tmp_path, 'push:\n  screenshot:\n    image_host: []\n')

    code, _, program_dir = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 1
    assert len(saved_pngs(program_dir)) == 1
    assert '失败' in capsys.readouterr().out


def test_image_host_not_a_list_returns_one(monkeypatch, tmp_path, capsys):
    """image_host 非列表时按配置错误返回 1。"""
    config = write_config(
        tmp_path, 'push:\n  screenshot:\n    image_host: nope\n')

    code, _, program_dir = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 1
    assert len(saved_pngs(program_dir)) == 1
    assert '失败' in capsys.readouterr().out


def test_filter_name_not_found_returns_one(monkeypatch, tmp_path, capsys):
    """--image-host 名称未找到时返回 1，且不调用任何图床。"""
    adapter = Mock(side_effect=AssertionError('不应调用图床'))
    install(monkeypatch, 'local', adapter)
    config = write_config(tmp_path, config_with_hosts('{provider: local}'))

    code, _, program_dir = run_cli(
        monkeypatch, tmp_path, config=config, image_host='absent')

    assert code == 1
    assert len(saved_pngs(program_dir)) == 1
    adapter.assert_not_called()
    assert '失败' in capsys.readouterr().out


def test_filter_duplicate_name_returns_one(monkeypatch, tmp_path, capsys):
    """--image-host 名称对应多项配置时返回 1，且不上传。"""
    adapter = Mock(side_effect=AssertionError('不应调用图床'))
    install(monkeypatch, 'local', adapter)
    config = write_config(tmp_path, config_with_hosts(
        '{provider: local, token: one}', '{provider: local, token: two}'))

    code, _, program_dir = run_cli(
        monkeypatch, tmp_path, config=config, image_host='local')

    assert code == 1
    assert len(saved_pngs(program_dir)) == 1
    adapter.assert_not_called()
    assert '失败' in capsys.readouterr().out


def test_filter_selects_named_item_only(monkeypatch, tmp_path, capsys):
    """--image-host 只保留被点名图床，并按其自身顺序执行。"""
    calls = []

    def first(*args):
        """记录不应发生的首站调用。"""
        calls.append('first')
        return URL

    def second(*args):
        """记录被选中的次站调用。"""
        calls.append('second')
        return URL

    install(monkeypatch, 'first', first)
    install(monkeypatch, 'second', second)
    config = write_config(tmp_path, config_with_hosts(
        '{provider: first}', '{provider: second}'))

    code, _, _ = run_cli(
        monkeypatch, tmp_path, config=config, image_host='second')

    assert code == 0
    assert calls == ['second']
    out = capsys.readouterr().out
    assert 'second' in out and URL in out


def test_filter_keeps_selected_item_credentials(monkeypatch, tmp_path):
    """--image-host 选中的项沿用自身 token，不借用其他项凭证。"""
    seen = []

    def alpha(image_bytes, filename, token, options):
        """记录首站调用。"""
        seen.append(('alpha', token))
        return URL

    def beta(image_bytes, filename, token, options):
        """记录被选中项收到的凭证。"""
        seen.append(('beta', token))
        return URL

    install(monkeypatch, 'alpha', alpha)
    install(monkeypatch, 'beta', beta)
    config = write_config(tmp_path, config_with_hosts(
        '{provider: alpha, token: one}', '{provider: beta, token: keep}'))

    code, _, _ = run_cli(
        monkeypatch, tmp_path, config=config, image_host='beta')

    assert code == 0
    assert seen == [('beta', 'keep')]


def test_first_host_failure_falls_back_to_second(
        monkeypatch, tmp_path, capsys):
    """首站失败后按原顺序尝试次站，次站成功即停。"""
    def fail(*args):
        """模拟首站携带响应原文的失败。"""
        raise RuntimeError('<html>' + SECRET + '</html>')

    install(monkeypatch, 'first', fail)
    install(monkeypatch, 'second', lambda *args: URL)
    config = write_config(tmp_path, config_with_hosts(
        "{provider: first, token: '%s'}" % SECRET, '{provider: second}'))

    code, _, program_dir = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 0
    assert len(saved_pngs(program_dir)) == 1
    out = capsys.readouterr()
    assert 'second' in out.out and URL in out.out
    assert SECRET not in out.out + out.err


def test_first_success_skips_second_host(monkeypatch, tmp_path):
    """首站成功后不再调用次站。"""
    second = Mock(side_effect=AssertionError('不应调用次站'))
    install(monkeypatch, 'first', lambda *args: URL)
    install(monkeypatch, 'second', second)
    config = write_config(tmp_path, config_with_hosts(
        '{provider: first}', '{provider: second}'))

    code, _, _ = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 0
    second.assert_not_called()


def test_duplicate_provider_without_filter_runs_in_order(
        monkeypatch, tmp_path):
    """不传筛选时按原列表顺序执行，允许同名多凭证项共存。"""
    calls = []

    def adapter(image_bytes, filename, token, options):
        """按凭证区分重复图床的调用顺序。"""
        calls.append(token)
        if token == 'one':
            raise RuntimeError('boom')
        return URL

    install(monkeypatch, 'same', adapter)
    config = write_config(tmp_path, config_with_hosts(
        '{provider: same, token: one}', '{provider: same, token: two}'))

    code, _, _ = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 0
    assert calls == ['one', 'two']


def test_all_hosts_fail_keeps_image_and_returns_one(
        monkeypatch, tmp_path, capsys):
    """全部图床失败时保留调试图片并返回 1，输出不含凭证与响应。"""
    def fail(*args):
        """模拟携带合成响应原文的失败。"""
        raise RuntimeError('response ' + SECRET)

    install(monkeypatch, 'local', fail)
    config = write_config(tmp_path, config_with_hosts(
        "{provider: local, token: '%s'}" % SECRET))

    code, _, program_dir = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 1
    assert len(saved_pngs(program_dir)) == 1
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert '失败' in captured.out
    assert SECRET not in text
    assert 'response' not in text
    assert 'PNG' not in text


def test_missing_environment_token_returns_one(monkeypatch, tmp_path, capsys):
    """凭证引用的环境变量缺失时返回 1，且不调用适配器。"""
    monkeypatch.delenv(ENV_NAME, raising=False)
    adapter = Mock(side_effect=AssertionError('不应调用适配器'))
    install(monkeypatch, 'local', adapter)
    config = write_config(tmp_path, config_with_hosts(
        "{provider: local, token: '${%s}'}" % ENV_NAME))

    code, _, program_dir = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 1
    assert len(saved_pngs(program_dir)) == 1
    adapter.assert_not_called()
    text = ''.join(capsys.readouterr())
    assert ENV_NAME not in text


def test_environment_token_is_resolved_without_touching_environment(
        monkeypatch, tmp_path):
    """环境引用在运行时解析，原样传值且不修改进程环境。"""
    monkeypatch.setenv(ENV_NAME, SECRET)
    seen = []

    def adapter(image_bytes, filename, token, options):
        """记录解析后的凭证并返回候选直链。"""
        seen.append(token)
        return URL

    install(monkeypatch, 'local', adapter)
    config = write_config(tmp_path, config_with_hosts(
        "{provider: local, token: '${%s}'}" % ENV_NAME))
    before = dict(os.environ)

    code, _, _ = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 0
    assert seen == [SECRET]
    assert dict(os.environ) == before


def test_output_hides_secrets_and_uses_readonly_config(
        monkeypatch, tmp_path, capsys):
    """输出不含凭证与响应原文，且不调用配置加载入口。"""
    def fail(*args):
        """模拟携带合成凭证与响应的失败。"""
        raise RuntimeError('<html>' + SECRET + '</html>')

    install(monkeypatch, 'local', fail)
    text = config_with_hosts("{provider: local, token: '%s'}" % SECRET)
    config = write_config(tmp_path, text)
    load_spy = Mock()
    monkeypatch.setattr(
        import_module('modules.config'), 'load_config', load_spy)

    code, _, _ = run_cli(monkeypatch, tmp_path, config=config)

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert code == 1
    assert SECRET not in output
    assert '<html>' not in output
    assert 'PNG' not in output
    load_spy.assert_not_called()


def test_config_file_is_left_untouched(monkeypatch, tmp_path):
    """只读解析不创建、不改写配置文件。"""
    text = config_with_hosts("{provider: local, token: 'one'}")
    config = write_config(tmp_path, text)
    install(monkeypatch, 'local', lambda *args: URL)

    code, _, _ = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 0
    with open(config, encoding='utf-8') as handle:
        assert handle.read() == text


def test_existing_target_skips_upload_and_returns_one(
        monkeypatch, tmp_path, capsys):
    """调试图片因目标已存在保存失败时返回 1，且不上传。"""
    cli = cli_module()
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    target = work / 'shot.png'
    target.write_bytes(b'user-data')
    uploader = Mock(side_effect=AssertionError('保存失败时不应上传'))
    monkeypatch.setattr(cli, 'upload_with_fallback', uploader)
    config = write_config(tmp_path, config_with_hosts('{provider: local}'))

    code, _, _ = run_cli(
        monkeypatch, tmp_path, config=config, output='shot.png')

    assert code == 1
    assert target.read_bytes() == b'user-data'
    uploader.assert_not_called()
    assert '目标文件已存在' in capsys.readouterr().out
