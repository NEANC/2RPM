#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Tk 主线程窗口、侧栏导航及隔离测试结果消费。"""

from collections.abc import Mapping
import ctypes
from ctypes import wintypes
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
from ctk_kit.styles import style_of

from modules.gui.appearance import (AppearancePreferences, COLORS, apply_appearance,
                                    FONT_BODY, FONT_BODY_BOLD, FONT_BRAND, FONT_GROUP,
                                    FONT_SMALL, load_preferences, preferences_path,
                                    save_preferences)
from modules.gui.controller import SaveResult
from modules.gui.pages import (external, home, logs, monitor, notification_ctk,
                               screenshot, settings)
from modules.gui.protocol import TestRequest, encode_request
from modules.gui.save_coordinator import SaveCoordinator
from modules.gui.test_dialogs import TestDialogs
from modules.gui.test_session import TestSession


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
        self.model.on_edit = self.save_coordinator.edited
        self.model.before_list_edit = self._before_list_edit
        self.model.on_list_edit = self.save_coordinator.flush
        self.test_session = TestSession(controller.runner)
        self.test_dialogs = TestDialogs(root, self.test_session)
        self.generation = self.model.document_generation
        self.document_path = self.model.document.path
        self._validated = None
        self.result_text = ''
        self.preview_image = None
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
            if not self.save_coordinator.flush():
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
                self.test_session.clear()
                self.test_dialogs.clear()
                self.actions['测试通知'].configure(text='测试通知', state='normal')
                self.result_text = ''
                self.preview_image = None
                self.preview_label.configure(image='', text='')
            message_page = self.visible_pages.get('消息推送设置')
            if message_page is not None:
                message_page._expanded_channel = None
                message_page._expanded_events.clear()
            self.generation = self.model.document_generation
            self.document_path = self.model.document.path
        for page in self.visible_pages.values():
            if hasattr(page, 'refresh'):
                page.refresh()
        self._refresh_save_status()

    def _refresh_saved_home(self):
        """同文档纯保存时仅刷新主页和保存标记，保留输入控件。"""
        self.visible_pages['主页'].refresh()
        self._refresh_save_status()

    def _refresh_save_status(self):
        """编辑时仅刷新保存标记，不重建正在输入的控件。"""
        self._invalidate_validation_if_stale()
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
        self._after_id = self.root.after(50, self._poll)

    def _require_saved(self):
        """在业务操作前补存，不把未保存草稿当作磁盘配置。"""
        if self.save_coordinator.flush():
            return True
        self.status.configure(text='配置未保存，操作已停止')
        messagebox.showerror('配置未保存', '配置未保存，操作已停止。草稿已保留。', parent=self.root)
        return False

    def _before_list_edit(self):
        """补存若替换草稿则停止列表操作并要求重新选择。"""
        identity = (self.model.document_generation, self.model.revision)
        saved = self.save_coordinator.flush()
        if identity != (self.model.document_generation, self.model.revision):
            message_page = self.visible_pages.get('消息推送设置')
            if message_page is not None:
                message_page._expanded_channel = None
                message_page.refresh()
            self.status.configure(text='配置已更新，请重新选择列表项')
            return False
        if not saved:
            self.status.configure(text='配置未保存，操作已停止')
            messagebox.showerror('配置未保存', '配置未保存，操作已停止。草稿已保留。', parent=self.root)
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
        result = self.controller._save_result()
        if result is SaveResult.CONFLICT:
            self.status.configure(text='磁盘配置已变化，原文件保存已暂停')
            return
        if result is SaveResult.FAILED:
            self.status.configure(text='配置未保存')
            messagebox.showerror('保存配置', '配置未保存，请检查输入或文件写入权限。草稿已保留。',
                                 parent=self.root)

    def save_as(self):
        """选择另存为目标，由控制器规范化并检查冲突。"""
        if self.controller.runner.active:
            self.status.configure(text='请等待测试完成或取消测试后切换配置')
            return
        result = self.controller._save_as_result(lambda: filedialog.asksaveasfilename(
            parent=self.root, defaultextension='.yaml') or None)
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
            name = destination(issue)
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
            self.focus_validation_issue(destination(issue), issue)
        return candidate

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
        if not self._require_saved():
            return
        result = self.controller.start_cli()
        self.status.configure(text=result.message if result else '未启动 CLI')
        if result is None:
            if not self.controller.cli_cancelled:
                messagebox.showerror('启动 CLI',
                                     '未能启动 CLI，请检查配置、磁盘文件或保存状态。', parent=self.root)
            return
        popup = messagebox.showinfo if result.created else messagebox.showerror
        popup('启动 CLI', result.message, parent=self.root)

    def _request(self, action, *, target_index=None, host_index=None,
                 image_path=None, template_key=None, capture_provider=None,
                 capture_target=None, expected_identity=None):
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
            else:
                messagebox.showinfo('测试进行中', '已有测试正在运行，请等待完成。', parent=self.root)
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
            else:
                messagebox.showinfo('测试进行中', '已有测试正在运行，请等待完成。', parent=self.root)
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
            started = self._request('upload', host_index=index, image_path=image,
                                    expected_identity=identity[:2])
            if not started and identity[:2] != (self.model.document_generation,
                                                self.model.revision):
                self.open_upload_selection()
                self.status.configure(text='配置已更新，请重新选择图床')
            return started

        self.test_dialogs.open_upload(names, submit)

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
        if self.controller.close():
            self.test_dialogs.clear()
            self.save_coordinator.close()
            self.root.after_cancel(self._after_id)
            threading.Thread(target=shutil.rmtree, args=(self.session_dir,),
                             kwargs={'ignore_errors': True}, name='gui-preview-cleanup').start()
            self.root.destroy()
