"""
会话记忆管理
==============
按用户名隔离的持久化聊天记忆，存储于 JSON 文件。
"""

import os
import glob
import json
import uuid
import time
import re
from time import sleep as disk_retry_wait
from pathlib import Path

from config import MEMORY_FILE, SESSIONS_DIR
from agent.prompts import SYSTEM_PROMPT
from agent.titles import clean_title, first_message_title, is_placeholder_title

# 内存中的状态字典
user_memories: dict = {}


def _ensure_session_times(session: dict, fallback: float = 0):
    """Keep activity timestamps separate from reads and maintenance writes."""
    session.setdefault('created_at', fallback)
    session.setdefault('updated_at', fallback or session['created_at'])


def get_user_dir(username: str) -> str:
    if (not isinstance(username, str) or not re.fullmatch(r'[\w@.-]{1,80}', username)
            or username.endswith('.') or username.split('.')[0].casefold() in
            {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}):
        raise ValueError('无效的会话用户标识')
    root = Path(SESSIONS_DIR).resolve()
    directory = (root / username).resolve()
    if not directory.is_relative_to(root) or directory == root:
        raise ValueError('会话目录必须位于会话根目录内')
    return str(directory)


def _session_file(username, session_id):
    if not isinstance(session_id, str) or not re.fullmatch(r'[\w-]{1,100}', session_id):
        raise ValueError('无效的会话标识')
    directory = Path(get_user_dir(username))
    file = (directory / (session_id + '.json')).resolve()
    if file.parent != directory or session_id.casefold() in {
            'metadata', 'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}:
        raise ValueError('无效的会话文件名')
    return file


def _write_json(file, value):
    temporary = file.with_name(file.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        for attempt in range(5):
            try:
                temporary.replace(file)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                disk_retry_wait(0.02 * (attempt + 1))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _save_metadata(username: str):
    """保存用户的元数据（如活跃 session 配置）"""
    d = get_user_dir(username)
    os.makedirs(d, exist_ok=True)
    metadata_path = Path(d) / 'metadata.json'
    if metadata_path.resolve().parent != Path(d):
        raise ValueError('会话配置文件不能指向用户目录之外')
    data_to_save = {
        "active_session": user_memories[username].get("active_session")
    }
    _write_json(metadata_path, data_to_save)


def save_session(username: str, session_id: str):
    """将指定用户的单个会话数据持久化"""
    if username in user_memories and session_id in user_memories[username].get("sessions", {}):
        d = get_user_dir(username)
        os.makedirs(d, exist_ok=True)
        session_data = user_memories[username]["sessions"][session_id]
        _write_json(_session_file(username, session_id), session_data)


def save_memory():
    """兼容旧版接口的全量保存机制（建议在性能敏感区直接调用 save_session）"""
    for username, udata in user_memories.items():
        _save_metadata(username)
        for sid in udata.get("sessions", {}):
            save_session(username, sid)


def save_user_session(username: str, session_id: str):
    """增量保存单个会话及元数据，极大提升性能"""
    session = user_memories[username]['sessions'][session_id]
    _ensure_session_times(session)
    session['updated_at'] = time.time()
    user_memories[username]['active_session'] = session_id
    _save_metadata(username)
    save_session(username, session_id)



def load_memory() -> dict:
    """初始化加载：从分离的 session 文件夹加载，如果包含旧 memory.json 则尝试热迁移组合"""
    mem = {}
    
    # 1. 挂载旧版的全局 memory.json 进行兼容
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                old_mem = json.load(f)
                for uname, udata in old_mem.items():
                    if isinstance(udata, list):
                        mem[uname] = {
                            "active_session": "default",
                            "sessions": {"default": {"id": "default", "title": "默认会话", "messages": udata}}
                        }
                    else:
                        mem[uname] = udata
                    for session in mem[uname].get('sessions', {}).values():
                        _ensure_session_times(session, os.path.getmtime(MEMORY_FILE))
        except Exception:
            pass

    # 2. 如果存在分离的文件目录，其数据优先级更高以覆盖基座
    if os.path.exists(SESSIONS_DIR):
        for user_folder in os.listdir(SESSIONS_DIR):
            uname = user_folder
            user_path = os.path.join(SESSIONS_DIR, user_folder)
            if not os.path.isdir(user_path):
                continue
                
            if uname not in mem:
                mem[uname] = {"active_session": None, "sessions": {}}
                
            meta_path = os.path.join(user_path, "metadata.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as fm:
                        mem[uname]["active_session"] = json.load(fm).get("active_session")
                except Exception:
                    pass
            
            for s_file in glob.glob(os.path.join(user_path, "*.json")):
                if os.path.basename(s_file) == "metadata.json":
                    continue
                try:
                    with open(s_file, "r", encoding="utf-8") as fs:
                        s_data = json.load(fs)
                        sid = s_data.get("id")
                        if sid:
                            _session_file(uname, sid)
                            _ensure_session_times(s_data, os.path.getmtime(s_file))
                            mem[uname]["sessions"][sid] = s_data
                except Exception:
                    pass

    return mem


def _migrate_if_needed(username: str):
    # 此函数用作旧版老逻辑结构的容错防御
    data = user_memories.get(username)
    if isinstance(data, list):
        user_memories[username] = {
            "sessions": {
                "default": {
                    "id": "default",
                    "title": "默认会话",
                    "messages": data
                }
            },
            "active_session": "default"
        }
        _save_metadata(username)
        _ensure_session_times(user_memories[username]['sessions']['default'])
        save_session(username, "default")


def get_sessions(username: str) -> list:
    """获取所有会话，按最后一次内容更新倒序排列。"""
    if username not in user_memories:
        return []
    _migrate_if_needed(username)
    sessions = user_memories[username].get("sessions", {})
    result = []
    for sid, session in sessions.items():
        _ensure_session_times(session)
        result.append({'id': sid, 'title': session.get('title', '会话'),
                       'created_at': session['created_at'], 'updated_at': session['updated_at']})
    # Reverse insertion order provides a stable tie-breaker for legacy data.
    return sorted(reversed(result), key=lambda session: (session['updated_at'], session['created_at']), reverse=True)


def create_session(username: str) -> str:
    """创建一个新会话"""
    get_user_dir(username)
    if username not in user_memories:
        user_memories[username] = {"sessions": {}, "active_session": None}
    else:
        _migrate_if_needed(username)
    
    session_id = str(uuid.uuid4())
    created = time.time()
    user_memories[username]["sessions"][session_id] = {
        "id": session_id,
        "title": f"新对话 {len(user_memories[username]['sessions']) + 1}",
        "created_at": created,
        "updated_at": created,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}]
    }
    user_memories[username]["active_session"] = session_id
    
    save_session(username, session_id)
    _save_metadata(username)
    return session_id

def update_session_title(username: str, session_id: str, title: str):
    """更新会话的名称并持久化"""
    if username in user_memories:
        if session_id in user_memories[username].get("sessions", {}):
            title = clean_title(title)
            if not title:
                return
            user_memories[username]["sessions"][session_id]["title"] = title
            save_session(username, session_id)


def set_active_session(username: str, session_id: str):
    if username in user_memories:
        _migrate_if_needed(username)
        if session_id in user_memories[username]["sessions"]:
            user_memories[username]["active_session"] = session_id
            _save_metadata(username)


def get_user_messages(username: str, session_id: str = None) -> list:
    """获取指定用户的消息历史，不存在则初始化。"""
    if username not in user_memories:
        create_session(username)
    else:
        _migrate_if_needed(username)
        
    user_data = user_memories[username]
    if not session_id or session_id not in user_data["sessions"]:
        session_id = user_data["active_session"]
        if not session_id or session_id not in user_data["sessions"]:
             session_id = create_session(username)
             
    messages = user_data["sessions"][session_id]["messages"]
    
    # 强制更新老用户的系统提示词，以应用最新的Agent定位指令
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = SYSTEM_PROMPT
        
    return messages


def init_memory():
    """系统启动时的统一外层装载点。"""
    # Other modules hold a reference to this dictionary. Rebinding it leaves
    # the graph with stale session IDs and causes saves to target "unknown".
    loaded = load_memory()
    user_memories.clear()
    user_memories.update(loaded)
    # Repair unnamed conversations using their first visible message only.
    for username, user in user_memories.items():
        for sid, session in user.get('sessions', {}).items():
            file_path = os.path.join(get_user_dir(username), f'{sid}.json')
            _ensure_session_times(session, os.path.getmtime(file_path) if os.path.isfile(file_path) else 0)
            if not is_placeholder_title(session.get('title', '')):
                continue
            first = next((message for message in session.get('messages', [])
                          if message.get('role') == 'user' and message.get('display_content', message.get('content', '')).strip()), None)
            if first:
                session['title'] = first_message_title(first.get('display_content', first['content']))
    # 如果系统刚从单体记忆切换过来，顺便把它们切片冲刷到磁盘中
    save_memory()
