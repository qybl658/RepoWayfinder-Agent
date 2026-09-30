# 批量 Markdown → 可打开的 HTML

用现成的 Python-Markdown CLI 转换一批中文文件。输入正文由本地入口读取并交给 Agent，不需要让模型重写正文；输出是带 UTF-8 元信息、标题和基础样式的完整 HTML 页面。

先按仓库 [README](../../README.md) 完成 Agent 环境准备，使用已有 Agent 解释器启动。本入口会复用装有依赖的 Agent 环境，不负责安装 Agent 依赖。在 RepoWayfinder Agent 目录运行：

```text
python examples/cli-batch/run.py
```

默认使用这里的两份中文示例。输出放在 `.agent-data/cli-batch-deliveries/<job_id>/`，末行 JSON 给出 `job_id`、交付目录和每个 HTML 的绝对路径。直接打开 `入门 指南.html`。请求、结果和本地日志保存在 `.agent-data/cli-batch-controls/` 与原 job 内，不加入示例源码。

处理自己的输入及指定交付位置：

```text
python examples/cli-batch/run.py --input-directory "D:/我的文档" --output-directory "D:/文档交付"
```

只读输入目录最外层的 UTF-8 `.md` 文件，兼容 Windows 常见 UTF-8 BOM；上传时去除编码标记，原文件不改，不递归扫描。每次交付建立新的 `<job_id>` 子目录，不覆盖旧文件。转换同批本地文档的链接到 `.html`，保留查询和片段、正确编码空格；远程链接、正文和代码中的 `.md` 不改。未随批次提供的链接和资源不会自动复制。仅处理可信 Markdown：上游 CLI 保留原始 HTML，并不负责净化不可信内容。

## 实际执行路径

入口复用现有 `configure_clients.choose_python` 选择装有 Agent 依赖的项目运行环境（优先项目 `.venv`），构建普通 `rw_run` 请求，经 `agent.py call` 启动生产异步 worker；需要时通过 `rw_status` 等待。官方仓库 `Python-Markdown/markdown` 固定到 3.9.0 tag 的提交 `f39cf84a24124526c1a0efbe52219fa9950774f6`，使用已有 Python 3.11，运行时没有额外第三方依赖。在源码 checkout 中调用 `python -m markdown`，**没有 pip 安装这个工具**。Agent 使用本 job 的 Python 环境，不做全局安装。

`convert.py` 只组织批次、调用真实 CLI，然后将 HTML fragment 包成页面；Markdown 解析及表格、代码块、属性扩展均由上游实现。CLI 输出和 stderr 留在 job 内，返回紧凑结果。没有其他模型、来源适配器或通用新 DSL。首次获取会访问官方 GitHub；输入正文只写到本地 job。

此入口最多接收 **31 份输入**：生产 `files` 上限 32 项，其中一项是 `convert.py`。辅助脚本与输入正文合计不得超过 **256 KiB**，入口也对完整 JSON 请求做 256 KiB 校验，超限需分批。生产还限制 128 项检查、20 条命令；本流程使用两条命令、每个输出三项检查。

基础检查覆盖文件存在、UTF-8 元信息和标题；它不替代具体业务内容验收。示例还须核对中文、表格、代码块、空格文件名和本地链接。失败时不复制部分产物伪装成功，会返回 job 与证据位置。

## 在同一个 job 中恢复

先用 `rw_logs` 检查第一个实际原因，再用 `rw_replan` 提供缺失的任务输入或精确修正，并只重跑受影响文档，例如命令 `python cli-batch/convert.py "补充说明.md"`。检查替换时保留必要业务断言；已验证且内容没变的旧输出可明确使用 `freshness="preserved"`，新输出仍用 fresh。不要为局部失败重建仓库或环境，不自动重放失败命令。

恢复后的真实文件位于返回的 `project_path/cli-batch/output/`。已导出的交付目录是快照，不会自动更新；需要交付新文件时从这个 job 的输出复制到新的交付目录。不要重跑 `run.py` 当作恢复，那会创建新的 job 和 checkout。

默认正常流程不包含故障演示。真实缺输入失败与同 job 恢复另在本地验收中验证，不能用功能验证宣称模型端到端节省。

官方依据：[源码与固定版本](https://github.com/Python-Markdown/markdown/tree/3.9.0)、[CLI 文档](https://python-markdown.github.io/cli/)、[该版本依赖声明](https://github.com/Python-Markdown/markdown/blob/3.9.0/pyproject.toml)。
