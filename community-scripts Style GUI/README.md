# community-scripts Style GUI

面向 Python 终端程序的轻量展示组件，提供可复用的启动横幅、spinner 阶段提示和统一失败输出。

## 文件说明

- `spinner.py`：提供 `spinner_phase()` 和 `notify_fail()`。
- `banner.py`：提供 `print_info()`。
- `example.py`：完整最小示例。

## 依赖

需要安装 `colorama`：

```powershell
pip install colorama
```

除 `colorama` 外，其余依赖均来自 Python 标准库。

## 复制方式

`community-scripts Style GUI/` 是分发容器，不是 Python 包。

推荐把 `spinner.py` 和 `banner.py` 复制到目标项目入口同级目录：

```text
your-project/
  main.py
  spinner.py
  banner.py
```

然后在 `main.py` 中导入：

```python
from spinner import spinner_phase, notify_fail
from banner import print_info
```

如果复制到目标项目已有包目录，请按目标项目的包结构使用绝对导入或相对导入。

## Banner 用法

```python
from banner import print_info

print_info(
    app_name="Style GUI Demo",
    subtitle="Reusable Terminal UI",
    version="v1.0.0",
    license_name="MIT",
    banner_art="STYLE GUI",
)
```

参数说明：

- `app_name`：应用标题，空字符串会省略该行。
- `subtitle`：副标题，空字符串会省略该行。
- `version`：版本字段，空字符串会省略 `Version:`。
- `license_name`：许可证字段，空字符串会省略 `License:`。
- `banner_art`：ASCII Art，空字符串会省略横幅图案。

## Spinner 用法

```python
from spinner import spinner_phase

with spinner_phase("程序正在初始化...") as sp:
    sp.text("正在加载配置...")
    sp.write_done("配置加载完成")
    sp.done("程序初始化完成")
```

spinner 句柄方法：

- `text(message)`：更新当前 spinner 文案。
- `write(message)`：插入一行普通文本。
- `write_done(message)`：插入一行成功文本。
- `write_fail(message)`：插入一行失败文本。
- `done(message)`：以成功状态结束当前 spinner。
- `fail(message)`：以失败状态结束当前 spinner。

## 异常提示

```python
from spinner import notify_fail

try:
    raise RuntimeError("boom")
except Exception as exc:
    notify_fail(f"程序出现异常: {exc}", exc_info=True)
```

## 完整示例

运行：

```powershell
python example.py
```
