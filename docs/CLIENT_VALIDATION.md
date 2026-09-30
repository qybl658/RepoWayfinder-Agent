# 客户端接入验证

`setup.cmd` / `configure_clients.py` 支持 10 类客户端，检测、选择与回退见 [自动接入](AUTO_SETUP.md)。客户端适配测试覆盖配置保留、并发检查、幂等、同名冲突、卸载 receipt 和非交互选择边界；这不能代替每个客户端当前版本的真实会话验证。

2026-09-27 的实际接入任务统一使用 `coderamp-labs/gitingest` 提交 `4e259a02fe72115bee538271622f1234a81c8e1a`：安装在作业环境内，运行 CLI 并生成新的 UTF-8 `digest.txt`。外部检查覆盖文件非空、内容标记、读取错误和源码提交。

- Grok CLI 1.0.41 / Grok 4.7 high：MCP 作业与独立产物检查通过，78.031 秒，44,684 记录 token（含 29,440 cache read）。同期原生对照产物逐字节一致；完整试次见 [Grok 对照](GROK_COMPARISON.md)。
- DSH Desktop 0.9.2 内置 Harness SDK 0.1.5-rc.2 / DeepSeek Flash：SDK → MCP → worker → 产物验证通过，37.734 秒，12,811 totalTokens；此前权限启动失败和一次恢复运行保留在本地档案，不作为此成功试次的统计。更完整的负面样本和课程任务见 [DSH 对照](DSH_COMPARISON.md)。
- Codex：本地原生 MCP 接入与产物检查通过；没有同期无工具对照，不能推算节省比例。

这些是当时版本的任务接入证据，不代表所有客户端和项目的完整兼容性。课程手册与部署矩阵的公开任务、独立验收器及统计摘要继续保留在 `benchmarks/`；原始会话、账户状态和机器路径不公开。
