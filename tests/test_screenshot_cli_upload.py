#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证截图调试上传：只读配置、顺序故障转移、安全输出与退出码。

截图边界使用真实 Pillow 合成图片；基础测试使用本地图床桩件，贯通测试
保留真实注册表、上下文、缓存及传输逻辑，仅替换 HTTP 适配器边界。
配置与缓存写入临时目录，绝不执行真实截图、网络或真实上传。
"""

from argparse import Namespace
import builtins
from copy import deepcopy
from email.parser import BytesParser
from importlib import import_module
from importlib.util import find_spec
from io import BytesIO
import json
import netrc
import os
from pathlib import Path
import socket
from unittest.mock import Mock

from PIL import Image
import pytest
import requests

from modules.screenshot.models import CaptureResult


URL = 'https://cdn.example.com/image.png'
SECRET = 'FAKE_UPLOAD_SECRET_6120'
ENV_NAME = 'SCREENSHOT_UPLOAD_TEST_TOKEN'
FINAL_FAILURE = '上传失败：所有图床尝试均未成功'


@pytest.fixture(autouse=True)
def isolate_cli_uploads(monkeypatch, tmp_path):
    """所有上传测试仅使用临时缓存，禁止真实网络及本机凭证读取。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))

    def forbidden(*args, **kwargs):
        """任何未替换的网络或 netrc 边界均立即使测试失败。"""
        pytest.fail('禁止真实网络或 netrc 访问')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', forbidden)
    monkeypatch.setattr(requests.sessions, 'get_netrc_auth', forbidden)
    monkeypatch.setattr(requests.utils, 'get_netrc_auth', forbidden)
    monkeypatch.setattr(netrc, 'netrc', forbidden)


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
        ('init', True), 'enter', ('upload', True), ('exit', None)]


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


class CliRaw(BytesIO):
    """为真实 Requests 响应提供可关闭的合成正文流。"""

    def release_conn(self):
        """释放合成连接，不访问任何外部资源。"""
        self.close()


@pytest.fixture
def cli_http(monkeypatch, tmp_path):
    """保留真实上传内部组件，仅观察生命周期并替换 HTTP 边界。"""
    context_type = import_module('modules.image_host.context').UploadContext
    initialize = context_type.__init__
    close = context_type.close
    diagnostics = import_module('modules.image_host.diagnostics')
    state = {'requests': [], 'replies': [], 'responses': [],
             'contexts': [], 'closed': [], 'saved_before_request': [],
             'saved_paths': None, 'outer_scopes': [], 'closing_scopes': []}

    def observed_init(self, **kwargs):
        """调用真实构造方法并记录操作开始时的外层诊断作用域。"""
        assert kwargs == {'diagnostics': True}
        initialize(self, **kwargs)
        state['contexts'].append(self)
        state['outer_scopes'].append(diagnostics._CURRENT_SECRETS.get())

    def observed_close(self):
        """观察编排作用域先退出，再执行真实状态释放。"""
        state['closing_scopes'].append(diagnostics._CURRENT_SECRETS.get())
        close(self)
        state['closed'].append(self)

    def send(adapter, request, **kwargs):
        """返回队列中的合成响应，保留最终 PreparedRequest 供断言。"""
        assert isinstance(request, requests.PreparedRequest)
        assert kwargs['stream'] is True
        assert kwargs['verify'] is True
        saved = state['saved_paths']
        if saved is None:
            saved = list((tmp_path / 'program' / 'screenshot').glob('*.png'))
        if not saved or not all(path.is_file() for path in saved):
            pytest.fail('HTTP 请求前本地图片必须已经保存')
        state['saved_before_request'].append(saved)
        state['requests'].append(request)
        assert state['replies'], '发生未批准的额外 HTTP 请求'
        reply = state['replies'].pop(0)
        if callable(reply):
            reply = reply()
        if isinstance(reply, BaseException):
            raise reply
        status, payload = reply
        response = requests.Response()
        response.status_code = status
        response.url = request.url
        response.request = request
        if isinstance(payload, CliRaw):
            response.raw = payload
        else:
            body = (payload if isinstance(payload, bytes)
                    else json.dumps(payload).encode('utf-8'))
            response.raw = CliRaw(body)
        state['responses'].append(response)
        return response

    monkeypatch.setattr(context_type, '__init__', observed_init)
    monkeypatch.setattr(context_type, 'close', observed_close)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    yield state
    assert all(response.raw.closed for response in state['responses'])
    assert state['closed'] == state['contexts']
    assert state['closing_scopes'] == state['outer_scopes']
    for context in state['contexts']:
        assert context._closed
        assert not context._entries and not context._images
        assert not context._credentials and not context._secrets
        assert context._cache is None
    assert not state['replies']


def group_reply(ids=(13, 14), retention=None):
    """提供合成用户组存储列表及可选期限，不包含真实账号信息。"""
    return 200, {'status': 'success', 'data': {
        'group': {'options': {'file_expire_seconds': retention}},
        'storages': [{'id': value} for value in ids],
    }}


def profile_reply(default=14):
    """提供合成默认存储编号。"""
    return 200, {'status': 'success', 'data': {
        'options': {'default_storage_id': default},
    }}


def upload_reply(url=URL):
    """提供完整业务成功链接，响应提示不参与成功输出。"""
    return 200, {'status': 'success', 'message': SECRET,
                 'data': {'public_url': url}}


def test_real_business_rejection_prints_history_then_final_conclusion(
        cli_http, monkeypatch, tmp_path, capsys):
    """真实业务拒绝先展示安全历史，再恰好输出一次固定失败结论。"""
    cli_http['replies'] = [
        (200, {'status': 'error', 'message': '请先绑定手机号'}),
    ]
    config = write_config(tmp_path, config_with_hosts(
        "{provider: boltp, token: '%s'}" % SECRET))

    code, png, program_dir = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 1
    saved, = saved_pngs(program_dir)
    assert saved.read_bytes() == png
    assert all(paths == [saved]
               for paths in cli_http['saved_before_request'])
    assert [request.method for request in cli_http['requests']] == ['POST']
    captured = capsys.readouterr()
    history = ('上传尝试：boltp：图床拒绝上传（阶段 upload，HTTP 200）'
               '；诊断：请先绑定手机号')
    assert captured.out.count(history) == 1
    assert captured.out.count('上传尝试：') == 1
    assert captured.out.count(FINAL_FAILURE) == 1
    assert captured.out.index(history) < captured.out.index(FINAL_FAILURE)
    assert captured.out.rstrip().endswith(FINAL_FAILURE)
    assert '上传成功：' not in captured.out
    assert SECRET not in captured.out + captured.err


def test_real_registry_alone_collects_once_and_freezes_chain_credentials(
        cli_http, monkeypatch, tmp_path, capsys, caplog):
    """真实编排独占一次凭证收集，冻结备用凭证并保护整链诊断。"""
    cli = cli_module()
    context_module = import_module('modules.image_host.context')
    collect = context_module.UploadContext.collect_secrets
    resolve = context_module.resolve_token
    upload = registry_module().upload_with_fallback
    backup = 'FAKE_CLI_BACKUP_9347'
    changed = 'FAKE_CLI_CHANGED_2851'
    backup_env = ENV_NAME + '_BACKUP'
    monkeypatch.setenv(ENV_NAME, SECRET)
    monkeypatch.setenv(backup_env, backup)
    calls = {'collect': [], 'resolve': [], 'upload': []}
    uploading = [False]
    caplog.set_level('DEBUG')

    def observed_collect(self, hosts):
        """记录调用是否来自上传编排，继续执行真实秘密收集。"""
        calls['collect'].append((self, uploading[0]))
        return collect(self, hosts)

    def observed_resolve(value):
        """观察解析次数，不替代凭证解析及其错误处理。"""
        calls['resolve'].append(value)
        return resolve(value)

    def observed_upload(png_bytes, filename, hosts, *, context=None):
        """透传原始编排调用并界定其执行区间。"""
        calls['upload'].append(context)
        uploading[0] = True
        try:
            return upload(png_bytes, filename, hosts, context=context)
        finally:
            uploading[0] = False

    def change_environment_after_collection():
        """在首次请求边界变更合成环境，验证后续仍使用旧快照。"""
        monkeypatch.setenv(ENV_NAME, changed)
        monkeypatch.setenv(backup_env, changed)
        return group_reply()

    monkeypatch.setattr(context_module.UploadContext, 'collect_secrets',
                        observed_collect)
    monkeypatch.setattr(context_module, 'resolve_token', observed_resolve)
    monkeypatch.setattr(cli, 'upload_with_fallback', observed_upload)
    cli_http['replies'] = [
        change_environment_after_collection, profile_reply(),
        (200, {'status': 'error', 'message':
               f'拒绝 {SECRET} {backup} ${{{backup_env}}}'}),
        upload_reply(),
    ]
    config = write_config(tmp_path, config_with_hosts(
        "{provider: beeimg_cn, token: '${%s}'}" % ENV_NAME,
        "{provider: boltp, token: '${%s}'}" % backup_env))

    code, png, program_dir = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 0
    context, = cli_http['contexts']
    assert calls['upload'] == [context]
    assert calls['resolve'] == [f'${{{ENV_NAME}}}', f'${{{backup_env}}}']
    assert [request.method for request in cli_http['requests']] == [
        'GET', 'GET', 'POST', 'POST']
    assert [request.headers.get('Authorization')
            for request in cli_http['requests']] == (
                ['Bearer ' + SECRET] * 3 + ['Bearer ' + backup])
    saved, = saved_pngs(program_dir)
    assert saved.read_bytes() == png
    captured = capsys.readouterr()
    text = captured.out + captured.err + caplog.text
    for secret in (SECRET, backup, changed, ENV_NAME, backup_env):
        assert secret not in text
    assert captured.out.index('上传尝试：') < captured.out.index('上传成功：')
    assert FINAL_FAILURE not in captured.out
    assert URL in captured.out
    assert context._closed
    assert calls['collect'] == [(context, True)]


def multipart_parts(request):
    """解析最终请求的 multipart 字节，不依赖随机分隔符。"""
    message = BytesParser().parsebytes(
        ('Content-Type: ' + request.headers['Content-Type']
         + '\r\nMIME-Version: 1.0\r\n\r\n').encode('ascii')
        + request.body)
    return {part.get_param('name', header='content-disposition'): part
            for part in message.get_payload()}


@pytest.mark.parametrize('provider, token, suffix', [
    ('beeimg_cn', SECRET, '.png'),
    ('boltp', SECRET, '.png'),
    ('boltp', '', '.png'),
    ('beeimg_cn', SECRET, '.jpg'),
])
def test_real_cold_upload_preserves_request_image_and_signed_url(
        cli_http, monkeypatch, tmp_path, capsys, provider, token, suffix):
    """两站冷缓存及匿名成功保留真实载荷、文件类型和完整签名链接。"""
    signed_url = URL + '?signature=FAKE_SUCCESS_SIGNATURE&path=a%2Fb#image'
    config = write_config(tmp_path, config_with_hosts(
        "{provider: %s, token: '%s'}" % (provider, token)))
    original_config = Path(config).read_bytes()
    original_environment = dict(os.environ)
    target = tmp_path / ('capture' + suffix)
    cli_http['saved_paths'] = [target]
    cli_http['replies'] = []
    if provider == 'beeimg_cn':
        cli_http['replies'].append(group_reply())
        if token:
            cli_http['replies'].append(profile_reply())
    cli_http['replies'].append(upload_reply(signed_url))

    code, png, _ = run_cli(
        monkeypatch, tmp_path, config=config, output=str(target))

    assert code == 0
    requests_seen = cli_http['requests']
    expected_paths = (['/group', '/user/profile', '/upload'] if token
                      else ['/group', '/upload']) if provider == 'beeimg_cn' else ['/upload']
    base = ('https://www.beeimg.cn/api/v2' if provider == 'beeimg_cn'
            else 'https://www.boltp.com/api/v2')
    assert [request.url for request in requests_seen] == [
        base + path for path in expected_paths]
    assert [request.method for request in requests_seen] == (
        ['GET'] * (len(expected_paths) - 1) + ['POST'])
    for request in requests_seen:
        assert request.headers.get('Authorization') == (
            'Bearer ' + token if token else None)
        assert request.headers['Accept'] == 'application/json'
    parts = multipart_parts(requests_seen[-1])
    assert set(parts) == {'file', 'storage_id', 'is_public'}
    assert parts['file'].get_filename() == 'capture.png'
    assert parts['file'].get_content_type() == 'image/png'
    assert parts['file'].get_payload(decode=True) == png
    assert parts['storage_id'].get_payload(decode=True) == (
        (b'14' if token else b'13') if provider == 'beeimg_cn' else b'2')
    assert parts['is_public'].get_payload(decode=True) == b'1'
    with Image.open(target) as image:
        image.load()
        assert image.format == ('JPEG' if suffix == '.jpg' else 'PNG')
        assert image.size == (4, 3)
    if suffix == '.png':
        assert target.read_bytes() == png
    else:
        assert target.read_bytes().startswith(b'\xff\xd8')
        assert target.read_bytes() != png
    out = capsys.readouterr().out
    assert f'图片地址：{signed_url}\n' in out
    assert f'Markdown：![capture]({signed_url})\n' in out
    assert '上传尝试：' not in out and '上传失败：' not in out
    assert '期限' not in out
    assert len(cli_http['contexts']) == 1
    assert Path(config).read_bytes() == original_config
    assert dict(os.environ) == original_environment


class CliReadFailure(CliRaw):
    """在响应读取边界抛出合成异常，保留真实传输异常处理。"""

    def read(self, *args, **kwargs):
        """模拟读取超时，不暴露合成敏感异常内容。"""
        raise requests.exceptions.ReadTimeout('RAW_READ_' + SECRET)


@pytest.mark.parametrize('case, summary', [
    ('http', '图床 HTTP 请求失败'),
    ('connect', '图床网络传输失败'),
    ('read', '图床网络传输失败'),
    ('malformed', '图床响应无效'),
])
def test_real_transport_failures_only_display_safe_summaries(
        cli_http, monkeypatch, tmp_path, capsys, caplog, case, summary):
    """非成功状态、连接、读取与畸形正文只显示固定摘要且不丢图片。"""
    replies = {
        'http': (503, {'message': 'RAW_HTTP_' + SECRET}),
        'connect': requests.exceptions.ConnectionError('RAW_CONNECT_' + SECRET),
        'read': (200, CliReadFailure(b'')),
        'malformed': (200, ('RAW_MALFORMED_' + SECRET).encode()),
    }
    cli_http['replies'] = [replies[case]]
    config = write_config(tmp_path, config_with_hosts(
        "{provider: boltp, token: '%s'}" % SECRET))
    caplog.set_level('DEBUG')

    code, png, program_dir = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 1
    saved, = saved_pngs(program_dir)
    assert saved.read_bytes() == png
    out, err = capsys.readouterr()
    assert out.count('上传尝试：boltp：' + summary) == 1
    assert out.count(FINAL_FAILURE) == 1
    assert out.index('上传尝试：') < out.index(FINAL_FAILURE)
    assert '；诊断：' not in out
    assert 'RAW_' not in out + err + caplog.text
    assert SECRET not in out + err + caplog.text
    assert [request.method for request in cli_http['requests']] == [
        'POST']


@pytest.mark.parametrize('provider', ['beeimg_cn', 'boltp'])
def test_real_exact_storage_rejection_recovers_once_before_success(
        cli_http, monkeypatch, tmp_path, capsys, provider):
    """精确拒绝刷新一次；Boltp 仅验证合成兼容策略而非真实站点行为。"""
    cli_http['replies'] = (
        [group_reply(), profile_reply(),
         (200, {'status': 'error', 'message': '不存在的储存驱动'}),
         group_reply((21,)), profile_reply(21), upload_reply()]
        if provider == 'beeimg_cn' else [
            (200, {'status': 'error', 'message': '不存在的储存驱动'}),
            upload_reply()])
    config = write_config(tmp_path, config_with_hosts(
        "{provider: %s, token: '%s'}" % (provider, SECRET)))

    code, png, program_dir = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 0
    expected_methods = (['GET', 'GET', 'POST', 'GET', 'GET', 'POST']
                        if provider == 'beeimg_cn' else ['POST', 'POST'])
    assert [request.method for request in cli_http['requests']] == expected_methods
    posts = [request for request in cli_http['requests'] if request.method == 'POST']
    expected_storage_ids = ([b'14', b'21'] if provider == 'beeimg_cn'
                            else [b'2', b'3'])
    assert [multipart_parts(request)['storage_id'].get_payload(decode=True)
            for request in posts] == expected_storage_ids
    assert all(multipart_parts(request)['file'].get_payload(decode=True) == png
               for request in posts)
    out = capsys.readouterr().out
    assert out.count('上传尝试：') == (1 if provider == 'beeimg_cn' else 0)
    if provider == 'beeimg_cn':
        assert '储存驱动不可用（阶段 upload，HTTP 200）' in out
        assert out.index('上传尝试：') < out.index('上传成功：')
    assert FINAL_FAILURE not in out and URL in out
    assert saved_pngs(program_dir)[0].read_bytes() == png


@pytest.mark.parametrize('success', [True, False])
def test_real_registry_warnings_remain_ordered_and_unique(
        cli_http, monkeypatch, tmp_path, capsys, caplog, success):
    """真实缓存故障与两站重复告警在成功、失败时都保序去重展示。"""
    cache_type = import_module('modules.image_host.storage_cache').StorageCache

    def deny_write(self, destination, body):
        """仅阻断临时缓存记录的原子写入，保留其真实故障分类。"""
        assert tmp_path in destination.parents
        raise OSError('RAW_CACHE_' + SECRET)

    def other_site(image_bytes, filename, token, options):
        """非 v2 站仅替换适配器边界，期限告警仍由真实编排产生。"""
        if not success:
            raise RuntimeError('RAW_OTHER_' + SECRET)
        return URL

    monkeypatch.setattr(cache_type, '_write_atomic', deny_write)
    install(monkeypatch, 'catbox', other_site)
    reject = (200, {'status': 'error', 'message': '请先绑定手机号'})
    cli_http['replies'] = [group_reply(retention=60), profile_reply(), reject,
                           group_reply(retention=60), reject]
    config = write_config(tmp_path, config_with_hosts(
        "{provider: beeimg_cn, token: '%s', expiration: 2m, "
        "options: {storage_id: 99}}" % SECRET,
        "{provider: boltp, token: '%s', expiration: 2m, "
        "options: {storage_id: 99}}" % SECRET,
        '{provider: catbox, expiration: 2m}'))
    caplog.set_level('DEBUG')

    code, png, program_dir = run_cli(monkeypatch, tmp_path, config=config)

    assert code == (0 if success else 1)
    out, err = capsys.readouterr()
    warnings = ['存储元数据缓存保存失败', '手填储存驱动不可用，已自动替代',
                '保存期限已按图床上限缩短', '该图床不支持保存期限，已忽略']
    assert [line for line in out.splitlines() if line.startswith('提示：')] == [
        '提示：' + warning for warning in warnings]
    assert out.index(warnings[-1]) < out.index('上传尝试：')
    assert out.index('上传尝试：') < out.index(
        '上传成功：' if success else FINAL_FAILURE)
    assert out.count(FINAL_FAILURE) == (0 if success else 1)
    assert 'RAW_' not in out + err + caplog.text
    assert SECRET not in out + err + caplog.text
    assert [request.method for request in cli_http['requests']] == [
        'GET', 'GET', 'POST', 'GET', 'POST']
    for request in cli_http['requests']:
        if request.method == 'POST':
            parts = multipart_parts(request)
            assert 'expired_at' in parts
            assert parts['storage_id'].get_payload(decode=True) == (
                b'14' if 'www.beeimg.cn' in request.url else b'99')
    assert saved_pngs(program_dir)[0].read_bytes() == png


@pytest.mark.parametrize('case', [
    'credentials', 'signed_url', 'terminal', 'html', 'long', 'oversized',
])
def test_real_diagnostics_hide_unused_backup_and_untrusted_messages(
        cli_http, monkeypatch, tmp_path, capsys, caplog, case):
    """真实失败诊断保护当前和未尝试备用秘密，不输出原始响应对象。"""
    backup = 'FAKE_UNUSED_BACKUP_7621'
    backup_env = ENV_NAME + '_UNUSED'
    monkeypatch.setenv(backup_env, backup)
    messages = {
        'credentials': f'请检查 {SECRET} {backup} ${{{backup_env}}}',
        'signed_url': '请检查 https://invalid.example/image?sig=FAKE_ERROR_SIG',
        'terminal': '\x1b[31m请重试\x1b[0m\x1b]0;OSC_PRIVATE_TITLE\x07',
        'html': '<html>PRIVATE_HTML_BODY</html>',
        'long': '请稍后重试' * 100 + backup,
        'oversized': '请稍后重试' * 1000 + backup,
    }
    cli_http['replies'] = [
        (200, {'status': 'error', 'message': messages[case]}),
        group_reply(), profile_reply(), upload_reply(),
    ]
    config = write_config(tmp_path, config_with_hosts(
        "{provider: boltp, token: '%s'}" % SECRET,
        '{provider: beeimg_cn, token: FAKE_SECOND_TOKEN}',
        "{provider: boltp, token: '${%s}'}" % backup_env))
    caplog.set_level('DEBUG')

    code, _, _ = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 0
    out, err = capsys.readouterr()
    combined = out + err + caplog.text
    for forbidden in (SECRET, backup, backup_env, 'FAKE_SECOND_TOKEN',
                      'FAKE_ERROR_SIG', 'invalid.example', '\x1b', '\x07',
                      'OSC_PRIVATE_TITLE', 'PRIVATE_HTML_BODY', '<html>',
                      'PreparedRequest', 'UploadResult(', '<Response'):
        assert forbidden not in combined
    assert out.count('上传尝试：') == 1
    assert '图床拒绝上传（阶段 upload，HTTP 200）' in out
    assert out.index('上传尝试：') < out.index('上传成功：')
    assert FINAL_FAILURE not in out
    if case == 'terminal':
        assert '请重试' in out
    for line in out.splitlines():
        if '；诊断：' in line:
            assert len(line.split('；诊断：', 1)[1]) <= 200
    assert [request.method for request in cli_http['requests']] == [
        'POST', 'GET', 'GET', 'POST']
    assert all(request.headers.get('Authorization') != 'Bearer ' + backup
               for request in cli_http['requests'])


@pytest.mark.parametrize('first_success', [True, False])
def test_real_invalid_backup_credential_is_deferred_until_attempted(
        cli_http, monkeypatch, tmp_path, capsys, first_success):
    """备用项凭证错误预收集但延迟呈现，首站成功不受影响。"""
    missing = ENV_NAME + '_ABSENT'
    monkeypatch.delenv(missing, raising=False)
    cli_http['replies'] = [upload_reply() if first_success else
                           (200, {'status': 'error', 'message': '请先绑定手机号'})]
    config = write_config(tmp_path, config_with_hosts(
        "{provider: boltp, token: '%s'}" % SECRET,
        "{provider: beeimg_cn, token: '${%s}'}" % missing))

    code, _, _ = run_cli(monkeypatch, tmp_path, config=config)

    assert code == (0 if first_success else 1)
    out, err = capsys.readouterr()
    assert ('图床凭证环境变量缺失或为空' in out) is not first_success
    assert out.count('上传尝试：') == (0 if first_success else 2)
    assert out.count(FINAL_FAILURE) == (0 if first_success else 1)
    assert missing not in out + err and SECRET not in out + err
    assert [request.method for request in cli_http['requests']] == [
        'POST']


@pytest.mark.parametrize('case, expected', [
    ('no_upload', 0), ('missing_config', 1), ('invalid_yaml', 1),
    ('unsafe_yaml', 1), ('filter', 1), ('save_error', 1), ('exists', 1),
])
def test_early_exit_never_creates_context_or_touches_upload_io(
        cli_http, monkeypatch, tmp_path, case, expected):
    """保存、加载、筛选前置失败及无上传操作均不触发上下文和缓存。"""
    cli = cli_module()
    cache_type = import_module('modules.image_host.storage_cache').StorageCache

    def forbidden(*args, **kwargs):
        """提前退出时禁止缓存、凭证解析和完整配置加载。"""
        pytest.fail('提前退出路径不得触发上传资源')

    for method in ('identity', 'load', 'save', 'invalidate', '_prepare'):
        monkeypatch.setattr(cache_type, method, forbidden)
    monkeypatch.setattr(import_module('modules.image_host.context'),
                        'resolve_token', forbidden)
    monkeypatch.setattr(import_module('modules.config'), 'load_config', forbidden)
    config = write_config(tmp_path, config_with_hosts(
        '{provider: boltp, token: FAKE_EARLY_TOKEN}'))
    original = Path(config).read_bytes()
    kwargs = {'config': config}
    if case == 'no_upload':
        kwargs['upload'] = False
        monkeypatch.setattr(cli, '_read_yaml_config', forbidden)
    elif case == 'missing_config':
        kwargs['config'] = str(tmp_path / 'absent.yaml')
    elif case in ('invalid_yaml', 'unsafe_yaml'):
        text = ('push: [unterminated\n' if case == 'invalid_yaml' else
                '!!python/object/apply:builtins.str [unsafe]\n')
        kwargs['config'] = write_config(tmp_path, text, 'invalid.yaml')
    elif case == 'filter':
        kwargs['image_host'] = 'absent'
    elif case == 'save_error':
        def denied(*args, **kwargs):
            """仅在保存文件边界模拟写入失败。"""
            raise OSError('FAKE_SAVE_FAILURE')

        monkeypatch.setattr(cli, '_write_exclusive_file', denied)
        monkeypatch.setattr(cli, '_read_yaml_config', forbidden)
    elif case == 'exists':
        existing = tmp_path / 'existing.png'
        existing.write_bytes(b'user-owned-test-data')
        kwargs['output'] = str(existing)
        monkeypatch.setattr(cli, '_read_yaml_config', forbidden)

    code, _, _ = run_cli(monkeypatch, tmp_path, **kwargs)

    assert code == expected
    assert cli_http['contexts'] == cli_http['requests'] == []
    assert not (tmp_path / 'local').exists()
    assert Path(config).read_bytes() == original
    if case == 'exists':
        assert existing.read_bytes() == b'user-owned-test-data'


@pytest.mark.parametrize('outcome', [
    'success', 'failure', 'keyboard', 'system_exit', 'display_error',
])
def test_real_context_closes_after_scope_exit_and_preserves_signals(
        cli_http, monkeypatch, tmp_path, outcome):
    """正常、失败和异常路径均关闭上下文，并原样传播控制信号。"""
    diagnostics = import_module('modules.image_host.diagnostics')
    outer_secret = 'FAKE_OUTER_SCOPE_1835'
    signal = {'keyboard': KeyboardInterrupt('FAKE_SIGNAL'),
              'system_exit': SystemExit(19),
              'display_error': RuntimeError('FAKE_DISPLAY')}.get(outcome)
    response = (200, {'status': 'error', 'message': '请先绑定手机号'})
    if outcome in ('success', 'display_error'):
        response = upload_reply()
    elif outcome in ('keyboard', 'system_exit'):
        response = signal
    cli_http['replies'] = [response]
    config = write_config(tmp_path, config_with_hosts(
        "{provider: boltp, token: '%s'}" % SECRET))
    if outcome == 'display_error':
        def fail_display(*args, **kwargs):
            """仅在最终成功展示处模拟输出设备异常。"""
            if args and str(args[0]).startswith('上传成功：'):
                raise signal
            return builtins.print(*args, **kwargs)

        monkeypatch.setattr(cli_module(), 'print', fail_display, raising=False)
    before = diagnostics._CURRENT_SECRETS.get()
    with diagnostics.diagnostic_scope((outer_secret,)):
        if signal is not None:
            with pytest.raises(type(signal)) as caught:
                run_cli(monkeypatch, tmp_path, config=config)
            assert caught.value is signal
        else:
            code, _, _ = run_cli(monkeypatch, tmp_path, config=config)
            assert code == (0 if outcome == 'success' else 1)
        assert diagnostics._CURRENT_SECRETS.get() == (outer_secret,)
        assert cli_http['closing_scopes'] == [(outer_secret,)]
        assert cli_http['contexts'][0]._closed
    assert diagnostics._CURRENT_SECRETS.get() == before
    assert len(saved_pngs(tmp_path / 'program')) == 1
    assert [request.method for request in cli_http['requests']] == [
        'POST']


def test_real_consecutive_operations_only_reuse_disk_metadata(
        cli_http, monkeypatch, tmp_path, capsys):
    """连续操作重新截图、上传、建上下文，仅复用有效磁盘元数据。"""
    config = write_config(tmp_path, config_with_hosts(
        "{provider: beeimg_cn, token: '%s'}" % SECRET))
    cli_http['replies'] = [group_reply(), profile_reply(), upload_reply(),
                           upload_reply(URL + '?second=1')]
    captured_sizes = []
    real_patch_capture = patch_capture

    def observe_capture(patch, result):
        """记录每次真实 Pillow 合成截图被 CLI 获取，不复用上次结果。"""
        mock = real_patch_capture(patch, result)
        captured_sizes.append((result, mock))
        return mock

    monkeypatch.setattr(import_module(__name__), 'patch_capture', observe_capture)
    first, first_png, program_dir = run_cli(
        monkeypatch, tmp_path, config=config, size=(4, 3))
    second, second_png, _ = run_cli(
        monkeypatch, tmp_path, config=config, size=(5, 4))

    assert first == second == 0
    assert len(captured_sizes) == 2
    assert all(mock.call_count == 1 for _, mock in captured_sizes)
    assert len(cli_http['contexts']) == 2
    assert cli_http['contexts'][0] is not cli_http['contexts'][1]
    assert [request.method for request in cli_http['requests']] == [
        'GET', 'GET', 'POST', 'POST']
    posts = [request for request in cli_http['requests'] if request.method == 'POST']
    assert [multipart_parts(request)['file'].get_payload(decode=True)
            for request in posts] == [first_png, second_png]
    saved = saved_pngs(program_dir)
    assert len(saved) == 2 and saved[0] != saved[1]
    assert {path.read_bytes() for path in saved} == {first_png, second_png}
    out = capsys.readouterr().out
    assert out.count('上传成功：') == 2
    assert f'图片地址：{URL}\n' in out
    assert f'图片地址：{URL}?second=1\n' in out
    cache_files = list((tmp_path / 'local' / '2RPM' / 'image_host').glob('*.json'))
    assert len(cache_files) == 1
    cache_text = cache_files[0].read_text(encoding='utf-8')
    assert URL not in cache_text and SECRET not in cache_text
    assert set(json.loads(cache_text)) == {
        'version', 'provider', 'fetched_at', 'storage_ids',
        'default_storage_id', 'file_expire_seconds'}


def test_real_safe_yaml_filter_and_nested_options_remain_unchanged(
        cli_http, monkeypatch, tmp_path):
    """真实只读 safe YAML 与筛选重排不改变配置、嵌套选项或环境。"""
    cli = cli_module()
    reader = cli._read_yaml_config
    yaml_factory = cli.YAML
    loaded = []
    modes = []
    yaml_modes = []

    def observe_read(path):
        """调用真实配置读取并保存返回对象供操作后比较。"""
        data = reader(path)
        loaded.append((data, deepcopy(data)))
        return data

    def observe_open(path, mode, **kwargs):
        """记录 CLI 文件打开模式并调用真实文件操作。"""
        modes.append((str(path), mode))
        return builtins.open(path, mode, **kwargs)

    def observe_yaml(*args, **kwargs):
        """记录 YAML 安全模式，返回真实解析器。"""
        yaml_modes.append(kwargs.get('typ'))
        return yaml_factory(*args, **kwargs)

    def forbidden(*args, **kwargs):
        """不得调用具有迁移或写回行为的完整配置入口。"""
        pytest.fail('不得调用 load_config')

    monkeypatch.setattr(cli, '_read_yaml_config', observe_read)
    monkeypatch.setattr(cli, 'open', observe_open, raising=False)
    monkeypatch.setattr(cli, 'YAML', observe_yaml)
    monkeypatch.setattr(import_module('modules.config'), 'load_config', forbidden)
    config = write_config(tmp_path, config_with_hosts(
        "{provider: beeimg_cn, token: '%s', options: "
        "{album_id: 7, extra: {nested: [1, 2]}}}" % SECRET,
        '{provider: boltp, token: FAKE_FILTER_TOKEN}'))
    config_before = Path(config).read_bytes()
    environment_before = dict(os.environ)
    cli_http['replies'] = [
        (200, {'status': 'error', 'message': '请先绑定手机号'}),
        group_reply(), profile_reply(), upload_reply(),
    ]

    code, _, _ = run_cli(monkeypatch, tmp_path, config=config,
                         image_host=' boltp, beeimg_cn ')

    assert code == 0
    assert yaml_modes == ['safe']
    assert [(path, mode) for path, mode in modes if path == config] == [(config, 'r')]
    assert len(loaded) == 1 and loaded[0][0] == loaded[0][1]
    assert Path(config).read_bytes() == config_before
    assert dict(os.environ) == environment_before
    assert [request.url.split('/api/v2')[0] for request in cli_http['requests']] == (
        ['https://www.boltp.com'] + ['https://www.beeimg.cn'] * 3)
    assert multipart_parts(cli_http['requests'][-1])['album_id'].get_payload(
        decode=True) == b'7'


def test_empty_failure_history_keeps_one_safe_final_conclusion(
        monkeypatch, tmp_path, capsys):
    """防御性空历史结果仍恰好输出一条安全结论，不伪造尝试记录。"""
    core = import_module('modules.image_host.core')
    monkeypatch.setattr(cli_module(), 'upload_with_fallback',
                        lambda *args, **kwargs: core.UploadResult(
                            False, None, None, (), ()))
    config = write_config(tmp_path, config_with_hosts('{provider: local}'))

    code, png, program_dir = run_cli(monkeypatch, tmp_path, config=config)

    assert code == 1
    out = capsys.readouterr().out
    assert out.count('上传失败：图床未返回有效链接') == 1
    assert out.count('上传失败：') == 1 and '上传尝试：' not in out
    assert saved_pngs(program_dir)[0].read_bytes() == png
