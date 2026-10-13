"""LangGraph-based agent runtime.

The graph keeps the existing OpenAI-compatible model, FastMCP tools and SSE
wire format.  It adds durable checkpoints and a resumable approval gate around
mutating tool calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from weakref import WeakKeyDictionary
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
from agent.prompts import CHAT_QUALITY_RULE, CONVERSATION_INTENT_RULE, SYSTEM_PROMPT, USER_FACING_LANGUAGE_RULE
from agent.safety import (
    MutationCounts,
    approval_prompt,
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
from designer.attachments import attachment_context, attachment_summary
from agent.titles import first_message_title
from designer import progress_store
from agent import excel_preview
from agent import chat_context, chat_intent, chat_preferences, chat_requirements
from agent.chat_evidence import evidence_scope
from designer.design_contract import validate_calls, inspect_calls


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
    excel_sources: list[dict]
    excel_approved_calls: list[str]
    excel_confirmation_response: str
    task_context: dict
    read_only: bool
    validation_failures: int
    tool_failures: list[str]
    inquiry: bool


_client: Any = None
_openai_tools: list[dict[str, Any]] = []
_graph: Any = None
_checkpointer_cm: Any = None
_session_locks = WeakKeyDictionary()


def _pending_key(state):
    return '|'.join(str(call.get('id')) for call in state.get('pending_tool_calls', []))


def _notes(username, session_id, state):
    return user_memories[username]['sessions'][session_id].get('pending_dialogue', {}).get(_pending_key(state), [])


def _merge_notes(messages, notes):
    if not notes:
        return list(messages)
    # The queued assistant call stays adjacent to its future tool responses.
    return list(messages[:-1]) + notes + list(messages[-1:])


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


def _adopt_requirements(state):
    if state.get('inquiry'):
        return {}
    username, session_id = state['username'], state['session_id']
    queued = chat_requirements.pending(username, session_id)
    revisions = [item for item in queued if item['kind'] == 'revision']
    pending = state.get('pending_tool_calls', [])
    # Ordinary questions wait for the current batch; they never cancel writes.
    if not queued or pending and not revisions:
        return {}
    items = chat_requirements.claim(username, session_id)
    messages = list(state.get('messages', []))
    writer = get_stream_writer()
    if pending:
        messages = _merge_notes(messages, _notes(username, session_id, state))
        user_memories[username]['sessions'][session_id].get('pending_dialogue', {}).pop(_pending_key(state), None)
        ids = {call.get('id') for call in pending}
        old_preview = next((message['excel_preview'] for message in reversed(messages)
                            if message.get('excel_preview') and any(call.get('id') in ids for call in message.get('tool_calls', []))), None)
        if not old_preview:
            old_preview = next((message['excel_preview'] for message in reversed(get_user_messages(username, session_id))
                                if message.get('excel_preview') and any(call.get('id') in ids for call in message.get('tool_calls', []))), None)
        if old_preview:
            batch_ids = {call.get('id') for call in next(
                (m for m in reversed(messages) if m.get('tool_calls')), {}).get('tool_calls', [])}
            preview = {**old_preview, 'status': 'superseded', 'partial_execution': any(
                message.get('role') == 'tool' and message.get('tool_call_id') in batch_ids
                for message in messages)}
            messages = excel_preview.attach_preview(messages, preview, pending)
            writer({'type': 'excel_preview_status', 'id': preview['id'], 'status': 'superseded',
                    'partial_execution': preview['partial_execution']})
        for call in pending:
            messages.append({'role': 'tool', 'tool_call_id': call.get('id', 'unknown'),
                             'name': call.get('function', {}).get('name', 'unknown'),
                             'content': '用户在运行中补充了要求，本步骤尚未执行，已取消；已完成的步骤以真实工具回执为准，请按最新要求重新规划。'})
    if state.get('excel_confirmation_response'):
        messages.append({'role': 'user', 'content': state['excel_confirmation_response']})
    messages.extend(chat_requirements.user_record(item) for item in items)
    for item in items:
        chat_preferences.apply(username, session_id, item['content'])
    task = dict(state.get('task_context', {}))
    task.pop('prior_plan', None)
    sources = state.get('excel_sources', [])
    for item in revisions:
        reset = chat_intent.new_task(item['content'])
        task = chat_context.update_task(task, item['content'], [], reset=reset)
        if reset:
            sources = []
    if revisions:
        task['live_revision'] = {'notice': '用户在运行中修改了需求。已执行步骤不能视为已撤销；先依据真实回执核对当前设计，再规划后续修改。新设计必须重新出预览确认，旧确认不授权新参数。',
                                 'requirement_ids': [item['id'] for item in revisions]}
    for item in items:
        writer({'type': 'chat_requirement', 'requirement': item})
    writer({'type': 'requirements_applied'})
    return {'messages': messages, 'pending_tool_calls': [], 'loop_count': 0,
            'request_message': '\n'.join(item['content'] for item in items),
            'task_context': task, 'excel_sources': sources,
            'approval_granted': False, 'excel_approved_calls': [], 'excel_confirmation_response': '',
            'read_only': not bool(revisions), 'validation_failures': 0,
            'final_reply': '', 'stop_reason': 'replan'}


def _requirements_node(state):
    return _adopt_requirements(state)


def _route_after_requirements(state):
    if state.get('stop_reason') == 'replan':
        return 'agent'
    if state.get('pending_tool_calls'):
        return 'policy'
    return END if state.get('stop_reason') in {'complete', 'loop_limit', 'validation_failed', 'cancelled'} else 'agent'


async def _agent_node(state: AgentState) -> dict[str, Any]:
    writer = get_stream_writer()
    history = state.get('messages', [])
    if history and history[0].get('role') == 'system':
        # Old checkpoints must use current program-owned instructions too.
        state = {**state, 'messages': [{**history[0], 'content': SYSTEM_PROMPT}] + history[1:]}
    loops = int(state.get("loop_count", 0)) + 1
    limit = 4 if state.get('read_only') else MAX_TOOL_LOOPS
    if loops > limit:
        reply = (
            f"连续操作已达到上限（{limit} 次），本次处理已停止。"
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
    inference_id, _ = progress_store.begin(state.get('username', 'unknown'), state.get('session_id', 'unknown'),
                                            '__llm_inference', {})
    inference_pulse = asyncio.create_task(progress_store.heartbeat(inference_id))
    try:
        tool_names = {
            str(message.get("name"))
            for message in state.get("messages", [])[-8:]
            if message.get("role") == "tool" and message.get("name")
        }
        experience_context = build_runtime_experience_context(
            state.get("request_message", ""), tool_names
        )
        model_messages = chat_context.build_messages(
            state.get('messages', []), task=state.get('task_context'),
            preferences=chat_preferences.current(state['username'], state['session_id']),
            extra_system=[USER_FACING_LANGUAGE_RULE, CONVERSATION_INTENT_RULE, CHAT_QUALITY_RULE, experience_context],
        )
        available_tools = [tool for tool in _openai_tools if not state.get('read_only')
                           or chat_intent.read_only_tool(tool.get('function', {}).get('name', ''))]

        response_stream = await _client.chat.completions.create(
            model=LLM_MODEL,
            messages=model_messages,
            tools=available_tools,
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
        progress_store.finish(inference_id, error='请求已停止', status='interrupted')
        raise
    except Exception as exc:
        progress_store.finish(inference_id, error=str(exc), status='failed')
        raise
    else:
        progress_store.finish(inference_id, {'status': 'inference_complete'})
    finally:
        inference_pulse.cancel()
        await asyncio.gather(inference_pulse, return_exceptions=True)

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
        "task_context": chat_context.update_task(state.get('task_context'), '', [], ordered_calls)
                        if not state.get('read_only') else state.get('task_context', {}),
    }
    if stream_started and not ordered_calls:
        writer({"type": "stream_end"})
    return update


def _route_after_agent(state: AgentState) -> str:
    return 'requirements'


def _policy_node(state: AgentState) -> dict[str, Any]:
    revised = _adopt_requirements(state)
    if revised:
        return revised
    pending = state.get("pending_tool_calls", [])
    creation_checks = []
    if state.get('task_context'):
        state = {**state, 'task_context': chat_context.update_task(state['task_context'], '', [])}
    if not state.get('task_context'):
        task = chat_context.update_task({}, '', state.get('messages', []))
        state = {**state, 'task_context': chat_context.update_task(task, '', [], pending)}
    if state.get('read_only') and any(not chat_intent.read_only_tool((call.get('function') or {}).get('name', '')) for call in pending):
        return _reject_plan(state, '追问期间仅允许只读查询，待确认设计保持不变。')
    try:
        validate_calls(pending)
        task = state.get('task_context', {})
        for call in pending:
            function = call.get('function') or {}
            if function.get('name') not in excel_preview.DESIGN_TOOLS:
                continue
            if _attempt_key(call) in state.get('tool_failures', []):
                raise ValueError('相同参数和来源版本的设计调用已经失败；请先只读核验实际结果和错误原因，不要直接重复写入。')
            args = json.loads(function.get('arguments') or '{}')
            creates = ([{'name': args.get('cdo_name'), 'parent': args.get('parent_cdo'), 'create_table': args.get('create_table', False)}]
                       if function['name'] == 'generate_designer_cdo_package' else
                       [op for op in args.get('operations', []) if op.get('action') == 'create_cdo'])
            if task.get('requires_table') and any(not op.get('create_table') for op in creates):
                raise ValueError('当前需求明确包含建表，请使用 create_cdo 的 create_table=true 和 table_name；仅创建 CDO 不满足本次目标。')
            expected_name = task.get('required_object_name')
            if expected_name and creates and not any(op.get('name') == expected_name for op in creates):
                raise ValueError(f'对象名称未采用用户最新修正：应为 {expected_name}。')
            prior_fields = chat_context.planned_fields(task.get('prior_plan', []))
            parent_edits = re.findall(r'([A-Za-z][A-Za-z0-9_]+)\s*的父对象\s*(?:改为|改成|换成)\s*([A-Za-z][A-Za-z0-9_]+)', state.get('request_message', ''))
            for target, parent in parent_edits:
                if any(op.get('name', '').casefold() == target.casefold() and op.get('parent', '').casefold() != parent.casefold() for op in creates):
                    raise ValueError(f'用户已指定 {target} 的父对象为 {parent}，当前计划没有应用此选择；请重新生成符合要求的预览。')
            if creates and prior_fields and any(text in state.get('request_message', '') for text in ('其他不变', '其余不变', '其余要求不变', '其他要求不变')):
                if prior_fields != chat_context.planned_fields(task.get('proposed_plan', [])):
                    raise ValueError('用户要求其他不变，当前计划却遗漏或改变了原字段属性；请保留原字段、类型、长度、持久化和列表属性。')
        creation_checks = inspect_calls(pending)
        from designer.vendor import preflight_error
        rejected = [report for report in creation_checks if not report['ready']]
        if rejected:
            return _reject_plan(state, '\n'.join(preflight_error(report) for report in rejected))
    except (ValueError, TypeError, KeyError) as exc:
        return _reject_plan(state, f'参数校验未通过，尚未调用设计后端：{exc}')
    # A rejected mutation may leave high cumulative attempt counts behind.
    # Reads needed to diagnose it must not ask for mutation approval, nor mark
    # the rejected proposed plan as validated just because a read is valid.
    if pending and all(chat_intent.read_only_tool((call.get('function') or {}).get('name', '')) for call in pending):
        return {'task_context': state.get('task_context', {})}
    if not state.get('read_only') and state.get('task_context', {}).get('proposed_plan'):
        state = {**state, 'task_context': {**state['task_context'], 'validated_plan': state['task_context']['proposed_plan']}}
    design_calls = [call for call in pending if (call.get('function') or {}).get('name') in excel_preview.DESIGN_TOOLS
                    and excel_preview.fingerprint(call) not in state.get('excel_approved_calls', [])]
    needs_preview = bool((state.get('excel_sources') or state.get('task_context', {}).get('live_revision')) and design_calls)
    if state.get("approval_granted") and not needs_preview:
        return {'task_context': state.get('task_context', {})}
    decision = evaluate_mutations(_completed_counts(state), pending)
    if design_calls and any(report.get('objects') for report in creation_checks) and (
            decision.requires_approval or state.get('validation_failures', 0)):
        needs_preview = True
    if not decision.requires_approval and not needs_preview:
        return {'task_context': state.get('task_context', {})}
    preview = excel_preview.build(design_calls, state.get('excel_sources', []), creation_checks) if needs_preview else None
    approval = interrupt(
        {
            "type": "approval_required",
            "message": preview['message'] if preview else approval_prompt(decision),
            "excel_preview": preview,
            "counts": decision.totals.as_dict(),
            "categories": list(decision.categories),
        }
    )
    notes = _notes(state['username'], state['session_id'], state)
    if notes:
        state = {**state, 'messages': _merge_notes(state.get('messages', []), notes)}
        user_memories[state['username']]['sessions'][state['session_id']].get('pending_dialogue', {}).pop(_pending_key(state), None)
    if isinstance(approval, dict) and approval.get("revision") is True:
        messages = (excel_preview.attach_preview(state.get('messages', []), {**preview, 'status': 'superseded'}, pending)
                    if preview else list(state.get('messages', [])))
        # Close every old tool call before adding the new user turn. The model
        # must see valid tool-call pairs and must not retry the superseded batch.
        for call in pending:
            messages.append({
                "role": "tool", "tool_call_id": call.get('id', 'unknown'),
                "name": (call.get('function') or {}).get('name', 'unknown'),
                "content": "旧方案因用户补充要求已作废，本调用未执行。请按最新要求重新核对并生成预览。",
            })
        response = approval['response']
        revised_task = dict(state.get('task_context', {}))
        revised_task.pop('prior_plan', None)
        messages.append({'role': 'user', 'content': response})
        legacy_messages = get_user_messages(state['username'], state['session_id'])
        legacy_messages[:] = messages
        save_user_session(state['username'], state['session_id'])
        writer = get_stream_writer()
        if preview:
            writer({'type': 'excel_preview_status', 'id': preview['id'], 'status': 'superseded'})
        writer({'type': 'message_saved', 'session_id': state['session_id']})
        return {
            "messages": messages, "request_message": response, "loop_count": 0,
            "pending_tool_calls": [], "final_reply": "", "stop_reason": "replan",
            "approval_granted": False, "excel_approved_calls": [], "excel_confirmation_response": '',
            "task_context": chat_context.update_task(revised_task, response, [], reset=chat_intent.new_task(response)),
            "excel_sources": [] if chat_intent.new_task(response) else state.get('excel_sources', []),
            "validation_failures": 0,
        }
    if isinstance(approval, dict) and approval.get("approved") is True:
        if preview and approval.get('preview_id') and approval['preview_id'] != preview['id']:
            return _reject_plan(state, '创建预检的来源、SDK 或字段规则已变化，原预览已失效；请重新核对并生成预览后确认。')
        update = {"approval_granted": state.get('approval_granted', False) or decision.requires_approval,
                  "excel_confirmation_response": approval.get('response', ''), 'messages': state.get('messages', []),
                  'task_context': state.get('task_context', {})}
        if preview:
            if not excel_preview.confirmation(approval.get('response', '')):
                raise ValueError('Excel 设计需要人员输入“确定”或“确认”')
            update.update(excel_approved_calls=state.get('excel_approved_calls', []) + preview['call_fingerprints'],
                          excel_confirmation_response=approval['response'],
                          messages=excel_preview.attach_preview(state.get('messages', []), {
                              **preview, 'status': 'confirmed', 'confirmation_response': approval['response'],
                              'confirmation_received_utc': approval.get('received_utc', datetime.now(timezone.utc).isoformat()),
                          }, pending))
        return update

    cancelled_messages = (excel_preview.attach_preview(state.get('messages', []), {**preview, 'status': 'cancelled'}, pending)
                          if preview else list(state.get('messages', [])))
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
    if isinstance(approval, dict):
        cancelled_messages.append({'role': 'user', 'content': approval.get('response', '取消')})
    reply = "已取消本次 Excel 设计，尚未写入 Designer。" if preview else "已取消本次待确认操作，未继续执行被拦截的工具。"
    cancelled_messages.append({"role": "assistant", "content": reply})
    return {
        "messages": cancelled_messages,
        "pending_tool_calls": [],
        "final_reply": reply,
        "stop_reason": "cancelled",
    }


def _reject_plan(state, reason):
    messages = list(state.get('messages', []))
    if not state.get('read_only'):
        notes = _notes(state['username'], state['session_id'], state)
        messages = _merge_notes(messages, notes)
        session = user_memories[state['username']]['sessions'][state['session_id']]
        session.get('pending_dialogue', {}).pop(_pending_key(state), None)
        ids = {call.get('id') for call in state.get('pending_tool_calls', [])}
        old_preview = next((message['excel_preview'] for message in session.get('messages', [])
                            if message.get('excel_preview', {}).get('status') == 'pending'
                            and any(call.get('id') in ids for call in message.get('tool_calls', []))), None)
        if old_preview:
            messages = excel_preview.attach_preview(messages, {**old_preview, 'status': 'invalid'}, state.get('pending_tool_calls', []))
            get_stream_writer()({'type': 'excel_preview_status', 'id': old_preview['id'], 'status': 'invalid'})
    for call in state.get('pending_tool_calls', []):
        messages.append({'role': 'tool', 'tool_call_id': call.get('id', 'unknown'),
                         'name': (call.get('function') or {}).get('name', 'unknown'), 'content': reason})
    attempts = int(state.get('validation_failures', 0)) + 1
    reply = '方案连续两次未通过校验，已停止执行。' + reason if attempts >= 2 else ''
    if reply:
        messages.append({'role': 'assistant', 'content': reply})
    return {'messages': messages, 'pending_tool_calls': [], 'validation_failures': attempts,
            'approval_granted': False, 'excel_approved_calls': [], 'excel_confirmation_response': '',
            'final_reply': reply, 'stop_reason': 'validation_failed' if reply else 'replan',
            'task_context': state.get('task_context', {})}


def _attempt_key(call):
    function = call.get('function') or {}
    try:
        arguments = json.loads(function.get('arguments') or '{}')
    except (ValueError, TypeError):
        arguments = function.get('arguments')
    return json.dumps({'name': function.get('name'), 'arguments': arguments}, sort_keys=True, ensure_ascii=False)


def _route_after_policy(state: AgentState) -> str:
    if state.get("stop_reason") == "replan":
        return "agent"
    return "execute_tool" if state.get("pending_tool_calls") else END


async def _execute_tool_node(state: AgentState) -> dict[str, Any]:
    revised = _adopt_requirements(state)
    if revised:
        return revised
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
            if (state.get('excel_sources') or state.get('task_context', {}).get('live_revision')) and func_name in excel_preview.DESIGN_TOOLS:
                if excel_preview.fingerprint(tool_call) not in state.get('excel_approved_calls', []):
                    raise ValueError('Excel 设计尚未通过预览确认')
                await asyncio.to_thread(excel_preview.validate_sources, state.get('excel_sources', []), state['username'], state['session_id'])
            # Source checks may await; intake can change while they run.
            revised = _adopt_requirements(state)
            if revised:
                return revised
            tool_func = get_tool_func(func_name)
            if tool_func is None:
                result = f"Error: tool '{func_name}' not found"
            else:
                with evidence_scope(state.get('messages', [])):
                    result = await progress_store.run_tool(
                        state.get('username', 'unknown'), state.get('session_id', 'unknown'),
                        func_name, func_args, tool_call.get('id', ''), tool_func)
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

    if isinstance(result, dict) and func_name in {'generate_designer_cdo_package', 'generate_designer_design_package'} and result.get('status') == 'saved_to_test_copy_and_exported':
        from designer.review import summary
        try:
            writer({'type': 'designer_result', 'result': summary(result['files']['manifest.json'])})
        except (ValueError, KeyError, OSError):
            pass

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
    call_count = count_tool_calls([tool_call])
    # Count attempts conservatively: a failing vendor batch may have saved
    # some changes before returning an error. Never undercount mixed batches.
    creates += call_count.creates
    updates += call_count.updates
    deletes += call_count.deletes

    tool_message = {
        "role": "tool",
        "tool_call_id": tool_call.get("id", "unknown"),
        "name": func_name,
        "content": str(result),
    }
    messages = state.get('messages', []) + [tool_message]
    response = state.get('excel_confirmation_response', '')
    if not pending and response:
        messages.append({'role': 'user', 'content': response})
    return {
        "messages": messages,
        "pending_tool_calls": pending,
        "excel_approved_calls": [item for item in state.get('excel_approved_calls', []) if item != excel_preview.fingerprint(tool_call)],
        "excel_confirmation_response": response if pending else '',
        "create_count": creates,
        "update_count": updates,
        "delete_count": deletes,
        "tool_failures": state.get('tool_failures', []) + ([_attempt_key(tool_call)]
                         if str(result).startswith('Error') or isinstance(result, dict) and result.get('status') == 'failed' else []),
        "task_context": {**state.get('task_context', {}), 'latest_evidence': {
            'tool': func_name, 'evidence_id': tool_call.get('id'),
            'status': result.get('status') if isinstance(result, dict) else ('failed' if str(result).startswith('Error') else 'returned'),
            'notice': '只依据该回执报告，不将返回结果等同于数据库发布或整项设计已验收。',
        }},
    }


def _route_after_tool(state: AgentState) -> str:
    return 'requirements'


def _build_graph(checkpointer: AsyncSqliteSaver):
    builder = StateGraph(AgentState)
    builder.add_node("agent", _agent_node)
    builder.add_node('requirements', _requirements_node)
    builder.add_node("policy", _policy_node)
    builder.add_node("execute_tool", _execute_tool_node)
    builder.add_edge(START, 'requirements')
    builder.add_conditional_edges('requirements', _route_after_requirements)
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
    graph=None,
) -> AsyncIterator[str]:
    async for mode, payload in (graph or _graph).astream(
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
    if state.get("messages"):
        legacy_messages = get_user_messages(username, session_id)
        preview = (_first_interrupt_value(snapshot) or {}).get('excel_preview')
        legacy_messages[:] = (excel_preview.attach_preview(state["messages"], preview, state.get('pending_tool_calls', []))
                              if preview else state["messages"])
        if _has_interrupt(snapshot):
            legacy_messages.extend(_notes(username, session_id, state))
        save_user_session(username, session_id)
        chat_requirements.reconcile(username, session_id, legacy_messages)
    return state


async def langgraph_chat_stream(
    username: str,
    message: str,
    session_id: str | None = None,
    attachments: list[dict] | None = None,
) -> AsyncIterator[str]:
    # Requests from multiple browser tabs must not race the same checkpoint.
    loop = asyncio.get_running_loop()
    locks = _session_locks.setdefault(loop, {})
    get_user_messages(username, session_id)
    actual = _actual_session_id(username, session_id)
    lock = locks.setdefault((username, actual), asyncio.Lock())
    if lock.locked():
        yield _sse({'type': 'error', 'message': '这个对话正在处理上一条消息，请等处理结束后再发送。'})
        return
    async with lock:
        async for event in _chat_stream(username, message, actual, attachments):
            yield event


async def _pending_question(username, session_id, message, snapshot, user_record=None):
    """Use an isolated, read-only graph without resuming the pending write."""
    from langgraph.checkpoint.memory import InMemorySaver
    state = snapshot.values
    session = user_memories[username]['sessions'][session_id]
    notes = session.setdefault('pending_dialogue', {}).setdefault(_pending_key(state), [])
    previous_notes = list(notes)
    user_record = user_record or {'role': 'user', 'content': message}
    notes.append(user_record)
    await _sync_legacy_messages(username, session_id, _graph_config(username, session_id))
    yield _sse({'type': 'message_saved', 'session_id': session_id})
    preference_reply = chat_preferences.apply(username, session_id, message)
    if preference_reply:
        notes.append({'role': 'assistant', 'content': preference_reply})
        await _sync_legacy_messages(username, session_id, _graph_config(username, session_id))
        yield _sse({'type': 'done', 'reply': preference_reply})
        return
    preview = (_first_interrupt_value(snapshot) or {}).get('excel_preview')
    base = list(state.get('messages', [])[:-1]) + previous_notes + [user_record]
    base.insert(1, {'role': 'system', 'content': '当前设计仍在等待人员确认。本次只回答追问或执行只读查询，不能确认、取消或修改该方案。待确认预览数据：\n' + json.dumps(preview or state.get('pending_tool_calls', []), ensure_ascii=False)})
    graph = _build_graph(InMemorySaver())
    inquiry_config = _graph_config(username, session_id + ':question:' + str(time.time_ns()))
    inquiry_state: AgentState = {
        'messages': base, 'username': username, 'session_id': session_id, 'request_message': message,
        'read_only': True, 'loop_count': 0, 'pending_tool_calls': [], 'excel_sources': [],
        'excel_approved_calls': [], 'approval_granted': False, 'validation_failures': 0,
        'task_context': state.get('task_context', {}), 'final_reply': '', 'stop_reason': '',
        'inquiry': True,
    }
    try:
        async for event in _stream_graph(inquiry_state, inquiry_config, graph):
            yield event
        result = (await graph.aget_state(inquiry_config)).values
        notes.extend(result.get('messages', [])[len(base):])
        if result.get('stop_reason') in {'validation_failed', 'loop_limit'}:
            yield _sse({'type': 'done', 'reply': result['final_reply']})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reply = f'追问处理失败：{exc}。待确认方案保持不变。'
        notes.append({'role': 'assistant', 'content': reply})
        yield _sse({'type': 'error', 'message': reply})
    finally:
        await _sync_legacy_messages(username, session_id, _graph_config(username, session_id))


async def continue_queued_chat_stream(username, session_id):
    """Cover input accepted just as the previous graph ends or pauses."""
    graph_config = _graph_config(username, session_id)
    snapshot = await _graph.aget_state(graph_config)
    queued = chat_requirements.pending(username, session_id)
    if not queued:
        return
    if _has_interrupt(snapshot) and not any(item['kind'] == 'revision' for item in queued):
        for item in chat_requirements.claim(username, session_id):
            async for event in _pending_question(username, session_id, item['content'], snapshot,
                                                 chat_requirements.user_record(item)):
                yield event
        return
    graph_input = (Command(resume={'revision': True, 'response': '\n'.join(item['content'] for item in queued)})
                   if _has_interrupt(snapshot) else snapshot.values)
    async for event in _stream_graph(graph_input, graph_config):
        yield event
    final = await _graph.aget_state(graph_config)
    await _sync_legacy_messages(username, session_id, graph_config)
    if _has_interrupt(final):
        value = _first_interrupt_value(final) or {}
        yield _sse({'type': 'done', 'reply': value.get('message', '请核对后确认。'), 'excel_preview': value.get('excel_preview')})


async def _chat_stream(username, message, session_id=None, attachments=None):
    if _graph is None:
        raise RuntimeError("LangGraph runtime has not been initialized")

    chat_messages = get_user_messages(username, session_id)
    actual_session_id = _actual_session_id(username, session_id)
    if progress_store.background_running(username, actual_session_id):
        yield _sse({'type': 'done', 'reply': '上次后台操作仍在完成，请在设计进度中查看结果，完成后再继续。'})
        return
    progress_store.recover_chat_results(username, actual_session_id, chat_messages)
    is_first_message = not any(item.get('role') == 'user' for item in chat_messages)
    graph_config = _graph_config(username, actual_session_id)

    snapshot = await _graph.aget_state(graph_config)
    if _has_interrupt(snapshot):
        interrupt_value = _first_interrupt_value(snapshot) or {}
        if attachments:
            yield _sse({"type": "error", "message": "当前设计正在等待确认，请先确认或取消，再发送新的附件。"})
            return
        confirmed = excel_preview.confirmation(message) if interrupt_value.get('excel_preview') else is_explicit_confirmation(message)
        if confirmed:
            received_utc = datetime.now(timezone.utc).isoformat()
            session = user_memories[username]['sessions'][actual_session_id]
            session.setdefault('chat_events', []).append({
                'type': 'confirmation_received', 'received_utc': received_utc, 'response': message,
                'preview_id': (interrupt_value.get('excel_preview') or {}).get('id'),
                'call_fingerprints': [excel_preview.fingerprint(call) for call in snapshot.values.get('pending_tool_calls', [])],
            })
            save_user_session(username, actual_session_id)
            try:
                if interrupt_value.get('excel_preview'):
                    await asyncio.to_thread(excel_preview.validate_sources, interrupt_value['excel_preview']['attachments'], username, actual_session_id)
                async for event in _stream_graph(
                    Command(resume={"approved": True, "response": message,
                                    "preview_id": (interrupt_value.get('excel_preview') or {}).get('id'),
                                    "received_utc": received_utc}),
                    graph_config,
                ):
                    yield event
                if interrupt_value.get('excel_preview'):
                    approved_snapshot = await _graph.aget_state(graph_config)
                    approved_preview = next((item.get('excel_preview') for item in approved_snapshot.values.get('messages', [])
                                             if item.get('excel_preview', {}).get('id') == interrupt_value['excel_preview']['id']), None)
                    if approved_preview and approved_preview.get('status') == 'confirmed':
                        yield _sse({'type': 'excel_preview_status', 'id': approved_preview['id'],
                                    'status': 'confirmed', 'confirmation_received_utc': received_utc})
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
                if interrupt_value.get('excel_preview'):
                    yield _sse({'type': 'excel_preview_status', 'id': interrupt_value['excel_preview']['id'], 'status': 'cancelled'})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                yield _sse({"type": "error", "message": f"工作流取消失败: {exc}"})
                return
        elif is_explicit_confirmation(message) or message.strip(' \t\r\n，。！？、,.!?;；:：') in {'', '好的', '好'}:
            prompt = interrupt_value.get(
                "message",
                "当前有操作等待确认。请回复明确的确认指令，或回复“取消执行”。",
            )
            yield _sse({"type": "done", "reply": prompt, 'excel_preview': interrupt_value.get('excel_preview')})
            return
        else:
            if chat_intent.pending_intent(message) != 'revision':
                async for event in _pending_question(username, actual_session_id, message, snapshot):
                    yield event
                return
            try:
                async for event in _stream_graph(
                    Command(resume={"revision": True, "response": message}), graph_config,
                ):
                    yield event
            except asyncio.CancelledError:
                await _sync_legacy_messages(username, actual_session_id, graph_config)
                raise
            except Exception as exc:
                await _sync_legacy_messages(username, actual_session_id, graph_config)
                yield _sse({"type": "error", "message": f"工作流调整失败: {exc}"})
                return
    else:
        record = {"role": "user", "content": message + attachment_context(attachments or [])}
        if attachments:
            record['display_content'] = message
            record['attachments'] = [attachment_summary(item) for item in attachments]
        chat_messages.append(record)
        save_user_session(username, actual_session_id)
        yield _sse({"type": "message_saved", "session_id": actual_session_id})
        preference_reply = chat_preferences.apply(username, actual_session_id, message)
        if preference_reply:
            chat_messages.append({'role': 'assistant', 'content': preference_reply})
            save_user_session(username, actual_session_id)
            yield _sse({'type': 'done', 'reply': preference_reply})
            return

        if is_first_message and actual_session_id != "unknown":
            # Persist a useful name before network latency or a cancelled turn.
            initial_title = first_message_title(message)
            update_session_title(username, actual_session_id, initial_title)
            yield _sse({'type': 'title_update', 'title': initial_title, 'session_id': actual_session_id})
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

        task = dict(snapshot.values.get('task_context') or chat_context.update_task({}, '', chat_messages[:-1]))
        task.pop('prior_plan', None)
        reset = chat_intent.new_task(message) or bool(attachments)
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
            "excel_sources": (attachments and [attachment_summary(item) for item in attachments]) or
                             ([] if reset else snapshot.values.get('excel_sources', excel_preview.sources(chat_messages))),
            "excel_approved_calls": [],
            "excel_confirmation_response": '',
            "task_context": chat_context.update_task(task, message, [], reset=reset),
            "validation_failures": 0, "read_only": False,
            'inquiry': False,
            "tool_failures": [] if reset else snapshot.values.get('tool_failures', []),
        }
        if initial_state['excel_sources']:
            initial_state['task_context']['attachment_evidence'] = [
                {'evidence_id': 'attachment-' + item['id'], 'name': item['name'], 'sha256': item['sha256']}
                for item in initial_state['excel_sources']
            ]
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
        await _sync_legacy_messages(username, actual_session_id, graph_config)
        interrupt_value = _first_interrupt_value(final_snapshot) or {}
        yield _sse(
            {
                "type": "done",
                "reply": interrupt_value.get(
                    "message", "操作已暂停，请明确确认或取消。"
                ),
                "excel_preview": interrupt_value.get('excel_preview'),
            }
        )
        return

    final_state = await _sync_legacy_messages(
        username, actual_session_id, graph_config
    )
    reply = final_state.get("final_reply", "")
    if reply and final_state.get("stop_reason") in {"loop_limit", "cancelled", "validation_failed"}:
        yield _sse({"type": "done", "reply": reply})
    elif not reply:
        yield _sse({"type": "done", "reply": "任务已完成。"})
