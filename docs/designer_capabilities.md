# Designer 对象模型工具

## 工具入口

| 工具 | 用途 |
|---|---|
| get_designer_environment / get_designer_capabilities | 文件、驱动、后端及真实能力状态 |
| inspect_designer_installation | 授权共享中Designer界面、MDB路径、原生导入程序和组件版本，只读 |
| get_designer_catalog | 官方定义数量、全部工作区 |
| list_designer_entities / get_designer_entity | 有效定义与字段、CLF、查询文本和参数 |
| get_designer_entity_schema | 实际属性、类型、枚举、可编辑标记 |
| analyze_designer_where_used | 厂商引用追踪；不支持的类型明确拒绝 |
| generate_designer_cdo_package | CDO 继承、专用类型、字段和存储映射 |
| generate_designer_design_package | 批量设计、原值检查、保存、回读、官方导出 |
| export_designer_vendor_diff | 官方比较引擎生成 XML；项目渲染 HTML |
| compile_designer_mdb | 官方工作区编译及字段继承归一化 |
| check_designer_package | 哈希、来源漂移；不代替运行验证 |
| inspect_designer_database | 发布目标实际表列，只读 |
| prepare_designer_publish_plan | 具体包与目标数据库的发布前检查 |
| backup_designer_test_database | COPY_ONLY/CHECKSUM及RESTORE VERIFYONLY，生成校验凭证 |
| publish_designer_test_database | 编译并调用官方Update DB，核对默认schema；保留审计和回退材料 |
| restore_designer_test_database | 明确授权下，恢复精确测试目标和校验备份 |
| verify_designer_published_design | 父对象、字段类型/持久化、物理列和可选String临时表边界测试 |
| generate_designer_wcf_package | 隔离调用官方WCF生成器；检查程序集及目标类型，适配仍在验收；部分服务包不能用于整体部署 |

原始 MDB、XML 和 MetadataExport CLI 工具仍可使用，其合同见 [历史 MVP](designer_mvp.md)。

## 设计操作

operations 为有序 JSON 列表，最多 50 项。全部在新副本执行；错误会生成 failure.json，失败副本不能发布。

| action | 必需参数 | 作用 |
|---|---|---|
| create_cdo | name,parent | 父 CDO 类别；可建表、Revision Base、Maintenance |
| create_field_type | name,data_type | String 需 max_length；数值可 precision/scale |
| add_field | name,owner,field_type | 可 persistent/is_list；厂商生成存储列 |
| patch | kind,name,changes,expected | schema 允许的简单属性，每个属性须提供原值 |
| change_field_type | owner,name,field_type,expected_field_type | 厂商字段类型变更方法 |
| create_clf | name,clf_type | 新建 CLF |
| copy_clf | name,template | 复制 CLF、函数及参数 |
| add_clf_function | owner,function,sequence | 在客户 CLF 添加调用 |
| reorder_clf_functions | owner,function_ids,expected_function_ids | 完整排列及预期调用顺序检查 |
| set_clf_parameter | owner,call_id,parameter,value,expected_value | 函数调用表达式原值检查和更新 |
| bind_event | owner,event,clf,feature | 客户CDO或指定客户field的事件绑定；拒绝已有绑定 |
| create_query | name,query_type,db_type_id,text | 定义查询并同步参数 |
| add_query_text | owner,db_type_id,text | 其他数据库方言；已有文本使用 query_text patch |
| create_label | name,text,category_id | 标签及分类 |
| add_column | name,owner,sql_type_id | 表列，可 precision/scale |
| create_index | name,owner,columns | 已有列索引，可 is_unique |
| create_map | owner,target | 源/目标 CDO 映射，厂商生成名称 |
| add_field_map | map,source_cdo,source_field,target_cdo,target_field | 字段映射 |
| delete | kind,name | 当前工作区拥有且厂商判定未使用的定义 |

CDO 专用工具默认持久化；通用 add_field 默认非持久化，调用者应明确指定。工作区实际设置到 MDB，官方 XML Header 也携带 WorkspaceCode。旧模板工具仅在 manifest 记录工作区。

patch 禁止内部 ID、继承掩码、集合、归属和关系引用。安装组件部分引用 setter 不同步 ID，关系必须使用专用方法。枚举只允许已定义值。继承字段的属性修改先创建目标 CDO 的覆盖，不修改父对象字段。column/index 可用 owner 表定位；嵌套定义必须提供所属对象；歧义名称拒绝执行。

## 真实证据

2026-10-08，InSite 10.6.0.0 本地快照：解析到 3324 CDO、3726 字段类型、4289 CLF、4669 函数、1198 表、15099 列、1066 索引、1631 查询、835 映射。

ExProduct 测试：父对象 Product（ID1580），ExDescription 使用 ExDescription200，PrecisionValue=200；持久化列 Product.ExDescription，SQLTypeValue=12，PrecisionValue=200。保存、重新加载、ODBC 查询及官方导出均已核对，原快照哈希不变。

批量测试：CDO/字段/类型、SQLQuery、CLF 新建与复制、标签、列、映射、同批次原值修改均完成保存、回读和导出。追加 CLF 函数与 Oracle 查询文本也通过副本测试。工作区编译成功生成 compiled.mdb。

索引与字段映射续测：Product 上新增非唯一 ExMcpIndex，指向 ExMcpColumn；ExMcpProbeToProduct 映射新增 ExMcpText→Description。已直接查询 DBIndexDefinition、DBIndexEntries、CDOFieldMapDefinition 核对保存结果。SQLServer 查询文本的原值修改完成官方导出。

CamstarPRD 官方发布已成功：ExProduct继承Product；ExDescription使用ExDescription200，字段Precision=200；CamstarPRD_SCHEMA.Product.ExDescription为nvarchar(200)。基于真实列创建临时表的200/201边界测试通过，业务行未修改。保留备份和恢复证据，服务器原MDB未覆盖，运行服务尚未部署。

事件及CLF测试：CDO BeforeInitialize、客户字段BeforeUpdate已绑定客户CLF和Base Feature；重复函数调用可用id定位，调用参数和完整顺序的原值检查已回读核对。差异XML支持BaseCLFFunctions/NewCLFFunctions分区以及同函数不同Sequence。

## 待完成边界

1. 当前使用安装版本公开成员，未确认厂商独立 SDK 兼容性承诺。
2. 对象模型保存不等同于 Designer File > Import；原生 XML Import 往返尚未验证。
3. CIMS.DBUpdate.Processor.DoUpdate 已适配并实际发布。备份管理员和Update DB账号分离；后者默认schema及schema CONTROL必须匹配。组件需MTA、多线程及安装版本回归。
4. 批处理确认 WCFBuilderLauncher.exe -nogui -nosilverlight，会复制到多个运行目录；部署目录、服务回收及输出核对尚需验证。
5. CLF顺序、参数与事件绑定已有专用操作及元数据往返；表达式语义和运行行为尚需服务用例。
6. 200/201存储边界已验证；完整Camstar业务接口接受与拒绝仍需服务部署后验收。

按各步骤回执报告状态。发布预检无阻断仅表示可继续备份和官方数据库发布；不会证明服务已部署或完整业务回归通过。

WCF续测：ProductMaint隔离生成通过官方回执，329个数据契约、4个服务，已检查ProductChanges类型并复制回本地。全量生成曾产出109MB客户端及39MB服务程序集，但当次回执未通过全部检查；两种产物均没有部署。ExProduct尚未在部分服务的可达对象图中生成，启用IsWSExposedStd及AllowClientToUpdate的独立包也尚未发布。服务器执行采用私有工作进程配置、现有Web.config的Server连接和临时SQL Agent作业；取消由Windows Job Object结束子进程。该安装版本StateChanged事件参数不匹配，已移除事件订阅并保留根生成日志。

原生导入位置：Designer2/Designer.Net.UI.exe调用InSite Administration/Designer/ImportMetaData.exe；COM接口InSiteImport.Import.PromptToImportConsole2已实际调用。32位Jet副本读写已通过；首个创建包以Stop冲突策略运行返回false，日志记录ImportConflict与处理终止。副本可能部分更改，返回false或超时均不能发布。尚未注册为已验收导入能力。

后续设计以get_designer_environment返回的published_baseline为起点。官方Update DB会重写设计目录；工具检查最近发布的MDB来源哈希和目标元数据/物理结构指纹，防止旧快照覆盖已有设计。
