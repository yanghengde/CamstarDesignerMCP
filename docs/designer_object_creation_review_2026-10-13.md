# 对象类别选择与基础字段冲突 Review

此文保留优化前的 Review 发现。随后已落实入口保护、官方只读预检、完整基础字段预览和类别修改草稿；当前行为及验收见 [对象创建优化记录](designer_creation_optimization_2026-10-13.md)。

本轮为只读代码、安装 DLL 和当前工作 MDB 检查，以及不调用 Designer 的参数校验演练；未创建或修改业务对象。

## 当前产品类别与工具合同

现场组件 Camstar.Metadata 10.6.0.0。当前工作 MDB 的 CDOUsage 有 78 条定义，包含默认项；77 条非默认用途组合由 CDOUsageMaskDefinition 的 25 个标志表达。已有对象实际使用了 48 种用途值。这些是产品内部用途组合，不能直接当成 78 个面向用户的创建入口。

当前创建接口只有 create_cdo 一种动作。桥接调用公开 CDODefinition.CreateNewCDO，并传入父对象的 CDOUsageMaskId 与 StorageCategoryId；类别主要由 parent 决定。create_table、create_revision_base、create_maintenance 为另行配置的布尔参数。便捷 generate_designer_cdo_package 只接收父对象和字段，没有显式类别及这几个建模选项；存在建表要求时，运行时已有保护要求改用通用 create_cdo。

建议先提供六个常用业务入口，其余高级类别从实际产品目录查询，不能随意组合用途位：

| 建议入口 | 代表父对象 | 需要说明的语义 |
|---|---|---|
| 命名主数据 | NamedDataObject | 已有 Name、Description 等，适合命名记录 |
| 版本化主数据 | RevisionedObject 或已确定的业务父对象 | 已有 Revision、Base、IsRevOfRcd、Status 等；明确版本、Base、Maintenance 结构 |
| 子实体／明细 | Subentity 或业务子实体 | 已有 Parent，说明归属与生命周期 |
| 业务服务 | Service 的合适业务子类 | 继承服务字段和事务语义，不能当作普通业务表 |
| 查询服务 | Inquiry 的合适业务子类 | 只读查询用途 |
| 历史记录 | HistoryMainline／HistoryDetails 的合适业务子类 | 历史主线与明细结构，需要明确父记录关系 |

这些入口是拟定的用户体验分组，不是新增厂商类别枚举。用户已经明确指定父对象或类别时直接展示已选结果；只有语义未确定或存在歧义时让用户选择，不要求用户每次重复选择。

通过现有官方模型查询得到的有效字段数量：NamedDataObject 17、RevisionedObject 23、Subentity 9、Service 93、HistoryMainline 101、Product 160。Product 的父对象是 RevisionedObject，但用途为 RevisionedDataWIPMsg，不能认为它与简单 RevisionedObject 相同。

## Review 发现

### P1：通用 patch 可修改对象用途和存储类别，缺少专用流程约束

位置：designer/VendorBridge.cs 的 Protected、Editable 和 Apply(patch)，约 134–196 行。

本机反射确认 CDODefinition.CDOUsageMask 和 StorageCategoryName 均有可写 string setter。当前 Editable 只排除 Protected、ID、集合等属性，这两个名称未被保护，所以 schema 会显示可编辑且通用 patch 会调用 setter。change_parent 却明确拒绝跨 CDOUsageMaskId 的父类别迁移，change_storage_category 也有原值和客户工作区检查。通用属性写入没有执行相同的语义校验。

建议将 CDOUsageMask 和 StorageCategoryName 加入通用 patch 的保护名单。用途由受控的创建合同及官方工厂确定；已有对象的跨用途迁移不能由字符串属性修改代替 Designer 的完整迁移与验收。存储类别只使用已有 change_storage_category 专用操作。这里已确认的是入口允许属性修改，未执行实际跨类别迁移来推断其保存后果。

### P2：继承字段冲突未在预览前检查，用户确认后才遇到执行错误

位置：agent/excel_preview.py:35、designer/design_contract.py:6、designer/vendor.py:121；最后防线在 designer/VendorBridge.cs:172、309。

现有最后防线是正确的：field 查询读取 CDODefinition.AllFields，CheckName 使用 OrdinalIgnoreCase，包含继承字段；已有 Name 和新增 name 会冲突。执行循环完成后才 SaveAll，工作 MDB 只有成功保存、回读、导出后提交。

但 validate_calls／validate_operations 仅做参数和操作合同校验；excel_preview.build 只展示拟新增的对象与字段，不读取父对象基础字段、不区分继承或新建，不提供冲突列表。本次纯参数演练确认：NamedDataObject 下新增 Name、Description，以及同批新增 Code／code，均通过当前参数校验。不能据此认为后端会允许重复保存，但它们会进入需要后续执行才发现错误的计划。

建议增加只读创建预检，在生成用户可确认的预览前完成：

1. 根据当前工作 MDB、工作区和确定的父对象读取官方 AllFields、FieldDefinition、CDOUsageMaskId、StorageCategoryId。
2. 对同批对象、字段类型和同一 owner 的字段名称，以及全部继承字段，进行不区分大小写的重复检查；不能把不同对象的同名字段当成冲突。
3. 返回结构化结果，包含字段归属、实际类型、长度／精度、持久化、列表属性和建议处理。
4. 完全等价的字段提示复用，不能未经用户接受静默跳过；存在差异时提供改名或受控客户覆盖方案。InstanceID 等系统用途字段不能作为普通新增字段处理。
5. 用户改变类别、父对象或冲突处理后重新生成预览；确认绑定真实来源 SHA256、SDK 版本、父对象和最终操作参数。实际执行继续保留 CheckName，防止确认后的来源变化。

示例：请求 Description=String(500)，而父对象已提供 Description=String(255)，必须显示类型／长度差异，不能按同名自动复用；可以选择新增业务含义明确的 FaultDescription，或在明确允许时走官方客户字段覆盖／ChangeCDOFieldDefinition，并再次确认。

### P2：缺少统一的类别与派生结构预览

位置：designer/VendorBridge.cs:206–210、designer/design_contract.py:6、tools/designer_design.py:222、agent/excel_preview.py:62–74。

当前 API 能传入合法父对象名称，却未形成业务类别合同；三个创建选项独立默认 false。预览只记录 parent，部分选项作为通用 details 显示，不呈现“此对象包含多少基础字段、采用哪些系统字段、是否产生新表／Revision Base／Maint／Changes”。用户难以判断父对象是否符合本轮需求。

建议返回 ObjectCreationProfile，包含业务类别、真实父对象和用途／存储属性、继承字段、派生对象／表预期和需要用户确定的选项。类别到父对象和选项的解析必须使用实际 Metadata API 与厂商对象模型，不能由模型凭文字猜用途位或固定基础字段清单。新建服务、版本对象、历史对象应有相应结构验收。

### P2：预检不能只使用 get CDO 返回的前 100 个字段

位置：designer/VendorBridge.cs:513–514。

get 模式有意只展示前 100 个有效字段，并正确提供 total_fields、fields_truncated。本次 Product 有 160 个字段，HistoryMainline 有 101 个。如果新增的规划层把 get 结果当成完整字段列表，会漏掉后续字段的冲突；现有执行阶段 AllFields 不存在这个截断问题。

建议新预检在厂商桥内直接遍历完整 AllFields 后输出冲突和字段摘要。现有 list field 分页可用于用户浏览；所有页必须来自同一来源 SHA256，不能将被截断的列表作为不存在证明。

## 建议实施顺序与验收

优先保护用途与存储属性的通用 patch 入口，然后增加只读预检，再增强类别选择和预览展示。保持公开 Metadata API、CreateNewCDO、AllFields、AddFieldOverride／ChangeCDOFieldDefinition 和既有编译回读流程，不把重复字段问题交给 SQL 层修复。

建议回归：继承 Name／name；Description 长度相同／不同；同批 Code／code；不同对象允许同名字段；超过 100 个基础字段；用户修改父对象后旧预览失效；系统字段重复；Revision Base／Maint 衍生对象重名；通用 patch 不允许改变用途／存储类别；来源或工作区变化后阻止使用旧预检结果。对允许客户覆盖的场景验证父对象本身保持原值。
