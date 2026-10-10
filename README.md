# CamstarDesignerMCP

Siemens Opcenter（Camstar）Designer 元数据设计助手。通过自然语言和 Excel 描述对象、字段及设计要求，在固定工作 MDB 中连续设计，再通过 Designer 或工作台完成审核、编译、数据库更新与结果核验。

浏览器工作台、LangGraph 对话流程与 FastMCP 共用 Designer 工具。项目通过已安装的 `Camstar.Metadata.dll` 调用厂商对象模型，保存并回读 MDB，导出官方差异 XML。

## 快速启动

在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

脚本自动创建或复用 `.venv`，根据 `requirements.txt` 安装缺少或版本不匹配的依赖，再启动服务；已有符合要求的依赖不会重复下载安装。仅首次创建 `.env`，保留已有配置。需要 Python 3.10 或以上版本；首次创建配置后，请填写模型接口与 Designer 环境配置。只准备依赖、不启动服务时，在命令末尾添加 `-InstallOnly`。

也可以手动准备环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 仅首次创建配置，保留已有 .env
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

编辑 `.env`，填写模型接口与 Designer 环境配置，然后启动：

```powershell
.\.venv\Scripts\python.exe main.py
```

默认端口为 **8032**，访问 [设计工作台](http://127.0.0.1:8032/)。`.env` 中的 `SERVER_PORT` 或启动进程的同名环境变量可覆盖默认值，修改后需重启服务。

```ini
SERVER_PORT=8032
CHAT_USERNAME=designer
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
DESIGNER_ROOT=data/designer
DESIGNER_METADATA_ASSEMBLY=C:\实际安装目录\Camstar.Metadata.dll
DESIGNER_BRIDGE_TIMEOUT=180
```

`CHAT_USERNAME` 是工作台会话标识，不是 Designer 登录账号。自然语言聊天需要配置模型接口；直接调用文件工具和官方对象模型不需要 LLM。

### 环境要求

- Python 与项目依赖；Designer 桥接需要 Windows PowerShell 5.1、.NET Framework，以及匹配进程位数的 ACE OLEDB 驱动。
- 已安装并可访问的 Designer 组件。厂商 DLL 不包含在仓库内。
- 本地测试 MDB 放在 `DESIGNER_ROOT` 下。首次设计建立固定项目工作 MDB；后续设计持续更新同一文件。
- 数据库发布、服务器文件交接与 WCF 生成需要额外配置，见下文及 [.env.example](.env.example)。

当前集成依据已检查安装版本中的公开成员实现；厂商版本升级后需要重新验证兼容性。

## 工作台页面

| 菜单 | 地址 | 用途 |
|---|---|---|
| 设计助手 | [打开](http://127.0.0.1:8032/) | 自然语言设计、Excel 附件、预览确认、设计文件交接 |
| 设计记录 | [打开](http://127.0.0.1:8032/records) | 查看历史对话、继续原设计 |
| 设计进度 | [打开](http://127.0.0.1:8032/progress) | 查看完整流程、当前步骤及可执行操作 |
| 运行日志 | [打开](http://127.0.0.1:8032/logs) | 查看模型推理、工具调用和耗时 |

各页面保留一致的左侧导航，设计进度位于运行日志上方。新对话首次发送需求后自动提炼名称，最多 20 个字符，并同步到最近对话和设计记录。

会话按最后一次内容更新时间倒序排列，继续历史对话时会移到前面。侧栏最多显示最近 30 条，设计记录保留全部历史。首页默认打开最近更新的会话，没有历史时创建新会话；带 `session_id` 的链接优先打开指定会话。浏览历史与重启服务不会改变内容更新时间，旧会话首次迁移时用原文件修改时间补齐时间字段。

## 自然语言设计

例如：

> 在测试 MDB 中创建 ExProduct，继承 Product，新增 ExDescription，String 最多 200 字符，持久化，并生成变更包。

助手检查父对象、工作区和来源文件后调用设计工具。新建字符串字段可生成专用类型（例如 `ExDescription200`），避免修改共享类型。工作区未指定时，仅自动选择唯一描述为 Site 的活跃客户工作区；存在歧义时需明确指定。

主要能力：

| 能力 | 当前支持 |
|---|---|
| 查询元数据 | CDO、继承字段、类型、CLF、函数、事件、Query、表列、索引、映射、标签、工作区，以及原始 MDB 查询 |
| 对象与字段设计 | 新建继承 CDO、专用字段类型、持久化或非持久化字段，保存并回读验证 |
| 其他设计操作 | Query、CLF、事件绑定、函数顺序与参数、标签、表列、索引、映射及允许修改的属性 |
| 设计验证 | 官方差异导出、工作区编译、包完整性检查 |
| 数据库发布 | 目标检查、发布预检、校验备份、官方 Update DB、发布后核验及授权恢复 |
| WCF | 隔离生成、类型与字段核对、程序集下载；运行环境部署由人员完成 |

修改操作按工具要求提供原值和来源哈希。原生 XML Import、REST 生成及自动服务部署尚未作为完整工作流交付。具体参数与验收证据见 [能力说明](docs/designer_capabilities.md)，后续安排见 [开发计划](docs/development_plan.md)。

### 设计产物

```text
data/designer/artifacts/<id>/
  baseline.mdb   原始快照
  modified.mdb   已保存并回读的设计副本
  changes.xml    官方比较引擎输出的差异
  manifest.json  来源、工作区、文件哈希和执行证据
  report.md      设计与验证说明
  export/        XML、HTML 报告及官方日志
```

`saved_to_test_copy_and_exported` 表示设计副本已保存并导出。服务器正在打开的 MDB、业务数据库和运行服务分别通过后续步骤更新。

## Excel 导入：先预览，再确认

1. 在设计助手中点击 **添加 Excel**，选择 `.xlsx` 或 `.xls`，查看文件卡片和工作表预览。
2. 输入设计要求。文字中的明确修正优先于表格，例如“按附件创建对象，ExDescription 长度改为 300”。
3. 助手读取和核对设计信息，在实际创建前展示 **Excel 设计预览**，列出本批对象、字段、类型、长度、持久化、列表属性及来源工作区。
4. 人员核对后在聊天中输入 **确定** 或 **确认**，后端才执行本批设计；也支持“确认导入”等明确指令。
5. 输入 **取消** 可停止本批操作。需要调整时，取消后补充需求并重新预览。

确认绑定本批具体操作参数。首次需求或 Excel 单元格中的“确认”不算人员确认；“继续”“好的”以及带附加要求的确认句不会触发导入。后续新增批次需要再次预览确认。刷新页面或重启服务后可恢复待确认状态，附件变化会阻止执行并要求重新上传。

### 推荐表格

每行一个字段，第一行使用中文或英文表头。同一对象的对象名、父对象可逐行填写，多个对象可分工作表组织。

| 对象名 | 父对象 | 字段名 | 数据类型 | 最大长度 | 是否持久化 | 是否列表 | 描述 |
|---|---|---|---|---|---|---|---|
| ExProduct | Product | ExDescription | String | 200 | 是 | 否 | 扩展描述 |
| ExProduct | Product | ExEnabled | Boolean | | 否 | 否 | 启用标记 |

上传仅解析文件；未填写文字时，默认请求读取附件并说明设计方案。来源、引用与名称冲突通过真实工具核对。超过工具单次操作限制的设计需要分批处理。

### 附件限制

- 每次最多 3 个附件，每个最多 10 MB、10 张工作表；每张表范围最多 2000 行、64 列。
- 非空单元格、文本和总上下文另有限制，超限报错，不静默截断。
- 文件卡片只展示前 6 行；模型接收完整解析数据，执行前的设计预览展示本批全部操作。
- 隐藏工作表会标注。`.xlsx` 中公式及错误值需先修正或粘贴为值；`.xls` 读取已保存结果，不重新计算。
- 不执行宏，不支持 `.xlsm`、加密或损坏文件。

附件绑定上传用户及对话，保存于 `data/excel_attachments/<id>`。发送和确认执行时校验原文件哈希。历史记录保留文字、附件与已确认或已取消的设计预览；切换或新建对话会清空未发送附件。

## 在 Designer 中查看设计

设计结果提供 **在 Designer 中查看** 与 **下载设计 MDB**。查看操作核对当前设计，将工作 MDB 与站点文件放在服务器固定目录 `C:\DesignerWorkspace\<项目ID>\`，后续批次更新同一路径。旧设计包继续保留原有查看方式。

需要配置服务器共享、Windows 凭据、Designer 路径及站点文件：

| 配置 | 用途 |
|---|---|
| `DESIGNER_DB_SERVER` | 目标服务器 |
| `DESIGNER_SERVER_SHARE` | 与服务器对应的 C 共享，当前实现使用 `\\服务器\C` |
| `DESIGNER_WINDOWS_USER` / `DESIGNER_WINDOWS_PASSWORD` | 访问服务器文件所需的 Windows 凭据 |
| `DESIGNER_UI_EXE` | 服务器 Designer 可执行文件路径 |
| `DESIGNER_SERVER_SITEINFO` | 站点 SiteInfo MDB 路径 |

具有配置写权限时，工具备份 Designer 配置并设置下次启动使用的 MDB。保存并关闭当前 Designer，再重新打开；无配置写权限时，按页面返回路径手动打开。进入对应客户工作区（例如 Site / 200），搜索目标对象核对。

也可输入“把这份设计准备好，让我在 Designer 中查看”。多批设计应交接包含全部内容的最终 MDB。文件准备成功仅表示交接完成，仍需在 Designer 中查看实际内容。

## 我的设计进度

完整流程与页面一致：

**提交需求 → 识别设计 → 检查现有定义 → 生成设计 → 核对结果 → 检查设计 → Compile → Update Database → 生成 WCF → 部署 WCF → 验证结果**

选择自己的设计对话后，页面显示已完成步骤、当前步骤和可用操作。耗时任务在后台执行，展示已耗时及最近更新时间。

| 步骤 | 页面操作 |
|---|---|
| 检查设计 | 核对设计包、准备 Designer 文件、同步服务器文件状态、记录人工审核 |
| Compile | 编译设计副本，或记录在 Designer 中已完成的编译 |
| Update Database | 检查发布目标和变更、确认执行官方 Update DB，测试环境默认无需备份；也可记录手动更新 |
| 生成 WCF | 编译当前副本并生成程序集，核对目标类型和新字段，下载已核验 DLL |
| 部署 WCF | 人员在运行环境部署后，点击手动部署确认；无需 WCF 时可按页面条件跳过 |
| 验证结果 | 执行已支持的发布后核验并记录完成状态 |

页面根据工具回执与人工确认展示进度，人工完成步骤标注“用户确认”。数据库更新、WCF 生成和部署分别记录。WCF 下载包不包含可能携带连接信息的生成配置，部署时使用运行环境配置。

设计进度只显示已开始生成设计的任务，普通聊天、对象查询和环境检查不会创建进度。前期需求与检查只取本次设计及其来源批次，后续闲聊不会改变已有设计步骤。没有设计任务时显示空状态，新设计开始后会自动出现。

当前对话以最新设计包为准，只累计该对话中作为来源的设计包。其他用户和独立分支不会自动合并；服务器文件发生变化时，后续操作会要求重新核对。此页面用于跟踪本人的设计流程，不是整个 Designer 环境所有改动的全局看板。

## 数据库发布与 WCF 配置

仅在本地 `.env` 配置数据库和 Windows 凭据，不提交到仓库。

| 配置 | 用途 |
|---|---|
| `DESIGNER_DB_SERVER/NAME/USER/PASSWORD` | 数据库连接；备份与恢复需要对应权限 |
| `DESIGNER_UPDATE_DB_USER/PASSWORD` | 官方 Update DB 账号，默认 schema 必须与应用 schema 一致 |
| `DESIGNER_TEST_TARGET_CONFIRMED=true` | 明确允许对已确认的测试目标发布 |
| `DESIGNER_REQUIRE_DATABASE_BACKUP` | 默认 `false`，测试发布无需备份；设为 `true` 时要求校验数据库备份 |
| `DESIGNER_SERVER_IMPORT_EXE` | 服务器官方 ImportMetaData 程序路径 |
| `DESIGNER_WCF_DIRECTORY` | WCF 组件目录 |
| `DESIGNER_SERVER_WCF_GENERATOR` | 服务器 WCF 生成程序路径 |
| `DESIGNER_WCF_ADDRESS` | WCF 生成所需地址 |
| `DESIGNER_PUBLICATION_TIMEOUT` | 发布超时，默认 900 秒 |
| `DESIGNER_SERVICE_TIMEOUT` | 服务生成超时，默认 1800 秒 |

服务器 WCF 生成还需要已认证的共享和执行临时 SQL Agent 作业所需权限。

测试环境发布顺序为：**检查设计包 → 发布预检 → 确认 Update Database → 发布后核验**，默认不要求数据库备份，也不显示备份按钮。发布确认绑定检查结果和设计清单；执行前再次核对目标设计指纹，变化时停止更新。需要强制备份时设置 `DESIGNER_REQUIRE_DATABASE_BACKUP=true`，此时增加备份步骤并核对备份哈希，校验凭证有效期为一小时。

Update DB 更新设计与存储结构，不等同于部署运行服务。失败可能留下部分更新；`restore_designer_test_database` 使用校验后的备份凭证与哈希恢复明确授权的测试目标，会覆盖备份之后的变更。

## MCP 接入

HTTP MCP 默认关闭，启用时在 `.env` 设置：

```ini
ENABLE_MCP_HTTP=True
MCP_HTTP_PATH=/mcp
MCP_API_KEY=replace-with-a-long-random-secret
MCP_ALLOWED_HOSTS=localhost:*,127.0.0.1:*,[::1]:*
```

重启后端点为 `http://127.0.0.1:8032/mcp/`，使用 `Authorization: Bearer <MCP_API_KEY>`。浏览器 `/chat` SSE 与 MCP 协议端点分开。上文 Excel 预览确认由浏览器对话的 LangGraph 流程执行，直接调用 MCP 工具的客户端需自行管理其操作确认。

## 数据目录

| 路径 | 内容 |
|---|---|
| `data/sessions/` | 对话、名称与历史消息 |
| `data/excel_attachments/` | 原始 Excel 与解析快照 |
| `data/designer/artifacts/` | 设计副本、差异文件、清单与报告 |
| `data/langgraph_checkpoints.sqlite` | 工作流检查点与待确认状态 |
| `data/designer_progress.sqlite` | 设计进度与操作回执 |
| `data/agent_experience.sqlite` | 助手经验记录 |

设计根目录、检查点与进度数据库可通过 `DESIGNER_ROOT`、`LANGGRAPH_CHECKPOINT_DB`、`DESIGNER_PROGRESS_DB` 配置。保留对话、附件和检查点，才能继续已有的 Excel 确认流程。

## 开发与测试

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
```

测试覆盖附件解析与归属、预览确认和取消、检查点恢复、设计工具及进度流程。真实厂商集成测试需要额外配置本地测试快照和已安装 DLL：

```powershell
$env:DESIGNER_TEST_MDB='data/designer/server_snapshot/InSite.mdb'
$env:DESIGNER_TEST_VENDOR='1'
.\.venv\Scripts\python.exe -m pytest tests -q
```

真实测试检查原始输入哈希，并验证 CDO、字段类型、持久化列及官方 XML。`examples/designer` 中合成 XML 演示不作为厂商导入样例。

## 常见问题

- **仍访问旧端口**：核对 `.env` 和启动进程的 `SERVER_PORT=8032`，重启后打开 `http://127.0.0.1:8032/`。
- **Excel 上传后没有创建对象**：先发送设计要求，待生成设计预览，再输入“确定”或“确认”。
- **Designer 看不到新对象**：确认打开的是交接路径中的最终 MDB，选择正确工作区；设置下次启动文件后需重新打开 Designer。
- **页面提示文件发生变化**：重新同步并核对设计来源；附件变化则取消待确认操作并重新上传。
- **数据库已更新但服务未生效**：继续完成所需 WCF 生成、运行环境部署和结果验证。

## 固定工作 MDB 与版本备份

每个来源项目的固定工作文件为 `data/designer/workspaces/<项目ID>/InSite.mdb`（实际根目录取 `DESIGNER_ROOT`）。生成工具先完成厂商保存、回读和差异导出，成功后更新此文件；失败不会覆盖工作 MDB。连续聊天或新对话使用该文件及最新哈希，旧来源路径在项目建立后也会解析到当前工作文件。

自动 Update Database 成功，或在进度页手动确认已更新数据库后，记录发布版本。下一轮设计首次实际修改前，备份该发布版本，再继续更新原工作文件；同一轮继续修改不重复备份。只保留最新 10 个备份，备份校验失败时停止修改，超过 10 个时删除最旧的完整备份。发布失败不会创建发布标记。备份包含 MDB，以及准备 Designer / 发布时保存的配套 SiteInfo；缺少 SiteInfo 时只备份 MDB。

设计结果和进度页显示固定路径及备份列表，可下载备份。已准备 Designer 文件的项目，后续设计自动更新同一服务器路径；服务器不可写时返回同步错误，需重新准备文件，不宣称服务器已更新。服务器备份随文件交接同步，亦只保留 10 个版本。历史包只能下载对应快照，不能覆盖最新工作文件；文件变化后必须重新核对、编译和发布。每次设计的内部核验快照、差异和清单保留于 artifacts，不属于 10 个发布备份。多人文件合并和兼容由 Opcenter 处理。
