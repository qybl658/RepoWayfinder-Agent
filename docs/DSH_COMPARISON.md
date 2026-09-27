# DSH 对照：当前结果与完整历史

最新正式对照为 0.5 companion 候选 `f2911528bdbf2d78e03762db60b6e60d1cebe027`：相同课程手册任务，两组均通过独立 21 项产物检查、实际 Python 3.11.9 核验和限定范围的命令审计。本次工具组少用 17.5% 时间、57.5% SDK totalTokens。只有一组成对样本，不能承诺稳定加速或同等比例省钱。此前负面结果全部保留。

**本页新结果全部来自 DSH，模型为 deepseek-official/deepseek-flash。不是 Grok；入口是 DSH 自带 Harness SDK，不是桌面界面操作。**

## 0.5：工具配合原生能力

之前的工具组关闭原生 PowerShell，任务编写和恢复被迫经过专用 files/checks/replan 接口，多次重传大段内容。这轮保留固定源码、环境准备、有限命令执行和恢复证据，让原生工具完成文档编写、文件转换与检查。这个方向来自失败记录；下述试验检验整个方案，不把变化单独归因于某一个提示或接口。

运行代码先冻结，工具预跑通过后，在不改实现的情况下另开正式工具组和原生组。任务沿用课程实验手册要求，仅明确两组都使用已给定的 Python 3.11.9。两组同为 DSH Desktop 0.9.2 / Harness 0.1.5-rc.2、deepseek-official/deepseek-flash、SDK 默认思考设置、danger-full-access / never，无模型轮数上限，15 分钟异常超时保护未触发。原生 PowerShell、jobs 和文件能力相同；工具组额外有 RepoWayfinder MCP。两组都有进程级 `PIP_REQUIRE_VIRTUALENV=1`，依赖缓存共享，目录各自全新。

| 正式比较 | 原生 | 配合工具 |
| --- | ---: | ---: |
| 完整进程耗时 | 161.159 秒 | 133.021 秒 |
| 模型响应 | 25 | 13 |
| 工具调用 | 35 | 19 |
| inputTokens | 19,299 | 15,961 |
| cacheReadTokens | 688,512 | 276,864 |
| outputTokens | 25,726 | 18,948 |
| SDK totalTokens | 733,537 | 311,773 |
| reasoningTokens（output 子集） | 14,729 | 12,729 |
| 独立产物检查 | 21/21 | 21/21 |
| 实际 Python | 3.11.9 | 3.11.9 |

两组均从指定 Sphinx 提交源码安装，无 docs/test/lint extras；执行两次真实 `-W` 构建，保留初版、修改 FAQ 源文件、清理本次 site、验证实际搜索命中及 ZIP 逐文件内容。每组最终站点 29 个文件，工具 117,413 字节、原生 118,211 字节；质量按共同要求判断，不声称两站逐字节相同。服务安装完成时只返回 `command_verified`，完整任务结论由独立验收与命令审计得出。

命令与生成脚本审计未观察到全局安装或直接改写生成的 HTML/搜索索引；Python 3.11、3.12、3.14 的系统及用户包清单在预跑、正式工具组、原生组之后都与试验前相同。这是观察到的命令和持久包状态证据，不是完整 OS 文件访问审计，也不证明旧 tool-02 的历史全局改动已完全恢复。

### 开发成本与结论边界

开发预跑 `dev-tool-01` 也通过 21/21，但耗时 194.030 秒、24 次模型响应、38 次工具调用；input=20,996、cacheRead=665,344、output=29,398、total=715,738、reasoning=17,272。它比正式工具组明显更慢，不能据单次正式成绩声称稳定收益。预跑没有冒充正式样本，所有尝试均保留。

本轮三次模型任务合计 **488.210 秒、1,761,048 SDK totalTokens**。这不是把研发成本算进去后仍然节省的结论。此前全部预跑另见历史记录；主控、审查、开发与独立验收的时间/token 没有完整可靠计量，未混入正式表格。外层计时取自 attempt-ledger；reasoning 不重复累加，缓存与非缓存不按同价解释，费用和订阅额度变化未知。

当前可支持的结论：companion 路线在这一正式样本中，以满足同一交付要求为前提，减少了任务时间和模型用量。两组先后顺序、共享缓存、服务延迟和模型波动仍有限制；不外推到其他项目、模型或普遍可靠性。Grok、Codex 的旧接入证据保留，本轮未重新跑这两个模型。

### 证据与使用

本机根目录：`D:/CodexWorkspace/RepoWayfinder-Agent-20260927/dsh/companion-course/`。

- `experiment.json` 记录预定顺序和验收条件；`comparison.json` 汇总全部三次尝试，`attempt-ledger.jsonl` 记录完整进程时间。
- `dev-tool-01-run/`、`formal-tool-01-run/`、`formal-native-01-run/` 各保存 summary、原始 trace、独立验收和 command-audit。
- `base-packages-*.json` 是三套基础 Python 的只读包清单，原始内容留本地。
- 工具作业：预跑 `336dd8593f87491daee7b36465827678`；正式 `f84d4144b05f4b84bc4f5c7d281ee34b`。

使用 `dsh_task.py --tool-mode companion`，在 `rw_run` 中按任务指定 `python_version`；后续原生执行使用返回的 `result.python_runtime.executable`。仍可选择 tool-only 模式或直接使用批量文件、检查和恢复接口。42 项 runtime/service/MCP 检查及真实 stdio→worker 的失败恢复、版本冲突检查通过；未把本轮当作原 RepoScout 发行验收，也未发布或推送。

## 历史结果

## 第一轮新任务：工具更慢，也更耗 token

2026-09-27，两组都以 `danger-full-access / never` 运行，没有 OS 沙箱与模型轮数上限。使用同一 DSH SDK、`deepseek-official / deepseek-flash`、同一新任务正文，各自全新工作目录。候选提交 `f48471bcfcb60b56ba255abf8931249fcd5539f3`，运行期间未改实现。

任务：从 MkDocs 1.6.1 固定提交源码安装，制作三页中文文档站，包含导航、跨页锚点、本地 SVG 与搜索；strict 构建 V1 并留快照，修改 FAQ 再 strict clean 构建 V2，清除旧文件，交付内容完整一致的 ZIP。

| 累计指标 | DSH 原生 | DSH + RepoWayfinder |
| --- | ---: | ---: |
| 整组启动次数 | 1 | 1 |
| 模型响应总数 | 18 | 18 |
| 工具调用总数 | 17 | 29 |
| 整组总耗时 | 94.271 秒 | 150.602 秒 |
| SDK 内部耗时 | 94.125 秒 | 150.469 秒 |
| inputTokens | 7,716 | 12,925 |
| cacheReadTokens | 171,008 | 446,464 |
| outputTokens | 9,942 | 24,733 |
| totalTokens | 188,666 | 484,122 |
| reasoningTokens（包含于 output） | 4,167 | 17,776 |
| 独立功能验收 | 通过 | 通过 |

**工具组耗时增加 59.8%，总 token 增加 156.6%，输出 token 增加 148.8%。** 两组各一次连续会话，无丢弃或重开试次。工具组内部失败的 replan 与四阶段执行全部包含，不能只取其中一段成功用时。

计时外层覆盖凭据环境准备、Python/SDK 启动、全部模型和工具往返、关闭退出；验收脚本、评估者设计/诊断/写报告另外记录，不冒充模型任务运行时间。原生 UTC+9 15:33:10.38—15:34:44.66，工具 15:34:56.94—15:37:27.54。从第一组启动至第二组退出墙钟 4 分 17.16 秒；两组实际累计执行 244.873 秒。约 12.28 秒组间间隔没有计为某个模型的速度。

所有 36 次模型响应都有 SDK usage。total=input+cacheRead+output，reasoning 不重复相加；缓存读取与生成不能按相同单价看待。SDK 没有账单或订阅额度百分比，因此不知道费用/额度变化。以上也不包含主控 Codex 的研发 token。

## 原因证据

- 工具组第一次 `rw_run` 只执行 Git 元数据与 Python 版本查询，尚未做目标任务。
- 模型随后用 12 次 write、1 次 edit 写任务文件与辅助脚本；有 4 次 replan、3 次 execute、3 次 status。另有 4 次 glob 和 1 次 grep。
- 一次 replan 明确失败：`Plan rejected: unknown command prefix: xcopy`。这是产品继承的命令名白名单，与本轮 OS 沙箱设置无关。
- 最终流程被分为初始探测、第一次构建、第二次构建打包、补充 Git 检查。流程编排和文件输入能力不足把工作重新交还模型。

这些是可观察的额外步骤，尚未逐项做消融实验，不能声称全部时差由 xcopy 单独造成。这个任务上，“少一次安装排错”的收益不足以抵消接口负担。当前版本没有通用提速/省 token 的证据。

## 独立验收与边界

检查真实文件：固定提交、已跟踪源码无修改、源码安装 direct_url、三页中文和导航、所需跨页链接与锚点、全部页面本地链接、SVG、V1/V2 搜索索引、旧文件清除、两份成功构建日志、ZIP 文件集合与内容。原生 32 个站点文件，工具 33 个；两份配置和文本细节不同，均满足任务，未声称逐字节相同。

原生安装为本地源码 wheel，工具安装为本地源码 editable，均为题目允许的源码安装。两次构建命令与模型工具执行记录保留。原生 ZIP 验证曾在系统 TEMP 建临时解压目录并清理，这是“所有写入限定工作目录”的指令偏离；pip/编译工具还可能使用系统缓存/临时文件。本表“通过”指功能产物，不声称所有进程写入均在任务目录，也未进行 OS 文件访问审计。

运行顺序为原生→工具，共享机器包缓存未清空，模型服务与网络会波动。只有一对任务，不能推断稳定倍数或模型能力上限。此前 Gitingest 环境调试轮次全部排除，原始结果见 [历史环境调试记录](DSH_ENVIRONMENT_DEBUGGING.md)。

## 复现与证据

- 共同任务：[mkdocs_site_zh.txt](../benchmarks/tasks/mkdocs_site_zh.txt)。目标提交 `bb7e8b62185b11d9f59bb7f50b13c15134f62f8a`。
- 入口：[dsh_compare.py](../benchmarks/dsh_compare.py)，两组均 `--permissions danger-full-access --max-model-calls 0`。15 分钟异常超时保护未触发。
- 独立检查：[verify_mkdocs_site.py](../benchmarks/verify_mkdocs_site.py)。
- 本机证据根：`D:/CodexWorkspace/RepoWayfinder-Agent-20260927/dsh/final-mkdocs/`。
- `attempt-ledger.jsonl` 自动追加每次启动/退出，`experiment.json` 固定配置；两组 `*-01-run/summary.json`、`trace.jsonl`、`independent-verification.json` 全部保留。
- 工具作业 `a7b22e0268b24baeb23939706b2f4500`，其 history/runs 包含全部阶段。
- 原始会话、凭据及私密运行状态不入仓库。

## 后续优化

已实现通用任务文件批量输入与命令能力解析。通过 38 项针对性检查，并实际执行 xcopy、robocopy、tar、含空格路径的批处理和显式 PowerShell。Robocopy 原始返回码保留，0–7 为非失败状态，依据 [Microsoft 文档](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/robocopy)。准备换 Sphinx 三页交接手册作为同等结构的新任务；上表保留，不用新结果覆盖负面结果。


## 第二轮：Sphinx，批量输入版本

固定候选 `c1e2927cd762d82cdaa0281632c9eaca8935c480`；同一任务、完整权限、无模型响应上限，工具先于原生，全部尝试累计。两组均正常结束，但**原生独立验收未通过完整题目**，不能把正常结束当成功。

| 累计指标 | 原生 | 工具 |
| --- | ---: | ---: |
| SDK 启动次数 | 1 | 1 |
| 模型响应 | 35 | 10 |
| 工具调用 | 43 | 12 |
| 外层总耗时 | 170.040 秒 | 322.689 秒 |
| inputTokens | 19,775 | 11,540 |
| cacheReadTokens | 658,176 | 511,232 |
| outputTokens | 20,084 | 61,440 |
| totalTokens | 698,035 | 584,212 |
| reasoningTokens（output 子集） | 5,935 | 42,177 |
| 独立产物验收 | 搜索索引两项失败 | 通过 |

原生搜索索引只有 stemmed `releaseoldmark/releasenewmark`，不包含题目要求的完整标记；HTML、链接、ZIP 等其余检查通过。工具最终产物包含完整标记且通过全部独立检查。该验收要求带来了额外的搜索词干问题，应反思测试任务是否代表有意义的真实用户需求，不能用这个特殊要求证明产品普遍更可靠。

工具虽只做 10 次响应，输出却达到 61,440 tokens：四次 rw_run 提交包含一次非法 Unicode 拒绝、一个实际构建后检查失败的作业、一次超过 20 检查项的拒绝，最后另一个作业完成。两份源码安装、所有重复大段脚本均计入。没有从 322.689 秒中剔除失败或只展示最终成功作业。原生结果较快但未完整满足题目，因此不发布“同质量任务省时/省 token”的百分比结论。

本机证据：`D:/CodexWorkspace/RepoWayfinder-Agent-20260927/dsh/optimized-sphinx/`，共同任务 `prompt.txt`，外层 `attempt-ledger.jsonl`，两组 `*-01-run/summary.json` 与 `independent-verification.json`。工具失败作业 `163c7b7ed84044858214169f40a0d107`，最终作业 `956fe49dbf354a7e959e76845f05b4bd`。两组共计 492.729 秒实际运行、1,282,247 SDK totalTokens；研发/评估者消耗另计且尚无完整可靠 token 计数。原始证据保留。

### 第二轮后的修复现场（历史记录）

用户要求继续解决问题，并先将 Codex 思考强度升为极高。已通过桌面界面确认 GPT-6 Astra · 极高；没有更换模型。待下一轮在新设置下继续，不能声称当前进行中的模型调用已即时改变强度。

已确认待修复：

1. 批量输入缺乏局部恢复入口，模型失败后重写整套脚本并新建 checkout；应在原作业内做精确局部编辑、复用安装环境、只运行受影响步骤，并保留每轮证据。
2. 公开 schema 与后端实际边界不完整一致，例如 checks 的 20 条上限没有声明。需统一审计参数长度/数量/编码、错误反馈和状态约束，而非只给一个报错加补丁。
3. 自动执行路径会短暂公开 prepared 状态，诱发额外 rw_execute；需修复自动准备→执行的状态衔接，防止重复启动。
4. 提示把文档内容与大量自检都塞入单个大脚本；应直接批量提交输入文件，复用服务检查，保持必要编排短小，验证失败后的恢复开销。
5. 对照任务应考察实际可用产物；先复核搜索标记判定是否不当地惩罚正常词干处理，再锁定有意义的新验收标准。既有结果不能追溯改写为通过。

下一步先验证“失败→局部修复→复用同一作业完成”的完整路径，再运行新的同等难度对照，累计所有模型轮次、尝试、耗时和 tokens。现有两轮证据不得覆盖或重跑来取更漂亮的数字。

## 第三轮：课程实验手册，恢复接口版本

2026-09-27，固定候选 `df9bcb384298fd5c06a7b5ced61521c3c9293cbc`。共同新任务为 [sphinx_course_manual_zh.txt](../benchmarks/tasks/sphinx_course_manual_zh.txt)：三页课程手册、引用、SVG、两次 -W 构建、初版快照、FAQ 更新、旧文件清除和精确 ZIP。搜索按实际词干与文档编号验证，不要求索引保留原词字面量。

同一 SDK、deepseek-official/deepseek-flash、完整权限，无模型轮数上限、各自全新目录；原生先、工具后。15 分钟保护未触发，运行期间没有修改实现。

| 全部累计指标 | 原生 | 工具 |
| --- | ---: | ---: |
| SDK 启动次数 | 1 | 1 |
| 模型响应 | 23 | 20 |
| 工具调用 | 28 | 36 |
| 完整进程耗时 | 146.874 秒 | 347.730 秒 |
| SDK 内部耗时 | 146.750 秒 | 347.609 秒 |
| inputTokens | 11,473 | 18,784 |
| cacheReadTokens | 279,808 | 1,101,696 |
| outputTokens | 14,026 | 74,729 |
| totalTokens | 305,307 | 1,195,209 |
| reasoningTokens（output 子集） | 6,869 | 51,363 |
| 独立验收 | 21/21 | 21/21 |

工具组时间增加 136.8%、totalTokens 增加 291.5%。两组实际累计 494.604 秒、1,500,516 totalTokens。没有剔除拒绝、失败检查、重写和收尾；缓存读取不等于同价输出，费用/额度百分比未知。主控及审查代理研发 token 没有完整可靠计数，未混进模型任务表。

两组各生成 25 个站点文件，原生 89,582 字节，工具 89,272 字节；都满足任务，不声称两站逐字节相同。独立检查包括源码安装 direct_url、固定 Git 提交和跟踪源码、全部要求内容/链接/图片、正常词干化后 FAQ 的新旧关键词命中、日志、清理及 ZIP 内容一致。未做全进程文件访问审计。

### 首个因果差异与修复

工具组复用了一个 checkout 和环境，但发生 8 次 replan、11 次 write、2 次 edit。前三次批量续作中有两次 Unicode 拒绝和一次缺 job_id；后续又因旧快照/复制资产没有改变 mtime，以及自写 stdout 标记不匹配而继续重跑。最终两组功能都通过，但这一版仍不适合宣称通用提效。

1. **传输编码错误。** 原始 trace 中被拒绝的内容本来是合法 UTF-8。Windows MCP 服务 stdin 沿用本地编码，将中文错误解码成 surrogate；之前将其归因为模型生成非法 Unicode 的判断不成立。用真实子进程、UTF-8 字节输入、`PYTHONIOENCODING=gbk:surrogateescape` 复现同类报错。服务入口固定 stdin/stdout UTF-8 后测试通过；原来被拒绝的 10 文件、12,441 字节请求经真实 stdio 重放，接收摘要完全一致，0 模型调用、0 目标命令。
2. **跨阶段验收定义过窄。** 默认要求本轮输出仍保留，防止输入变化后旧答案冒充新计算。新增明确的 `freshness: "preserved"`，用于刻意保留的前阶段产物：必须有同一作业的既有验证记录，SHA-256 一致，并保留原生产 attempt ID。mtime 保持的文件替换也用文件身份/创建时间区分。没有自动猜测产物依赖，更没有把任意旧文件改成通过。

修后 48 项受影响检查通过，包含真实 worker 的失败→局部修复→同环境成功→保留前阶段产物，以及改输入后旧输出不能默认通过。完整原始批量请求的重放记录在 `post-fix-transport-replay.json`。这些证明修复路径，不等于新版本的模型用量收益；该轮成绩保持不变。

本机证据根 `D:/CodexWorkspace/RepoWayfinder-Agent-20260927/dsh/recovery-course/`：`experiment.json`、`attempt-ledger.jsonl`、`comparison.json`，各组 summary/trace/independent-verification，以及工具作业 `ee1dac9c31744087b69c7658a6f1f9e5` 的全部 history/runs。原始内容不提交仓库。

### 后续测量顺序

用户已明确改为工具组先预跑，修复实际流程问题后冻结候选，再做同任务原生对照。预跑开发成本和正式比较分别累计，所有失败保留；不为每个已知有问题的工具版本重复跑原生，也不只拿预跑中最快的一次当正式成绩。该约定由仓库 AGENTS.md 持有。

## 修后预跑（先工具，暂不启动原生）

候选 `4d304b8`，证据 `dsh/utf8-course/tool-01-run/`。整组 355.285 秒，16 次模型响应，input=31,045、cacheRead=939,136、output=73,085、total=1,043,266、reasoning=60,795（output 子集）。实际产物 21/21 通过，但预跑发现重复创建虚拟环境问题，因此没有启动新的原生组，也不能把这次作为无故障效益结论。

UTF-8 和参数传输拒绝为零。首次计划执行 `python -m venv .venv`，服务已为 python 命令自动准备环境，Windows 中用该环境解释器覆盖自身失败；模型在同一作业内恢复。修复将 Python 标准 venv 引导显式路由到该环境的基础 Python，保留版本和全部参数；普通 Python/pip 仍用作业环境。调用约定明确环境由服务负责。真实已存在 venv 重建用例与全部 9 项 runtime 检查通过，旧环境标记未被删除。

本次工具环境实际使用已安装 Python 3.12.10；任务列出了可用的 3.11，未硬性限定必须使用该版本。运行时差异需记录，后续不得将它描述为所有环境因素完全相同。主要额外输出为模型 reasoning，修复执行错误还不能证明总成本下降。

### 随后的 tool-02：未通过完整任务验收

候选 `134dd1c`，证据 `dsh/utf8-course/tool-02-run/`，作业 `70542fdc10c34fb296e91eb7d188c557`。完整进程 473.006 秒、38 次模型响应、64 次工具调用；input=42,991、cacheRead=2,496,768、output=80,137、total=2,619,896、reasoning=61,795（output 子集）。

脚本内部曾用系统 Python 3.12 安装包，随后模型执行了卸载；缺少运行前完整系统包清单，不能认定完全恢复。最终构建使用嵌套的 `course-delivery/.venv311`，而根 `.venv` 缺少 docutils。服务当时返回 task_verified，但独立验收未完成，不是 21/21；发生明确禁止的全局安装后，该任务不能作为完整成功样本。tool-01 和 tool-02 合计 828.291 秒、3,663,162 totalTokens，没有为这两个已知有问题的候选另付原生组成本。

0.5 的环境选择、pip 防误用和原生协作路线是在保留上述失败证据后继续完成的修复。本页当前结果不追溯改变这些历史结论。
