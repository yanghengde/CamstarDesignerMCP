"""LangGraph-based agent runtime.

The graph keeps the existing OpenAI-compatible model, FastMCP tools and SSE
wire format.  It adds durable checkpoints and a resumable approval gate around
mutating tool calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, AsyncIterator, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.config import get_stream_writer
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent.memory import (
    get_user_messages,
    save_user_session,
    update_session_title,
    user_memories,
)
from agent.experience import (
    build_runtime_experience_context,
    record_tool_outcome,
)
from agent.prompts import USER_FACING_LANGUAGE_RULE
from agent.safety import (
    MutationCounts,
    approval_prompt,
    classify_tool,
    count_tool_calls,
    evaluate_mutations,
    is_explicit_confirmation,
    is_explicit_rejection,
)
from config import (
    LANGGRAPH_CHECKPOINT_DB,
    LANGGRAPH_RECURSION_LIMIT,
    LLM_MODEL,
    MAX_TOOL_LOOPS,
)
from core.perf_logger import record_perf
from tools import get_tool_func


class AgentState(TypedDict, total=False):
    messages: list[dict[str, Any]]
    username: str
    session_id: str
    request_message: str
    loop_count: int
    create_count: int
    update_count: int
    delete_count: int
    approval_granted: bool
    pending_tool_calls: list[dict[str, Any]]
    final_reply: str
    stop_reason: str


_client: Any = None
_openai_tools: list[dict[str, Any]] = []
_graph: Any = None
_checkpointer_cm: Any = None


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _actual_session_id(username: str, session_id: str | None) -> str:
    sessions = user_memories.get(username, {}).get("sessions", {})
    if session_id and session_id in sessions:
        return session_id
    return user_memories.get(username, {}).get("active_session", "unknown")


def _graph_config(username: str, session_id: str) -> dict[str, Any]:
    """Build one consistent config for state reads, runs, and resumptions."""
    return {
        "configurable": {"thread_id": f"{username}:{session_id}"},
        "recursion_limit": LANGGRAPH_RECURSION_LIMIT,
    }


def _completed_counts(state: AgentState) -> MutationCounts:
    return MutationCounts(
        creates=int(state.get("create_count", 0)),
        updates=int(state.get("update_count", 0)),
        deletes=int(state.get("delete_count", 0)),
    )


def _has_interrupt(snapshot: Any) -> bool:
    return any(task.interrupts for task in snapshot.tasks)


def _first_interrupt_value(snapshot: Any) -> dict[str, Any] | None:
    for task in snapshot.tasks:
        if task.interrupts:
            value = task.interrupts[0].value
            return value if isinstance(value, dict) else {"message": str(value)}
    return None


async def _agent_node(state: AgentState) -> dict[str, Any]:
    writer = get_stream_writer()
    loops = int(state.get("loop_count", 0)) + 1
    if loops > MAX_TOOL_LOOPS:
        reply = (
            f"⚠️ 连续操作已达到上限（{MAX_TOOL_LOOPS} 次），任务已安全停止。"
            "请缩小范围后继续。"
        )
        return {
            "loop_count": loops,
            "pending_tool_calls": [],
            "final_reply": reply,
            "stop_reason": "loop_limit",
            "messages": state.get("messages", [])
            + [{"role": "assistant", "content": reply}],
        }

    started = time.time()
    try:
        tool_names = {
            str(message.get("name"))
            for message in state.get("messages", [])[-8:]
            if message.get("role") == "tool" and message.get("name")
        }
        experience_context = build_runtime_experience_context(
            state.get("request_message", ""), tool_names
        )
        model_messages = list(state.get("messages", []))
        language_message = {
            "role": "system",
            "content": USER_FACING_LANGUAGE_RULE,
        }
        insert_at = 1 if model_messages and model_messages[0].get("role") == "system" else 0
        model_messages.insert(insert_at, language_message)
        if experience_context:
            context_message = {
                "role": "system",
                "content": experience_context,
            }
            model_messages.insert(insert_at + 1, context_message)

        response_stream = await _client.chat.completions.create(
            model=LLM_MODEL,
            messages=model_messages,
            tools=_openai_tools,
            tool_choice="auto",
            stream=True,
        )

        tool_calls: dict[int, dict[str, Any]] = {}
        full_content = ""
        stream_started = False
        async for chunk in response_stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                if not stream_started:
                    writer({"type": "stream_start"})
                    stream_started = True
                full_content += delta.content
                writer({"type": "stream_chunk", "content": delta.content})

            if delta.tool_calls:
                for call in delta.tool_calls:
                    item = tool_calls.setdefault(
                        call.index,
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if call.id:
                        item["id"] = call.id
                    if call.function.name:
                        item["function"]["name"] += call.function.name
                    if call.function.arguments:
                        item["function"]["arguments"] += call.function.arguments
    except asyncio.CancelledError:
        raise

    record_perf(
        "LLM_Inference",
        time.time() - started,
        state.get("username", "unknown"),
        state.get("session_id", "unknown"),
        {
            "model": LLM_MODEL,
            "prompt": state.get("request_message", ""),
            "streamed": True,
            "engine": "langgraph",
        },
    )

    ordered_calls = [tool_calls[index] for index in sorted(tool_calls)]
    assistant_message: dict[str, Any] = {"role": "assistant"}
    if full_content:
        assistant_message["content"] = full_content
    if ordered_calls:
        assistant_message["tool_calls"] = ordered_calls

    update: dict[str, Any] = {
        "messages": state.get("messages", []) + [assistant_message],
        "loop_count": loops,
        "pending_tool_calls": ordered_calls,
        "final_reply": full_content if not ordered_calls else "",
        "stop_reason": "complete" if not ordered_calls else "",
    }
    if stream_started and not ordered_calls:
        writer({"type": "stream_end"})
    return update


def _route_after_agent(state: AgentState) -> str:
    return "policy" if state.get("pending_tool_calls") else END


def _policy_node(state: AgentState) -> dict[str, Any]:
    pending = state.get("pending_tool_calls", [])
    if state.get("approval_granted"):
        return {}
    decision = evaluate_mutations(_completed_counts(state), pending)
    if not decision.requires_approval:
        return {}

    approval = interrupt(
        {
            "type": "approval_required",
            "message": approval_prompt(decision),
            "counts": decision.totals.as_dict(),
            "categories": list(decision.categories),
        }
    )
    if isinstance(approval, dict) and approval.get("approved") is True:
        return {"approval_granted": True}

    cancelled_messages = list(state.get("messages", []))
    for tool_call in pending:
        function = tool_call.get("function") or {}
        cancelled_messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.get("id", "unknown"),
                "name": function.get("name", "unknown"),
                "content": "Action cancelled by the user before execution.",
            }
        )
    reply = "已取消本次待确认操作，未继续执行被拦截的工具。"
    cancelled_messages.append({"role": "assistant", "content": reply})
    return {
        "messages": cancelled_messages,
        "pending_tool_calls": [],
        "final_reply": reply,
        "stop_reason": "cancelled",
    }


def _route_after_policy(state: AgentState) -> str:
    return "execute_tool" if state.get("pending_tool_calls") else END


async def _execute_tool_node(state: AgentState) -> dict[str, Any]:
    writer = get_stream_writer()
    pending = list(state.get("pending_tool_calls", []))
    if not pending:
        return {}

    tool_call = pending.pop(0)
    function = tool_call.get("function") or {}
    func_name = str(function.get("name") or "")
    func_args_str = str(function.get("arguments") or "{}")
    writer({"type": "step", "func": func_name, "args": func_args_str})

    func_args: dict[str, Any] = {}
    try:
        func_args = json.loads(func_args_str)
        if not isinstance(func_args, dict):
            raise ValueError("tool arguments must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        result = f"Error: invalid arguments for {func_name}: {exc}"
    else:
        started = time.time()
        try:
            tool_func = get_tool_func(func_name)
            if tool_func is None:
                result = f"Error: tool '{func_name}' not found"
            else:
                result = await tool_func(**func_args)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # Tool errors are returned to the model for recovery.
            result = f"Error executing {func_name}: {exc}"
        finally:
            record_perf(
                "Tool_Execute",
                time.time() - started,
                state.get("username", "unknown"),
                state.get("session_id", "unknown"),
                {
                    "tool": func_name,
                    "prompt": state.get("request_message", ""),
                    "engine": "langgraph",
                },
            )

    try:
        candidate_id = record_tool_outcome(
            session_id=state.get("session_id", "unknown"),
            tool_name=func_name,
            arguments=func_args,
            result=result,
        )
        if candidate_id is not None:
            writer(
                {
                    "type": "experience_candidate",
                    "candidate_id": candidate_id,
                    "tool": func_name,
                }
            )
    except Exception as exc:
        # Learning telemetry must never change the business transaction result.
        print(f"[WARN] Failed to record experience candidate: {exc}")

    creates = int(state.get("create_count", 0))
    updates = int(state.get("update_count", 0))
    deletes = int(state.get("delete_count", 0))
    category = classify_tool(func_name)
    call_count = count_tool_calls([tool_call])
    if category == "create":
        creates += call_count.creates
    elif category == "update":
        updates += call_count.updates
    elif category == "delete":
        deletes += call_count.deletes

    tool_message = {
        "role": "tool",
        "tool_call_id": tool_call.get("id", "unknown"),
        "name": func_name,
        "content": str(result),
    }
    return {
        "messages": state.get("messages", []) + [tool_message],
        "pending_tool_calls": pending,
        "create_count": creates,
        "update_count": updates,
        "delete_count": deletes,
    }


def _route_after_tool(state: AgentState) -> str:
    return "execute_tool" if state.get("pending_tool_calls") else "agent"


def _build_graph(checkpointer: AsyncSqliteSaver):
    builder = StateGraph(AgentState)
    builder.add_node("agent", _agent_node)
    builder.add_node("policy", _policy_node)
    builder.add_node("execute_tool", _execute_tool_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", _route_after_agent)
    builder.add_conditional_edges("policy", _route_after_policy)
    builder.add_conditional_edges("execute_tool", _route_after_tool)
    return builder.compile(checkpointer=checkpointer)


async def init_langgraph_runtime(client: Any, openai_tools: list[dict[str, Any]]) -> None:
    global _client, _openai_tools, _graph, _checkpointer_cm
    if _graph is not None:
        return

    db_path = os.path.abspath(LANGGRAPH_CHECKPOINT_DB)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    _checkpointer_cm = AsyncSqliteSaver.from_conn_string(db_path)
    checkpointer = await _checkpointer_cm.__aenter__()
    _client = client
    _openai_tools = openai_tools
    _graph = _build_graph(checkpointer)
    print(f"[OK] LangGraph 执行引擎已启用，检查点：{db_path}")


async def close_langgraph_runtime() -> None:
    global _client, _openai_tools, _graph, _checkpointer_cm
    if _checkpointer_cm is not None:
        await _checkpointer_cm.__aexit__(None, None, None)
    _client = None
    _openai_tools = []
    _graph = None
    _checkpointer_cm = None


async def _stream_graph(
    graph_input: AgentState | Command,
    graph_config: dict[str, Any],
) -> AsyncIterator[str]:
    async for mode, payload in _graph.astream(
        graph_input,
        graph_config,
        stream_mode=["custom", "updates"],
    ):
        if mode == "custom" and isinstance(payload, dict):
            yield _sse(payload)


async def _sync_legacy_messages(
    username: str,
    session_id: str,
    graph_config: dict[str, Any],
) -> AgentState:
    snapshot = await _graph.aget_state(graph_config)
    state: AgentState = snapshot.values
    if not _has_interrupt(snapshot) and state.get("messages"):
        legacy_messages = get_user_messages(username, session_id)
        legacy_messages[:] = state["messages"]
        save_user_session(username, session_id)
    return state


async def langgraph_chat_stream(
    username: str,
    message: str,
    session_id: str | None = None,
) -> AsyncIterator[str]:
    if _graph is None:
        raise RuntimeError("LangGraph runtime has not been initialized")

    chat_messages = get_user_messages(username, session_id)
    actual_session_id = _actual_session_id(username, session_id)
    is_first_message = len(chat_messages) == 1
    graph_config = _graph_config(username, actual_session_id)

    snapshot = await _graph.aget_state(graph_config)
    if _has_interrupt(snapshot):
        if is_explicit_confirmation(message):
            try:
                async for event in _stream_graph(
                    Command(resume={"approved": True, "response": message}),
                    graph_config,
                ):
                    yield event
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                yield _sse({"type": "error", "message": f"工作流恢复失败: {exc}"})
                return
        elif is_explicit_rejection(message):
            try:
                async for event in _stream_graph(
                    Command(resume={"approved": False, "response": message}),
                    graph_config,
                ):
                    yield event
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                yield _sse({"type": "error", "message": f"工作流取消失败: {exc}"})
                return
        else:
            interrupt_value = _first_interrupt_value(snapshot) or {}
            prompt = interrupt_value.get(
                "message",
                "当前有操作等待确认。请回复明确的确认指令，或回复“取消执行”。",
            )
            yield _sse({"type": "done", "reply": prompt})
            return
    else:
        chat_messages.append({"role": "user", "content": message})
        save_user_session(username, actual_session_id)

        if is_first_message and actual_session_id != "unknown":
            # Imported lazily to avoid a module cycle with llm_client.
            from agent.llm_client import generate_title

            title = await generate_title(message)
            update_session_title(username, actual_session_id, title)
            yield _sse(
                {
                    "type": "title_update",
                    "title": title,
                    "session_id": actual_session_id,
                }
            )

        initial_state: AgentState = {
            "messages": list(chat_messages),
            "username": username,
            "session_id": actual_session_id,
            "request_message": message,
            "loop_count": 0,
            "create_count": 0,
            "update_count": 0,
            "delete_count": 0,
            "approval_granted": False,
            "pending_tool_calls": [],
            "final_reply": "",
            "stop_reason": "",
        }
        try:
            async for event in _stream_graph(initial_state, graph_config):
                yield event
        except asyncio.CancelledError:
            raise
        except GraphRecursionError:
            yield _sse(
                {
                    "type": "error",
                    "message": (
                        "任务连续推理次数超过安全上限，已停止执行。"
                        "请缩小查询范围后重试。"
                    ),
                }
            )
            return
        except Exception as exc:
            yield _sse({"type": "error", "message": f"工作流执行失败: {exc}"})
            return

    final_snapshot = await _graph.aget_state(graph_config)
    if _has_interrupt(final_snapshot):
        interrupt_value = _first_interrupt_value(final_snapshot) or {}
        yield _sse(
            {
                "type": "done",
                "reply": interrupt_value.get(
                    "message", "操作已暂停，请明确确认或取消。"
                ),
            }
        )
        return

    final_state = await _sync_legacy_messages(
        username, actual_session_id, graph_config
    )
    reply = final_state.get("final_reply", "")
    if reply and final_state.get("stop_reason") in {"loop_limit", "cancelled"}:
        yield _sse({"type": "done", "reply": reply})
    elif not reply:
        yield _sse({"type": "done", "reply": "任务已完成。"})
