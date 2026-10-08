# CamstarDesignerMCP

> 本项目从 CamstarModelingMCP 当前工作目录复制，使用独立的 Git 历史和 GitHub 仓库，作为后续 Designer MCP 开发的起点。当前功能仍为原项目的 Modeling、Shopfloor 和 Query 能力，尚未实现 Designer 工具。
>
> 基于 **FastMCP + FastAPI + LangGraph + LLM** 的 Siemens Opcenter (Camstar) MES 建模 AI 助手。
> 将 Camstar Modeling REST API 封装为 MCP 工具，通过自然语言对话操控 Spec、Operation、Workflow 等建模实体。

---

## ✨ 功能特性

| 功能 | 说明 |
|---|---|
| **自然语言建模** | 用对话方式创建/查询/更新/删除 Spec、Operation、Workflow |
| **流式实时输出** | 基于 SSE 的逐字流式渲染，消除 LLM 阻塞等待 |
| **安全卡点防护** | 批量操作阈值拦截 + OTP 一次性密码双重验证，防止误操作 |
| **可恢复工作流** | LangGraph + SQLite 保存执行检查点，确认后从暂停位置继续 |
| **受控经验闭环** | 自动聚合脱敏失败候选，人工审核后才注入后续相关任务 |
| **标准 MCP 接入** | 提供带 Bearer 鉴权的 Streamable HTTP 端点，兼容新旧 MCP 客户端 |
| **多用户多会话** | 按用户名隔离的持久化历史记忆，支持会话切换 |
| **性能监控** | JSONL 追踪每次 LLM 推理与工具执行耗时，内置可视化 Dashboard |
| **兼容任意 LLM** | 支持 DeepSeek / 通义千问 / 智谱 / Ollama / OpenAI 等兼容 OpenAI 协议的模型 |

---

## 🗂️ 目录结构

```
CamstarDesignerMCP/
├── main.py              # 启动入口（uvicorn）
├── config.py            # 统一配置中心（读取 .env）
├── requirements.txt     # Python 依赖
├── .env.example         # 环境变量模板
│
├── core/                # 核心基础设施
│   ├── auth.py          # Bearer Token 生成
│   ├── http_client.py   # 异步 HTTP 请求分发器
│   ├── response.py      # 智能响应裁剪
│   └── perf_logger.py   # 性能日志（JSONL）
│
├── tools/               # MCP 工具层
│   ├── __init__.py      # FastMCP 实例 + 工具注册中心
│   ├── specs.py         # Spec 实体（11 个工具）
│   ├── operations.py    # Operation 实体（9 个工具）
│   ├── workflows.py     # Workflow 实体
│   └── security.py      # OTP 验证守卫
│
├── agent/               # 智能体层
│   ├── llm_client.py    # LLM 客户端 + 新旧执行引擎分发
│   ├── langgraph_runtime.py # 可恢复的 Agent 状态图
│   ├── safety.py        # 可测试的工具分类与确认策略
│   ├── memory.py        # 多用户多会话记忆管理
│   ├── prompts.py       # System Prompt
│   └── cli.py           # 命令行调试界面
│
├── web/                 # Web API 层
│   ├── app.py           # FastAPI 应用工厂
│   └── routes.py        # 路由定义
│
├── static/              # 前端静态资源
│   ├── index.html       # 聊天主界面
│   └── logs.html        # 性能日志 Dashboard
│
├── data/                # 运行时数据（自动创建）
│   ├── sessions/        # 用户会话 JSON 文件
│   └── logs/            # performance.jsonl
│
├── Swagger/             # Camstar API 原始 Swagger 文档
└── docs/
    └── DESIGN.md        # 完整程序设计文档
```

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

需要运行自动化测试时安装开发依赖：

```bash
pip install -r requirements-dev.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 Camstar 地址、账号密码和 LLM API Key
```

关键配置项：

```ini
CAMSTAR_BASE_URL=https://your-camstar-host/Modeling
CAMSTAR_SHOPFLOOR_BASE_URL=https://your-camstar-host/Shopfloor
CAMSTAR_USERNAME=CamstarAdmin
CAMSTAR_PASSWORD=your-password

LLM_API_KEY=sk-your-api-key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

AGENT_ENGINE=langgraph
LANGGRAPH_CHECKPOINT_DB=data/langgraph_checkpoints.sqlite
EXPERIENCE_DB=data/agent_experience.sqlite

# 对外提供 MCP Streamable HTTP 时启用；必须设置访问令牌
ENABLE_MCP_HTTP=True
MCP_HTTP_PATH=/mcp
MCP_API_KEY=请替换为高强度随机字符串
# 从局域网访问时加入服务端实际 IP，例如 172.25.23.19:*
MCP_ALLOWED_HOSTS=localhost:*,127.0.0.1:*,[::1]:*,172.25.23.19:*
```

### 3. 启动服务

```bash
python main.py
```

服务启动后访问：

- 💬 **聊天界面**：http://127.0.0.1:8030/
- 📊 **性能日志**：http://127.0.0.1:8030/logs
- 🔌 **MCP Streamable HTTP**：http://127.0.0.1:8030/mcp/（启用后）

### 外部 MCP 客户端配置

当前实现使用标准 **Streamable HTTP**，不再新建旧式 `/sse` MCP 传输。浏览器聊天使用的 `/chat` SSE 只是文本流，两者互不影响。

支持自定义 HTTP Header 的 MCP 客户端可使用以下通用配置：

```json
{
  "url": "http://127.0.0.1:8030/mcp/",
  "headers": {
    "Authorization": "Bearer 与 MCP_API_KEY 相同的值"
  }
}
```

局域网客户端还需把服务器 IP 加入 `MCP_ALLOWED_HOSTS`。端点默认关闭；这是因为其中包含创建工单、Start、Move、Rework 等生产写操作，未配置令牌时程序会拒绝启动外部 MCP 服务。

生产环境请在反向代理上启用 HTTPS；Bearer 令牌不应通过明文局域网 HTTP 传输。需要面向多用户或第三方客户端开放时，应进一步换成 OAuth 2.1，而不是共享一个静态令牌。

---

## 🛠️ MCP 工具一览

### Spec 实体（`/api/Specs`）

| 工具 | 方法 | 说明 |
|---|---|---|
| `list_specs` | GET | OData 查询 Spec 列表 |
| `get_spec` | GET | 按 key 获取单条 Spec |
| `get_spec_by_odata_key` | GET | OData 键语法获取 |
| `create_spec` | POST | 创建新 Spec |
| `update_spec` | PUT | 全量更新 Spec |
| `update_spec_by_odata_key` | PUT | OData 键全量更新 |
| `patch_spec` | PATCH | 部分字段更新 |
| `delete_spec` | DELETE | 删除 Spec |
| `delete_spec_by_odata_key` | DELETE | OData 键删除 |
| `get_specs_count` | GET | 获取 Spec 总数 |
| `request_selection_values` | POST | 获取 LOV 值 |

### Operation 实体（`/api/Operations`）

| 工具 | 方法 | 说明 |
|---|---|---|
| `list_operations` | GET | OData 查询 Operation 列表 |
| `get_operation` | GET | 按 key 获取单条 |
| `create_operation` | POST | 创建新 Operation |
| `update_operation` | PUT | 全量更新 |
| `delete_operation` | DELETE | 删除 Operation |
| `get_operations_count` | GET | 获取总数 |
| *(+ OData 变体及 RequestSelectionValues)* | — | — |

### Shopfloor Container 事务

| 工具 | 方法 | 说明 |
|---|---|---|
| `container_start` | POST | 按工单启动 Container，支持显式序列号或自动编号规则 |
| `container_move` | POST | 通过 MoveStd 将 Container 移动到下一工步 |
| `container_move_in` | POST | 将 Container 移入当前工步/资源开始处理 |
| `container_move_out` | POST | 将 Container 移出当前工步并继续流转 |
| `container_defect` | POST | 通过 ContainerDefect 记录 Container 缺陷 |
| `rework` | POST | 通过原始 Rework 服务将 Container 转入返工流程 |

### Container 编号参考数据

| 工具 | 方法 | 说明 |
|---|---|---|
| `list_numbering_rules` / `get_numbering_rule` | GET | 查询并校验自动编号规则 |
| `list_container_levels` / `get_container_level` | GET | 查询 Level 及其默认编号规则 |

### Query API（`/Query/api`）

| 工具 | 方法 | 说明 |
|---|---|---|
| `list_query_services` | GET | 从 Query Swagger 动态列出可用 Inquiry 与查询服务 |
| `get_query_service_schema` | GET | 查询某个 Query CDO 的路径、参数及请求字段 |
| `execute_query_inquiry` | POST | 执行标准 Inquiry CDO，支持 `$select`、`$expand` 与 `execute=true` |
| `execute_query_inquiry_event` | POST | 执行 Inquiry CDO 的自定义事件，并纳入修改安全锁 |
| `request_query_selection_values` | POST | 查询 Inquiry 字段的合法候选值 |
| `execute_advanced_query` | POST | 执行 Designer 中的系统 Advanced Query |
| `execute_user_query` | POST | 执行 Modeling UserQueries 中的 User Query |
| `execute_adhoc_query` | POST | 执行单条只读 SELECT；拒绝写入和多语句查询 |

---

## 🛡️ 安全机制

### 批量操作阈值拦截

在 `.env` 中可配置触发强制确认的阈值：

```ini
SAFE_CREATE_THRESHOLD=20   # 批量创建 > 20 条时暂停并请求确认
SAFE_UPDATE_THRESHOLD=3    # 批量更新 > 3 条时暂停
SAFE_DELETE_THRESHOLD=0    # 任意删除均需先确认（0 = >=1 拦截）
```

LangGraph 会把待执行工具调用保存在 SQLite 检查点中。系统只接受“确认创建”、
“确认修改”、“确认删除”等明确授权；“不是”“不要创建”“取消执行”不会误触发。
确认后会从暂停位置继续，不要求 LLM 重新生成工具参数。紧急回退时可设置
`AGENT_ENGINE=legacy` 并重启服务。

### OTP 单次密码验证

删除等高风险操作额外受到 OTP 守卫保护：LLM 首次尝试删除时系统自动生成一个内部 OTP，要求用户明确回复"确认删除"后，由 LLM 持 OTP 二次发起才能真正执行删除，且 OTP 一次性消耗防止重放攻击。

### 受控 Self-improvement

工具执行失败会以脱敏、去重的方式进入经验候选库，但不会自动改变 Agent 行为。查看状态和候选：

```bash
python -m agent.experience list
python -m agent.experience list --status all
```

验证解决方案后，由管理员显式批准或拒绝：

```bash
python -m agent.experience approve 12 --resolution "先通过 Details.Owner SelectionValues 解析 Owner" --evidence "2026-10-08 回归测试通过"
python -m agent.experience reject 13 --evidence "临时网络故障，不是稳定规则"
```

只有批准的经验会在相关工具或任务中注入。Container Start 请求还会直接读取项目 Skill 中的 Start contract 和 known failures，使 Codex 经验与 Web Agent 使用同一事实来源。只读状态接口为 `/api/experience/status` 和 `/api/experience/candidates`。

---

## ⚙️ 完整环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CAMSTAR_BASE_URL` | `http://localhost/Modeling` | Camstar REST API 根地址 |
| `CAMSTAR_SHOPFLOOR_BASE_URL` | 从 Modeling URL 推导 | Camstar Shopfloor REST API 根地址 |
| `CAMSTAR_QUERY_BASE_URL` | 从 Modeling URL 推导 | Camstar Query REST API 根地址 |
| `CAMSTAR_USERNAME` | `CamstarAdmin` | 登录用户名 |
| `CAMSTAR_PASSWORD` | `Cam1star` | 登录密码 |
| `CAMSTAR_TIMEOUT` | `30` | HTTP 超时（秒） |
| `MAX_RESPONSE_LENGTH` | `4000` | 响应裁剪阈值（字符） |
| `LLM_API_KEY` | — | LLM API Key |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | LLM 接口地址 |
| `LLM_MODEL` | `deepseek-chat` | 模型名称 |
| `MAX_TOOL_LOOPS` | `15` | 单轮最大工具调用次数 |
| `LANGGRAPH_RECURSION_LIMIT` | `MAX_TOOL_LOOPS * 6 + 10` | LangGraph 单次请求的节点步数上限 |
| `AGENT_ENGINE` | `langgraph` | 执行引擎；可设为 `legacy` 回退 |
| `LANGGRAPH_CHECKPOINT_DB` | `data/langgraph_checkpoints.sqlite` | 工作流检查点数据库 |
| `EXPERIENCE_DB` | `data/agent_experience.sqlite` | 脱敏经验事件、候选及审核结果数据库 |
| `ENABLE_PERFORMANCE_LOG` | `True` | 是否开启性能日志 |
| `SAFE_CREATE_THRESHOLD` | `20` | 批量创建拦截阈值 |
| `SAFE_UPDATE_THRESHOLD` | `3` | 批量更新拦截阈值 |
| `SAFE_DELETE_THRESHOLD` | `0` | 批量删除拦截阈值 |

**支持的 LLM Provider（替换 `LLM_BASE_URL` 即可，代码零改动）：**

| Provider | LLM_BASE_URL |
|---|---|
| DeepSeek | `https://api.deepseek.com/v1` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` |
| Ollama 本地 | `http://localhost:11434/v1` |
| OpenAI | `https://api.openai.com/v1` |

---

## 📐 架构概览

```
Browser ──HTTP/SSE──▶ FastAPI (web/)
                          │
                      Agent (agent/)
                     llm_client.py
                          │ OpenAI-compatible API
                      LLM Provider
                          │ function_call
                      Tools (tools/)
                     specs / operations / workflows
                          │
                      Core (core/)
                    auth / http_client / response
                          │ REST/HTTPS
                   Camstar Modeling API

MCP Client ──Streamable HTTP + Bearer──▶ FastMCP (/mcp/)
                                             │
                                        同一组 Tools
```

> 详细设计请参阅 [`docs/DESIGN.md`](docs/DESIGN.md)

---

## 📦 依赖

```
fastmcp>=4.0.11,<5.0.0
mcp>=2.3.0,<3.0.0
httpx>=0.27.0
python-dotenv>=1.0.0
openai>=1.0.0
fastapi>=0.142.0,<0.143.0
uvicorn>=0.20.0
```
