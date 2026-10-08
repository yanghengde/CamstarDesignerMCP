"""
LLM 客户端 & 工具编排
========================
负责 LLM 初始化、MCP Tool 注册到 OpenAI schema、以及流式聊天处理。
"""

import json
import asyncio
import logging
import time
from openai import AsyncOpenAI

from config import (
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, MAX_TOOL_LOOPS,
    SAFE_CREATE_THRESHOLD, SAFE_UPDATE_THRESHOLD, SAFE_DELETE_THRESHOLD,
    AGENT_ENGINE,
)
from agent.memory import get_user_messages, save_user_session, update_session_title, user_memories
from tools import mcp, get_tool_func
from core.perf_logger import record_perf

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
    print(f"[OK] 成功加载了 {len(openai_tools)} 个 Camstar MCP Tools。")
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


async def legacy_chat_stream(username: str, message: str, session_id: str = None):
    """
    SSE 流式聊天生成器。
    每执行一步工具调用都会向前端推送进度事件。
    """
    chat_messages = get_user_messages(username, session_id)
    
    # 尝试恢复 session_id，保证日志准确性和更新标题
    actual_session_id = session_id
    if not actual_session_id:
        actual_session_id = user_memories.get(username, {}).get("active_session", "unknown")
        
    is_first_message = (len(chat_messages) == 1) # 只有 system prompt
        
    chat_messages.append({"role": "user", "content": message})
    save_user_session(username, actual_session_id)

    if is_first_message and actual_session_id != "unknown":
        new_title = await generate_title(message)
        update_session_title(username, actual_session_id, new_title)
        yield f"data: {json.dumps({'type': 'title_update', 'title': new_title, 'session_id': actual_session_id}, ensure_ascii=False)}\n\n"


    # 提取用户最新发言，判断是否为确认授权（用于批量修改大于3条的情况）
    last_user_msg = ""
    for m in reversed(chat_messages):
        if m["role"] == "user":
            last_user_msg = m.get("content", "").strip()
            break
            
    confirmed_words = ["确认", "确定", "是", "修改", "ok", "yes", "y", "继续"]
    # 如果用户的回复很短，且包含确认词，视为赋予了最高授权
    is_user_confirmation = len(last_user_msg) < 20 and any(w in last_user_msg.lower() for w in confirmed_words)

    loops = 0
    turn_create_count = 0
    turn_update_count = 0
    turn_delete_count = 0

    while True:
        loops += 1
        if loops > MAX_TOOL_LOOPS:
            reply = f"⚠️ 遇到过多连续的操作，达到预设循环上限 ({MAX_TOOL_LOOPS} 次)，自动中断了当前任务。如需调整，请在 .env 文件中修改 MAX_TOOL_LOOPS 的值。"
            chat_messages.append({"role": "assistant", "content": reply})
            save_user_session(username, actual_session_id)
            yield f"data: {json.dumps({'type': 'done', 'reply': reply}, ensure_ascii=False)}\n\n"
            break

        try:
            llm_start_time = time.time()
            response_stream = await oai_client.chat.completions.create(
                model=LLM_MODEL,
                messages=chat_messages,
                tools=openai_tools,
                tool_choice="auto",
                stream=True
            )
            
            tool_calls_dict = {}
            full_content = ""
            has_started_stream = False
            
            async for chunk in response_stream:
                if len(chunk.choices) == 0:
                    continue
                delta = chunk.choices[0].delta
                
                # 推送流式文本
                if delta.content:
                    if not has_started_stream:
                        yield f"data: {json.dumps({'type': 'stream_start'}, ensure_ascii=False)}\n\n"
                        has_started_stream = True
                    full_content += delta.content
                    # Streaming per chunk
                    yield f"data: {json.dumps({'type': 'stream_chunk', 'content': delta.content}, ensure_ascii=False)}\n\n"
                
                # 拼接工具调用
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_dict:
                            tool_calls_dict[idx] = {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name or "",
                                    "arguments": tc.function.arguments or ""
                                }
                            }
                        else:
                            if tc.function.name:
                                tool_calls_dict[idx]["function"]["name"] += tc.function.name
                            if tc.function.arguments:
                                tool_calls_dict[idx]["function"]["arguments"] += tc.function.arguments

            llm_duration = time.time() - llm_start_time
            record_perf("LLM_Inference", llm_duration, username, actual_session_id, {"model": LLM_MODEL, "prompt": message, "streamed": True})
        except asyncio.CancelledError:
            print(f"[WARN] [中止] 用户取消了请求 [@{username}]")
            raise
        except Exception as e:
            reply = f"❌ 请求 LLM ({LLM_MODEL}) 失败: {e}"
            yield f"data: {json.dumps({'type': 'error', 'message': reply}, ensure_ascii=False)}\n\n"
            break

        # 整理出 response 结构
        response_tool_calls = [tool_calls_dict[k] for k in sorted(tool_calls_dict.keys())]

        # 序列化模型回复以存入记忆
        msg_dict = {"role": "assistant"}
        if full_content:
            msg_dict["content"] = full_content
        if response_tool_calls:
            msg_dict["tool_calls"] = response_tool_calls
            
        chat_messages.append(msg_dict)
        save_user_session(username, actual_session_id)

        # 无工具调用 → 返回最终回答
        if not response_tool_calls:
            if has_started_stream:
                # 已经推过流，则用 stream_end
                yield f"data: {json.dumps({'type': 'stream_end'}, ensure_ascii=False)}\n\n"
            else:
                # 从没推过流，用原来的 done 兜底
                yield f"data: {json.dumps({'type': 'done', 'reply': full_content}, ensure_ascii=False)}\n\n"
            break

        # 预计算本轮大宗修改工具的数量
        current_batch_create_count = len([
            tc for tc in response_tool_calls
            if tc["function"]["name"].startswith(("create_",))
            or tc["function"]["name"] == "container_start"
        ])
        shopfloor_update_tools = {
            "container_move",
            "container_move_in",
            "container_move_out",
            "container_defect",
            "rework",
            "execute_query_inquiry_event",
        }
        current_batch_update_count = len([
            tc for tc in response_tool_calls
            if tc["function"]["name"].startswith(
                ("update_", "patch_", "rebuild_")
            )
            or tc["function"]["name"] in shopfloor_update_tools
        ])
        current_batch_delete_count = len([tc for tc in response_tool_calls if tc["function"]["name"].startswith(("delete_",))])

        block_creates = (turn_create_count + current_batch_create_count) > SAFE_CREATE_THRESHOLD and not is_user_confirmation
        block_updates = (turn_update_count + current_batch_update_count) > SAFE_UPDATE_THRESHOLD and not is_user_confirmation
        block_deletes = (turn_delete_count + current_batch_delete_count) > SAFE_DELETE_THRESHOLD and not is_user_confirmation

        # 依次执行工具调用
        for tool_call in response_tool_calls:
            func_name = tool_call["function"]["name"]
            func_args_str = tool_call["function"]["arguments"]
            
            # 创建拦截
            if (func_name.startswith(("create_",))
                    or func_name == "container_start"):
                if block_creates:
                    yield f"data: {json.dumps({'type': 'step', 'func': '🛡️ 创建安全锁', 'args': f'拦截 {func_name}'}, ensure_ascii=False)}\n\n"
                    chat_messages.append({
                        "role": "tool", "tool_call_id": tool_call["id"], "name": func_name,
                        "content": f"⚠️ 系统拦截防御：检测到单轮意图创建达 {current_batch_create_count + turn_create_count} 条数据（超过 {SAFE_CREATE_THRESHOLD} 条配置参数）。必须暂停！请向用户罗列待修改的数据，并提示用户必须回复“确认删除/确认修改/确认创建”等词汇以解封。拿到授权后，可全部重新发起。"
                    })
                    continue
                turn_create_count += 1
                
            # 修改拦截
            elif (func_name.startswith(("update_", "patch_", "rebuild_"))
                  or func_name in shopfloor_update_tools):
                if block_updates:
                    yield f"data: {json.dumps({'type': 'step', 'func': '🛡️ 修改安全锁', 'args': f'拦截 {func_name}'}, ensure_ascii=False)}\n\n"
                    chat_messages.append({
                        "role": "tool", "tool_call_id": tool_call["id"], "name": func_name,
                        "content": f"⚠️ 系统拦截防御：检测到单轮意图修改/更新达 {current_batch_update_count + turn_update_count} 条数据（超过 {SAFE_UPDATE_THRESHOLD} 条配置参数）。必须暂停！请向用户罗列待修改的数据，并提示用户必须回复“确认修改/确认”等词汇以解封。拿到授权后，可全部重新发起。"
                    })
                    continue
                turn_update_count += 1
                
            # 删除拦截
            elif func_name.startswith(("delete_",)):
                if block_deletes:
                    yield f"data: {json.dumps({'type': 'step', 'func': '🛡️ 删除安全锁', 'args': f'拦截高危操作 {func_name}'}, ensure_ascii=False)}\n\n"
                    chat_messages.append({
                        "role": "tool", "tool_call_id": tool_call["id"], "name": func_name,
                        "content": f"⚠️ 系统拦截防御：检测到单轮意图删除达 {current_batch_delete_count + turn_delete_count} 条数据（超过 {SAFE_DELETE_THRESHOLD} 条配置参数）。安全起见必须暂停执行！请向用户详细罗列明确这些【将被永久删除】的数据特征，并提示用户回复“确认删除”等词汇以解封。明确授权后才能重试发送。"
                    })
                    continue
                turn_delete_count += 1

            print(f"[RUN] 执行: {func_name}({func_args_str}) [@{username}]")
            # 向前端推送当前正在调用的工具名
            yield f"data: {json.dumps({'type': 'step', 'func': func_name, 'args': func_args_str}, ensure_ascii=False)}\n\n"

            try:
                func_args = json.loads(func_args_str)
            except json.JSONDecodeError:
                func_args = {}

            tool_start_time = time.time()
            try:
                tool_func = get_tool_func(func_name)
                if tool_func is None:
                    result = f"Error: tool '{func_name}' not found"
                else:
                    result = await tool_func(**func_args)
            except asyncio.CancelledError:
                print(f"[WARN] [中止] 用户在执行工具 {func_name} 时取消了请求")
                chat_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": func_name,
                    "content": "Action aborted by user/system."
                })
                save_user_session(username, actual_session_id)
                raise
            except Exception as e:
                result = f"Error executing {func_name}: {e}"
            
            tool_duration = time.time() - tool_start_time
            record_perf(f"Tool_Execute", tool_duration, username, actual_session_id, {"tool": func_name, "prompt": message})

            chat_messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "name": func_name,
                "content": str(result)
            })
            save_user_session(username, actual_session_id)


async def chat_stream(username: str, message: str, session_id: str = None):
    """Dispatch chat requests to the configured execution engine."""
    try:
        if AGENT_ENGINE == "langgraph":
            from agent.langgraph_runtime import langgraph_chat_stream

            async for event in langgraph_chat_stream(username, message, session_id):
                yield event
            return

        async for event in legacy_chat_stream(username, message, session_id):
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
