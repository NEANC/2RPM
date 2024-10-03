> [!CAUTION]
> 本项目使用 GPT AI 生成，GPT 模型：o1-preview

> [!WARNING]
> 请注意：由 GPT AI 生成的代码可能有：不可预知的风险和错误！
> 如您需要直接使用本项目，请**审查并测试后再使用**；
> 如您要将本项目引用到其他项目，请**重构后再使用**。

# Running-Runtime Process Monitoring

> [!TIP]
> 本项目是基于在 [Gist](https://gist.github.com/) 上发布的 [MaaPiCli 运行监视脚本](https://gist.github.com/NEANC/ebd9fbec7d736dd16311047ba2cf5d9e) 的进一步扩写

对指定进程进行监视，当进程在指定时间内未运行/进程运行超过指定时间/进程运行结束时使用 [ServerChan](https://sct.ftqq.com/) 进行推送上报的，使用 GPT AI 生成的脚本。

- 理论上可用于所有进程
- 推送实例基于 [ServerChan-DEMO](https://github.com/easychen/serverchan-demo) 库使用 GPT 修改而来
- [ServerChan](https://sct.ftqq.com/) 可以替换为 [OnePush](https://github.com/y1ndan/onepush) 库

## 可能的计划

- [ ] 使用外部文件来设置配置
- [ ] 引入 asyncio 库
- [ ] 引入 OnePush 库，允许选择 ServerChan/OnePush 推送
- [ ] ~~重构~~

## 如何食用

### 仅 Windows

请查阅 [使用说明](InstallationManual.md)

## License

[WTFPL](./LICENSE)
