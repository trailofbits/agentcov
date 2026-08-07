from __future__ import annotations

import json
from typing import Any


def payloads_from_codex_record(record: dict[str, Any]) -> list[dict[str, Any]]:
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
