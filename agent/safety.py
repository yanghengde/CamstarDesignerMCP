"""Agent tool-call safety policy.

This module is intentionally independent from the LLM and LangGraph runtime so
the same rules can be unit-tested and reused by other entry points.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

from config import (
    SAFE_CREATE_THRESHOLD,
    SAFE_DELETE_THRESHOLD,
    SAFE_UPDATE_THRESHOLD,
)


CREATE_TOOL_NAMES = {"container_start"}
UPDATE_TOOL_NAMES = {
    "container_move",
    "container_move_in",
    "container_move_out",
    "container_defect",
    "rework",
    "execute_query_inquiry_event",
}


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
    if name.startswith("create_") or name in CREATE_TOOL_NAMES:
        return "create"
    if name.startswith(("update_", "patch_", "rebuild_")) or name in UPDATE_TOOL_NAMES:
        return "update"
    if name.startswith("delete_"):
        return "delete"
    return None


def _argument_item_count(arguments: str) -> int:
    """Estimate how many records a single batch-shaped tool call affects."""
    try:
        payload = json.loads(arguments or "{}")
    except (TypeError, json.JSONDecodeError):
        return 1

    if not isinstance(payload, dict):
        return 1

    for key in ("container_names", "items", "records", "entities"):
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
        category = classify_tool(str(function.get("name") or ""))
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
    totals = completed + count_tool_calls(pending_tool_calls)
    if intended is not None:
        totals = MutationCounts(
            creates=max(totals.creates, intended.creates),
            updates=max(totals.updates, intended.updates),
            deletes=max(totals.deletes, intended.deletes),
        )
    categories: list[str] = []
    if totals.creates > SAFE_CREATE_THRESHOLD:
        categories.append("创建")
    if totals.updates > SAFE_UPDATE_THRESHOLD:
        categories.append("修改")
    if totals.deletes > SAFE_DELETE_THRESHOLD:
        categories.append("删除")
    return SafetyDecision(bool(categories), tuple(categories), totals)


def infer_intended_mutations(
    message: str,
    pending_tool_calls: Iterable[dict],
) -> MutationCounts:
    """Conservatively infer an explicitly stated Container creation count.

    This protects sequential tool planners that emit one Start call at a time:
    an explicit request for 50 independent Containers must be approved before
    the first write, not after call 20.
    """
    calls = list(pending_tool_calls)
    if not any(
        (call.get("function") or {}).get("name") == "container_start"
        for call in calls
    ):
        return MutationCounts()

    normalized = (message or "").casefold()
    patterns = (
        r"(?:生成|创建|启动|start)?\s*(\d{1,6})\s*(?:个|条)?\s*(?:独立的?)?\s*(?:container|容器|序列号)",
        r"(?:container|容器|序列号).*?(\d{1,6})\s*(?:个|条)",
    )
    counts = [
        int(match.group(1))
        for pattern in patterns
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE)
    ]

    range_match = re.search(
        r"(?:sn)?(\d{3,})\s*(?:~|～|-|至|到)\s*(?:sn)?(\d{3,})",
        normalized,
        flags=re.IGNORECASE,
    )
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        if end >= start:
            counts.append(end - start + 1)

    return MutationCounts(creates=max(counts, default=0))


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
        f"其中“{actions}”已超过安全阈值。操作已暂停且尚未继续执行。\n\n"
        "请回复明确指令（例如“确认创建”“确认修改”或“确认删除”）继续，"
        "或回复“取消执行”。"
    )
