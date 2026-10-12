"""Only explicit human style preferences are persisted, never model self-review."""
import re

from agent import memory

ALLOWED = {
    'length': {'brief', 'detailed'}, 'focus': {'facts_and_next_step'},
    'apology': {'brief'}, 'choices': {'only_when_needed'},
}


def current(username, session_id):
    user = memory.user_memories[username]
    values = {**user.get('chat_preferences', {}),
              **user['sessions'][session_id].get('chat_preferences', {})}
    return {key: value for key, value in values.items() if key in ALLOWED and isinstance(value, str) and value in ALLOWED[key]}


def apply(username, session_id, text):
    if len(text) > 500:
        return None
    global_rule = bool(re.match(r'^(?:以后|长期规则[：:]|请记住|记住)', text.strip()))
    scope = memory.user_memories[username] if global_rule else memory.user_memories[username]['sessions'][session_id]
    values = {}
    if re.search(r'(?:回答|回复|解释|复盘).*(?:简短|短一点|少说|精简)', text):
        values['length'] = 'brief'
    if re.search(r'(?:回答|回复|解释).*(?:详细|展开)', text):
        values['length'] = 'detailed'
    if re.search(r'(?:先说|先讲|先给).*(?:事实|证据|结果|下一步)', text):
        values['focus'] = 'facts_and_next_step'
    if re.search(r'(?:少点|少一点|不要|别).*(?:自责|道歉)|(?:自责|道歉).*(?:少点|少一点|不要|别)', text):
        values['apology'] = 'brief'
    if re.search(r'(?:不要|别).*(?:反复|重复).*(?:选|方案|追问)', text):
        values['choices'] = 'only_when_needed'
    reset = bool(re.fullmatch(r'(?:以后|长期规则[：:]\s*)?(?:恢复默认回复风格|清除回复偏好)[。！!\s]*', text.strip()))
    if not values and not reset:
        return None
    if reset:
        scope.pop('chat_preferences', None)
    else:
        scope.setdefault('chat_preferences', {}).update(values)
    memory.save_user_session(username, session_id)
    scope_name = '后续对话' if global_rule else '当前对话'
    return f'已为{scope_name}恢复默认回复风格。' if reset else f'已记录{scope_name}的回复偏好，接下来按你的要求表达。'
