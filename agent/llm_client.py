"""
LLM 客户端 & 工具编排
========================
负责 LLM 初始化、MCP Tool 注册到 OpenAI schema、以及流式聊天处理。
"""

import json
import asyncio
import logging
from openai import AsyncOpenAI

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from tools import mcp

logger = logging.getLogger(__name__)

oai_client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

# 存储已注册的 OpenAI 格式工具列表
openai_tools: list[dict] = []


async def register_tools():
    """
    从 MCP Server 读取所有已注册的工具，转换为 OpenAI function calling 格式。
    应在 FastAPI lifespan 启动时调用。
    """
    global openai_tools
    openai_tools.clear()

    mcp_tools = await mcp.list_tools()
    for t in mcp_tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters
            }
        })
    print(f"[OK] 成功加载了 {len(openai_tools)} 个 Designer MCP Tools。")
    return openai_tools

async def generate_title(message: str) -> str:
    """生成不超过30字的短标题"""
    try:
        resp = await oai_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": "你是一个标题生成助手。请根据用户的第一句话提炼总结一个极短的标题（最多30个字符，只输出标题内容，不要加引号、句号等标点符号）。"},
                {"role": "user", "content": message}
            ],
            max_tokens=20,
            temperature=0.3
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "新对话"


async def chat_stream(username: str, message: str, session_id: str = None):
    """Stream Designer conversations through the checkpointed execution engine."""
    try:
        from agent.langgraph_runtime import langgraph_chat_stream

        async for event in langgraph_chat_stream(username, message, session_id):
            yield event
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Chat stream failed for user %s", username)
        payload = {
            "type": "error",
            "message": f"聊天工作流执行失败：{type(exc).__name__}: {exc}",
        }
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
