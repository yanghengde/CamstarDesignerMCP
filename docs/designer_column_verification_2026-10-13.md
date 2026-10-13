# 厂商物理列规则核验优化

本次发布的官方 Update DB 已完成。旧核验器直接比较差异 XML 的 Precision 与数据库字符串长度，错误拒绝了两个新表的 6 个实例 ID／引用列和 2 个 FilterTags 列。

## 实现

- 保留公开 Camstar.Metadata 对象模型的创建、继承、保存和 MetadataCompile 编译流程，不修改系统继承列以迎合核验器。
- 从官方编译 MDB 调用 CIMS.DBUpdate 的 Table.LoadFromMDB 和 Column.GetSQLDefinitionClause(SQLServer)，复用产品的默认精度、列用途、可空属性与 SQL 类型转换。
- 内部 API 使用隔离 .NET Framework 进程，只打开本地编译 MDB，不创建 Processor，不连接业务数据库。接受已实测组件哈希，遇到未知版本、类型或不完整返回时停止核验。
- 对照数据库的类型、字符／二进制长度、decimal 精度及小数位、可空属性。保留元数据值、关系和删除验收；元数据和物理列使用同一编译快照。可选临时表边界测试使用解析后的长度。
- 自动发布在 Update DB 前生成列定义；自动发布后核验与 Designer 手动更新确认共用实现。
- 回执分别记录数据库更新、设计核验、SiteState 问题和服务器回收状态；不把 DoUpdate 成功当成运行服务验证。

## 现场只读复核

目标：172.30.24.79 / CamstarPRD / CamstarPRD_SCHEMA。

清单：`data/designer/artifacts/31c471373cd14ccead1b0f6f9977b47f/manifest.json`。

编译快照：原发布目录 `f0fcc2d8fffc4776abd97fddf3b952f9/compile/compiled.mdb`。

重新核验结果：`data/designer/artifacts/7e458134738c4893909d5d621bef5d95/verification_result.json`，255 项全部通过，其中 151 项元数据、7 项对象、57 项字段和 40 项物理列。本次未执行 Update DB、服务回收、业务写入或发布基线登记。

SiteState 主键冲突与应用服务器回收连接失败仍需独立处理。重启正在运行的工作台服务后，后续核验入口才会加载新实现；不要因旧失败回执重复发布数据库。

## 进度恢复

当 SQL 已更新但旧核验规则导致第 8 步失败时，使用“核验已更新数据库并继续”。此操作不依赖用于重新发布的预检通过，不调用 Update DB；完整核验和目标指纹检查通过后，登记数据库发布基线、工作 MDB 发布状态及会话所属进度回执。第 8 步完成后禁用重复发布／重复登记按钮，页面停止显示旧预检阻断提示，第 9 步生成 WCF 恢复可操作。

现场会话 `11ac3805-dc0a-4693-a5d4-63b1a9fc7800` 已通过现有核验登记工作流恢复，进度从 7/11、当前第 8 步推进到 8/11、当前第 9 步。新核验回执为 `data/designer/artifacts/4901aa3496a04b90844990fd6ad7238c/verification_result.json`。数据库未重复执行更新，运行服务问题仍需独立处理。
