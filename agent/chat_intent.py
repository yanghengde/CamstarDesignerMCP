"""Conservative routing: questions and feedback never approve or replace a plan."""
import re


def pending_intent(text: str) -> str:
    value = (text or '').strip()
    if re.search(r'(?:回答|回复|复盘|解释|自责|道歉|语气|说话).*(?:简短|短一点|详细|少点|少一点|不要|别|先说)', value):
        return 'feedback'
    if re.search(r'^(?:长期规则|以后|记住).*(?:回答|回复|复盘|解释|自责|道歉|事实|证据)', value):
        return 'feedback'
    # An explicit change wins over a question about that change.
    if new_task(value):
        return 'revision'
    if re.search(r'(?:改成|改为|换成|改一下|重新生成|重新设计|先别创建|先不要创建|暂停)', value):
        return 'revision'
    if not re.search(r'^(?:为什么|为何|怎么|如何|是否|是不是|能否|可以|解释)', value) and not re.search(r'[?？]|(?:可以吗|可以么|行吗|行么)$', value):
        if re.search(r'(?:再加|增加|新增|添加|补充|去掉|删除|移除|取消).*(?:字段|对象|属性|表|需求|长度)|(?:字段|对象|表名|长度|类型).*(?:调整为|设为|设置为|改到)', value):
            return 'revision'
    if not re.search(r'^(?:为什么|为何|怎么|如何|是否|是不是|能否解释|解释)', value) and re.search(
            r'(?:修改|调整)(?:一下)?(?:名称|长度|字段|类型|工作区|方案|参数|这个|对象)', value):
        return 'revision'
    if re.search(r'(?:继续.*(?:生成|创建)|(?:生成|创建).*(?:继续|这个)|^用方案)', value):
        return 'revision'
    return 'question'


def new_task(text: str) -> bool:
    return bool(re.search(r'^(?:新任务[：:]|换个任务|另外一个任务|开始新设计|不要用(?:之前|上次)的附件)', text.strip()))


def running_intent(text: str) -> str:
    """The running composer accepts natural requirements and ordinary questions."""
    value = text.strip()
    intent = pending_intent(value)
    if intent == 'feedback':
        return intent
    if re.search(r'^(?:为什么|为何|怎么|如何|是否|是不是|能否|可以|解释)|[?？]|(?:吗|么)$', value):
        return 'question'
    if intent == 'revision':
        return intent
    if design_question(value) or value.strip('，。！,.!') in {'确认', '确定', '取消', '好的', '好', '收到', '谢谢', '你好'}:
        return 'question'
    return 'revision'


def design_question(text: str) -> bool:
    return bool(re.search(r'^(?:请|帮我)?(?:查询|查一下|查查|查看|解释|复盘|为什么|为何|如何|怎么|是否|是不是|现在.*(?:了吗|状态))', text.strip()))


def read_only_tool(name: str) -> bool:
    # Exact registered read operations, including evidence paging. Do not allow
    # check/prepare/publish tools based merely on a description from the model.
    return name.startswith(('get_', 'list_', 'inspect_', 'analyze_')) or name in {
        'check_designer_package', 'compare_designer_xml', 'query_designer_mdb',
    }


def metadata_lookup(text: str) -> bool:
    """Narrow schema selection only for explicit metadata lookups, never edits."""
    value = (text or '').strip()
    return bool(re.match(r'^(?:请|帮我)?(?:查询|查一下|查看|展示|显示|列出|列一下)', value)
                and re.search(r'字段|继承|子类|类型|定义|模型|CDO', value, re.I)
                and not re.search(r'创建|新建|新增|添加|增加|修改|改为|改成|换成|更新|删除|移除|写入|导入|生成|发布|保存|执行|设计|补充', value))


METADATA_LOOKUP_TOOLS = {
    'get_designer_environment', 'get_designer_capabilities', 'get_designer_catalog',
    'list_designer_entities', 'get_designer_entity', 'get_designer_fields',
    'get_designer_inheritance', 'get_designer_entity_schema', 'analyze_designer_where_used',
    'get_chat_evidence', 'inspect_designer_mdb', 'list_designer_mdb_cdos', 'get_designer_mdb_cdo',
}
