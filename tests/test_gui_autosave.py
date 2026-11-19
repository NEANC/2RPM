#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""保存协调器和文件事务回归测试。"""

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from modules.config_document import new_document, read_document, save_document
from modules.gui.controller import Controller
from modules.gui.model import EditorModel
from modules.gui.save_coordinator import SaveCoordinator


class Clock:
    """按虚拟毫秒推进的可控调度器。"""

    def __init__(self):
        """建立初始时钟。"""
        self.now = 0
        self.events = {}
        self.next_id = 0

    def schedule(self, delay, callback):
        """记录待运行回调。"""
        self.next_id += 1
        self.events[self.next_id] = (self.now + delay, callback)
        return self.next_id

    def cancel(self, handle):
        """撤销尚未触发的回调。"""
        self.events.pop(handle, None)

    def advance(self, milliseconds):
        """依时序运行截止时间内的回调。"""
        end = self.now + milliseconds
        while self.events and min(at for at, _ in self.events.values()) <= end:
            handle = min(self.events, key=lambda item: self.events[item][0])
            self.now, callback = self.events.pop(handle)
            callback()
        self.now = end


def setup(tmp_path, *, persisted=True):
    """建立真实文档、控制器和虚拟时钟。"""
    document = (save_document(new_document(), tmp_path / 'old.yaml', expected_fingerprint=None)
                if persisted else new_document())
    controller = Controller(EditorModel(document), confirm=lambda *_: 'confirm',
                            choose_path=lambda: tmp_path / 'new.yaml', cli_command=(),
                            runner=Mock(), program_dir=tmp_path)
    clock = Clock()
    coordinator = SaveCoordinator(controller, clock.schedule, clock.cancel)
    return controller, coordinator, clock


def test_debounce_coalesces_writes(tmp_path, monkeypatch):
    """连续编辑只在静默 500 毫秒后写一次。"""
    controller, coordinator, clock = setup(tmp_path)
    save = Mock(wraps=controller.save)
    monkeypatch.setattr(controller, 'save', save)
    controller.model.set(('monitor', 'target'), 'first.exe')
    coordinator.edited()
    clock.advance(400)
    controller.model.set(('monitor', 'target'), 'second.exe')
    coordinator.edited()
    clock.advance(499)
    assert not save.called
    clock.advance(1)
    assert save.call_count == 1
    assert read_document(controller.model.document.path).tree['monitor']['target'] == 'second.exe'


def test_partial_number_keeps_raw_draft(tmp_path):
    """无法转换的数字原文不落盘也不被刷新清除。"""
    controller, coordinator, clock = setup(tmp_path)
    path = ('monitor', 'common', 'timeout_threshold')
    before = controller.model.document.path.read_bytes()
    controller.model.set_input(path, '-', kind='int')
    coordinator.edited()
    clock.advance(500)
    assert controller.model.get_input(path) == '-'
    assert controller.model.dirty
    assert not coordinator.flush()
    assert controller.model.document.path.read_bytes() == before


def test_restored_number_restarts_debounce_with_other_dirty_field(tmp_path, monkeypatch):
    """数字恢复原值后，其他字段的待保存草稿从恢复输入重新计时。"""
    controller, coordinator, clock = setup(tmp_path)
    controller.model.on_edit = coordinator.edited
    save = Mock(wraps=controller.save)
    monkeypatch.setattr(controller, 'save', save)
    path = ('monitor', 'common', 'timeout_threshold')
    original = controller.model.get(path)
    controller.model.set(('monitor', 'target'), 'draft.exe')
    controller.model.set_input(path, '-', kind='int')
    clock.advance(400)
    controller.model.set_input(path, str(original), kind='int')
    assert controller.model.dirty
    assert not controller.model.input_errors
    clock.advance(100)
    save.assert_not_called()
    clock.advance(399)
    save.assert_not_called()
    clock.advance(1)
    save.assert_called_once_with()
    assert read_document(controller.model.document.path).tree['monitor']['target'] == 'draft.exe'


def test_saved_number_restores_edit_text(tmp_path):
    """成功保存数值后，输入框仍保留用户输入的原文。"""
    controller, coordinator, _ = setup(tmp_path)
    path = ('monitor', 'common', 'timeout_threshold')
    controller.model.set_input(path, '007', kind='int')
    assert coordinator.flush()
    assert read_document(controller.model.document.path).tree['monitor']['common']['timeout_threshold'] == 7
    assert controller.model.get_input(path) == '007'
    assert not controller.model.dirty


def test_old_generation_callback_cannot_save(tmp_path):
    """旧文件的延迟回调即使被调用也不能写入新文件。"""
    controller, coordinator, clock = setup(tmp_path)
    controller.model.set(('monitor', 'target'), 'stale.exe')
    controller.confirm = lambda *_: 'discard'
    coordinator.edited()
    stale = next(callback for _, callback in clock.events.values())
    assert controller.open(tmp_path / 'other.yaml') is False
    other = save_document(new_document(), tmp_path / 'other.yaml', expected_fingerprint=None)
    assert controller.open(other.path)
    stale()
    assert controller.model.document.path == other.path
    assert read_document(other.path).tree['monitor']['target'] != 'stale.exe'


def test_save_as_pauses_original_writes(tmp_path):
    """选择器停留超过防抖周期不会写回原路径。"""
    controller, coordinator, clock = setup(tmp_path)
    old = controller.model.document.path
    before = old.read_bytes()
    controller.model.set(('monitor', 'target'), 'draft.exe')
    coordinator.edited()

    def picker():
        """在文件选择器内推进事件循环。"""
        clock.advance(1200)
        assert old.read_bytes() == before
        return None

    assert not controller.save_as(picker)
    assert controller.model.dirty
    assert old.read_bytes() == before


@pytest.mark.parametrize('action', ['new', 'open', 'save_as'])
def test_failed_file_transaction_ignores_cancelled_save_callback(tmp_path, monkeypatch,
                                                                 action):
    """真实文件事务失败后迟到的旧回调不能提前写入草稿。"""
    controller, coordinator, clock = setup(tmp_path)
    original = controller.model.document.path
    original_bytes = original.read_bytes()
    controller.model.set(('monitor', 'target'), 'draft.exe')
    coordinator.edited()
    stale = clock.events[coordinator.pending][1]
    generation = controller.model.document_generation
    destination = tmp_path / 'selected.yaml'
    destination.write_bytes(b'old-target')
    destination_bytes = destination.read_bytes()
    controller.confirm = lambda kind, _: 'discard' if kind == 'unsaved' else 'confirm'

    if action == 'open':
        destination.write_bytes(b'\xff')
        destination_bytes = destination.read_bytes()
    else:
        original_replace = os.replace
        replace_calls = []

        def fail_replace(source, target):
            """在真实临时文件写完后拒绝目标替换。"""
            assert Path(source).is_file()
            if Path(target) == destination:
                replace_calls.append((Path(source), Path(target)))
                raise OSError('write failed')
            return original_replace(source, target)

        monkeypatch.setattr('modules.config_document.os.replace', fail_replace)

    def picker():
        """在选择器停留时推进防抖时间并返回真实目标。"""
        clock.advance(600)
        assert original.read_bytes() == original_bytes
        return destination

    assert not getattr(controller, action)(picker)
    if action != 'open':
        assert len(replace_calls) == 1
        assert replace_calls[0][1] == destination
    assert original.read_bytes() == original_bytes
    assert destination.read_bytes() == destination_bytes
    assert controller.model.document.path == original
    assert controller.model.document_generation == generation
    assert controller.model.get(('monitor', 'target')) == 'draft.exe'
    assert controller.model.dirty
    assert coordinator.pending is not None
    resumed = coordinator.pending
    stale()
    assert original.read_bytes() == original_bytes
    assert destination.read_bytes() == destination_bytes
    assert coordinator.pending == resumed
    clock.advance(500)
    assert read_document(original).tree['monitor']['target'] == 'draft.exe'


@pytest.mark.parametrize('action', ['new', 'save_as'])
def test_conflicting_file_transaction_ignores_cancelled_save_callback(tmp_path, action):
    """目标冲突与迟到回调均不能覆盖真实磁盘版本。"""
    controller, coordinator, clock = setup(tmp_path)
    original = controller.model.document.path
    original_bytes = original.read_bytes()
    controller.model.set(('monitor', 'target'), 'draft.exe')
    coordinator.edited()
    stale = clock.events[coordinator.pending][1]
    generation = controller.model.document_generation
    destination = tmp_path / 'selected.yaml'
    destination.write_bytes(b'first-version')

    def confirm(kind, detail):
        """确认覆盖后模拟另一进程修改目标字节。"""
        if kind == 'overwrite':
            destination.write_bytes(b'new-external-version')
        return 'discard' if kind == 'unsaved' else 'confirm'

    controller.confirm = confirm

    def picker():
        """目标选择不触发原路径的延迟保存。"""
        clock.advance(600)
        assert original.read_bytes() == original_bytes
        return destination

    assert not getattr(controller, action)(picker)
    assert destination.read_bytes() == b'new-external-version'
    assert original.read_bytes() == original_bytes
    assert controller.model.document.path == original
    assert controller.model.document_generation == generation
    assert controller.model.get(('monitor', 'target')) == 'draft.exe'
    assert controller.model.dirty
    assert coordinator.pending is not None
    resumed = coordinator.pending
    stale()
    assert original.read_bytes() == original_bytes
    assert destination.read_bytes() == b'new-external-version'
    assert coordinator.pending == resumed
    clock.advance(500)
    assert read_document(original).tree['monitor']['target'] == 'draft.exe'


def test_external_change_during_confirmation_blocks_write(tmp_path):
    """覆盖确认期间外部改动原路径时必须保护其字节。"""
    controller, _, _ = setup(tmp_path)
    path = controller.model.document.path
    controller.model.set(('monitor', 'target'), 'draft.exe')

    def confirm(kind, detail):
        """模拟模态确认窗口期间外部写入。"""
        if kind == 'relocate':
            path.write_bytes(b'external')
        return 'confirm'

    controller.confirm = confirm
    destination = tmp_path / 'elsewhere' / 'new.yaml'
    destination.parent.mkdir()
    assert controller.save(destination, as_new=True)
    assert path.read_bytes() == b'external'
    assert controller.model.document.path == destination


def test_clean_external_edit_reloads(tmp_path):
    """干净草稿遇到有效磁盘变更自动载入。"""
    controller, coordinator, _ = setup(tmp_path)
    path = controller.model.document.path
    external = read_document(path)
    external.tree['monitor']['target'] = 'external.exe'
    save_document(external, path, expected_fingerprint=external.fingerprint)
    coordinator.check_external()
    assert controller.model.get(('monitor', 'target')) == 'external.exe'
    assert not controller.conflicted


@pytest.mark.parametrize('action', ['new', 'open', 'save_as'])
@pytest.mark.parametrize('dirty', [False, True])
def test_external_update_in_same_path_picker_preserves_disk(tmp_path, action, dirty):
    """选择器内更新当前文件时，旧草稿不能覆盖新版本。"""
    controller, _, _ = setup(tmp_path)
    path = controller.model.document.path
    if dirty:
        controller.model.set(('monitor', 'target'), 'draft.exe')
    controller.confirm = lambda kind, _: 'discard' if kind == 'unsaved' else 'confirm'

    def picker():
        """在模态选择器停留期间由外部进程更新真实 YAML。"""
        external = read_document(path)
        external.tree['monitor']['target'] = 'external.exe'
        save_document(external, path, expected_fingerprint=external.fingerprint)
        return path

    result = getattr(controller, action)(picker)

    assert result is (action == 'open' or action == 'save_as' and not dirty)
    assert read_document(path).tree['monitor']['target'] == 'external.exe'
    if dirty and action != 'open':
        assert controller.model.get(('monitor', 'target')) == 'draft.exe'
        assert controller.model.dirty
        assert controller.conflicted
    else:
        assert controller.model.get(('monitor', 'target')) == 'external.exe'
        assert not controller.conflicted


@pytest.mark.parametrize('action', ['new', 'open', 'save_as'])
@pytest.mark.parametrize('damage', ['invalid', 'missing'])
def test_unavailable_external_version_in_picker_blocks_old_write(tmp_path, action, damage):
    """选择器内 YAML 损坏或文件暂缺时保留旧视图并阻断写回。"""
    controller, _, _ = setup(tmp_path)
    path = controller.model.document.path
    before = controller.model.snapshot()
    controller.confirm = lambda *_: 'confirm'

    def picker():
        """在模态选择器内制造真实文件不可读取状态。"""
        if damage == 'invalid':
            path.write_bytes(b'\xff')
        else:
            path.unlink()
        return path

    assert not getattr(controller, action)(picker)
    assert controller.model.snapshot() == before
    assert controller.model.document.path == path
    assert controller.conflicted
    assert not controller.save()
    if damage == 'invalid':
        assert path.read_bytes() == b'\xff'
    else:
        assert not path.exists()


def test_invalid_external_yaml_preserves_view(tmp_path):
    """坏 YAML 不替换最后有效的视图，并禁止写回。"""
    controller, coordinator, _ = setup(tmp_path)
    path = controller.model.document.path
    before = controller.model.snapshot()
    path.write_bytes(b'\xff')
    coordinator.check_external()
    assert controller.model.snapshot() == before
    assert controller.conflicted
    assert not coordinator.flush()
    assert path.read_bytes() == b'\xff'


def test_own_save_is_not_external_change(tmp_path):
    """自身保存更新指纹基准。"""
    controller, coordinator, _ = setup(tmp_path)
    controller.model.set(('monitor', 'target'), 'own.exe')
    assert coordinator.flush()
    coordinator.check_external()
    assert not controller.conflicted


def test_save_as_success_changes_save_destination(tmp_path):
    """成功另存后新的自动保存目标不是原文件。"""
    controller, coordinator, clock = setup(tmp_path)
    original = controller.model.document.path
    before = original.read_bytes()
    controller.model.set(('monitor', 'target'), 'draft.exe')
    assert controller.save_as(lambda: tmp_path / 'new.yaml')
    assert original.read_bytes() == before
    assert controller.model.document.path == tmp_path / 'new.yaml'
    controller.model.set(('monitor', 'target'), 'next.exe')
    coordinator.edited()
    clock.advance(500)
    assert read_document(tmp_path / 'new.yaml').tree['monitor']['target'] == 'next.exe'
    assert original.read_bytes() == before


@pytest.mark.parametrize('action', ['open', 'new'])
def test_discard_then_picker_cancel_keeps_draft(tmp_path, action):
    """选择放弃但选择器取消时保留原文与代次。"""
    controller, _, _ = setup(tmp_path)
    controller.model.set_input(('monitor', 'common', 'timeout_threshold'), '-', kind='int')
    controller.confirm = lambda *_: 'discard'
    generation = controller.model.document_generation
    assert not getattr(controller, action)(lambda: None)
    assert controller.model.get_input(('monitor', 'common', 'timeout_threshold')) == '-'
    assert controller.model.document_generation == generation


@pytest.mark.parametrize('action', ['new', 'open', 'save_as'])
def test_picker_cancel_invalidates_old_callback_and_reschedules_save(tmp_path, action):
    """文件选择取消后旧回调失效，新回调满 500 毫秒才保存。"""
    controller, coordinator, clock = setup(tmp_path)
    original = controller.model.document.path
    original_bytes = original.read_bytes()
    controller.model.set(('monitor', 'target'), 'draft.exe')
    coordinator.edited()
    stale = clock.events[coordinator.pending][1]
    generation = controller.model.document_generation
    controller.confirm = lambda kind, _: 'discard' if kind == 'unsaved' else 'confirm'

    def picker():
        """模拟选择器运行超过旧防抖期限后取消。"""
        clock.advance(600)
        assert original.read_bytes() == original_bytes
        return None

    assert not getattr(controller, action)(picker)
    assert controller.model.document_generation == generation
    assert controller.model.get(('monitor', 'target')) == 'draft.exe'
    assert controller.model.dirty
    assert coordinator.pending is not None
    resumed = coordinator.pending
    stale()
    assert coordinator.pending == resumed
    assert original.read_bytes() == original_bytes
    clock.advance(499)
    assert original.read_bytes() == original_bytes
    clock.advance(1)
    assert read_document(original).tree['monitor']['target'] == 'draft.exe'
    assert not controller.model.dirty


def test_discard_then_exit_cancel_keeps_draft(tmp_path):
    """放弃草稿之后取消最终退出不终止 worker。"""
    controller, _, _ = setup(tmp_path)
    controller.model.set_input(('monitor', 'common', 'timeout_threshold'), '-', kind='int')
    controller.confirm = lambda kind, _: 'discard' if kind == 'unsaved' else 'cancel'
    assert not controller.close()
    assert controller.model.get_input(('monitor', 'common', 'timeout_threshold')) == '-'
    controller.runner.close.assert_not_called()


@pytest.mark.parametrize('cancel_at', ['unsaved', 'exit_test', 'exit'])
def test_close_cancel_invalidates_old_callback_and_reschedules_save(tmp_path, cancel_at):
    """关闭各层确认取消后旧回调失效，新回调按防抖期限保存。"""
    controller, coordinator, clock = setup(tmp_path)
    original = controller.model.document.path
    original_bytes = original.read_bytes()
    controller.model.set(('monitor', 'target'), 'draft.exe')
    coordinator.edited()
    stale = clock.events[coordinator.pending][1]
    generation = controller.model.document_generation
    if cancel_at == 'exit_test':
        controller.runner.active = True
    seen = []

    def confirm(kind, detail):
        """在指定确认层取消，并在模态对话期间推进时间。"""
        seen.append(kind)
        clock.advance(600)
        assert original.read_bytes() == original_bytes
        return 'cancel' if kind == cancel_at else 'discard' if kind == 'unsaved' else 'confirm'

    controller.confirm = confirm
    assert not controller.close()
    assert cancel_at in seen
    assert controller.model.document_generation == generation
    assert controller.model.get(('monitor', 'target')) == 'draft.exe'
    assert controller.model.dirty
    controller.runner.close.assert_not_called()
    assert coordinator.pending is not None
    resumed = coordinator.pending
    stale()
    assert coordinator.pending == resumed
    assert original.read_bytes() == original_bytes
    clock.advance(499)
    assert original.read_bytes() == original_bytes
    clock.advance(1)
    assert read_document(original).tree['monitor']['target'] == 'draft.exe'
    assert not controller.model.dirty


@pytest.mark.parametrize('action', ['open', 'new'])
def test_discard_then_open_or_create_failure_keeps_draft(tmp_path, action):
    """选取后读取失败或新建写盘失败都不替换草稿。"""
    controller, _, _ = setup(tmp_path)
    controller.model.set_input(('monitor', 'common', 'timeout_threshold'), '-', kind='int')
    controller.confirm = lambda *_: 'discard'
    generation = controller.model.document_generation
    selected = tmp_path / 'missing' / 'missing.yaml'
    assert not getattr(controller, action)(lambda: selected)
    assert controller.model.get_input(('monitor', 'common', 'timeout_threshold')) == '-'
    assert controller.model.document_generation == generation


def test_conflict_save_as_requires_different_path(tmp_path):
    """冲突草稿不能借另存为覆盖原路径。"""
    controller, coordinator, _ = setup(tmp_path)
    path = controller.model.document.path
    controller.model.set(('monitor', 'target'), 'draft.exe')
    path.write_bytes(b'external')
    coordinator.check_external()
    assert not controller.resolve_external('save_as', path)
    assert path.read_bytes() == b'external'


def test_existing_unknown_fields_survive_save(tmp_path):
    """自动保存不删除 YAML 未知扩展字段。"""
    controller, coordinator, _ = setup(tmp_path)
    path = controller.model.document.path
    document = read_document(path)
    document.tree['custom_key'] = {'keep': 'yes'}
    saved = save_document(document, path, expected_fingerprint=document.fingerprint)
    controller.model.replace_document(saved)
    controller.model.set(('monitor', 'target'), 'new.exe')
    assert coordinator.flush()
    assert read_document(controller.model.document.path).tree['custom_key']['keep'] == 'yes'


def test_failed_flush_can_return_home_not_launch(tmp_path, monkeypatch):
    """未完成输入允许导航返回主页，但不允许启动。"""
    controller, coordinator, _ = setup(tmp_path)
    controller.model.set_input(('monitor', 'common', 'timeout_threshold'), '-', kind='int')
    launch = Mock()
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)
    assert not coordinator.flush()
    assert controller.start_cli() is None
    launch.assert_not_called()


def test_business_invalid_value_is_saved(tmp_path):
    """业务诊断错误不阻止可表示字段落盘。"""
    controller, coordinator, _ = setup(tmp_path)
    controller.model.set(('monitor', 'mode'), 'invalid-mode')
    assert coordinator.flush()
    assert read_document(controller.model.document.path).tree['monitor']['mode'] == 'invalid-mode'


def test_external_dirty_conflict_stays_frozen_after_later(tmp_path):
    """暂不处理不能因再次轮询而自动重载或者写盘。"""
    controller, coordinator, clock = setup(tmp_path)
    path = controller.model.document.path
    controller.model.set(('monitor', 'target'), 'draft.exe')
    path.write_bytes(b'external')
    coordinator.check_external()
    assert not controller.resolve_external('later')
    clock.advance(2000)
    assert not coordinator.flush()
    assert controller.model.get(('monitor', 'target')) == 'draft.exe'
    assert path.read_bytes() == b'external'


def test_save_as_failure_and_cancel_keep_original(tmp_path):
    """选择器取消或写失败都保持原路径与未保存输入。"""
    controller, coordinator, clock = setup(tmp_path)
    original = controller.model.document.path
    before = original.read_bytes()
    controller.model.set(('monitor', 'target'), 'draft.exe')
    coordinator.edited()
    assert not controller.save_as(lambda: None)
    assert original.read_bytes() == before
    coordinator.begin_transaction()
    clock.advance(600)
    assert original.read_bytes() == before
    coordinator.end_transaction(False)
    assert not controller.save_as(lambda: tmp_path / 'missing' / 'new.yaml')
    assert controller.model.document.path == original
    assert controller.model.get(('monitor', 'target')) == 'draft.exe'


@pytest.mark.parametrize('outer_cancelled', [True, False])
def test_save_as_restores_own_cancel_result_after_transaction_reentry(tmp_path,
                                                                       outer_cancelled):
    """事务结束重入保存后，另存为仍报告其自身的取消或失败。"""
    controller, coordinator, _ = setup(tmp_path)
    controller.model.set(('monitor', 'target'), 'draft.exe')
    original = controller.model.document.path
    before = original.read_bytes()
    entered = False

    def reenter():
        """在结束事务恢复自动保存时执行一次内层保存。"""
        nonlocal entered
        if entered:
            return
        entered = True
        if outer_cancelled:
            assert controller.save() is True
            assert controller.save_cancelled is False
        else:
            assert controller.save_as(lambda: None) is False
            assert controller.save_cancelled is True

    controller.on_edit = reenter
    destination = None if outer_cancelled else tmp_path / 'missing' / 'new.yaml'
    assert controller.save_as(lambda: destination) is False
    assert entered
    assert controller.save_cancelled is outer_cancelled
    assert controller.model.document.path == original
    if outer_cancelled:
        assert original.read_bytes() != before
    else:
        assert original.read_bytes() == before
        assert controller.model.dirty


def test_reload_invalid_conflict_preserves_draft(tmp_path):
    """冲突文件无法解析时重新加载不得丢弃内存草稿。"""
    controller, coordinator, _ = setup(tmp_path)
    path = controller.model.document.path
    controller.model.set(('monitor', 'target'), 'draft.exe')
    path.write_bytes(b'\xff')
    coordinator.check_external()
    assert not controller.resolve_external('reload')
    assert controller.model.get(('monitor', 'target')) == 'draft.exe'
    assert controller.conflicted


def test_autosave_without_path_does_not_open_picker(tmp_path):
    """无路径的新草稿自动保存不弹出系统选择器。"""
    controller, coordinator, clock = setup(tmp_path, persisted=False)
    picker = Mock(return_value=None)
    controller.choose_path = picker
    controller.model.set(('monitor', 'target'), 'draft.exe')
    coordinator.edited()
    clock.advance(500)
    picker.assert_not_called()
    assert controller.model.dirty


def test_unsaved_state_updates_on_user_edit(tmp_path):
    """用户编辑即时通知 UI，而不是等防抖写盘完成。"""
    controller, coordinator, _ = setup(tmp_path)
    changed = Mock()
    controller.on_edit = changed
    controller.model.on_edit = coordinator.edited
    controller.model.set(('monitor', 'target'), 'draft.exe')
    changed.assert_called_once()


def test_external_edit_after_save_as_picker_preserves_original(tmp_path):
    """另存选择器内原文件改变时仍可保存到不同新路径。"""
    controller, coordinator, clock = setup(tmp_path)
    path = controller.model.document.path
    controller.model.set(('monitor', 'target'), 'draft.exe')
    coordinator.edited()

    def picker():
        """在模态选择器内模拟外部编辑。"""
        clock.advance(1200)
        path.write_bytes(b'external')
        return tmp_path / 'new.yaml'

    assert controller.save_as(picker)
    assert controller.model.document.path == tmp_path / 'new.yaml'
    assert path.read_bytes() == b'external'
    assert read_document(tmp_path / 'new.yaml').tree['monitor']['target'] == 'draft.exe'


def test_flush_does_not_erase_new_input_from_modal_confirmation(tmp_path):
    """确认窗口内产生的新修订仍需保留为未保存草稿。"""
    controller, coordinator, _ = setup(tmp_path)
    controller.model.set(('monitor', 'target'), 'first.exe')
    destination = tmp_path / 'different.yaml'
    destination.write_bytes(b'existing')

    def confirm(kind, detail):
        """在覆盖确认时模拟输入新字符。"""
        if kind == 'overwrite':
            controller.model.set(('monitor', 'target'), 'second.exe')
        return 'confirm'

    controller.confirm = confirm
    assert controller.save(destination)
    assert controller.model.get(('monitor', 'target')) == 'second.exe'
    assert controller.model.dirty


def test_conflict_reload_confirm_does_not_replace_newer_disk_version(tmp_path):
    """重载放弃确认期间又被外部修改时读取最终版本。"""
    controller, coordinator, _ = setup(tmp_path)
    path = controller.model.document.path
    controller.model.set(('monitor', 'target'), 'draft.exe')
    current = read_document(path)
    current.tree['monitor']['target'] = 'first-external.exe'
    save_document(current, path, expected_fingerprint=current.fingerprint)
    coordinator.check_external()

    def confirm(kind, detail):
        """模态确认停留时产生二次外部写入。"""
        if kind == 'discard_external':
            newer = read_document(path)
            newer.tree['monitor']['target'] = 'last-external.exe'
            save_document(newer, path, expected_fingerprint=newer.fingerprint)
        return 'confirm'

    controller.confirm = confirm
    assert controller.resolve_external('reload')
    assert controller.model.get(('monitor', 'target')) == 'last-external.exe'


def test_partial_number_survives_saving_another_field(tmp_path):
    """未完成数字草稿不能被其它字段的保存清除。"""
    controller, coordinator, _ = setup(tmp_path)
    path = ('monitor', 'common', 'timeout_threshold')
    controller.model.set_input(path, '-', kind='int')
    controller.model.set(('monitor', 'target'), 'other.exe')
    assert not coordinator.flush()
    assert controller.model.get_input(path) == '-'
    assert path in controller.model.input_errors


def test_start_rechecks_after_modal_confirmation(tmp_path, monkeypatch):
    """最终确认中产生新草稿时不得以先前配置启动 CLI。"""
    controller, coordinator, _ = setup(tmp_path)
    controller.cli_command = ('python.exe',)
    controller.launch_count = 1
    launch = Mock()
    monkeypatch.setattr('modules.gui.controller.launch_cli', launch)

    def confirm(kind, detail):
        """在重复启动确认停留期间编辑当前文档。"""
        if kind == 'repeat_launch':
            controller.model.set(('monitor', 'target'), 'new-draft.exe')
        return 'confirm'

    controller.confirm = confirm
    assert controller.start_cli() is None
    launch.assert_not_called()


def test_conflict_notifies_ui_once(tmp_path):
    """冲突进入时通知 UI 一次，后续轮询不重复弹窗。"""
    controller, coordinator, clock = setup(tmp_path)
    changed = Mock()
    controller.on_conflict = changed
    controller.model.set(('monitor', 'target'), 'draft.exe')
    controller.model.document.path.write_bytes(b'external')
    coordinator.check_external()
    clock.advance(2100)
    changed.assert_called_once()


def test_invalid_external_yaml_recovers_when_valid(tmp_path):
    """无草稿时坏 YAML 恢复为有效版本后自动重新加载。"""
    controller, coordinator, _ = setup(tmp_path)
    path = controller.model.document.path
    path.write_bytes(b'\xff')
    coordinator.check_external()
    external = new_document()
    external.tree['monitor']['target'] = 'recovered.exe'
    path.unlink()
    save_document(external, path, expected_fingerprint=None)
    coordinator.check_external()
    assert not controller.conflicted
    assert controller.model.get(('monitor', 'target')) == 'recovered.exe'


def test_save_after_clean_external_reload_preserves_external_bytes(tmp_path):
    """干净草稿的手动保存不得用旧快照覆盖外部更新。"""
    controller, _, _ = setup(tmp_path)
    path = controller.model.document.path
    external = read_document(path)
    external.tree['monitor']['target'] = 'external.exe'
    save_document(external, path, expected_fingerprint=external.fingerprint)

    assert controller.save()
    assert read_document(path).tree['monitor']['target'] == 'external.exe'


def test_save_as_during_clean_external_change_keeps_original(tmp_path):
    """另存为目标等于原路径时仍不能覆盖新磁盘版本。"""
    controller, _, _ = setup(tmp_path)
    path = controller.model.document.path
    external = read_document(path)
    external.tree['monitor']['target'] = 'external.exe'
    save_document(external, path, expected_fingerprint=external.fingerprint)

    assert controller.save_as(lambda: path)
    assert read_document(path).tree['monitor']['target'] == 'external.exe'
