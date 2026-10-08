# Designer MCP 第一阶段

服务名称为 `CamstarDesigner`。浏览器聊天和外部 MCP 共用工具。默认关闭复制过来的业务实例创建、Shopfloor 事务和 Query 工具；设置 `ENABLE_LEGACY_MODELING_TOOLS=True` 才注册这些兼容能力。

## 工具

所有输入文件均须位于 `DESIGNER_ROOT`。支持相对于该目录的路径和该目录内的绝对路径，不接受目录外文件或错误扩展名。产物保存到唯一的 `artifacts/<id>` 目录，不覆盖用户文件。

| 工具 | 关键参数 | 用途 |
|---|---|---|
| `get_designer_environment` | 无 | 文件清单、Access 驱动、导出程序配置及未实现能力 |
| `inspect_designer_mdb` | `mdb_file` | 发现真实表结构，不假设内部表名 |
| `read_designer_mdb_table` | `mdb_file, table, limit` | 读取已发现表的前 1～100 行，不接受自由 SQL |
| `list_designer_mdb_cdos` | `mdb_file, search, offset, limit` | 检索真实 CDO 原始工作区版本 |
| `get_designer_mdb_cdo` | `mdb_file, cdo_name, offset, limit` | 读取 CDO、字段、类型引用和工作区，不合并覆盖或继承 |
| `list_designer_cdos` | `xml_file, search, offset, limit` | 分页检索并返回来源 SHA256 |
| `get_designer_cdo` | `xml_file, cdo_name` | 原始对象定义及字段模板 |
| `validate_designer_xml` | `xml_file` | 文档结构、重复定义、预期值结构校验 |
| `compare_designer_xml` | `base_xml, modified_xml, limit` | 输入 XML 定义的新增、缺失和属性变化 |
| `generate_designer_field_package` | `xml_file, expected_sha256, target_cdo, template_cdo, template_field, field_name, workspace, description` | 新增字段草案包 |
| `check_designer_package` | `manifest_file` | 产物哈希、来源漂移检查 |
| `export_designer_metadata_diff` | `base_mdb, modified_mdb` | 官方工具在输入副本上生成差异 XML/HTML |

`get_mcp_server_status` 继续用于 MCP 协议和 SDK 诊断。

## 代码结构

- `designer/files.py`：文件访问范围、独立产物目录。
- `designer/mdb.py`：ODBC READONLY 连接、真实表名校验、基础值转换。
- `designer/catalog.py`：经过真实快照验证的 CDO/字段/类型读取；先检查表结构，返回工作区原始版本。
- `designer/metadata.py`：安全 XML 解析、定义索引、已有模板复制、结构校验。
- `designer/reserved_words.txt`：Designer 手册附录 A 的保留字文本提取；不是数据库名称规则的完整验证器。
- `tools/designer.py`：MCP 与自然语言 Agent 的公共工具入口。
- `designer/demo.py`：不使用 LLM 或 MES API 的合成数据演示。
- `tests/test_designer.py`：离线合同及适配器测试；官方程序使用替身。

## 字段生成合同

先查询 XML 并取得哈希，再以该哈希请求生成，防止读取和生成之间来源漂移。目标对象及模板字段必须出现在同一来源文件中。新字段名检查字符、保留字、已知本地字段和已知父对象字段；遇到继承循环拒绝生成，遇到缺失父对象记入未解决引用。

首版只复制非持久化、非列表简单字段模板。保留模板属性及类型引用，允许修改字段名和描述；不会为了支持用户需求凭空生成类型编码。存储表/列、映射、唯一标识和未验证复合引用均拒绝。模板缺少必需类型引用也拒绝。

模板来源可以是差异导出，不等同于完整目标元数据。即使已知字段没有冲突，仍须在测试 Designer 中检查实际目标和父对象。模板类型、长度、默认值、权限等需要逐项审核。对简单字段的属性名称只做保守检查；这不是厂商 XSD 或语义验证。

包中的 `baseline.xml` 是本次解析后的审计快照，排版和 CDATA 表示可能改变；`source_sha256` 记录原始输入文件哈希，`baseline_sha256` 记录审计快照哈希。`manifest.json` 包含计划、工作区、文件哈希和待执行验证步骤。

工作区不直接写入 XML。生成包不建立目标不存在的厂商级前置条件；`Action=Create` 是可读意图，导入器会自行判断创建或更新。Header 默认 `Stop` 不代表为新增字段建立了 ExpectedValue 检查。文件哈希也不能检测目标 MDB 的变化。

## XML 比较边界

比较仅覆盖输入文档中出现的命名定义与属性，不构建完整 MDB 快照。不应把 `missing` 解读为删除，也不应将零差异解读为两个 MDB 一致。忽略 Action 和 XML 排版，使用 NewValue 作为有效属性值。此能力用于首版 CDO/字段核对；不承担复杂 CLF 执行顺序或 SQL 语义等价判断。

XML 大小限制为 20 MiB，节点数限制为 100000，嵌套深度限制为 128，拒绝 DTD、实体及外部实体。仅支持无命名空间的 `InSiteMetaData`，Version 为 `1.0`。结构校验不包含引用完整性、保留标识 ID 分配、完整类型系统或 Siemens 官方 XSD。

## MDB 与官方导出边界

MDB 连接只读；表查询使用 ODBC 发现的真实表名，不接受自由 SQL。2026-10-08 从用户提供的服务器共享获取本地快照，确认 InSite.mdb 有 66 张表、SiteInfo.mdb 有 10 张表。已验证 CDODefinition、CDOFields、FieldDefinitions、CPPDataTypes 和 Workspace 的原始关联，读到 Container 的 240 条字段版本及 csi、10、15、60、200 活跃工作区。快照和探查结果保存在被 Git 忽略的 data/designer/server_snapshot，不提交 MDB 或环境凭据。

真实 ACE 驱动的 SQLColumns 在部分备注元数据上返回不可解码的 UTF-16。因此表结构读取改用 SELECT * FROM [table] WHERE 1=0，仅读取结果结构，不读取行或备注。catalog 适配器会检查必需列；版本不匹配时拒绝套用已知映射。返回原始工作区版本，不处理继承掩码和覆盖，不生成未经验证的 MDB 到 XML 转换。

导出程序由管理员配置固定绝对路径，使用参数数组执行，Windows 下隐藏进程窗口，不使用 shell。命令来自手册：

```text
MetadataExport -a -b base.mdb -m modified.mdb -o changes.xml -r report.html
```

每次复制两份输入到独立目录再执行，不把原始 MDB 交给导出进程。进程超时或退出码异常时保留日志用于排查。实际程序及真实 MDB 的兼容性尚未验证。

## 下一阶段验收

当前安装目录中已确认存在 CIMS.exe、CIMSClient.exe 和 Administration WebAPI 的 OECAdmin.WebApi.Server.dll。在已检查的 Camstar 和 Common Files/Camstar 目录内没有定位到独立 MetadataExport.exe。存在 WebAPI 组件不等于已经验证 Designer 编辑或导入 API；下一阶段需要检查其路由、身份验证、文件选择和实际操作语义。

2026-10-08 验证记录：项目专用 .venv 安装 requirements-dev.txt，pip check 无依赖冲突；完整测试 90 项通过，包括三项显式启用的真实 MDB 本地副本集成测试及前后文件哈希检查。FastAPI 启动、首页响应和 LangGraph 检查点初始化通过，默认注册 13 个工具。

1. 配置测试目录，放入真实 MDB 副本和一个已成功导入的简单客户字段 XML。
2. 读取真实表结构，建立经过验证的对象、字段、继承、类型和存储映射读取适配器。
3. 按真实模板生成非持久化字段草案，在指定客户工作区导入测试 MDB。
4. 用官方 MetadataExport 比较导入前后副本，核对预期字段与意外变更。
5. 在上述闭环通过后增加持久化字段映射、预期值更新、复杂定义和发布适配器。

没有暴露未经验证的自动导入或 Update DB 工具。没有将 Modeling、Shopfloor 或 Query REST 当作 Designer 编辑 API。

## 文档依据

本地手册：`D:/Deepseek/camstar/2510/OCEXCR_Designer_2504plus_R1.pdf`，Release 2504+ Rev.1。

- 14-2：Designer File > Import 的图形界面流程。
- 14-4、14-5：InSiteMetaData、Header/Import、Version=1.0 和 ExpectedValues 默认动作；已查看图示。
- 14-6～14-9：定义分组、Name/Action、CDO 和字段引用上下文；已查看 CDO 图示。
- 14-10、14-11：NewValue/ExpectedValue 及优先级；已查看简单文本图示。
- A-2～A-9：名称字符、数字首字符限制和保留字。
- C-4～C-5：MetadataExport 命令参数，-a 无界面导出全部差异，不支持单对象筛选。

合成示例中的 FieldDef、IsPersistent、IsList 仅用于检验模板复制机制，尚未得到真实字段导入样例确认，不应作为真实环境的标准 XML 模板。
