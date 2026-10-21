#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""验证截图失败详情身份去重与唯一受控聚合构建，不访问真实设备或图床。"""

from copy import deepcopy
from importlib import import_module
from importlib.util import find_spec
import netrc
import socket
from unittest.mock import Mock

import pytest
import requests

from modules.image_host.core import ImageHostError
from modules.image_host.core import UploadFailure
from modules.image_host.core import UploadResult
from modules.screenshot.models import CaptureError
from modules.screenshot.models import CaptureResult


URL = 'https://cdn.example.com/image?signature=a%2Fb%3D&token=FAKE_URL_KEY'
SECRET = 'FAKE_EXCEPTION_KEY_7319'
GENERIC_UPLOAD = '截图上传失败：通道 未知图床 图床上传失败。'


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
    capture = Mock(
        side_effect=lambda source, name, **kwargs: captured(source, name))
    upload = Mock(return_value=uploaded())
    monkeypatch.setattr(module, 'capture', capture)
    monkeypatch.setattr(module, 'upload_with_fallback', upload)
    return module, capture, upload


def test_failure_details_preserve_legacy_values(monkeypatch):
    """编号槽位保留旧失败值，默认聚合改用去重受控句。"""
    module, _, _ = boundaries(monkeypatch)
    batch = module.prepare_screenshots(
        section([None, target(out='last')]), True, ())
    assert batch.values['screenshot_1'] == '截图配置失败：截图目标必须为映射'
    assert batch.values['last'] == f'![last]({URL})'
    assert batch.failure_details == (
        module.ScreenshotFailureDetail(
            'screenshot_1', 'config', '', 'target_invalid'),)
    assert batch.values['screenshot'] == (
        f'截图配置失败：截图目标配置无效\n\n![last]({URL})')


def test_reason_identity_order_and_success_images():
    """按插入顺序构建身份去重片段，成功图片与同源同因保留首片。"""
    module = pipeline()
    image_markdown = f'![image]({URL})'
    legacy = '截图上传失败：图床网络传输失败；储存驱动不可用'
    batch = module.ScreenshotBatch(
        {'one': legacy, 'image': image_markdown,
         'three': '截图上传失败：图床上传失败',
         'four': '截图上传失败：图床网络传输失败',
         'screenshot': '旧聚合'},
        (), (),
        (
            module.ScreenshotFailureDetail(
                'one', 'upload', 'beeimg_cn', 'transport_failed'),
            module.ScreenshotFailureDetail(
                'one', 'upload', 'beeimg_cn', 'storage_unavailable'),
            module.ScreenshotFailureDetail(
                'three', 'upload', 'beeimg_cn', 'transport_failed'),
            module.ScreenshotFailureDetail(
                'three', 'upload', 'beeimg_cn', 'upload_failed'),
            module.ScreenshotFailureDetail(
                'four', 'upload', 'catbox', 'transport_failed'),
        ),
    )
    assert module.build_screenshot_fragments(batch) == (
        ('one', 'text', '截图上传失败：通道 BeeIMG.CN 图床网络传输失败。'),
        ('one', 'text', '截图上传失败：通道 BeeIMG.CN storage_id 不可用。'),
        ('image', 'image', image_markdown),
        ('three', 'text', '截图上传失败：通道 BeeIMG.CN 图床上传失败。'),
        ('four', 'text', '截图上传失败：通道 Catbox 图床网络传输失败。'),
    )
    assert module.build_screenshot_fragments(batch, output_names=()) == ()
    assert batch.values['one'] == legacy
    assert module.ScreenshotBatch({}, (), ()).failure_details == ()
    duplicated = module.ScreenshotBatch(
        {'first': f'![first]({URL})', 'second': f'![second]({URL})'}, (), ())
    assert module.build_screenshot_fragments(duplicated) == (
        ('first', 'image', f'![first]({URL})'),
        ('second', 'image', f'![second]({URL})'),
    )


def test_namespace_and_generic_failure(monkeypatch):
    """按类别、安全 provider 与规范 code 记录，未知不猜最后通道。"""
    module, capture, upload = boundaries(monkeypatch)
    detail = module.ScreenshotFailureDetail
    reset = lambda source, name, **kwargs: captured(source, name)

    config = module.prepare_screenshots(section([None]), True, ())
    assert config.failure_details == (
        detail('screenshot_1', 'config', '', 'target_invalid'),)

    capture.side_effect = CaptureError('window_not_found', SECRET)
    known = module.prepare_screenshots(section([target()]), True, ())
    assert known.failure_details == (
        detail('screenshot_1', 'capture', '', 'window_not_found'),)

    capture.side_effect = RuntimeError(SECRET)
    unknown = module.prepare_screenshots(section([target()]), True, ())
    assert unknown.failure_details == (
        detail('screenshot_1', 'capture', '', 'capture_failed'),)

    capture.side_effect = reset
    upload.return_value = UploadResult(False, None, None, (), (
        UploadFailure('beeimg_cn', 'transport_failed', SECRET),))
    host = module.prepare_screenshots(section([target()]), True, ())
    assert host.failure_details == (
        detail('screenshot_1', 'upload', 'beeimg_cn', 'transport_failed'),)

    upload.return_value = UploadResult(False, None, None, (), (
        UploadFailure('配置', 'config_error', SECRET),))
    shared = module.prepare_screenshots(section([target()]), True, ())
    assert shared.failure_details == (
        detail('screenshot_1', 'config', '配置', 'config_error'),)
    assert shared.values['screenshot'] == '截图配置失败：图床配置无效'

    upload.side_effect = RuntimeError(SECRET)
    ordinary = module.prepare_screenshots(section([target()]), True, ())
    assert ordinary.failure_details == (
        detail('screenshot_1', 'upload', 'unknown', 'upload_failed'),)

    empty = module.prepare_screenshots(section([]), True, ())
    assert empty.failure_details == (
        detail('screenshot', 'config', '', 'no_targets'),)
    assert empty.values['screenshot'] == '截图配置失败：未配置有效截图目标'


def test_aggregate_exclusion_and_legacy_fallback():
    """排除聚合自身、精确空值过滤及仅在无片段时全量回退。"""
    module = pipeline()
    sentence = '截图上传失败：通道 BeeIMG.CN 图床网络传输失败。'
    detail = module.ScreenshotFailureDetail

    excluded = module.ScreenshotBatch(
        {'screenshot_1': f'![screenshot_1]({URL})', 'screenshot': '旧聚合'}, (), (),
        (detail('screenshot_1', 'upload', 'beeimg_cn', 'transport_failed'),
         detail('screenshot', 'upload', 'beeimg_cn', 'upload_failed')))
    assert module.build_screenshot_fragments(excluded) == (
        ('screenshot_1', 'text', sentence),)
    assert module.build_screenshot_fragments(excluded, output_names=()) == ()
    assert module.build_screenshot_fragments(
        excluded, output_names=('missing',)) == ()

    blanks = module.ScreenshotBatch({'one': ' \t', 'two': ''}, (), ())
    assert module.build_screenshot_fragments(blanks) == (
        ('one', 'text', ' \t'),)
    assert module.build_screenshot_fragments(
        module.ScreenshotBatch({'two': ''}, (), ())) == ()

    mixed = module.ScreenshotBatch(
        {'one': sentence, 'two': sentence}, (), (),
        (detail('one', 'upload', 'beeimg_cn', 'transport_failed'),))
    assert module.build_screenshot_fragments(mixed) == (
        ('one', 'text', sentence), ('two', 'text', sentence))

    deduped = module.ScreenshotBatch(
        {'one': 'a', 'two': 'b', 'three': 'c'}, (), (),
        (detail('one', 'upload', 'beeimg_cn', 'transport_failed'),
         detail('two', 'upload', 'beeimg_cn', 'transport_failed'),
         detail('three', 'upload', 'beeimg_cn', 'storage_unavailable')))
    assert module.build_screenshot_fragments(deduped) == (
        ('one', 'text', sentence),
        ('three', 'text', '截图上传失败：通道 BeeIMG.CN storage_id 不可用。'))

    legacy = module.ScreenshotBatch({'screenshot': '旧聚合'}, (), ())
    assert module.build_screenshot_fragments(legacy) == (
        ('screenshot', 'text', '旧聚合'),)
    aggregate_detail = module.ScreenshotBatch(
        {'screenshot': ''}, (), (),
        (detail('screenshot', 'config', '', 'no_targets'),))
    assert module.build_screenshot_fragments(aggregate_detail) == (
        ('screenshot', 'text', '截图配置失败：未配置有效截图目标'),)


def test_recovery_success_has_no_details(monkeypatch):
    """上传最终成功忽略早先恢复失败，成功图片不携带失败详情。"""
    module, _, upload = boundaries(monkeypatch)
    upload.return_value = UploadResult(True, 'catbox', URL, ('catbox',), (
        UploadFailure('catbox', 'transport_failed', SECRET),))
    batch = module.prepare_screenshots(section([target()]), True, ())
    assert batch.failure_details == ()
    assert batch.values['screenshot_1'] == f'![screenshot_1]({URL})'
    assert batch.values['screenshot'] == f'![screenshot_1]({URL})'


def test_default_aggregation_uses_builder_without_mutation(monkeypatch):
    """默认聚合等于构建器片段拼接，且只读调用不修改批次。"""
    module, capture, _ = boundaries(monkeypatch)
    capture.side_effect = [CaptureError('adb_offline', SECRET), captured()]
    config = section([target(provider='adb', target='设备'), target(out='last')])
    before_config = deepcopy(config)
    batch = module.prepare_screenshots(config, True, ())
    fragments = module.build_screenshot_fragments(batch)
    assert batch.values['screenshot'] == '\n\n'.join(
        text for _, _, text in fragments)
    assert '；' not in batch.values['screenshot']
    assert config == before_config
    snapshot = deepcopy(batch)
    assert module.build_screenshot_fragments(batch) == fragments
    assert batch == snapshot


def test_final_failure_without_upload_failures_gets_generic_detail(monkeypatch):
    """最终失败但无有效 UploadFailure 时仍生成通用上传详情并保留编号旧值。"""
    module, _, upload = boundaries(monkeypatch)
    detail = module.ScreenshotFailureDetail

    upload.return_value = UploadResult(False, None, None, (), ())
    empty = module.prepare_screenshots(section([target()]), True, ())
    assert empty.failure_details == (
        detail('screenshot_1', 'upload', 'unknown', 'upload_failed'),)
    assert empty.values['screenshot_1'] == '截图上传失败'
    assert empty.values['screenshot'] == GENERIC_UPLOAD

    upload.return_value = UploadResult(False, None, None, (), (SECRET, object()))
    untrusted = module.prepare_screenshots(section([target()]), True, ())
    assert untrusted.failure_details == (
        detail('screenshot_1', 'upload', 'unknown', 'upload_failed'),)
    assert untrusted.values['screenshot_1'] == '截图上传失败'
    assert untrusted.values['screenshot'] == GENERIC_UPLOAD


def test_generic_upload_uses_known_error_message(monkeypatch):
    """未知 provider 的已知上传码仍给出固定通道名与原因。"""
    module, _, upload = boundaries(monkeypatch)
    upload.return_value = UploadResult(False, None, None, (), (
        UploadFailure(SECRET, 'http_failed', SECRET),))
    batch = module.prepare_screenshots(section([target()]), True, ())
    reason = ImageHostError('http_failed', '').message
    assert batch.values['screenshot'] == f'截图上传失败：通道 未知图床 {reason}。'


@pytest.mark.parametrize('code', ['invalid_provider', 'invalid_options'])
def test_single_and_aggregate_share_failure_category(monkeypatch, code):
    """同一失败在单项、编号与聚合变量中分类与文案一致。"""
    module, capture, upload = boundaries(monkeypatch)
    upload.return_value = UploadResult(False, None, None, (),
                                       (UploadFailure('配置', code, SECRET),))
    batch = module.prepare_screenshots(section([target(out='shot')]), True, ())
    sentence = module.format_failure_sentence('config', '配置', code)

    assert batch.values['shot'] == sentence
    assert batch.values['screenshot'] == sentence
    assert batch.failure_details == (
        module.ScreenshotFailureDetail('shot', 'config', '配置', code),)


@pytest.mark.parametrize(('category', 'code', 'expected'), [
    ('capture', 'malicious', '截图失败'),
    ('config', 'malicious', '截图配置失败：图床配置无效'),
    ('upload', 'malicious', '截图上传失败：通道 未知图床 图床上传失败。'),
])
def test_unknown_failures_do_not_cross_categories(category, code, expected):
    """未知码只使用本类别兜底，不借用其他类别或回显原值。"""
    module = pipeline()
    assert module.format_failure_sentence(category, 'unknown', code) == expected


def test_save_warning_does_not_replace_image(monkeypatch, tmp_path):
    """保存失败只追加提示，成功图片仍按变量输出。"""
    module, capture, _ = boundaries(monkeypatch)

    def fail_write(path, data, *args, **kwargs):
        """模拟临时目录写入失败，不触碰真实磁盘。"""
        raise OSError(SECRET)

    winfs = import_module('modules.screenshot.retention').winfs
    monkeypatch.setattr(winfs, 'list_names', lambda path: ())
    monkeypatch.setattr(winfs, 'write_exclusive', fail_write)
    config = section([target(out='shot')])
    config['retention'] = {'enabled': True}
    batch = module.prepare_screenshots(
        config, True, (),
        runtime={'program_dir': str(tmp_path), 'config_stem': 'config'})

    assert batch.values['shot'] == f'![shot]({URL})'
    assert batch.values['screenshot'] == f'![shot]({URL})'
    assert '截图保存失败：无法写入输出路径' in batch.warnings
    assert SECRET not in str(batch)


def test_legacy_profile_codes_and_current_group_403():
    """历史权限码保留兼容展示，当前组 403 只按 HTTP 失败展示。"""
    module = pipeline()
    legacy = (
        ('profile_forbidden',
         '截图上传失败：通道 BeeIMG.CN 的账号资料接口无访问权限，'
         '请为令牌授权资料读取权限，或填写该账号可用的 options.storage_id。'),
        ('profile_forbidden_invalid_storage',
         '截图上传失败：通道 BeeIMG.CN 手填储存驱动无效，'
         '请授权资料读取权限或填写当前账号可用的 options.storage_id。'),
    )
    for code, sentence in legacy:
        assert module.format_failure_sentence(
            'upload', 'beeimg_cn', code) == sentence

    error = ImageHostError('http_failed', '', stage='group')
    error.http_status = 403
    failure = UploadFailure('beeimg_cn', error.code, error.message,
                            stage=error.stage, http_status=403)
    batch = module.ScreenshotBatch(
        {'shot': '旧值'}, (), (),
        (module.ScreenshotFailureDetail('shot', 'upload', 'beeimg_cn',
                                        failure.code),))
    fragments = module.build_screenshot_variable_fragments(batch, 'shot')
    assert fragments == (
        ('shot', 'text', '截图上传失败：通道 BeeIMG.CN 图床 HTTP 请求失败。'),)
    assert module.format_failure_sentence(
        'upload', 'beeimg_cn', failure.code) == (
            '截图上传失败：通道 BeeIMG.CN 图床 HTTP 请求失败。')
    assert '资料' not in fragments[0][2]


def test_variable_fragments_place_notice_after_result():
    """提示块以分隔线接在结果之后，多条提示用单换行连接。"""
    module = pipeline()
    image = f'![shot]({URL})'
    batch = module.ScreenshotBatch(
        {'shot': image, 'screenshot': image}, ('提示甲', '提示乙'), ('shot',))

    assert module.build_screenshot_variable_fragments(batch, 'shot') == (
        ('shot', 'image', image),
        ('', 'separator', '---'),
        ('', 'text', '截图配置提示: 提示甲\n提示乙'),
    )


def test_variable_fragments_without_warnings_have_no_separator():
    """无批次提示时不产生分隔线或空提示块。"""
    module = pipeline()
    image = f'![shot]({URL})'
    batch = module.ScreenshotBatch({'shot': image, 'screenshot': image}, (), ())

    assert module.build_screenshot_variable_fragments(batch, 'shot') == (
        ('shot', 'image', image),)


def test_variable_fragments_warning_only_has_no_leading_separator():
    """只有提示而没有结果时直接给出提示块，不制造前导分隔线。"""
    module = pipeline()
    batch = module.ScreenshotBatch({'shot': ''}, ('提示甲',), ())

    assert module.build_screenshot_variable_fragments(batch, 'shot') == (
        ('', 'text', '截图配置提示: 提示甲'),)


def test_variable_fragments_deduplicate_warnings():
    """重复批次提示在变量值内只出现一次。"""
    module = pipeline()
    image = f'![shot]({URL})'
    batch = module.ScreenshotBatch(
        {'shot': image, 'screenshot': image}, ('提示甲', '提示甲'), ())

    assert module.build_screenshot_variable_fragments(batch, 'shot') == (
        ('shot', 'image', image),
        ('', 'separator', '---'),
        ('', 'text', '截图配置提示: 提示甲'),
    )


def test_aggregate_variable_fragments_keep_result_order():
    """聚合变量先按既有顺序列出全部结果，再接一次提示块。"""
    module = pipeline()
    first = f'![one]({URL})'
    second = f'![two]({URL})'
    batch = module.ScreenshotBatch(
        {'one': first, 'two': second, 'screenshot': f'{first}\n\n{second}'},
        ('提示甲',), ())

    assert module.build_screenshot_variable_fragments(batch, 'screenshot') == (
        ('one', 'image', first),
        ('two', 'image', second),
        ('', 'separator', '---'),
        ('', 'text', '截图配置提示: 提示甲'),
    )
