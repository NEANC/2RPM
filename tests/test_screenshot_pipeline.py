#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证多目标截图编排及仅追加的正文提示，不访问真实设备或图床。"""

from collections import UserDict
from copy import deepcopy
from dataclasses import FrozenInstanceError
from importlib import import_module
from importlib.util import find_spec
from inspect import signature
import netrc
import os
import socket
from unittest.mock import Mock

import pytest
import requests

from modules.image_host.context import UploadContext
from modules.image_host.core import ImageHostError
from modules.image_host.core import UploadFailure
from modules.image_host.core import UploadResult
from modules.screenshot.models import CaptureError
from modules.screenshot.models import CaptureResult
from modules.screenshot.retention import SaveOutcome


URL = 'https://cdn.example.com/image?signature=a%2Fb%3D&token=FAKE_URL_KEY'
SECRET = 'FAKE_EXCEPTION_KEY_7319'
SAFE_WARNING = '图像为纯色或低方差，可能未正确渲染或未更新'


@pytest.fixture(autouse=True)
def isolate_upload_environment(monkeypatch, tmp_path):
    """隔离缓存目录，独立阻断真实网络和隐式账号文件读取。"""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))

    def forbidden(*args, **kwargs):
        """任何越过合成边界的访问都立即终止测试。"""
        pytest.fail('禁止真实网络或 netrc 访问')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', forbidden)
    monkeypatch.setattr(requests.sessions, 'get_netrc_auth', forbidden)
    monkeypatch.setattr(requests.utils, 'get_netrc_auth', forbidden)
    monkeypatch.setattr(netrc, 'netrc', forbidden)


def pipeline():
    """加载真实编排模块，让缺失功能成为明确的断言失败。"""
    assert find_spec('modules.screenshot.pipeline') is not None, (
        '尚未实现多目标截图编排')
    return import_module('modules.screenshot.pipeline')


def target(**overrides):
    """构造独立目标并覆盖当前场景所需字段。"""
    return {'provider': 'window', 'target': '窗口', **overrides}


def section(targets):
    """构造截图映射而非整个推送配置。"""
    return {'targets': targets, 'image_host': [{'provider': 'catbox'}]}


def captured(source='window', name='窗口', warnings=()):
    """使用实际结果类型与合成字节构造截图边界结果。"""
    return CaptureResult(b'png-' + name.encode(), source, name, 2, 3, warnings)


def uploaded(url=URL):
    """使用实际上传结果类型保留完整签名链接。"""
    return UploadResult(True, 'catbox', url, ('catbox',), ())


def boundaries(monkeypatch):
    """只替换截图及上传边界，保留真实分配与编排逻辑。"""
    module = pipeline()
    capture = Mock(side_effect=lambda source, name, **kwargs: captured(source, name))
    upload = Mock(return_value=uploaded())
    monkeypatch.setattr(module, 'capture', capture)
    monkeypatch.setattr(module, 'upload_with_fallback', upload)
    return module, capture, upload


def test_fixed_interfaces_and_frozen_batch():
    """固定字段、参数名及冻结属性与约定一致。"""
    module = pipeline()
    assert list(signature(module.prepare_screenshots).parameters) == [
        'section', 'enabled', 'reserved_names', 'runtime', 'event']
    assert list(signature(module.append_screenshot_notices).parameters) == [
        'rendered_content', 'original_template', 'batch']
    assert module.ScreenshotBatch.__annotations__ == {
        'values': dict[str, str], 'warnings': tuple[str, ...],
        'renamed_outputs': tuple[str, ...],
    }
    batch = module.ScreenshotBatch({'screenshot': ''}, (), ())
    with pytest.raises(FrozenInstanceError):
        batch.warnings = ('不可赋值',)


def test_two_targets_serial_success_and_aggregate(monkeypatch, caplog):
    """完成全部分配后依次截图上传，每项仅调用一次且不记业务链接。"""
    module, capture, upload = boundaries(monkeypatch)
    events = []

    def take(source, name, **kwargs):
        """记录截图顺序并返回真实结果类型。"""
        events.append(('capture', source, name))
        return captured(source, name)

    def send(image, filename, hosts, *, context):
        """记录同张图片的唯一完整上传调用。"""
        assert isinstance(context, UploadContext)
        events.append(('upload', image, filename))
        assert hosts == [{'provider': 'catbox'}]
        return uploaded()

    capture.side_effect = take
    upload.side_effect = send
    batch = module.prepare_screenshots(section([
        target(target='第一'), target(provider='adb', target='第二', out='device'),
    ]), True, ())
    assert events == [
        ('capture', 'window', '第一'),
        ('upload', b'png-' + '第一'.encode(), 'screenshot_1.jpg'),
        ('capture', 'adb', '第二'),
        ('upload', b'png-' + '第二'.encode(), 'device.jpg'),
    ]
    assert batch.values == {
        'screenshot_1': f'![screenshot_1]({URL})',
        'device': f'![device]({URL})',
        'screenshot': f'![screenshot_1]({URL})\n\n![device]({URL})',
    }
    assert batch.warnings == batch.renamed_outputs == ()
    assert not caplog.records


@pytest.mark.parametrize('failure', [
    CaptureError('window_not_found', SECRET),
    CaptureError('adb_timeout', SECRET),
    CaptureError(SECRET, SECRET),
    CaptureError([], SECRET),
    type(SECRET, (Exception,), {})(SECRET),
])
def test_capture_failure_keeps_slot_and_continues(monkeypatch, caplog, failure):
    """只使用可信类别，不上传失败项，不改变后一项编号或后端。"""
    module, capture, upload = boundaries(monkeypatch)
    capture.side_effect = [failure, captured('adb', '第二')]
    batch = module.prepare_screenshots(section([
        target(), target(provider='adb', target='第二'),
    ]), True, ())
    first = batch.values['screenshot_1']
    assert '截图失败' in first and '上传失败' not in first
    if isinstance(failure, CaptureError) and failure.code == 'window_not_found':
        assert '窗口' in first
    assert batch.values['screenshot_2'] == f'![screenshot_2]({URL})'
    assert batch.values['screenshot'] == (
        first + '\n\n' + batch.values['screenshot_2'])
    assert capture.call_count == 2
    context = upload.call_args.kwargs['context']
    assert isinstance(context, UploadContext)
    upload.assert_called_once_with(
        captured('adb', '第二').image_bytes, 'screenshot_2.jpg',
        [{'provider': 'catbox'}], context=context)
    assert SECRET not in str(batch.values) + caplog.text
    assert URL not in caplog.text
    assert not any(record.exc_info for record in caplog.records)
    assert all(record.levelname == 'WARNING' for record in caplog.records)


@pytest.mark.parametrize('ordinary', [False, True])
def test_upload_failure_is_distinct_and_safe(monkeypatch, caplog, ordinary):
    """全链失败和普通上传异常均使用固定摘要，且后续图片继续执行。"""
    module, capture, upload = boundaries(monkeypatch)
    failure = UploadResult(False, None, None, ('catbox', SECRET), (
        UploadFailure(SECRET, SECRET, SECRET),))
    upload.side_effect = [RuntimeError(SECRET) if ordinary else failure,
                          uploaded()]
    batch = module.prepare_screenshots(section([target(), target()]), True, ())
    assert '截图上传失败' in batch.values['screenshot_1']
    assert batch.values['screenshot_2'] == f'![screenshot_2]({URL})'
    assert capture.call_count == upload.call_count == 2
    assert SECRET not in str(batch.values) + caplog.text
    assert URL not in caplog.text
    assert not any(record.exc_info for record in caplog.records)


def test_single_image_all_hosts_fail(monkeypatch, caplog):
    """单图全失败仍提供实际输出和同一聚合，不复制上传器告警。"""
    module, _, upload = boundaries(monkeypatch)
    upload.return_value = UploadResult(False, None, None, ('catbox',), ())
    batch = module.prepare_screenshots(section([target()]), True, ())
    assert batch.values['screenshot'] == batch.values['screenshot_1']
    assert '截图上传失败' in batch.values['screenshot']
    assert batch.warnings == ()
    assert all('FAKE_' not in record.getMessage() for record in caplog.records)


def test_real_registry_restarts_each_image_and_preserves_inputs(
        monkeypatch, caplog):
    """真实上传链每图从首站开始，适配器修改副本不污染原配置和环境。"""
    module = pipeline()
    registry = import_module('modules.image_host.registry')
    calls = []
    monkeypatch.setattr(module, 'capture', Mock(side_effect=[
        captured(name='第一'), captured(name='第二')]))

    def first(image, filename, token, options):
        """每次首站失败并尝试修改仅属于本次的参数副本。"""
        calls.append(('first', filename))
        assert options['nested']['items'] == [1]
        options['nested']['items'].append(2)
        raise RuntimeError(SECRET)

    def second(image, filename, token, options):
        """每次次站返回完整签名链接而不产生任何网络请求。"""
        calls.append(('second', filename))
        return URL

    monkeypatch.setitem(registry.UPLOADERS, 'first', first)
    monkeypatch.setitem(registry.UPLOADERS, 'second', second)
    monkeypatch.setenv('PIPELINE_TEST_TOKEN', SECRET)
    config = section([target(out='same'), target(out='same')])
    config['image_host'] = [
        {'provider': 'first', 'token': '${PIPELINE_TEST_TOKEN}',
         'options': {'nested': {'items': [1]}}},
        {'provider': 'second', 'token': SECRET},
    ]
    reserved = ['screenshot_2']
    before = deepcopy((config, reserved, dict(os.environ)))
    batch = module.prepare_screenshots(config, True, reserved)
    assert calls == [('first', 'same.jpg'), ('second', 'same.jpg'),
                     ('first', 'screenshot_3.jpg'),
                     ('second', 'screenshot_3.jpg')]
    assert all(value.startswith('![') for value in batch.values.values())
    assert (config, reserved, dict(os.environ)) == before
    assert SECRET not in caplog.text and URL not in caplog.text


@pytest.mark.parametrize('bad', [None, 'unknown-secret', {},
                                     target(provider='unknown-secret')])
def test_invalid_target_retains_position(monkeypatch, bad):
    """非法目标保留对应错误槽位，其他目标使用原编号。"""
    module, capture, upload = boundaries(monkeypatch)
    batch = module.prepare_screenshots(section([bad, target()]), True, ())
    assert '配置' in batch.values['screenshot_1']
    assert batch.values['screenshot_2'] == f'![screenshot_2]({URL})'
    assert batch.values['screenshot'].split('\n\n') == [
        batch.values['screenshot_1'], batch.values['screenshot_2']]
    assert 'unknown-secret' not in str(batch)
    assert capture.call_count == upload.call_count == 1


@pytest.mark.parametrize(('outs', 'reserved', 'expected', 'renamed'), [
    (['screenshot_2', 'screenshot_1'], [],
     ['screenshot_2', 'screenshot_1'], ()),
    (['screenshot_2', None, None], [],
     ['screenshot_2', 'screenshot_4', 'screenshot_3'], ('screenshot_4',)),
    (['screenshot_8', 'screenshot_8', 'screenshot_9'], [],
     ['screenshot_8', 'screenshot_10', 'screenshot_9'], ('screenshot_10',)),
    (['screenshot', 'custom', None], ['custom', 'screenshot_1'],
     ['screenshot_2', 'screenshot_4', 'screenshot_3'],
     ('screenshot_2', 'screenshot_4')),
])
def test_actual_allocation_and_conflict_notices(
        monkeypatch, caplog, outs, reserved, expected, renamed):
    """复用实际分配规则，显式互换合法且未来名称预留不被抢占。"""
    module, _, upload = boundaries(monkeypatch)
    config = section([target(**({'out': out} if out is not None else {}))
                      for out in outs])
    before = deepcopy((config, reserved))
    batch = module.prepare_screenshots(config, True, reserved)
    assert list(batch.values) == expected + ['screenshot']
    assert batch.renamed_outputs == renamed
    assert [call.args[1] for call in upload.call_args_list] == [
        name + '.jpg' for name in expected]
    assert (config, reserved) == before
    assert len(batch.warnings) == len(renamed)
    for warning in batch.warnings:
        assert warning not in batch.values['screenshot']
        assert sum(warning in record.getMessage()
                   for record in caplog.records) == 1
    for name in renamed:
        assert any(name in warning for warning in batch.warnings)


def test_all_failed_targets_are_aggregated(monkeypatch):
    """配置、截图和上传失败都按目标顺序进入聚合。"""
    module, capture, upload = boundaries(monkeypatch)
    capture.side_effect = [CaptureError('adb_offline', SECRET), captured()]
    upload.return_value = UploadResult(False, None, None, (), ())
    batch = module.prepare_screenshots(section([
        None, target(provider='adb'), target(),
    ]), True, ())
    assert batch.values['screenshot'].split('\n\n') == [
        batch.values[f'screenshot_{index}'] for index in range(1, 4)]
    assert all('失败' in value for value in batch.values.values())


@pytest.mark.parametrize('config', [None, False, 4, SECRET, [], {},
                                    {'targets': None}, {'targets': []},
                                    {'targets': {}}, {'targets': SECRET}])
@pytest.mark.parametrize('enabled', [True, False])
def test_empty_or_bad_containers_are_safe(monkeypatch, config, enabled):
    """空目标和错误容器均不触发边界，禁用时仅返回空聚合。"""
    module, capture, upload = boundaries(monkeypatch)
    batch = module.prepare_screenshots(config, enabled, ())
    assert set(batch.values) == {'screenshot'}
    if enabled:
        assert '配置' in batch.values['screenshot']
    else:
        assert batch.values['screenshot'] == ''
        assert batch.warnings == batch.renamed_outputs == ()
    assert SECRET not in str(batch)
    capture.assert_not_called()
    upload.assert_not_called()


def test_disabled_allocates_without_reading_hosts_or_environment(
        monkeypatch, caplog):
    """禁用时只在内存分配输出名，不读取图床节点、凭证或环境。"""
    module, capture, upload = boundaries(monkeypatch)

    class GuardedSection(UserDict):
        """拒绝任何图床配置访问。"""

        def get(self, key, *args):
            """保证禁用路径不会触及上传设置。"""
            assert key != 'image_host'
            return super().get(key, *args)

    config = GuardedSection(section([
        target(out='screenshot'), None, target(out='screenshot_2'),
    ]))
    reserved = {'screenshot_1'}
    before = deepcopy((config, reserved, dict(os.environ)))
    resolver = Mock(side_effect=AssertionError('不得解析凭证'))
    monkeypatch.setattr(import_module('modules.image_host.registry'),
                        'resolve_token', resolver)
    batch = module.prepare_screenshots(config, False, reserved)
    assert batch.values == {
        'screenshot_3': '', 'screenshot_2': '', 'screenshot_4': '',
        'screenshot': '',
    }
    assert batch.warnings == batch.renamed_outputs == ()
    assert (config, reserved, dict(os.environ)) == before
    capture.assert_not_called()
    upload.assert_not_called()
    resolver.assert_not_called()
    assert not caplog.records


def test_capture_warnings_ordered_unique_and_not_in_aggregate(
        monkeypatch, caplog):
    """成功截图告警与分配告警保序去重，在日志和附加正文各出现一次。"""
    module, capture, _ = boundaries(monkeypatch)
    other = '未能还原原线程 DPI 上下文'
    capture.side_effect = [captured(warnings=(SAFE_WARNING, SAFE_WARNING)),
                           captured(warnings=(SAFE_WARNING, other))]
    batch = module.prepare_screenshots(section([
        target(out='same'), target(out='same'),
    ]), True, ())
    assert len(batch.warnings) == 3
    assert '目标 2' in batch.warnings[0]
    assert 'same' in batch.warnings[0] and 'screenshot_2' in batch.warnings[0]
    assert batch.warnings[1:] == (SAFE_WARNING, other)
    record_count = len(caplog.records)
    template = '{screenshot}'
    rendered = template.format(**batch.values)
    result = module.append_screenshot_notices(rendered, template, batch)
    assert result.startswith(rendered + '\n\n截图配置提示')
    assert result.count('截图配置提示') == 1
    for warning in batch.warnings:
        assert warning not in batch.values['screenshot']
        assert result.count(warning) == 1
        assert sum(warning in record.getMessage()
                   for record in caplog.records) == 1
    assert len(caplog.records) == record_count


@pytest.mark.parametrize(('template', 'supplement'), [
    ('正文', True),
    ('{screenshot_2}', True),
    ('{{screenshot}}', True),
    ('{{screenshot_3}}', True),
    ('{screenshot}', False),
    ('{screenshot_3}', False),
    ('{screenshot_3!s} {screenshot_3}', False),
    ('{body:{screenshot_3}}', False),
    ('{body:{screenshot}}', False),
    ('{body:{{screenshot}}}', True),
    ('{body:{width}.{screenshot_3}}', False),
])
@pytest.mark.parametrize('failure', [False, True])
def test_renamed_supplement_uses_real_fields_only(
        monkeypatch, template, supplement, failure):
    """解析真实及嵌套字段，转义文字不算引用，保持既有输出归属。"""
    module, capture, _ = boundaries(monkeypatch)
    if failure:
        capture.side_effect = [captured(), CaptureError('adb_offline', SECRET)]
    batch = module.prepare_screenshots(section([
        target(out='screenshot_2'), target(),
    ]), True, ())
    assert batch.renamed_outputs == ('screenshot_3',)
    assert batch.values['screenshot_2'] == f'![screenshot_2]({URL})'
    rendered = '已渲染正文 {不应再次格式化}'
    result = module.append_screenshot_notices(rendered, template, batch)
    value = batch.values['screenshot_3']
    assert result.startswith(rendered + '\n\n截图配置提示')
    assert (value in result) is supplement
    if supplement:
        assert result.endswith(value)
        assert result.count(value) == 1
    assert batch.values['screenshot_2'] not in result
    assert capture.call_count == 2


def test_repeated_references_and_channels_do_not_repeat_work(monkeypatch):
    """相同变量多次引用或跨通道复用结果不增加截图上传次数。"""
    module, capture, upload = boundaries(monkeypatch)
    batch = module.prepare_screenshots(section([target(out='custom')]), True, ())
    template = '{custom}\n{custom}\n{{screenshot}}'
    rendered = template.format(**batch.values)
    for _ in range(3):
        assert module.append_screenshot_notices(
            rendered, template, batch) == rendered
    assert capture.call_count == upload.call_count == 1
    assert module.append_screenshot_notices('普通正文', '普通正文', batch) == '普通正文'


@pytest.mark.parametrize('template', ['{', '}', '{screenshot_3',
                                       '{screenshot_3} {', '{x:{broken}'])
def test_malformed_template_is_append_only_and_conservative(
        monkeypatch, template):
    """畸形模板不重新渲染或上传，只追加去重告警而不猜测补附。"""
    module, capture, upload = boundaries(monkeypatch)
    batch = module.prepare_screenshots(section([
        target(out='same'), target(out='same'),
    ]), True, ())
    result = module.append_screenshot_notices('已有正文 {x}', template, batch)
    assert result.startswith('已有正文 {x}\n\n截图配置提示')
    assert '![' not in result
    assert capture.call_count == upload.call_count == 2


def test_append_deduplicates_batch_warnings_without_logging(caplog):
    """公开结果中的重复告警只在提示段出现一次，不额外写日志。"""
    module = pipeline()
    batch = module.ScreenshotBatch({'screenshot': ''}, ('提示', '提示'), ())
    assert module.append_screenshot_notices('', '', batch) == '截图配置提示\n提示'
    assert not caplog.records


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('stage', ['capture', 'upload'])
def test_control_signals_propagate_identically(monkeypatch, signal_type, stage):
    """控制信号原对象传播，编排层不吞掉信号或继续下一目标。"""
    module, capture, upload = boundaries(monkeypatch)
    signal = signal_type(SECRET)
    boundary = capture if stage == 'capture' else upload
    boundary.side_effect = signal
    with pytest.raises(signal_type) as caught:
        module.prepare_screenshots(section([target(), target()]), True, ())
    assert caught.value is signal
    assert capture.call_count == 1
    assert upload.call_count == (stage == 'upload')


@pytest.mark.parametrize(('raw', 'safe'), [
    pytest.param(
        'https://[2001:db8::1]/image.png?sig=a%2Fb%3D&token=FAKE_ONLY',
        'https://[2001:db8::1]/image.png?sig=a%2Fb%3D&token=FAKE_ONLY',
        id='ipv6-signed'),
    pytest.param(
        'https://[2001:db8::1]:8443/image.png?sig=a%2Fb%3D',
        'https://[2001:db8::1]:8443/image.png?sig=a%2Fb%3D',
        id='ipv6-port'),
    pytest.param(
        'https://[2001:db8::1]:8443/a[b](c).png'
        '?sig=a%2Fb%3D&token=FAKE_ONLY&part=[x](y)#part[z](w)',
        'https://[2001:db8::1]:8443/a%5Bb%5D%28c%29.png'
        '?sig=a%2Fb%3D&token=FAKE_ONLY&part=%5Bx%5D%28y%29'
        '#part%5Bz%5D%28w%29',
        id='ipv6-markdown-delimiters'),
    pytest.param(
        'HTTPS://[2001:DB8::A]:08443/Image%2f.png'
        '?z=a%2Fb%3D&a=%2f%3d&z=FAKE_ONLY#',
        'HTTPS://[2001:DB8::A]:08443/Image%2f.png'
        '?z=a%2Fb%3D&a=%2f%3d&z=FAKE_ONLY#',
        id='ipv6-original-spelling'),
    pytest.param(
        'https://[2001:db8::1]?', 'https://[2001:db8::1]?',
        id='ipv6-empty-query'),
    pytest.param(
        'https://[2001:db8::1]#', 'https://[2001:db8::1]#',
        id='ipv6-empty-fragment'),
    pytest.param(
        'https://[2001:db8::1]?#', 'https://[2001:db8::1]?#',
        id='ipv6-empty-query-and-fragment'),
    pytest.param(
        'https://[2001:db8::1]?part=[x](y)&sig=a%2Fb%3D',
        'https://[2001:db8::1]?part=%5Bx%5D%28y%29&sig=a%2Fb%3D',
        id='ipv6-query-without-path'),
    pytest.param(
        'https://CDN.example.com/a[b](c).png'
        '?sig=a%2Fb%3D&part=[x](y)#part[z](w)',
        'https://CDN.example.com/a%5Bb%5D%28c%29.png'
        '?sig=a%2Fb%3D&part=%5Bx%5D%28y%29#part%5Bz%5D%28w%29',
        id='domain-markdown-delimiters'),
])
def test_real_registry_markdown_preserves_url_structure(
        monkeypatch, caplog, raw, safe):
    """真实上传校验后保留主机及签名，独立输出与聚合使用同一链接。"""
    module = pipeline()
    registry = import_module('modules.image_host.registry')
    calls = []
    capture = Mock(side_effect=lambda source, name, **kwargs: captured(source, name))
    monkeypatch.setattr(module, 'capture', capture)

    def local_upload(image, filename, token, options):
        """仅返回合成链接，经真实 registry 校验，不发起网络请求。"""
        assert image == captured().image_bytes
        assert token == '' and options == {}
        calls.append(filename)
        return raw

    monkeypatch.setitem(registry.UPLOADERS, 'local', local_upload)
    config = section([target(out='custom'), target()])
    config['image_host'] = [{'provider': 'local'}]
    batch = module.prepare_screenshots(config, True, ())
    custom = f'![custom]({safe})'
    automatic = f'![screenshot_2]({safe})'
    assert batch.values == {
        'custom': custom,
        'screenshot_2': automatic,
        'screenshot': custom + '\n\n' + automatic,
    }
    assert calls == ['custom.jpg', 'screenshot_2.jpg']
    assert capture.call_count == 2
    assert batch.warnings == batch.renamed_outputs == ()
    assert not caplog.records


def test_markdown_url_escapes_only_structure_and_preserves_signature(
        monkeypatch, caplog):
    """只编码链接结构字符，既有百分号转义、签名查询和完整业务链接保留。"""
    module, _, upload = boundaries(monkeypatch)
    raw = 'https://cdn.example.com/a [b](c).png?sig=a%2Fb%3D&token=FAKE_URL_KEY'
    safe = ('https://cdn.example.com/a%20%5Bb%5D%28c%29.png'
            '?sig=a%2Fb%3D&token=FAKE_URL_KEY')
    upload.return_value = uploaded(raw)
    batch = module.prepare_screenshots(section([target(out='custom')]), True, ())
    assert batch.values == {'custom': f'![custom]({safe})',
                            'screenshot': f'![custom]({safe})'}
    assert 'FAKE_URL_KEY' not in caplog.text
    assert not caplog.records


@pytest.mark.parametrize('code', [
    'transport_failed', 'http_failed', 'business_rejected', 'invalid_response',
    'storage_lookup_failed', 'storage_unavailable', 'config_error',
    'not_configured', 'invalid_options',
])
def test_upload_failure_remaps_untrusted_fields(monkeypatch, caplog, code):
    """失败仅按核心认可代码重建摘要，不信任标签、消息或诊断。"""
    module, _, upload = boundaries(monkeypatch)
    upload.return_value = UploadResult(False, None, None, (SECRET,), (
        UploadFailure(SECRET, code, SECRET, diagnostic=SECRET),
        UploadFailure(SECRET, code, SECRET, diagnostic=SECRET),
    ))
    batch = module.prepare_screenshots(section([target()]), True, ())
    expected = '截图上传失败：' + ImageHostError(code, '').message
    assert batch.values['screenshot_1'] == expected
    assert batch.values['screenshot'] == expected
    assert SECRET not in repr(batch) + caplog.text
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage().count(expected) == 1


@pytest.mark.parametrize('code', [SECRET, None, [], 'upload_failed'])
def test_unknown_upload_failure_keeps_fixed_fallback(monkeypatch, caplog, code):
    """未知、非法或无具体类别仍返回固定上传失败兜底。"""
    module, _, upload = boundaries(monkeypatch)
    upload.return_value = UploadResult(False, None, None, (), (
        UploadFailure(SECRET, code, SECRET, diagnostic=SECRET),))
    batch = module.prepare_screenshots(section([target()]), True, ())
    assert batch.values['screenshot'] == '截图上传失败'
    assert SECRET not in repr(batch) + caplog.text


def test_upload_warnings_survive_success_and_failure(monkeypatch, caplog):
    """两种上传结局的告警与分配、截图告警一起保序去重。"""
    module, capture, upload = boundaries(monkeypatch)
    capture.side_effect = [captured(warnings=(SAFE_WARNING,)), captured()]
    first = '手填储存驱动不可用，已自动替代'
    second = '保存期限已按图床上限缩短'
    upload.side_effect = [
        UploadResult(True, 'catbox', URL, (), (
            UploadFailure(SECRET, 'http_failed', SECRET, diagnostic=SECRET),
        ), (first, SAFE_WARNING)),
        UploadResult(False, None, None, (), (), (first, second)),
    ]
    batch = module.prepare_screenshots(section([
        target(out='same'), target(out='same')]), True, ())
    assert len(batch.warnings) == 4
    assert batch.warnings[1:] == (SAFE_WARNING, first, second)
    assert batch.values['same'] == f'![same]({URL})'
    assert batch.values['screenshot_2'] == '截图上传失败'
    body = module.append_screenshot_notices(
        batch.values['screenshot'], '{screenshot}', batch)
    assert body.count('截图配置提示') == 1
    assert len(caplog.records) == 1
    for warning in batch.warnings:
        assert body.count(warning) == caplog.text.count(warning) == 1
    assert SECRET not in body + caplog.text
    assert URL not in caplog.text


@pytest.mark.parametrize('stage', ['capture', 'upload'])
@pytest.mark.parametrize('outcome', ['success', 'ordinary', 'interrupt', 'exit'])
def test_batch_context_owns_lifecycle(monkeypatch, stage, outcome):
    """批次在所有退出路径释放唯一自有事件并保留控制信号原对象。"""
    module, capture, upload = boundaries(monkeypatch)
    instances = []
    closed = []
    initialize = UploadContext.__init__
    close = UploadContext.close

    def observed_init(self, **kwargs):
        """观察真实事件初始化，不改变默认诊断语义。"""
        assert kwargs.get('diagnostics') is False
        initialize(self, **kwargs)
        instances.append(self)

    def observed_close(self):
        """记录真实关闭次数并释放全部事件状态。"""
        closed.append(self)
        close(self)

    monkeypatch.setattr(UploadContext, '__init__', observed_init)
    monkeypatch.setattr(UploadContext, 'close', observed_close)
    signal = (KeyboardInterrupt(SECRET) if outcome == 'interrupt'
              else SystemExit(SECRET))
    error = RuntimeError(SECRET) if outcome == 'ordinary' else signal
    if outcome != 'success':
        boundary = capture if stage == 'capture' else upload
        boundary.side_effect = ([captured(), error] if stage == 'capture'
                                else [uploaded(), error])
    config = section([target(), target()])
    if outcome in ('interrupt', 'exit'):
        with pytest.raises(type(signal)) as caught:
            module.prepare_screenshots(config, True, ())
        assert caught.value is signal
    else:
        batch = module.prepare_screenshots(config, True, ())
        assert batch.values['screenshot_1'].startswith('![')
    assert len(instances) == 1
    assert closed == instances
    assert instances[0]._closed
    assert instances[0]._cache is instances[0]._clock is None
    assert all(call.kwargs['context'] is instances[0]
               for call in upload.call_args_list)


@pytest.mark.parametrize('enabled, targets', [
    (False, [target()]), (True, []), (True, [None]),
])
def test_inactive_batch_never_constructs_context(monkeypatch, enabled, targets):
    """禁用、空目标及全无效目标不创建事件或读取缓存凭证。"""
    module, capture, upload = boundaries(monkeypatch)

    def forbidden(*args, **kwargs):
        """无有效上传工作时不允许初始化上传事件。"""
        pytest.fail('无有效目标却创建了上传上下文')

    monkeypatch.setattr(UploadContext, '__init__', forbidden)
    module.prepare_screenshots(section(targets), enabled, ())
    capture.assert_not_called()
    upload.assert_not_called()


def test_capture_uses_jpeg_automatic(monkeypatch):
    """自动截图固定请求 JPEG 且标记 purpose=automatic。"""
    module, capture, _ = boundaries(monkeypatch)
    module.prepare_screenshots(section([target()]), True, ())
    capture.assert_called_once_with(
        'window', '窗口', image_format='jpeg', purpose='automatic')


def test_capture_save_upload_share_bytes_in_order(monkeypatch):
    """同一目标的截图、保存、上传使用同一 image_bytes 且顺序固定。"""
    module, capture, upload = boundaries(monkeypatch)
    events = []

    def take(source, name, **kwargs):
        """记录截图顺序。"""
        events.append(('capture', source, name))
        return captured(source, name)

    capture.side_effect = take
    monkeypatch.setattr(
        module, 'save_automatic',
        lambda result, runtime, event, policy: (
            events.append(('save', result.image_bytes))
            or SaveOutcome('saved.jpg', 'saved.jpg', ())))
    upload.side_effect = lambda image, filename, hosts, *, context: (
        events.append(('upload', image, filename)) or uploaded())
    batch = module.prepare_screenshots(section([target()]), True, ())
    payload = captured().image_bytes
    assert events == [
        ('capture', 'window', '窗口'),
        ('save', payload),
        ('upload', payload, 'screenshot_1.jpg'),
    ]
    assert batch.values['screenshot_1'].startswith('![')


def test_disabled_policy_does_not_touch_disk(monkeypatch):
    """关闭保留策略时保存层不写盘。"""
    module, _, _ = boundaries(monkeypatch)
    winfs = import_module('modules.screenshot.retention').winfs
    monkeypatch.setattr(
        winfs, 'write_exclusive',
        lambda *args, **kwargs: pytest.fail('关闭策略不得写盘'))
    module.prepare_screenshots(section([target()]), True, ())


def test_enabled_policy_passes_runtime_event_and_policy(monkeypatch):
    """启用保留时向保存层传递 runtime、真实事件键与解析后的策略。"""
    module, _, _ = boundaries(monkeypatch)
    seen = {}

    def save(result, runtime, event, policy):
        """记录保存层入参并返回带告警的结果。"""
        seen['runtime'] = runtime
        seen['event'] = event
        seen['enabled'] = policy.enabled
        return SaveOutcome('a.jpg', 'a.jpg', ('保留提示',))

    monkeypatch.setattr(module, 'save_automatic', save)
    config = section([target()])
    config['retention'] = {'enabled': True, 'max_days': 7}
    runtime = {'program_dir': 'P', 'config_stem': 'c'}
    batch = module.prepare_screenshots(
        config, True, (), runtime=runtime, event='on_end')
    assert seen == {'runtime': runtime, 'event': 'on_end', 'enabled': True}
    assert '保留提示' in batch.warnings


def test_runtime_fallback_uses_program_dir_and_config_stem(monkeypatch):
    """缺省运行上下文时按程序目录与 config.yaml 生成。"""
    module, _, _ = boundaries(monkeypatch)
    seen = {}
    monkeypatch.setattr(
        module, 'save_automatic',
        lambda result, runtime, event, policy: (
            seen.update(runtime=runtime, event=event)
            or SaveOutcome(None, '', ())))
    module.prepare_screenshots(section([target()]), True, ())
    assert seen['runtime'] == {
        'program_dir': module.get_program_directory(), 'config_stem': 'config'}
    assert seen['event'] == 'event'


def test_save_exception_does_not_block_upload(monkeypatch):
    """保存抛异常只追加固定告警，不阻断上传。"""
    module, _, upload = boundaries(monkeypatch)
    monkeypatch.setattr(
        module, 'save_automatic', Mock(side_effect=OSError('boom')))
    batch = module.prepare_screenshots(section([target()]), True, ())
    assert batch.values['screenshot_1'].startswith('![')
    upload.assert_called_once()
    assert '截图保存失败：无法写入输出路径' in batch.warnings


def test_automatic_write_cleanup_warning_does_not_block_upload(
        monkeypatch, tmp_path, caplog):
    """真实保存层传递固定清理告警，仍上传且日志与正文不泄露异常。"""
    module, capture, upload = boundaries(monkeypatch)
    winfs = import_module('modules.screenshot.retention').winfs
    error = OSError(SECRET)
    error.add_note(SECRET)
    error.add_note('截图半成品清理失败')

    def fail_write(path, data):
        """仅模拟临时目录中的写入失败及清理附注。"""
        assert path.startswith(str(tmp_path) + os.sep)
        assert data == captured().image_bytes
        raise error

    monkeypatch.setattr(winfs, 'list_names', lambda path: ())
    monkeypatch.setattr(winfs, 'write_exclusive', fail_write)
    config = section([target(), target()])
    config['retention'] = {'enabled': True}
    batch = module.prepare_screenshots(
        config, True, (),
        runtime={'program_dir': str(tmp_path), 'config_stem': 'config'})
    assert capture.call_count == upload.call_count == 2
    assert all(value.startswith('![') for value in batch.values.values())
    assert [call.args[:2] for call in upload.call_args_list] == [
        (captured().image_bytes, 'screenshot_1.jpg'),
        (captured().image_bytes, 'screenshot_2.jpg')]
    assert batch.warnings == (
        '截图保存失败：无法写入输出路径', '截图半成品清理失败')
    body = module.append_screenshot_notices(
        batch.values['screenshot'], '{screenshot}', batch)
    for warning in batch.warnings:
        assert body.count(warning) == caplog.text.count(warning) == 1
    assert SECRET not in repr(batch) + body + caplog.text
    assert not any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit])
def test_automatic_write_control_signal_stops_pipeline(
        monkeypatch, tmp_path, signal_type):
    """真实保存层的控制信号原样传播，不上传或处理后续目标。"""
    module, capture, upload = boundaries(monkeypatch)
    winfs = import_module('modules.screenshot.retention').winfs
    signal = signal_type(SECRET)
    signal.add_note('截图半成品清理失败')

    def interrupt_write(path, data):
        """模拟带清理附注的底层写入中断。"""
        raise signal

    monkeypatch.setattr(winfs, 'list_names', lambda path: ())
    monkeypatch.setattr(winfs, 'write_exclusive', interrupt_write)
    config = section([target(), target()])
    config['retention'] = {'enabled': True}
    with pytest.raises(signal_type) as caught:
        module.prepare_screenshots(
            config, True, (),
            runtime={'program_dir': str(tmp_path), 'config_stem': 'config'})
    assert caught.value is signal
    assert capture.call_count == 1
    upload.assert_not_called()


def test_invalid_retention_policy_warning_surfaces_once(
        monkeypatch, tmp_path, caplog):
    """真实截图仅保存到临时目录，非法保留天数告警只出现一次。"""
    module, _, _ = boundaries(monkeypatch)
    monkeypatch.setattr(
        module, 'get_program_directory',
        Mock(side_effect=AssertionError('不得回退到程序目录')))
    config = section([target()])
    config['retention'] = {'enabled': True, 'max_days': 'bad'}
    runtime = {'program_dir': str(tmp_path), 'config_stem': 'config'}
    batch = module.prepare_screenshots(config, True, (), runtime=runtime)
    files = [path for path in tmp_path.rglob('*') if path.is_file()]
    assert len(files) == 1
    saved = files[0]
    assert saved.relative_to(tmp_path).parts[0] == 'screenshot'
    assert saved.parent.name == 'config'
    assert saved.name == 'event_01.jpg'
    assert saved.read_bytes() == captured().image_bytes
    assert sum('保留天数无效' in warning for warning in batch.warnings) == 1
    assert caplog.text.count('保留天数无效') == 1
