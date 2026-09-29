#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""截图配置默认值、无损写回与凭证安全回归测试。"""

import copy
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.parser import ParserError

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


@pytest.mark.parametrize('with_screenshot', [False, True])
@pytest.mark.parametrize('stage', ['mkstemp', 'fdopen', 'dump', 'replace'])
def test_write_error_does_not_disclose_token_and_preserves_file(
        tmp_path, monkeypatch, caplog, stage, with_screenshot):
    """截图配置写回异常不泄露凭证，并保留原文件及清理临时文件。"""
    push = {'screenshot': {
        'targets': [], 'image_host': [{'provider': 'wmimg', 'token': SECRET}],
    }} if with_screenshot else {}
    path = _write_config(tmp_path, push)
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
    expected_token = SECRET if with_screenshot else ''
    assert result['push']['screenshot']['image_host'][0]['token'] == expected_token
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
    assert SECRET not in caplog.text
    assert 'cannot write token:' not in caplog.text
    assert '无法写回配置文件' in caplog.text
    expected_stage = 'serialize' if stage == 'dump' else stage
    assert f'stage={expected_stage}' in caplog.text
    assert 'type=OSError' in caplog.text
    assert 'errno=' not in caplog.text


@pytest.mark.parametrize(
    'serialize_kind, close_kind, unlink_kind, close_before',
    [
        ('os', 'permission', None, False),
        ('os', 'permission', 'os', False),
        ('os', None, None, False),
        (None, 'permission', None, False),
        ('os', 'permission', None, True),
        (None, 'permission', None, True),
        ('interrupt', 'permission', 'os', False),
        ('exit', 'permission', 'os', False),
        ('interrupt', 'exit', 'os', False),
        ('exit', 'interrupt', 'os', False),
        ('os', 'interrupt', None, False),
        ('os', 'exit', None, False),
        ('os', 'permission', 'interrupt', False),
        ('os', 'permission', 'exit', False),
    ],
    ids=[
        'serialize-close', 'serialize-close-unlink', 'serialize-only',
        'close-only', 'serialize-close-before-release', 'close-before-release',
        'serialize-interrupt', 'serialize-exit', 'interrupt-before-exit',
        'exit-before-interrupt', 'close-interrupt', 'close-exit',
        'unlink-interrupt', 'unlink-exit',
    ],
)
def test_atomic_stream_failure_preserves_primary_and_releases_resources(
        tmp_path, caplog, serialize_kind, close_kind, unlink_kind,
        close_before):
    """组合故障保留主因和控制信号，关闭资源且不替换原配置。"""
    path = _write_config(tmp_path, {})
    original = path.read_bytes()
    original_fdopen = os.fdopen
    original_unlink = os.unlink
    streams = []
    descriptors = []
    close_attempts = []
    unlink_attempts = []

    def make_failure(kind, message):
        """构造仅含合成敏感文本的普通异常或控制信号。"""
        if kind == 'os':
            return OSError(28, message)
        if kind == 'permission':
            return PermissionError(13, message)
        if kind == 'interrupt':
            return KeyboardInterrupt(message)
        if kind == 'exit':
            return SystemExit(message)
        return None

    serialize_error = make_failure(serialize_kind, 'FAKE_PRIMARY_SECRET')
    close_error = make_failure(close_kind, 'FAKE_CLEANUP_SECRET')
    unlink_error = make_failure(unlink_kind, 'FAKE_UNLINK_SECRET')
    failures = [error for error in (
        serialize_error, close_error, unlink_error,
    ) if error is not None]
    control_error = next((error for error in failures
                          if not isinstance(error, Exception)), None)

    class FailingStream:
        """包装真实临时流，模拟释放底层资源之前或之后关闭失败。"""

        def __init__(self, stream):
            """保留真实流以验证关闭状态。"""
            self.stream = stream

        def __getattr__(self, name):
            """将普通流操作交给真实文件对象。"""
            return getattr(self.stream, name)

        def __enter__(self):
            """兼容原有上下文管理器写回路径。"""
            return self

        def __exit__(self, *args):
            """在退出上下文时执行同一关闭故障。"""
            self.close()

        def close(self):
            """记录关闭尝试，并在指定释放时机抛出合成异常。"""
            close_attempts.append(self)
            if close_before and close_error is not None:
                raise close_error
            self.stream.close()
            if close_error is not None:
                raise close_error

    def wrap_fdopen(fd, *args, **kwargs):
        """记录实际描述符并包装生产路径创建的临时流。"""
        descriptors.append(fd)
        wrapped = FailingStream(original_fdopen(fd, *args, **kwargs))
        streams.append(wrapped)
        return wrapped

    def partial_dump(value, stream):
        """写入部分临时内容后按场景触发序列化失败。"""
        stream.write('partial: true\n')
        if serialize_error is not None:
            raise serialize_error

    def unlink_temp(temp_path):
        """记录删除尝试，在故障场景模拟系统拒绝删除。"""
        unlink_attempts.append(temp_path)
        if unlink_error is not None:
            raise unlink_error
        original_unlink(temp_path)

    writer = config_module._make_write_yaml()
    caplog.set_level('INFO', logger='modules.config')
    try:
        with patch('modules.config.os.fdopen', side_effect=wrap_fdopen), \
                patch('modules.config.os.unlink', side_effect=unlink_temp), \
                patch('modules.config.os.replace') as replace, \
                patch.object(writer, 'dump', side_effect=partial_dump), \
                patch('modules.config._make_write_yaml', return_value=writer):
            if control_error is not None:
                with pytest.raises(BaseException) as caught:
                    config_module.load_config(str(path))
                assert caught.value is control_error
            else:
                result = config_module.load_config(str(path))
                assert result['monitor']['psutil']['process_name'] == 'ok.exe'
                for name in TEMPLATE_NAMES:
                    assert result['push']['templates'][name][
                        'capture_screenshot'] is True
            replace.assert_not_called()
        assert path.read_bytes() == original
        assert len(streams) == len(descriptors) == 1
        assert close_attempts
        for fd in descriptors:
            with pytest.raises(OSError) as caught:
                os.fstat(fd)
            assert caught.value.errno == 9
        if not close_before:
            assert all(stream.stream.closed for stream in streams)
        assert len(unlink_attempts) == 1
        if unlink_error is None:
            assert list(tmp_path.iterdir()) == [path]
        else:
            assert os.path.exists(unlink_attempts[0])
        if control_error is None:
            expected = (
                'stage=serialize type=OSError errno=28 category=ENOSPC'
                if serialize_error is not None else
                'stage=close type=PermissionError errno=13 category=EACCES'
            )
            assert expected in caplog.text
            if serialize_error is not None:
                assert 'type=PermissionError' not in caplog.text
                assert 'errno=13' not in caplog.text
        else:
            assert '无法写回配置文件' not in caplog.text
        for secret in ('FAKE_PRIMARY_SECRET', 'FAKE_CLEANUP_SECRET',
                       'FAKE_UNLINK_SECRET'):
            assert secret not in caplog.text
        assert not any(record.exc_info for record in caplog.records)
        assert '正在写回配置信息' not in caplog.text
        assert '配置参数版本差异检查完成' not in caplog.text
    finally:
        # 即使红灯阶段未释放资源，也不让测试遗留句柄和临时文件。
        for stream in streams:
            try:
                stream.stream.close()
            except OSError:
                pass
        for fd in descriptors:
            try:
                os.close(fd)
            except OSError:
                pass
        for temp_path in tmp_path.glob('*.tmp'):
            original_unlink(temp_path)


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


@pytest.mark.parametrize('with_errno', [False, True])
def test_read_failure_keeps_safe_diagnostics_and_exit(
        tmp_path, monkeypatch, caplog, with_errno):
    """读取失败保留阶段及数字错误码，不输出异常原文或文件名。"""
    path = _write_config(tmp_path, {})
    original = path.read_bytes()
    monkeypatch.setenv('CONFIG_TEST_TOKEN', 'expanded-config-secret')
    message = f'{SECRET}\n{os.environ["CONFIG_TEST_TOKEN"]}'
    failure = (PermissionError(13, message, f'{message}.yaml')
               if with_errno else OSError(message))
    failure.filename = f'{message}.yaml'
    with patch('modules.config.open', side_effect=failure):
        with pytest.raises(SystemExit) as error:
            config_module.load_config(str(path))
    assert error.value.code == 1
    assert path.read_bytes() == original
    assert SECRET not in caplog.text
    assert 'expanded-config-secret' not in caplog.text
    assert not any(record.exc_info for record in caplog.records)
    assert 'stage=read' in caplog.text
    if with_errno:
        assert 'type=PermissionError' in caplog.text
        assert 'errno=13' in caplog.text
        assert 'category=EACCES' in caplog.text
    else:
        assert 'type=OSError' in caplog.text
        assert 'errno=' not in caplog.text


def test_yaml_error_without_screenshot_reports_one_based_positions(
        tmp_path, caplog):
    """无截图的坏 YAML 也保留行列，且不输出凭证源码。"""
    path = tmp_path / 'invalid.yaml'
    path.write_text(f'token: ["{SECRET}"\n', encoding='utf-8')
    original = path.read_bytes()
    with pytest.raises(SystemExit) as error:
        config_module.load_config(str(path))
    assert error.value.code == 1
    assert path.read_bytes() == original
    assert 'stage=parse' in caplog.text
    assert 'type=ParserError' in caplog.text
    assert 'problem_line=2 problem_column=1' in caplog.text
    assert 'context_line=1 context_column=8' in caplog.text
    assert SECRET not in caplog.text
    assert 'token:' not in caplog.text


def test_yaml_context_is_not_used_as_diagnostic_text(
        tmp_path, monkeypatch, caplog):
    """解析上下文及标记中的原文不进入摘要，只保留数字位置。"""
    path = _write_config(tmp_path, {})
    original = path.read_bytes()
    monkeypatch.setenv('CONFIG_TEST_TOKEN', 'expanded-config-secret')
    message = f'{SECRET}\n{os.environ["CONFIG_TEST_TOKEN"]}'
    context_mark = SimpleNamespace(
        line=2, column=4, buffer=message, name=message,
    )
    problem_mark = SimpleNamespace(
        line=6, column=8, buffer=message, name=message,
    )
    failure = ParserError(message, context_mark, message, problem_mark)
    with patch.object(YAML, 'load', side_effect=failure):
        with pytest.raises(SystemExit) as error:
            config_module.load_config(str(path))
    assert error.value.code == 1
    assert path.read_bytes() == original
    assert 'stage=parse' in caplog.text
    assert 'type=ParserError' in caplog.text
    assert 'problem_line=7 problem_column=9' in caplog.text
    assert 'context_line=3 context_column=5' in caplog.text
    assert SECRET not in caplog.text
    assert 'expanded-config-secret' not in caplog.text
    assert not any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize('push', [
    {},
    {'screenshot': {}},
    {'screenshot': []},
    {'screenshot': {'image_host': [{'provider': 'wmimg', 'token': SECRET}]}},
], ids=['missing', 'empty-map', 'empty-list', 'credentials'])
@pytest.mark.parametrize('stage', ['mkstemp', 'fdopen', 'dump', 'replace'])
def test_write_errno_diagnostics_do_not_depend_on_screenshot(
        tmp_path, monkeypatch, caplog, push, stage):
    """所有截图形态使用相同安全诊断，保留磁盘错误码与原文件。"""
    path = _write_config(tmp_path, push)
    original = path.read_bytes()
    failure = OSError(28, SECRET, f'{SECRET}.yaml')
    failure.winerror = 112

    def fail(*args, **kwargs):
        """模拟带敏感消息和文件名的磁盘空间不足。"""
        raise failure

    if stage == 'dump':
        writer = config_module._make_write_yaml()
        monkeypatch.setattr(writer, 'dump', fail)
        with patch('modules.config._make_write_yaml', return_value=writer):
            result = config_module.load_config(str(path))
    else:
        owner = config_module.tempfile if stage == 'mkstemp' else os
        monkeypatch.setattr(owner, stage, fail)
        result = config_module.load_config(str(path))
    assert result['monitor']['psutil']['process_name'] == 'ok.exe'
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
    expected_stage = 'serialize' if stage == 'dump' else stage
    assert f'stage={expected_stage}' in caplog.text
    assert 'type=OSError' in caplog.text
    assert 'errno=28' in caplog.text
    assert 'winerror=112' in caplog.text
    assert 'category=ENOSPC' in caplog.text
    assert SECRET not in caplog.text
    assert not any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize('failure_kind', ['os', 'yaml', 'generic'])
def test_untrusted_exception_fields_do_not_enter_summary(
        tmp_path, caplog, failure_kind):
    """异常元数据只接受数值，通用异常仅记录安全类型及阶段。"""
    path = _write_config(tmp_path, {})
    original = path.read_bytes()
    message = f'{SECRET}\nforged-log-entry'
    if failure_kind == 'os':
        failure = OSError(message)
        failure.errno = message
        failure.winerror = message
        target = 'modules.config.open'
        expected_type = 'OSError'
        expected_stage = 'read'
    elif failure_kind == 'yaml':
        mark = SimpleNamespace(line=message, column=message, buffer=message)
        failure = ParserError(message, mark, message, mark)
        target = 'ruamel.yaml.YAML.load'
        expected_type = 'ParserError'
        expected_stage = 'parse'
    else:
        failure = ValueError(message)
        target = 'ruamel.yaml.YAML.load'
        expected_type = 'ValueError'
        expected_stage = 'parse'
    with patch(target, side_effect=failure):
        with pytest.raises(SystemExit) as error:
            config_module.load_config(str(path))
    assert error.value.code == 1
    assert path.read_bytes() == original
    assert f'stage={expected_stage}' in caplog.text
    assert f'type={expected_type}' in caplog.text
    assert SECRET not in caplog.text
    assert 'forged-log-entry' not in caplog.text
    assert 'errno=' not in caplog.text
    assert 'winerror=' not in caplog.text
    assert '_line=' not in caplog.text
    assert '_column=' not in caplog.text
    assert not any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize('stage', ['create', 'serialize'])
def test_default_creation_error_keeps_safe_summary_and_reraises(
        tmp_path, caplog, stage):
    """默认文件创建异常继续抛出，但日志不使用异常消息或文件名。"""
    path = tmp_path / 'default.yaml'
    failure = OSError(28, SECRET, f'{SECRET}.yaml')
    target = 'modules.config.open' if stage == 'create' else (
        'ruamel.yaml.YAML.dump'
    )
    with patch(target, side_effect=failure):
        with pytest.raises(OSError) as error:
            config_module.create_default_config(str(path))
    assert error.value is failure
    assert f'stage={stage}' in caplog.text
    assert 'type=OSError' in caplog.text
    assert 'errno=28' in caplog.text
    assert 'category=ENOSPC' in caplog.text
    assert SECRET not in caplog.text
