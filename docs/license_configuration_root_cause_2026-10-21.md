# Designer 自动发布后 License 与站点配置丢失的原因

检查日期：2026-10-21。检查范围：用户提供的共享对话、当前仓库、历史发布回执、实际安装的厂商 DLL，以及当前数据库配置的只读查询。

## 结论

已定位到自动 **Update Database** 路径。旧版 `designer/PublicationBridge.cs` 调用 `processor.DoUpdate(true,false)`，将第二个参数理解为“保留数据库中的运行配置”。当前安装版本的 `CIMS.DBUpdate.Processor` 实际仍会重建并替换站点配置表；`false` 只阻止将配置数据从 SiteInfo MDB 复制到新表。因此空的新表替换了原有配置表。

这会同时影响 License 地址和其他运行参数，与“重新 Load Configuration 后恢复”的现象吻合。共享对话中的故障检查已经记录 `InsiteSiteInfo`、`SystemInfo` 为空，并记录通过产品自带 Load Configuration 恢复。本次没有再次清空配置或重现破坏操作。

共享对话：[排查 Camstar License Server](https://chatgpt.com/s/cx_6ad8c3ae0c208191b95ef2adde05acf3)。

## 证据

### 1. 历史发布的实际日志

`data/designer/artifacts/9bc419282dec4f88ad1287b661f632dc/update.log`：

- 第 22 行：`Load Metadata: True, Load Configuration: False`。
- 第 86–91 行：创建 `DBDataSourceNames`、`InsiteSiteInfo`、`SiteInfoKeyList`、`SystemInfo` 的 SiteInfo 元数据表。
- 第 97、99 行：将原表改名为 `_T`，再把 `_C` 表切换为正式表。

同一目录的 `result.json` 仍返回成功和 `configuration_updated:false`。旧桥接代码把这个字段写死为 false，没有核验配置内容；发布后的设计核验也不包含这些配置表。因此“发布成功”不能证明配置得到保留。

`de5d56385c8b460caa0c3c4583675ec0` 等其他历史发布日志中也存在相同处理序列。

### 2. 当前安装 DLL 的处理逻辑

文件：`C:\Program Files (x86)\Camstar\InSite Administration\CIMS.DBUpdate.dll`。

SHA256：`3784CBA5C3CD3DE154738CA6BBFE7B93A10CC358129302D540FE6581EF10A10C`。

只读反编译结果保存在 `tmp/license-investigation/Processor.decompiled.cs`，未执行 DLL 中的数据库更新方法。

| 方法 | 与故障相关的行为 |
|---|---|
| `GetMetaTables` | `loadMetadata=true` 时读取 MDB 的 MetaAdmin 清单，其中含站点配置表。 |
| `CreateMetaTables` | 创建 SiteInfo 临时表的循环没有检查 `loadConfiguration`。 |
| `CopyMetaTablesData` | 复制 SiteInfo 数据的分支受 `loadConfiguration` 控制；false 时不复制数据。 |
| `ProcessUpdateMetadata` | 将旧表改名为 `_T`，再将 `_C` 临时表改为正式表。 |
| `PostProcessMetadata` | 删除 `_T` 旧表。 |

由此得到确定的故障链：

```text
DoUpdate(true, false)
  → 创建空的站点配置临时表
  → 跳过配置行复制
  → 空表替换现有配置表
  → License 地址及其他运行配置丢失
  → Load Configuration 将 SiteInfo 数据重新写入数据库后恢复
```

### 3. 当前数据库状态

本次通过 SELECT 查询已配置目标 `localhost / CamstarPRD`，schema 为 `CamstarPRD_SCHEMA`。检查时配置已有数据：

| 表 | 行数 |
|---|---:|
| DBDataSourceNames | 2 |
| InsiteSiteInfo | 43 |
| SiteInfoKeyList | 5 |
| SystemInfo | 47 |

`LicenseServer`、`LicenseFileType`、`LicenseServerType` 均非空。诊断输出只记录键名和数据长度，没有输出配置秘密。配置存在不等于本次已经验证授权接口或业务交易正常。

## 工作区中已有的保护及本次补充

开始本次检查时，工作区已经存在共享对话中添加的、尚未提交的保护代码：

1. 从目标数据库捕获四张配置表的当前内容。
2. 将内容写入本次发布专用的 `runtime_siteinfo.mdb` 副本。
3. 使用这个副本调用 `DoUpdate(true,true)`，确保重建的表重新填入当前配置。
4. 发布后按内容指纹逐表检查配置未变化；关键表为空、License 关键参数缺失或复制失真时拒绝继续。

不能仅把参数改成 true 并直接使用旧的项目 SiteInfo：那会把过期配置覆盖到数据库。保存当前数据库配置的步骤必须保留。

本次发现并复现了另一处阻塞：`publication_bridge.ps1` 未引用 `System.Xml`，新代码使用 DataSet 时会报 `IXmlSerializable` 所在程序集缺失，保护代码无法编译。本次只为生产桥接脚本补齐这个引用，保留其他已有修改，并新增 `tests/test_runtime_configuration.py`。

## 验证结果与边界

- 使用生产脚本的程序集引用列表，实际编译 `RuntimeConfiguration.cs` 和 `PublicationBridge.cs`，通过；未调用 `Run` 或 `DoUpdate`。
- 使用当前 SQL 配置及已有 `review_siteinfo.mdb` 快照，在独立临时副本上执行 StageSiteInfo；四表内容指纹与 SQL 快照一致，源 MDB 哈希及数据库配置保持不变。
- 配置缺表、空表、缺失 License 地址、空 License 类型、快照独立性，以及指纹对行列顺序和内容变化的判断均通过回归测试。
- 执行：`.venv\Scripts\python.exe -m pytest tests/test_runtime_configuration.py tests/test_designer_publication_components.py tests/test_designer_acceptance.py tests/test_designer_progress.py -q`，结果 **76 passed**。

临时副本核验回执：`tmp/license-investigation/54c7cdeef51e442aa696e0488e5fcca1/verification.json`。

本次未执行真实数据库发布、Load Configuration、服务重启或应用池回收。完整发布后的配置保持仍需在后续正常发布中检查 `runtime_configuration_preserved:true` 及四表核验结果；发布后检查用于发现变化，不是失败时自动回滚数据库的机制。
