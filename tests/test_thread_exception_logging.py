#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""线程未捕获异常日志：真实线程、真实迁移与安全元数据边界。"""

import inspect
import logging
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import threading
import types

import pytest

from modules import log_safety
from modules import logger as logger_mod


REPO_ROOT = Path(__file__).resolve().parents[1]

ANSI_RED = '\x1b[31m'

PARENT_MARK = 'PARENT_SCOPE_MARK_7723'
WORKER_MARK = 'WORKER_SCOPE_MARK_3187'
LOCAL_MARK = 'LOCAL_PAYLOAD_MARK_4402'
CHAIN_MARK = 'INNER_CHAIN_MARK_2081'
METADATA_MARK = 'METADATA_VALUE_7741'
PRIVATE_URL_MARKER = 'PRIVATE_URL_TAIL'
NO_HANDLER_MARK = 'NO_HANDLER_BODY_MARK_9931'
MIGRATION_BODY_MARK = 'RAW_BODY_MIGRATION_MARK'
EMIT_BODY_MARK = 'RAW_BODY_EMIT_MARK'
ONCE_BODY_MARK = 'RAW_BODY_MARK_ONCE'


def _boom(secret):
    """在隐藏正文中放入局部变量、链接链与 ANSI 的高频异常点。"""
    local_value = LOCAL_MARK + ANSI_RED
    try:
        raise KeyError('INNER ' + CHAIN_MARK + ANSI_RED)
    except KeyError as exc:
        raise ValueError(
            'BODY ' + secret + ' ' + local_value + ANSI_RED) from exc


def _worker_boom():
    """真实线程入口：把父线程与工作线程秘密放进异常正文。"""
    _boom(WORKER_MARK + ' 与 ' + PARENT_MARK)


def _scoped_worker():
    """先登记工作线程秘密，随后在作用域退出后向外抛出。"""
    with log_safety.safety_scope((WORKER_MARK,)):
        _worker_boom()


def _line_of(function, needle):
    """定位函数源码中包含标记的行号。"""
    lines, start = inspect.getsourcelines(function)
    for offset, line in enumerate(lines):
        if needle in line:
            return start + offset
    raise AssertionError('未找到源码标记: ' + needle)


def _build_raiser(exc_type, filename, co_name):
    """用自定义异常类型、编译文件名与替换后的函数名构造真实线程入口。"""
    source = 'def placeholder():\n    raise RAISE_TYPE("合成异常正文")\n'
    module_code = compile(source, filename, 'exec')
    replaced = module_code.replace(co_consts=tuple(
        const.replace(co_name=co_name)
        if isinstance(const, types.CodeType) else const
        for const in module_code.co_consts))
    namespace = {'RAISE_TYPE': exc_type}
    exec(replaced, namespace)
    return namespace['placeholder']


def _read_log(handler):
    """刷新并读取真实文件处理器当前日志内容。"""
    handler.flush()
    return Path(handler.baseFilename).read_text(encoding='utf-8')


def _run_script(script, *arguments, timeout=90):
    """在隔离子进程中运行脚本，仓库根加入模块搜索路径。"""
    environment = dict(os.environ)
    environment['PYTHONPATH'] = str(REPO_ROOT)
    environment['PYTHONIOENCODING'] = 'utf-8'
    return subprocess.run(
        [sys.executable, '-c', textwrap.dedent(script), *map(str, arguments)],
        cwd=str(REPO_ROOT),
        env=environment,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
    )


@pytest.fixture
def logging_state():
    """完整保存并恢复线程 hook、根处理器、级别与启动文件处理器。"""
    root = logging.getLogger()
    saved = {
        'handlers': root.handlers[:],
        'level': root.level,
        'hook': threading.excepthook,
        'file_handler': logger_mod._FILE_HANDLER,
    }
    try:
        yield saved
    finally:
        threading.excepthook = saved['hook']
        for handler in root.handlers[:]:
            if handler not in saved['handlers']:
                root.removeHandler(handler)
                handler.close()
        root.handlers = saved['handlers']
        root.setLevel(saved['level'])
        logger_mod._FILE_HANDLER = saved['file_handler']


@pytest.fixture
def thread_log(logging_state, tmp_path):
    """在根 logger 上挂真实文件处理器，供线程 hook 落盘。"""
    handler = logger_mod._create_file_handler(str(tmp_path), 'thread')
    assert handler is not None
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    return handler


@pytest.mark.parametrize('scope', ['parent', 'worker'])
def test_thread_exception_records_metadata_only(scope, thread_log, capsys):
    """真实线程异常只落受限元数据，正文、局部变量、链与秘密都不落盘。"""
    logger_mod._install_thread_exception_logging()
    if scope == 'parent':
        with log_safety.safety_scope((PARENT_MARK,)):
            thread = threading.Thread(target=_worker_boom, name=PARENT_MARK)
            thread.start()
            thread.join(10)
    else:
        thread = threading.Thread(target=_scoped_worker, name=WORKER_MARK)
        thread.start()
        thread.join(10)
    assert not thread.is_alive()

    text = _read_log(thread_log)
    stderr = capsys.readouterr().err
    base = os.path.basename(__file__)
    boom = f'{base}:{_line_of(_boom, "raise ValueError(")}:_boom'
    worker = f'{base}:{_line_of(_worker_boom, "_boom(WORKER_MARK")}:_worker_boom'
    if scope == 'parent':
        expected = f'{worker} -> {boom}'
    else:
        scoped = (f'{base}:{_line_of(_scoped_worker, "_worker_boom()")}'
                  ':_scoped_worker')
        expected = f'{scoped} -> {worker} -> {boom}'

    assert expected in text
    assert thread.ident is not None
    assert f'thread={thread.ident}' in text
    assert 'type=ValueError' in text
    assert '异常正文已省略' in text
    assert text.count('线程异常') == 1
    for secret in (PARENT_MARK, WORKER_MARK, LOCAL_MARK, CHAIN_MARK):
        assert secret not in text
        assert secret not in stderr
    assert ANSI_RED not in text
    assert 'Traceback' not in stderr


def test_static_exception_frame_is_locatable(thread_log):
    """普通 ValueError 的文件名、行号与函数名仍可定位，完整路径不外泄。"""
    logger_mod._install_thread_exception_logging()
    thread = threading.Thread(target=_worker_boom)
    thread.start()
    thread.join(10)
    assert not thread.is_alive()

    text = _read_log(thread_log)
    base = os.path.basename(__file__)
    expected = (
        f'{base}:{_line_of(_worker_boom, "_boom(WORKER_MARK")}:_worker_boom'
        f' -> {base}:{_line_of(_boom, "raise ValueError(")}:_boom'
    )
    assert expected in text
    assert 'type=ValueError' in text
    assert str(Path(__file__).parent) not in text


def test_install_thread_hook_is_idempotent(thread_log):
    """重复安装只保留同一 hook 对象。"""
    logger_mod._install_thread_exception_logging()
    installed = threading.excepthook
    logger_mod._install_thread_exception_logging()
    assert installed is logger_mod._log_thread_exception
    assert threading.excepthook is installed


def test_setup_default_logging_installs_hook_before_early_return(
        logging_state, monkeypatch, tmp_path):
    """已有根处理器提前返回时仍安装线程 hook。"""
    root = logging.getLogger()
    existing = logging.NullHandler()
    root.handlers = [existing]
    monkeypatch.setattr(
        logger_mod, '_create_file_handler',
        lambda *args, **kwargs: pytest.fail('已配置日志时不应再创建文件处理器'))
    monkeypatch.setattr(logger_mod, '_resolve_log_dir', lambda value: str(tmp_path))

    logger_mod.setup_default_logging()

    assert threading.excepthook is logger_mod._log_thread_exception
    assert root.handlers == [existing]


def test_setup_default_logging_is_idempotent(
        logging_state, monkeypatch, tmp_path):
    """重复初始化只保留一套控制台与文件处理器。"""
    monkeypatch.setattr(logger_mod, '_resolve_log_dir', lambda value: str(tmp_path))
    root = logging.getLogger()
    root.handlers = []

    logger_mod.setup_default_logging()
    first = logger_mod._FILE_HANDLER
    assert first is not None
    assert len(root.handlers) == 2

    logger_mod.setup_default_logging()

    assert logger_mod._FILE_HANDLER is first
    assert len(root.handlers) == 2
    assert threading.excepthook is logger_mod._log_thread_exception


def test_system_exit_is_ignored(thread_log, capsys):
    """SystemExit 由线程 hook 明确忽略，不写日志也不输出原始堆栈。"""
    logger_mod._install_thread_exception_logging()

    def target():
        raise SystemExit('SYSTEM_EXIT_BODY_5510')

    thread = threading.Thread(target=target, name='exit-thread')
    thread.start()
    thread.join(10)
    assert not thread.is_alive()
    assert _read_log(thread_log) == ''
    stderr = capsys.readouterr().err
    assert 'Traceback' not in stderr
    assert 'SYSTEM_EXIT_BODY_5510' not in stderr


def test_unavailable_file_handler_does_not_recurse(
        logging_state, monkeypatch, tmp_path, capsys):
    """文件处理器不可用时 hook 保持稳定，不递归、不泄露正文。"""
    monkeypatch.setattr(
        logger_mod, '_create_file_handler', lambda *args, **kwargs: None)
    monkeypatch.setattr(logger_mod, '_resolve_log_dir', lambda value: str(tmp_path))
    root = logging.getLogger()
    root.handlers = []
    logger_mod._FILE_HANDLER = None

    logger_mod.setup_default_logging()
    assert logger_mod._FILE_HANDLER is None

    def target():
        raise ValueError(NO_HANDLER_MARK)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(10)

    assert not thread.is_alive()
    assert threading.excepthook is logger_mod._log_thread_exception
    stderr = capsys.readouterr().err
    assert 'Traceback' not in stderr
    assert 'RecursionError' not in stderr
    assert NO_HANDLER_MARK not in stderr


def _one_record_script():
    return r"""
import logging
import sys
import threading
from pathlib import Path

from modules import logger as logger_mod

log_dir = Path(sys.argv[1])
handler = logger_mod._create_file_handler(str(log_dir), 'once')
assert handler is not None, '文件处理器创建失败'
root = logging.getLogger()
root.handlers = [handler]
root.setLevel(logging.DEBUG)
logger_mod._install_thread_exception_logging()


def worker():
    raise ValueError('RAW_BODY_MARK_ONCE \x1b[31mANSI\x1b[0m')


thread = threading.Thread(target=worker)
thread.start()
thread.join(10)
assert not thread.is_alive(), '线程未结束'
handler.flush()
text = Path(handler.baseFilename).read_text(encoding='utf-8')
assert text.count('线程异常') == 1, text
assert 'RAW_BODY_MARK_ONCE' not in text, text
assert '\x1b' not in text, text
assert 'ValueError' in text, text
print('ONE_RECORD_OK')
"""


def test_single_exception_writes_one_record_in_subprocess(tmp_path):
    """隔离子进程确认一次异常只写一条记录，stderr 无原始堆栈。"""
    result = _run_script(_one_record_script(), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'ONE_RECORD_OK' in result.stdout
    assert ONCE_BODY_MARK not in result.stderr
    assert 'Traceback' not in result.stderr

    logs = list(tmp_path.glob('*.log'))
    assert len(logs) == 1
    assert logs[0].read_text(encoding='utf-8').count('线程异常') == 1


def _migration_block_script():
    return r"""
import io
import logging
import os
import sys
import threading
from pathlib import Path

from modules import logger as logger_mod

log_dir = Path(sys.argv[1])
mode = sys.argv[2]

root = logging.getLogger()
handler = logger_mod._create_file_handler(str(log_dir), '2RPM')
assert handler is not None
source_path = Path(handler.baseFilename)
console = logging.StreamHandler(io.StringIO())
root.handlers = [handler, console]
root.setLevel(logging.DEBUG)
logger_mod._FILE_HANDLER = handler
logger_mod._install_thread_exception_logging()

migration_entered = threading.Event()
release_migration = threading.Event()
hook_attempted = threading.Event()
error_entered = threading.Event()


class LockProxy:
    def __init__(self, real):
        self._real = real
        self.watch_ident = None

    def acquire(self, *args, **kwargs):
        return self._real.acquire(*args, **kwargs)

    def release(self):
        return self._real.release()

    def __enter__(self):
        if threading.get_ident() == self.watch_ident:
            hook_attempted.set()
        self._real.acquire()
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self._real.release()
        return False


logger_mod._LOGGING_LOCK = LockProxy(threading.RLock())

real_error = logger_mod.LOGGER.error


def observed_error(message, *args, **kwargs):
    error_entered.set()
    return real_error(message, *args, **kwargs)


logger_mod.LOGGER.error = observed_error

real_replace = os.replace


def blocking_replace(source, target):
    migration_entered.set()
    assert release_migration.wait(20), '迁移屏障等待超时'
    if mode == 'failure':
        raise OSError('合成替换失败')
    real_replace(source, target)


os.replace = blocking_replace


def migrate():
    logger_mod.setup_logging(
        {'log': {'log_directory': str(log_dir),
                 'max_log_files': 15, 'retention_days': 3}},
        'other.yaml')


def hook_worker():
    logger_mod._LOGGING_LOCK.watch_ident = threading.get_ident()
    raise ValueError('RAW_BODY_MIGRATION_MARK')


migration_thread = None
hook_thread = None
original_hook = threading.excepthook
try:
    migration_thread = threading.Thread(target=migrate, name='migration')
    migration_thread.start()
    assert migration_entered.wait(20), '迁移未进入窗口'

    hook_thread = threading.Thread(target=hook_worker, name='hook-worker')
    hook_thread.start()
    assert hook_attempted.wait(20), 'hook 未尝试获取共享锁'
    assert not error_entered.wait(0.2), '迁移窗口内 hook 不应写日志'

    release_migration.set()
    migration_thread.join(30)
    hook_thread.join(30)
    assert not migration_thread.is_alive(), '迁移线程未结束'
    assert not hook_thread.is_alive(), '异常线程未结束'

    texts = {}
    for path in log_dir.rglob('*.log'):
        texts[path] = path.read_text(encoding='utf-8')
    combined = ''.join(texts.values())
    assert combined.count('线程异常') == 1, combined
    assert 'RAW_BODY_MIGRATION_MARK' not in combined

    if mode == 'failure':
        assert source_path.exists(), '回退时应保留原文件'
        assert texts[source_path].count('线程异常') == 1
    else:
        assert not source_path.exists(), '成功迁移后原文件不应保留'
        targets = [path for path in texts if path.name.startswith('other_')]
        assert len(targets) == 1, texts
        assert texts[targets[0]].count('线程异常') == 1
    print('MIGRATION_OK')
finally:
    release_migration.set()
    for thread in (migration_thread, hook_thread):
        if thread is not None:
            thread.join(30)
    threading.excepthook = original_hook
    logger_mod._FILE_HANDLER = None
    root.handlers = []
"""


@pytest.mark.parametrize('mode', ['success', 'failure'])
def test_hook_waits_for_log_migration(mode, tmp_path):
    """迁移窗口内 hook 阻塞；释放后事件在目标或原文件恰好出现一次。"""
    result = _run_script(_migration_block_script(), tmp_path, mode)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'MIGRATION_OK' in result.stdout
    assert MIGRATION_BODY_MARK not in result.stderr
    assert 'Traceback' not in result.stderr


def _emit_block_script():
    return r"""
import io
import logging
import sys
import threading
from pathlib import Path

from modules import logger as logger_mod

log_dir = Path(sys.argv[1])

root = logging.getLogger()
handler = logger_mod._create_file_handler(str(log_dir), '2RPM')
assert handler is not None
source_path = Path(handler.baseFilename)
console = logging.StreamHandler(io.StringIO())
root.handlers = [handler, console]
root.setLevel(logging.DEBUG)
logger_mod._FILE_HANDLER = handler
logger_mod._install_thread_exception_logging()

emit_entered = threading.Event()
release_emit = threading.Event()
hook_attempted = threading.Event()
migration_attempted = threading.Event()
order = []


class LockProxy:
    def __init__(self, real):
        self._real = real
        self.hook_ident = None
        self.migration_ident = None

    def acquire(self, *args, **kwargs):
        return self._real.acquire(*args, **kwargs)

    def release(self):
        return self._real.release()

    def __enter__(self):
        ident = threading.get_ident()
        if ident == self.hook_ident:
            hook_attempted.set()
        elif ident == self.migration_ident:
            migration_attempted.set()
        self._real.acquire()
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self._real.release()
        return False


logger_mod._LOGGING_LOCK = LockProxy(threading.RLock())

real_emit = handler.emit


def blocking_emit(record):
    emit_entered.set()
    assert release_emit.wait(20), '发射屏障等待超时'
    real_emit(record)
    order.append('emit')


handler.emit = blocking_emit


def migrate():
    logger_mod._LOGGING_LOCK.migration_ident = threading.get_ident()
    logger_mod.setup_logging(
        {'log': {'log_directory': str(log_dir),
                 'max_log_files': 15, 'retention_days': 3}},
        'other.yaml')
    order.append('migrate')


def hook_worker():
    logger_mod._LOGGING_LOCK.hook_ident = threading.get_ident()
    raise ValueError('RAW_BODY_EMIT_MARK')


hook_thread = None
migration_thread = None
original_hook = threading.excepthook
try:
    hook_thread = threading.Thread(target=hook_worker, name='hook-worker')
    hook_thread.start()
    assert emit_entered.wait(20), 'hook 未进入文件发射'
    assert hook_attempted.wait(5), 'hook 未获取共享锁'

    migration_thread = threading.Thread(target=migrate, name='migration')
    migration_thread.start()
    assert migration_attempted.wait(20), '迁移未尝试获取共享锁'
    assert logger_mod._FILE_HANDLER is handler, '发射期间不应替换处理器'
    assert handler in root.handlers, '发射期间不应摘除旧处理器'
    assert (handler.stream is not None and not handler.stream.closed), \
        '发射期间不应关闭旧流'

    release_emit.set()
    hook_thread.join(30)
    migration_thread.join(30)
    assert not hook_thread.is_alive(), '异常线程未结束'
    assert not migration_thread.is_alive(), '迁移线程未结束'
    assert order == ['emit', 'migrate'], order

    texts = {}
    for path in log_dir.rglob('*.log'):
        texts[path] = path.read_text(encoding='utf-8')
    combined = ''.join(texts.values())
    assert combined.count('线程异常') == 1, combined
    assert 'RAW_BODY_EMIT_MARK' not in combined
    assert not source_path.exists(), '迁移后原文件不应保留'
    targets = [path for path in texts if path.name.startswith('other_')]
    assert len(targets) == 1, texts
    assert texts[targets[0]].count('线程异常') == 1
    print('EMIT_ORDER_OK')
finally:
    release_emit.set()
    for thread in (hook_thread, migration_thread):
        if thread is not None:
            thread.join(30)
    threading.excepthook = original_hook
    logger_mod._FILE_HANDLER = None
    root.handlers = []
"""


def test_migration_waits_for_hook_emit(tmp_path):
    """hook 发射期间迁移阻塞，发射完成后才移动文件且只留一条记录。"""
    result = _run_script(_emit_block_script(), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'EMIT_ORDER_OK' in result.stdout
    assert EMIT_BODY_MARK not in result.stderr
    assert 'Traceback' not in result.stderr


def _reentrant_lock_script():
    return r"""
import logging
import sys
from pathlib import Path

from modules import logger as logger_mod

log_dir = Path(sys.argv[1])
logger_mod._resolve_log_dir = lambda value: str(log_dir)
root = logging.getLogger()
root.handlers = []
with logger_mod._LOGGING_LOCK:
    logger_mod.setup_default_logging()
handler = logger_mod._FILE_HANDLER
assert handler is not None, '默认文件处理器创建失败'
assert handler in root.handlers
handler.flush()
print('REENTRANT_OK')
"""


def test_shared_lock_is_reentrant_for_setup(tmp_path):
    """同线程预持共享锁后调用默认初始化不会自锁。"""
    result = _run_script(_reentrant_lock_script(), tmp_path, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'REENTRANT_OK' in result.stdout


def test_dynamic_metadata_boundary(thread_log, capsys):
    """自定义异常类型、文件名与函数名中的动态文本先脱敏再裁剪。"""
    payload = 'token=' + METADATA_MARK + ' ' + ANSI_RED + '\r\n\t'
    exc_type = type('Injected ' + payload, (ValueError,), {})
    raiser = _build_raiser(
        exc_type,
        'https://example.invalid/' + PRIVATE_URL_MARKER,
        'call ' + payload,
    )
    logger_mod._install_thread_exception_logging()
    thread_name = 'THREAD_NAME_' + METADATA_MARK
    thread = threading.Thread(target=raiser, name=thread_name)
    thread.start()
    thread.join(10)
    assert not thread.is_alive()

    text = _read_log(thread_log)
    stderr = capsys.readouterr().err

    assert text.strip().count('\n') == 0
    assert '线程异常（异常正文已省略）' in text
    assert '[链接已隐藏]' in text
    assert ':2:' in text
    assert PRIVATE_URL_MARKER not in text
    assert PRIVATE_URL_MARKER not in stderr
    assert METADATA_MARK not in text
    assert METADATA_MARK not in stderr
    assert thread_name not in text
    assert thread_name not in stderr
    assert '\x1b' not in text
    assert '\x1b' not in stderr
    assert '\r' not in text
    assert '\t' not in text


@pytest.mark.parametrize('field', ['type', 'filename', 'function'])
@pytest.mark.parametrize('registered_in', ['parent', 'worker'])
def test_unlisted_bare_marker_survives_registration(
        field, registered_in, thread_log):
    """未被识别的裸标记可保留：这是安全边界，不是全量脱敏承诺。

    登记发生在父线程或工作作用域退出后，hook 都无法取得秘密快照，
    因此不含敏感模式的普通标记会留在动态元数据中。
    """
    marker = f'BARE_MARKER_{field.upper()}_6182'
    exc_type = ValueError
    filename = __file__
    co_name = 'bare_marker_raiser'
    if field == 'type':
        exc_type = type('PlainError' + marker, (ValueError,), {})
    elif field == 'filename':
        filename = 'C:\\合成目录\\' + marker + '.py'
    else:
        co_name = 'func_' + marker
    raiser = _build_raiser(exc_type, filename, co_name)
    logger_mod._install_thread_exception_logging()

    if registered_in == 'parent':
        def target():
            raiser()

        with log_safety.safety_scope((marker,)):
            thread = threading.Thread(target=target)
            thread.start()
            thread.join(10)
    else:
        def target():
            with log_safety.safety_scope((marker,)):
                raiser()

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(10)

    assert not thread.is_alive()
    text = _read_log(thread_log)
    assert '线程异常' in text
    assert marker in text
