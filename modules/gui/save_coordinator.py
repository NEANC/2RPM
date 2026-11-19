#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""无 Tk 的自动保存及磁盘变更协调器。"""


class SaveCoordinator:
    """将延迟回调绑定文档代次，并在事务中暂停自动写入。"""

    def __init__(self, controller, schedule, cancel):
        """注入控制器及主线程可取消调度器。"""
        self.controller = controller
        self.schedule = schedule
        self.cancel = cancel
        self.pending = None
        self._save_serial = 0
        self.poll = None
        self.depth = 0
        self.closed = False
        controller.coordinator = self
        self._watch()

    def _watch(self):
        """按当前代次安排下一次外部变更检测。"""
        if not self.closed:
            generation = self.controller.model.document_generation
            self.poll = self.schedule(1000, lambda: self._poll(generation))

    def _poll(self, generation):
        """忽略已切换文档的检测回调。"""
        if self.closed:
            return
        self.poll = None
        if generation == self.controller.model.document_generation and not self.depth:
            self.check_external()
        self._watch()

    def edited(self):
        """新的编辑撤销旧防抖，仅保留最后一次写入。"""
        self.controller.on_edit()
        self._save_serial += 1
        if self.pending is not None:
            self.cancel(self.pending)
            self.pending = None
        if self.closed or self.depth or self.controller.conflicted:
            return
        generation = self.controller.model.document_generation
        serial = self._save_serial
        self.pending = self.schedule(500, lambda: self._save(generation, serial))

    def _save(self, generation, serial=None):
        """过期回调不写入别的文档。"""
        if (generation != self.controller.model.document_generation or self.closed or self.depth
                or serial is not None and serial != self._save_serial):
            return
        self.pending = None
        self.flush()

    def flush(self) -> bool:
        """在可写时立即尝试提交当前草稿。"""
        if self.closed or self.depth or self.controller.conflicted or self.controller.model.input_errors:
            return False
        if self.pending is not None:
            self.cancel(self.pending)
            self.pending = None
        if not self.controller.model.dirty:
            return not self.controller.check_external()
        if self.controller.model.document.path is None:
            return False
        return self.controller.save()

    def begin_transaction(self) -> None:
        """暂停自动写入及自动重载直到文件事务结束。"""
        self.depth += 1
        self._save_serial += 1
        if self.pending is not None:
            self.cancel(self.pending)
            self.pending = None

    def end_transaction(self, switched: bool) -> None:
        """结束事务后检查外部版本并恢复可用的保存。"""
        self.depth -= 1
        if self.depth:
            return
        if not switched:
            self.check_external()
        if not self.closed and self.controller.model.dirty and not self.controller.conflicted:
            self.edited()

    def check_external(self) -> None:
        """对当前文档执行无写入的版本检查。"""
        if not self.closed and not self.depth:
            self.controller.check_external()

    def close(self) -> None:
        """撤销所有尚未执行的回调。"""
        self.closed = True
        for handle in (self.pending, self.poll):
            if handle is not None:
                self.cancel(handle)
        self.pending = self.poll = None
