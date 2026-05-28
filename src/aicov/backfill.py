from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .hooks import events_from_payload


def backfill_codex_session(
    *,
    session_id: str | None = None,
    transcript_path: Path | None = None,
) -> tuple[Path, list[Any]]:
    path = transcript_path or _find_session(session_id)
    events = []
    root = Path.cwd().resolve()
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            for payload in _payloads_from_record(record):
                root, parsed_events = events_from_payload(payload, source="transcript-backfill")
                events.extend(parsed_events)
    return root, events


def _find_session(session_id: str | None) -> Path:
    if not session_id:
        raise ValueError("provide --session-id or --path")
    sessions_root = Path.home() / ".codex" / "sessions"
    matches = sorted(sessions_root.rglob(f"*{session_id}*.jsonl"))
    if not matches:
        raise FileNotFoundError(f"no Codex session JSONL found for {session_id!r}")
    return matches[-1]


def _payloads_from_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    if _looks_like_tool_payload(record):
        payloads.append(record)
    for key in ("item", "payload"):
        child = record.get(key)
        if isinstance(child, dict):
            payloads.extend(_payloads_from_child_record(record, child))
    return payloads


def _looks_like_tool_payload(record: dict[str, Any]) -> bool:
    return any(key in record for key in ("tool_name", "tool_input", "command", "cmd"))


def _payloads_from_child_record(
    record: dict[str, Any],
    child: dict[str, Any],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    if _looks_like_tool_payload(child):
        payloads.append({**record, **child})
    name = child.get("name")
    arguments = child.get("arguments")
    if isinstance(name, str) and arguments is not None:
        payloads.append(
            {
                **record,
                "tool_name": name,
                "tool_input": _parse_tool_arguments(arguments),
                "tool_response": child.get("output") or record.get("output"),
                "tool_use_id": child.get("call_id") or record.get("tool_use_id"),
            }
        )
    return payloads


def _parse_tool_arguments(arguments: object) -> object:
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return {"command": arguments}
    return arguments
