# 主数据字段的维护配套

新增持久化命名或版本化主数据字段时，如果其 MaintenanceCDO 的
ObjectToChange 指向该对象，后端默认向其实际 ObjectChanges 对象补齐
同名、同类型、同列表形态的可读写维护字段。预检和实际保存使用同一规则。
这不依赖模型是否填写 `portal_editable=true`，也不猜测 Changes 对象名称。

预检结果的 `maintenance_fields` 会出现在创建确认预览中。维护字段不创建
第二个 SQL 列；保存后重新加载核对字段类型、列表形态及读写权限。
安全计数为可能生成的维护字段预留一次创建，预览显示实际创建内容。

- `portal_editable=false` 明确选择仅存储字段，不配套维护字段，不能据此承诺
  Portal 可编辑；不要仅因用户未提 Portal 就主动设置 false。
- `portal_editable=true` 强制要求匹配的维护关系，缺失时阻断预检。
- 未设置时，非持久化字段、非主数据对象不自动配套。
- 派生对象继承的维护服务若仍指向父对象，不修改父对象的维护字段。
- 新建对象明确生成自己的维护服务后，同批次字段可自动配套到该服务。
- 同名维护字段的类型、列表形态或读写权限冲突时阻断，不覆盖已有定义。

2026-10-20 的 ExtName 问题来自旧的显式开关设计：创建 Product.ExtName
时未携带开关，因此 ProductChanges 缺少字段，WCF 前置校验按预期阻断。
当前修复保留用户最终确认的 `[String]`、40 字符及已有 Product 数据列，
只补齐 ProductChanges.ExtName。原发布版本先备份，随后通过官方 API
保存、回读、导出、编译及 Update Database，保留各阶段真实回执。

本次修复清单：
`data/designer/artifacts/92d06d01701847b6abe73900162ebdb0/manifest.json`。

官方 Update Database 已完成，维护字段核验通过。发布回执：
`data/designer/artifacts/caaa72038d084be7a04a4ce86eb80bcd/publish_result.json`。
全量 WCF 93.66 秒完成，798 个服务、2592 个数据契约；客户端和服务端均验证
ExtName 与 ExProductDescription 存在。当前会话第 9 步为已完成，原失败回执保留。
生成回执：
`data/designer/artifacts/c8dfbe8f703b4c298c26fdfa575be4c7/generation_result.json`。

回归测试 483 项通过；另有 11 项安装版本 SDK 测试通过，覆盖默认补齐、
显式选择、保存回读、维护关系、父对象隔离及冲突阻断。测试均使用隔离副本。

WCF 仍检查完整生成结果中客户端和服务端的字段，没有放宽缺字段验收。
WCF 成功不代表 Portal 已部署或页面读写已经验收。
