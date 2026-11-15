#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Tk 主线程窗口、四页导航及隔离测试结果消费。"""

import queue
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk
import ctk_kit

from modules.gui.appearance import (AppearancePreferences, apply_appearance,
                                    FONT_BODY, load_preferences, preferences_path,
                                    save_preferences)
from modules.gui.pages import attachments, monitor, notification, review
from modules.gui.protocol import TestRequest, encode_request
from modules.gui.save_coordinator import SaveCoordinator


class App:
    """只在 Tk 主线程上更新控件，不读取 CLI 日志或进程。"""

    def __init__(self, root, controller):
        """建立四页导航和主线程结果泵。"""
        self.root = root
        self.controller = controller
        self.model = controller.model
        self.save_coordinator = SaveCoordinator(controller, root.after, root.after_cancel)
        self.model.on_edit = self.save_coordinator.edited
        self.model.before_list_edit = self.save_coordinator.flush
        self.model.on_list_edit = self.save_coordinator.flush
        self.latest_request = None
        self.generation = self.model.document_generation
        self.result_text = ''
        self.preview_image = None
        self.session_dir = Path(tempfile.mkdtemp(prefix='2rpm-gui-')).resolve()
        self.page_names = ('监视什么', '如何通知我', '附图与后续动作', '检查与启动')
        root._gui_program_dir = controller.program_dir
        root.title('2RPM 配置')
        root.geometry('900x680')
        self.path_label = ttk.Label(root)
        self.path_label.pack(fill='x', padx=12, pady=6)
        toolbar = ttk.Frame(root)
        toolbar.pack(fill='x', padx=12)
        self.actions = {}
        for name, action in (('新建', self.new), ('打开', self.open),
                             ('保存', self.save), ('另存为', self.save_as),
                             ('完整路径', self.show_path), ('打开日志目录', self.open_logs)):
            self.actions[name] = ttk.Button(toolbar, text=name, command=action)
            self.actions[name].pack(side='left')
        self.actions['处理冲突'] = ttk.Button(toolbar, text='处理冲突', command=self._show_conflict)
        if isinstance(root, ctk_kit.App):
            self.appearance_path = preferences_path()
            self.appearance = load_preferences(self.appearance_path)
            apply_appearance(root, self.appearance)
            self.appearance_theme = ctk_kit.CTkOptionMenu(
                toolbar, values=['跟随系统', '深色', '浅色'],
                command=self.choose_theme, width=100, font=FONT_BODY,
                dropdown_font=FONT_BODY)
            self.appearance_theme.set({None: '跟随系统', True: '深色',
                                       False: '浅色'}[self.appearance.dark])
            self.appearance_theme.pack(side='right', padx=4)
            self.appearance_material = ctk_kit.CTkOptionMenu(
                toolbar, values=['Mica', 'Acrylic', 'none'],
                command=self.choose_material, width=100, font=FONT_BODY,
                dropdown_font=FONT_BODY)
            self.appearance_material.set(self.appearance.material)
            self.appearance_material.pack(side='right', padx=4)
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill='both', expand=True, padx=12, pady=8)
        self.pages = [page.Page(self.notebook, self.model)
                      for page in (monitor, notification, attachments, review)]
        for title, page in zip(self.page_names, self.pages):
            self.notebook.add(page, text=title)
        self.notebook.bind('<<NotebookTabChanged>>', self._tab_changed)
        self.status = ttk.Label(root, text='新建配置或打开现有 YAML')
        self.status.pack(fill='x', padx=12)
        self.preview_label = ttk.Label(root)
        self.preview_label.pack(fill='x', padx=12)
        controls = ttk.Frame(root)
        controls.pack(fill='x', padx=12, pady=8)
        for name, action in (('校验', self.validate), ('截图预览', self.capture),
                             ('上传测试', self.upload), ('测试通知', self.notify),
                             ('取消测试', self.cancel_test), ('启动 CLI', self.start_cli)):
            self.actions[name] = ttk.Button(controls, text=name, command=action)
            self.actions[name].pack(side='left')
        self.controller.on_change = self.refresh
        self.controller.on_edit = self._refresh_save_status
        self.controller.on_conflict = self._show_conflict
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()
        self._after_id = root.after(50, self._poll)

    def _tab_changed(self, event):
        """离开配置页时尝试补存，失败仍允许返回检查页。"""
        index = self.notebook.index('current')
        if index == 3:
            self.save_coordinator.flush()
        self.pages[index].refresh()

    def _show_conflict(self):
        """向用户提供重载、另存为与暂不处理三个选择。"""
        self._refresh_save_status()
        self.status.configure(text='磁盘配置已变化，原文件保存已暂停')
        choice = messagebox.askyesnocancel('外部变更',
                                           '磁盘配置已变化。是：重新加载；否：将草稿另存为；取消：暂不处理。',
                                           parent=self.root)
        if choice is True:
            self.controller.resolve_external('reload')
        elif choice is False:
            self.controller.save_as(lambda: filedialog.asksaveasfilename(
                parent=self.root, defaultextension='.yaml') or None)
        else:
            self.controller.resolve_external('later')

    def choose_theme(self, value):
        """手动深浅色选择立即作用于本次会话。"""
        dark = {'跟随系统': None, '深色': True, '浅色': False}[value]
        self._save_appearance(AppearancePreferences(dark, self.appearance.material))

    def choose_material(self, value):
        """期望材质选择立即作用于本次会话。"""
        self._save_appearance(AppearancePreferences(self.appearance.dark, value))

    def _save_appearance(self, preferences):
        """独立保存外观，写盘失败仍保留本次会话选择。"""
        try:
            save_preferences(self.appearance_path, preferences)
        except OSError:
            messagebox.showerror('外观设置', '无法保存外观偏好', parent=self.root)
        self.appearance = preferences
        apply_appearance(self.root, preferences)

    @property
    def summary_text(self):
        """返回不包含凭证的检查页文字。"""
        return self.pages[3].summary.cget('text')

    @staticmethod
    def _plain_value(value):
        """把 YAML 的字典列表子类转换为协议可接受的基础容器。"""
        if isinstance(value, dict):
            return {key: App._plain_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [App._plain_value(item) for item in value]
        return value

    def refresh(self):
        """保存或替换文档后同步全部页面与标题。"""
        if self.generation != self.model.document_generation:
            self.latest_request = None
            self.result_text = ''
            self.preview_image = None
            self.preview_label.configure(image='', text='')
            self.generation = self.model.document_generation
        for page in self.pages:
            page.refresh()
        self._refresh_save_status()

    def _refresh_save_status(self):
        """编辑时仅刷新保存标记，不重建正在输入的控件。"""
        path = self.model.document.path
        pending = self.model.dirty or bool(self.model.input_errors)
        suffix = '（外部冲突）' if self.controller.conflicted else ' *' if pending else ''
        self.path_label.configure(text=f'{path or "未保存的新配置"}{suffix}')
        conflict = self.actions['处理冲突']
        if self.controller.conflicted:
            conflict.pack(side='left')
        else:
            conflict.pack_forget()

    def _poll(self):
        """每 50ms 在 Tk 线程消费一次队列。"""
        self.drain_results()
        self._after_id = self.root.after(50, self._poll)

    def _require_saved(self):
        """在业务操作前补存，不把未保存草稿当作磁盘配置。"""
        if self.save_coordinator.flush():
            return True
        self.status.configure(text='配置未保存，操作已停止')
        return False

    def drain_results(self):
        """只接纳最近请求及当前文档代次的终态。"""
        while True:
            try:
                result = self.controller.runner.results.get_nowait()
            except queue.Empty:
                break
            current = self.latest_request
            if (current is None or result.request_id != current[0]
                    or result.document_generation != current[1]
                    or result.document_generation != self.model.document_generation
                    or result.revision != current[2]):
                continue
            suffix = '（对应先前草稿）' if result.revision != self.model.revision else ''
            self.result_text = f'{result.status}: {result.reason}{suffix}'
            self.status.configure(text=self.result_text)
            self.preview_image = None
            self.preview_label.configure(image='', text='')
            if result.preview_paths:
                try:
                    with Image.open(result.preview_paths[0]) as image:
                        preview = image.copy()
                    preview.thumbnail((360, 160))
                    self.preview_image = ImageTk.PhotoImage(preview)
                    self.preview_label.configure(image=self.preview_image)
                except (OSError, ValueError):
                    self.preview_label.configure(text='无法显示预览图片')

    def new(self):
        """由控制器确认旧草稿后建立新配置。"""
        self.controller.new(lambda: filedialog.asksaveasfilename(
            parent=self.root, defaultextension='.yaml') or None)

    def open(self):
        """用原始选择路径读取，不隐式添加后缀。"""
        def picker():
            """由控制器暂停保存后再打开系统选择器。"""
            name = filedialog.askopenfilename(parent=self.root, filetypes=[('YAML', '*.yaml *.yml'), ('所有文件', '*.*')])
            return Path(name) if name else None

        if not self.controller.open(picker):
            messagebox.showerror('打开配置', '无法打开配置，或操作已取消', parent=self.root)

    def save(self):
        """只通过控制器保存编辑文档。"""
        if not self.controller.save():
            self.status.configure(text='配置未保存')

    def save_as(self):
        """选择另存为目标，由控制器规范化并检查冲突。"""
        if not self.controller.save_as(lambda: filedialog.asksaveasfilename(
                parent=self.root, defaultextension='.yaml') or None):
            self.status.configure(text='配置未另存')

    def show_path(self):
        """展示完整路径而非缩略的文件名。"""
        messagebox.showinfo('配置路径', str(self.model.document.path or '尚未保存'), parent=self.root)

    def open_logs(self):
        """只解析日志目录，不读取或跟踪其中内容。"""
        directory = self.controller.log_directory(self.controller.program_dir)
        if directory.is_dir():
            import os
            os.startfile(directory)
        else:
            messagebox.showinfo('日志目录', '目录尚不存在：' + str(directory), parent=self.root)

    def validate(self):
        """显示安全问题数量并定位第一个错误字段。"""
        self.save_coordinator.flush()
        path = self.model.document.path
        candidate = self.model.validate(path.parent if path else self.controller.program_dir)

        def page_index(issue):
            """按字段所在页面统一计算诊断路由。"""
            issue_path = issue.path
            if getattr(issue, 'code', None) in ('root_type', 'legacy', 'mapping') and issue_path != ('monitor', 'common'):
                return 3
            if not issue_path:
                return 3
            if (issue_path == ('monitor', 'common', 'timeout_threshold')
                    or issue_path[0] == 'external'
                    or issue_path[:2] == ('push', 'screenshot')):
                return 2
            return {'monitor': 0, 'push': 1}.get(issue_path[0], 3)

        counts = [0] * len(self.pages)
        for issue in candidate.issues:
            counts[page_index(issue)] += 1
        for index, name in enumerate(self.page_names):
            self.notebook.tab(index, text=f'{name} ({counts[index]})' if counts[index] else name)
        self.status.configure(text=f'校验：{sum(counts)} 个问题')
        errors = [issue for issue in candidate.issues if issue.severity == 'error']
        if errors:
            issue = errors[0]
            index = page_index(issue)
            self.notebook.select(index)
            self.root.update()
            self.pages[index].refresh()
            if index == 3 and getattr(issue, 'code', None) in ('root_type', 'legacy', 'mapping'):
                self.pages[index].show_issue(issue.code)
            else:
                self.pages[index].focus_issue(issue.path)
        return candidate

    def start_cli(self):
        """反馈进程创建结果但不跟踪其生命周期。"""
        if not self._require_saved():
            return
        result = self.controller.start_cli()
        self.status.configure(text=result.message if result else '未启动 CLI')

    def _request(self, action, *, target_index=None, host_index=None,
                 image_path=None, template_key=None):
        """主线程冻结草稿与绝对路径，后台只接收不可变请求。"""
        if not self._require_saved():
            return
        if self.controller.runner.active:
            self.status.configure(text='已有测试正在运行')
            return
        if action in ('upload', 'notify') and not messagebox.askyesno(
                '真实测试', '此操作会向外部服务发送请求，继续吗？', parent=self.root):
            return
        path = self.model.document.path
        request = TestRequest(uuid.uuid4().hex, self.model.document_generation,
                              self.model.revision, action, self._plain_value(self.model.snapshot()),
                              str(path.parent if path else self.controller.program_dir),
                              str(self.session_dir),
                              target_index, host_index,
                              str(Path(image_path).resolve()) if image_path else None,
                              template_key)
        try:
            encode_request(request)
        except (TypeError, ValueError):
            self.status.configure(text='配置包含无法用于测试的值')
            return
        try:
            self.controller.runner.start(request)
        except (RuntimeError, ValueError):
            self.status.configure(text='无法启动测试')
            return
        self.latest_request = (request.request_id, request.document_generation, request.revision)
        self.status.configure(text='测试进行中')

    def capture(self):
        """测试选定截图目标且不启动监视。"""
        index = self.pages[2].target_list.current()
        if index < 0 or index >= len(self.model.get(self.pages[2].targets_path)):
            self.status.configure(text='请选择截图目标')
            return
        self._request('capture', target_index=index)

    def upload(self):
        """单图床测试须先明确选择图片。"""
        index = self.pages[2].host_list.current()
        if index < 0 or index >= len(self.model.get(self.pages[2].hosts_path)):
            self.status.configure(text='请选择图床')
            return
        image = filedialog.askopenfilename(parent=self.root, filetypes=[('图片', '*.png *.jpg *.jpeg')])
        if image:
            self._request('upload', host_index=index, image_path=image)

    def notify(self):
        """以模拟事件值发送一次独立测试通知。"""
        self._request('notify', template_key=self.pages[1].selected_event.get())

    def cancel_test(self):
        """取消仅针对测试 worker，不影响 CLI。"""
        self.controller.runner.cancel()

    def close(self):
        """处理未保存草稿并正常关闭测试运行器。"""
        if self.controller.close():
            self.save_coordinator.close()
            self.root.after_cancel(self._after_id)
            threading.Thread(target=shutil.rmtree, args=(self.session_dir,),
                             kwargs={'ignore_errors': True}, name='gui-preview-cleanup').start()
            self.root.destroy()
