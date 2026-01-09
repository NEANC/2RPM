> [!CAUTION]
> 本项目使用 GPT AI 与 Claude AI 生成，GPT 模型：o1-preview，Claude 模型: claude-3-5-sonnet
> 

> [!WARNING]
> 请注意：由 GPT AI 生成的代码可能有：不可预知的风险和错误！
> 如您需要直接使用本项目，请**审查并测试后再使用**；
> 如您要将本项目引用到其他项目，请**重构后再使用**。

# Running-Runtime Process Monitoring

> [!TIP]
> 本项目是基于在 [Gist](https://gist.github.com/) 上发布的 [MaaPiCli 运行监视脚本](https://gist.github.com/NEANC/ebd9fbec7d736dd16311047ba2cf5d9e) 的进一步扩写

对指定进程进行监视，当进程在指定时间内未运行/进程运行超过指定时间/进程运行结束时使用 [`ServerChan-SDK 库`](https://github.com/easychen/serverchan-sdk "ServerChan 的 SDK") 或 [`OnePush 库`](https://github.com/y1ndan/onepush "仅v2版本支持") 进行推送上报；使用 GPT AI 与 Claude AI 生成。

- 可用于所有进程

[v3 版本](v3/README.md)现已支持：

- 支持配置文件运行，方便维护多配置。
- 支持命令行参数调用，使用 '-c' 命令调用配置文件运行。
- 可指定**特定进程结束时**执行外部程序。
- 支持通过 **[`ServerChan`](https://sct.ftqq.com/)** 进行消息推送，方便及时了解进程状态。

## 可能的计划

- [ ] ~~人工重构~~

## 如何食用

### 仅 Windows

[使用说明](.\InstallationManual.md)


## License

[WTFPL](./LICENSE)
