#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""截图配置默认值、无损写回与凭证安全回归测试。"""

import copy
import os
import sys
from unittest.mock import patch

import pytest
from ruamel.yaml import YAML

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import config as config_module


TEMPLATE_NAMES = ('on_end', 'on_timeout', 'on_wait_timeout', 'on_external')
SECRET = 'private-image-host-token-7319'


def _write_config(tmp_path, push, complete=False):
    """使用有效监控配置写入独立临时文件。"""
    value = config_module.get_default_config() if complete else {
        'monitor': {'mode': 'psutil', 'psutil': {'process_name': 'ok.exe'}},
    }
    if complete:
        value['push'].update(push)
    else:
        value['push'] = push
    path = tmp_path / 'screenshot-config.yaml'
    with path.open('w', encoding='utf-8') as stream:
        config_module._make_write_yaml().dump(value, stream)
    return path


def _read_config(path):
    """重新解析真实写回文件以核对语义及节点风格。"""
    with path.open(encoding='utf-8') as stream:
        return YAML().load(stream)


def test_defaults_are_registered_before_templates():
    """截图默认不指定目标，且默认图床仅含一个无凭证条目。"""
    push = config_module.DEFAULT_VALUES['push']
    assert push['screenshot'] == {
        'targets': [], 'image_host': [{'provider': 'catbox', 'token': ''}],
    }
    assert list(push).index('screenshot') < list(push).index('templates')
    for name in TEMPLATE_NAMES:
        assert push['templates'][name]['capture_screenshot'] is True
        assert push['templates'][name]['enable'] is (name != 'on_external')


def test_default_nodes_are_independent():
    """多次获取默认值时截图列表和嵌套映射互不影响。"""
    first = config_module.get_default_config()
    second = config_module.get_default_config()
    first['push']['screenshot']['targets'].append({'provider': 'window'})
    first['push']['screenshot']['image_host'][0]['token'] = SECRET
    first['push']['templates']['on_end']['capture_screenshot'] = False
    assert second['push']['screenshot'] == {
        'targets': [], 'image_host': [{'provider': 'catbox', 'token': ''}],
    }
    assert second['push']['templates']['on_end']['capture_screenshot'] is True
    assert config_module.DEFAULT_VALUES['push']['screenshot'] == {
        'targets': [], 'image_host': [{'provider': 'catbox', 'token': ''}],
    }


def test_comments_explain_independent_targets_and_template_variables():
    """中文注释解释独立目标、图床顺序和模板变量。"""
    comments = config_module.COMMENTS['push']
    targets = comments['screenshot']['targets']
    hosts = comments['screenshot']['image_host']
    for text in ('window', 'adb', 'PID', 'out', '位置', '独立'):
        assert text in targets
    for text in ('顺序', '成功', 'token', '环境', 'options'):
        assert text in hosts
    variables = comments['templates']['_comment_extra']
    for text in ('{screenshot}', 'screenshot_N', 'out'):
        assert text in variables
    for name in TEMPLATE_NAMES:
        assert 'capture_screenshot' in comments['templates'][name]
        assert '全部目标' in comments['templates'][name]


def test_missing_fields_are_completed_without_logging_host_values(
        tmp_path, caplog):
    """真实加载补齐缺省开关且不把截图默认映射写入日志。"""
    path = _write_config(tmp_path, {})
    result = config_module.load_config(str(path))
    assert result['push']['screenshot'] == {
        'targets': [], 'image_host': [{'provider': 'catbox', 'token': ''}],
    }
    for name in TEMPLATE_NAMES:
        template = result['push']['templates'][name]
        assert template['capture_screenshot'] is True
        assert template['enable'] is (name != 'on_external')
    assert "'provider': 'catbox'" not in caplog.text
    assert "'image_host':" not in caplog.text


@pytest.mark.parametrize('missing', ['targets', 'image_host'])
def test_partial_screenshot_only_fills_missing_field(tmp_path, missing):
    """只补缺少的字段，不覆盖另一个显式空列表。"""
    existing = 'image_host' if missing == 'targets' else 'targets'
    path = _write_config(tmp_path, {'screenshot': {existing: []}})
    screenshot = config_module.load_config(str(path))['push']['screenshot']
    assert screenshot[existing] == []
    assert screenshot[missing] == (
        [] if missing == 'targets' else [{'provider': 'catbox', 'token': ''}]
    )


def test_explicit_empty_lists_and_template_switches_survive(tmp_path):
    """显式空列表、关闭截图和通知开关保持用户值。"""
    templates = {
        name: {'capture_screenshot': False, 'enable': name == 'on_external'}
        for name in TEMPLATE_NAMES
    }
    path = _write_config(tmp_path, {
        'screenshot': {'targets': [], 'image_host': []},
        'templates': templates,
    })
    for _ in range(2):
        result = config_module.load_config(str(path))
        assert result['push']['screenshot'] == {'targets': [], 'image_host': []}
        for name in TEMPLATE_NAMES:
            assert result['push']['templates'][name]['capture_screenshot'] is False
            assert result['push']['templates'][name]['enable'] is (
                name == 'on_external'
            )


def test_round_trip_preserves_values_order_duplicates_and_flow_style(
        tmp_path, monkeypatch, caplog):
    """列表格式化保留大小写、凭证、重复 out 和嵌套 options。"""
    monkeypatch.setenv('WMIMG_TOKEN', 'expanded-value-must-not-appear')
    screenshot = {
        'targets': [
            {'provider': 'window', 'target': 'MuMu模拟器 1', 'out': 'Custom'},
            {'provider': 'adb', 'target': '127.0.0.1:16384', 'out': 'Custom'},
            {'provider': 'WINDOW', 'target': 'Second Window'},
        ],
        'image_host': [
            {'provider': 'wmimg', 'token': SECRET,
             'options': {'strategy_id': 1, 'album_id': 12}},
            {'provider': 'wmimg', 'token': '${WMIMG_TOKEN}'},
            {'provider': 'CatBox', 'token': 'another-token',
             'options': {'nested': {'flag': False}, 'values': [1, 2]}},
        ],
    }
    expected = copy.deepcopy(screenshot)
    path = _write_config(tmp_path, {'screenshot': screenshot})
    for _ in range(2):
        result = config_module.load_config(str(path))
        assert result['push']['screenshot'] == expected
        persisted = _read_config(path)['push']['screenshot']
        assert persisted == expected
        for key in ('targets', 'image_host'):
            assert persisted[key].fa.flow_style() is False
            assert all(item.fa.flow_style() is True for item in persisted[key])
        assert persisted['image_host'][0]['options'].fa.flow_style() is True
        assert persisted['image_host'][2]['options']['nested'].fa.flow_style()
    text = path.read_text(encoding='utf-8')
    assert '- {provider: window, target: MuMu模拟器 1, out: Custom}' in text
    assert 'target: 127.0.0.1:16384' in text
    assert "token: '${WMIMG_TOKEN}'" in text
    assert SECRET in text
    assert SECRET not in caplog.text
    assert 'expanded-value-must-not-appear' not in text + caplog.text


def test_style_only_change_writes_once(tmp_path):
    """字段齐全时纯格式变化也写回，第二次加载不再写回。"""
    path = _write_config(tmp_path, {'screenshot': {
        'targets': [{'provider': 'window', 'target': 'Window'}],
        'image_host': [{'provider': 'catbox', 'token': ''}],
    }}, complete=True)
    original = path.read_bytes()
    original_replace = os.replace
    with patch('modules.config.os.replace', wraps=original_replace) as replace:
        first = config_module.load_config(str(path))
        assert first['push']['screenshot']['targets'] == [
            {'provider': 'window', 'target': 'Window'},
        ]
        assert replace.call_count == 1
        assert path.read_bytes() != original
        saved = path.read_bytes()
        second = config_module.load_config(str(path))
        assert replace.call_count == 1
    assert first == second
    assert path.read_bytes() == saved


@pytest.mark.parametrize('screenshot', [
    None, '', 'legacy-string-is-not-migrated', False, 42, [],
    {'targets': None, 'image_host': None},
    {'targets': 'invalid', 'image_host': 42},
    {'targets': {'token': SECRET}, 'image_host': {'token': SECRET}},
    {'targets': [None, 42, 'invalid', ['nested'], {'out': 'same'}],
     'image_host': [False, 'invalid', {'token': SECRET, 'options': None}]},
])
def test_invalid_screenshot_nodes_do_not_abort_monitor(
        tmp_path, caplog, screenshot):
    """非法截图类型和显式空值保留，不阻断正常监控或猜测目标。"""
    path = _write_config(tmp_path, {'screenshot': screenshot})
    result = config_module.load_config(str(path))
    assert result['monitor']['psutil']['process_name'] == 'ok.exe'
    assert result['push']['screenshot'] == screenshot
    assert _read_config(path)['push']['screenshot'] == screenshot
    assert SECRET not in caplog.text


def test_unknown_fields_are_cleaned_without_values(tmp_path, caplog):
    """只清理 schema 外字段，日志仅包含字段路径而不包含值。"""
    path = _write_config(tmp_path, {
        'unknown_push': SECRET,
        'screenshot': {
            'unknown_screenshot': {'token': SECRET},
            'targets': [],
            'image_host': [{'provider': 'custom', 'token': SECRET,
                            'options': {'custom': SECRET}}],
        },
    })
    result = config_module.load_config(str(path))
    assert 'unknown_push' not in result['push']
    assert 'unknown_screenshot' not in result['push']['screenshot']
    assert result['push']['screenshot']['image_host'][0]['options'] == {
        'custom': SECRET,
    }
    assert SECRET not in caplog.text
    assert 'unknown_screenshot' in caplog.text


def test_yaml_parse_error_does_not_disclose_token(tmp_path, caplog):
    """解析失败摘要不包含 YAML 源码中的凭证片段。"""
    path = tmp_path / 'invalid.yaml'
    path.write_text(
        'push:\n  screenshot:\n    image_host:\n'
        f'      - {{provider: wmimg, token: "{SECRET}\n',
        encoding='utf-8',
    )
    original = path.read_bytes()
    with pytest.raises(SystemExit) as error:
        config_module.load_config(str(path))
    assert error.value.code == 1
    assert SECRET not in caplog.text + str(error.value)
    assert 'token:' not in caplog.text
    assert '无法加载配置文件' in caplog.text
    assert path.read_bytes() == original


@pytest.mark.parametrize('stage', ['mkstemp', 'fdopen', 'dump', 'replace'])
def test_write_error_does_not_disclose_token_and_preserves_file(
        tmp_path, monkeypatch, caplog, stage):
    """截图配置写回异常不泄露凭证，并保留原文件及清理临时文件。"""
    path = _write_config(tmp_path, {'screenshot': {
        'targets': [], 'image_host': [{'provider': 'wmimg', 'token': SECRET}],
    }})
    original = path.read_bytes()

    def fail(*args, **kwargs):
        """模拟错误信息携带敏感值的写回失败。"""
        raise OSError(f'cannot write token: {SECRET}')

    if stage == 'dump':
        writer = config_module._make_write_yaml()
        monkeypatch.setattr(writer, 'dump', fail)
        with patch('modules.config._make_write_yaml', return_value=writer):
            result = config_module.load_config(str(path))
    else:
        owner = config_module.tempfile if stage == 'mkstemp' else os
        monkeypatch.setattr(owner, stage, fail)
        result = config_module.load_config(str(path))
    assert result['push']['screenshot']['image_host'][0]['token'] == SECRET
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
    assert SECRET not in caplog.text
    assert '无法写回配置文件' in caplog.text


@pytest.mark.parametrize('exception', [KeyboardInterrupt, SystemExit])
def test_load_does_not_swallow_control_exceptions(tmp_path, monkeypatch, exception):
    """截图配置处理不能吞掉进程控制异常。"""
    path = _write_config(tmp_path, {'screenshot': {'targets': [], 'image_host': []}})

    def interrupt(*args, **kwargs):
        """模拟创建临时文件前收到进程控制异常。"""
        raise exception()

    monkeypatch.setattr(config_module.tempfile, 'mkstemp', interrupt)
    with pytest.raises(exception):
        config_module.load_config(str(path))


def test_created_default_file_has_flow_host_and_unchanged_content(tmp_path):
    """新建文件使用流式图床条目，不向既有正文追加图片变量。"""
    path = tmp_path / 'default.yaml'
    config_module.create_default_config(str(path))
    persisted = _read_config(path)
    screenshot = persisted['push']['screenshot']
    assert screenshot['targets'] == []
    assert screenshot['image_host'][0].fa.flow_style() is True
    assert "- {provider: catbox, token: ''}" in path.read_text(encoding='utf-8')
    for name in TEMPLATE_NAMES:
        template = persisted['push']['templates'][name]
        assert template['content'] == (
            config_module.DEFAULT_VALUES['push']['templates'][name]['content']
        )
        assert 'screenshot' not in template['content']


def test_missing_screenshot_nodes_do_not_share_defaults():
    """补齐截图字段后修改用户节点不会污染提供的默认结构。"""
    defaults = config_module.get_default_config()['push']
    user = {}
    assert config_module._check_missing_params(user, defaults, 'push')
    user['screenshot']['image_host'][0]['token'] = SECRET
    user['screenshot']['targets'].append({'provider': 'window'})
    assert defaults['screenshot']['image_host'][0]['token'] == ''
    assert defaults['screenshot']['targets'] == []


def test_merge_screenshot_nodes_does_not_share_user_input():
    """合并后的截图嵌套节点与调用方输入保持独立。"""
    user = {'push': {'screenshot': {
        'targets': [{'provider': 'window', 'target': 'Window'}],
        'image_host': [{'provider': 'custom', 'token': SECRET,
                        'options': {'values': [1, 2]}}],
    }}}
    expected = copy.deepcopy(user)
    merged = config_module.merge_configs(user, config_module.get_default_config())
    merged['push']['screenshot']['targets'][0]['target'] = 'Changed'
    merged['push']['screenshot']['image_host'][0]['options']['values'].append(3)
    assert user == expected


def test_screenshot_lists_bypass_channel_parser_and_preserve_environment(
        tmp_path, monkeypatch):
    """截图列表不经过通道语义解析，也不展开或修改环境变量。"""
    monkeypatch.setenv('WMIMG_TOKEN', 'fake-environment-secret')
    environment = dict(os.environ)
    path = _write_config(tmp_path, {
        'screenshot': {
            'targets': [{'provider': 'window', 'target': 'MuMu模拟器 1'}],
            'image_host': [{'provider': 'wmimg', 'token': '${WMIMG_TOKEN}'}],
        },
        'templates': {name: {'content': '用户正文 {screenshot}'}
                      for name in TEMPLATE_NAMES},
    })
    parser = config_module.parse_push_channels
    with patch('modules.config.parse_push_channels', wraps=parser) as parsed:
        result = config_module.load_config(str(path))
    for call in parsed.call_args_list:
        assert call.args[0] not in (
            result['push']['screenshot']['targets'],
            result['push']['screenshot']['image_host'],
        )
    assert dict(os.environ) == environment
    assert result['push']['screenshot']['image_host'][0]['token'] == (
        '${WMIMG_TOKEN}'
    )
    for name in TEMPLATE_NAMES:
        assert result['push']['templates'][name]['content'] == '用户正文 {screenshot}'
