from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class _PiHeader:
    session_id: str | None = None
    cwd: str | None = None
    timestamp: str | None = None
    parent_session: str | None = None


@dataclass(frozen=True)
class _PiToolResult:
    tool_use_id: str | None
    tool_name: str | None
    value: object
    is_error: bool = False


def payloads_from_pi_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    header = _pi_header(records)
    results_by_tool_id = _pi_results_by_tool_id(records)

    payloads: list[dict[str, Any]] = []
    last_user_request: str | None = None
    for record in records:
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        role = _string(message.get("role"))
        if role == "user":
            last_user_request = _message_text(message)
            continue
        if role == "assistant":
            payloads.extend(
                _assistant_payloads(
                    header=header,
                    record=record,
                    message=message,
                    user_request=last_user_request,
                    results_by_tool_id=results_by_tool_id,
                )
            )
            continue
        if role == "bashExecution":
            payload = _bash_execution_payload(
                header,
                record,
                message,
                user_request=last_user_request,
            )
            if payload:
                payloads.append(payload)
    return payloads


def looks_like_pi_transcript(records: list[dict[str, Any]]) -> bool:
    if any(_looks_like_pi_header(record) for record in records[:1]):
        return True
    for record in records:
        if record.get("type") != "message" or not isinstance(record.get("message"), dict):
            continue
        message = record["message"]
        if _string(message.get("role")) in {"assistant", "toolResult", "bashExecution"}:
            return True
    return False


def _assistant_payloads(
    *,
    header: _PiHeader,
    record: dict[str, Any],
    message: dict[str, Any],
    user_request: str | None,
    results_by_tool_id: dict[str, _PiToolResult],
) -> list[dict[str, Any]]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    payloads: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "toolCall":
            continue
        tool_name = _string(block.get("name"))
        tool_use_id = _string(block.get("id"))
        if not tool_name or not tool_use_id:
            continue
        payload = _base_payload(
            header=header,
            record=record,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            user_request=user_request,
        )
        arguments = block.get("arguments")
        payload["tool_input"] = _pi_tool_input(
            tool_name,
            arguments if isinstance(arguments, dict) else {},
            cwd=payload.get("cwd"),
        )
        result = results_by_tool_id.get(tool_use_id)
        if result is not None:
            payload = _payload_with_result(payload, result)
            if payload is None:
                continue
        elif tool_name == "read" and not _has_bounded_read_input(payload.get("tool_input")):
            continue
        payloads.append(payload)
    return payloads


def _payload_with_result(
    payload: dict[str, Any],
    result: _PiToolResult,
) -> dict[str, Any] | None:
    tool_name = payload.get("tool_name")
    if tool_name == "read":
        capped = _cap_pi_read_input(payload.get("tool_input"), result)
        if capped is None:
            return None
        payload["tool_input"] = capped
    elif tool_name == "grep":
        payload["tool_response"] = _normalize_pi_grep_response(payload, result.value)
    else:
        payload["tool_response"] = result.value
    return payload


def _base_payload(
    *,
    header: _PiHeader,
    record: dict[str, Any],
    tool_name: str,
    tool_use_id: str,
    user_request: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": header.session_id,
        "turn_id": _string(record.get("id")),
        "tool_use_id": tool_use_id,
        "timestamp": _string(record.get("timestamp")) or header.timestamp,
        "cwd": header.cwd,
        "tool_name": tool_name,
        "user_request": user_request,
    }
    subagent = _pi_subagent_label(header)
    if subagent:
        payload["subagent"] = subagent
    return {key: value for key, value in payload.items() if value is not None}


def _bash_execution_payload(
    header: _PiHeader,
    record: dict[str, Any],
    message: dict[str, Any],
    *,
    user_request: str | None,
) -> dict[str, Any] | None:
    command = _string(message.get("command"))
    if not command:
        return None
    payload = {
        "session_id": header.session_id,
        "turn_id": _string(record.get("id")),
        "tool_use_id": _string(record.get("id")),
        "timestamp": _string(record.get("timestamp")) or header.timestamp,
        "cwd": header.cwd,
        "tool_name": "bashExecution",
        "tool_input": {"command": command},
        "tool_response": _string(message.get("output")),
        "user_request": user_request,
    }
    subagent = _pi_subagent_label(header)
    if subagent:
        payload["subagent"] = subagent
    return {key: value for key, value in payload.items() if value is not None}


def _pi_tool_input(tool_name: str, arguments: dict[str, Any], *, cwd: object) -> object:
    if tool_name == "read":
        return _pi_read_tool_input(arguments)
    if tool_name == "grep":
        return {
            "command": _pi_grep_command(arguments),
            "_agentcov_grep_path": _string(arguments.get("path")) or ".",
            "_agentcov_grep_cwd": _string(cwd),
        }
    return arguments


def _pi_read_tool_input(arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(arguments)
    offset = _as_int(normalized.pop("offset", None))
    if offset is not None and "start_line" not in normalized and "line_start" not in normalized:
        normalized["start_line"] = max(1, offset)
    return normalized


def _cap_pi_read_input(tool_input: object, result: _PiToolResult) -> object | None:
    if not isinstance(tool_input, dict):
        return tool_input
    if result.is_error or _looks_like_pi_error(result.value):
        return None
    if _looks_like_pi_image_read(result.value):
        return None
    normalized = dict(tool_input)
    bounds = _pi_read_result_bounds(result.value, normalized)
    if bounds is None:
        if _result_looks_truncated(result.value):
            return None if not _has_bounded_read_input(tool_input) else tool_input
        return tool_input
    start, end = bounds
    if end < start:
        return None
    normalized["start_line"] = start
    normalized["end_line"] = end
    normalized.pop("offset", None)
    normalized.pop("limit", None)
    return normalized


def _pi_read_result_bounds(result: object, tool_input: dict[str, Any]) -> tuple[int, int] | None:
    start = _as_int(tool_input.get("start_line") or tool_input.get("line_start")) or 1
    limit = _as_int(tool_input.get("limit"))
    if limit is not None and limit > 0:
        return start, start + limit - 1

    text = _response_text(result)
    match = re.search(r"Showing lines\s+(\d+)-(\d+)\s+of\s+\d+", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"Use offset=(\d+)\s+to continue", text)
    if match:
        return start, max(start, int(match.group(1)) - 1)

    details = result.get("details") if isinstance(result, dict) else None
    truncation = details.get("truncation") if isinstance(details, dict) else None
    if isinstance(truncation, dict):
        output_lines = _as_int(truncation.get("outputLines") or truncation.get("output_lines"))
        if output_lines and output_lines > 0:
            return start, start + output_lines - 1
    return None


def _normalize_pi_grep_response(payload: dict[str, Any], result: object) -> object:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return result
    raw_path = _string(tool_input.get("_agentcov_grep_path")) or "."
    text = _response_text(result)
    if not text or raw_path in {"", "."}:
        return result

    lines = []
    changed = False
    for line in text.splitlines():
        rewritten = _rewrite_pi_grep_line(line, raw_path=raw_path, cwd=_string(payload.get("cwd")))
        lines.append(rewritten)
        changed = changed or rewritten != line
    return "\n".join(lines) if changed else result


def _rewrite_pi_grep_line(line: str, *, raw_path: str, cwd: str | None) -> str:
    match = re.match(r"^(.+?)(?P<sep>[:\-])(\d+)(?P<trailing>[:\-].*)$", line)
    if not match:
        return line
    file_part = match.group(1)
    if (
        Path(file_part).is_absolute()
        or file_part == raw_path
        or file_part.startswith(f"{raw_path}/")
    ):
        return line

    replacement = _grep_output_path(file_part, raw_path=raw_path, cwd=cwd)
    if not replacement:
        return line
    return f"{replacement}{match.group('sep')}{match.group(3)}{match.group('trailing')}"


def _grep_output_path(file_part: str, *, raw_path: str, cwd: str | None) -> str | None:
    search_path = Path(raw_path).expanduser()
    if not search_path.is_absolute() and cwd:
        search_path = Path(cwd) / search_path

    if search_path.is_file() or (not search_path.exists() and Path(raw_path).suffix):
        return raw_path

    if Path(raw_path).is_absolute():
        return (search_path / file_part).as_posix()
    return PurePosixPath(raw_path, file_part).as_posix()


def _pi_grep_command(arguments: dict[str, Any]) -> str:
    pattern = _string(arguments.get("pattern")) or ""
    path = _string(arguments.get("path")) or "."
    parts = ["grep", "-R", "-n"]
    if arguments.get("ignoreCase"):
        parts.append("-i")
    if arguments.get("literal"):
        parts.append("-F")
    glob = _string(arguments.get("glob"))
    if glob:
        parts.extend(["--include", glob])
    parts.extend([pattern, path])
    return " ".join(shlex.quote(part) for part in parts)


def _pi_results_by_tool_id(records: list[dict[str, Any]]) -> dict[str, _PiToolResult]:
    results: dict[str, _PiToolResult] = {}
    for record in records:
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != "toolResult":
            continue
        tool_use_id = _string(message.get("toolCallId"))
        if not tool_use_id:
            continue
        results[tool_use_id] = _PiToolResult(
            tool_use_id=tool_use_id,
            tool_name=_string(message.get("toolName")),
            value={
                "content": message.get("content"),
                "details": message.get("details"),
                "output": message.get("output"),
            },
            is_error=bool(message.get("isError")),
        )
    return results


def _pi_header(records: list[dict[str, Any]]) -> _PiHeader:
    for record in records:
        if _looks_like_pi_header(record):
            return _PiHeader(
                session_id=_string(record.get("id")),
                cwd=_string(record.get("cwd")),
                timestamp=_string(record.get("timestamp")),
                parent_session=_string(record.get("parentSession"))
                or _string(record.get("branchedFrom")),
            )
    return _PiHeader()


def _looks_like_pi_header(record: dict[str, Any]) -> bool:
    return (
        record.get("type") == "session"
        and isinstance(record.get("id"), str)
        and isinstance(record.get("cwd"), str)
    )


def _pi_subagent_label(header: _PiHeader) -> str | None:
    if not header.parent_session:
        return None
    return f"pi child session {header.session_id}" if header.session_id else "pi child session"


def _message_text(message: dict[str, Any]) -> str | None:
    text = _response_text(message.get("content")).strip()
    return text or None


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
        return "\n".join(part for part in parts if part)
    if isinstance(value, list):
        return "\n".join(_response_text(item) for item in value)
    return ""


def _result_looks_truncated(result: object) -> bool:
    details = result.get("details") if isinstance(result, dict) else None
    if isinstance(details, dict) and details.get("truncation"):
        return True
    text = _response_text(result)
    return (
        "Use offset=" in text
        or "[Truncated:" in text
        or "more lines not shown" in text
        or "more lines in file" in text
    )


def _looks_like_pi_error(value: object) -> bool:
    if isinstance(value, dict) and value.get("isError"):
        return True
    text = _response_text(value).strip().lower()
    return text.startswith("error:") or "operation aborted" in text


def _looks_like_pi_image_read(value: object) -> bool:
    return _response_text(value).lstrip().startswith("Read image file [")


def _has_bounded_read_input(tool_input: object) -> bool:
    if not isinstance(tool_input, dict):
        return False
    return any(tool_input.get(key) is not None for key in ("end_line", "line_end", "limit"))


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
