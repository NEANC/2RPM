> [!CAUTION]
> 本项目使用 TRAE IDE 生成与迭代

> [!WARNING]
> 请注意：由 AI 生成的代码可能有：不可预知的风险和错误！  
> 如您需要直接使用本项目，请**审查并测试后再使用**；  
> 如您要将本项目引用到其他项目，请**重构后再使用**。

# Running-Runtime Process Monitoring

> [!TIP]
> 本项目是基于 [MaaPiCli 运行监视脚本](https://gist.github.com/NEANC/ebd9fbec7d736dd16311047ba2cf5d9e) 的进一步扩写

2RPM 用于监控 Windows 下指定进程的运行状态，并在关键事件发生时发送通知。

支持场景：

- 指定等待窗口内未检测到目标进程启动
- 进程运行时长超过阈值（超时告警）
- 监视进程结束时上报（可选择是否执行外部程序）

通知使用 [`OnePush`](https://github.com/y1ndan/onepush) 推送。

---

## 主要功能

- 支持配置文件运行，默认配置文件为 `config.yaml`
- 支持 CLI 与配置文件双入口：`-c/--config` 或直接传入配置文件路径
- 通知模板化（支持多种消息类型）
- 异步主循环，减少阻塞影响
- 外部程序联动：进程结束/超时场景可自动执行脚本
- 自动配置修正：
  - 程序会在加载配置时自动纠正部分渠道参数别名
  - 例如 `provider: serverchan` 的历史写法 `key` 会自动改写为 `sckey`

---

## 如何使用

1. 从 [Releases](https://github.com/NEANC/2RPM/releases/latest) 下载
2. 首次运行会生成 `config.yaml` 配置文件
3. 编辑 `config.yaml` 文件
4. 再次运行程序，开始监控目标进程

---

## 配置说明（`config.yaml`）

配置文件是注释型 YAML，核心结构如下：

- `monitor.mode`：监控模式（`psutil`、`task_scheduler` 或 `launch`）
- `monitor.common`：共用的超时、轮询、等待与外部动作触发阈值
- `monitor.psutil`：按进程名监控的目标
- `monitor.task_scheduler`：计划任务名称与事件回溯窗口
- `monitor.launch`：主动启动的程序或计划任务
- `push`：推送模板与通道参数
- `external`：`on_end`、`on_timeout`、`on_wait_timeout` 三个外部动作
- `log`：日志输出与清理策略

```yaml
monitor:
  mode: psutil
  common:
    timeout_interval: 15m
    loop_interval: 1s
    timeout_threshold: 3
    max_wait: 30s
    check_interval: 1s
  psutil:
    process_name: notepad.exe
  task_scheduler:
    task_name: '\Custom\MyTask'
    lookback_minutes: 10
  launch:
    type: program
    path: 'C:\path\to\target.exe'
    args: null
    cwd: null
    task_name: '\Custom\MyTask'
external:
  on_end: null
  on_timeout: null
  on_wait_timeout: null
```

当前旧 layout 仅由 `load_config` 在加载配置文件时自动迁移；直接构造的运行时配置必须使用上述新 layout。当前模式的目标字段必须有效；`launch.type` 为 `program` 时填写 `path`，为 `task` 时填写 `task_name`。`task_scheduler.lookback_minutes` 为 `0` 时查询窗口至少为 1 毫秒，launch-task 查询固定回溯 1 分钟。

### 推送通道配置示例

#### 单通道配置示例

```yaml
push_settings:
  push_channel_settings:
    push_channel:
      - {provider: serverchan, sckey: SCTxxxx}
```

#### 多通道配置示例

```yaml
push_settings:
  push_channel_settings:
    push_channel:
      - {provider: serverchan, sckey: SCTxxxx}
      - {provider: bark, key: xxxxxxx}
      - {provider: line, token: xxxxxxx}
```

### 通知模板示例

`push_settings.push_templates` 下预置了多种模板：

- `process_end_notification`
- `process_timeout_warning`
- `process_wait_timeout_warning`
- `external_program_execution_notification`

模板字段支持变量自动替换（如 `{host_name}`、`{process_name}`、`{current_time}` 等），
可按需增删和修改文案。

---

## License

[WTFPL](./LICENSE)
