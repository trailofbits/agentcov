from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_READ_TOOL_NAMES = {"Read", "NotebookRead"}


@dataclass(frozen=True)
class _ClaudeToolUse:
    parent_uuid: str | None
    tool_use_id: str | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class _ClaudeToolResult:
    tool_use_id: str | None
    value: object
    is_error: bool = False


def payloads_from_claude_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tool_uses: list[_ClaudeToolUse] = []
    results_by_parent_uuid: dict[str, list[_ClaudeToolResult]] = {}
    results_by_tool_use_id: dict[str, _ClaudeToolResult] = {}

    for record in _claude_records(records):
        parent_uuid = _string(record.get("parentUuid"))
        for result in _claude_results_from_record(record):
            if result.tool_use_id:
                results_by_tool_use_id[result.tool_use_id] = result
            if parent_uuid:
                results_by_parent_uuid.setdefault(parent_uuid, []).append(result)
        tool_uses.extend(_claude_tool_uses_from_record(record))

    payloads: list[dict[str, Any]] = []
    for tool_use in tool_uses:
        payload = dict(tool_use.payload)
        result = _matching_claude_result(tool_use, results_by_tool_use_id, results_by_parent_uuid)
        if result is not None:
            tool_response = _normalize_claude_grep_response(payload, result.value)
            tool_input = _cap_claude_read_input(
                payload.get("tool_name"),
                payload.get("tool_input"),
                result,
            )
            if tool_input is None:
                continue
            payload["tool_response"] = tool_response
            payload["tool_input"] = tool_input
        else:
            tool_input = _unpaired_claude_tool_input(
                payload.get("tool_name"),
                payload.get("tool_input"),
            )
            if tool_input is None:
                continue
            payload["tool_input"] = tool_input
        payloads.append(payload)
    return payloads


def looks_like_claude_transcript(records: list[dict[str, Any]]) -> bool:
    for record in _claude_records(records):
        if _claude_tool_uses_from_record(record):
            return True
    return any(
        isinstance(record.get("message"), dict) and isinstance(record.get("sessionId"), str)
        for record in records
    )


def _matching_claude_result(
    tool_use: _ClaudeToolUse,
    results_by_tool_use_id: dict[str, _ClaudeToolResult],
    results_by_parent_uuid: dict[str, list[_ClaudeToolResult]],
) -> _ClaudeToolResult | None:
    if tool_use.tool_use_id:
        result = results_by_tool_use_id.get(tool_use.tool_use_id)
        if result is not None:
            return result
    if tool_use.parent_uuid:
        parent_results = results_by_parent_uuid.get(tool_use.parent_uuid, [])
        if len(parent_results) == 1:
            return parent_results[0]
    return None


def _unpaired_claude_tool_input(tool_name: object, tool_input: object) -> object | None:
    if tool_name not in _READ_TOOL_NAMES:
        return tool_input
    if not isinstance(tool_input, dict):
        return None
    return tool_input if _has_bounded_read_input(tool_input) else None


def _claude_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for record in records:
        normalized.append(record)
        data = record.get("data")
        child = data.get("message") if isinstance(data, dict) else None
        if isinstance(child, dict):
            merged = dict(record)
            merged.update(child)
            for key in ("cwd", "sessionId", "gitBranch", "version", "agentId", "slug"):
                if key not in merged and key in record:
                    merged[key] = record[key]
                if key not in merged and key in data:
                    merged[key] = data[key]
            if "prompt" not in merged and "prompt" in data:
                merged["prompt"] = data["prompt"]
            normalized.append(merged)
    return normalized


def _claude_tool_uses_from_record(record: dict[str, Any]) -> list[_ClaudeToolUse]:
    if record.get("type") != "assistant":
        return []
    parent_uuid = _string(record.get("uuid"))
    tool_uses: list[_ClaudeToolUse] = []
    for block in _claude_message_content(record):
        if block.get("type") != "tool_use":
            continue
        tool_name = _string(block.get("name"))
        tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
        tool_use_id = _string(block.get("id"))
        payload = {
            "session_id": _string(record.get("sessionId")),
            "turn_id": _string(record.get("uuid")),
            "tool_use_id": tool_use_id,
            "timestamp": _string(record.get("timestamp")),
            "cwd": _string(record.get("cwd")),
            "tool_name": tool_name,
            "tool_input": _claude_tool_input(tool_name, tool_input),
            "subagent": _string(record.get("agentId")),
            "user_request": _string(record.get("prompt")),
        }
        tool_uses.append(
            _ClaudeToolUse(
                parent_uuid=parent_uuid,
                tool_use_id=tool_use_id,
                payload={key: value for key, value in payload.items() if value is not None},
            )
        )
    return tool_uses


def _claude_results_from_record(record: dict[str, Any]) -> list[_ClaudeToolResult]:
    if record.get("type") != "user":
        return []
    fallback_result = record.get("toolUseResult")
    results = []
    for block in _claude_message_content(record):
        if block.get("type") != "tool_result":
            continue
        result = fallback_result if fallback_result is not None else block.get("content")
        results.append(
            _ClaudeToolResult(
                tool_use_id=_string(block.get("tool_use_id")),
                value=result,
                is_error=bool(block.get("is_error")),
            )
        )
    if not results and fallback_result is not None:
        results.append(
            _ClaudeToolResult(
                tool_use_id=None,
                value=fallback_result,
                is_error=_looks_like_claude_error(fallback_result),
            )
        )
    return results


def _claude_message_content(record: dict[str, Any]) -> list[dict[str, Any]]:
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [item for item in content if isinstance(item, dict)]


def _claude_tool_input(tool_name: str | None, tool_input: object) -> object:
    if tool_name in _READ_TOOL_NAMES and isinstance(tool_input, dict):
        normalized = dict(tool_input)
        offset = normalized.pop("offset", None)
        if offset is not None and "start_line" not in normalized and "line_start" not in normalized:
            normalized["start_line"] = offset
        return normalized
    if tool_name == "Grep" and isinstance(tool_input, dict):
        return {
            "command": _claude_grep_command(tool_input),
            "_aicov_grep_path": _string(tool_input.get("path")),
        }
    return tool_input


def _cap_claude_read_input(
    tool_name: object, tool_input: object, result: _ClaudeToolResult
) -> object | None:
    if tool_name not in _READ_TOOL_NAMES or not isinstance(tool_input, dict):
        return tool_input
    if result.is_error or _looks_like_claude_error(result.value):
        return None
    bounds = _claude_read_result_bounds(result.value)
    if bounds is None:
        return tool_input if _has_bounded_read_input(tool_input) else None
    start, end = bounds
    if end < start:
        return tool_input
    normalized = dict(tool_input)
    normalized["start_line"] = start
    normalized["end_line"] = end
    normalized.pop("offset", None)
    normalized.pop("limit", None)
    file_path = _claude_read_result_path(result.value)
    if file_path:
        normalized.setdefault("file_path", file_path)
    return normalized


def _has_bounded_read_input(tool_input: dict[str, Any]) -> bool:
    return any(tool_input.get(key) is not None for key in ("end_line", "line_end", "limit"))


def _normalize_claude_grep_response(payload: dict[str, Any], result: object) -> object:
    if payload.get("tool_name") != "Grep":
        return result
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return result
    raw_path = _string(tool_input.get("_aicov_grep_path"))
    if not raw_path:
        return result
    if not _looks_like_single_file_path(raw_path, _string(payload.get("cwd"))):
        return result
    text = _response_text(result)
    if not text:
        return result
    lines = []
    changed = False
    for line in text.splitlines():
        if re.match(r"^\d+(?::|-)", line):
            lines.append(f"{raw_path}:{line}")
            changed = True
        else:
            lines.append(line)
    return "\n".join(lines) if changed else result


def _looks_like_single_file_path(raw_path: str, cwd: str | None) -> bool:
    path = Path(raw_path).expanduser()
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    if path.is_file():
        return True
    if path.exists():
        return False
    return bool(Path(raw_path).suffix)


def _claude_read_result_bounds(result: object) -> tuple[int, int] | None:
    file_result = _claude_read_file_result(result)
    if file_result:
        start = _as_int(file_result.get("startLine")) or 1
        line_count = _as_int(file_result.get("numLines"))
        if line_count is None:
            content = file_result.get("content")
            if isinstance(content, str):
                line_count = _line_count(content)
        if line_count is not None and line_count > 0:
            return start, start + line_count - 1
    return _numbered_result_bounds(_response_text(result))


def _claude_read_result_path(result: object) -> str | None:
    file_result = _claude_read_file_result(result)
    if not file_result:
        return None
    return _string(file_result.get("filePath"))


def _claude_read_file_result(result: object) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    file_result = result.get("file")
    if isinstance(file_result, dict):
        return file_result
    return None


def _numbered_result_bounds(text: str) -> tuple[int, int] | None:
    lines = []
    for line in text.splitlines():
        match = re.match(r"^\s*(\d+)(?:\t|\s*(?:\u2192|\|))", line)
        if match:
            lines.append(int(match.group(1)))
    if not lines:
        return None
    return min(lines), max(lines)


def _line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _claude_grep_command(tool_input: dict[str, Any]) -> str:
    pattern = _string(tool_input.get("pattern")) or ""
    path = _string(tool_input.get("path")) or "."
    parts = ["grep", "-R"]
    if tool_input.get("-i") or tool_input.get("case_insensitive"):
        parts.append("-i")
    if tool_input.get("-n") or tool_input.get("output_mode") == "content":
        parts.append("-n")
    parts.extend([pattern, path])
    return " ".join(shlex.quote(part) for part in parts)


def _response_text(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("stdout", "output", "stderr", "text", "content"):
            child = value.get(key)
            if isinstance(child, str):
                parts.append(child)
            elif isinstance(child, list):
                parts.extend(_response_text(item) for item in child)
        return "\n".join(parts)
    if isinstance(value, list):
        return "\n".join(_response_text(item) for item in value)
    return ""


def _looks_like_claude_error(value: object) -> bool:
    if isinstance(value, dict):
        error_value = value.get("error") or value.get("is_error")
        if error_value:
            return True
    text = _response_text(value).strip().lower()
    return (
        text.startswith("error:")
        or text.startswith("<tool_use_error>")
        or text.startswith("file does not exist")
        or "sibling tool call errored" in text
    )


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
