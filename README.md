# CamstarDesignerMCP

Siemens Opcenter (Camstar) Designer 元数据助手。FastMCP、浏览器聊天和 LangGraph 共用设计工具，项目仅包含 Designer 相关能力。

通过客户安装的 `Camstar.Metadata.dll` 调用真实对象模型，在独立 MDB 副本设计并导出官方差异 XML，不需要先提供字段 XML 模板。

| 能力 | 状态 |
|---|---|
| 有效 CDO、继承字段、类型、CLF、函数、事件、Query、表列、索引、映射、标签、工作区目录 | 已接入官方模型；保留原始 MDB 查询 |
| 新建 CDO、专用字段类型、持久化或非持久化字段 | 真实 MDB 保存与回读验证 |
| 字段类型长度与持久化列映射 | 已验证 String 200 与存储列 Precision 200 |
| Query、CLF、事件绑定、函数顺序与参数、标签、表列、索引、映射及属性设计 | 已实现厂商对象与专用方法；修改要求原值 |
| 官方差异导出、工作区编译、包完整性 | 已实现，不依赖独立 MetadataExport.exe |
| 发布目标数据库检查、列冲突与发布计划 | 已实现，只读 |
| 官方 Update DB、校验备份与恢复 | 已在授权测试库实际执行；数据库更新与服务部署分别报告 |
| 发布后父对象、字段类型、列长度与边界验证 | 已验证 ExProduct；临时表接受200、拒绝201字符 |
| WCF 隔离生成 | ProductMaint 实测生成329个数据契约、4个服务并检查ProductChanges；没有部署 |
| 原生 XML Import、REST 生成与服务部署 | 继续验证；不得将数据库发布当作服务已部署 |

参数和验收证据见 [能力说明](docs/designer_capabilities.md)，后续路线见 [开发计划](docs/development_plan.md)。

## 运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env  # 已有配置时不要覆盖
.\.venv\Scripts\python.exe main.py
```

默认聊天地址 http://127.0.0.1:8031/。自然语言聊天需要 LLM 配置，文件工具与官方对象模型不需要 LLM。

```ini
DESIGNER_ROOT=data/designer
DESIGNER_METADATA_ASSEMBLY=C:\实际安装目录\Camstar.Metadata.dll
DESIGNER_BRIDGE_TIMEOUT=180
DESIGNER_METADATA_EXPORT_EXE=
DESIGNER_UI_EXE=C:\Program Files (x86)\Camstar\Designer2\Designer.Net.UI.exe
DESIGNER_SERVER_IMPORT_EXE=C:\Program Files (x86)\Camstar\InSite Administration\Designer\ImportMetaData.exe
```

需要 Windows PowerShell 5.1、.NET Framework 和匹配进程位数的 ACE OLEDB 驱动。厂商 DLL 不包含在仓库中。这些是已检查安装版本中的公开成员，未确认是厂商承诺兼容的独立 SDK，升级后需重新验收。

将本地测试 MDB 放入 `DESIGNER_ROOT`。工具只把副本交给厂商组件。工作区省略时，仅选择唯一描述为 Site 的活跃客户工作区；否则须指定。

## 自然语言示例

> 在测试 MDB 中创建 ExProduct，继承 Product，新增 ExDescription，String 最多 200 字符，持久化，并生成变更包。

助手检查父对象和来源哈希后调用 `generate_designer_cdo_package`。工具创建专用类型 `ExDescription200`，避免修改共享 String 类型，生成：

```text
data/designer/artifacts/<id>/
  baseline.mdb   原始快照
  modified.mdb   已保存并回读的设计副本
  changes.xml    官方比较引擎输出的差异
  manifest.json  来源、工作区、文件哈希和执行证据
  report.md      设计与验证说明
  export/        XML、项目渲染的 HTML 报告及官方日志
```

`saved_to_test_copy_and_exported` 表示本地副本已保存和导出，服务器 MDB、业务数据库和运行服务尚未更新。

## Excel 附件设计

聊天输入框点击 **添加 Excel**，选择 `.xlsx` 或 `.xls`，核对文件卡片中的工作表与前 6 行预览，再输入例如：

> 按附件创建 Designer 对象，ExDescription 的长度改为 300，生成设计副本和变更包。

推荐每行一个字段，第一行使用中文或英文表头；同一对象的对象名、父对象可逐行填写，多个对象也可分工作表组织。例如：

| 对象名 | 父对象 | 字段名 | 数据类型 | 最大长度 | 是否持久化 | 是否列表 | 描述 |
|---|---|---|---|---|---|---|---|
| ExProduct | Product | ExDescription | String | 200 | 是 | 否 | 扩展描述 |
| ExProduct | Product | ExEnabled | Boolean | | 否 | 否 | 启用标记 |

表格数据与自然语言一起交给当前配置的模型，由助手识别设计参数并调用已有 Designer 工具。明确的文字修正优先于表格；来源、工作区、引用及名称冲突仍通过真实工具核对。上传本身仅解析文件，未填写文字时发送只要求读取并说明设计方案。超过工具单次字段/操作限制的设计需按工具合同逐步生成并核对，不会因附件功能而取消工具限制。

每次最多 3 个附件，每个最多 10 MB、10 张表，每张表范围最多 2000 行和 64 列；合计非空单元格和上下文另有上限，超限返回错误，不静默截断。预览只展示前 6 行，发送使用完整解析数据。包含隐藏工作表时会标注。`.xlsx` 公式与错误值要求先修正或粘贴为值；`.xls` 读取已保存结果，不重新计算。不执行宏；不支持 `.xlsm`、加密或损坏文件。

原始文件与解析快照存放在被 Git 忽略的 `data/excel_attachments/<id>`，绑定上传用户与对话；发送时检查原文件哈希。历史记录显示原始文字及附件卡片，完整表格内容保留在模型会话与检查点中用于后续设计。切换或新建对话会清空未发送的附件选择。

## MCP 与数据库预检

```ini
ENABLE_MCP_HTTP=True
MCP_HTTP_PATH=/mcp
MCP_API_KEY=高强度随机字符串
MCP_ALLOWED_HOSTS=localhost:*,127.0.0.1:*,[::1]:*
```

端点 http://127.0.0.1:8031/mcp/，使用 `Authorization: Bearer <MCP_API_KEY>`。HTTP MCP 默认关闭，与浏览器聊天 SSE 分开。

仅在本机 `.env` 配置 `DESIGNER_DB_SERVER/NAME/USER/PASSWORD`。管理员账号用于备份和恢复；`DESIGNER_UPDATE_DB_USER/PASSWORD` 用于官方 Update DB，其默认 schema 必须与应用 schema 一致。通过用户确认后配置 `DESIGNER_TEST_TARGET_CONFIRMED=true`。凭据不进入仓库和工具结果，工具不接受自由 SQL。

发布流程：检查设计包 → 发布预检 → `backup_designer_test_database` → `publish_designer_test_database` → `verify_designer_published_design`。发布需要精确清单哈希和一小时内的有效备份，Update DB 只更新设计和存储结构。失败可能部分更新；`restore_designer_test_database` 使用已校验备份凭证和哈希恢复明确授权的测试目标，会覆盖备份后的变更。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
# 可选：真实本地快照及已配置的官方 DLL
$env:DESIGNER_TEST_MDB='data/designer/server_snapshot/InSite.mdb'
$env:DESIGNER_TEST_VENDOR='1'
.\.venv\Scripts\python.exe -m pytest tests -q
```

真实测试检查原输入哈希不变，并验证 CDO、字段类型、持久化列与官方 XML。合成 XML 演示保留在 `examples/designer`，不能当成厂商导入样例。
