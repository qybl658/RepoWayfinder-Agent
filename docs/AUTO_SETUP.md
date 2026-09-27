# 自动接入 Agent 客户端

在 Windows 仓库目录运行 `setup.cmd`，入口会准备本项目 `.venv`，再调用 `configure_clients.py`。需要已有 Python 3.11+；项目运行还需要 Git。依赖安装仅进入项目虚拟环境，不安装客户端或系统运行时。

## 自动检测与写入位置

支持 Grok、DSH、Claude Code、Claude Desktop、Codex、Cursor、Gemini CLI、OpenCode、VS Code 和 Windsurf。检测到客户端命令或已有客户端配置后，才登记 `repo_wayfinder`；没有发现的客户端会列为 `not_found`。检测到多个客户端时，交互菜单先让用户多选再写入。非交互调用没有显式 `--clients` 且发现多个时，返回 `selection_required`，不自动全选。单个客户端可直接配置；`--dry-run` 只列出检测结果，不提问也不写入。

| 客户端 | 默认用户配置 | `--scope project` 配置 |
| --- | --- | --- |
| Grok | `~/.grok/config.toml` | `<项目>/.grok/config.toml` |
| DSH Desktop | 检测到的 Harness home 下 `profiles/web/cordis.patch.yml` | 不支持 |
| Codex | `$CODEX_HOME/config.toml`，未设置时为 `~/.codex/config.toml` | `<项目>/.codex/config.toml` |
| Cursor | `~/.cursor/mcp.json` | `<项目>/.cursor/mcp.json` |
| Claude Code | `~/.claude.json` | `<项目>/.mcp.json` |
| Claude Desktop | `%APPDATA%/Claude/claude_desktop_config.json` | 不支持 |
| Gemini CLI | `~/.gemini/settings.json`，设置 `GEMINI_CLI_HOME` 时从该根目录定位 | `<项目>/.gemini/settings.json` |
| OpenCode | `~/.config/opencode/opencode.json(c)`，或 `OPENCODE_CONFIG` | `<项目>/opencode.json(c)` |
| VS Code | `%APPDATA%/Code/User/mcp.json`（默认配置文件） | `<项目>/.vscode/mcp.json` |
| Windsurf Cascade | `~/.codeium/windsurf/mcp_config.json` | 不支持 |

登记的是本地 Python 进程及本仓库 `agent.py` 的绝对路径。优先使用仓库虚拟环境，并先验证其能导入服务依赖。缺少环境时会报错，不写一个无法启动的命令。账户、登录、模型、信任目录、工具自动批准规则保持原样。项目配置可能覆盖同名用户配置；客户端原有目录信任要求仍适用。

DSH Desktop 通过安装标记、运行进程或快捷方式等证据定位现有 web profile。发现多个 DSH 副本时，需要再选择具体实例；脚本不会猜。也可用 `--dsh-home` 明确指定含 `profiles/web` 的 Harness home。仅追加本工具 MCP 插件块，保留现有 YAML 条目与注释，不修改 `settings.yaml` 或凭据文件；使用 DSH 自带的 Node/YAML 解析器，不额外安装解析依赖。DSH CLI 和其他社区桌面壳的配置位置并不统一，本适配针对检测到的 DSH Desktop web profile。

## 常用操作

已有依赖时，以下命令不再安装任何包：

```powershell
# 预览检测结果和目标文件，不写配置
.\.venv\Scripts\python.exe configure_clients.py --dry-run

# 默认：检测客户端，多客户端时先选择
.\.venv\Scripts\python.exe configure_clients.py

# 只选择 Grok 与 Cursor
.\.venv\Scripts\python.exe configure_clients.py --clients grok cursor

# 只为指定项目配置，任务数据放到指定目录
.\.venv\Scripts\python.exe configure_clients.py --scope project --directory D:\my-work --workspace D:\my-work\repo-jobs

# 机器可读结果
.\.venv\Scripts\python.exe configure_clients.py --json
```

配置完成后开启新客户端会话。Grok 可通过 `grok mcp doctor repo_wayfinder` 检查连接；Codex 可用 `codex mcp get repo_wayfinder` 检查登记，在新会话 `/mcp` 查看加载情况；Cursor 保存后重启，在 MCP 面板查看。脚本写入成功不等于已经打开的会话立即加载了服务。

客户端参数名：`grok`、`dsh`、`claude`（Claude Code）、`claude_desktop`、`codex`、`cursor`、`gemini`、`opencode`、`vscode`、`windsurf`。DSH 与其他 GUI 客户端写入后可能需要重新打开应用；不会为了加载配置强行关闭正在进行的会话。

## 重复运行、冲突与移除

相同条目返回 `unchanged`，不会新增备份或重复服务。同名条目不同则返回 `conflict` 并保持原样；损坏配置也不会被覆盖。TOML 追加独立块，保留原有设置和注释；普通 JSON 保留其他键值，但格式可能重新缩进；不接受重复键。VS Code 与 OpenCode 的 JSONC 使用定点编辑保留注释，其他客户端配置中的非标准注释会明确报错。

每次修改现有配置前，会在其旁边生成带时间戳的备份。登记记录保存在本仓库 `.agent-data/client-registrations.json`，包含本工具条目及其配置位置，不包含账号凭据。此目录不提交到 Git。

```powershell
# 预览移除；仅移除由此副本登记且未被更改的条目
.\.venv\Scripts\python.exe configure_clients.py --uninstall --dry-run
.\.venv\Scripts\python.exe configure_clients.py --uninstall
```

项目级移除需带原来的 `--scope project --directory ...`。移除不删除任务数据、Python 环境或其他 MCP 服务。若登记记录丢失或条目已被人工修改，自动移除会拒绝操作；可在对应客户端配置中核对后手动移除 `repo_wayfinder`。不要直接恢复整份旧备份覆盖其后的其他配置修改。

移动或删除仓库前先移除登记，再从新位置运行接入。脚本不会擅自覆盖旧位置的同名条目。

## 配置依据

Grok、Codex、Cursor 的适配依据为 [Grok 官方 MCP 文档](https://docs.x.ai/build/features/mcp-servers)、[OpenAI 官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp)及 [Cursor 官方 MCP 文档](https://prod.cursor.com/help/customization/mcp)。本功能仅使用本地 STDIO，无账号变更、额外模型调用或远程配置上传。

其他适配依据：[Claude Code](https://code.claude.com/docs/en/mcp)、[Claude Desktop 本地 MCP](https://modelcontextprotocol.io/docs/2026-07-28/develop/connect-local-servers)、[Gemini CLI](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md)、[OpenCode](https://opencode.ai/docs/mcp-servers)、[VS Code](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)。Windsurf 此处指旧版 Cascade 配置，不将新 Devin 应用误写到旧目录。Claude Code 若设置了自定义 `CLAUDE_CONFIG_DIR`，当前用户级自动适配会明确拒绝，避免写错位置。

OpenCode 同时适配 v1 与 v2 的 MCP 层级，通过版本或现有结构识别；检测到版本与配置布局冲突时，要求先迁移原配置，避免混写。DSH 适配还核对了本机发行包实际读取 web profile 的代码，并用临时 profile 的 `--dump-config` 验证插件合成，没有调用模型或改动真实账号配置。

## 本次验证

35 项配置相关检查通过，覆盖检测、多选、未选择不写入、幂等、同名冲突、配置保留、原件备份、并发修改保护和限定条目卸载。本机实际预览发现 Grok、Codex、Cursor、DSH、VS Code；无显式选择时返回 `selection_required`，真实客户端配置保持原样。临时项目完成三种配置的注册、重复运行与卸载，并通过生成的启动命令完成 MCP 初始化，列出 9 个工具。DSH 另经临时 profile 的实际配置合成验证；未调用模型。

未安装客户端的适配通过配置样例验证，不能据此声称每种桌面程序都已实际启动验收。各客户端中的实际工具加载，可在用户选择接入并新开会话后检查。
