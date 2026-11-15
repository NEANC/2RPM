#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""单个隔离测试 worker 的请求身份与最后结果。"""

from modules.gui.protocol import TestRequest
from modules.gui.protocol import TestResult


class TestSession:
    """管理单个请求，窗口可隐藏而工作继续。"""

    def __init__(self, runner):
        """使用已有的单 worker 运行器。"""
        self.runner = runner
        self.active_request: TestRequest | None = None
        self.last_capture: TestResult | None = None
        self.last_upload: TestResult | None = None
        self.last_capture_previous = False
        self.last_upload_previous = False
        self.previous_draft = False
        self.hidden = False

    def start(self, request: TestRequest) -> bool:
        """占用唯一 worker；启动失败不保留运行态。"""
        if self.active_request is not None or self.runner.active:
            return False
        try:
            self.runner.start(request)
        except (RuntimeError, ValueError):
            return False
        self.active_request = request
        self.hidden = False
        self.previous_draft = False
        if request.action == 'capture':
            self.last_capture = None
            self.last_capture_previous = False
        elif request.action == 'upload':
            self.last_upload = None
            self.last_upload_previous = False
        return True

    def accept(self, result: TestResult, *, current_revision: int | None = None) -> bool:
        """只接纳当前请求及其固定文档身份，注明过期草稿。"""
        request = self.active_request
        if (request is None or result.request_id != request.request_id
                or result.document_generation != request.document_generation
                or result.revision != request.revision):
            return False
        self.previous_draft = current_revision is not None and result.revision != current_revision
        if request.action == 'capture':
            self.last_capture = result
            self.last_capture_previous = self.previous_draft
        elif request.action == 'upload':
            self.last_upload = result
            self.last_upload_previous = self.previous_draft
        self.active_request = None
        return True

    def hide(self) -> None:
        """隐藏仅影响展示，不向 worker 发送取消。"""
        self.hidden = True

    def cancel(self) -> None:
        """取消当前 worker；等待其终态再释放槽位。"""
        if self.active_request is not None:
            self.runner.cancel()

    def clear(self) -> None:
        """成功切换配置后丢弃旧结果与展示引用。"""
        self.active_request = None
        self.last_capture = None
        self.last_upload = None
        self.last_capture_previous = False
        self.last_upload_previous = False
        self.previous_draft = False
        self.hidden = False
