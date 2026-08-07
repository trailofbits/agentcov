from __future__ import annotations

import json
import re
from typing import Any

_EXEC_COMMAND_CALL = re.compile(r"\btools\.exec_command\(\s*(?=\{)")
_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?")
_STRING_ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "0": "\0"}


class _NotALiteral(Exception):
    """Raised when a JavaScript value is not a self-contained literal."""


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
    if not isinstance(name, str):
        return payloads
    payloads.extend(
        {
            **record,
            "tool_name": name,
            "tool_input": tool_input,
            "tool_response": child.get("output") or record.get("output"),
            "tool_use_id": child.get("call_id") or record.get("tool_use_id"),
        }
        for tool_input in _tool_inputs_from_child(child, name)
    )
    return payloads


def _tool_inputs_from_child(child: dict[str, Any], name: str) -> list[object]:
    arguments = child.get("arguments")
    if arguments is not None:
        return [_parse_tool_arguments(arguments)]
    source = child.get("input")
    # A `custom_tool_call` carries a JavaScript program instead of arguments.
    # Only `tools.exec_command` names a command; `apply_patch`, `write_stdin`
    # and the rest either write or do not touch files at all.
    if child.get("type") == "custom_tool_call" and name == "exec" and isinstance(source, str):
        return _exec_command_arguments(source)
    return []


def _parse_tool_arguments(arguments: object) -> object:
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return {"command": arguments}
    return arguments


def _exec_command_arguments(source: str) -> list[object]:
    arguments: list[object] = []
    for match in _EXEC_COMMAND_CALL.finditer(source):
        try:
            value, _ = _read_object(source, match.end())
        except _NotALiteral:
            # Interpolated or computed arguments cannot be resolved without
            # running the program, so the call is skipped rather than guessed.
            continue
        arguments.append(value)
    return arguments


def _read_value(source: str, index: int) -> tuple[Any, int]:
    index = _skip_whitespace(source, index)
    if index >= len(source):
        raise _NotALiteral
    char = source[index]
    if char == "{":
        return _read_object(source, index)
    if char == "[":
        return _read_array(source, index)
    if char in "\"'":
        return _read_string(source, index)
    for word, value in (("true", True), ("false", False), ("null", None)):
        end = index + len(word)
        if source.startswith(word, index) and not _IDENTIFIER.match(source, end):
            return value, end
    number = _NUMBER.match(source, index)
    if number:
        return json.loads(number.group()), number.end()
    raise _NotALiteral


def _read_object(source: str, index: int) -> tuple[dict[str, Any], int]:
    index = _skip_whitespace(source, index + 1)
    entries: dict[str, Any] = {}
    if index < len(source) and source[index] == "}":
        return entries, index + 1
    while True:
        index = _skip_whitespace(source, index)
        if index < len(source) and source[index] in "\"'":
            key, index = _read_string(source, index)
        else:
            identifier = _IDENTIFIER.match(source, index)
            if not identifier:
                raise _NotALiteral
            key, index = identifier.group(), identifier.end()
        index = _skip_whitespace(source, index)
        if index >= len(source) or source[index] != ":":
            raise _NotALiteral
        entries[key], index = _read_value(source, index + 1)
        index, done = _read_separator(source, index, close="}")
        if done:
            return entries, index


def _read_array(source: str, index: int) -> tuple[list[Any], int]:
    index = _skip_whitespace(source, index + 1)
    items: list[Any] = []
    if index < len(source) and source[index] == "]":
        return items, index + 1
    while True:
        value, index = _read_value(source, index)
        items.append(value)
        index, done = _read_separator(source, index, close="]")
        if done:
            return items, index


def _read_separator(source: str, index: int, *, close: str) -> tuple[int, bool]:
    """Consume the `,` or closing bracket after an element. Returns whether it closed."""
    index = _skip_whitespace(source, index)
    if index >= len(source):
        raise _NotALiteral
    if source[index] == close:
        return index + 1, True
    if source[index] != ",":
        raise _NotALiteral
    index = _skip_whitespace(source, index + 1)
    if index < len(source) and source[index] == close:
        return index + 1, True
    return index, False


def _read_string(source: str, index: int) -> tuple[str, int]:
    quote = source[index]
    index += 1
    parts: list[str] = []
    while index < len(source):
        char = source[index]
        if char == quote:
            return "".join(parts), index + 1
        if char == "\n":
            raise _NotALiteral
        if char != "\\":
            parts.append(char)
            index += 1
            continue
        index += 1
        if index >= len(source):
            break
        escape = source[index]
        if escape in "ux":
            width = 4 if escape == "u" else 2
            try:
                parts.append(chr(int(source[index + 1 : index + 1 + width], 16)))
            except ValueError:
                raise _NotALiteral from None
            index += 1 + width
            continue
        if escape != "\n":
            parts.append(_STRING_ESCAPES.get(escape, escape))
        index += 1
    raise _NotALiteral


def _skip_whitespace(source: str, index: int) -> int:
    while index < len(source) and source[index] in " \t\r\n":
        index += 1
    return index
