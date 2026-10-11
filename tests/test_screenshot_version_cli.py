from argparse import Namespace
from unittest.mock import Mock

import pytest

from modules.screenshot import cli
from modules.screenshot.models import CaptureError


MARKERS = ('@{version}', '@{VERSION}', '@{Version}')
FORMS = ('inline', 'bare', 'positional', 'option')
INVALID_TARGETS = (
    '@{version}', '   @{Version}', 'PrivateApp@{vErSiOn}',
    'PrivateApp@{ version}', 'PrivateApp@{version }',
    'PrivateApp@{version}@{VERSION}', 'PrivateApp@{version} suffix',
    'PrivateApp@{version} ', 'PrivateApp@{version}\t',
    'PrivateApp@{version', 'PrivateApp@{VERSION{other}',
)


def source_args(source, target, form):
    if form == 'inline':
        return ['--source', f'{source}:{target}']
    if form == 'bare':
        return ['--source', target]
    if form == 'positional':
        return ['--source', source, target]
    return ['--source', source, '--target', target]


@pytest.mark.parametrize('marker', MARKERS)
@pytest.mark.parametrize('form', FORMS)
@pytest.mark.parametrize('base', ['AppName', '  Straße'])
def test_cli_marker_forms_preserved(marker, form, base):
    target = base + marker
    args = cli.parse_screenshot_args(
        source_args('window', target, form)
        + ['--output', 'debug.png', '--upload', 'provider: catbox'])
    assert vars(args) == {
        'source': 'window', 'target': target, 'output': 'debug.png',
        'hosts': [{'provider': 'catbox', 'options': {}}],
    }


@pytest.mark.parametrize('source,form', [
    ('window', form) for form in FORMS
] + [('adb', form) for form in FORMS if form != 'bare'])
@pytest.mark.parametrize('target', INVALID_TARGETS)
def test_cli_invalid_expression_exit_two(
        monkeypatch, capsys, source, form, target):
    upload_parser = Mock(return_value=[])
    monkeypatch.setattr(cli, 'parse_inline_hosts', upload_parser)
    with pytest.raises(SystemExit) as caught:
        cli.parse_screenshot_args(
            source_args(source, target, form)
            + ['--output', '--upload', 'provider: catbox'])
    assert caught.value.code == 2
    assert capsys.readouterr() == ('', '截图参数无效\n')
    upload_parser.assert_not_called()


@pytest.mark.parametrize('marker', MARKERS)
@pytest.mark.parametrize('form', ['inline', 'positional', 'option'])
def test_cli_adb_rejects_valid_window_markers(capsys, marker, form):
    with pytest.raises(SystemExit) as caught:
        cli.parse_screenshot_args(
            source_args('adb', ' PrivateDevice' + marker + ' ', form)
            + ['--output'])
    assert caught.value.code == 2
    assert capsys.readouterr() == ('', '截图参数无效\n')


@pytest.mark.parametrize('source,target', [
    (source, target) for source in ('window', 'adb')
    for target in INVALID_TARGETS
] + [('adb', 'PrivateDevice' + marker) for marker in MARKERS])
def test_direct_namespace_rejected_without_side_effects(
        monkeypatch, capsys, source, target):
    spies = {name: Mock() for name in (
        'capture', '_resolve_output', 'save_cli', '_upload_all')}
    spies['_resolve_output'].return_value = ('debug.png', True)
    spies['capture'].side_effect = CaptureError('window_not_found', 'unused')
    for name, spy in spies.items():
        monkeypatch.setattr(cli, name, spy)
    args = Namespace(source=source, target=target, output='debug.png',
                     hosts=[{'provider': 'catbox'}])
    assert cli.run_screenshot_cli(args, '.') == 2
    assert capsys.readouterr() == ('截图参数无效\n', '')
    for spy in spies.values():
        spy.assert_not_called()


@pytest.mark.parametrize('marker', MARKERS)
@pytest.mark.parametrize('code,message', [
    ('window_not_found', '未找到匹配的窗口'),
    ('window_ambiguous', '存在多个匹配的窗口'),
    ('window_gone', '目标窗口已失效或标题已变化'),
])
def test_valid_expression_capture_failure_is_one(
        monkeypatch, capsys, marker, code, message):
    target = 'AppName' + marker
    capture = Mock(side_effect=CaptureError(code, 'private backend detail'))
    monkeypatch.setattr(cli, 'capture', capture)
    save = Mock()
    upload = Mock()
    monkeypatch.setattr(cli, 'save_cli', save)
    monkeypatch.setattr(cli, '_upload_all', upload)
    args = cli.parse_screenshot_args(
        ['--source', target, '--upload', 'provider: catbox'])
    assert cli.run_screenshot_cli(args, '.') == 1
    capture.assert_called_once_with(
        'window', target, image_format='jpeg', purpose='cli')
    assert capsys.readouterr() == ('截图失败：' + message + '\n', '')
    save.assert_not_called()
    upload.assert_not_called()


@pytest.mark.parametrize('source,form', [
    ('window', form) for form in FORMS
] + [('adb', form) for form in FORMS if form != 'bare'])
def test_other_marker_keeps_existing_whitespace_rules(source, form):
    target = '  AppName@{other}  '
    args = cli.parse_screenshot_args(
        source_args(source, target, form) + ['--output'])
    assert args.target == (target if source == 'window' else target.strip())


def test_cli_help_explains_version_matching(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.parse_screenshot_args(['--help'])
    assert caught.value.code == 0
    help_text = ''.join(capsys.readouterr().out.split())
    for text in (*MARKERS, '无标记', '完整窗口标题', '精确匹配', '区分大小写',
                 '末尾', '一次', '应用名', '自由尾部', 'Launcher', 'Settings',
                 '多个匹配', '拒绝', 'ADB'):
        assert text in help_text
