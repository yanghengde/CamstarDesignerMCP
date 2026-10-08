import os
import asyncio
from typing import Literal

from fastapi import APIRouter, Query, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from pydantic import BaseModel, Field

from config import CHAT_USERNAME, ENABLE_PERFORMANCE_LOG
from agent.memory import get_user_messages, get_sessions, create_session, set_active_session
from agent.llm_client import chat_stream
from agent.experience import get_experience_status, list_candidates
from core.perf_logger import get_perf_logs
from designer.attachments import AttachmentError, MAX_FILE_BYTES, resolve_attachments, save_attachment

router = APIRouter()


class DesignerReviewRequest(BaseModel):
    username: str
    session_id: str
    package_id: str


def review_for_session(username: str, session_id: str, package_id: str) -> dict:
    from designer.review import session_packages
    require_session(username, session_id)
    for item in session_packages(get_user_messages(username, session_id)):
        if item['id'] == package_id:
            return item
    raise HTTPException(404, '当前对话没有这份可用设计结果。')


@router.post('/designer/review')
async def designer_review(req: DesignerReviewRequest):
    from designer.review import prepare
    item = review_for_session(req.username, req.session_id, req.package_id)
    try:
        return await asyncio.to_thread(prepare, item['manifest_file'], True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/designer/results/{package_id}/mdb')
def download_designer_mdb(package_id: str, username: str, session_id: str):
    item = review_for_session(username, session_id, package_id)
    return FileResponse(item['mdb_file'], filename=f"Designer_{package_id[:8]}.mdb", media_type='application/octet-stream')


class ChatRequest(BaseModel):
    message: str = Field(max_length=10000)
    username: str
    session_id: str = None
    attachment_ids: list[str] = Field(default_factory=list, max_length=3)


def require_session(username: str, session_id: str) -> None:
    if not session_id or not any(session['id'] == session_id for session in get_sessions(username)):
        raise HTTPException(404, "对话不存在，请新建对话后添加附件。")


@router.post('/attachments/excel')
async def upload_excel(
    file: UploadFile = File(...), username: str = Form(...), session_id: str = Form(...),
):
    """Parse an Excel upload without executing any Designer operation."""
    try:
        require_session(username, session_id)
        data = bytearray()
        while chunk := await file.read(64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_FILE_BYTES:
                raise AttachmentError("Excel 附件不能超过 10 MB。", 413)
        return await asyncio.to_thread(save_attachment, bytes(data), file.filename or '', username, session_id)
    except AttachmentError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    finally:
        await file.close()


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
    messages = get_user_messages(username, session_id)
    from designer.review import session_packages
    return {"designer_results": session_packages(messages), "messages": [
        {**{key: value for key, value in message.items() if key != 'display_content'},
         'content': message.get('display_content', message.get('content', ''))}
        for message in messages
    ]}


@router.post("/chat")
async def chat_endpoint(req: ChatRequest):
    """流式聊天接口 (SSE)。"""
    if not req.message.strip():
        raise HTTPException(400, "请填写设计要求，或说明希望如何处理附件。")
    attachments = []
    if req.attachment_ids:
        require_session(req.username, req.session_id)
        try:
            attachments = await asyncio.to_thread(resolve_attachments, req.attachment_ids, req.username, req.session_id)
        except AttachmentError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
    stream = chat_stream(req.username, req.message, req.session_id, attachments=attachments) if attachments else chat_stream(req.username, req.message, req.session_id)
    return StreamingResponse(
        stream,
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
