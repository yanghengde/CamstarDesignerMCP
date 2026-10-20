# 项目 Review：设计、发布与 WCF 完整性

## 修复状态（2026-10-20）

已按本次审查的 5 类问题完成代码修复，正式回归位于
[test_project_review_regressions.py](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/tests/test_project_review_regressions.py)。

| 问题 | 已落地行为 |
|---|---|
| R1 字段生命周期 | 根据真实 MaintenanceCDO/ObjectChanges 关系联动改名、换型、删除、列表及客户端读写属性；预检和清单列出双方。直接编辑维护字段也校验反向关系。继承、非客户所有及在用引用等无法安全迁移的情况在保存前阻断；显式启用 Portal 编辑的非持久化字段遵循同一规则。Update Database 前再次检查编译 MDB 的字段类型、长度/精度、列表形态一致性。 |
| R2 WCF 验收 | 选择范围自动扩展到本轮全部对象，移除 20 个对象上限；校验客户端和服务端字段存在性、CLR 类型及列表形态，保留完整验收范围和最终编译契约。旧的单一布尔回执不能标记完成。 |
| R3 恢复与审批 | 恢复计入破坏性操作，并始终要求明确单次确认；提示目标库、备份凭证与哈希。授权绑定用户、会话、调用标识、参数及有效期，执行后消费；CLI 复用计数与确认规则。读取与隔离产物生成不会因历史写入计数反复要求确认。外部 MCP 客户端继续负责其用户授权。 |
| R4 Web 边界 | `.env`、默认监听和启动入口统一为 `127.0.0.1`；拒绝非回环管理监听、远程客户端、非本机 Host 及跨源管理请求。MCP 的令牌和 Host 校验独立保留。 |
| R5 提交恢复 | 读取、恢复和提交使用可重入线程锁与进程文件锁；遗留日志只在互斥状态下处理。MDB 命中提交哈希时前滚，仍为旧版本时清理未切换提交，未知哈希时保留日志并阻断。 |

真实 WCF 隔离回归已生成 **798 个服务**，最后一轮 **123.17 秒**，
主对象和维护对象共 6 个字符串、整数、列表字段契约通过；未部署运行目录。
详细产物位于 `tmp/review_fix_native/`，摘要为 `tmp/review_fix_wcf_final.json`。

完整常规回归：**506 passed，57 skipped**；跳过的项目包含默认关闭的厂商、
数据库和环境集成测试，不能视为通过。另单独启用本机 SDK，在隔离 MDB
运行本次问题的原生回归。正式测试命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
$env:DESIGNER_TEST_VENDOR='1'
$env:DESIGNER_TEST_MDB=(Resolve-Path data\designer\InSite.mdb).Path
.\.venv\Scripts\python.exe -m pytest tests/test_project_review_regressions.py -q
```

原生专项一轮 **32 passed（317.49 秒，包含 10 项真实 SDK 用例）**；追加的
“已引用字段类型禁止全局修改形态”SDK 用例也通过。已引用类型需要新建专用类型，
再通过 `change_field_type` 迁移目标字段及其维护字段，避免一次修改影响所有引用。
另补充预检维护字段客户端读取/更新权限的 2 个 SDK 用例，**2 passed（47.66 秒）**，
防止 Portal 不能选取或编辑字段。合计单独启用的 **13 项真实 SDK 检查全部通过**。

本次实现与测试未修改当前工作 MDB、Designer 交接 MDB 或业务数据库。
两份当前设计文件哈希仍为 `874f757c7dbf8658c00ce50346c83ff908d5f65b13af959f1b300a6fa8d6d58f`。
**现有 8032 服务进程未重启；新监听边界和已加载 Python 模块在下次启动时生效。**
真实 SQL 发布/恢复、Portal Studio 字段选择和 Portal 业务读写未作为本次修复的现场验收。

以下为修复前的审查记录与复现证据，位置行号对应审查基线。

审查基线：`abd6bf6b699ce1253ef88a98887aa63d54273d6c`，2026-10-20。

确认发现 **5 类缺陷，8 个复现用例**。其中 4 类建议按 P1 优先修复，
1 类按 P2 修复。现有测试通过不能覆盖这些边界。本次没有修改业务代码、
当前工作 MDB、Designer 交接文件、运行服务、业务数据库或已发布状态。
所有写入复现仅使用临时目录、内存桩或元数据副本。

## R1 · P1：配套维护字段只在新增时联动，后续变更会失配

位置：
[VendorBridge.cs:527](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/designer/VendorBridge.cs:527)、
[VendorBridge.cs:387](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/designer/VendorBridge.cs:387)、
[VendorBridge.cs:573](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/designer/VendorBridge.cs:573)、
[VendorBridge.cs:580](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/designer/VendorBridge.cs:580)。

**触发条件：** 对已经有配套维护字段的主数据字段，使用现有工具改名、
`change_field_type` 或删除。

安装版本 SDK 在隔离 MDB 中实测：

| 操作 | 数据对象 Product | 维护对象 ProductChanges |
| --- | --- | --- |
| 改名 | `ExReviewRenamed` | 仍为 `ExReviewRename` |
| 换型 | `[Integer]`，FieldDefID 1 | 仍为 `Name`，FieldDefID 139 |
| 删除 | `ExReviewDelete` 已不存在 | `ExReviewDelete` 仍存在 |

以上操作都完成官方保存和重新加载校验，说明保存校验目前只保证本次直接
修改的对象保存成功，没有保证配套关系完整。当前发布预检也没有维护契约
一致性门禁，因此问题可能到数据库更新后才在 WCF 阶段暴露。

**影响：** 改名、删除会再次产生字段存在状态不匹配。换型更隐蔽：
[WcfWorker.cs:43](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/designer/WcfWorker.cs:43)
以及生成后的字段检查主要核对名称和存在状态，无法识别数据字段与维护字段
同名异型的情况。本次没有把元数据差异声称为已经复现了实际 Portal 读写错误。

**建议修复：** 用同一个变更规划器处理新增、改名、换型、删除及关键属性
变更，解析实际 MaintenanceCDO/ObjectChanges 关系，将配套操作列入预览。
已有引用、客户覆盖和不兼容关系必须先阻断或要求明确迁移，不能盲目级联。
在 Update Database 前检查配套字段的类型、长度、列表形态、读写权限及
最终存在状态；生成后再对照 CLR 契约类型验收。

**验收要求：** 覆盖上述三种变更的保存、编译、契约检查，以及受影响业务的
运行回归；缺少维护服务、继承覆盖和共享字段类型分别测试。

## R2 · P1：只验收部分对象，可以把整轮 WCF 标记为通过

位置：
[progress_actions.py:176](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/designer/progress_actions.py:176)、
[progress_actions.py:210](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/designer/progress_actions.py:210)、
[progress_actions.py:235](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/designer/progress_actions.py:235)、
[progress.py:373](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/designer/progress.py:373)。

**触发条件：** 本轮修改多个对象，`verify_types` 只选择其中一部分。
`wcf_types` 允许合法子集，最终字段集合和验收又都跳过未选对象，之后仍设置
`fields_verified=true` 并允许第 9 步完成。

**复现：** Product.GoodField 已检出，同时本轮还要求
OtherChangedObject.MissingField；只选择 Product 验收，函数仍返回成功，
未拒绝漏验的 OtherChangedObject 字段。

**影响：** 全量生成的服务数量不等于本轮所有改动都已核验。新的对象或字段
未进入可达契约图时，可以被这个选择范围遗漏。默认验收对象还有 20 个上限，
配套对象加入后较大的批次更容易遇到这一限制。

**建议修复：** 必验范围由本轮最终变更自动计算，不能通过手动缩小集合来
标记整轮通过。允许部分诊断时，单独返回部分验收状态并列出漏验项；完整
发布验收要求覆盖全部必验对象，超限时分批校验并合并结果。

## R3 · P1：写入确认没有覆盖全部危险操作，也没有始终绑定具体计划

位置：
[safety.py:49](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/agent/safety.py:49)、
[safety.py:112](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/agent/safety.py:112)、
[langgraph_runtime.py:418](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/agent/langgraph_runtime.py:418)、
[designer_design.py:121](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/tools/designer_design.py:121)。

确认了两个独立路径：

1. `restore_designer_test_database` 不匹配 create/update/delete 前缀，也不在
   专门计数表内。实测计数为创建 0、修改 0、删除 0，`requires_approval=false`。
   但它会调用
   [publication.py:289](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/designer/publication.py:289)
   的断开连接操作，以及随后的 `RESTORE DATABASE ... WITH REPLACE`。
   当前“用户必须明确授权回退”主要依赖工具描述和提示词，备份哈希、目标确认
   只能证明备份和环境匹配，不能证明本次恢复已获授权。
2. 普通审批一旦留下 `approval_granted=true`，没有 Excel 或运行中修订预览时，
   `_policy_node` 会在重新计算阈值之前返回。实测原本批准创建的状态下，新提交
   删除操作，即使删除阈值为 0，也没有触发新的审批。

**影响：** 模型输出不准确或尝试自行修复错误时，可能执行未被本次具体确认
覆盖的破坏性操作。本次复现没有真正执行数据库恢复或删除业务定义。

**建议修复：** 用显式工具效果表替代名称前缀识别；数据库恢复等关键动作
必须有独立的、一次性的确认门禁。所有授权绑定用户、会话、目标库、来源
版本、操作参数摘要和有效期；新参数或新类别不能沿用旧的布尔授权。
CLI 也直接分发工具，应复用同一执行门禁。HTTP MCP 客户端自行管理确认是
README 已说明的边界，不将这一已说明的接口职责单独列为新缺陷。

## R4 · P1（取决于网络可达性）：Web 管理接口没有认证，却监听全部网卡

位置：
[main.py:20](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/main.py:20)、
[routes.py:44](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/web/routes.py:44)、
[routes.py:205](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/web/routes.py:205)。

`CHAT_USERNAME` 是客户端传入的标识，`/config` 会返回该值，不能作为身份
认证。浏览器路由没有 MCP 的 Bearer 保护，而启动入口绑定 `0.0.0.0`。

**复现：** 隔离 TestClient 不带任何凭据即可读取用户名并创建会话，返回
HTTP 200。后续设计与进度管理接口沿用同样的身份检查。

**影响：** 如果 8032 可从局域网或其他网络访问，访问者可以以配置用户名
读取会话、发起设计，并按进度接口调用管理操作。没有实际测试防火墙、
公网或局域网可达性，不能据此断言当前机器已被外部访问。

**建议修复：** 当前本地单用户部署默认只监听 `127.0.0.1`；如果要开放网络
访问，给全部管理路由加入真实认证和写入授权。MCP 端点已有的令牌保护
不能代替浏览器路由保护。本项复现测试仅证明 API 无认证，监听行为依据
启动代码审查；改为仅回环监听时还需要单独验证网络边界。

## R5 · P2：读取项目状态会提前消费正在提交的恢复日志

位置：
[working.py:62](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/designer/working.py:62)、
[working.py:180](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/designer/working.py:180)。

`commit` 先写 `pending.json`，再替换 MDB，最后写状态。`load` 没有取得同一
项目锁，发现 pending 后不论当前 MDB 是否已经切换到新版本，都会删除它。

**复现：** 两线程模拟界面读取与提交交错。读取发生在 pending 写入之后、
MDB 替换之前，将 pending 删除；随后模拟提交进程在 MDB 替换后、状态写入前
异常退出。再次加载，状态仍是旧哈希，实际 MDB 已是新哈希，恢复日志已不存在。

**影响：** 异常退出后可能留下无法自动恢复的项目状态，后续来源校验会阻断。
本次测试模拟了精确交错和异常窗口，不代表当前工作文件已经损坏。

**建议修复：** 项目读取、恢复、提交共享同一锁；只有确认没有活跃提交时
才处理遗留日志。校验不匹配时保留证据并阻断，不能无条件消费 pending。
CLI 与 Web 并用时，还应采用跨进程单写入机制或文件锁。

## 审查与测试范围

已检查入口及配置、Web/MCP 路由、聊天确认与工具分发、附件与证据处理、
元数据预检和写入、工作文件及备份、Designer 交接、数据库发布和核验、
WCF 工作进程及验收、进度与后台回执、页面主要渲染路径。

- 现有套件：**483 passed，44 skipped**。
- 新增审查复现：8 个断言均暴露当前缺陷，其中改名、换型、删除使用安装
  版本的 SDK 在隔离副本保存并回读；其余使用桩、隔离 Web 客户端或临时文件。
- 复现断言使用 `xfail(strict=True)` 表示当前已知缺陷，不能称为功能通过。
- 未执行真实 SQL 发布、备份恢复、WCF 全量重新生成、Portal 页面读写或真实
  LLM 调用；未停止或重启服务。跳过的集成测试不作为已验收能力。
- 未开展全量第三方依赖漏洞库扫描、厂商二进制审计或外部网络渗透测试。

可复现脚本：
[test_findings.py](C:/Codes/CamstarDesignMCP/CamstarDesignerMCP/tmp/review_20261020/test_findings.py)。
原始观察值位于 `tmp/review_20261020/evidence/`，该目录属于忽略的本地审查产物。

```powershell
$env:PYTHONPATH=$PWD.Path
$env:REVIEW_SEED_MDB=(Join-Path $PWD 'data/designer/InSite.mdb')
.\.venv\Scripts\python.exe -m pytest tmp/review_20261020/test_findings.py -q -rx
```

## 修复顺序与验收原则

1. 先封闭恢复/审批边界，并确认管理接口的网络边界。
2. 用统一字段变更规划器覆盖完整生命周期，把维护契约检查前移到数据库更新前。
3. 将 WCF 的必验范围从用户选择改成由最终设计自动确定，补充契约类型验收。
4. 修复项目提交/恢复的并发规则，测试异常退出和跨进程访问。

把本次复现转成正式回归测试后再修实现。最终以“预览参数 → 保存回读 →
数据库元数据/物理结构 → WCF 契约 → Portal 运行”的同一版本证据闭环验收，
分别记录各阶段成功，避免一个总完成标记掩盖漏验。
