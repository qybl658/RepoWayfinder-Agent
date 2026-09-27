# RepoWayfinder Agent

让 AI 把公开 GitHub 项目变成可验证的本地任务结果。

这是独立的 AI 工具版本。调用方理解目标、选择项目；工具获取固定源码、准备项目环境、执行并保存证据。模型可继续用宿主原生工具编写文档、处理文件和检查结果，无需把所有工作塞进专用接口。完整日志和结果留在本地，默认只返回紧凑状态与证据路径。内部不调用模型。

当前定位是 **Windows 上有限时长的 CLI / 仓库运行任务**，适合 Grok 等支持本地 MCP 的 Agent。外部代码以当前用户权限运行，规则检查不是操作系统沙箱。

## 用实测看增效

2026-09-27，Grok 4.7 / high 完成三个固定源码任务：两个轻量部署项目，以及带登录、权限和数据库持久化的 Flaskr。每个任务各跑一组原生工具与一组原生工具 + RepoWayfinder，六次全部完成，不设模型轮数或进程时间上限。

| 任务 | 独立验收 | 原生用时 | 加本工具用时 | 少用时间 |
| --- | --- | ---: | ---: | ---: |
| Bottle 报价 API | 两组均 27/27 | 750.609 秒 | 467.391 秒 | **283.218 秒（37.73%）** |
| http-server 静态站点 | 两组均 25/25 | 590.796 秒 | 443.157 秒 | **147.639 秒（24.99%）** |
| Flaskr 登录与持久化 | 两组均 30/30 | 973.890 秒 | 926.782 秒 | **47.108 秒（4.84%）** |

| 任务 | 原生 token | 加本工具 token | 本次记录变化 |
| --- | ---: | ---: | ---: |
| Bottle 报价 API | 2,279,337 | 889,194 | **少 60.99%** |
| http-server 静态站点 | 1,293,294 | 1,159,729 | **少 10.33%** |
| Flaskr 登录与持久化 | 1,966,181 | 1,967,386 | **多 0.06%（接近持平）** |

每组仅一个样本，固定工具组先跑；缓存与顺序影响未控制。token 合计包含缓存读取，不等于费用节省。时间覆盖模型运行器启动至退出，独立验收、开发及主控时间另计。Flaskr 的 token 未减少，不能据此宣传复杂任务普遍增效。

[三方向数据](benchmarks/results/2026-09-27-deployment-matrix.json) · [任务、验收与问题说明](docs/DEPLOYMENT_COMPARISON.md)

### 此前的课程手册任务

同一个课程手册任务，由模型分别使用原生工具、原生工具 + RepoWayfinder 完成；两组都独立验收通过 21/21。

| 模型 / 宿主 | 原生 token | 加本工具 token | 本次记录减少 |
| --- | ---: | ---: | ---: |
| DeepSeek Flash / DSH | 733,537 | 311,773 | **57.5%** |
| Grok 4.7 high | 2,857,855 | 654,870 | **77.1%*** |

| 模型 / 宿主 | 原生用时 | 加本工具用时 | 少用时间 | 本次活跃用时减少 |
| --- | ---: | ---: | ---: | ---: |
| DeepSeek Flash / DSH | 161.159 秒 | 133.021 秒 | **28.138 秒** | **17.5%** |
| Grok 4.7 high | 898.876 秒 | 526.167 秒 | **372.709 秒（约 6 分 13 秒）** | **41.5%*** |

计时覆盖模型进程启动、工具执行和收尾；独立验收与开发时间另计。Grok 原生值包含两段活跃运行，未计中间 106.560 秒的恢复间隙。

这是单任务单组样本，合计包含缓存 token，不代表账单或普遍收益。*Grok 原生组曾被测试器轮数上限打断，随后在同会话续完；表中计入全部续跑消耗，因此不是完全无干扰的节省估计。当前入口默认不再设置轮数或时间上限。

[可读取的实测数据](benchmarks/results/2026-09-27-course.json) · [Grok 完整口径](docs/GROK_COMPARISON.md) · [DSH 完整口径](docs/DSH_COMPARISON.md)

面向普通 Windows 用户的菜单、项目发现与部署入口，请看原版 [RepoWayfinder](https://github.com/qybl658/RepoWayfinder)。本仓库提供面向 AI Agent 的独立 MCP 接口。

## 接入 Grok

需要已有 Python 3.11+、Git 和 Grok CLI。PowerShell 中进入本目录：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe configure_grok.py
grok
```

首次由 Grok 显示目录信任提示，确认本目录后使用。注册只写项目 `.grok/config.toml` 的 `repo_wayfinder`，不修改全局配置、不读取或保存账号凭据。同名服务拒绝覆盖，其他配置保留，修改前生成备份。移除这一段 MCP 配置即可解除接入；任务输出另行保留或清理。

在别的工作目录使用：

```powershell
.\.venv\Scripts\python.exe configure_grok.py --directory D:\my-work --workspace D:\my-work\repo-jobs
grok --cwd D:\my-work
```

配置记录工具和 Python 的绝对路径，移动后需要重新注册。Grok 工具超时设为 75 秒，单次等待最多 50 秒，耗时任务由后台作业继续。

可以直接对 Grok 说：

> 用 repo_wayfinder 从指定 GitHub 提交安装这个 CLI，实际生成需要的文件。提供明确计划和输出检查，用 rw_run 执行。未完成时用 rw_status 等待；失败才读必要日志。最后给文件路径和验证结果。

## 工具

| 工具 | 用途 |
| --- | --- |
| `rw_run` | 批量提交任务文件、命令和结果检查，最多等待 50 秒，完成即返回 |
| `rw_prepare` | 获取源码并准备计划，不执行目标代码 |
| `rw_execute` | 执行已准备的计划 |
| `rw_replan` | 在同一环境局部修改任务文件、换执行步骤；`execute=true` 可直接复验运行 |
| `rw_status` | 紧凑状态，可等待 0–50 秒，通常用 30 秒 |
| `rw_logs` | 必要时读取有限长度的日志尾部 |
| `rw_resume` | 解决阻碍后，明确重跑保存的计划 |
| `rw_cancel` | 协作式取消，当前命令可能先完成 |
| `rw_search` | 搜索公开 GitHub 元数据，不做模型推荐 |

已知任务用 `rw_run(repository, commands, checks)`。只准备环境时可以省略 checks；有版本要求时传 `python_version="3.11"`。返回的 `project_path`、`revision` 和 `result.python_runtime.executable` 可供原生终端继续工作。安装完成仅证明安装，最终交付仍须检查。

`python_runtime.version` 是实际运行的版本，`base` 记录创建此虚拟环境时使用的解释器路径，可能本身也是一个虚拟环境；它不是 `sys.base_prefix`。

需要一次完成有限任务时，可提交文件、命令与检查。不必生成 `DEPLOY`、步骤类型、用途和理由。这个入口不把 `main.py` 等名字猜成 Web 服务。需要先审阅计划或做服务器运行探测时用 prepare → execute；prepare 的缺省路线仍是继承的启发式，可能不适合目标。

每步一个命令，不支持管道或 shell 串联。`python` / `pip` 映射到项目虚拟环境。最多 20 步，每步最多 600 秒。安装依赖会运行第三方安装逻辑；账户、许可、提权和系统运行时安装交给用户处理。

Python/pip 步骤或明确指定的 `python_version` 会自动创建或复用作业虚拟环境，无需 Agent 再创建/激活；未选择 Python 的纯 shell 计划不会触发创建。版本选择在同一作业恢复期间保持不变；与仓库版本要求冲突时暂停，不自动换版本。服务命令使用 `python`/`pip`，显式解释器路径和 `py -3.11` 会提示改用 `python_version`，避免悄悄改写解释器。显式执行 Python 标准 `venv` 模块时使用对应的基础 Python，避免 Windows 上让正在运行的虚拟环境覆盖自身解释器。

脚本内再启动 Python 时使用 `sys.executable`，安装用 `[sys.executable, '-m', 'pip', ...]`；其他子程序用 `shutil.which` 取得完整路径。服务统一传递作业 PATH、VIRTUAL_ENV 与 UTF-8 设置，但 Windows 的裸 `python` 查找仍可能选中基础解释器，不能声称自动隔离任意子进程。目标进程设置 `PIP_REQUIRE_VIRTUALENV=1`，误用系统 Python 安装会提前失败；不修改主机环境，也不把这项保护当作 OS 沙箱。此约定遵循 [Python subprocess 文档](https://docs.python.org/3/library/subprocess.html#subprocess.Popen)。

0.3 新增 `files: [{"path": "task/run.py", "content": "..."}]`：获取源码后统一写入新的 UTF-8 任务文件，再执行命令。最多 32 个文件、合计 256 KiB，路径相对源码根目录，不覆盖已有文件。可一次提交配置、输入和编排脚本，连续完成生成、构建、修改、复验与打包，不必先探测源码路径再逐个写文件。原始输入和完整日志保存在作业内，结果仅回传摘要。执行前复核输入，未变化的输入不能算新产物。

Agent 的显式命令不再按旧版工具名白名单拒绝：程序统一从作业虚拟环境、PATH 或显式路径解析，Windows `.cmd`/`.bat` 复用带引号的执行入口。PowerShell cmdlet、CMD 内建命令须通过对应 Shell 显式调用；不会将不存在的程序猜成 Shell 代码。Robocopy 保留原始返回码，0–7 按其文档作为非失败结果。语法检查、已知危险操作检查、Git 外部写入限制和现有源码校验仍在；这些检查不是 OS 沙箱，也不能保证任意脚本安全。

0.2 的 MCP `rw_run` 改用 `commands` 数组；旧调用方请刷新工具定义。需要结构化计划的调用仍可使用 `rw_prepare` / `rw_replan`，JSON CLI 也继续接受旧 `plan` 格式。

## 结果与证据

默认位于 `.agent-data/jobs/<job_id>/`：源码、计划、`job.json`、`worker.log`、每次执行的 `runs/*.json` 和 `deployment_result.json`。退出对话后仍能凭 job_id 查询。`request_id` 防止同一请求重复提交；同一 ID 携带不同内容会拒绝。

- `task_verified`：执行成功且调用方检查全部通过，只证明这些检查覆盖的结果。
- `runtime_verified`：运行探测通过，不等于业务目标完成。
- `command_verified`：命令执行成功，没有足够的结果断言。
- `waiting_environment` / `blocked` / `failed`：不能宣称成功。

支持 `file_exists`、`file_contains`、`json_value` 和 `stdout_contains`。文件检查默认要求本次执行产生结果；对于刻意保留的前阶段快照等，显式设置 `freshness: "preserved"`，要求它在本作业此前已验证且 SHA-256 仍一致。输入变化后应重新计算的结果不能借此冒充新产物。未经验证的旧文件、预置答案不能直接通过。内容检查上限 20 MiB，路径相对任务源码目录，JSON 检查使用 JSON Pointer。

0.4 的恢复入口复用原作业与虚拟环境。例如 `rw_replan(job_id, edits=[{path,old,new}], commands=[...], checks=[...], execute=true, request_id="fix-1")`。只提交需要修改的文本和需要重跑的步骤；配置、文档直接用文件表达，脚本只保留必要编排。每条旧文本须唯一匹配，整批输入先校验再写入；仅支持未跟踪的任务文件，拒绝源码、运行环境目录、链接及共享文件。省略 commands/checks 时复用原值。0.4.1 增加前一阶段产物的验证记录复用，返回原验证 attempt ID；不会把它描述为本轮新生成的结果。MCP 标准输入输出固定 UTF-8，不依赖 Windows 本地代码页。

旧计划、变更前文件和独立运行结果均保留；每个结果的 evidence_path 指向不可变的单次报告。恢复 request_id 重放只查询既有操作，改参数须换 ID。自动执行从准备到运行保持活动状态；同一作业由一个执行进程负责。输入修订中断可回滚输入，命令本身不具备事务性，进程中断后仍须核对副作用再显式恢复。检查上限 128 条，包含判断文本最长 4,000 字符，这些边界同时公布于工具 schema。

执行前复核源码提交、已跟踪文件和计划输入。取消是协作式的；恢复重跑整个计划，须先核对部分完成的副作用。运行探测结束会停止测试服务器，探测 URL 不是持久服务。

## JSON 入口

```powershell
.\.venv\Scripts\python.exe agent.py call rw_run --file examples\gitingest.json
.\.venv\Scripts\python.exe agent.py call rw_status --json '{"job_id":"返回的ID","wait_seconds":30}'
```

示例获取公开源码，在作业虚拟环境安装 CLI 并生成 `digest.txt`。需要网络；运行前请检查计划。

## 让 Grok 接手一个任务

将普通、非敏感任务写进 UTF-8 文本文件，说明目标、仓库和验收条件。完成 MCP 注册和目录信任后：

```powershell
.\.venv\Scripts\python.exe grok_task.py --prompt-file D:\my-task.txt
```

默认沿用 Grok 已配置的模型，也可显式传 `--model`。默认 `--execution-mode tool-only` 只允许文件读取和本服务调用；`--execution-mode companion` 同时开放原生终端和文件编辑，以过程级 `--always-approve` 运行。它使用已有 Grok 登录与额度，不创建账号或切换付费 API。原始对话留在 `.agent-data/grok-runs/`，终端只返回截短结论、用量和本地证据路径。

默认等待任务完成，不设置轮数或时间上限。需要明确预算时才传 `--max-turns N` 或 `--timeout 秒数`；0 表示不限制。意外中断后可在同一 `--directory` 用 `--resume 会话ID` 和新的 `--output` 续接，保留原产物与全部分段用量，不必重新克隆安装。会话 ID 在原 trace 的 `system/init.session_id` 中。

入口会附上精简调用说明，让 Grok 优先只发现当前需要的工具。每轮 token、工具参数长度和模型报告的 API 时间保存在本地 `summary.json`，便于定位开销；不将完整推理或日志塞回调度方上下文。

`grok_finished` 只表示 Grok 正常结束，不能代替目标文件检查。CLI 报告的 cost/token 不代表实际账单或剩余额度；没有用量字段时保留未知。任务文件与原始对话不要提交 Git。若手动中断或显式设置的限额触发，已经提交的后台作业仍须查询或取消。

## 接入 DSH

`dsh_task.py` 使用已有 DSH Desktop 便携包内置的官方 Harness SDK，单独启动一次任务，并通过 stdio MCP 调用本工具。需要该包自带 Node/SDK，以及启动进程中已有的 `DEEPSEEK_API_KEY`。它不读取或保存桌面版的密钥，也不修改原桌面会话。

默认 `--tool-mode companion` 同时开放 DSH 原生 PowerShell 和文件能力，使用 `--permissions workspace-write`。可明确指定已授权的其他权限模式；不会更改桌面全局设置。`--tool-mode tool-only` 保留原来的仅服务执行方式。Grok 的任务入口也可通过 `--execution-mode companion` 组合原生工具。

```powershell
.\.venv\Scripts\python.exe dsh_task.py --bundle D:\DSH-Desktop --directory D:\my-work --prompt-file D:\my-task.txt --output D:\my-work\dsh-run
```

任务运行配置、SDK 原始事件和紧凑用量摘要保存在输出目录。模型来源写入摘要；没有报告的用量或费用保持未知。接入方式和实际验证范围见下方记录。不要将输出目录或密钥提交 Git。

## 实测与开发

见 [Grok、DSH、Codex 接入记录](docs/INTEGRATION_TESTS.md)、[Grok 对照记录](docs/GROK_COMPARISON.md) 和 [DSH 对照及完整时间线](docs/DSH_COMPARISON.md)。比较完成同一目标的时间、模型报告的 token、往返次数和结果质量；成功单次运行与整个测试过程分别记录，不把 token 当作订阅额度百分比，不把一次实验外推成普遍收益。

0.5 的一次正式 DSH 对照中，两组课程手册均通过独立 21 项检查，工具组耗时 133.021 秒、原生 161.159 秒；SDK totalTokens 为 311,773 与 733,537。开发预跑耗时 194.030 秒，说明波动仍然明显；这不是稳定收益或费用比例承诺。完整试次均保留。

相同任务的 Grok 对照也最终通过 21/21：companion 为 654,870 token / 526.167 秒，原生为 2,857,855 token / 898.876 活跃秒。原生曾被测试器上限中断，随后在原会话续完；表内计入全部续跑消耗，不能当作完全无干扰的净节省估计。见 [Grok 完整记录](docs/GROK_COMPARISON.md)。

本分支继承公开 RepoWayfinder 执行核心（基线 `be3e5a060b178d42178681b854282de5e46b2d87`），AI 入口为 `agent.py`。旧菜单、API 配置、浏览器弹窗和自动安装流程不属于该入口。根目录遗留脚本暂留作核心迁移参考；原小白版本独立保留。

受影响的 Agent 检查：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_agent_*.py'
```
