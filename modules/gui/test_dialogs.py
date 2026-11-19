#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""ctk-kit 测试选择窗与可恢复的结果窗。"""


import tkinter as tk

import ctk_kit
from PIL import Image

from modules.gui.appearance import FONT_BODY


class TestDialogs:
    """选择与结果分别展示，关闭结果不影响后台测试。"""

    def __init__(self, root, session):
        """绑定父窗口和测试会话。"""
        self.root = root
        self.session = session
        self.selection_window = None
        self.result_window = None
        self.result_content = None
        self.result_label = None
        self.displayed_result = None
        self.result_image = None
        self.result_preview = None
        self.retry = None
        self.cancel_button = None
        self._capture_callback = None
        self._upload_callback = None
        self._host_names = []
        self._capture_retry_callback = None
        self._upload_retry_callback = None

    def _window(self, title):
        """创建窗口居中的 ctk-kit 可见控件容器。"""
        window = ctk_kit.CTkToplevel(self.root)
        window.title(title)
        window.geometry('440x300')
        window.transient(self.root)
        window.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - 440) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - 300) // 2
        window.geometry(f'+{x}+{y}')
        return window

    def _selection(self, title):
        """选择窗口只保留一次实例。"""
        if self.selection_window is not None and self.selection_window.winfo_exists():
            self.selection_window.destroy()
        window = self._window(title)
        self.selection_window = window
        window.protocol('WM_DELETE_WINDOW', window.destroy)
        ctk_kit.CTkButton(window, text='×', width=32, command=window.destroy,
                          font=FONT_BODY).pack(anchor='ne', padx=12, pady=8)
        return window

    def open_capture(self, callback=None):
        """以手填窗口标题或 ADB 地址提交截图目标。"""
        self._capture_callback = callback
        window = self._selection('截图测试')
        self.capture_provider = ctk_kit.CTkOptionMenu(
            window, values=['窗口', 'ADB'], command=self._capture_source_changed,
            font=FONT_BODY, dropdown_font=FONT_BODY)
        self.capture_provider.pack(fill='x', padx=24, pady=8)
        self.capture_provider.set('窗口')
        self.capture_target = ctk_kit.CTkEntry(window, placeholder_text='请输入窗口标题',
                                               font=FONT_BODY)
        self.capture_target.pack(fill='x', padx=24, pady=8)
        self.capture_target_value = tk.StringVar(master=window)
        self.capture_target.configure(textvariable=self.capture_target_value)
        ctk_kit.CTkButton(window, text='取消', command=window.destroy,
                          font=FONT_BODY).pack(side='right', padx=16, pady=12)
        self.capture_start = ctk_kit.CTkButton(
            window, text='开始测试', command=self.submit_capture, font=FONT_BODY)
        self.capture_start.pack(side='right', pady=12)
        self.capture_target_value.trace_add('write', lambda *_: self._update_capture_start())
        self._update_capture_start()
        window.grab_set()
        return window

    def _capture_source_changed(self, source):
        """按选择来源显示窗口标题或 ADB 地址输入提示。"""
        self.capture_target.configure(placeholder_text=('请输入 ADB 地址'
                                                    if source == 'ADB' else '请输入窗口标题'))
        self._update_capture_start()

    def _update_capture_start(self):
        """来源与目标均有效时才允许截图测试。"""
        ready = (self.capture_provider.get() in ('窗口', 'ADB')
                 and bool(self.capture_target.get().strip()))
        self.capture_start.configure(state='normal' if ready else 'disabled')

    def submit_capture(self):
        """不完整的选择不能进入 worker。"""
        provider = {'窗口': 'window', 'ADB': 'adb'}.get(self.capture_provider.get())
        target = self.capture_target.get().strip()
        if provider is None or not target:
            return
        window = self.selection_window
        if self._capture_callback(provider, target):
            window.destroy()

    def open_upload(self, hosts=None, callback=None):
        """展示当前图床顺序与本地图片选择。"""
        self._upload_callback = callback
        self._host_names = [f'{index:02d} {name}' for index, name in enumerate(hosts or [], 1)]
        window = self._selection('上传测试')
        self.upload_host = ctk_kit.CTkOptionMenu(
            window, values=self._host_names or ['请选择图床'],
            command=lambda _value: self._update_upload_start(), font=FONT_BODY,
            dropdown_font=FONT_BODY)
        self.upload_host.pack(fill='x', padx=24, pady=8)
        self.upload_host.set('请选择图床')
        self.upload_image = ctk_kit.PathField(
            window, mode='file', title='选择本地图片',
            placeholder='请选择 PNG/JPG/JPEG 图片',
            on_change=lambda _path: self._update_upload_start())
        self.upload_image.pack(fill='x', padx=24, pady=8)
        ctk_kit.CTkButton(window, text='取消', command=window.destroy,
                          font=FONT_BODY).pack(side='right', padx=16, pady=12)
        self.upload_start = ctk_kit.CTkButton(
            window, text='开始测试', command=self.submit_upload, font=FONT_BODY)
        self.upload_start.pack(side='right', pady=12)
        self._update_upload_start()
        window.grab_set()
        return window

    def _update_upload_start(self):
        """图床与图片路径均已选择时才允许上传测试。"""
        ready = (self.upload_host.get() in self._host_names
                 and bool(self.upload_image.get().strip()))
        self.upload_start.configure(state='normal' if ready else 'disabled')

    def submit_upload(self):
        """仅将此刻有效的图床索引和图片传给调用者。"""
        selected = self.upload_host.get()
        image = self.upload_image.get().strip()
        if selected not in self._host_names or not image:
            return
        window = self.selection_window
        if self._upload_callback(self._host_names.index(selected), image):
            window.destroy()

    def show_result(self, action, result=None, *, previous=False, retry_callback=None):
        """恢复同一结果窗并显示真实图片或真实回调地址。"""
        if retry_callback is not None:
            if action == 'capture':
                self._capture_retry_callback = retry_callback
            else:
                self._upload_retry_callback = retry_callback
        if self.result_window is None or not self.result_window.winfo_exists():
            window = self._window('测试结果')
            self.result_window = window
            window.protocol('WM_DELETE_WINDOW', self.hide_result)
            ctk_kit.CTkButton(window, text='×', width=32, command=self.hide_result,
                              font=FONT_BODY).pack(anchor='ne', padx=12, pady=8)
            actions = ctk_kit.CTkFrame(window, fg_color='transparent')
            actions.pack(side='bottom', fill='x')
            self.result_content = ctk_kit.CTkScrollableFrame(window, fg_color='transparent')
            self.result_content.pack(fill='both', expand=True)
            self.result_label = ctk_kit.CTkLabel(self.result_content, text='', font=FONT_BODY,
                                                  wraplength=390)
            self.result_label.pack(padx=18, pady=8)
            self.result_preview = ctk_kit.CTkLabel(self.result_content, text='', font=FONT_BODY)
            self.result_preview.pack(padx=18, pady=8)
            self.cancel_button = ctk_kit.CTkButton(actions, text='取消测试',
                                                     command=self.session.cancel, font=FONT_BODY)
            self.cancel_button.pack(side='right', padx=18, pady=10)
            self.retry = ctk_kit.CTkButton(actions, text='重新测试', font=FONT_BODY)
            self.retry.pack(side='right', padx=8, pady=10)
        self.result_window.deiconify()
        self.result_window.lift()
        self.result_image = None
        self.result_preview.configure(image='', text='')
        if result is None:
            self.displayed_result = None
            self.result_label.configure(text='测试进行中')
            self.retry.pack_forget()
            self.cancel_button.pack(side='right', padx=18, pady=10)
            return self.result_window
        self.cancel_button.pack_forget()
        self.displayed_result = result
        self.retry.configure(command=lambda: self._retry(action))
        self.retry.pack(side='right', padx=8, pady=10)
        suffix = ('（对应先前配置）' if previous == 'config' else
                  '（对应先前草稿）' if previous else '')
        if result.status == 'success':
            text = ('上传成功\n回调地址：' + result.upload_url
                    if action == 'upload' else '截图成功')
        else:
            text = ('上传失败' if action == 'upload' else '截图失败') + '\n问题：' + result.reason
        if result.status != 'success':
            text += ('\n解决方案：请检查截图来源和目标后重试。'
                     if action == 'capture' else
                     '\n解决方案：请检查图床配置和网络连接后重试。')
        self.result_label.configure(text=text + suffix)
        if action == 'capture' and result.preview_paths:
            try:
                with Image.open(result.preview_paths[0]) as image:
                    preview = image.copy()
                preview.thumbnail((360, 140))
                self.result_image = ctk_kit.CTkImage(light_image=preview, dark_image=preview,
                                                      size=preview.size)
                self.result_preview.configure(image=self.result_image)
            except (OSError, ValueError):
                self.result_preview.configure(text='无法显示截图图片')
        return self.result_window

    def mark_previous(self, generation, revision):
        """可见旧结果在草稿编辑或同路径重载后更新归属文案。"""
        result = self.displayed_result
        if (result is None or self.result_window is None
                or not self.result_window.winfo_viewable()):
            return
        text = self.result_label.cget('text')
        if result.document_generation != generation:
            suffix = '（对应先前配置）'
        elif result.revision != revision:
            suffix = '（对应先前草稿）'
        else:
            return
        text = text.removesuffix('（对应先前草稿）').removesuffix('（对应先前配置）')
        self.result_label.configure(text=text + suffix)

    def _retry(self, action):
        """重新测试返回选择页而非重复发送旧参数。"""
        self.hide_result()
        callback = (self._capture_retry_callback if action == 'capture'
                    else self._upload_retry_callback)
        if callback is not None:
            callback()
        elif action == 'capture':
            self.open_capture(self._capture_callback)
        else:
            self.open_upload([item.split(' ', 1)[1] for item in self._host_names],
                             self._upload_callback)

    def hide_result(self):
        """仅隐藏结果，不取消 worker。"""
        if self.result_window is not None and self.result_window.winfo_exists():
            self.result_window.withdraw()
        self.session.hide()

    def clear(self):
        """配置切换时销毁旧窗口与图片引用。"""
        for window in (self.selection_window, self.result_window):
            if window is not None and window.winfo_exists():
                window.destroy()
        self.selection_window = self.result_window = self.result_content = None
        self.displayed_result = None
        self.result_image = None
        self._capture_retry_callback = None
        self._upload_retry_callback = None
