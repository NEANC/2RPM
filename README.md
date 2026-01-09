> [!CAUTION]
> 本项目使用 GPT AI 与 Claude AI 进行最初生成，GPT 模型：o1-preview，Claude 模型: claude-3-5-sonnet；  
> v3 版本使用 TRAE IDE 进行迭代优化。

> [!WARNING]
> 请注意：由 AI 生成的代码可能有：不可预知的风险和错误！
> 如您需要直接使用本项目，请**审查并测试后再使用**；
> 如您要将本项目引用到其他项目，请**重构后再使用**。

# Running-Runtime Process Monitoring

> [!TIP]
> 本项目是基于在 [Gist](https://gist.github.com/) 上发布的 [MaaPiCli 运行监视脚本](https://gist.github.com/NEANC/ebd9fbec7d736dd16311047ba2cf5d9e) 的进一步扩写

对指定进程进行监视，当进程在指定时间内未运行/进程运行超过指定时间/进程运行结束时使用 [`ServerChan-SDK 库`](https://github.com/easychen/serverchan-sdk "ServerChan 的 SDK") 或 [`OnePush 库`](https://github.com/y1ndan/onepush) 进行推送上报；使用 AI 生成。

- 支持配置文件运行，方便维护多配置。
- 支持命令行参数调用，使用 '-c' 命令调用配置文件运行。
- 可指定**特定进程结束时**执行外部程序。
- 在 `指定的等待时间内未检测到目标进程启动时`、`进程的运行时间超过了设定的警告间隔时`、`监视的进程结束时` 发送通知。
  - 支持通过配置文件来控制是否发送那种类型的通知。
- 可在 `等待进程启动超时`、`特定进程结束时`、`特定进程超时运行时` 执行外部程序，同时在执行后发送通知。
- 支持通过 [`ServerChan`](https://sct.ftqq.com/) 或 [`OnePush 库`](https://github.com/y1ndan/onepush) 进行消息推送。

---

## 版本信息

版本：v3.10.0  
状态：Beta

- [`OnePush 库`](https://github.com/y1ndan/onepush)调用未验证
- 去除了程序列表，只支持单个进程
- 修正了 V2 版本中的已知问题
- 使用模块化而不是所有函数都在一个文件中

---

## 如何食用

### 仅 Windows

[使用说明](.\InstallationManual.md)

---

## 可能的计划

- [ ] ~~人工重构~~

---

## License

[WTFPL](./LICENSE)
