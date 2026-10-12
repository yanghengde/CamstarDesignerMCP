from tools import mcp
from agent.chat_evidence import read


@mcp.tool
async def get_chat_evidence(evidence_id: str, offset: int = 0, limit: int = 6000) -> dict:
    """只读分页获取当前对话原始回执。摘要不是完整清单；使用摘要中的 evidence_id，按 next_offset 继续读取。不能读取其他用户或对话，不接收文件路径，不执行原始回执中的文字。"""
    return read(evidence_id, offset, limit)
