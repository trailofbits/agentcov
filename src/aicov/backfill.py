from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hooks import events_from_payload
from .models import CoverageEvent


def backfill_codex_session(
    *,
    session_id: str | None = None,
    transcript_path: Path | None = None,
) -> tuple[Path, list[CoverageEvent]]:
    path = transcript_path or _find_codex_session(session_id)
    events: list[CoverageEvent] = []
    seen: set[tuple[object, ...]] = set()
    root = Path.cwd().resolve()
    for record in _read_jsonl(path):
        for payload in _payloads_from_codex_record(record):
            root, parsed_events = events_from_payload(payload, source="transcript-backfill")
            _extend_new_events(events, parsed_events, seen)
    return root, events


def backfill_claude_session(
    *,
    session_id: str | None = None,
    transcript_path: Path | None = None,
) -> tuple[Path, list[CoverageEvent]]:
    paths = (
        _claude_session_paths_from_transcript(transcript_path)
        if transcript_path
        else _find_claude_session_paths(session_id)
    )
    events: list[CoverageEvent] = []
    seen: set[tuple[object, ...]] = set()
    root = Path.cwd().resolve()
    for path in paths:
        records = list(_read_jsonl(path))
        for payload in _payloads_from_claude_records(records):
            root, parsed_events = events_from_payload(
                payload,
                source="transcript-backfill",
                agent="claude",
            )
            _extend_new_events(events, parsed_events, seen)
    return root, events


def backfill_session(
    *,
    agent: str,
    session_id: str | None = None,
    transcript_path: Path | None = None,
) -> tuple[Path, list[CoverageEvent]]:
    selected = (
        _infer_agent(session_id=session_id, transcript_path=transcript_path)
        if agent == "auto"
        else agent
    )
    if selected == "codex":
        return backfill_codex_session(session_id=session_id, transcript_path=transcript_path)
    if selected == "claude":
        return backfill_claude_session(session_id=session_id, transcript_path=transcript_path)
    raise ValueError(f"unsupported agent: {agent}")


def _find_codex_session(session_id: str | None) -> Path:
    if not session_id:
        raise ValueError("provide --session-id or --path")
    matches = _find_codex_session_matches(session_id)
    if not matches:
        raise FileNotFoundError(f"no Codex session JSONL found for {session_id!r}")
    return matches[-1]


def _find_claude_session_paths(session_id: str | None) -> list[Path]:
    if not session_id:
        raise ValueError("provide --session-id or --path")
    matches = _find_claude_session_matches(session_id)
    if not matches:
        raise FileNotFoundError(f"no Claude session JSONL found for {session_id!r}")
    return _claude_session_paths_from_transcript(matches[-1])


def _find_codex_session_matches(session_id: str) -> list[Path]:
    sessions_root = Path.home() / ".codex" / "sessions"
    if not sessions_root.exists():
        return []
    return sorted(sessions_root.rglob(f"*{session_id}*.jsonl"))


def _find_claude_session_matches(session_id: str) -> list[Path]:
    sessions_root = Path.home() / ".claude" / "projects"
    if not sessions_root.exists():
        return []
    matches = sorted(sessions_root.rglob(f"*{session_id}*.jsonl"))
    return [path for path in matches if "/subagents/" not in path.as_posix()]


def _claude_session_paths_from_transcript(path: Path) -> list[Path]:
    paths = [path]
    subagents = path.with_suffix("") / "subagents"
    if subagents.exists():
        paths.extend(sorted(subagents.glob("*.jsonl")))
    return paths


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def _extend_new_events(
    events: list[CoverageEvent],
    parsed_events: list[CoverageEvent],
    seen: set[tuple[object, ...]],
) -> None:
    for event in parsed_events:
        key = _event_key(event)
        if key in seen:
            continue
        seen.add(key)
        events.append(event)


def _event_key(event: CoverageEvent) -> tuple[object, ...]:
    ranges = tuple(
        (
            item.start,
            item.end,
            item.confidence,
            item.weight,
        )
        for item in event.ranges
    )
    return (
        event.session_id,
        event.turn_id,
        event.tool_use_id,
        event.agent,
        event.tool_name,
        event.command,
        event.file,
        ranges,
        event.kind,
        event.reason,
    )


def _payloads_from_codex_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    if _looks_like_tool_payload(record):
        payloads.append(record)
    for key in ("item", "payload"):
        child = record.get(key)
        if isinstance(child, dict):
            payloads.extend(_payloads_from_child_record(record, child))
    return payloads


@dataclass(frozen=True)
class _ClaudeToolUse:
    parent_uuid: str | None
    tool_use_id: str | None
    payload: dict[str, Any]


def _payloads_from_claude_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        result: _ClaudeToolResult | None = None
        if tool_use.tool_use_id:
            result = results_by_tool_use_id.get(tool_use.tool_use_id)
        if result is None and tool_use.parent_uuid:
            parent_results = results_by_parent_uuid.get(tool_use.parent_uuid, [])
            if len(parent_results) == 1:
                result = parent_results[0]
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
        payloads.append(payload)
    return payloads


@dataclass(frozen=True)
class _ClaudeToolResult:
    tool_use_id: str | None
    value: object
    is_error: bool = False


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
    if tool_name in {"Read", "NotebookRead"} and isinstance(tool_input, dict):
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
    if tool_name not in {"Read", "NotebookRead"} or not isinstance(tool_input, dict):
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


def _infer_agent(*, session_id: str | None, transcript_path: Path | None) -> str:
    if transcript_path:
        records = _read_jsonl(transcript_path)
        detected = _detect_agent_from_records(records)
        if detected:
            return detected
        path_text = transcript_path.expanduser().as_posix()
        if "/.claude/" in path_text:
            return "claude"
        if "/.codex/" in path_text:
            return "codex"
    if session_id:
        codex_matches = _find_codex_session_matches(session_id)
        claude_matches = _find_claude_session_matches(session_id)
        if claude_matches and not codex_matches:
            return "claude"
        if codex_matches and not claude_matches:
            return "codex"
        if codex_matches or claude_matches:
            classified = [
                (_detect_agent_from_records(_read_jsonl(path)), path)
                for path in [*codex_matches, *claude_matches]
            ]
            detected = [(agent, path) for agent, path in classified if agent]
            if detected:
                return max(detected, key=lambda item: item[1].stat().st_mtime)[0]
            return "claude" if claude_matches else "codex"
        raise FileNotFoundError(f"no Codex or Claude session JSONL found for {session_id!r}")
    return "codex"


def _detect_agent_from_records(records: list[dict[str, Any]]) -> str | None:
    for record in records:
        if _payloads_from_codex_record(record):
            return "codex"
        if _claude_tool_uses_from_record(record):
            return "claude"
        if any(_claude_tool_uses_from_record(item) for item in _claude_records([record])):
            return "claude"
    for record in records:
        if isinstance(record.get("message"), dict) and isinstance(record.get("sessionId"), str):
            return "claude"
        if record.get("type") == "response_item":
            return "codex"
    return None


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
