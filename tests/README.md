# GUI 测试分片

真实 Tk 窗口测试按「页面 / 关注点」拆成小文件，单个文件可独立运行，不必每次跑完整套。
所有用例只依赖 `tests/gui_support.py` 的 `make_app` 与 `tests/conftest.py` 的 Tk 默认根重置。

| 文件 | 覆盖范围 |
| --- | --- |
| `test_gui_window.py` | 系统标题栏、主页/配置页留白、侧栏、滚动与关闭确认 |
| `test_gui_home.py` / `test_gui_home_layout.py` | 主页交互与原型布局 |
| `test_gui_page_monitor.py` | 监视页、CLI 启动、切页补存 |
| `test_gui_page_screenshot_targets.py` | 截图目标、图床、ADB、序号/列表 |
| `test_gui_page_screenshot_actions.py` | 截图/上传请求、自动保存、会话与重载 |
| `test_gui_page_push.py` | 推送通道、通知事件、变量与预览 |
| `test_gui_page_validation.py` | 校验结果、诊断分类、主页摘要 |
| `test_gui_page_focus.py` | 诊断定位、失活/恢复焦点、切页焦点 |
| `test_gui_page_structure.py` | 缺失/非法配置结构 |
| `test_gui_page_external.py` / `test_gui_page_logs.py` | 外部程序页 / 日志页 |
| `test_gui_page_lifecycle.py` | 文档代次、关闭轮询、后台心跳 |
| `test_gui_config_screenshot.py` / `test_gui_config_monitor.py` | 截图页与监视页的控件级细节（原 config_pages） |
| `test_gui_focus_isolation.py` | 子进程隔离的焦点回归（依赖上面的分片路径） |
| `test_gui_appearance.py` / `test_gui_lifecycle.py` / `test_gui_build_config.py` | 外观、生命周期、构建契约 |

## 常用命令

```powershell
# 只跑改动的页面（示例：主页）
.\.venv\Scripts\python.exe -B -m pytest tests/test_gui_home.py tests/test_gui_home_layout.py -q

# 只跑窗口与标题栏
.\.venv\Scripts\python.exe -B -m pytest tests/test_gui_window.py -q

# 只跑截图页相关
.\.venv\Scripts\python.exe -B -m pytest tests/test_gui_page_screenshot_*.py tests/test_gui_config_screenshot.py -q

# 全部 GUI 分片（耗时较长，建议按需选择）
.\.venv\Scripts\python.exe -B -m pytest tests/test_gui_page_*.py tests/test_gui_config_*.py -q
```

## 本机沙箱说明

DSH 沙箱会拒绝再次访问 `mode=0o700` 创建的目录，pytest 的 `tmp_path` 因此在部分环境不可用。
本机调试使用 `logs/pytest_sandbox_shim.py`（仅本地测试垫片，丢弃 `os.mkdir` 权限位）：

```powershell
$env:PYTHONPATH = "$PWD\logs"
$env:PYTEST_ADDOPTS = '-p pytest_sandbox_shim'
```

该垫片不进入产品代码，也不修改 pytest 安装。
