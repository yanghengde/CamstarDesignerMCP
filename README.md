# CamstarDesignerMCP

Siemens Opcenter (Camstar) Designer 元数据助手，复用 FastMCP、FastAPI、LangGraph 和自然语言对话框架。

当前是第一阶段 MVP：读取本地元数据、生成字段变更草案、校验并比较差异。所有工具仅服务于 Designer 元数据设计。

## 当前能力

| 能力 | 状态 |
|---|---|
| MDB 表结构、CDO、字段与类型读取 | 已在服务器 MDB 的本地副本验证，只读；工作区和继承尚未合并 |
| XML 的 CDO/字段检索 | 支持手册中的 InSiteMetaData 1.0 |
| 简单字段草案生成 | 复制现有非持久化、非列表字段模板，附来源哈希与报告 |
| 结构校验、包完整性、来源漂移 | 已实现，不等同于官方导入验证 |
| XML 定义差异比较 | 已实现，仅比较输入 XML 中出现的定义 |
| 官方 MetadataExport 封装 | 按手册参数执行输入副本，真实程序尚待验证 |
| 存储映射、自动导入、Update DB、服务生成 | 尚未实现 |

## 运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env  # 已有 .env 时不要覆盖
.\.venv\Scripts\python.exe main.py
```

聊天地址默认 http://127.0.0.1:8031/。

配置 `.env` 中的 LLM 接口用于自然语言聊天。离线演示和 MCP 文件工具不需要调用 LLM。

```ini
SERVER_PORT=8031
DESIGNER_ROOT=data/designer
DESIGNER_METADATA_EXPORT_EXE=
DESIGNER_EXPORT_TIMEOUT=120
```

把测试 MDB 的副本和 Designer 导出的 XML 放入 `DESIGNER_ROOT`。不要把生产 MDB 作为实验输入。需要官方差异导出时，再填写已安装的 `MetadataExport.exe` 的绝对路径。MDB 读取要求 Windows Access ODBC 驱动与 Python 位数一致。

## 离线演示与测试

```powershell
.\.venv\Scripts\python.exe -m designer.demo
.\.venv\Scripts\python.exe -m pytest tests -q
# 可选：真实 MDB 副本集成测试，前后校验文件哈希
$env:DESIGNER_TEST_MDB="data/designer/server_snapshot/InSite.mdb"
.\.venv\Scripts\python.exe -m pytest tests/test_designer_mdb_integration.py -q
```

演示使用 `examples/designer/demo_metadata.xml` 合成数据，不代表真实 Siemens 导出或已验证的导入模板。每次生成独立目录：

```text
data/designer/artifacts/<id>/
  changes.xml     字段变更草案
  baseline.xml    本次读取定义的审计快照
  manifest.json   来源 SHA256、工作区、校验状态和计划
  report.md       变更说明与测试导入步骤
```

可在聊天中输入：

> 检查 Designer 环境，列出可用元数据文件。

> 查询 demo_metadata.xml 中 DemoContainer 的定义，使用 ExistingText 模板，在 customer 工作区生成 ExternalLotNumber 字段草案，描述为“外部批次号”。

真实需求应使用真实导出文件及用户指定的客户工作区。MVP 不生成持久化字段或存储映射。

## MCP 接入

```ini
ENABLE_MCP_HTTP=True
MCP_HTTP_PATH=/mcp
MCP_API_KEY=替换为高强度随机字符串
MCP_ALLOWED_HOSTS=localhost:*,127.0.0.1:*,[::1]:*
```

客户端使用 `http://127.0.0.1:8031/mcp/` 和 `Authorization: Bearer <MCP_API_KEY>`。端点默认关闭；启用后要求 Bearer 认证。HTTP 传输与浏览器聊天 SSE 分开。

## 验证边界

生成包始终标记为 `draft_requires_test_import`，`ready_for_publish=false`。工作区只记录在 manifest，需在 Designer 中选择。Action=Create 不保证目标字段不存在；Stop 仅对已指定预期值的属性冲突生效。XML 导出可能仅包含差异，不能据此确认目标 MDB 中字段不存在或已删除。

已取得服务器 MDB 的本地副本，并验证表结构、CDO/字段与类型读取。下一步仍需要真实字段导出样例和可用的 Designer/MetadataExport 程序，完成工作区继承解析、测试导入和导出核对，再开发存储映射及发布。

工具参数、文档依据和实施范围见 [Designer MVP 说明](docs/designer_mvp.md)。
