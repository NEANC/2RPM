#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 独立入口与内部测试 worker 早期分流。"""

import argparse
import queue
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """在导入图形界面及业务模块前分流内部 worker。"""
    args = sys.argv[1:] if argv is None else argv
    if args == ['--internal-gui-worker']:
        from modules.gui.worker import main as worker_main
        return worker_main()
    from tkinter import filedialog, messagebox

    import ctk_kit

    from modules.config_document import normalize_config_path, new_document
    from modules.gui.app import App
    from modules.gui.build_identity import BUILD_TAG, BUILD_TOOL
    from modules.gui.controller import Controller
    from modules.gui.launcher import CliLocationError, resolve_cli_command
    from modules.gui.model import EditorModel
    from modules.gui.test_runner import TestRunner

    parser = argparse.ArgumentParser(description='2RPM 本地配置窗口')
    parser.add_argument('-c', '--config', type=Path)
    options = parser.parse_args(args)
    program_dir = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    root = ctk_kit.App(title='2RPM 配置', remember_geometry=False,
                       remember_theme=False, notifier=False, backdrop='none')

    def confirm(kind, detail):
        """把控制器的固定确认类型映射到原生对话框。"""
        if kind == 'unsaved':
            response = messagebox.askyesnocancel('未保存修改', '保存当前配置？否将放弃修改。', parent=root)
            return 'save' if response is True else 'discard' if response is False else 'cancel'
        if kind == 'normalize':
            text = '启动前须应用配置规范化差异：\n' + '\n'.join(
                f'{".".join(map(str, change["path"]))} ({change["operation"]}): '
                f'{change["before"]} → {change["after"]}' for change in detail) + '\n继续？'
        else:
            text = {'relocate': f'路径基准将改变：{detail}，继续？',
                    'overwrite': f'确认覆盖：{detail}？',
                    'repeat_launch': '再次启动会创建另一独立 CLI 控制台，继续？'}[kind]
        return 'confirm' if messagebox.askyesno('确认操作', text, parent=root) else 'cancel'

    try:
        command = resolve_cli_command(executable=Path(sys.executable),
                                      frozen=bool(getattr(sys, 'frozen', False)),
                                      project_dir=program_dir, build_tool=BUILD_TOOL,
                                      build_tag=BUILD_TAG)
    except CliLocationError:
        command = ()
    worker_command = ((str(Path(sys.executable).resolve()), '--internal-gui-worker')
                      if getattr(sys, 'frozen', False) else
                      (str(Path(sys.executable).resolve()), str(Path(__file__).resolve()), '--internal-gui-worker'))
    runner = TestRunner(worker_command, queue.Queue(), worker_cwd=program_dir)
    controller = Controller(EditorModel(new_document()), confirm=confirm,
                            choose_path=lambda: Path(name) if (name := filedialog.asksaveasfilename(parent=root)) else None,
                            cli_command=command, runner=runner, program_dir=program_dir)
    App(root, controller)
    if options.config is not None:
        path = normalize_config_path(options.config, program_dir=program_dir)
        if not controller.open(path):
            messagebox.showerror('配置读取失败', '无法读取指定配置文档', parent=root)
    root.mainloop()
    runner.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
