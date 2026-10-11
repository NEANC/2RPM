from dataclasses import FrozenInstanceError
from importlib import import_module

import pytest


MARKERS = ['@{version}', '@{VERSION}', '@{Version}']
INVALID_TARGETS = [
    '@{version}', '   @{version}', '\t@{version}',
    'App@{vErSiOn}', 'App@{ version}', 'App@{version }',
    'App@{\tVERSION\n}', 'App@{\u2003version\u2003}',
    'App@{version} ', 'App@{version} tail',
    'App@{version', 'App@{VERSION trailing',
    'App@{version}@{Version}', 'App@{ version}@{version}',
    'App@{version}@{vErSiOn}', 'App@{version}@{VERSION',
    'App@{VERSION broken @{version}',
    'App@{version broken {tail',
    'App@{version broken {tail@{Version}',
]
CONTROLS = [chr(value) for value in (
    *range(32), *range(127, 160), 0x2028, 0x2029)]


@pytest.fixture
def matcher():
    return import_module('modules.screenshot.title_match')


@pytest.mark.parametrize('marker', MARKERS)
@pytest.mark.parametrize('base', ['AppName', 'AnotherTool'])
@pytest.mark.parametrize('suffix', [
    '', ' V2.33.0', ' 2.33', ' v 2.33.0', ' Version 2.33.0.1',
    ' v2.33.0 Alpha+g8e91ab2', ' v2.33.0-beta.2-g8e91ab2',
    ' v2.33.0-RC1 g8e91ab2', ' v2.33.0-RC1 unknown-text',
    ' v2.33.0+', ' version 2.33.0 Launcher', ' 8e91ab2',
    ' v2.33.0 Settings', ' v2.33.0 中文构建说明',
    '   v   02.003.0', ' VeRsIoN   1.2', ' 1.2-', ' 1.2 ',
])
def test_version_target_accepts_supported_titles(matcher, marker, base, suffix):
    target = matcher.parse_window_target(base + marker)
    assert matcher.matches_window_title(target, base.lower() + suffix)
    assert (target.text, target.base, target.versioned) == (
        base + marker, base, True)


@pytest.mark.parametrize('suffix', [
    'Helper', ' Launcher', ' Settings', ' version Settings', ' v2',
    ' v2.33.0.1.5', ' v2.33.0abc', ' version2.33.0',
    ' ver 2.33', ' version: 2.33', ' 版本 2.33',
    ' １.２', ' 1.２', ' 1..2', ' .1.2', ' 1.2.',
    '\u00a0v1.2', ' v\u00a01.2', ' version\u00a01.2',
    ' 8e91abz', ' 8e91ab2 extra', ' 8e91ab2+', ' 8e91ab2-',
    ' gg8e91ab2', ' 8e91ab2 ', ' ',
])
def test_version_target_rejects_partial_matches(matcher, suffix):
    target = matcher.parse_window_target('AppName@{version}')
    assert not matcher.matches_window_title(target, 'AppName' + suffix)


@pytest.mark.parametrize('length', [6, 7, 40, 64, 65])
@pytest.mark.parametrize('prefix', ['', 'g', 'G'])
@pytest.mark.parametrize('digit', ['a', 'F', '0'])
def test_hash_length_excludes_decoration(matcher, length, prefix, digit):
    target = matcher.parse_window_target('App@{version}')
    assert matcher.matches_window_title(
        target, 'App ' + prefix + digit * length) == (7 <= length <= 64)


@pytest.mark.parametrize('parts', [1, 2, 3, 4, 5])
@pytest.mark.parametrize('prefix', ['', 'v', 'V ', 'version '])
def test_version_segment_count(matcher, parts, prefix):
    target = matcher.parse_window_target('App@{version}')
    assert matcher.matches_window_title(
        target, 'App ' + prefix + '.'.join(['01'] * parts)
    ) == (2 <= parts <= 4)


@pytest.mark.parametrize('base,title', [
    ('Straße', 'STRASSE v1.2'), ('STRASSE', 'Straße v1.2'),
    ('Straße', 'STRASSE'), ('程序 窗口', '程序 窗口 V1.2'),
])
def test_casefold_length_change(matcher, base, title):
    assert matcher.matches_window_title(
        matcher.parse_window_target(base + '@{version}'), title)


def test_protocol_stays_ascii(matcher):
    target = matcher.parse_window_target('App@{version}')
    assert not matcher.matches_window_title(target, 'App verſion 1.2')
    assert not matcher.has_version_marker_candidate('App@{verſion}')
    assert not matcher.parse_window_target('App@{verſion}').versioned


def test_literal_regex_characters(matcher):
    target = matcher.parse_window_target('A[1].(B)@{version}')
    assert matcher.matches_window_title(target, 'a[1].(b) v1.2')
    assert not matcher.matches_window_title(target, 'A1xB v1.2')


@pytest.mark.parametrize('control', CONTROLS)
@pytest.mark.parametrize('position', ['base', 'separator', 'tail'])
def test_version_mode_rejects_controls(matcher, control, position):
    base = 'App' + (control if position == 'base' else '')
    target = matcher.parse_window_target(base + '@{version}')
    title = base + (control if position == 'separator' else ' ') + 'v1.2'
    if position == 'tail':
        title += ' tail' + control
    assert not matcher.matches_window_title(target, title)
    if position == 'base':
        assert not matcher.matches_window_title(target, base)


@pytest.mark.parametrize('text', [
    'App', ' App ', 'App\t\n', 'App\x00', 'App\u2028', 'App@{other}',
])
def test_exact_mode_unchanged(matcher, text):
    target = matcher.parse_window_target(text)
    assert (target.text, target.base, target.versioned) == (text, text, False)
    assert matcher.matches_window_title(target, text)
    assert not matcher.matches_window_title(target, text.lower())
    assert not matcher.matches_window_title(target, text + ' ')


@pytest.mark.parametrize('text', INVALID_TARGETS)
def test_marker_candidates_rejected(matcher, text):
    assert matcher.has_version_marker_candidate(text)
    with pytest.raises(ValueError) as error:
        matcher.parse_window_target(text)
    assert error.value.args == ('invalid_target',)


@pytest.mark.parametrize('text', [None, False, 1, {}, '', ' \t\n'])
def test_empty_or_non_string_target_rejected(matcher, text):
    with pytest.raises(ValueError) as error:
        matcher.parse_window_target(text)
    assert error.value.args == ('invalid_target',)


@pytest.mark.parametrize('text', [
    'App@{other}', 'App@{versionX}', 'App@{other', 'App@{verſion}',
])
def test_other_marker_is_literal(matcher, text):
    assert not matcher.has_version_marker_candidate(text)
    target = matcher.parse_window_target(text)
    assert not target.versioned
    assert matcher.matches_window_title(target, text)


def test_application_whitespace_is_not_trimmed(matcher):
    target = matcher.parse_window_target(' App @{version}')
    assert target.base == ' App '
    assert matcher.matches_window_title(target, ' App ')
    assert matcher.matches_window_title(target, ' App  v1.2')
    assert not matcher.matches_window_title(target, 'App v1.2')


def test_window_target_is_frozen(matcher):
    target = matcher.parse_window_target('App@{version}')
    assert isinstance(target, matcher.WindowTarget)
    with pytest.raises(FrozenInstanceError):
        target.text = 'actual title'


@pytest.mark.parametrize('source', ['window', 'adb'])
@pytest.mark.parametrize('text', [
    ' Device ', 'Device@{other}', 'Device\t', 'App@{versionX}',
])
def test_validate_capture_target_preserves_literal_inputs(matcher, source, text):
    assert matcher.validate_capture_target(source, text) is None


@pytest.mark.parametrize('marker', MARKERS)
def test_validate_window_accepts_version_target(matcher, marker):
    assert matcher.validate_capture_target('window', 'App' + marker) is None


@pytest.mark.parametrize('source,text', [
    *[(source, text) for source in ['window', 'adb']
      for text in INVALID_TARGETS + [None, '', ' \t']],
    *[('adb', 'Device' + marker) for marker in MARKERS],
    ('other', 'App'), (' Window ', 'App'), ('ADB', 'Device'),
])
def test_validate_capture_target_rejects_safely(matcher, source, text):
    with pytest.raises(ValueError) as error:
        matcher.validate_capture_target(source, text)
    assert error.value.args == ('invalid_target',)
