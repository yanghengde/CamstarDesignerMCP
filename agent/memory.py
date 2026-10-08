"""
会话记忆管理
==============
按用户名隔离的持久化聊天记忆，存储于 JSON 文件。
"""

import os
import glob
import json
import uuid

from config import MEMORY_FILE, SESSIONS_DIR
from agent.prompts import SYSTEM_PROMPT

# 内存中的状态字典
user_memories: dict = {}


def get_user_dir(username: str) -> str:
    return os.path.join(SESSIONS_DIR, username)


def _save_metadata(username: str):
    """保存用户的元数据（如活跃 session 配置）"""
    d = get_user_dir(username)
    os.makedirs(d, exist_ok=True)
    metadata_path = os.path.join(d, "metadata.json")
    data_to_save = {
        "active_session": user_memories[username].get("active_session")
    }
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(data_to_save, f, ensure_ascii=False, indent=2)


def save_session(username: str, session_id: str):
    """将指定用户的单个会话数据持久化"""
    if username in user_memories and session_id in user_memories[username].get("sessions", {}):
        d = get_user_dir(username)
        os.makedirs(d, exist_ok=True)
        session_data = user_memories[username]["sessions"][session_id]
        file_path = os.path.join(d, f"{session_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)


def save_memory():
    """兼容旧版接口的全量保存机制（建议在性能敏感区直接调用 save_session）"""
    for username, udata in user_memories.items():
        _save_metadata(username)
        for sid in udata.get("sessions", {}):
            save_session(username, sid)


def save_user_session(username: str, session_id: str):
    """增量保存单个会话及元数据，极大提升性能"""
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
        save_session(username, "default")


def get_sessions(username: str) -> list:
    """获取指定用户的所有会话列表"""
    if username not in user_memories:
        return []
    _migrate_if_needed(username)
    sessions = user_memories[username].get("sessions", {})
    # 按时间或自然序号直接吐出列表信息
    return [{"id": k, "title": v.get("title", "会话")} for k, v in sessions.items()]


def create_session(username: str) -> str:
    """创建一个新会话"""
    if username not in user_memories:
        user_memories[username] = {"sessions": {}, "active_session": None}
    else:
        _migrate_if_needed(username)
    
    session_id = str(uuid.uuid4())
    user_memories[username]["sessions"][session_id] = {
        "id": session_id,
        "title": f"新对话 {len(user_memories[username]['sessions']) + 1}",
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
    global user_memories
    user_memories = load_memory()
    # 如果系统刚从单体记忆切换过来，顺便把它们切片冲刷到磁盘中
    save_memory()
