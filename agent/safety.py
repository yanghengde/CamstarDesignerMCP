"""Agent tool-call safety policy.

This module is intentionally independent from the LLM and LangGraph runtime so
the same rules can be unit-tested and reused by other entry points.
"""

from __future__ import annotations

import json
import re
import hashlib
import time
from dataclasses import dataclass
from typing import Iterable

from config import (
    SAFE_CREATE_THRESHOLD,
    SAFE_DELETE_THRESHOLD,
    SAFE_UPDATE_THRESHOLD,
)


@dataclass(frozen=True)
class MutationCounts:
    creates: int = 0
    updates: int = 0
    deletes: int = 0

    def __add__(self, other: "MutationCounts") -> "MutationCounts":
        return MutationCounts(
            creates=self.creates + other.creates,
            updates=self.updates + other.updates,
            deletes=self.deletes + other.deletes,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "creates": self.creates,
            "updates": self.updates,
            "deletes": self.deletes,
        }


@dataclass(frozen=True)
class SafetyDecision:
    requires_approval: bool
    categories: tuple[str, ...]
    totals: MutationCounts


def classify_tool(name: str) -> str | None:
    """Return the mutation category for a tool, or ``None`` if read-only."""
    effects = {
        'restore_designer_test_database': 'delete',
        'restore_designer_mdb_backup': 'update',
        'sync_designer_working_file': 'update',
        'publish_designer_test_database': 'update',
    }
    if name in effects:
        return effects[name]
    if name.startswith("create_"):
        return "create"
    if name.startswith(("update_", "patch_", "rebuild_")):
        return "update"
    if name.startswith("delete_"):
        return "delete"
    return None


CRITICAL_TOOLS = {'restore_designer_test_database'}


def approval_key(call: dict, username: str, session_id: str) -> str:
    """Bind a one-use grant to the exact invocation and authenticated session."""
    function = call.get('function') or {}
    try:
        args = json.loads(function.get('arguments') or '{}')
    except (TypeError, ValueError):
        args = function.get('arguments')
    value = [username, session_id, call.get('id'), function.get('name'), args]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def approved_call(call, state) -> bool:
    key = approval_key(call, state.get('username', ''), state.get('session_id', ''))
    return any(grant.get('key') == key and grant.get('expires', 0) > time.time()
               for grant in state.get('approval_grants', []))


def grant_calls(calls, state):
    return [{'key': approval_key(call, state.get('username', ''), state.get('session_id', '')),
             'expires': time.time() + 1800} for call in calls]


def _argument_item_count(arguments: str) -> int:
    """Estimate how many records a single batch-shaped tool call affects."""
    try:
        payload = json.loads(arguments or "{}")
    except (TypeError, json.JSONDecodeError):
        return 1

    if not isinstance(payload, dict):
        return 1

    for key in ("items", "records", "entities"):
        value = payload.get(key)
        if isinstance(value, list):
            return max(1, len(value))

    count = payload.get("count")
    if isinstance(count, int) and count > 0:
        return count
    return 1


def count_tool_calls(tool_calls: Iterable[dict]) -> MutationCounts:
    creates = updates = deletes = 0
    for tool_call in tool_calls:
        function = tool_call.get("function") or {}
        name = str(function.get('name') or '')
        if name in {'generate_designer_design_package', 'generate_designer_cdo_package'}:
            from designer.operations import CREATE_ACTIONS, UPDATE_ACTIONS, DELETE_ACTIONS
            try:
                payload = json.loads(function.get('arguments') or '{}')
            except (TypeError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict): payload = {}
            if name == 'generate_designer_cdo_package':
                fields = payload.get('fields') or []
                if not isinstance(fields, list): fields = []
                creates += 1 + len(fields) + sum(isinstance(field, dict) and not field.get('field_type') for field in fields)
                # Without metadata, reserve a create for a possible maintenance
                # companion. The vendor preview lists the actual paired fields.
                creates += sum(isinstance(field, dict) and (field.get('portal_editable') is True or
                               ('portal_editable' not in field and field.get('persistent', True) is True)) for field in fields)
            else:
                operations = payload.get('operations') or []
                if not isinstance(operations, list): operations = []
                for op in operations:
                    action = op.get('action') if isinstance(op, dict) else ''
                    creates += action in CREATE_ACTIONS
                    creates += action == 'add_field' and (op.get('portal_editable') is True or
                               ('portal_editable' not in op and op.get('persistent') is True))
                    updates += action in UPDATE_ACTIONS
                    deletes += action in DELETE_ACTIONS
                    # Reserve the linked maintenance mutation before SDK preview.
                    updates += action == 'change_field_type' or (action == 'patch' and op.get('kind') == 'field')
                    deletes += action == 'delete' and op.get('kind') == 'field'
            continue
        if name in {'sync_designer_working_file', 'restore_designer_mdb_backup'}:
            updates += 1
            continue
        category = classify_tool(name)
        amount = _argument_item_count(str(function.get("arguments") or "{}"))
        if category == "create":
            creates += amount
        elif category == "update":
            updates += amount
        elif category == "delete":
            deletes += amount
    return MutationCounts(creates, updates, deletes)


def evaluate_mutations(
    completed: MutationCounts,
    pending_tool_calls: Iterable[dict],
    intended: MutationCounts | None = None,
) -> SafetyDecision:
    pending_tool_calls = list(pending_tool_calls)
    mutations = count_tool_calls(pending_tool_calls)
    totals = completed + mutations
    if intended is not None:
        totals = MutationCounts(
            creates=max(totals.creates, intended.creates),
            updates=max(totals.updates, intended.updates),
            deletes=max(totals.deletes, intended.deletes),
        )
    if mutations == MutationCounts() and intended is None:
        return SafetyDecision(False, (), totals)
    categories: list[str] = []
    if totals.creates > SAFE_CREATE_THRESHOLD:
        categories.append("创建")
    if totals.updates > SAFE_UPDATE_THRESHOLD:
        categories.append("修改")
    if totals.deletes > SAFE_DELETE_THRESHOLD:
        categories.append("删除")
    if any((call.get('function') or {}).get('name') in CRITICAL_TOOLS for call in pending_tool_calls):
        categories.append('数据库恢复（覆盖当前数据库并断开连接）')
    return SafetyDecision(bool(categories), tuple(categories), totals)


def approval_details(calls):
    """Show the concrete destructive target alongside the generic count prompt."""
    import config
    lines = []
    for call in calls:
        function = call.get('function') or {}
        if function.get('name') in CRITICAL_TOOLS:
            args = json.loads(function.get('arguments') or '{}')
            lines.append(f"恢复目标：{config.DESIGNER_DB_SERVER}/{config.DESIGNER_DB_NAME}；"
                         f"备份凭证：{args.get('backup_receipt')}；"
                         f"凭证 SHA256：{args.get('expected_receipt_sha256')}。"
                         '执行将覆盖目标库并断开现有连接。')
    return '\n'.join(lines)


def is_explicit_confirmation(text: str) -> bool:
    """Accept an explicit approval only; substrings such as “不是” never pass."""
    normalized = re.sub(r"[\s，。！？、,.!?;；:：]+", "", (text or "").casefold())
    if not normalized:
        return False

    negative_markers = ("不", "否", "取消", "停止", "拒绝", "no", "cancel", "stop")
    if any(marker in normalized for marker in negative_markers):
        return False

    exact = {
        "确认",
        "确定",
        "同意",
        "批准",
        "继续",
        "继续执行",
        "确认继续",
        "确认执行",
        "确认创建",
        "确认修改",
        "确认删除",
        "确认导入",
        "确定导入",
        "确定创建",
        "确定执行",
        "ok",
        "okay",
        "yes",
        "y",
    }
    return normalized in exact


def is_explicit_rejection(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？、,.!?;；:：]+", "", (text or "").casefold())
    if not normalized:
        return False
    return normalized in {
        "不",
        "否",
        "不确认",
        "取消",
        "取消执行",
        "停止",
        "停止执行",
        "拒绝",
        "no",
        "n",
        "cancel",
        "stop",
    }


def approval_prompt(decision: SafetyDecision) -> str:
    actions = "、".join(decision.categories)
    totals = decision.totals
    return (
        f"⚠️ 安全确认：当前任务累计涉及 {totals.creates} 条创建、"
        f"{totals.updates} 条修改、{totals.deletes} 条删除，"
        f"其中“{actions}”需要本次操作的确认。操作已暂停且尚未继续执行。\n\n"
        "请回复明确指令（例如“确认创建”“确认修改”或“确认删除”）继续，"
        "或回复“取消执行”。"
    )
