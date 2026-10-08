# Camstar Modeling MCP Agent — 程序设计文档

> **版本**：v1.0 &nbsp;|&nbsp; **更新日期**：2026-04-15  
> **项目路径**：`d:\Deepseek\camstar\CamstarModelingMCP`

---

## 目录

1. [项目概述](#1-项目概述)
2. [技术栈](#2-技术栈)
3. [整体架构](#3-整体架构)
4. [目录结构](#4-目录结构)
5. [模块详解](#5-模块详解)
   - 5.1 [入口层 — main.py](#51-入口层--mainpy)
   - 5.2 [配置中心 — config.py](#52-配置中心--configpy)
   - 5.3 [Core 核心库 — core/](#53-core-核心库--core)
   - 5.4 [Tools 工具层 — tools/](#54-tools-工具层--tools)
   - 5.5 [Agent 智能体层 — agent/](#55-agent-智能体层--agent)
   - 5.6 [Web 接口层 — web/](#56-web-接口层--web)
6. [完整请求数据流](#6-完整请求数据流)
7. [安全防御机制](#7-安全防御机制)
8. [性能监控设计](#8-性能监控设计)
9. [会话与记忆管理](#9-会话与记忆管理)
10. [MCP 工具全览](#10-mcp-工具全览)
11. [环境变量配置](#11-环境变量配置)
12. [部署指南](#12-部署指南)
13. [扩展指南](#13-扩展指南)

---

## 1. 项目概述

**Camstar Modeling MCP Agent** 是面向 Siemens Opcenter (Camstar) MES 系统的 **AI 建模助手**，将 Camstar Modeling REST API 封装为 MCP（Model Context Protocol）工具，并通过 DeepSeek / OpenAI 兼容接口驱动大语言模型（LLM）进行智能操控。

### 核心功能

| 功能模块 | 描述 |
|---|---|
| **MCP 工具注册** | 将 Camstar REST API 封装为可被 LLM 调用的标准工具 |
| **流式对话** | 基于 SSE 的实时流式输出，消除 LLM 阻塞等待 |
| **安全卡点** | OTP 验证 + 批量操作阈值拦截，防止 LLM 误操作数据 |
| **会话记忆** | 多用户、多会话持久化历史记录，跨轮上下文感知 |
| **性能监控** | JSONL 追踪 LLM 推理与工具执行耗时 |
| **Web UI** | 内置聊天界面 + 性能日志可视化 Dashboard |

---

## 2. 技术栈

| 层次 | 技术 | 版本要求 |
|---|---|---|
| Web 框架 | FastAPI + Uvicorn | `>=0.100.0` / `>=0.20.0` |
| MCP 框架 | FastMCP | `>=4.0.11,<5.0.0` |
| HTTP 客户端 | httpx (异步) | `>=0.27.0` |
| LLM SDK | openai (兼容协议) | `>=1.0.0` |
| 配置管理 | python-dotenv | `>=1.0.0` |
| 数据持久化 | JSON 文件系统 | — |
| 性能日志 | JSONL 追加写入 | — |
| 前端 | 原生 HTML + CSS + JS | — |

---

## 3. 整体架构

```
┌─────────────────────────────────────────────────────┐
│                     Browser / Client                 │
│              (Chat UI / MCP Inspector)               │
└──────────────────────────┬──────────────────────────┘
                           │ HTTP / SSE
┌──────────────────────────▼──────────────────────────┐
│                   Web Layer (FastAPI)                │
│   routes.py: /  /chat  /sessions  /history  /logs   │
└──────────────────────────┬──────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────┐
│                   Agent Layer                        │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │ llm_client  │  │   memory     │  │  prompts   │  │
│  │ (stream +   │  │ (multi-user  │  │ (system    │  │
│  │  tool-loop) │  │  session)    │  │  prompt)   │  │
│  └──────┬──────┘  └──────────────┘  └────────────┘  │
│         │ OpenAI API (streaming)                     │
└─────────┼───────────────────────────────────────────┘
          │
┌─────────▼───────────────────────────────────────────┐
│             LLM Provider (DeepSeek / OpenAI)         │
└─────────────────────────────────────────────────────┘
          │ function_call
┌─────────▼───────────────────────────────────────────┐
│                   Tools Layer (FastMCP)              │
│  ┌──────────┐  ┌────────────┐  ┌─────────────────┐  │
│  │ specs.py │  │operations  │  │  workflows.py   │  │
│  │ (11工具) │  │ (9工具)    │  │  (N工具)        │  │
│  └────┬─────┘  └─────┬──────┘  └────────┬────────┘  │
│       └──────────────┴──────────────────┘           │
│                    security.py (OTP Guard)           │
└──────────────────────────┬──────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────┐
│                   Core Layer                         │
│  ┌─────────────┐  ┌──────────┐  ┌────────────────┐  │
│  │ http_client │  │  auth    │  │  response      │  │
│  │ (httpx异步) │  │ (Bearer) │  │ (智能裁剪)     │  │
│  └──────┬──────┘  └──────────┘  └────────────────┘  │
│         │  perf_logger (JSONL)                       │
└─────────┼───────────────────────────────────────────┘
          │ REST / HTTPS
┌─────────▼───────────────────────────────────────────┐
│           Camstar Modeling REST API                  │
│     /api/Specs   /api/Operations   /api/Workflows    │
└─────────────────────────────────────────────────────┘
```

---

## 4. 目录结构

```
CamstarModelingMCP/
├── main.py                  # 程序启动入口 (uvicorn)
├── config.py                # 统一配置中心（读取 .env）
├── requirements.txt         # Python 依赖
├── .env                     # 本地环境变量（不提交 Git）
├── .env.example             # 环境变量模板
│
├── core/                    # 核心基础设施层
│   ├── auth.py              # Bearer Token 生成（Base64 编码）
│   ├── http_client.py       # 异步 HTTP 请求分发器
│   ├── response.py          # 智能响应裁剪（关键字段提取）
│   └── perf_logger.py       # 性能日志记录（JSONL）
│
├── tools/                   # MCP 工具注册层
│   ├── __init__.py          # FastMCP 实例 + 工具注册中心
│   ├── specs.py             # Spec 实体操作（11 个工具）
│   ├── operations.py        # Operation 实体操作（9 个工具）
│   ├── workflows.py         # Workflow 实体操作（N 个工具）
│   └── security.py          # OTP 验证守卫
│
├── agent/                   # 智能体层
│   ├── llm_client.py        # LLM 调用 + 工具编排 + 安全拦截
│   ├── memory.py            # 多用户多会话记忆管理
│   ├── prompts.py           # System Prompt 集中管理
│   └── cli.py               # 命令行交互界面（调试用）
│
├── web/                     # Web API 层 (FastAPI)
│   ├── app.py               # FastAPI 应用工厂
│   └── routes.py            # 路由定义
│
├── static/                  # 前端静态资源
│   ├── index.html           # 主聊天界面
│   ├── logs.html            # 性能日志 Dashboard
│   └── svg/                 # SVG 图标资源
│
├── data/                    # 运行时数据（本地持久化）
│   ├── sessions/            # 用户会话 JSON 文件目录
│   │   └── {username}/      # 按用户名隔离
│   │       ├── metadata.json
│   │       └── {session_id}.json
│   └── logs/
│       └── performance.jsonl # 性能日志
│
├── Swagger/                 # Camstar API 原始 Swagger 文档
├── docs/                    # 项目文档
└── tests/                   # 单元/集成测试
```

---

## 5. 模块详解

### 5.1 入口层 — `main.py`

**职责**：程序启动的唯一入口点。

```python
# 关键逻辑
app = create_app()          # 调用 web/app.py 工厂方法
uvicorn.run("main:app", host="0.0.0.0", port=8030, reload=False)
```

- 强制设置 stdout 为 UTF-8 编码（解决 Windows 中文乱码）
- 委托给 `web.app.create_app()` 创建并配置 FastAPI 实例

---

### 5.2 配置中心 — `config.py`

**职责**：所有环境变量的**唯一读取点**，其他模块通过 `from config import xxx` 使用，绝不直接调用 `os.getenv`。

| 配置变量 | 默认值 | 用途 |
|---|---|---|
| `CAMSTAR_BASE_URL` | `http://localhost/Modeling` | Camstar REST API 根地址 |
| `CAMSTAR_USERNAME` | `CamstarAdmin` | API 认证用户名 |
| `CAMSTAR_PASSWORD` | `Cam1star` | API 认证密码 |
| `CAMSTAR_TIMEOUT` | `30` | HTTP 请求超时（秒） |
| `MAX_RESPONSE_LENGTH` | `4000` | 响应裁剪阈值（字符数） |
| `LLM_API_KEY` | — | LLM Provider API Key |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | LLM 接口地址（OpenAI 协议兼容） |
| `LLM_MODEL` | `deepseek-chat` | 使用的模型名称 |
| `MAX_TOOL_LOOPS` | `15` | 单轮最大工具调用次数上限 |
| `SAFE_CREATE_THRESHOLD` | `20` | 批量创建拦截阈值 |
| `SAFE_UPDATE_THRESHOLD` | `3` | 批量更新拦截阈值 |
| `SAFE_DELETE_THRESHOLD` | `0` | 批量删除拦截阈值（0 = 任意删除均拦截） |
| `ENABLE_PERFORMANCE_LOG` | `True` | 是否开启性能日志 |

---

### 5.3 Core 核心库 — `core/`

#### `core/auth.py` — 认证令牌生成

Camstar 使用自定义 Bearer Token，生成规则：

```
Token = Base64( JSON({ "username": "xxx", "Password": { "value": "yyy", "isEncrypted": false } }) )
```

> **设计原因**：Camstar 的认证协议不同于标准 Basic Auth，Token 按此固定格式构造后通过 `Authorization: Bearer <token>` 头传输。

#### `core/http_client.py` — 异步 HTTP 分发器

所有 MCP 工具的 HTTP 请求统一经过此模块分发：

```
请求入口 request(method, path, body, params)
    │
    ├─ build_url()       → 拼接完整 URL
    ├─ get_headers()     → 动态生成认证头（每次请求即时生成 Token）
    │
    └─ httpx.AsyncClient.request()
            │
            ├─ 4xx/5xx → 返回格式化错误信息 ❌
            ├─ 空响应  → 返回成功提示 ✅
            └─ 正常响应 → smart_response() 智能裁剪
```

**关键设计**：
- 使用 `verify=False` 禁用 SSL 验证（Camstar 常用自签名证书）
- Token 在每次请求时即时生成，无缓存，确保密码变更立即生效

#### `core/response.py` — 智能响应裁剪

当 API 响应超过 `MAX_RESPONSE_LENGTH` 时，自动提取关键字段，防止超出 LLM 上下文窗口：

**关键字段白名单**（case-insensitive）：

```python
KEY_FIELDS = {
    "instanceid", "displayname", "name", "revision",
    "status", "description", "isfrozen", "isrevofrcd",
    "lastchangedate", "lastchangedategmt",
    "creationdate", "creationdategmt", "creationusername",
    "currentstatus", "control", "eco", "operation", "useror"
}
```

同时支持 OData `value` 数组递归提取。

#### `core/perf_logger.py` — 性能日志

以 **JSONL（JSON Lines）** 格式追加写入 `data/logs/performance.jsonl`：

```json
{
  "timestamp": "2026-04-15 14:30:00.123",
  "start_time": "2026-04-15 14:29:58.456",
  "end_time": "2026-04-15 14:30:00.123",
  "action": "LLM_Inference",
  "duration_ms": 1667.5,
  "username": "CamstarAdmin",
  "session_id": "abc-123",
  "details": { "model": "deepseek-chat", "prompt": "...", "streamed": true }
}
```

追踪两类事件：
- `LLM_Inference`：LLM 推理耗时
- `Tool_Execute`：单个 MCP 工具执行耗时

---

### 5.4 Tools 工具层 — `tools/`

#### `tools/__init__.py` — FastMCP 注册中心

```python
mcp = FastMCP("CamstarModeling", instructions="...")

# 所有工具模块在此 import，自动触发 @mcp.tool 注册
from tools import specs
from tools import operations
from tools import workflows

def get_tool_func(name: str):
    """按名称查找工具函数，供 Agent 直接调用"""
    for module in [specs, operations, workflows]:
        func = getattr(module, name, None)
        if func: return func
    return None
```

> **扩展设计**：新增实体类型只需创建新模块并在此 import，无需修改其他代码。

#### `tools/specs.py` — Spec 实体（11 个工具）

| 工具函数 | HTTP | 路径 | 说明 |
|---|---|---|---|
| `list_specs` | GET | `/api/Specs` | OData 查询列表 |
| `get_spec` | GET | `/api/Specs/{key}` | 按 key 获取单条 |
| `get_spec_by_odata_key` | GET | `/api/Specs({key})` | OData 键语法获取 |
| `create_spec` | POST | `/api/Specs` | 创建新 Spec |
| `update_spec` | PUT | `/api/Specs/{key}` | 全量更新 |
| `update_spec_by_odata_key` | PUT | `/api/Specs({key})` | OData 键全量更新 |
| `patch_spec` | PATCH | `/api/Specs` | 部分更新 |
| `delete_spec` | DELETE | `/api/Specs/{key}` | 删除 |
| `delete_spec_by_odata_key` | DELETE | `/api/Specs({key})` | OData 键删除 |
| `get_specs_count` | GET | `/api/Specs/$count` | 获取总数 |
| `request_selection_values` | POST | `/api/Specs/RequestSelectionValues` | 获取 LOV 值 |

#### `tools/operations.py` — Operation 实体（9 个工具）

| 工具函数 | HTTP | 路径 |
|---|---|---|
| `list_operations` | GET | `/api/Operations` |
| `get_operation` | GET | `/api/Operations/{key}` |
| `get_operation_by_odata_key` | GET | `/api/Operations({key})` |
| `create_operation` | POST | `/api/Operations` |
| `update_operation` | PUT | `/api/Operations/{key}` |
| `update_operation_by_odata_key` | PUT | `/api/Operations({key})` |
| `delete_operation` | DELETE | `/api/Operations/{key}` |
| `delete_operation_by_odata_key` | DELETE | `/api/Operations({key})` |
| `get_operations_count` | GET | `/api/Operations/$count` |
| `request_operation_selection_values` | POST | `/api/Operations/RequestSelectionValues` |

#### `tools/security.py` — OTP 验证守卫

内存中维护一个 `OTP_STORE` 字典，对高危操作（尤其是 delete 类）执行一次性密码校验：

```
第一次调用 delete_xxx（无 otp_code）
    → 生成 6 位随机 OTP 存入 OTP_STORE
    → 返回拦截警告（不向用户暴露 OTP）
    → 提示用户回复"确认删除"

LLM 接收到用户确认 → 第二次调用 delete_xxx（携带 otp_code）
    → 验证 OTP 匹配 → 清除 OTP_STORE → 放行执行
```

> **安全设计原则**：OTP 仅在 LLM 侧可见，完全不暴露给终端用户，防止 Prompt Injection 攻击直接伪造授权。

---

### 5.5 Agent 智能体层 — `agent/`

#### `agent/prompts.py` — System Prompt

定义 Agent 的行为准则：
1. **严格参数审查**：缺少必填参数时拒绝调用，主动询问用户
2. **互动追问**：参数不足时友好提示具体缺失字段
3. **记忆连贯性**：结合历史上下文拼接完整参数
4. **专业风格**：使用 Markdown 格式，简练专业

#### `agent/memory.py` — 多用户多会话记忆

**数据结构设计**：

```
data/sessions/
└── {username}/
    ├── metadata.json          # 记录 active_session_id
    └── {session_uuid}.json    # 单会话数据
```

单会话文件结构：
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "批量创建 Operation",
  "messages": [
    { "role": "system", "content": "..." },
    { "role": "user", "content": "..." },
    { "role": "assistant", "content": "..." }
  ]
}
```

**关键特性**：
- **多用户隔离**：按 `username` 分目录存储
- **多会话支持**：每用户可有多个独立会话
- **热迁移兼容**：兼容旧版单文件 `memory.json` 格式，自动迁移至新结构
- **System Prompt 热更新**：每次 `get_user_messages` 时强制替换最新的 system prompt

#### `agent/llm_client.py` — LLM 工具编排核心

这是整个 Agent 最核心的模块，负责：

**① 工具注册**（启动时执行）：
```python
mcp_tools = await mcp.list_tools()
# 转换为 OpenAI function calling 格式
openai_tools = [{"type": "function", "function": {...}} for t in mcp_tools]
```

**② 流式对话循环**（每次用户请求执行）：

```
chat_stream(username, message, session_id)
│
├─ 追加 user 消息到历史记忆
├─ [首轮] 异步生成会话标题（generate_title）
│
└─ WHILE loop（最大 MAX_TOOL_LOOPS 次）：
    │
    ├─ 调用 LLM（stream=True）
    │   ├─ 拼接流式 delta.content → 推送 stream_chunk 给前端
    │   └─ 拼接工具调用 delta.tool_calls
    │
    ├─ [无工具调用] → 推送 stream_end/done → 退出循环
    │
    └─ [有工具调用] → 对每个 tool_call：
        │
        ├─ 安全检查：
        │   ├─ create_* → 计数是否超过 SAFE_CREATE_THRESHOLD
        │   ├─ update_*/patch_*/rebuild_* → 计数是否超过 SAFE_UPDATE_THRESHOLD
        │   └─ delete_* → 计数是否超过 SAFE_DELETE_THRESHOLD
        │       → 超过且未获用户确认 → 注入拦截消息 → continue
        │
        └─ 执行工具 get_tool_func(name)(**args)
            → 记录耗时（perf_logger）
            → 将 tool_result 追加到消息历史
            → 继续下一次 LLM 调用
```

**用户确认检测逻辑**：
```python
confirmed_words = ["确认", "确定", "是", "修改", "ok", "yes", "y", "继续"]
is_user_confirmation = len(last_user_msg) < 20 and any(w in last_user_msg.lower() for w in confirmed_words)
```

---

### 5.6 Web 接口层 — `web/`

#### `web/app.py` — FastAPI 应用工厂

利用 FastAPI `lifespan` 在应用启动时：
1. 校验 `CAMSTAR_BASE_URL` 配置有效性
2. 调用 `init_memory()` 恢复历史会话
3. 调用 `register_tools()` 从 MCP Server 加载并注册工具

#### `web/routes.py` — 路由定义

| 路由 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 返回主聊天界面 HTML |
| `/config` | GET | 返回前端配置（当前绑定用户名） |
| `/sessions/{username}` | GET | 获取用户所有会话列表 |
| `/sessions/{username}/new` | POST | 为用户创建新会话 |
| `/history/{username}` | GET | 获取聊天历史记录 |
| `/chat` | POST | **流式聊天主接口（SSE）** |
| `/logs` | GET | 性能日志 Dashboard 页面 |
| `/api/logs/data` | GET | 获取结构化性能日志数据 |

**聊天请求协议**：
```json
POST /chat
{
  "message": "帮我查询所有 Spec",
  "username": "CamstarAdmin",
  "session_id": "550e8400-..."
}
```

**SSE 响应事件类型**：

| event data type | 含义 |
|---|---|
| `stream_start` | LLM 开始输出文本 |
| `stream_chunk` | 文本增量片段（`content` 字段） |
| `stream_end` | 文本输出完毕 |
| `done` | 最终回复（无流式时的兜底） |
| `step` | 工具调用进度（`func` + `args` 字段） |
| `title_update` | 首轮自动更新会话标题 |
| `error` | 错误信息 |

---

## 6. 完整请求数据流

以用户发送 **"查询所有 Spec"** 为例：

```
① 用户在浏览器输入消息
        ↓
② POST /chat { message, username, session_id }
        ↓
③ routes.py → StreamingResponse(chat_stream(...))
        ↓
④ agent/llm_client.py::chat_stream()
   - 加载历史消息（含 system prompt）
   - 追加用户消息
        ↓
⑤ 调用 OpenAI Chat API (stream=True)
   - 模型决策：调用 list_specs 工具
        ↓ SSE: { type: "step", func: "list_specs", args: "{}" }
        ↓
⑥ tools/specs.py::list_specs()
   - 调用 core/http_client.py::request("GET", "/api/Specs")
        ↓
⑦ core/auth.py::generate_camstar_auth_token()
   - 生成 Bearer Token
        ↓
⑧ httpx.AsyncClient → GET https://<camstar>/Modeling/api/Specs
        ↓
⑨ core/response.py::smart_response(data)
   - 若响应 > 4000 字符 → 提取关键字段
        ↓
⑩ 工具结果写入 chat_messages
   - core/perf_logger.py 记录 Tool_Execute 耗时
        ↓
⑪ 第二次调用 LLM（携带工具结果）
   - 模型生成自然语言回答（stream）
        ↓ SSE: { type: "stream_start" }
        ↓ SSE: { type: "stream_chunk", content: "查询到..." }
        ↓ SSE: { type: "stream_end" }
   - perf_logger 记录 LLM_Inference 耗时
        ↓
⑫ 前端渲染完整回答，用户看到结果
```

---

## 7. 安全防御机制

项目实现了**两层**独立的安全防御：

### 第一层：批量操作阈值拦截

由 `agent/llm_client.py` 在工具执行前执行，基于单轮累计计数：

| 操作类型 | 默认触发阈值 | 配置变量 |
|---|---|---|
| `create_*` | > 20 条 | `SAFE_CREATE_THRESHOLD` |
| `update_*` / `patch_*` / `rebuild_*` | > 3 条 | `SAFE_UPDATE_THRESHOLD` |
| `delete_*` | >= 1 条 | `SAFE_DELETE_THRESHOLD=0` |

**触发后行为**：
- 向消息历史注入系统拦截消息
- LLM 收到消息后，向用户列出待操作数据并请求确认
- 用户回复确认词（"确认修改"、"确认删除"等）后解除拦截

### 第二层：OTP 单次密码验证

由 `tools/security.py` 在工具内部执行，针对极高风险操作：

```
LLM 调用 delete_* → verify_and_generate_otp(action_id, otp_code="")
    → OTP_STORE 中无记录 → 生成新 OTP → 存储 → 返回警告消息
    → LLM 向用户说明风险，请求"确认删除"
    → 用户确认 → LLM 再次调用，传入 otp_code
    → OTP 验证通过 → 清除 OTP_STORE（防重放）→ 操作执行
```

> **防重放设计**：OTP 在验证成功后立即从 `OTP_STORE` 删除，每次操作消耗一个独立 OTP。

---

## 8. 性能监控设计

### 数据采集

两个采集点均在 `agent/llm_client.py` 中：

```python
# LLM 推理耗时
llm_start = time.time()
# ... streaming ...
record_perf("LLM_Inference", time.time() - llm_start, username, session_id, {"model": LLM_MODEL})

# 工具执行耗时
tool_start = time.time()
result = await tool_func(**func_args)
record_perf("Tool_Execute", time.time() - tool_start, username, session_id, {"tool": func_name})
```

### 日志访问

- **API**：`GET /api/logs/data` → 返回最新 1000 条日志
- **Dashboard**：`GET /logs` → 可视化图表页面

### 日志字段

| 字段 | 说明 |
|---|---|
| `action` | `LLM_Inference` 或 `Tool_Execute` |
| `duration_ms` | 执行耗时（毫秒） |
| `start_time` / `end_time` | 精确到毫秒的时间戳 |
| `username` | 操作用户 |
| `session_id` | 所属会话 |
| `details.model` | LLM 模型名（LLM 事件） |
| `details.tool` | 工具名称（Tool 事件） |
| `details.prompt` | 触发本次执行的原始用户消息 |

---

## 9. 会话与记忆管理

### 数据隔离模型

```
user_memories (内存字典)
└── username: "CamstarAdmin"
    ├── active_session: "session-uuid-1"
    └── sessions:
        ├── "session-uuid-1":
        │   ├── id: "session-uuid-1"
        │   ├── title: "创建 Spec 任务"
        │   └── messages: [ {role, content}, ... ]
        └── "session-uuid-2": { ... }
```

### 持久化策略

| 操作 | 触发时机 |
|---|---|
| `save_session(username, session_id)` | 每次 LLM 回复后 |
| `_save_metadata(username)` | 会话创建/切换时 |
| `save_memory()` | 全量保存（兼容旧接口） |
| `load_memory()` | 应用启动时 `init_memory()` 调用 |

### 向前兼容

支持从旧版单文件 `data/memory.json` 热迁移至新的分离文件结构，确保历史数据不丢失。

---

## 10. MCP 工具全览

| 类别 | 工具数 | 覆盖实体 |
|---|---|---|
| Spec 实体 | 11 | `/api/Specs` |
| Operation 实体 | 9 | `/api/Operations` |
| Workflow 实体 | 若干 | `/api/Workflows` |
| Product 实体 | 10 | `/api/Products` |
| MfgOrder 实体 | 11 | `/api/MfgOrders` |
| **合计** | **40+** | — |

**通用参数设计规范**：
- 所有 list 类工具均支持 OData 参数：`$filter`, `$top`, `$skip`, `$select`, `$expand`, `$orderby`
- 所有 create/update 类工具均提供 `body_json` 扩展参数，支持传入任意额外字段
- 关键实体（Spec, Operation）均支持两种 key 格式：`/api/Entity/{key}` 和 `/api/Entity({key})`（OData 语法）

---

## 11. 环境变量配置

完整 `.env` 配置参考：

```ini
# === Camstar API 配置 ===
CAMSTAR_BASE_URL=https://172.27.251.56/Modeling
CAMSTAR_USERNAME=CamstarAdmin
CAMSTAR_PASSWORD=Cam1star
CAMSTAR_TIMEOUT=30
MAX_RESPONSE_LENGTH=4000

# === LLM 大模型配置（兼容 OpenAI 协议）===
LLM_API_KEY=sk-your-api-key-here
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# === Agent 行为配置 ===
MAX_TOOL_LOOPS=30

# === 性能监控 ===
ENABLE_PERFORMANCE_LOG=True

# === 安全卡点阈值 ===
SAFE_CREATE_THRESHOLD=20   # 批量创建 > 20 条触发确认
SAFE_UPDATE_THRESHOLD=3    # 批量更新 > 3 条触发确认
SAFE_DELETE_THRESHOLD=0    # 任意删除均触发确认（0 = >=1 拦截）
```

**支持的 LLM Provider**（`LLM_BASE_URL` 替换即可）：

| Provider | Base URL |
|---|---|
| DeepSeek | `https://api.deepseek.com/v1` |
| 通义千问 (Qwen) | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 智谱 (GLM) | `https://open.bigmodel.cn/api/paas/v4` |
| Ollama 本地 | `http://localhost:11434/v1` |
| OpenAI | `https://api.openai.com/v1` |

---

## 12. 部署指南

### 本地开发启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入正确的 Camstar 地址和 API Key

# 3. 启动服务
python main.py
# 服务地址: http://127.0.0.1:8030

# 4. 访问界面
# 聊天主界面: http://127.0.0.1:8030/
# 性能日志:   http://127.0.0.1:8030/logs
```

### 用作 MCP Server（Streamable HTTP）

在 `.env` 中启用独立的 MCP 协议入口：

```ini
ENABLE_MCP_HTTP=True
MCP_HTTP_PATH=/mcp
MCP_API_KEY=replace-with-a-long-random-secret
MCP_ALLOWED_HOSTS=localhost:*,127.0.0.1:*,[::1]:*
```

支持远程 MCP 的客户端连接 `http://127.0.0.1:8030/mcp/`，并附带：

```json
{
  "url": "http://127.0.0.1:8030/mcp/",
  "headers": {
    "Authorization": "Bearer replace-with-a-long-random-secret"
  }
}
```

`/chat` 的 SSE 是浏览器答案流，不是旧版 MCP HTTP+SSE 传输。MCP 客户端统一使用 `/mcp/` 的 Streamable HTTP。

---

## 13. 扩展指南

### 新增实体类型（如 `Containers`）

1. **创建工具模块**：
   ```python
   # tools/containers.py
   from tools import mcp
   from core.http_client import request

   @mcp.tool
   async def list_containers(...) -> str:
       """..."""
       return await request("GET", "/api/Containers", ...)
   ```

2. **注册到工具中心**：
   ```python
   # tools/__init__.py
   from tools import containers  # 添加这一行

   def get_tool_func(name: str):
       for module in [specs, operations, workflows, containers]:  # 追加
           ...
   ```

3. **完成** — LLM 自动感知新工具，无需其他修改。

### 替换 LLM Provider

仅需修改 `.env` 中的三个配置项，代码零改动：
```ini
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=sk-your-qwen-key
LLM_MODEL=qwen-max
```

### 调整安全阈值

根据业务场景调整 `.env` 中的阈值，服务重启后生效：
```ini
SAFE_UPDATE_THRESHOLD=10   # 允许更多批量操作
SAFE_DELETE_THRESHOLD=5    # 允许少量删除不拦截
```

---

*文档由 Antigravity AI 根据源码自动分析生成 · 2026-04-15*
