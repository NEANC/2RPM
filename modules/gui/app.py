#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Tk 主线程窗口、四页导航及隔离测试结果消费。"""

import queue
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image, ImageTk
import ctk_kit

from modules.gui.appearance import (AppearancePreferences, apply_appearance,
                                    FONT_BODY, load_preferences, preferences_path,
                                    save_preferences)
from modules.gui.pages import attachments, external, home, logs, monitor, notification_ctk, review, screenshot, settings
from modules.gui.protocol import TestRequest, encode_request
from modules.gui.save_coordinator import SaveCoordinator
from modules.gui.test_dialogs import TestDialogs
from modules.gui.test_session import TestSession


class App:
    """只在 Tk 主线程上更新控件，不读取 CLI 日志或进程。"""

    def __init__(self, root, controller):
        """建立四页导航和主线程结果泵。"""
        # ctk-kit 字体和 CustomTkinter 内部字体依赖 Tk 默认根，不能沿用已销毁解释器。
        if tk._default_root is not root:
            tk._default_root = root
        self.root = root
        self.controller = controller
        self.model = controller.model
        self.save_coordinator = SaveCoordinator(controller, root.after, root.after_cancel)
        self.model.on_edit = self.save_coordinator.edited
        self.model.before_list_edit = self._before_list_edit
        self.model.on_list_edit = self.save_coordinator.flush
        self.test_session = TestSession(controller.runner)
        self.test_dialogs = TestDialogs(root, self.test_session)
        self.generation = self.model.document_generation
        self.document_path = self.model.document.path
        self.result_text = ''
        self.preview_image = None
        self.session_dir = Path(tempfile.mkdtemp(prefix='2rpm-gui-')).resolve()
        self.page_names = ('主页', '监视设置', '截图设置', '消息推送设置',
                           '外部程序设置', '日志设置', '设置')
        self.current_page = '主页'
        root._gui_program_dir = controller.program_dir
        root.title('2RPM 配置')
        root.geometry('900x680')
        self.appearance_path = preferences_path()
        self.appearance = load_preferences(self.appearance_path)
        if isinstance(root, ctk_kit.App):
            apply_appearance(root, self.appearance)
        self.actions = {}
        self.path_label = ctk_kit.CTkLabel(root, text='', font=FONT_BODY)
        self.status = ctk_kit.CTkLabel(root, text='新建配置或打开现有 YAML', font=FONT_BODY)
        self.preview_label = ctk_kit.CTkLabel(root, text='', font=FONT_BODY)
        self.actions['处理冲突'] = ctk_kit.CTkButton(root, text='处理冲突', command=self._show_conflict, font=FONT_BODY)
        self.actions['测试通知'] = ctk_kit.CTkButton(root, text='通知测试', command=self.notify, font=FONT_BODY)
        self.pages = []
        self.visible_pages = {}

        def build(name, module, *, legacy=False):
            """在侧栏真实内容区域装入常驻页面。"""
            def create(parent):
                """业务页暂用旧表单桥接；后续逐页改用 ctk-kit。"""
                container = ctk_kit.CTkFrame(parent, corner_radius=0)
                if legacy:
                    actions = {
                        '截图设置': (('截图测试', self.capture), ('上传测试', self.upload)),
                        '消息推送设置': (('通知测试', self.notify),),
                        '日志设置': (('打开日志目录', self.open_logs),),
                    }
                    for label, callback in actions.get(name, ()):
                        button = ctk_kit.CTkButton(container, text=label,
                                                   command=callback, font=FONT_BODY)
                        button.pack(side='top', anchor='e', padx=12, pady=4)
                        if name == '消息推送设置':
                            self.actions['测试通知'] = button
                page = module.Page(container, self.model if legacy else self)
                page.pack(fill='both', expand=True)
                self.visible_pages[name] = page
                return container
            return create

        builders = {'主页': build('主页', home),
                    '监视设置': build('监视设置', monitor, legacy=True),
                    '截图设置': build('截图设置', screenshot),
                    '消息推送设置': build('消息推送设置', notification_ctk),
                    '外部程序设置': build('外部程序设置', external),
                    '日志设置': build('日志设置', logs)}
        self.sidebar = ctk_kit.SidebarNav(root, title='2RPM', logo=False,
                                           pages=builders,
                                           bottom_pages={'设置': build('设置', settings)},
                                           initial='主页', on_change=self._page_changed)
        self.sidebar.pack(fill='both', expand=True)
        self.sidebar._warm_enabled = False
        for name in ('监视设置', '消息推送设置', '截图设置', '日志设置'):
            self.sidebar.page(name)
            self.pages.append(self.visible_pages[name])
        self.actions['测试通知'] = self.visible_pages['消息推送设置'].notify_button
        self.status.pack(fill='x', padx=12)
        self.path_label.pack(fill='x', padx=12)
        self.actions['处理冲突'].pack_forget()
        self.controller.on_change = self.refresh
        self.controller.on_edit = self._refresh_save_status
        self.controller.on_conflict = self._show_conflict
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()
        self._after_id = root.after(50, self._poll)

    def _page_changed(self, name):
        """侧栏切换时更新当前页，配置页离开时尝试补存。"""
        if getattr(self, 'current_page', '主页') != name and self.current_page != '主页':
            self.save_coordinator.flush()
        self.current_page = name

    def show_page(self, name: str) -> bool:
        """按业务名称切页；返回主页不因未完成草稿而拒绝。"""
        if name not in self.page_names:
            return False
        self.sidebar.show(name)
        page = self.visible_pages[name]
        if hasattr(page, 'refresh'):
            page.refresh()
        return True

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
        if '设置' in getattr(self, 'visible_pages', {}):
            self.visible_pages['设置'].refresh()

    @property
    def summary_text(self):
        """返回不包含凭证的检查页文字。"""
        return self.visible_pages['主页'].summary.cget('text')

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
            if self.document_path != self.model.document.path:
                self.test_session.clear()
                self.test_dialogs.clear()
                self.actions['测试通知'].configure(text='测试通知', state='normal')
                self.result_text = ''
                self.preview_image = None
                self.preview_label.configure(image='', text='')
            self.visible_pages['消息推送设置']._expanded_channel = None
            self.visible_pages['消息推送设置']._expanded_events.clear()
            self.generation = self.model.document_generation
            self.document_path = self.model.document.path
        for page in self.visible_pages.values():
            if hasattr(page, 'refresh'):
                page.refresh()
        for page in self.pages:
            page.refresh()
        self._refresh_save_status()

    def _refresh_save_status(self):
        """编辑时仅刷新保存标记，不重建正在输入的控件。"""
        path = self.model.document.path
        pending = self.model.dirty or bool(self.model.input_errors)
        suffix = '（外部冲突）' if self.controller.conflicted else '（未保存）' if pending else ''
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

    def _before_list_edit(self):
        """补存若替换草稿则停止列表操作并要求重新选择。"""
        identity = (self.model.document_generation, self.model.revision)
        saved = self.save_coordinator.flush()
        if identity != (self.model.document_generation, self.model.revision):
            self.visible_pages['消息推送设置']._expanded_channel = None
            self.visible_pages['消息推送设置'].refresh()
            self.status.configure(text='配置已更新，请重新选择列表项')
            return False
        return saved

    def drain_results(self):
        """只接纳最近请求及当前文档代次的终态。"""
        while True:
            try:
                result = self.controller.runner.results.get_nowait()
            except queue.Empty:
                break
            current = self.test_session.active_request
            if current is None or not self.test_session.accept(
                    result, current_revision=self.model.revision,
                    current_generation=self.model.document_generation):
                continue
            suffix = ('（对应先前配置）' if self.test_session.previous_generation else
                      '（对应先前草稿）' if self.test_session.previous_draft else '')
            self.result_text = f'{result.status}: {result.reason}{suffix}'
            if current.action == 'notify':
                self.actions['测试通知'].configure(text='测试通知', state='normal')
                if result.status != 'success':
                    failures = [f'{item.index + 1} {item.provider}：{item.reason}'
                                for item in result.channel_results if item.status == 'failure']
                    messagebox.showerror('通知测试失败', '\n'.join(failures) or result.reason,
                                         parent=self.root)
                continue
            if not self.test_session.hidden:
                self.test_dialogs.show_result(current.action, result,
                                              previous=(self.test_session.previous_generation
                                                        or self.test_session.previous_draft),
                                              retry_callback=(self.open_upload_selection
                                                              if current.action == 'upload'
                                                              else self.open_capture_selection))
            self.status.configure(text=self.result_text)
            self.preview_image = None
            self.preview_label.configure(image='', text='')
            if result.preview_paths and not isinstance(self.root, ctk_kit.App):
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
        if self.controller.runner.active:
            self.status.configure(text='请等待测试完成或取消测试后切换配置')
            return
        self.controller.new(lambda: filedialog.asksaveasfilename(
            parent=self.root, defaultextension='.yaml') or None)

    def open(self):
        """用原始选择路径读取，不隐式添加后缀。"""
        if self.controller.runner.active:
            self.status.configure(text='请等待测试完成或取消测试后切换配置')
            return

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
        if self.controller.runner.active:
            self.status.configure(text='请等待测试完成或取消测试后切换配置')
            return
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

        counts = {name: 0 for name in self.page_names}
        def destination(issue):
            """结构问题归主页，高级隐藏字段归相关业务页。"""
            path = issue.path
            if not path or getattr(issue, 'code', None) in ('root_type', 'legacy', 'mapping'):
                return '主页'
            if path[:3] == ('monitor', 'common', 'timeout_threshold') or path[0] == 'external':
                return '外部程序设置'
            if path[:2] == ('push', 'screenshot'):
                return '截图设置'
            return {'monitor': '监视设置', 'push': '消息推送设置',
                    'log': '日志设置'}.get(path[0], '主页')
        for issue in candidate.issues:
            counts[destination(issue)] += 1
        self.visible_pages['主页'].check.configure(
            text=f'存在 {len(candidate.issues)} 个问题，请检查对应配置'
            if candidate.issues else '配置检查：无问题')
        self.status.configure(text=f'校验：{len(candidate.issues)} 个问题')
        errors = [issue for issue in candidate.issues if issue.severity == 'error']
        if errors:
            issue = errors[0]
            name = destination(issue)
            self.show_page(name)
            if name == '主页':
                self.visible_pages['主页'].show_issue(getattr(issue, 'code', 'structure'))
            elif name in self.visible_pages and hasattr(self.visible_pages[name], 'focus_issue'):
                self.visible_pages[name].focus_issue(issue.path)
            else:
                self.status.configure(text=f'{name}：请在 YAML 中检查对应字段')
        return candidate

    def start_cli(self):
        """反馈进程创建结果但不跟踪其生命周期。"""
        if not self._require_saved():
            return
        result = self.controller.start_cli()
        self.status.configure(text=result.message if result else '未启动 CLI')

    def _request(self, action, *, target_index=None, host_index=None,
                 image_path=None, template_key=None, capture_provider=None,
                 capture_target=None):
        """主线程冻结草稿与绝对路径，后台只接收不可变请求。"""
        if not self._require_saved():
            return False
        if self.test_session.active_request is not None or self.controller.runner.active:
            self.status.configure(text='已有测试正在运行')
            return False
        if action in ('upload', 'notify') and not messagebox.askyesno(
                '真实测试', ('固定测试消息将发往全部配置通道，继续吗？' if action == 'notify'
                           else '图片将真实上传到选定图床，继续吗？'), parent=self.root):
            return False
        path = self.model.document.path
        request = TestRequest(uuid.uuid4().hex, self.model.document_generation,
                              self.model.revision, action, self._plain_value(self.model.snapshot()),
                              str(path.parent if path else self.controller.program_dir),
                              str(self.session_dir),
                              target_index, host_index,
                              str(Path(image_path).resolve()) if image_path else None,
                              template_key, capture_provider, capture_target)
        try:
            encode_request(request)
        except (TypeError, ValueError):
            self.status.configure(text='配置包含无法用于测试的值')
            return False
        if not self.test_session.start(request):
            self.status.configure(text='无法启动测试')
            return False
        self.status.configure(text='测试进行中')
        if action == 'notify':
            self.actions['测试通知'].configure(text='发送中…', state='disabled')
        else:
            self.test_dialogs.show_result(action)
        return True

    def capture(self):
        """打开手填来源与目标的选择窗，或恢复运行结果。"""
        if self.test_session.active_request is not None:
            if self.test_session.active_request.action == 'capture':
                self.test_session.hidden = False
                self.test_dialogs.show_result('capture')
            return
        if self.test_session.last_capture is not None:
            self.test_dialogs.show_result('capture', self.test_session.last_capture,
                                          previous=(self.test_session.last_capture_previous
                                                    or self.test_session.last_capture.document_generation
                                                    != self.model.document_generation
                                                    or self.test_session.last_capture.revision
                                                    != self.model.revision),
                                          retry_callback=self.open_capture_selection)
            return
        self.open_capture_selection()

    def open_capture_selection(self):
        """选择截图来源前补存并绑定文档身份。"""
        if not self._require_saved():
            return
        identity = (self.model.document_generation, self.model.revision)

        def submit(provider, target):
            """截图目标输入期间的文档变更需重新选择。"""
            if not self._require_saved():
                return False
            if identity != (self.model.document_generation, self.model.revision):
                self.status.configure(text='配置已更新，请重新选择截图目标')
                self.open_capture_selection()
                return False
            return self._request('capture', capture_provider=provider,
                                 capture_target=target)

        self.test_dialogs.open_capture(submit)

    def upload(self):
        """展示图床和本地图片选择，确认后仅发送选定图床。"""
        if self.test_session.active_request is not None:
            if self.test_session.active_request.action == 'upload':
                self.test_session.hidden = False
                self.test_dialogs.show_result('upload')
            return
        if self.test_session.last_upload is not None:
            self.test_dialogs.show_result('upload', self.test_session.last_upload,
                                          previous=(self.test_session.last_upload_previous
                                                    or self.test_session.last_upload.document_generation
                                                    != self.model.document_generation
                                                    or self.test_session.last_upload.revision
                                                    != self.model.revision),
                                          retry_callback=self.open_upload_selection)
            return
        self.open_upload_selection()

    def open_upload_selection(self):
        """配置变动后重开新图床列表，不复用旧索引。"""
        if not self._require_saved():
            return
        hosts = self.model.get(('push', 'screenshot', 'image_host'))
        names = [item.get('provider', '未知') if isinstance(item, dict) else '未知'
                 for item in hosts]
        identity = (self.model.document_generation, self.model.revision, hosts)

        def submit(index, image):
            """复核重选时的列表内容与修订。"""
            if not self._require_saved():
                return False
            if (identity != (self.model.document_generation, self.model.revision,
                             self.model.get(('push', 'screenshot', 'image_host')))
                    or index >= len(hosts)):
                self.status.configure(text='配置已更新，请重新选择图床')
                self.open_upload_selection()
                return False
            return self._request('upload', host_index=index, image_path=image)

        self.test_dialogs.open_upload(names, submit)

    def notify(self):
        """通知测试无通道提前阻止，不保留可重开的结果。"""
        if self.test_session.active_request is not None or self.controller.runner.active:
            return
        if not self._require_saved():
            return
        channels = self.model.get(('push', 'push_channel_settings', 'channels'))
        if not channels:
            self.status.configure(text='请先添加推送通道')
            return
        self._request('notify', template_key='on_end')

    def cancel_test(self):
        """取消仅针对截图或上传 worker，不影响 CLI。"""
        current = self.test_session.active_request
        if current is not None and current.action != 'notify':
            self.test_session.cancel()

    def close(self):
        """处理未保存草稿并正常关闭测试运行器。"""
        if self.controller.close():
            self.test_dialogs.clear()
            self.save_coordinator.close()
            self.root.after_cancel(self._after_id)
            threading.Thread(target=shutil.rmtree, args=(self.session_dir,),
                             kwargs={'ignore_errors': True}, name='gui-preview-cleanup').start()
            self.root.destroy()
