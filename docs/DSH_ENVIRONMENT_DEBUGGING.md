# 历史环境调试记录（不计入后续独立实验）

按用户要求，以下旧轮次保留供追溯，原成功子试次对比不再作为当前结论。

# DSH 原生工具与 RepoWayfinder 对照 · 2026-09-27

## 结论

补齐此前遗漏的无工具对照后，两组都完成了同一任务，产物逐字节相同。本次工具组耗时减少 **29.9%**，SDK 报告总 token 减少 **88.9%**。后一个数字主要来自减少缓存上下文重复读取；输出 token 仅减少 **6.0%**，不能宣称费用或额度也少了 88.9%。

这是一个任务的一对有效运行，另外保留了两次未完成的原生试次。不能据此推断稳定收益或 DSH 的能力上限。

## 87 秒不等于整个测试过程

`wall_seconds` 使用单调时钟，从一次 SDK 子进程启动前计时，到该子进程结束后停止。**87.063 秒只属于最后一次成功的原生运行**，不含前面试次、适配器编写、诊断、等待用户授权和独立验收。前次对用户汇报没有把这两个时间口径放在一起，容易产生误解。

已用 SDK 事件时间与文件创建/写入时间核对。本机时间为 UTC+9：

| 阶段 | 开始—结束 | 该阶段耗时 | 结果 |
| --- | --- | ---: | --- |
| 原生沙箱试次 | 15:07:38—15:13:16 | 约 338 秒 | 评估者中断，排除运行环境干扰 |
| 本轮工具组 | 15:13:17.98—15:14:18.98 | 61.016 秒 | 完成 |
| 原生 20 响应上限试次 | 15:14:19.96—15:15:41.31 | 81.359 秒 | 触及上限，未完成 |
| 原生 40 响应上限试次 | 15:16:54.94—15:18:21.99 | 87.063 秒 | 完成 |

从第一轮启动到最后一轮结束，墙钟约 **10 分 43 秒**；此前准备和此后核验、写报告还另需时间。三次原生试次运行时间合计约 **8 分 27 秒**。性能表比较最后一对成功任务的边际用时，不能代表本次研究只花了 87 秒，或整个研发已经净省时间。

第一轮的沙箱配置由测试方选择，第二轮的 20 响应上限由测试方设置；不能把它们直接算成 DSH 的能力失败。能归因到有效原生试次的观察是：该模型在这个 Windows 编码问题上反复排查，21 次响应后完成。DSH 是框架，本次模型仅为 deepseek-flash；没有测其他模型、任务或日常交互表现。

## 相同任务和配置

- DSH Desktop v0.9.2 包内 Harness `0.1.5-rc.2`；显式指定 provider=`deepseek-official`、model=`deepseek-flash`，两组都沿用相同默认推理设置。
- `coderamp-labs/gitingest`，固定提交 `4e259a02fe72115bee538271622f1234a81c8e1a`。
- 各自在全新目录创建项目虚拟环境、从源码安装 CLI，不装 dev/server extras；实际处理 `src/gitingest`，新生成 `digest.txt`。
- 两组收到同一任务正文，均要求限制日志回传。原生组可组合 PowerShell 命令；工具组附产品调用说明。没有提供专门替原生组完成任务的脚本。
- 原生组使用 DSH 自带 PowerShell、文件读取/写入工具，不加载 RepoWayfinder；工具组使用 `repo_wayfinder` MCP。都禁用网页、子 Agent 和嵌套模型调用。
- 原生组最终使用用户明确批准的、仅限这次独立测试的 `danger-full-access / never` 配置；工具组的执行核心本身也没有 OS 文件沙箱。模型提示均限制写入各自测试目录。没有更改原桌面配置或系统权限。

## 有效完成结果

| 指标 | DSH 原生工具 | DSH + RepoWayfinder | 变化 |
| --- | ---: | ---: | ---: |
| 任务与独立产物验收 | 通过 | 通过 | 产物相同 |
| 端到端耗时 | 87.063 秒 | 61.016 秒 | 减少 29.9% |
| 模型响应次数 | 21 | 2 | 减少 90.5% |
| 工具调用次数 | 28 | 1 | 减少 96.4% |
| inputTokens | 10,624 | 4,615 | 减少 56.6% |
| cacheReadTokens | 194,432 | 11,264 | 减少 94.2% |
| outputTokens | 8,189 | 7,699 | 减少 6.0% |
| totalTokens | 213,245 | 23,578 | 减少 88.9% |
| reasoningTokens（输出的子集） | 3,440 | 6,933 | 增加 101.5% |

各 token 字段原样取自 SDK。两组的 `totalTokens` 都等于 input + cacheRead + output；reasoning 已在 output 内，不再相加。SDK 没有报告这两次调用的美元费用或订阅额度百分比，因此保留未知。表中不包含 Codex 开发、审查、诊断或其他试次开销。

原生组的 213,245 总 token 中，194,432 是累计缓存读取（约 91.2%），不是生成了 21 万 token。去掉缓存读取，input + output 为 18,813 对 12,314，减少约 34.5%；这些字段也不能直接按等价单价合算费用。

独立检查读取了真实产物与 Git 状态：两组均为 **117,771 字节**、严格 UTF-8，含 `entrypoint.py` 和 `async def ingest_async(`，没有实际文件读取错误段落，提交一致且已跟踪源码未被修改。文件逐字节一致。没有直接采用模型“完成了”的自述作为验收。

## 差别出在哪里

原生组自行获取源码、准备环境、运行 CLI 和检查结果。第一次 CLI 输出漏掉了文件，随后检查与排查定位到 Windows cp936 编码，最终使用 Python UTF-8 模式重跑成功。它能做成，但需要多轮读取文件、日志和再执行。

工具组一次 `rw_run` 完成固定源码获取、虚拟环境、三条命令和结果断言；执行核心直接提供 UTF-8 目标进程环境。模型无需再次排查这次编码问题。完整源码与安装日志未进入模型上下文。

不过，工具组的推理 token 反而更多：减少工具往返不保证每次模型决策更短。此前接入测试工具组为 37.734 秒、12,811 总 token，这次为 61.016 秒、23,578 总 token；报告使用本轮结果，没有挑此前更好的数字做对照。

## 未完成试次

1. **Windows 文件沙箱内的原生试次**：在安装构建依赖时日志持续停在 started，子进程持续耗 CPU；约 5 分 38 秒后由评估者停止。普通进程下载同一构建依赖约 3 秒完成，这不足以证明沙箱内部具体根因，但说明不能直接把这段停滞当成产品提速。5 次模型响应、6 次工具调用，已报告 totalTokens=28,348。原始证据保留，不计入有效性能比。
2. **批准 full-access 后的 20 响应上限试次**：81.359 秒触及上限，SDK 报告 totalTokens=258,881。独立读取产物为 91,195 字节，缺少目标函数且含 3 个实际文件读取错误段落，不通过验收。随后将原生组上限扩到 40，从新目录重新运行，21 次响应后完成。工具组上限为 20、实际仅 2 次响应，未触及上限；它没有因该上限变化再次运行。

以上消耗属于真实探索成本，不能抹掉；有效组只反映两个成功任务的边际表现。运行顺序为沙箱异常原生 → 工具组 → 20 上限原生 → 40 上限原生，网络、缓存和模型服务存在波动，未进行随机多次实验。

## 复现与证据

仓库新增 [对照入口](../benchmarks/dsh_compare.py)，复用 `dsh_task.py` 的同一 SDK 采集逻辑。调用方提供已授权的 API key 进程环境；脚本不读取或存储密钥。`--mode native` 默认仍为 workspace-write；full-access 参数只能在明确授权的试次使用。

```powershell
python benchmarks/dsh_compare.py --mode native --bundle D:\DSH-Desktop --directory D:\trial\native --prompt-file D:\trial\task.txt --output D:\trial\native-evidence --provider deepseek-official --model deepseek-flash
python benchmarks/dsh_compare.py --mode tool --bundle D:\DSH-Desktop --directory D:\trial\tool --prompt-file D:\trial\task.txt --output D:\trial\tool-evidence --provider deepseek-official --model deepseek-flash
```

本机证据根目录：`D:/CodexWorkspace/RepoWayfinder-Agent-20260927/dsh/comparison/`。

- 共同任务正文：`prompt.txt`；各组附加说明保存于各自 `submitted-prompt.txt`。
- 成功原生组：`native-complete-run/summary.json`、`trace.jsonl`、`independent-verification.json`；产物在 `native-complete-work/digest.txt`。
- 工具组：`tool-run/summary.json`、`trace.jsonl`、`independent-verification.json`；作业 `tool-work/jobs/1da29f81ff6d4edd9cbfc67d7823c990/`。
- 沙箱中断：`native-run/partial-summary.json`、`interrupted.json`、`trace.jsonl`；保留全部工作目录。
- 20 上限试次：`native-authorized-run/summary.json`、`trace.jsonl`；不合格产物在 `native-authorized-work/out/digest.txt`。

工具组在 `711b2e9` 开始运行，结束时摘要记录了 `8433bb3`；期间只为原生组增加了授权权限参数，工具组路径与执行核心未变。成功原生组为 `d141924`。后续对照入口改为在运行开始时记录提交，避免再次发生这种索引歧义。原始 trace、私密运行状态和凭据不提交仓库。
