> [!CAUTION]
> 本文档使用 GPT AI 辅助编写，GPT 模型：o1-preview

# 2RPM - 使用说明

介绍如何使用 2RPM，包括安装、配置、运行和打包等步骤。

适用于：[2RPM-v2](.\2RPM-v2.py)

---

## 目录

1. [2RPM - 使用说明](#2rpm---使用说明)
   1. [目录](#目录)
   2. [1. 程序用途](#1-程序用途)
   3. [2. 下载并安装 Python](#2-下载并安装-python)
   4. [3. 安装依赖库](#3-安装依赖库)
   5. [4. 配置脚本](#4-配置脚本)
   6. [5. 测试运行脚本](#5-测试运行脚本)
   7. [6. 打包 exe 文件](#6-打包-exe-文件)
      1. [6.1 安装 PyInstaller](#61-安装-pyinstaller)
      2. [6.2 打包为有控制台输出的 exe 文件](#62-打包为有控制台输出的-exe-文件)
      3. [6.3 打包为无控制台输出的 exe 文件](#63-打包为无控制台输出的-exe-文件)
      4. [6.4 注意事项](#64-注意事项)
   8. [7. 通过批处理文件运行程序](#7-通过批处理文件运行程序)
      1. [7.1 通过批处理文件运行源代码(不推荐)](#71-通过批处理文件运行源代码不推荐)
   9. [附录 1：开发文档](#附录-1开发文档)
      1. [代码结构](#代码结构)
      2. [日志系统](#日志系统)
      3. [异常处理](#异常处理)
      4. [信号处理](#信号处理)
   10. [单元测试](#单元测试)
       1. [测试内容](#测试内容)
       2. [运行单元测试](#运行单元测试)
       3. [附录 2：示例单元测试代码](#附录-2示例单元测试代码)
       4. [说明](#说明)

---

## 1. 程序用途

2RPM 程序用于监视指定的进程，当以下情况发生时发送通知：

- **进程未运行**：在指定的等待时间内未检测到目标进程启动。
- **进程运行时间过长**：进程的运行时间超过了设定的警告间隔。
- **进程结束**：监视的进程意外终止或正常结束。

现已支持：

- 支持配置文件运行，方便维护多配置。
- 支持命令行参数调用，使用 '-c' 命令调用配置文件运行。
- 可指定**特定进程结束时**执行外部程序。
- 支持通过 **ServerCha** 或 **OnePush** 进行消息推送，方便及时了解进程状态。

---

## 2. 下载并安装 Python

1. **下载 Python 安装包**

   - 访问 [Python 官方网站](https://www.python.org/downloads/)。
   - 下载适用于您操作系统的最新版本 Python 3 安装包。

2. **安装 Python**

   - 运行下载的安装包。
   - 在安装过程中，**勾选** “Add Python 3.x to PATH” （将 Python 添加到环境变量）。
   - 选择 “Install Now” 进行默认安装。

3. **验证安装**

   安装完成后，再次在命令行中输入：

   ```bash
   python --version
   ```

   若能看到 Python 的版本信息，则表示安装成功。

PS.**请注意：Python 版本 不能低于 3.6**

---

## 3. 安装依赖库

本程序依赖于：

- **colorama**: 用于在控制台输出彩色日志。
- **psutil**: 用于获取和监控系统进程信息。
- **ruamel.yaml**: 用于处理 YAML 配置文件。
- **onepush**: 用于通过 OnePush 发送推送通知。
- **serverchan_sdk**: 用于通过 ServerChan 发送推送通知。

请按照以下步骤安装依赖库：

1. **打开命令行或终端**

   - 按 `Win + R`，输入 `cmd`，然后按回车。

2. **下载该版本目录内的 `requirements.txt` 文件**

3. **安装依赖**  
   在项目根目录下运行以下命令安装所有依赖：

   ```bash
   pip install -r requirements.txt
   ```

   如果提示 `pip` 未安装，可以尝试使用 `python -m pip install -r requirements.txt`。

4. **验证安装**

   安装完成后，输入以下命令检查是否安装成功：

   ```bash
   pip show psutil colorama ruamel.yaml onepush serverchan_sdk
   ```

   如果显示了 `psutil`、`colorama`、`ruamel.yaml`、`onepush`、`serverchan_sdk` 的版本信息，表示安装成功。

---

## 4. 配置脚本

程序的配置文件采用 YAML 格式，默认文件名为 `config.yaml`。  
程序启动时会加载该配置文件，并根据其中的参数执行相应的监控和通知操作。

配置文件包含以下主要部分：

- `monitor_settings`: 监视设置
- `wait_process_settings`: 等待进程设置
- `push_settings`: 推送设置
- `external_program_settings`: 外部程序调用设置
- `log_settings`: 日志设置

详细请查阅 `配置文件` 内的注释

---

## 5. 测试运行脚本

在终端中，运行以下命令：

```bash
python C:\path\2RPM.py
```

---

## 6. 打包 exe 文件

建议使用 PyInstaller 将 2RPM 打包为可执行的 exe 文件，方便直接调用运行。

### 6.1 安装 PyInstaller

在命令行或终端中运行以下命令安装 PyInstaller：

```bash
pip install pyinstaller
```

### 6.2 打包为有控制台输出的 exe 文件

如果您希望在运行 exe 文件时看到控制台输出，请按照以下步骤：

1. **导航到脚本目录**

   ```bash
   cd path\to\your\script
   ```

2. **运行 PyInstaller 命令**

   ```Python
   pyinstaller -F -c 2RPM.py -n 2RPM使用控制台
   ```

3. **找到生成的 exe 文件**

   打包完成后，生成的 exe 文件位于 `dist` 目录下，名为 `2RPM.exe`。

### 6.3 打包为无控制台输出的 exe 文件

如果您希望脚本在后台运行，不显示控制台窗口，请使用以下命令：

```Python
pyinstaller -F -w 2RPM.py -n 2RPM不显示命令行窗口
```

这样生成的 exe 文件在运行时不会弹出控制台窗口。

### 6.4 注意事项

- **确保所有依赖库都已安装**

  在打包前，确保您已安装了所有必要的依赖库，例如 `psutil`。

- **更新脚本路径**

  如果脚本中有涉及到相对路径的配置，请确保在打包后路径依然正确。

- **测试 exe 文件**

  打包完成后，建议在目标环境中测试 exe 文件，确保其能够正常运行。

---

## 7. 通过批处理文件运行程序

示例 BAT 文件内容：

```bat
@echo off
set "RPM=C:\T O\2RPM使用控制台.exe"
set "config=C:\T O\my_config.yaml"

start "" "%RPM%" -c "%config%"

exit /b
```

### 7.1 通过批处理文件运行源代码(不推荐)

不建议通过批处理文件运行脚本，若多 Python 版本共存或更新 Python 版本，或者其他不可预知的问题（例如：Python 无法识别依赖库）会导致无法运行脚本。

```bat
@echo off
start "C:\Windows\System32\cmd.exe"
python x:\2RPM.py -c my_config.yaml
taskkill /f /im cmd.exe
exit
```

若有空格请使用该方案：

```bat
@echo off
set "RPM=C:\T O\2RPM.py"
set "config=C:\T O\my config.yaml"
start "C:\Windows\System32\cmd.exe"
python "%RPM%" -c "%config%"
taskkill /f /im cmd.exe
exit
```

---

## 附录 1：开发文档

### 代码结构

- **主函数 `main`**: 程序的入口，负责初始化配置和日志，并启动监视器。
- **`monitor_processes`**: 异步函数，负责监视进程的启动和运行状态。
- **`send_notification`**: 发送通知的函数，支持 ServerChan 和 OnePush。
- **`run_external_program`**: 在特定进程结束时执行外部程序。
- **配置管理函数**: 包括 `get_default_config`、`create_default_config`、`load_config`、`check_and_update_config` 等，用于管理配置文件。
- **日志管理函数**: 包括 `setup_logging` 和 `clean_logs`，用于配置和维护日志系统。

### 日志系统

- **日志级别**: DEBUG > INFO > WARNING > ERROR > CRITICAL
- **控制台输出**: 支持彩色日志输出（需要安装 `colorama`）。
- **日志文件**: 使用旋转日志文件机制，按大小和数量进行管理。
- **日志目录**: 默认在程序根目录下的 `logs` 文件夹。

### 异常处理

程序在关键步骤添加了异常处理机制，确保在发生错误时能够记录详细的日志，并根据情况退出程序或进行重试。

### 信号处理

程序支持捕获中断信号（如 `Ctrl+C`），确保在收到中断信号后能够优雅地退出。

## 单元测试

单元测试使用 Python 的 `unittest` 模块编写，确保程序的关键功能正常运行。

### 测试内容

- **监视进程启动**: 测试当指定进程未启动时，程序是否正确超时退出。
- **监视进程运行**: 测试当指定进程启动并运行时，程序是否正确监控其状态。
- **时间格式化函数**: 测试时间格式化函数的正确性。

### 运行单元测试

1. **确保已安装所有依赖**

   ```bash
   pip install -r requirements.txt
   ```

2. **运行测试**

   在项目根目录下运行以下命令：

   ```bash
   python -m unittest discover
   ```

   > **说明**: `unittest` 将自动发现并运行项目中的测试用例。

### 附录 2：示例单元测试代码

以下是一个示例的单元测试文件，命名为 `test_process_monitor.py`：

```python
import unittest
from unittest.mock import patch, MagicMock
import asyncio
import time

# 假设上述代码的模块名为 process_monitor
# from process_monitor import (
#     monitor_processes, format_time_ns, CONFIG, get_default_config
# )

class TestProcessMonitor(unittest.TestCase):

    def setUp(self):
        """在每个测试用例之前运行，初始化配置。"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        # 设置默认配置
        global CONFIG
        CONFIG = get_default_config()
        # 禁用日志文件输出以防止影响测试环境
        CONFIG['log_settings']['enable_log_file'] = False

    def tearDown(self):
        """在每个测试用例之后运行，关闭事件循环。"""
        self.loop.close()

    @patch('psutil.process_iter')
    def test_monitor_processes_no_processes(self, mock_process_iter):
        """测试当没有指定的进程时，监视器是否正确退出。"""
        mock_process_iter.return_value = []
        with self.assertRaises(SystemExit):
            self.loop.run_until_complete(monitor_processes())

    @patch('psutil.process_iter')
    def test_monitor_processes_with_processes(self, mock_process_iter):
        """测试当有指定的进程时，监视器是否正确运行。"""
        process_mock = MagicMock()
        process_mock.pid = 1234
        process_mock.info = {
            'pid': 1234,
            'name': 'notepad.exe',
            'create_time': time.time()
        }

        mock_process_iter.return_value = [process_mock]
        # 为了使测试不无限循环，设置一个短的超时时间
        CONFIG['monitor_settings']['monitor_loop_interval_ms'] = 100
        CONFIG['wait_process_settings']['max_wait_time_ms'] = 100
        CONFIG['monitor_settings']['timeout_warning_interval_ms'] = 500
        try:
            self.loop.run_until_complete(
                asyncio.wait_for(monitor_processes(), timeout=1)
            )
        except asyncio.TimeoutError:
            pass  # 超时退出，表示监视器正在运行

    def test_format_time_ns(self):
        """测试时间格式化函数。"""
        nanoseconds = 3661000000000  # 1 hour, 1 minute, 1 second
        expected = "01:01:01.000"
        result = format_time_ns(nanoseconds)
        self.assertEqual(result, expected)

if __name__ == '__main__':
    unittest.main()
```

### 说明

- **`setUp`**: 每个测试用例运行前执行，初始化事件循环和配置。
- **`tearDown`**: 每个测试用例运行后执行，关闭事件循环。
- **`test_monitor_processes_no_processes`**: 测试当没有指定进程时，程序是否正确超时退出。
- **`test_monitor_processes_with_processes`**: 测试当指定进程启动时，程序是否正确监控其状态。
- **`test_format_time_ns`**: 测试时间格式化函数的正确性。

> **注意**: 请根据实际模块名称调整导入语句中的 `process_monitor`。

---
