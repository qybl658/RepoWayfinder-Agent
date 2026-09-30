# RepoWayfinder Agent

让 AI 批量完成仓库执行、文件检查和本地服务验证。

这是独立的 AI 工具版本。调用方理解目标、选择项目；工具获取固定源码、准备项目环境、执行并保存证据。模型可继续用宿主原生工具编写文档、处理文件和检查结果，无需把所有工作塞进专用接口。完整日志和结果留在本地，默认只返回紧凑状态与证据路径。内部不调用模型。

当前定位是 **Windows 上有限时长的 CLI / 仓库运行任务**，适合 Grok 等支持本地 MCP 的 Agent。外部代码以当前用户权限运行，规则检查不是操作系统沙箱。

需要获取固定源码、准备环境、保留执行证据或恢复失败作业时使用本工具。已有合适环境的简单读改和命令可直接用原生工具；不要为了使用本工具而重新克隆、安装或增加作业。原生续接时，每次终端调用都显式切到返回的 `project_path`，Python 使用返回的 `result.python_runtime.executable`，不要假定上一次调用的工作目录会保留。

已有任务目录也可以直接使用 `rw_verify`：批量检查 CSV、JSON 和文本，按明确要求运行及复跑命令，或启动本地 HTTP 服务、请求、重启并清理。无需 Git 仓库或部署作业。业务代码和预期结果仍由调用者决定；工具只替代重复的解析、请求与进程管理。接入时会通过标准 MCP 服务说明提供通用使用指引，不复制私人规则或账户信息。[能力与例子](docs/LOCAL_VERIFICATION.md)。

选择工具时看它能否省掉一整段重复工作。已有紧凑检查继续使用原生工具，不必改成同样长的清单；仍有效的检查结果直接复用。HTTP 错误案例可用原始文本或字节数组与正常请求同批验证，无需另写编码脚本和服务控制程序。

## 可以复用的相邻流程

| 流程 | 当前接口覆盖 | 使用边界 |
| --- | --- | --- |
| 仓库运行、构建和测试 | 获取固定源码、准备项目环境、执行有限命令并保留证据 | 命令和业务验收由调用者确定，安装成功不等于任务完成 |
| 输入、配置和交付文件的失败恢复 | 在原作业内修改新增的任务文件并复跑，保留此前记录 | 不能用 `rw_replan` 修改已跟踪源码；重跑前须核对已发生的操作 |
| 本地 HTTP 服务验证 | 批量请求、重启、清理服务，生成实际执行报告 | 仅覆盖调用者提供的断言，已有结果应直接复用 |
| CSV、JSON、文本产物检查 | 批量解析和检查已有目录中的文件 | 长断言清单可能比原生脚本更贵，不能默认迁入工具 |

这些流程复用同一套执行与证据能力。具体任务是否省 token、是否更快，仍以完整任务结果为准。

## 批量处理本地文件示例

现在有一条可直接复用的流程：[批量 Markdown → HTML](examples/cli-batch/README.md)。入口读取本地中文文档，调用固定版本的 Python-Markdown CLI，生成可打开的完整 HTML 页面，并保留同批文档的本地链接。正文不需要由模型逐字重写，转换过程不调用模型。

先按[自动接入](#自动接入)准备 Agent 环境，再运行：

```text
python examples/cli-batch/run.py --input-directory "D:/我的文档" --output-directory "D:/文档交付"
```

它复用已有的仓库获取、项目环境、批量执行和作业恢复能力。输入与旧交付不会被覆盖；输出目录和 job ID 由结果返回。局部失败沿用原作业修复，已经导出的文件作为快照保留。[输入限制与恢复方法](examples/cli-batch/README.md)。正常转换与同一作业的缺输入恢复已通过独立产物核对；首次入口环境失败及检查脚本修正均保留在[执行记录](benchmarks/results/2026-09-30-cli-batch.json)。这是一条具体工作流的扩展，尚未做模型 token 性能对照。

## 用实测看增效

### 原装 Grok 的本地验证：收益依任务而异

2026-09-30，隔离客户端配置不加载私人规则；产品组只增加完整 MCP 与正常分发的说明，双方保留原生能力。结果如下，均为单组观察：

| 任务与版本 | 独立业务验收 | 产品组会话 token 变化 | 结论 |
| --- | --- | ---: | --- |
| CSV 对照 | 两组 15/15 | **多 24.73%** | 长检查脚本被换成长清单，不适合默认迁入工具 |
| HTTP 服务 a4 | 两组 18/18 | 少 7.58% | 尚有工具外的原始请求验证流程 |
| HTTP 服务 a5，复用 a4 原生基线 | 两组 18/18 | **多 0.31%** | 额外服务消失，新增编码失败与恢复抵消了收益 |

最终接口进一步支持直接文本和字节数组，原 38 个请求与重启已通过无模型等价回放；**这个最终修订的端到端性能尚未测量**。功能完整、调用次数减少或工具执行很快，都不能代替总 token 证据。服务原生基线还出现 754.750 秒未归因间隔，耗时差不能当作稳定提速。

统计包含缓存读取，不等于费用；独立业务断言也不代表所有质量维度完全相同。全部九次尝试、设置阻断、失败及受污染对照均保留。[完整结果与限制](docs/LOCAL_VERIFICATION_COMPARISON.md) · [数据](benchmarks/results/2026-09-30-local-verification.json) · [复现材料](benchmarks/local-verification/README.md)。以下较早结果保留其原有条件与限制。

### 课程手册生成

2026-09-27 的首组记录：同一个课程手册任务，由模型分别使用原生工具、原生工具 + RepoWayfinder 完成；两组都独立验收通过 21/21。下方同时保留 9 月 30 日重复实验中的弱收益和反例。

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

### 轻量部署与状态应用

Grok 4.7 / high 完成三个固定源码任务：两个轻量部署项目，以及带登录、权限和数据库持久化的 Flaskr。每个任务各跑一组原生工具与一组原生工具 + RepoWayfinder，六次全部完成，不设模型轮数或进程时间上限。

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

### DSH 重复实验：收益并不稳定

2026-09-30，DeepSeek Flash / DSH 对以上四个任务各跑两组配对，交替先后顺序，共 16 次；业务验收全部通过。下表合并每个任务的两组原始成本，包含范围审计有争议或不通过的样本，不能把它们称为完全合规条件下的节省。

| 任务 | 工具组时间变化 | 工具组 token 变化 | 保守目录范围审计 |
| --- | ---: | ---: | --- |
| Sphinx 课程手册 | 少 6.71% | 少 24.01% | 2/4 通过；无完整可比配对 |
| Bottle 报价 API | 少 30.14% | 少 59.10% | 2/4 通过；无完整可比配对 |
| http-server 静态站 | **多 26.37%** | **多 9.30%** | 4/4 通过 |
| Flaskr 登录与持久化 | **多 6.93%** | 少 6.10% | 3/4 通过；一个可比配对 |

范围审计共 11/16 通过：课程两次明确违反“全部任务写入留在工作目录”，另三次为辅助日志/验证数据位于系统临时目录，按任务范围作保守不通过处理。后者与明确违约分列，不代表交付物缺失或已发生数据损坏。Flaskr 唯一完全可比配对时间少 3.24%、token 少 29.30%；这是事后审计子集，不应挑选它替代完整结果。

工具组每个任务平均模型响应更少，但更少轮次没有稳定转化为更快、更省。DSH 加载了更多工具定义，且静态站第一次工具组的工具等待明显增加；尚未通过消融实验分离各自影响。已有环境和简单任务不应仅为使用工具而重复准备。两组都使用 SDK 实际生效的 high 思考设置；跨模型的 high 不是统一计算预算。

[全部配对、失败与机制边界](docs/DSH_REPEATED_COMPARISON.md) · [逐次分项数据](benchmarks/results/2026-09-30-dsh-repeats.json)

[机制复审](docs/EFFICIENCY_MECHANISMS.md)拆分了轮次、输入上下文与输出，改进优先压总token并尽量缩短时间；本地 HTTP 探测替代只证明了局部时间收益。另一次 [Grok 日常规则对照](docs/GROK_DAILY_EFFICIENCY.md)出现负收益：两组业务通过，扩展规则组却更慢、更费，已撤回长段指导并窄修具体调用问题；通用性能改善仍未证。性能数据用于选择适用任务和改进流程，不构成固定比例的承诺。

面向普通 Windows 用户的菜单、项目发现与部署入口，请看原版 [RepoWayfinder](https://github.com/qybl658/RepoWayfinder)。本仓库提供面向 AI Agent 的独立 MCP 接口。

### DSH 静态部署修复：复用实际验证报告

模型此前为交付 `smoke.log`，先写原生 HTTP 验证脚本，再调用 `rw_verify`，重复执行了服务验证。现在 HTTP 验证返回 `summary_path`，模型可读取实际报告并用于交付。

修复后的单次任务独立验收 **25/25**、写入范围审计通过；模型只执行了一套 HTTP 生命周期，并复用了报告。记录为 14 次响应、19 次工具调用、277,903 token（含缓存）、80.844 秒。此前两个候选的范围审计未通过，不能据此给出严格匹配的节省率；仅改指引的候选与最终候选 token 接近，也不支持宣称普遍大幅降本。[修复证据与限制](docs/DSH_HTTP_REPORT_REUSE.md)。

### 国内模型：本轮尚未形成有效收益对照

通义的原生组在客户端能力修复后完成 23/25 项检查，随后免费额度耗尽，未启动工具组。智谱两组均存在交付结构缺失；工具组虽连接了 10 个 MCP 工具，70 回合内实际调用为 0。宿主审批迟到与模型操作错误均保留，不能把接入成功或会话 token 差异当成工具收益。[完整尝试与限制](docs/DOMESTIC_MODEL_COMPARISON.md)。

## 自动接入

Windows 已有 Python 3.11+ 和 Git 时，在仓库目录运行：

```powershell
.\setup.cmd
```

入口会准备项目自己的 Python 环境，检测受支持的已安装客户端。**检测到多个时，先由你多选，再配置所选项**；也可以在命令行显式指定客户端。支持 Grok、DSH、Claude Code、Claude Desktop、Codex、Cursor、Gemini CLI、OpenCode、VS Code 和 Windsurf。已有相同配置保持不变，同名配置指向别处则提示冲突；修改前备份，不改登录、模型、目录信任或工具审批设置。安装完重新打开客户端会话；Cursor 如未加载新工具，重启客户端。

已有项目环境时可直接运行，或先查看将修改的位置：

```powershell
.\.venv\Scripts\python.exe configure_clients.py --dry-run
.\.venv\Scripts\python.exe configure_clients.py
```

只接入 Grok，或仅为某个项目配置：

```powershell
.\.venv\Scripts\python.exe configure_clients.py --clients grok
.\.venv\Scripts\python.exe configure_clients.py --scope project --directory D:\my-work
```

配置使用本地绝对路径；保留此仓库目录，移动后需要重新配置。首次项目目录信任仍由客户端处理。[检测规则、配置位置与移除方式](docs/AUTO_SETUP.md)。

接入后可直接对 Agent 说：

> 用 repo_wayfinder 从指定 GitHub 提交安装这个 CLI，实际生成需要的文件。提供明确计划和输出检查，用 rw_run 执行。未完成时用 rw_status 等待；失败才读必要日志。最后给文件路径和验证结果。

原有 `configure_grok.py --directory D:\my-work --workspace D:\my-work\repo-jobs` 仍可使用，只配置 Grok 的项目级入口。

## 工具

| 工具 | 用途 |
| --- | --- |
| `rw_verify` | 已有目录中的 CSV/JSON/文件断言、显式运行与复跑、本地 HTTP 服务验证 |
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

默认不限制模型响应轮次或任务总时间；如有明确预算，可传 `--max-model-calls N` 或 `--timeout 秒数`，0 表示不限制。中断后的后台作业须核对状态和已有副作用。

## 复现实测

课程手册与部署矩阵的固定任务、验收器和脱敏摘要位于 `benchmarks/`。完整统计口径见 [Grok 对照](docs/GROK_COMPARISON.md)、[DSH 历史对照](docs/DSH_COMPARISON.md)、[DSH 两组重复对照](docs/DSH_REPEATED_COMPARISON.md) 和 [部署对照](docs/DEPLOYMENT_COMPARISON.md)，历史接入范围见 [客户端验证](docs/CLIENT_VALIDATION.md)。Grok 各任务只有一组，DSH 新批次各两组，保留失败、恢复和弱收益，不能外推为普遍节省。

本仓库保留 Agent / MCP 入口、10 类客户端自动接入、执行与恢复核心、有效测试和复现证据。`main.py` 是被 Agent worker 导入的共享执行核心；直接运行会提示使用 Agent 入口。`wsl_status_utils.ps1` 是运行条件检测 helper。虚拟环境归档恢复脚本由核心按需生成，不依赖旧菜单。任务配置由显式 files / edits 和宿主原生工具处理，恢复使用 `rw_replan` / `rw_resume`。

原小白菜单、语言/API 配置、安装卸载及双套快捷入口属于独立的 [RepoWayfinder](https://github.com/qybl658/RepoWayfinder) 产品。这里不生成指向这些入口的报告脚本。

本地来源已做有限的[执行与恢复原型](docs/LOCAL_SOURCE_EXPERIMENT.md)：一次本地合成 Git 获取，失败后复用同一作业和环境，10 项检查通过。生产接口仍只接受 GitHub；未开放任意本地路径、任意 URL 或嵌套 Agent。

```powershell
python -m unittest discover -s tests -p 'test_agent_*.py'
```
