# CreatedAtType 重名后的诊断中断

## 已核实原因

会话 `c2263c97-398b-4783-bea0-208ca4ff28d2` 的第一批 CustomerComplaint 已保存。第二批 CustInventoryTransfer 的 CreatedAt 没有明确 field_type，生成器按字段名生成 CreatedAtType，触发全局名称重复的预检拒绝，第二批未保存。

真实工作 MDB 的 Designer SDK 读取结果：CreatedAtType 的 FieldDefID=671088650，CPPDataType=TimeStamp，WorkspaceCode=200，PrecisionValue=0，Scale=0。官方 GetWhereUsed(false) 返回 AssetTrace_6381.CreatedAt 引用它。该类型不是第一批刚创建的。

预检拒绝后，模型发出了两个 list_designer_entities 查询，但策略节点把此前累计 54 条创建尝试套用到纯只读查询，产生无预览的 approval_required 中断。查询尚未执行，并非模型或 Designer 查询超时。

## 修复

- 纯只读工具调用完成参数检查后，跳过累计写入审批；读写混合调用仍需审批。
- 只读查询不能把此前被拒绝的 proposed_plan 标记为 validated_plan。
- 保留累计尝试计数，避免低估可能部分保存的失败写入。
- 修复 SDK GetWhereUsed 返回 DataTable 时的桥接转换，保留官方列名、总数及截断标记。

补充回归覆盖：高累计次数的纯读取、混合读写审批、旧中断检查点直接恢复只读查询，以及安装版 SDK 的 DataTable 引用查询。

验证：常规测试 360 passed、17 skipped；新增真实 SDK where-used 测试 1 passed。

## 会话恢复与证据

恢复脚本仅允许原来的两个 list_designer_entities 调用，禁用模型自由续写，完成后追加诊断结果。不生成或执行新的设计，不执行 Update Database。恢复前备份会话、元数据和 SQLite 检查点。

证据目录：`data/designer/artifacts/ee0a3b5c3ada422fb7dee99e7f1b7a62`，包括 `CreatedAtType_where_used.json`、`recover_queries.py` 和恢复回执 `recovery_result.json`。

读取前后工作 MDB SHA256 应保持 `0969a80ab3cb6ccf0e7984ec2eed881a7c4202cd3697a2a7a0fed1ac3d960b40`。

后续设计应在新预览里明确选择复用 CreatedAtType，并核对属性兼容；不要再次创建同名类型。此诊断不代表第二批已经创建或数据库发布完成。
