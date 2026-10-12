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

## 快速开始

### 1.安装

1. 从 [Releases](https://github.com/NEANC/2RPM/releases/latest)，下载 `2RPM-Nuitka-*.exe` 或 `2RPM-PyInstaller-*.exe`
2. 首次运行会生成 `config.yaml` 配置文件
3. 编辑 `config.yaml` 文件
4. 再次运行程序，开始监控目标进程

### 2.首次运行

首次运行时程序会在程序目录生成 `config.yaml`，并提示「配置文件已生成」，此时按任意键退出：

```powershell
python .\2RPM.py
```

### 3.最少需要修改的字段

打开 `config.yaml`，把下面两处改成自己的目标即可，其余字段保持默认：

```yaml
monitor:
  mode: psutil
  psutil:
    process_name: notepad.exe

push:
  push_channel_settings:
    channels:
      - { provider: serverchan, sckey: SCTxxxx }
```

- `monitor.psutil.process_name`：要监视的进程名（如 `notepad.exe`），不是窗口标题，也不是完整路径。使用其他模式时改填对应字段，见 [监控模式](#监控模式)。
- `push.push_channel_settings.channels`：至少留一个有效通道，否则不会收到通知。参数填写参考 [推送通道](#推送通道)。

---

## 4.监控模式

模式由 `monitor.mode` 指定，程序只读取当前模式对应的小节。必须显式配置目标字段，否则加载配置时会报错退出。

| `monitor.mode`   | 检测方式                                               | 必填字段                                                          | 适用场景                   |
| ---------------- | ------------------------------------------------------ | ----------------------------------------------------------------- | -------------------------- |
| `psutil`（默认） | 按进程名轮询检测进程是否运行                           | `monitor.psutil.process_name`                                     | 已经或随时会运行的普通进程 |
| `task_scheduler` | 通过 Windows 事件日志获取计划任务创建的进程 PID 后监视 | `monitor.task_scheduler.task_name`                                | 由计划任务拉起的进程       |
| `launch`         | 主动拉起目标程序或计划任务并获取 PID 进行监视          | `monitor.launch.type`；`program` 填 `path`，`task` 填 `task_name` | 需要由 2RPM 主动启动目标   |

> 从旧版本升级时，旧 layout 会在加载配置时自动迁移；迁移范围与边界见 [兼容性与实现细节](docs/兼容性与实现细节.md)。

---

## 配置参考

配置文件是注释型 YAML，生成时已带完整注释，可直接按注释修改。核心结构如下：

- `monitor.mode`：监控模式（`psutil`、`task_scheduler` 或 `launch`）
- `monitor.common`：共用的超时、轮询、等待与外部动作触发阈值
- `monitor.psutil`：按进程名监控的目标
- `monitor.task_scheduler`：计划任务名称与事件回溯窗口
- `monitor.launch`：主动启动的程序或计划任务
- `push`：推送模板与通道参数
- `external`：`on_end`、`on_timeout`、`on_wait_timeout` 三个外部动作
- `log`：日志输出与清理策略

格式说明：时间类字段支持 `h` / `m` / `s` 单单位字符串（如 `1h`、`15m`、`30s`），也可直接填秒数；填写非法值或负值时程序会告警并回退默认值。

### `monitor.common`

| 字段                | 默认值 | 说明                             |
| ------------------- | ------ | -------------------------------- |
| `timeout_interval`  | `15m`  | 超时告警间隔                     |
| `loop_interval`     | `1s`   | 监视循环间隔                     |
| `timeout_threshold` | `3`    | 进程超时触发外部程序前的次数阈值 |
| `max_wait`          | `30s`  | 等待进程启动的最长时间           |
| `check_interval`    | `1s`   | 等待进程时的检查间隔             |

### `monitor.psutil`

| 字段           | 默认值        | 说明             |
| -------------- | ------------- | ---------------- |
| `process_name` | `notepad.exe` | 要监视的进程名称 |

### `monitor.task_scheduler`

| 字段               | 默认值           | 说明                                                  |
| ------------------ | ---------------- | ----------------------------------------------------- |
| `task_name`        | `\Custom\MyTask` | 要监视的计划任务名称，完整路径格式 `\Folder\TaskName` |
| `lookback_minutes` | `10`             | 事件回溯时间（分钟）                                  |

### `monitor.launch`

| 字段        | 默认值                  | 说明                                   |
| ----------- | ----------------------- | -------------------------------------- |
| `type`      | `program`               | 拉起类型：`program` / `task`           |
| `path`      | `C:\path\to\target.exe` | 要拉起的可执行文件路径（仅 `program`） |
| `args`      | `null`                  | 命令行参数（选填，仅 `program`）       |
| `cwd`       | `null`                  | 工作目录（选填，仅 `program`）         |
| `task_name` | `\Custom\MyTask`        | 要触发的计划任务名称（仅 `task`）      |

### `push`

| 字段                                  | 默认值                  | 说明                                     |
| ------------------------------------- | ----------------------- | ---------------------------------------- |
| `push_channel_settings.channels`      | 见示例                  | OnePush 推送通道，可配置多个             |
| `retry.interval`                      | `3s`                    | 单个通道失败后的等待间隔                 |
| `retry.max_count`                     | `3`                     | 每个通道的总尝试次数（含首次发送）       |
| `templates.<事件>.enable`             | 见[通知模板](#通知模板) | 是否启用该事件的通知                     |
| `templates.<事件>.capture_screenshot` | `true`                  | 该事件是否截取全部截图目标               |
| `templates.<事件>.title`              | 见示例                  | 通知标题                                 |
| `templates.<事件>.content`            | 见示例                  | 通知内容                                 |
| `screenshot.targets`                  | `[]`                    | 截图目标列表，默认空，不自动匹配监控目标 |
| `screenshot.image_host`               | `catbox`                | 图床列表，按顺序尝试，成功后停止         |
| `screenshot.retention`                | 见[本地保留](#本地保留) | 自动截图的本地保留策略                   |

### `external`

| 字段              | 触发时机           |
| ----------------- | ------------------ |
| `on_end`          | 进程结束时         |
| `on_timeout`      | 进程运行超时后     |
| `on_wait_timeout` | 等待进程启动超时后 |

三项均填写外部程序/BAT 脚本的完整路径，留空 (`null`) 表示不执行。

### `log`

| 字段             | 默认值 | 说明             |
| ---------------- | ------ | ---------------- |
| `log_directory`  | `logs` | 日志输出目录     |
| `max_log_files`  | `15`   | 日志最大保存数量 |
| `retention_days` | `3`    | 日志保存天数     |

---

## 通知与推送

### 推送通道

推送支持多通道顺序发送，每个通道会按照配置顺序发送并重试，支持的推送通道请查阅 [`OnePush`](https://github.com/y1ndan/onepush) 。

#### 单通道配置示例

```yaml
push:
  push_channel_settings:
    channels:
      - { provider: serverchan, sckey: SCTxxxx }
```

#### 多通道配置示例

```yaml
push:
  push_channel_settings:
    channels:
      - { provider: serverchan, sckey: SCTxxxx }
      - { provider: bark, key: xxxxxxx }
      - { provider: line, token: xxxxxxx }
  retry:
    interval: 3s
    max_count: 3
```

- `push.retry.max_count` 是每个通道的总尝试次数，包含首次发送（默认 3 次）
- `push.retry.interval` 是失败后的等待间隔。

### 通知模板

入口为 `push.templates`

|      模板键       |       事件       | 默认值  |
| :---------------: | :--------------: | :-----: |
|     `on_end`      |     进程结束     | `true`  |
|   `on_timeout`    |     运行超时     | `true`  |
| `on_wait_timeout` |   等待启动超时   | `true`  |
|   `on_external`   | 外部程序执行通知 | `false` |

每个模板使用 `enable`、`capture_screenshot`、`title`、`content`

- `capture_screenshot` 默认 `true`，控制该事件的全部独立截图目标；设为 `false` 时不截图、不上传，截图变量替换为空字符串。

```yaml
push:
  templates:
    on_end:
      enable: true
      capture_screenshot: true
      title: "进程结束：{process_name}"
      content: |
        主机：{host_name}
        当前时间：{current_time}
        运行时间：{process_run_time}
        {screenshot}
```

### 模板变量

通用变量在所有模板中均可用：

| 变量                   | 说明                                      |
| ---------------------- | ----------------------------------------- |
| `{host_name}`          | 当前主机名                                |
| `{current_time}`       | 当前时间，格式 `YYYY/MM/DD HH:MM:SS`      |
| `{short_current_time}` | 当前时间，格式 `HH:MM:SS`                 |
| `{screenshot}`         | 截图功能占位符，详见[截图变量](#截图变量) |

事件变量仅在对应模板中可用：

| 变量                      | 说明                              | 可用模板                                                 |
| ------------------------- | --------------------------------- | -------------------------------------------------------- |
| `{process_name}`          | 当前进程名                        | `on_end`、`on_timeout`、`on_wait_timeout`、`on_external` |
| `{process_pid}`           | 当前进程 PID                      | `on_end`、`on_timeout`、`on_external`                    |
| `{process_run_time}`      | 当前进程运行时间，格式 `HH:MM:SS` | `on_end`、`on_timeout`                                   |
| `{process_wait_time}`     | 当前进程等待时间，格式 `HH:MM:SS` | `on_wait_timeout`                                        |
| `{external_program_name}` | 外部程序名                        | `on_external`                                            |
| `{external_program_path}` | 外部程序路径                      | `on_external`                                            |

- 在不提供该变量的事件模板中引用对应变量，会导致该条通知被整体跳过，日志提示「通知模板缺少变量」。
- `on_external` 由等待超时触发时，`{process_name}` 为计划任务名，`{process_pid}` 为 `None`。

---

## 截图功能

### 截图目标

截图目标位于 `push.screenshot.targets`，独立于监控目标；默认空列表，不会按监控 PID 或进程名自动匹配窗口。

```yaml
push:
  screenshot:
    targets:
      - { provider: window, target: "AppName@{version}", out: screenshot_1 }
      - { provider: adb, target: "127.0.0.1:16384" }
    image_host:
      - { provider: beeimg_cn, token: "${BEEIMG_CN_TOKEN}", expiration: 7d }
      - {
          provider: boltp,
          token: "${BOLTP_TOKEN}",
          expiration: 1d12h,
          options: { permission: 1 },
        }
    retention:
      enabled: null
      max_days: 14
```

- 自动截图使用 JPEG；图床按列表顺序失败回退，首个成功即停止。图床 `token` 支持 `${ENV_NAME}`，配置加载时保留原文，上传时由通知渠道解析。可选图床见 [图床服务](#图床服务)。

### 截图变量

截图目标的结果通过变量注入通知模板；是否执行截图由该模板的 `capture_screenshot` 决定。

`targets` 列表项的字段

| 字段       | 说明                                                        |
| ---------- | ----------------------------------------------------------- |
| `provider` | 截图方式，可选值 `window`、`adb`                            |
| `target`   | 截图目标，根据 `provider` 不同而选择，窗口标题、ADB 设备 IP |
| `out`      | 配置了 `out` 的目标，变量名即 `out` 的值                    |

- `target` 相关说明
  - 窗口标题支持 `@{version}` 版本标记
    - 可使用 `@{version}`、`@{VERSION}`、`@{Version}` 标记别名
    - 可匹配 `AppName v1.2.3`、`appname version 1.2`、`AppName gabc1234`、`AppName 1.2 Launcher`、`AppName 1.2 Settings`
    - 若存在多个匹配窗口时会拒绝截图，不自动选取窗口
  - 无标记时按完整窗口标题精确匹配，区分大小写
  - `adb` 填设备地址/序列号，不支持版本标记
  - 示例 `{target: "AppName@{version}"}`、`{target: "127.0.0.1:16384"}`

- `out` 相关说明
  - 必须符合 `[A-Za-z_][A-Za-z0-9_]*`（字母或下划线开头，仅含字母、数字、下划线）
  - 无效、重复或与保留名冲突时，会改用下一个未被占用的 `screenshot_N` 并记录告警；无效目标保留原位置与输出名，不挤占后续编号
  - 示例 `{out: screenshot_1 }`

覆盖`{screenshot}` 变量的取值：

| 情况                   | 取值                                                            |
| ---------------------- | --------------------------------------------------------------- |
| 截图并上传成功         | Markdown 图片 `![输出名](直链)`，是否展示图片由通知渠道能力决定 |
| 目标配置无效           | `截图配置失败：<原因>`                                          |
| 截图失败               | `截图失败：<原因>`                                              |
| 上传失败               | `截图上传失败：<原因>`                                          |
| 未启用截图             | 空字符串                                                        |
| 引用了未配置的截图编号 | `截图失败：未配置该截图目标`                                    |

- 模板引用了截图变量但没有有效目标时，`{screenshot}` 取 `截图配置失败：未配置有效截图目标`。
- 模板不会自动附带截图变量，需要自行写入 `content`；`capture_screenshot: false` 时全部截图变量替换为空字符串。
- 截图变量取值恒为字符串，不能对它做属性或下标访问，否则视为无效模板。
- 每个目标在每次事件中只截取一次，结果供全部通道及其重试复用；单个目标失败不阻断其他目标和通知。
- 模板禁用、无有效通道、缺少普通变量或模板校验失败时，不执行截图。
- 存在截图配置告警时会在正文末尾追加「截图配置提示」；正文未引用 `{screenshot}` 时，未被引用的改名结果也会追加到正文。

### 本地保留

本地保留策略位于 `push.screenshot.retention`：

| 字段       | 默认值 | 说明                                                            |
| ---------- | ------ | --------------------------------------------------------------- |
| `enabled`  | `null` | `null` 或 `false` 时不保存自动截图、不清理；`true` 时保存并清理 |
| `max_days` | `14`   | 保留天数，必须为正整数                                          |

- `enabled: true` 时保存到程序根目录的 `screenshot/YYYY_MM_DD/配置名/事件名_01.jpg`。
- 每次监控启动在日志初始化后清理一次：目录日期距今天数大于等于 `max_days` 时删除整个日期目录（包含各配置子目录，而非只删除图片）。
- 本地保存失败不阻断上传；保存路径拒绝符号链接、junction 等重解析点。
- 该策略不是图床保存期限，截图 CLI 不执行此清理。

---

## 截图 CLI

用于在不启动监控的情况下手动截图或上传的测试工具。

### 参数

| 参数                  | 是否必填               | 说明                                                                                                              |
| --------------------- | ---------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `--source`            | 是                     | 截图来源，仅能出现一次；接受 `window:标题`、`adb:serial`、裸窗口标题，或 `window`/`adb` 配合位置目标或 `--target` |
| 位置目标 / `--target` | 视写法                 | 配合 `--source window` 或 `--source adb` 使用；这些目标写法不可重复混用                                           |
| `--output`            | 与 `--upload` 至少一项 | 保存路径；不带值时保存到程序根目录的 `screenshot`；与 `--upload` 各限一次，可同时使用                             |
| `--upload`            | 与 `--output` 至少一项 | 分号分隔的内联 YAML 映射，支持嵌套 `options` 和引号内分隔字符，**不是文件路径**                                   |

### 示例

从项目目录运行以下 PowerShell 示例；打包版本可将 `python .\2RPM.py` 替换为实际可执行文件。

```powershell
python .\2RPM.py screenshot --source window --target 'AppName@{Version}' --output
python .\2RPM.py screenshot --source 'window:完整窗口标题' --output '.\captures\shot.png'
python .\2RPM.py screenshot --source adb '127.0.0.1:16384' --output '.\captures\'
python .\2RPM.py screenshot --source '完整窗口标题' --upload '{provider: catbox}'
python .\2RPM.py screenshot --source 'window:完整窗口标题' --output --upload '{provider: beeimg_cn, token: "${BEEIMG_CN_TOKEN}", expiration: 7d}; {provider: boltp, token: "${BOLTP_TOKEN}"}'
```

### 保存与上传规则

- `--output` 不带值时保存到程序根目录的 `screenshot`；显式相对路径基于当前工作目录。现存目录、尾随斜杠路径按目录处理；其他路径仅在后缀为 `.jpg/.jpeg/.png/.webp/.raw` 时按文件处理，否则按目录处理。
- 目录输出默认 JPEG，自动命名 `screenshot_YYYYMMDD_0000.jpg`，遇重名递增，至少四位编号；显式文件不覆盖已有文件。文件后缀选择格式，WebP 编码器不可用时直接报编码失败，不降级；ADB 原始采集失败走 PNG 回退时，按实际格式调整后缀。`.raw` 不能与上传组合。CLI 禁止写入自动截图受管日期目录。
- CLI 与自动回退不同：只截图一次，依次上传全部图床，单项失败仍继续，全部成功才算上传成功。保存失败不阻断上传；同时保存与上传时退出码以全部上传结果为准。
- 重复选项、静态校验发现任一内联项非法都会拒绝整条命令；图床专属选项还可能在上传阶段被拒绝。
- CLI 匿名写法省略 `token`（仅 `catbox`、`beeimg` 可通过无 token 的静态校验）；显式 token 必须是非空字符串，纯数字凭证需加引号。环境变量不存在或为空时，需要认证的项失败并继续其他项；匿名图床可降级匿名，但 `beeimg` 设置 `albumid` 时不能降级。PowerShell 中用单引号包住整个参数以保留 `${ENV_NAME}`。

### 退出码

| 退出码 | 含义                                     |
| ------ | ---------------------------------------- |
| `0`    | 保存成功（无上传时）或全部上传成功       |
| `1`    | 截图失败、仅保存失败，或至少一个上传失败 |
| `2`    | 参数错误，发生在截图前                   |

---

## 图床服务

已验证支持的图床供应商如下

- [无铭图床](https://wmimg.com)
- [蜜蜂图床](https://www.beeimg.cn/)
- [闪电图床](https://www.boltp.com/)
- [Superbed（聚合图床）](https://www.superbed.cn/)
- [BeeIMG.com](https://beeimg.com)
  - 支持匿名上传，匿名上传有效期未知，登陆后未找到 API Key 配置

已准备，但是未验证支持的图床供应商如下

- [catbox](https://catbox.moe)
  - 测试上传超时，使用代理也超时，无法继续测试
  - 理论上支持匿名上传，匿名上传有效期 2 年

### 图床能力

|                    图床供应商                    |    缩写     | 是否支持时效性设置 | 是否支持匿名上传 |
| :----------------------------------------------: | :---------: | :----------------: | :--------------: |
|          [无铭图床](https://wmimg.com)           |   `wmimg`   |         ❌         |        ❌        |
|        [蜜蜂图床](https://www.beeimg.cn/)        | `beeimg_cn` |         ✅         |        ❌        |
|        [闪电图床](https://www.boltp.com/)        |   `boltp`   |         ✅         |        ❌        |
| [Superbed（聚合图床）](https://www.superbed.cn/) | `superbed`  |         ❌         |        ❌        |
|         [BeeIMG.com](https://beeimg.com)         |  `beeimg`   |         ❌         |        ✅        |

若站点「不支持匿名上传」表示必须提供有效 `token`。时效性设置、存储选择与期限计算的详细规则见 [兼容性与实现细节](docs/兼容性与实现细节.md)。

---

## 常见问题

**首次运行后程序停住不动？**
首次运行会生成 `config.yaml` 并提示「配置文件已生成」，此时按任意键退出，这是正常行为。编辑配置后再次运行即可。

**改了配置需要重启吗？**
需要。程序在启动时读取一次配置，修改后请重新运行。

**没收到通知？**
依次检查：`push.push_channel_settings.channels` 是否至少有一个有效通道；对应 `push.templates.<事件>.enable` 是否为 `true`；通道凭证是否正确。推送结果会逐条输出到控制台，失败原因也会写入日志。

**`process_name` 该填什么？**
填进程名（如 `notepad.exe`），不是窗口标题，也不是完整路径。可以在任务管理器的「详细信息」里查看进程名。

**日志在哪里？**
程序目录下的 `logs` 文件夹，文件名形如 `2RPM_2026-10-12_10-20-30.123.log`；可用 `log.log_directory` 改成其他目录。

**如何停止监控？**
在控制台按 `Ctrl+C`。

**配置文件可以用别的名字或路径吗？**
可以。用 `-c` 指定，例如 `python .\2RPM.py -c D:\configs\rpm.yaml`；也支持把配置文件拖到程序上或通过文件关联打开。

---

## 从源码运行

```powershell
git clone https://github.com/NEANC/2RPM.git
cd 2RPM
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## License

[WTFPL](./LICENSE)
