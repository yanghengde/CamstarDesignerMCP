# Status 类型修改被误判为对象改名

## 依据与根因

会话 `c2263c97-398b-4783-bea0-208ca4ff28d2` 的用户原话为 `Status 改成Integer类型`。原 `chat_context.update_task` 将所有 `X 改成 Y` 提升为对象名称约束，产生并持久化 `required_object_name= Integer`。后续“继续”沿用该错误状态，正确使用 `[Integer]` 的计划仍被策略节点拒绝。

原始会话 messages 的零基索引：

- 42：用户要求修改 Status 类型。
- 43／44：拟新建 CC_Status_Int／CIT_Status_Int，拒绝原因实际为“对象名称未采用用户最新修正：应为 Integer”，不是两个专用类型名称非法。
- 48／49：尝试新建名为 Integer 的字段类型，才触发 Designer 保留字校验。
- 47：真实 FieldDefinition 查询读到 `[Integer]`，FieldDefID=1、CPPDataType=Integer。
- 55／56：已经正确引用 `[Integer]`，仍被残留对象名约束拦截。
- 60／61：误用 `field_type=Integer`，再次先被错误对象名约束拦截。

`data_type=Integer`、已有类型引用 `field_type=[Integer]`、新定义名称是不同的参数语义。合法业务名称 CC_Status_Int 配合 data_type=Integer 可以通过命名校验；保留字规则只限制新定义名称，不禁止整数数据类型。

## 修复

- 只有明确标注对象／CDO，或命中当前方案实际 CDO 名称的修改，才形成对象改名约束。字段、数据类型、父对象、表名及工作区修改不再提升为对象名称。
- 为任务约束增加版本，旧检查点从用户需求和真实方案对象身份重建对象改名约束，清理 Integer 等错误值；保留有依据的真实对象改名。
- 普通新消息和挂起工作流的策略恢复均执行兼容修复；原需求及计划保留。
- 提示规则明确 CPPDataType 与 FieldDefinition 的区别，并要求按实际校验信息解释失败。

## 实际恢复

以原待确认方案为依据，只把两个 Status 的 field_type 改成 `[Integer]`，删除已无人引用的 CC_Status_20 与 CIT_Status_20 新建操作。其他字段、说明、父对象、表名、派生结构及工作区参数保留。

当前工作 MDB SHA256 与失败记录相同：`5cf1ffcff57f20113c0c7d374211a8db6174e93b38bdcfc16dad3cc70f29f0a2`。完整 36 项操作通过实际 SDK 只读预检，工作区解析为 200，两个 CDO、18 个新增字段，冲突为空。

证据目录：`data/designer/artifacts/13741e69f5af4a8992846f5e5b4d82fb/`。包含 corrected_arguments.json、creation_preflight.json、preview.json 和 recovery_result.json。

检查所有会话和发布任务均空闲后停止工作台；备份 SQLite 检查点、会话与元数据，再用正式策略节点恢复待确认预览。恢复进程禁用模型调用和实际工具执行节点，只允许生成审批中断；随后启动新代码服务。原对话可刷新查看并确认。未执行创建或 Update DB，恢复后 MDB 哈希再次核对不变。

整体回归：357 passed、17 skipped。新增用例覆盖原话复现、对象／字段／父对象／表名区分、真实对象改名保留、旧错误状态迁移、整数引用和保留字区别，以及修改后生成新预览且不写入。
