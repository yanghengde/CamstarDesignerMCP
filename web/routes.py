import os
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from config import CHAT_USERNAME, ENABLE_PERFORMANCE_LOG
from agent.memory import get_user_messages, get_sessions, create_session, set_active_session
from agent.llm_client import chat_stream
from agent.experience import get_experience_status, list_candidates
from core.perf_logger import get_perf_logs

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    username: str
    session_id: str = None


@router.get("/")
def index():
    """返回主页 HTML。"""
    html_path = os.path.join("static", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@router.get("/config")
def config_endpoint():
    """返回前端所需的配置（如当前绑定用户名）。"""
    return {"username": CHAT_USERNAME}


@router.get("/sessions/{username}")
def sessions_endpoint(username: str):
    """获取用户所有会话"""
    return {"sessions": get_sessions(username)}


@router.post("/sessions/{username}/new")
def new_session_endpoint(username: str):
    """创建新会话"""
    session_id = create_session(username)
    return {"session_id": session_id}


@router.get("/history/{username}")
def history_endpoint(username: str, session_id: str = None):
    """获取指定用户的聊天历史。"""
    if session_id:
        set_active_session(username, session_id)
    return {"messages": get_user_messages(username, session_id)}


@router.post("/chat")
async def chat_endpoint(req: ChatRequest):
    """流式聊天接口 (SSE)。"""
    return StreamingResponse(
        chat_stream(req.username, req.message, req.session_id),
        media_type="text/event-stream"
    )


@router.get("/logs")
def logs_page():
    """返回性能日志管理页面 HTML。"""
    html_path = os.path.join("static", "logs.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@router.get("/api/logs/data")
def api_logs_data():
    """获取统计与原始日志数据"""
    if not ENABLE_PERFORMANCE_LOG:
        return {"status": "disabled", "logs": []}
        
    logs = get_perf_logs(1000)
    return {"status": "enabled", "logs": logs}


@router.get("/api/experience/status")
def experience_status_endpoint():
    """Return aggregate self-improvement queue status (read-only)."""
    return get_experience_status()


@router.get("/api/experience/candidates")
def experience_candidates_endpoint(
    status: Literal["pending", "approved", "rejected", "all"] = "pending",
    limit: int = Query(default=100, ge=1, le=500),
):
    """List sanitized experience candidates for human review (read-only)."""
    return {"candidates": list_candidates(status=status, limit=limit)}
