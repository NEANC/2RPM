from copy import deepcopy

import pytest

from modules.screenshot.targets import allocate_targets


@pytest.mark.parametrize('marker', ['@{version}', '@{VERSION}', '@{Version}'])
def test_version_targets_keep_slots_and_inputs(marker):
    raw = [
        {'provider': ' WINDOW ', 'target': ' AppName' + marker + ' \t'},
        {'provider': 'window', 'target': 'private@{vErSiOn}', 'out': 'shared'},
        {'provider': 'adb', 'target': 'private' + marker, 'out': 'shared'},
        {'provider': 'window', 'target': 'Next', 'out': 'shared'},
        {'provider': 'adb', 'target': ' device ', 'out': 'screenshot_3'},
    ]
    original = deepcopy(raw)
    targets, warnings = allocate_targets(raw)
    assert raw == original
    assert [item['index'] for item in targets] == [1, 2, 3, 4, 5]
    assert [item['provider'] for item in targets] == [
        'window', 'window', 'adb', 'window', 'adb']
    assert [item['target'] for item in targets] == [
        'AppName' + marker, 'private@{vErSiOn}', 'private' + marker,
        'Next', 'device']
    assert [item['out'] for item in targets] == [
        'screenshot_1', 'shared', 'screenshot_4', 'screenshot_5',
        'screenshot_3']
    assert [bool(item['error']) for item in targets] == [
        False, True, True, False, False]
    assert [item['renamed_from'] for item in targets] == [
        None, None, 'shared', 'shared', None]
    assert len(warnings) == 2
    assert 'private' not in str([item['error'] for item in targets])
    assert 'private' not in str(warnings)


@pytest.mark.parametrize('provider', ['window', 'adb'])
@pytest.mark.parametrize('target', [
    'private@{vErSiOn}', 'private@{ version}', 'private@{version }',
    'private@{\tVERSION\n}', 'private@{version}@{Version}',
    'private@{version}suffix', 'private@{version',
    'private@{VERSION unfinished', 'private@{version@{other}',
    '@{version}', '   @{Version}',
])
def test_invalid_version_targets_still_allocate_outputs(provider, target):
    raw = [
        {'provider': provider, 'target': target, 'out': 'screenshot'},
        {'provider': 'window', 'target': 'Next', 'out': 'screenshot_1'},
    ]
    targets, warnings = allocate_targets(raw)
    assert targets[0]['error']
    assert target not in targets[0]['error']
    assert targets[0]['out'] == 'screenshot_2'
    assert targets[0]['renamed_from'] == 'screenshot'
    assert targets[1]['error'] is None
    assert targets[1]['out'] == 'screenshot_1'
    assert len(warnings) == 1


@pytest.mark.parametrize('provider', ['window', 'adb'])
def test_other_marker_remains_literal_in_targets(provider):
    targets, warnings = allocate_targets([
        {'provider': provider, 'target': ' literal@{other} '}])
    assert targets[0]['target'] == 'literal@{other}'
    assert targets[0]['error'] is None
    assert warnings == []
