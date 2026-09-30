# DSH 多轮配对实测

2026-09-30。[逐次数据与分项用量](../benchmarks/results/2026-09-30-dsh-repeats.json) · [目录范围审计](../benchmarks/results/2026-09-30-dsh-scope-audit.json) · [有效思考设置](../benchmarks/results/2026-09-30-dsh-reasoning.json) · [源码可比性](../benchmarks/results/2026-09-30-dsh-source.json)。

下述冻结提交属于本地实验来源记录，并非本独立公开仓库中可直接 checkout 的 Git ref。公开仓库保留任务、驱动、验收器与脱敏证据；原始模型记录不上传。

已完成 16/16 次新尝试，独立业务验收通过 16 次；保守目录范围审计通过 11 次。

每个任务两组配对，第一组工具先行，第二组原生先行。各次使用新工作目录、新 job 与新 SDK home，顺序运行；相同模型、默认思考和原生工具权限，工具组额外增加 MCP。

基础提交 `9633dca38a25af08437f36d8dae04e1827294ba3`；测试冻结提交 `1cbeb7f09c10a7988b51de514e42f14e5b6e77c6`。仅修复驱动的默认限时，默认无限，保留显式限时选项；产品执行核心与任务正文同 Grok 比较提交。

下表保留全部原始运行及成本。范围偏离样本的变化不能叫完全合规条件下的收益。正值表示原始运行量减少，负值表示增加。

| 任务 | 配对 | 业务均通过 | 范围可比 | 工具/原生秒 | 时间变化 | 工具/原生 token | token 变化 |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: |
| sphinx_course_manual_zh | 1 | True | False | 188.688/220.703 | 14.51% | 522187/941587 | 44.54% |
| sphinx_course_manual_zh | 2 | True | False | 163.531/156.844 | -4.26% | 493630/395170 | -24.92% |
| bottle_quote_zh | 1 | True | False | 90.516/108.859 | 16.85% | 155761/240974 | 35.36% |
| bottle_quote_zh | 2 | True | False | 105.469/171.672 | 38.56% | 229127/700133 | 67.27% |
| http_server_static_zh | 1 | True | True | 142.078/94.187 | -50.85% | 430730/336120 | -28.15% |
| http_server_static_zh | 2 | True | True | 66.906/71.187 | 6.01% | 151969/196993 | 22.86% |
| flaskr_state_zh | 1 | True | True | 106.422/109.985 | 3.24% | 274384/388069 | 29.30% |
| flaskr_state_zh | 2 | True | False | 150.562/130.344 | -15.51% | 548189/487922 | -12.35% |

范围审计发现两次明确违反课程的“任务写入限定本次工作目录”：工具第一轮在系统临时目录解压校验ZIP；原生第二轮在固定系统临时子目录解压并递归删除。固定目录的既有状态未知，不能声称用户数据已损坏。

Bottle两个工具轮次将辅助服务stdout/stderr日志写到系统临时目录；Flaskr第二工具轮次额外创建临时合成验证数据库及日志。这三个任务没有课程同样的all-writes句子，外层工具指引只明确约束安装日志。因此这三项是按任务输入/交付物范围的严格解释作出的保守审计不通过，与课程的明确违约分列。必需交付物仍在正确目录。

普通pip/npm缓存、平台内部临时工作不计入这些主动写出验证数据的发现。一次原生Bottle仅切换cwd到临时目录读取本次安装位置，不构成写出偏离。未发现基础Python依赖变化；此审计不证明完整文件系统合规，也不构成泄漏或系统损坏结论。

严格范围可比配对：

这些是运行后的保守范围审计子集，尤其三项临时验证产物的判定包含解释性标准；它们不是另一次随机试验。全部样本、业务结果和消耗始终保留在上表，不应只引用子集中较好的数字。

| 任务 | 可比配对数 | 时间减少 | token 减少 |
| --- | ---: | ---: | ---: |
| sphinx_course_manual_zh | 0 | 无可比估计 | 无可比估计 |
| bottle_quote_zh | 0 | 无可比估计 | 无可比估计 |
| http_server_static_zh | 2 | -26.37% | -9.30% |
| flaskr_state_zh | 1 | 3.24% | 29.30% |

全部16次原始成本：

- 工具：2805977 SDK token，1014.172秒。
- 原生：3686968 SDK token，1063.781秒。

实际SDK配置已从request/header字段核实：requested reasoning未覆盖（null），effective reasoning为high；旧课程两组也同样为high。null不表示没有思考或比Grok high低。跨供应商同名high不证明同等计算强度。脱敏精确事件引用见effective-reasoning-evidence.json。

机制与弱收益：

- MCP将固定Git获取、隔离环境、安装日志与恢复身份集中处理，任务文件仍可用原生工具写。全部新样本中工具组平均模型响应数较少，但这本身不能证明总token或时间收益。
- 工具组可用工具定义20个、序列化22694字符；原生11个、11450字符。新增MCP定义构成固定上下文开销；这些是字符测量，不是精确token归因。
- 严格可比的静态站两组，合并时间增加26.37%、token增加9.30%，其中第二对反而小幅节省。第一工具轮的工具等待约74.8秒，原生约32.8秒；更大的上下文与等待可能抵消少轮次收益，但没有做网络控制或schema消融，不能宣布单一根因。
- Flaskr严格可比第一对时间减少3.24%、token减少29.30%；第二对含范围偏离，保留原始成本但不用于完全合规收益估计。工具只改善通用运行步骤，不能保证Sphinx搜索索引或HTTP测试脚本的领域检查一次正确。
- 错误与恢复保留：原生Bottle修复PowerShell变量解析与控制台Unicode编码问题；课程两组均出现自行编写的搜索校验器错误；Flaskr第二工具轮为额外临时验证目录缺失补了日志父目录并重试。原生课程一次git describe缺少标签是附加元数据探测失败，源码获取与最终业务均完成。这些恢复均由受测模型完成，零人工产物修补；SDK isError标志未反映所有非零命令，另保留工具输出中的异常标记，不能把SDK标志为零叫作零内部失败。

SDK token 字段分别记 uncached input、output、cache reads/writes 和 reasoning；totalTokens 口径包含缓存读，不能据此直接推算计费。请求阶段时间包括供应商响应、网络和流式输出。独立验收时间单列，不计入任务运行收益。

失败、模型内恢复和工具错误保留在 attempt ledger 与各次证据中。基础 Python 环境每次前后审计；验收器只控制自己启动的服务。

仅两组配对，交替顺序缓解单向顺序偏差，仍不能控制供应商缓存、网络和安装波动，也不能推出统计普适收益。主控开发、准备和验收 token 不包含在此 SDK 数字中。

## 复现入口

任务正文位于 [benchmarks/tasks](../benchmarks/tasks)，源项目与版本均已固定。将任务文件复制到仓库外，把 `<USER>` 解释器占位路径替换为本机实际 Python 3.11.9；两组使用同一份替换后的任务。保持驱动源码工作树干净，在每次运行中使用全新且互不重叠的工作目录和输出目录。

以下是一个工具组的入口示例；`--bundle` 指向现有 DSH Desktop 便携包，进程须已有相应模型访问配置。相同参数换 `--mode native` 即为原生组。本文正式批次使用已明确授权的 `danger-full-access / never`；下面保留默认工作区权限，若改变权限，结果应单独记录，不当作相同实验条件。

```powershell
.\.venv\Scripts\python.exe benchmarks\dsh_compare.py --mode tool --bundle D:\DSH-Desktop --directory D:\bench\bottle-pair1-tool-work --prompt-file D:\bench\bottle-task.txt --output D:\bench\bottle-pair1-tool-run --provider deepseek-official --model deepseek-flash --max-model-calls 0 --timeout 0
```

每个任务按 tool→native、native→tool 的顺序各做一组，保留所有尝试。检查摘要中的实际模型、思考设置、源码版本和权限；默认值不代替实际记录。对实际返回 checkout 下的 `bench-delivery` 另运行 [部署验收器](../benchmarks/verify_deployment_matrix.py)，课程使用 [课程验收器](../benchmarks/verify_course_manual.py)，并审查目录范围、失败恢复与环境变化。模型声称完成或运行器正常退出均不能代替这些结果。

模型、网络、依赖缓存和个性化宿主配置会变化；公开文件足以复用任务与验收方法，不能重建原机器的全部运行条件。完整原始对话、账号配置和本机路径不公开。
