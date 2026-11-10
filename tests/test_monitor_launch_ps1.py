import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.monitor_launch import build_launch_command
from modules.monitor_target import MonitorTarget


@pytest.mark.skipif(os.name != 'nt', reason='需要 Windows 命令行解析')
@pytest.mark.parametrize('interpreter_name', ['pwsh.exe', 'powershell.exe'])
@pytest.mark.parametrize('args,expected', [
    pytest.param(None, [], id='no-args'),
    pytest.param("''", [''], id='empty-string'),
    pytest.param('hello world', ['hello', 'world'], id='simple'),
    pytest.param('"中文 空格"', ['中文 空格'], id='chinese-space'),
    pytest.param('"C:\\目录 空格\\"', ['C:\\目录 空格\\'], id='trailing-slash'),
    pytest.param("'say \"hello\"'", ['say "hello"'], id='embedded-quotes'),
    pytest.param("'&|<>^%!'", ['&|<>^%!'], id='literal-specials'),
    pytest.param('("hello" + " world") (1 + 2)', ['hello world', 3],
                 id='expressions'),
])
def test_ps1_real_argv(interpreter_name, args, expected):
    interpreter = shutil.which(interpreter_name)
    if interpreter is None:
        pytest.skip(f'未安装 {interpreter_name}')

    script = (
        '$result = @{version = $PSVersionTable.PSVersion.ToString(); '
        'argv = @($args)}\r\n'
        '$json = ConvertTo-Json -InputObject $result -Compress\r\n'
        '[Console]::WriteLine([Convert]::ToBase64String('
        '[Text.Encoding]::UTF8.GetBytes($json)))\r\n'
    )
    with tempfile.TemporaryDirectory(prefix='2rpm-f1-') as directory:
        path = Path(directory) / '参数 回显.ps1'
        path.write_bytes(script.encode('utf-8-sig'))
        target = MonitorTarget(kind='file', value=str(path), cwd=directory)
        with patch('modules.monitor_launch._resolve_powershell',
                   return_value=interpreter):
            command = build_launch_command(target, args)
        result = subprocess.run(
            command.command_line, cwd=command.cwd, shell=False,
            capture_output=True, timeout=30)

    print(f'{interpreter_name}: returncode={result.returncode}, '
          f'stdout={result.stdout!r}, stderr={result.stderr!r}')
    assert result.returncode == 0
    actual = json.loads(base64.b64decode(result.stdout.strip()))
    print(json.dumps({'interpreter': interpreter_name, **actual},
                     ensure_ascii=True))
    assert actual['argv'] == expected
