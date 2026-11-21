#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Tk 主线程窗口、侧栏导航及隔离测试结果消费。"""

from collections.abc import Mapping
import ctypes
from ctypes import wintypes
import queue
import shutil
import stat
import tempfile
import threading
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image, ImageTk
import ctk_kit
from ctk_kit.styles import style_of

from modules.gui.appearance import (AppearancePreferences, COLORS, apply_appearance,
                                    FONT_BODY, FONT_BODY_BOLD, FONT_BRAND, FONT_GROUP,
                                    FONT_SMALL, load_preferences, preferences_path,
                                    save_preferences)
from modules.gui.controller import LAUNCH_FAILURE_MESSAGE, SaveResult
from modules.gui.pages import (external, home, logs, monitor, notification_ctk,
                               screenshot, settings)
from modules.gui.protocol import TestRequest, encode_request
from modules.gui.save_coordinator import SaveCoordinator
from modules.gui.save_shortcuts import SaveShortcuts
from modules.gui.test_dialogs import TestDialogs
from modules.gui.test_session import TestSession
from modules.screenshot.retention import FORMAT_SUFFIX

INCOMPLETE_INPUT_MESSAGE = '输入尚未完成，请补全数字后保存'
SAVE_GUIDANCE_MESSAGE = '请先按 Ctrl+S 或点击保存，选择配置文件路径'


class _MonitorInfo(ctypes.Structure):
    """Win32 显示器工作区结构。"""

    _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                ('work', wintypes.RECT), ('flags', wintypes.DWORD)]


def work_area_size(root):
    """读取窗口所在显示器可用工作区的逻辑宽高。"""
    info = _MonitorInfo()
    info.size = ctypes.sizeof(info)
    monitor = ctypes.windll.user32.MonitorFromWindow(root.winfo_id(), 2)
    scaling = root._get_window_scaling() if isinstance(root, ctk_kit.App) else 1
    if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return (int((info.work.right - info.work.left) / scaling),
                int((info.work.bottom - info.work.top) / scaling))
    return (int(root.winfo_screenwidth() / scaling),
            int(root.winfo_screenheight() / scaling))


def work_area_height(root):
    """返回窗口所在显示器可用工作区的逻辑高度。"""
    return work_area_size(root)[1]


class App:
    """只在 Tk 主线程上更新控件，不读取 CLI 日志或进程。"""

    def __init__(self, root, controller):
        """建立侧栏导航和主线程结果泵。"""
        try:
            self._initialize(root, controller)
        except Exception:
            if hasattr(self, 'save_shortcuts'):
                self.save_shortcuts.close()
            if hasattr(self, 'save_coordinator'):
                self.save_coordinator.close()
            raise

    def _initialize(self, root, controller):
        """初始化窗口组件及会话目录。"""
        # ctk-kit 字体和 CustomTkinter 内部字体依赖 Tk 默认根，不能沿用已销毁解释器。
        if tk._default_root is not root:
            tk._default_root = root
        self.root = root
        self.controller = controller
        self.model = controller.model
        self.save_coordinator = SaveCoordinator(controller, root.after, root.after_cancel)
        self.save_coordinator.on_auto_save_failed = self._auto_save_failed
        self.model.on_edit = self.save_coordinator.edited
        self.model.before_list_edit = self._before_list_edit
        self.model.on_list_edit = self._after_list_edit
        self.test_session = TestSession(controller.runner)
        self.test_dialogs = TestDialogs(root, self.test_session)
        self.generation = self.model.document_generation
        self.document_path = self.model.document.path
        self._validated = None
        self.result_text = ''
        self.preview_image = None
        self._owned_previews = set()
        self.page_names = ('主页', '监视设置', '截图设置', '消息推送设置',
                           '外部程序设置', '日志设置', '设置')
        self.current_page = '主页'
        root._gui_program_dir = controller.program_dir
        root.title('2RPM 配置')
        available_width, available_height = work_area_size(root)
        width = min(900, available_width)
        root._gui_work_size = (width, min(680, work_area_height(root)))
        root.minsize(min(600, width), 300)
        root.geometry(f'{width}x{root._gui_work_size[1]}')
        self.appearance_path = preferences_path()
        self.appearance = load_preferences(self.appearance_path)
        if isinstance(root, ctk_kit.App):
            # 系统标题栏窗口不能再使用 ctk-kit 的玻璃客户区：Acrylic/Mica 会让
            # 整窗半透明并透出桌面。保存的材质偏好不变，仅本次渲染降级为纯色。
            apply_appearance(root, self._effective_appearance(self.appearance))
        self.actions = {}
        self.footer = ctk_kit.CTkFrame(root, corner_radius=0, fg_color=COLORS['side'])
        self.path_label = ctk_kit.CTkLabel(self.footer, text='', font=FONT_BODY)
        self.status = ctk_kit.CTkLabel(self.footer, text='新建配置或打开现有 YAML', font=FONT_BODY)
        self.preview_label = ctk_kit.CTkLabel(root, text='', font=FONT_BODY)
        self.actions['处理冲突'] = ctk_kit.CTkButton(root, text='处理冲突', command=self._show_conflict, font=FONT_BODY)
        self.actions['测试通知'] = ctk_kit.CTkButton(root, text='通知测试', command=self.notify, font=FONT_BODY)
        self.visible_pages = {}

        def build(name, module, *, with_model=False):
            """在侧栏真实内容区域装入常驻页面。"""
            def create(parent):
                """按页面约定注入共享模型或窗口对象。"""
                container = ctk_kit.CTkFrame(parent, corner_radius=0,
                                             fg_color=COLORS['side'])
                page = module.Page(container, self.model if with_model else self)
                try:
                    page.configure(fg_color=COLORS['side'])
                except Exception:
                    pass
                page.pack(fill='both', expand=True)
                self.visible_pages[name] = page
                if name == '消息推送设置':
                    self.actions['测试通知'] = page.notify_button
                if name in ('截图设置', '消息推送设置'):
                    self._sync_test_buttons()
                return container
            return create

        builders = {'主页': build('主页', home),
                    '监视设置': build('监视设置', monitor, with_model=True),
                    '截图设置': build('截图设置', screenshot),
                    '消息推送设置': build('消息推送设置', notification_ctk),
                    '外部程序设置': build('外部程序设置', external),
                    '日志设置': build('日志设置', logs)}
        self.sidebar = ctk_kit.SidebarNav(root, title='2RPM', logo=False,
                                           pages=builders, width=246, bottom_width=206,
                                           padding=(0, 0),
                                           bottom_pages={'设置': build('设置', settings)},
                                           initial='主页', on_change=self._page_changed)
        self.sidebar.title_label.configure(font=FONT_BRAND)
        self.sidebar.subtitle_label.configure(font=FONT_SMALL)
        for button in self.sidebar._buttons.values():
            button.configure(font=FONT_BODY, height=56, corner_radius=12,
                             border_spacing=12)
            button.pack_configure(padx=16, pady=4)
        for button in self.sidebar._bottom_buttons.values():
            # 与导航项一致：无底色阴影、文字左对齐、占满侧栏宽度。
            button.configure(font=FONT_BODY, height=56, anchor='w',
                             border_width=0, fg_color='transparent',
                             border_spacing=12,
                             hover_color=COLORS['card_hover'])
            button.pack_configure(fill='x', padx=4, pady=4)
        self._apply_nav_styles()
        self.config_group_button = ctk_kit.CTkButton(
            self.sidebar.nav_scroll, text='▼ 配置', height=44, anchor='w',
            fg_color='transparent', font=FONT_GROUP,
            command=self._toggle_config_group)
        self.config_group_button.pack(fill='x', padx=16, pady=4,
                                      before=self.sidebar._buttons['监视设置'])
        self._paint_sidebar()
        self.sidebar.pack(fill='both', expand=True)
        self.sidebar.pack_propagate(False)
        self.sidebar._warm_enabled = False
        self.actions['处理冲突'].pack_forget()
        # 底部状态栏已移除；状态与路径标签保留为隐藏对象，供内部状态与测试读取。
        self.controller.on_change = self.refresh
        self.controller.on_edit = self._refresh_save_status
        self.controller.on_saved = self._refresh_saved_home
        self.controller.on_conflict = self._show_conflict
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.save_shortcuts = SaveShortcuts(root, self.save)
        self._paint_sidebar()
        self.refresh()
        session_dir = Path(tempfile.mkdtemp(prefix='2rpm-gui-'))
        try:
            self.session_dir = session_dir.resolve()
            self._after_id = root.after(50, self._poll)
        except Exception:
            shutil.rmtree(session_dir)
            raise

    def _toggle_config_group(self):
        """切换五个配置导航入口的可见性，保持原有页面顺序。"""
        names = self.page_names[1:-1]
        buttons = self.sidebar._buttons
        if buttons[names[0]].winfo_manager():
            for name in names:
                buttons[name].pack_forget()
            self.config_group_button.configure(text='▶ 配置')
            self._paint_config_group()
            return
        previous = self.config_group_button
        for name in names:
            buttons[name].pack(fill='x', padx=16, pady=4, after=previous)
            previous = buttons[name]
        self.config_group_button.configure(text='▼ 配置')
        self._paint_config_group()

    def _effective_appearance(self, preferences):
        """系统标题栏窗口只渲染纯色材质，保存的用户选择保持不变。"""
        if not self.root.overrideredirect():
            return type(preferences)(preferences.dark, 'none')
        return preferences

    def _paint_config_group(self):
        """收起时以组标题表示当前配置页，展开或离开时恢复普通样式。"""
        collapsed = not self.sidebar._buttons[self.page_names[1]].winfo_manager()
        active = collapsed and self.current_page in self.page_names[1:-1]
        state = 'nav_active' if active else 'nav_normal'
        self.config_group_button.configure(**style_of('sidebar', state))
        if not active:
            self.config_group_button.configure(text_color=COLORS['text_secondary'])

    @staticmethod
    def _apply_nav_styles():
        """侧栏选中项采用原型的浅色强调底，而不是整块实心强调色。"""
        from ctk_kit import styles
        styles.SHEETS['sidebar']['nav_active'].update({
            'fg_color': COLORS['nav_active_bg'], 'bg_color': COLORS['side'],
            'hover_color': COLORS['nav_active_bg'], 'text_color': COLORS['accent']})
        styles.SHEETS['sidebar']['nav_normal'].update({
            'fg_color': 'transparent', 'bg_color': COLORS['side'],
            'hover_color': COLORS['card_hover'],
            'text_color': COLORS['text_secondary']})

    def _paint_sidebar(self):
        """按当前明暗主题刷新侧栏表面与导航对比度。"""
        dark = ctk_kit.get_appearance_mode() == 'Dark'
        index = int(dark)
        side = COLORS['side'][index]
        text = COLORS['text_primary'][index]
        muted = COLORS['text_secondary'][index]
        self.sidebar.configure(fg_color=side)
        self.sidebar.sidebar.configure(fg_color=side, bg_color=side)
        for frame in (self.sidebar._head, self.sidebar._title_box,
                      self.sidebar.nav_scroll, self.sidebar.bottom):
            frame.configure(fg_color=side, bg_color=side)
        if self.sidebar.divider is not None:
            self.sidebar.divider.configure(bg=COLORS['border_strong'][index])
        nav_bar = getattr(self.sidebar.nav_scroll, '_scrollbar', None)
        if nav_bar is not None:
            nav_bar.configure(fg_color=side,
                              button_color=COLORS['border_strong'][index],
                              button_hover_color=COLORS['text_secondary'][index])
        canvas = getattr(self.sidebar.nav_scroll, '_parent_canvas', None)
        if canvas is not None:
            canvas.configure(bg=side, highlightthickness=0)
        self.sidebar.title_label.configure(text_color=text, bg_color=side)
        self.sidebar.subtitle_label.configure(text_color=muted, bg_color=side)
        for name, button in self.sidebar._buttons.items():
            active = name == self.current_page
            button.configure(
                bg_color=side,
                fg_color=COLORS['nav_active_bg'][index] if active else 'transparent',
                hover_color=(COLORS['nav_active_bg'][index] if active
                             else COLORS['card_hover'][index]),
                text_color=COLORS['accent'][index] if active else muted,
                font=FONT_BODY_BOLD if active else FONT_BODY)
        for button in self.sidebar._bottom_buttons.values():
            button.configure(bg_color=side, text_color=text,
                             fg_color='transparent',
                             hover_color=COLORS['card_hover'][index],
                             anchor='w')
        self.config_group_button.configure(bg_color=side, text_color=muted)

    def _page_changed(self, name):
        """侧栏切换时更新当前页，配置页离开时尝试补存。"""
        if getattr(self, 'current_page', '主页') != name and self.current_page != '主页':
            result = self.save_coordinator.flush_result()
            if result is SaveResult.RELOADED:
                self.status.configure(text='已重新加载磁盘配置')
            elif result not in (SaveResult.SAVED, SaveResult.SUPERSEDED):
                self.status.configure(text='配置未保存，已保留草稿')
        self.current_page = name
        if hasattr(self, 'config_group_button'):
            self._paint_config_group()
            self._paint_sidebar()

    def show_page(self, name: str) -> bool:
        """按业务名称切页；返回主页不因未完成草稿而拒绝。"""
        if name not in self.page_names:
            return False
        self._invalidate_validation_if_stale()
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
            if self.controller.runner.active:
                messagebox.showinfo('配置切换', '请等待测试完成或取消测试后切换配置',
                                    parent=self.root)
            else:
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
        apply_appearance(self.root, self._effective_appearance(preferences))
        if hasattr(self, 'config_group_button'):
            self._apply_nav_styles()
            self.sidebar._paint_nav(self.current_page, force=True)
            self._paint_config_group()
            self._paint_sidebar()
        if '设置' in getattr(self, 'visible_pages', {}):
            self.visible_pages['设置'].refresh()

    @property
    def summary_text(self):
        """返回不包含凭证的检查页文字。"""
        return self.visible_pages['主页'].summary_text

    @staticmethod
    def _plain_value(value):
        """把 YAML 容器与标量子类转换为协议可接受的基础类型。"""
        if isinstance(value, bool):
            return bool(value)
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            return float(value)
        if isinstance(value, str):
            return str(value)
        if isinstance(value, Mapping):
            return {str(key): App._plain_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [App._plain_value(item) for item in value]
        return value

    def _invalidate_validation_if_stale(self):
        """草稿或文档变化后撤销旧校验结果。"""
        if self._validated is None:
            return
        if self._validated != (self.model.document_generation, self.model.revision):
            self._validated = None
            self.visible_pages['主页'].invalidate_validation()

    def refresh(self):
        """保存或替换文档后同步全部页面与标题。"""
        self._invalidate_validation_if_stale()
        if self.generation != self.model.document_generation:
            if self.document_path != self.model.document.path:
                self._clear_test_results()
            message_page = self.visible_pages.get('消息推送设置')
            if message_page is not None:
                message_page._expanded_channel = None
                message_page._expanded_events.clear()
            self.generation = self.model.document_generation
            self.document_path = self.model.document.path
        for page in self.visible_pages.values():
            if hasattr(page, 'refresh'):
                page.refresh()
        self.test_dialogs.mark_previous(self.model.document_generation, self.model.revision)
        self._sync_test_buttons()
        self._refresh_save_status()

    def _clear_test_results(self):
        """释放旧配置的测试会话、结果窗口和本窗口预览引用。"""
        self.test_session.clear()
        self.test_dialogs.clear()
        self.actions['测试通知'].configure(text='测试通知', state='normal')
        self.result_text = ''
        self.preview_image = None
        self.preview_label.configure(image='', text='')
        for path in tuple(self._owned_previews):
            try:
                if self.session_dir.stat(follow_symlinks=False).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    continue
                path.unlink(missing_ok=True)
            except OSError:
                continue
            self._owned_previews.remove(path)

    def _refresh_saved_home(self):
        """同文档纯保存时仅刷新主页和保存标记，保留输入控件。"""
        self.visible_pages['主页'].refresh()
        self._refresh_save_status()

    def _auto_save_failed(self):
        """后台写盘失败时在当前页面显示不含异常详情的提示。"""
        messagebox.showerror('自动保存失败', '配置未保存，请检查输入或文件写入权限。草稿已保留。',
                             parent=self.root)

    def _refresh_save_status(self):
        """编辑时仅刷新保存标记，不重建正在输入的控件。"""
        self._invalidate_validation_if_stale()
        self.test_dialogs.mark_previous(self.model.document_generation, self.model.revision)
        path = self.model.document.path
        pending = self.model.dirty or bool(self.model.input_errors)
        suffix = '（外部冲突）' if self.controller.conflicted else '（未保存）' if pending else ''
        self.path_label.configure(text=f'{path or "未保存的新配置"}{suffix}')
        self.visible_pages['主页'].refresh_path()
        conflict = self.actions['处理冲突']
        if self.controller.conflicted:
            conflict.pack(side='left')
        else:
            conflict.pack_forget()

    def _poll(self):
        """每 50ms 在 Tk 线程消费一次队列。"""
        self.drain_results()
        self._sync_test_buttons()
        self._after_id = self.root.after(50, self._poll)

    def _sync_test_buttons(self):
        """按会话请求和 worker 回收状态同步三个真实测试入口。"""
        current = self.test_session.active_request
        action = current.action if current is not None else None
        busy = current is not None or self.controller.runner.active
        page = self.visible_pages.get('截图设置')
        if page is not None:
            page.capture_button.configure(state='normal' if not busy or action == 'capture' else 'disabled')
            page.upload_button.configure(state='normal' if not busy or action == 'upload' else 'disabled')
        self.actions['测试通知'].configure(
            text='发送中…' if action == 'notify' else '通知测试',
            state='disabled' if busy else 'normal')

    def _show_save_guidance(self):
        """未绑定路径时指导用户先完成首次保存，不启动选择器或业务动作。"""
        self.status.configure(text=SAVE_GUIDANCE_MESSAGE)
        messagebox.showinfo('首次保存', SAVE_GUIDANCE_MESSAGE, parent=self.root)

    def _require_saved(self):
        """在业务操作前补存，不把未保存草稿当作磁盘配置。"""
        if self.model.document.path is None:
            self._show_save_guidance()
            return False
        result = self.save_coordinator.flush_result()
        if result in (SaveResult.SAVED, SaveResult.SUPERSEDED):
            return True
        if result is SaveResult.RELOADED:
            messagebox.showinfo('配置已更新', '磁盘配置已重新加载，操作已停止，请重新选择。',
                                parent=self.root)
            return False
        if self.model.input_errors:
            self._show_incomplete_input()
            return False
        if self.controller.conflicted:
            self.status.configure(text='磁盘配置已变化，原文件保存已暂停')
            return False
        self.status.configure(text='配置未保存，操作已停止')
        messagebox.showerror('配置未保存', '配置未保存，操作已停止。草稿已保留。', parent=self.root)
        return False

    def _before_list_edit(self):
        """补存若替换草稿则停止列表操作并要求重新选择。"""
        if self.model.document.path is None:
            return True
        identity = (self.model.document_generation, self.model.revision)
        result = self.save_coordinator.flush_result()
        saved = result in (SaveResult.SAVED, SaveResult.SUPERSEDED)
        if identity != (self.model.document_generation, self.model.revision):
            message_page = self.visible_pages.get('消息推送设置')
            if message_page is not None:
                message_page._expanded_channel = None
                message_page.refresh()
            self.status.configure(text='配置已更新，请重新选择列表项')
            messagebox.showinfo('配置已更新', '配置已更新，请重新选择列表项。', parent=self.root)
            return False
        if not saved:
            self.status.configure(text='配置未保存，操作已停止')
            messagebox.showerror('配置未保存', '配置未保存，操作已停止。草稿已保留。', parent=self.root)
        return saved

    def _after_list_edit(self):
        """列表编辑写入内存后补存；未绑定路径时不触发任何写盘。"""
        if self.model.document.path is None:
            return
        self.save_coordinator.flush()

    def drain_results(self):
        """待 worker 回收后只接纳最近请求及当前文档代次的终态。"""
        if self.controller.runner.active:
            return
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
            if current.action == 'capture':
                for name in result.preview_paths:
                    path = Path(name)
                    if (path.parent == self.session_dir
                            and path.name in {current.request_id + suffix
                                              for suffix in FORMAT_SUFFIX.values()}):
                        self._owned_previews.add(path)
            self._sync_test_buttons()
            suffix = ('（对应先前配置）' if self.test_session.previous_generation else
                      '（对应先前草稿）' if self.test_session.previous_draft else '')
            self.result_text = f'{result.status}: {result.reason}{suffix}'
            self.status.configure(text=self.result_text)
            if current.action == 'notify':
                if result.status != 'success':
                    failures = [f'{item.index + 1} {item.provider}：{item.reason}'
                                for item in result.channel_results if item.status == 'failure']
                    messagebox.showerror('通知测试失败', '\n'.join(failures) or result.reason,
                                         parent=self.root)
                continue
            if not self.test_session.hidden:
                self.test_dialogs.show_result(current.action, result,
                                              previous=('config' if self.test_session.previous_generation
                                                        else self.test_session.previous_draft),
                                              retry_callback=(self.open_upload_selection
                                                              if current.action == 'upload'
                                                              else self.open_capture_selection))
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
        """由控制器确认旧草稿后建立无路径空白草稿。"""
        if self.controller.runner.active:
            self.status.configure(text='请等待测试完成或取消测试后切换配置')
            messagebox.showinfo('配置切换', '请等待测试完成或取消测试后切换配置', parent=self.root)
            return
        if self.controller.new():
            self._clear_test_results()
        elif self.controller.unsaved_input_blocked:
            self._show_incomplete_input()

    def open(self):
        """用原始选择路径读取，不隐式添加后缀。"""
        if self.controller.runner.active:
            self.status.configure(text='请等待测试完成或取消测试后切换配置')
            messagebox.showinfo('配置切换', '请等待测试完成或取消测试后切换配置', parent=self.root)
            return

        def picker():
            """由控制器暂停保存后再打开系统选择器。"""
            name = filedialog.askopenfilename(parent=self.root, filetypes=[('YAML', '*.yaml *.yml'), ('所有文件', '*.*')])
            return Path(name) if name else None

        result = self.controller._open_result(picker)
        if result is SaveResult.SAVED:
            self._clear_test_results()
        elif result is SaveResult.FAILED:
            messagebox.showerror('打开配置', '无法打开配置', parent=self.root)
        elif result is SaveResult.CANCELLED and self.controller.unsaved_input_blocked:
            self._show_incomplete_input()

    def save(self):
        """只通过控制器保存编辑文档，未完成输入先提示并定位字段。"""
        message_page = self.visible_pages.get('消息推送设置')
        if message_page is not None:
            message_page.sync_pending_edits()
        if self.model.input_errors:
            self._show_incomplete_input()
            return
        result = self.controller._transaction(self.controller._save_result)
        if result is SaveResult.CONFLICT:
            self.status.configure(text='磁盘配置已变化，原文件保存已暂停')
            return
        if result is SaveResult.RELOADED:
            self.status.configure(text='已重新加载磁盘配置')
            return
        if result is SaveResult.FAILED:
            self.status.configure(text='配置未保存')
            messagebox.showerror('保存配置', '配置未保存，请检查输入或文件写入权限。草稿已保留。',
                                 parent=self.root)

    def save_as(self):
        """选择另存为目标，由控制器规范化并检查冲突。"""
        if self.controller.runner.active:
            self.status.configure(text='请等待测试完成或取消测试后切换配置')
            messagebox.showinfo('配置切换', '请等待测试完成或取消测试后切换配置', parent=self.root)
            return
        if self.model.input_errors:
            self._show_incomplete_input()
            return
        result = self.controller._save_as_result(lambda: filedialog.asksaveasfilename(
            parent=self.root, defaultextension='.yaml') or None)
        if result is SaveResult.SAVED:
            self._clear_test_results()
        if result is SaveResult.FAILED:
            self.status.configure(text='配置未另存')
            messagebox.showerror('另存配置', '配置未保存，请检查输入或文件写入权限。草稿已保留。',
                                 parent=self.root)

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

        counts = {name: [0, 0] for name in self.page_names}
        by_page = {name: [] for name in self.page_names}
        for issue in candidate.issues:
            name = self._issue_page(issue)
            counts[name][0 if issue.severity == 'error' else 1] += 1
            by_page[name].append(issue)
        groups = [(name, error_count, other_count, by_page[name])
                  for name, (error_count, other_count) in counts.items()
                  if error_count + other_count]
        self._validated = (self.model.document_generation, self.model.revision)
        self.visible_pages['主页'].show_validation(len(candidate.issues), groups)
        self.status.configure(text=f'校验：{len(candidate.issues)} 个问题')
        errors = [issue for issue in candidate.issues if issue.severity == 'error']
        if errors:
            issue = errors[0]
            self.focus_validation_issue(self._issue_page(issue), issue)
        return candidate

    @staticmethod
    def _issue_page(issue):
        """把诊断路径归入承载页面，供校验与保存定位共用。"""
        path = issue.path
        if not path or getattr(issue, 'code', None) in ('root_type', 'legacy', 'mapping'):
            return '主页'
        if path[:3] == ('monitor', 'common', 'timeout_threshold') or path[0] == 'external':
            return '外部程序设置'
        if path[:2] == ('push', 'screenshot'):
            return '截图设置'
        return {'monitor': '监视设置', 'push': '消息推送设置',
                'log': '日志设置'}.get(path[0], '主页')

    def _show_incomplete_input(self):
        """主动保存被未完成数字阻断时提示并定位首个字段。"""
        messagebox.showerror('保存配置', INCOMPLETE_INPUT_MESSAGE, parent=self.root)
        issue = next(iter(self.model.input_errors.values()))
        self.focus_validation_issue(self._issue_page(issue), issue)
        self.status.configure(text=INCOMPLETE_INPUT_MESSAGE)

    def focus_validation_issue(self, name, issue):
        """从主页诊断行前往实际字段，隐藏字段只指向所属页。"""
        if not self.show_page(name):
            return
        if name == '主页':
            self.visible_pages[name].show_issue(getattr(issue, 'code', 'structure'))
            return
        page = self.visible_pages[name]
        path = tuple(issue.path)
        if path == ('monitor', 'launch', 'cwd') or path[-1:] == ('capture_screenshot',):
            self.status.configure(text=f'{name}：请在 YAML 中检查对应字段')
            return
        page.focus_issue(path)

    def start_cli(self):
        """反馈进程创建结果但不跟踪其生命周期。"""
        if self.model.document.path is None:
            self._show_save_guidance()
            return
        if self.model.input_errors:
            self._show_incomplete_input()
            return
        if self.controller.conflicted:
            self.status.configure(text='磁盘配置已变化，原文件保存已暂停')
            return
        generation = self.model.document_generation
        save_result = self.save_coordinator.flush_result()
        if save_result not in (SaveResult.SAVED, SaveResult.SUPERSEDED):
            if save_result is SaveResult.RELOADED:
                path = self.model.document.path
                candidate = self.model.validate(path.parent if path else self.controller.program_dir)
                if any(issue.severity == 'error' for issue in candidate.issues):
                    messagebox.showerror('启动 CLI', LAUNCH_FAILURE_MESSAGE, parent=self.root)
                else:
                    messagebox.showinfo('配置已更新', '磁盘配置已重新加载，操作已停止，请重新选择。',
                                        parent=self.root)
                return
            self.status.configure(text='配置未保存，操作已停止')
            if not self.controller.conflicted and self.model.document_generation == generation:
                messagebox.showerror('配置未保存', '配置未保存，操作已停止。草稿已保留。',
                                     parent=self.root)
            return
        generation = self.model.document_generation
        result = self.controller.start_cli()
        self.status.configure(text=result.message if result else '未启动 CLI')
        if result is None:
            if (not self.controller.cli_cancelled and not self.controller.conflicted
                    and self.model.document_generation == generation):
                messagebox.showerror('启动 CLI', LAUNCH_FAILURE_MESSAGE, parent=self.root)
            return
        popup = messagebox.showinfo if result.created else messagebox.showerror
        popup('启动 CLI', result.message, parent=self.root)

    def _request(self, action, *, target_index=None, host_index=None,
                 image_path=None, template_key=None, capture_provider=None,
                 capture_target=None, expected_identity=None, selection_unchanged=None):
        """主线程冻结草稿与绝对路径，后台只接收不可变请求。"""
        if not self._require_saved():
            return False
        identity = (self.model.document_generation, self.model.revision)
        if expected_identity is not None and identity != expected_identity:
            self.status.configure(text='配置已更新，请重新选择测试参数')
            return False
        if self.test_session.active_request is not None or self.controller.runner.active:
            self.status.configure(text='已有测试正在运行')
            messagebox.showinfo('测试进行中', '已有测试正在运行，请等待完成。', parent=self.root)
            return False
        if action in ('upload', 'notify') and not messagebox.askyesno(
                '真实测试', ('固定测试消息将发往全部配置通道，继续吗？' if action == 'notify'
                           else '图片将真实上传到选定图床，继续吗？'), parent=self.root):
            return False
        if action in ('upload', 'notify') and self.controller.check_external():
            self.status.configure(text='磁盘配置已更新，请先处理冲突')
            return False
        if identity != (self.model.document_generation, self.model.revision):
            self.status.configure(text='配置已更新，请重新选择测试参数')
            return False
        if selection_unchanged is not None and not selection_unchanged():
            self.status.configure(text='选择已更新，请重新确认测试参数')
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
            self._sync_test_buttons()
            return False
        self.status.configure(text='测试进行中')
        self._sync_test_buttons()
        if action != 'notify':
            self.test_dialogs.show_result(action)
        return True

    def capture(self):
        """打开手填来源与目标的选择窗，或恢复运行结果。"""
        if self.test_session.active_request is not None:
            if self.test_session.active_request.action == 'capture':
                self.test_session.hidden = False
                self.test_dialogs.show_result('capture')
            else:
                messagebox.showinfo('测试进行中', '已有测试正在运行，请等待完成。', parent=self.root)
            return
        if self.test_session.last_capture is not None:
            self.test_dialogs.show_result('capture', self.test_session.last_capture,
                                          previous=('config' if self.test_session.last_capture.document_generation
                                                    != self.model.document_generation else
                                                    self.test_session.last_capture_previous
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
        window = None

        def submit(provider, target):
            """截图目标输入期间的文档变更需重新选择。"""
            if window is not None and (not window.winfo_exists()
                                       or self.test_dialogs.selection_window is not window):
                return False
            if not self._require_saved():
                return False
            if identity != (self.model.document_generation, self.model.revision):
                self.status.configure(text='配置已更新，请重新选择截图目标')
                self.open_capture_selection()
                return False
            selection_unchanged = None
            if window is not None and window.winfo_exists():
                selection_unchanged = lambda: (
                    window.winfo_exists() and self.test_dialogs.selection_window is window
                    and self.test_dialogs.capture_provider.get() == {'window': '窗口', 'adb': 'ADB'}.get(provider)
                    and self.test_dialogs.capture_target.get().strip() == target)
            return self._request('capture', capture_provider=provider,
                                 capture_target=target, expected_identity=identity,
                                 selection_unchanged=selection_unchanged)

        opened = self.test_dialogs.open_capture(submit)
        if opened is self.test_dialogs.selection_window:
            window = opened

    def upload(self):
        """展示图床和本地图片选择，确认后仅发送选定图床。"""
        if self.test_session.active_request is not None:
            if self.test_session.active_request.action == 'upload':
                self.test_session.hidden = False
                self.test_dialogs.show_result('upload')
            else:
                messagebox.showinfo('测试进行中', '已有测试正在运行，请等待完成。', parent=self.root)
            return
        if self.test_session.last_upload is not None:
            self.test_dialogs.show_result('upload', self.test_session.last_upload,
                                          previous=('config' if self.test_session.last_upload.document_generation
                                                    != self.model.document_generation else
                                                    self.test_session.last_upload_previous
                                                    or self.test_session.last_upload.revision
                                                    != self.model.revision),
                                          retry_callback=self.open_upload_selection)
            return
        self.open_upload_selection()

    def open_upload_selection(self):
        """配置变动后重开新图床列表，不复用旧索引。"""
        if not self._require_saved():
            return
        try:
            hosts = self.model.get(('push', 'screenshot', 'image_host'))
        except (KeyError, IndexError):
            hosts = []
        except TypeError:
            self.status.configure(text='图床配置结构无效，请在 YAML 中修复')
            return
        if not isinstance(hosts, list):
            self.status.configure(text='图床配置结构无效，请在 YAML 中修复')
            return
        if not hosts:
            self.status.configure(text='请先添加图床后再测试')
            messagebox.showinfo('上传测试', '请先添加图床后再测试', parent=self.root)
            return
        names = [item.get('provider', '未知') if isinstance(item, dict) else '未知'
                 for item in hosts]
        identity = (self.model.document_generation, self.model.revision, hosts)
        window = None

        def submit(index, image):
            """复核重选时的列表内容与修订。"""
            if window is not None and (not window.winfo_exists()
                                       or self.test_dialogs.selection_window is not window):
                return False
            if not self._require_saved():
                return False
            if window is not None and (not window.winfo_exists()
                                       or self.test_dialogs.selection_window is not window):
                return False
            if (identity != (self.model.document_generation, self.model.revision,
                             self.model.get(('push', 'screenshot', 'image_host')))
                    or index >= len(hosts)):
                self.status.configure(text='配置已更新，请重新选择图床')
                self.open_upload_selection()
                return False
            selection_unchanged = None
            if window is not None and window.winfo_exists():
                selected = self.test_dialogs.upload_host.get()
                selection_unchanged = lambda: (window.winfo_exists()
                                                and self.test_dialogs.selection_window is window
                                                and selected == f'{index + 1:02d} {names[index]}'
                                                and self.test_dialogs.upload_host.get() == selected
                                                and self.test_dialogs.upload_image.get().strip() == image)
            started = self._request('upload', host_index=index, image_path=image,
                                    expected_identity=identity[:2],
                                    selection_unchanged=selection_unchanged)
            if not started and identity[:2] != (self.model.document_generation,
                                                self.model.revision):
                self.open_upload_selection()
                self.status.configure(text='配置已更新，请重新选择图床')
            return started

        opened = self.test_dialogs.open_upload(names, submit)
        if opened is self.test_dialogs.selection_window:
            window = opened

    def notify(self):
        """通知测试无通道提前阻止，不保留可重开的结果。"""
        if self.test_session.active_request is not None or self.controller.runner.active:
            self.status.configure(text='已有测试正在运行')
            messagebox.showinfo('测试进行中', '已有测试正在运行，请等待完成。', parent=self.root)
            return
        if not self._require_saved():
            return
        try:
            channels = self.model.get(('push', 'push_channel_settings', 'channels'))
        except (KeyError, IndexError):
            channels = []
        except TypeError:
            self.status.configure(text='推送通道配置结构无效，请在 YAML 中修复')
            return
        if not channels:
            self.status.configure(text='请先添加推送通道')
            messagebox.showinfo('通知测试', '请先添加推送通道', parent=self.root)
            return
        self._request('notify', template_key='on_end', expected_identity=(
            self.model.document_generation, self.model.revision))

    def cancel_test(self):
        """取消仅针对截图或上传 worker，不影响 CLI。"""
        current = self.test_session.active_request
        if current is not None and current.action != 'notify':
            self.test_session.cancel()

    def close(self):
        """处理未保存草稿并正常关闭测试运行器。"""
        if getattr(self, '_closing', False):
            return
        self._closing = True
        try:
            if self.controller.close():
                self.test_dialogs.clear()
                self.save_shortcuts.close()
                self.save_coordinator.close()
                self.root.after_cancel(self._after_id)
                threading.Thread(target=shutil.rmtree, args=(self.session_dir,),
                                 kwargs={'ignore_errors': True}, name='gui-preview-cleanup').start()
                self.root.destroy()
            elif self.controller.unsaved_input_blocked:
                self._show_incomplete_input()
        finally:
            self._closing = False
