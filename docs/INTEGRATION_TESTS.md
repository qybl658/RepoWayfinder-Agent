# Agent 接入实测 · 2026-09-27

按 Grok → DSH → Codex 的顺序测试。这里的“测过”指实际调用本项目并检查任务产物；失败、受阻、未测试分别记录。

后续 0.5 更新：DSH companion 候选 `f291152` 完成正式课程手册对照，两组都通过 21/21 与 Python 3.11.9 核验，工具组 133.021 秒 / 311,773 totalTokens，原生 161.159 秒 / 733,537；全部成本和局限见 [DSH 对照](DSH_COMPARISON.md)。

Grok 也以相同任务完成 companion 对照（候选 `acbd0f3`），两组最终 21/21。工具组 526.167 秒 / 654,870 token，原生含原会话续跑 898.876 活跃秒 / 2,857,855 token。原生曾被测试器 40 轮上限中断，用户要求取消限制后续完；恢复间隙及额外提示开销限制了对照的解释。预跑和全部分段见 [Grok 对照](GROK_COMPARISON.md)。下列条目保留历史简单任务接入证据，Codex 原生 MCP 未在本轮重跑。

## 共同任务

公开仓库 `coderamp-labs/gitingest`，固定提交 `4e259a02fe72115bee538271622f1234a81c8e1a`。从源码安装到作业自己的 Python 环境，实际运行 CLI 处理 `src/gitingest`，生成新的 UTF-8 `digest.txt`。不安装开发或服务器 extras，不需要目标项目账户。

验收重新读取真实文件，检查非空、UTF-8、`entrypoint.py`、`async def ingest_async(`、源码提交和读取错误段落，并与 Grok 原生终端对照产物逐字节比较。工具内的 `task_verified` 仅覆盖调用者给出的断言；外部复核是另一层证据。

## Grok：通过

- Grok CLI 1.0.41，模型 `grok-4.7`，reasoning effort `high`。
- 入口：项目级 stdio MCP，由 `grok_task.py` 驱动。没有替换成直接调用执行引擎。
- 最终执行代码：`66dcc28`。3 轮、2 次工具调用，任务成功且独立文件检查通过。
- 78.031 秒；未缓存输入 11,700、缓存读取 29,440、输出 3,544 token；CLI 报告 costUSD 0.02019056。
- 同期原生终端对照为 148.922 秒，结果文件相同。全部试次、失败及限制见 [Grok 对照记录](GROK_COMPARISON.md)。

## DSH：通过（Harness SDK + MCP）

使用 DSH Desktop v0.9.2 便携包内置 DeepSeek Harness `0.1.5-rc.2` 的官方 SDK。provider=`deepseek-official`，模型 `deepseek-flash`，来自当前包实际默认配置；未冒充桌面界面手动选中的模型。测试配置、会话和 MCP 作业单独保存。这是实际 SDK → 模型 → MCP → 执行链测试，不是桌面界面交互验收。

- 最终运行代码：`927466c`；37.734 秒，1 个用户 turn、2 次模型响应、1 次 `mcp__repo_wayfinder__rw_run`。3 条命令，全部退出 0；没有修复重试。
- SDK 报告：inputTokens=1,026、cacheReadTokens=9,600、outputTokens=2,185、totalTokens=12,811；reasoningTokens=1,472，已包含在输出中，不另加。
- `task_verified` 与独立文件检查均通过：新文件 117,771 字节，UTF-8、固定提交、两个内容标记、无读取错误，与原生对照逐字节一致。
- API 费用和订阅额度百分比未由该 SDK 报告，保留未知。上面只证明最初接入成功。旧 87.063/61.016 秒子运行已归入环境调试历史；[DSH 原生工具对照](DSH_COMPARISON.md) 保留复杂任务中的负面结果与 0.5 新样本，不能用早期子运行宣传普遍收益。

遇到的问题也保留：第一次 SDK 启动因沙箱/审批组合没有对应权限预设而退出，未进入模型调用；为独立测试配置补上显式预设。第二次实际模型运行用时 50.500 秒、9 次模型响应、8 次工具调用，最终成功，但经历了不支持的 GNU `find`、stdout 断言空 path 被误当文件、shell 重定向被拒等恢复。SDK 报告 totalTokens=70,302，原始试次仍保留。

修复后：stdout 检查不再做文件快照；调用说明明确 Windows、逐条 argv、无 shell 串联，并说明固定 revision 与文件断言已由工具处理。增加 stdout 空 path 的回归检查，19 项服务检查通过。最终模型回信把 `attempt_count: 3` 误解释为重试；执行证据表明它是 3 条命令记录。返回值说明现已明确这个字段的含义，以降低下一次误读。

## Codex：通过（当前会话 + JSON CLI）

DSH 完成后，由当前 Codex 会话经 `agent.py call rw_run` 发起相同任务，独立新建作业，读取紧凑结果并复核真实文件。没有把这次测试描述成已安装 Codex 原生 MCP。

- 执行代码：`927466c`；1 次工具调用，CLI 往返 24.969 秒，其中任务命令执行 20.953 秒。
- `task_verified` 与独立文件检查通过，产物同为 117,771 字节，并与 Grok 原生对照逐字节一致。
- 24.969 秒只计工具往返，不含 Codex 推理和审查，不能直接与另外两组的模型端到端时间排名。

### 按用户要求记录额度百分比

使用 Codex 应用的账号额度接口，在本次正式调用前后各采样一次；两次属于同一 10,080 分钟窗口，reset 时间未变。

| 采样（UTC） | 已用比例 | 剩余比例 |
| --- | ---: | ---: |
| 2026-09-27 06:00:51.752 | 51% | 49% |
| 2026-09-27 06:01:18.562 | 51% | 49% |

显示差值为 **0 个百分点**。这不能解释为零 token、零额度消耗或节省 100%：接口显示整数百分比，更新延迟未知，而且是共享账号窗口，不是单任务计量器。本任务子代理此时已结束；没有控制账号其他会话。这里没有 Codex 无工具对照，不能推算其节省比例。早先检查接口是否可用时显示 49% 已用，那不是本轮测试前值，期间开发和 DSH 适配开销不能算到这 25 秒工具执行里。

## 本机证据索引

根目录：`D:/CodexWorkspace/RepoWayfinder-Agent-20260927/`。这里只提交摘要，不提交原始对话、登录状态或密钥。

- Grok：见 [完整对照索引](GROK_COMPARISON.md)。
- DSH：`dsh/sdk-v02-final/summary.json`、`independent-verification.json`；作业 `dsh/work/jobs/9ad7f012390943269789ae5283c6b4b1/`。早期启动失败在 `dsh/sdk-v02/`，恢复试次在 `dsh/sdk-v02-permission/`。
- Codex：`codex/summary.json`、`calls.json`、`independent-verification.json`；作业 `codex/tool-state/jobs/6c9716cc010445da9af37a0a7ddc880a/`。
- 额度：`codex-quota-before.json`、`codex-quota-after.json`，仅存窗口与百分比，不含账号身份。

## 适用边界

这是一项真实任务的接入验证，不是所有项目、环境或版本的兼容性承诺。Grok 和 DSH 已做原生工具对照；Codex 仅验证接入，不能从其接入成功推出节省比例。原始会话、凭据、完整日志和产物留在本地，不提交本仓库。
