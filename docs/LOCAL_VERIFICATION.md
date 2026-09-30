# 本地结果检查

`rw_verify` 把确定的检查放在一次工具调用中执行。它适用于已有任务目录中的 CSV、JSON、文本产物，以及需要启动、请求、重启和停止的本地 HTTP 服务。业务规则、预期结果和必要错误案例仍由调用者确定。

该能力包含共享检查代码、MCP/JSON CLI 入口和服务端随附指引。它不需要私人规则，也不调用另一个模型。是否节省总 token 和时间，需要完整任务对照；检查通过本身不是性能证据。

不要为了调用工具，把已有紧凑检查改写成同样长的断言清单。2026-09-30 的原装 Grok CSV 配对中，两组独立业务验收均通过 15/15，但工具组会话 usage 多 24.73%、wall time 多 5.13%：原生检查脚本约 9.6 千字符，声明式清单约 10 千字符，模型生成负担没有明显下降。这一单组结果不支持默认把普通 CSV 检查迁入工具。应优先替代确实重复的执行、解析或生命周期代码，复用仍有效的验证结果；业务专用逻辑保留适合的原生实现。usage 含缓存读取，安全审批分类器未单独报告用量，不能视为完整费用数据。

## 文件与表格

所有检查路径相对于 `directory`。目录可以没有 Git，也可以包含用户尚未提交的修改；工具不重置或覆盖这些文件。省略 `run` 时只读取指定产物，另在任务内写本次证据。

```json
{
  "directory": "D:/work/example",
  "checks": [
    {"type": "csv_row_count", "path": "results/items.csv", "expected": 3},
    {"type": "csv_sum", "path": "results/items.csv", "column": "amount", "expected": "12.30"},
    {"type": "json_value", "path": "results/summary.json", "pointer": "/rows", "expected": 3}
  ]
}
```

| 检查 | 参数与含义 |
| --- | --- |
| `file_exists` | `path`，要求普通文件存在 |
| `file_contains` | `path`、非空 `expected` 文本 |
| `json_value` | `path`、可选 RFC 6901 `pointer`、`expected` JSON 值；保留类型区别 |
| `csv_row_count` | `path`、`expected` 整数行数；可加 `where`，不传 `column` |
| `csv_sum` | `path`、`column`、`expected` 精确十进制和，可用字符串避免浮点歧义 |
| `csv_value_counts` | `path`、必填 `column`、`expected` 值到整数数量的映射 |
| `csv_rows` | `path`、`expected` 完整字符串行数组，检查内容及顺序；不传 `column` |

CSV 检查可以增加 `where`，按给定列的字符串值全部精确匹配后检查。检查可带可读 `id`。CSV 兼容 UTF-8 BOM 和引号内换行；缺失/重复表头、错误字段数、非法数值会明确失败。每批读取最多 20 MiB，单个 CSV 最多 5 万行、25 万单元格；超限保留原产物并报告，不采样后冒充全量通过。同一批检查复用解析结果；不同调用重新读取文件。

## 执行与显式复跑

可增加 `run: {"argv": ["python", "task.py"], "timeout_seconds": 60}`。使用固定目录、参数数组和已有解释器，不创建环境、安装依赖、猜服务命令或自动修复。裸 `python` 优先使用目录现有 `.venv`，否则使用工具的解释器；需要另一环境时传完整解释器路径。

`unchanged: ["results/items.csv", "results/summary.json"]` **明确请求执行相同命令两次**，比较解析后的 CSV/JSON 或 UTF-8 文本。它适用于已授权的重复执行检查，不是自动重试机制；不要用于未经授权的重复发送、付款或其他外部变更。失败会保留已发生操作的证据。

`timeout_seconds` 是一次有限检查阶段的总预算，不是模型任务的时间上限；默认 60 秒。显式增加时，宿主 MCP 调用超时也必须覆盖它。更长的独立作业应使用现有作业或原生后台执行能力。

## 本地 HTTP 服务

`service` 与 `run` 互斥。下面的服务程序必须已存在，并支持指定端口：

```json
{
  "directory": "D:/work/example",
  "service": {
    "argv": ["python", "app.py", "--port", "{port}"],
    "ready": {"path": "/health", "status": 200, "contains": "ready"},
    "requests": [
      {"method": "GET", "path": "/api/items", "status": 200, "json_pointer": "/count", "expected": 3},
      {"method": "GET", "path": "/missing", "status": 404},
      {"restart": true},
      {"method": "GET", "path": "/api/items", "status": 200, "json_pointer": "/count", "expected": 3}
    ]
  }
}
```

省略 `port` 时分配 loopback 端口，替换 argv 中的 `{port}`。请求只到该本地服务，不跟随跳转到其他地址；按 `actor` 分开 Cookie 会话，支持显式 JSON 或表单请求。HTTP 断言可以独立使用，不需要凑一个无关文件检查。服务结束后会清理本次拥有的进程；重启保留目录中的数据。

错误请求可以放在同一次服务验证中：`body_text` 直接发送原始 UTF-8 文本，`body_bytes` 直接发送 0–255 的字节数组；已有 Base64 数据也可用 `body_base64`。无需先生成编码脚本。例如 `{"method":"POST","path":"/items","status":400,"body_text":"{","content_type":"application/json"}` 发送不完整 JSON；`body_bytes: [255]` 发送非法 UTF-8。`content_type` 仅用于这三种原始请求体，默认 `application/octet-stream`。原始请求体不会经过 JSON 序列化修复。所有请求体模式与 `json`、`form` 互斥，GET/HEAD 不允许请求体；编码、类型和大小在启动服务前验证。无需为这类错误案例另写一套 HTTP 客户端和启动/停服代码。

这不是浏览器或通用业务测试框架。DOM、CSRF、动态 ID 提取及未覆盖的业务逻辑继续使用适合的原生工具。服务检查 URL 在返回后不再代表持续运行的服务。

## 证据与边界

可用 `evidence_directory` 将证据放在工作目录内指定的相对输出区，例如 `bench-delivery/.checks`。默认是 `.repowayfinder-checks`，每次新建唯一子目录，不覆盖以前证据。完整记录和日志留本地，默认响应给出通过/失败、失败摘要、操作与证据路径；仅在摘要不足时进一步读取记录。

HTTP 执行还会返回 `summary_path`，指向同一次运行的 `summary.txt`。它记录实际请求、状态码、断言结果、重启和清理情况，也保留失败或超时结论。需要交付烟测日志时，读取并引用或复制这份报告，不必重新编写服务启动、请求和停服脚本。报告仅证明本次实际覆盖的检查；不能替代未执行的业务验收。

该摘要不默认包含响应正文、请求头、查询参数值或断言的期望内容；完整本地证据按需读取。摘要最多 32 KiB，截断时仍保留最终结果及清理结论。[DSH 实际采用记录](DSH_HTTP_REPORT_REUSE.md)。

这是对当前指定文件/服务的显式断言，和部署 job 的 fresh/preserved 证明不同。通过这些断言不等于完整任务验收、全部目录合规或文件永远未变。`run`/`service` 执行程序具有普通用户权限；路径保护和进程所有权不是操作系统沙箱。

CLI 与 MCP 使用同一入口：

```powershell
python agent.py call rw_verify --file checks.json
```

命令、断言或清理失败会返回失败状态与非零 CLI 退出码。安装仍走已有 `setup.cmd` / `configure_clients.py`，无需额外模型、账号或检查服务。
