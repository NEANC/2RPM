> [!CAUTION]
> 本项目使用 GPT AI 生成，GPT 模型：o1-preview

> [!WARNING]
> 请注意：由 GPT AI 生成的代码可能有：不可预知的风险和错误！
> 如您需要直接使用本项目，请**审查并测试后再使用**；
> 如您要将本项目引用到其他项目，请**重构后再使用**。

# 2RPM v1

版本：v1.10.0  
状态：Release Candidate

## 功能列表

## 审查

**无**，请自行审查并测试

## 测试汇报

仅为**个人测试**

- 直接运行：成功

  - 已达成预期功能
  - 全推送正常
  - 获取到的进程信息正确
  - log 输出正常

- 编译运行：成功

  - 已达成预期功能
  - 全推送正常
  - 获取到的进程信息正确
  - log 输出正常

编译命令如下：

```PYTHON
    # 不显示命令行窗口
    pyinstaller -F -w 2RPM-v1.py -n 2RPM-v1不显示命令行窗口
    # 使用控制台
    pyinstaller -F -c 2RPM-v1.py -n 2RPM-v1使用控制台
    # 默认
    pyinstaller -F 2RPM-v1.py -n 2RPM-v1默认
```

---

测试平台：

```rust
Python：Python 3.12.5
处理器：AMD Ryzen7 8845HS
主板：AZW SER8
内存：32GB DDR5 5600MHz (16GBx2)
显卡：AMD Radeon 780M Graphics (4GB)
磁盘：CT1000P3PSSD8 (1000GB)
系统：Windows 10 专业工作站版 22H2 19045.4894
```
