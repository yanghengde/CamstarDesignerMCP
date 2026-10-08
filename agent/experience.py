"""Reviewed experience store for safe, incremental agent improvement.

Runtime failures are collected as *candidates*. They never become instructions
until a human approves them with an explicit resolution. Approved experience is
then injected as read-only context on later related requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import EXPERIENCE_DB


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTAINER_START_SKILL_DIR = (
    PROJECT_ROOT / ".codex" / "skills" / "camstar-container-start"
)

_ERROR_MARKERS = (
    "❌",
    "error",
    "failed",
    "exception",
    "timed out",
    "write access denied",
    "requires input",
    "does not exist",
    "not found",
)
_SECRET_PATTERN = re.compile(
    r"(?i)(password|token|authorization|api[_-]?key|otp)\s*[:=]\s*[^\s,;]+"
)
_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_VOLATILE_PATTERN = re.compile(
    r"\b(?:[0-9a-f]{8}-[0-9a-f-]{27,}|\d{3,})\b", re.IGNORECASE
)


def _connect() -> sqlite3.Connection:
    db_path = Path(EXPERIENCE_DB)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS experience_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            session_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            argument_keys TEXT NOT NULL,
            outcome TEXT NOT NULL,
            result_excerpt TEXT NOT NULL,
            candidate_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS experience_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT NOT NULL UNIQUE,
            tool_name TEXT NOT NULL,
            symptom TEXT NOT NULL,
            occurrences INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            resolution TEXT,
            evidence TEXT,
            revalidate INTEGER NOT NULL DEFAULT 1,
            reviewed_at TEXT
        );
        """
    )
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sanitize(text: str, limit: int = 1200) -> str:
    value = _SECRET_PATTERN.sub(r"\1=<redacted>", text)
    value = _URL_PATTERN.sub("<url>", value)
    return value[:limit]


def _is_failure(result: str) -> bool:
    normalized = result.casefold()
    return any(marker.casefold() in normalized for marker in _ERROR_MARKERS)


def _fingerprint(tool_name: str, symptom: str) -> str:
    normalized = _VOLATILE_PATTERN.sub("<value>", symptom.casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(f"{tool_name}:{normalized}".encode("utf-8")).hexdigest()


def record_tool_outcome(
    *,
    session_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None,
    result: Any,
) -> int | None:
    """Record a sanitized event and return a pending candidate id on failure."""
    raw_result = str(result)
    outcome = "failure" if _is_failure(raw_result) else "success"
    result_text = _sanitize(raw_result) if outcome == "failure" else "success"
    argument_keys = sorted((arguments or {}).keys())
    now = _now()
    candidate_id: int | None = None

    with _connect() as connection:
        if outcome == "failure":
            fingerprint = _fingerprint(tool_name, result_text)
            connection.execute(
                """
                INSERT INTO experience_candidates (
                    fingerprint, tool_name, symptom, occurrences,
                    first_seen, last_seen, status
                ) VALUES (?, ?, ?, 1, ?, ?, 'pending')
                ON CONFLICT(fingerprint) DO UPDATE SET
                    occurrences = occurrences + 1,
                    last_seen = excluded.last_seen,
                    symptom = excluded.symptom
                """,
                (fingerprint, tool_name, result_text, now, now),
            )
            candidate_id = connection.execute(
                "SELECT id FROM experience_candidates WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()["id"]

        connection.execute(
            """
            INSERT INTO experience_events (
                created_at, session_id, tool_name, argument_keys,
                outcome, result_excerpt, candidate_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                session_id,
                tool_name,
                json.dumps(argument_keys, ensure_ascii=False),
                outcome,
                result_text,
                candidate_id,
            ),
        )
    return candidate_id


def list_candidates(status: str = "pending", limit: int = 100) -> list[dict]:
    if status not in {"pending", "approved", "rejected", "all"}:
        raise ValueError("status must be pending, approved, rejected, or all")
    limit = max(1, min(limit, 500))
    query = "SELECT * FROM experience_candidates"
    params: list[Any] = []
    if status != "all":
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY last_seen DESC LIMIT ?"
    params.append(limit)
    with _connect() as connection:
        return [dict(row) for row in connection.execute(query, params)]


def review_candidate(
    candidate_id: int,
    *,
    status: str,
    resolution: str | None = None,
    evidence: str | None = None,
    revalidate: bool = True,
) -> None:
    """Approve or reject a candidate through an explicit administrative action."""
    if status not in {"approved", "rejected"}:
        raise ValueError("status must be approved or rejected")
    if status == "approved" and not (resolution or "").strip():
        raise ValueError("resolution is required when approving a candidate")

    with _connect() as connection:
        cursor = connection.execute(
            """
            UPDATE experience_candidates
            SET status = ?, resolution = ?, evidence = ?, revalidate = ?,
                reviewed_at = ?
            WHERE id = ?
            """,
            (
                status,
                (resolution or "").strip() or None,
                (evidence or "").strip() or None,
                int(revalidate),
                _now(),
                candidate_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"candidate {candidate_id} was not found")


def get_experience_status() -> dict[str, int]:
    with _connect() as connection:
        counts = {
            row["status"]: row["count"]
            for row in connection.execute(
                "SELECT status, COUNT(*) AS count FROM experience_candidates GROUP BY status"
            )
        }
        events = connection.execute(
            "SELECT COUNT(*) AS count FROM experience_events"
        ).fetchone()["count"]
    return {
        "events": events,
        "pending": counts.get("pending", 0),
        "approved": counts.get("approved", 0),
        "rejected": counts.get("rejected", 0),
    }


def _is_container_start_context(message: str, tool_names: set[str]) -> bool:
    if "container_start" in tool_names:
        return True
    normalized = message.casefold()
    keywords = (
        "container start",
        "container_start",
        "序列号",
        "容器启动",
        "启动container",
        "启动 container",
        "自动编号",
    )
    return any(keyword in normalized for keyword in keywords)


def _read_skill_reference(name: str) -> str:
    path = CONTAINER_START_SKILL_DIR / "references" / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def build_runtime_experience_context(
    message: str,
    tool_names: set[str] | None = None,
) -> str:
    """Build reviewed, relevant context without changing persisted chat history."""
    tool_names = tool_names or set()
    sections: list[str] = []
    is_start_context = _is_container_start_context(message, tool_names)
    if is_start_context:
        contract = _read_skill_reference("start-contract.md")
        failures = _read_skill_reference("known-failures.md")
        if contract or failures:
            sections.append(
                "The following project rules are verified Container Start "
                "knowledge. Follow them over generic assumptions:\n"
                f"{contract}\n{failures}"
            )

    approved = list_candidates("approved", limit=50)
    relevant_rules = []
    for item in approved:
        tool_name = item["tool_name"]
        if tool_names and tool_name not in tool_names:
            continue
        if not tool_names:
            if not (is_start_context and tool_name == "container_start"):
                continue
        relevant_rules.append(
            f"- Tool {tool_name}, approved candidate #{item['id']}: "
            f"{item['resolution']}"
            + (" and revalidate it in the live context." if item["revalidate"] else ".")
        )
    if relevant_rules:
        sections.append(
            "Human-approved operational experience:\n" + "\n".join(relevant_rules)
        )
    return "\n\n".join(sections)


def _print_candidates(status: str) -> None:
    rows = list_candidates(status=status)
    if not rows:
        print("No experience candidates found.")
        return
    for row in rows:
        print(
            f"#{row['id']} [{row['status']}] {row['tool_name']} "
            f"occurrences={row['occurrences']}\n  {row['symptom']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Review agent experience candidates")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument(
        "--status", choices=["pending", "approved", "rejected", "all"], default="pending"
    )

    approve_parser = subparsers.add_parser("approve")
    approve_parser.add_argument("candidate_id", type=int)
    approve_parser.add_argument("--resolution", required=True)
    approve_parser.add_argument("--evidence")
    approve_parser.add_argument("--no-revalidate", action="store_true")

    reject_parser = subparsers.add_parser("reject")
    reject_parser.add_argument("candidate_id", type=int)
    reject_parser.add_argument("--evidence")

    args = parser.parse_args()
    if args.command == "list":
        _print_candidates(args.status)
    elif args.command == "approve":
        review_candidate(
            args.candidate_id,
            status="approved",
            resolution=args.resolution,
            evidence=args.evidence,
            revalidate=not args.no_revalidate,
        )
        print(f"Approved experience candidate #{args.candidate_id}.")
    else:
        review_candidate(
            args.candidate_id,
            status="rejected",
            evidence=args.evidence,
        )
        print(f"Rejected experience candidate #{args.candidate_id}.")


if __name__ == "__main__":
    main()
