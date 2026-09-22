from __future__ import annotations

import re
import shlex
from pathlib import Path

from .git import file_line_count
from .models import LineRange, ParsedObservation
from .paths import normalize_file_path

# Bump when extraction logic changes what it records for an already-supported
# shape. Aggregation drops a tool call's lower-versioned events once the same
# call has been re-parsed at a higher version, so re-backfills stay idempotent.
PARSER_VERSION = 2

READ_WEIGHTS = {
    "focused": 1.0,
    "head_tail": 0.8,
    "whole_file": 0.4,
    "search_hit": 0.35,
    "search_context": 0.2,
}

NO_RANGE_COMMANDS = {
    "pwd",
    "echo",
    "printf",
    "true",
    "false",
    "which",
}

SEARCH_COMMANDS = {"rg", "grep", "egrep", "fgrep"}
_XARGS_READ_COMMANDS = {"awk", "cat", "head", "sed", "tail"}


def parse_shell_command(
    command: str,
    *,
    cwd: Path,
    root: Path,
    tool_response: object | None = None,
) -> list[ParsedObservation]:
    command = command.strip()
    if not command:
        return []

    observations: list[ParsedObservation] | None = None
    if command.startswith("for "):
        observations = _parse_simple_for_loop(
            command, cwd=cwd, root=root, tool_response=tool_response
        )

    if observations is None:
        observations = []
        for segment in _split_top_level_segments(command):
            parsed = _parse_segment(segment, cwd=cwd, root=root, tool_response=tool_response)
            observations.extend(parsed)
    observations = _dedupe_search_observations(observations)
    return _cap_reads_to_response(observations, tool_response)


def parse_direct_tool_input(
    tool_input: object, *, cwd: Path, root: Path
) -> list[ParsedObservation]:
    if not isinstance(tool_input, dict):
        return []
    files: list[str] = []
    unresolved: list[str] = []

    def collect(raw: str) -> None:
        normalized = normalize_file_path(raw, cwd, root)
        if normalized:
            files.append(normalized)
        else:
            unresolved.append(raw)

    for key in ("path", "file", "file_path", "filename"):
        value = tool_input.get(key)
        if isinstance(value, str):
            collect(value)
    raw_files = tool_input.get("files")
    if isinstance(raw_files, list):
        for item in raw_files:
            if isinstance(item, str):
                collect(item)

    start = _as_int(tool_input.get("start_line") or tool_input.get("line_start"))
    end = _as_int(tool_input.get("end_line") or tool_input.get("line_end"))
    offset = _as_int(tool_input.get("offset"))
    limit = _as_int(tool_input.get("limit"))
    # `offset` is the first line to read, not a count of lines to skip.
    if start is None and offset is not None:
        start = max(1, offset)
    if start is None and limit is not None:
        start = 1
    if end is None and start is not None and limit is not None:
        end = start + max(0, limit - 1)

    observations: list[ParsedObservation] = []
    for raw in dict.fromkeys(unresolved):
        # A read that cannot be attributed must stay auditable, not vanish.
        observations.append(_unknown(raw, f"path is outside the repository or unresolvable: {raw}"))
    for file in dict.fromkeys(files):
        line_count = file_line_count(root / file)
        range_start = start or 1
        range_end = end or line_count
        if range_end <= 0:
            # No explicit bound and the file has no readable lines: nothing
            # was read, but the attempt must stay auditable.
            observations.append(_unknown(file, f"file is missing or empty: {file}"))
            continue
        if range_end < range_start:
            range_end = range_start
        observations.append(
            ParsedObservation(
                file=file,
                ranges=[LineRange(range_start, range_end, "exact", READ_WEIGHTS["focused"])],
                kind="read",
                confidence="exact",
                weight=READ_WEIGHTS["focused"],
            )
        )
    return observations


def _parse_segment(
    segment: str,
    *,
    cwd: Path,
    root: Path,
    tool_response: object | None,
) -> list[ParsedObservation]:
    try:
        tokens = shlex.split(segment)
    except ValueError as exc:
        return [_unknown(segment, f"unsupported shell syntax: {exc}")]
    if not tokens:
        return []

    first = Path(tokens[0]).name
    if first in SEARCH_COMMANDS:
        return _parse_search_output(tool_response, cwd=cwd, root=root)
    xargs_pipeline_may_read = "|" in tokens and _xargs_pipeline_may_read_source(tokens)
    if first in NO_RANGE_COMMANDS:
        if xargs_pipeline_may_read:
            return [_unknown(segment, "unsupported pipeline")]
        return []
    if first == "command" and len(tokens) >= 3 and tokens[1] == "-v":
        return []
    if first == "git" and len(tokens) >= 2 and tokens[1] in {"status", "rev-parse", "branch"}:
        return []
    if first == "find":
        if "|" in tokens:
            if xargs_pipeline_may_read:
                return [_unknown(segment, "unsupported pipeline")]
            return []
        if any(token in {"-exec", "-execdir"} for token in tokens):
            return [_unknown(segment, "unsupported find read shape")]
        return []
    if first in {"ls", "wc"}:
        if xargs_pipeline_may_read:
            return [_unknown(segment, "unsupported pipeline")]
        return []

    if "|" in tokens:
        return _parse_pipeline(tokens, cwd=cwd, root=root)
    if first == "sed":
        parsed = _parse_sed(tokens, cwd=cwd, root=root)
        return parsed or [_unknown(segment, "unsupported sed shape")]
    if first == "head":
        parsed = _parse_head(tokens, cwd=cwd, root=root)
        return parsed or [_unknown(segment, "unsupported head shape")]
    if first == "tail":
        parsed = _parse_tail(tokens, cwd=cwd, root=root)
        return parsed or [_unknown(segment, "unsupported tail shape")]
    if first == "cat":
        parsed = _parse_cat(tokens, cwd=cwd, root=root)
        return parsed or [_unknown(segment, "unsupported cat shape")]
    if first == "awk":
        parsed = _parse_awk(tokens, cwd=cwd, root=root)
        return parsed or [_unknown(segment, "unsupported awk shape")]
    if first == "sort":
        parsed = _parse_sort(tokens, cwd=cwd, root=root)
        return parsed or [_unknown(segment, "unsupported sort shape")]
    return [_unknown(segment, "unsupported command")]


def _parse_pipeline(tokens: list[str], *, cwd: Path, root: Path) -> list[ParsedObservation]:
    pipe_index = tokens.index("|")
    left = tokens[:pipe_index]
    right = tokens[pipe_index + 1 :]
    if "|" in right:
        # A later stage (e.g. `| head -5`) can shrink what the agent saw below
        # what the sed range claims, so the range cannot be trusted.
        return [_unknown(" ".join(tokens), "unsupported multi-stage pipeline")]
    if not left or not right or Path(right[0]).name != "sed":
        return [_unknown(" ".join(tokens), "unsupported pipeline")]
    source_file = _pipeline_source_file(left, cwd=cwd, root=root)
    if not source_file:
        return [_unknown(" ".join(tokens), "unsupported pipeline input")]
    sed_ranges = _sed_ranges_from_tokens(right, file_line_count(root / source_file))
    if not sed_ranges:
        return [_unknown(" ".join(tokens), "unsupported sed pipeline range")]
    return [
        ParsedObservation(
            file=source_file,
            ranges=sed_ranges,
            kind="read",
            confidence="exact",
            weight=READ_WEIGHTS["focused"],
        )
    ]


def _xargs_pipeline_may_read_source(tokens: list[str]) -> bool:
    for pipe_index, token in enumerate(tokens):
        if token != "|":
            continue
        command_names = {Path(item).name for item in tokens[pipe_index + 1 :]}
        if "xargs" in command_names and _XARGS_READ_COMMANDS.intersection(command_names):
            return True
    return False


def _pipeline_source_file(tokens: list[str], *, cwd: Path, root: Path) -> str | None:
    first = Path(tokens[0]).name
    if first == "cat":
        return _last_path_token(tokens[1:], cwd=cwd, root=root, value_options=frozenset())
    if first == "nl":
        candidates = [token for token in tokens[1:] if not token.startswith("-")]
        if candidates:
            return normalize_file_path(candidates[-1], cwd, root)
    return None


def _parse_sed(tokens: list[str], *, cwd: Path, root: Path) -> list[ParsedObservation]:
    source_file = _sed_source_file(tokens, cwd=cwd, root=root)
    if not source_file:
        return []
    ranges = _sed_ranges_from_tokens(tokens, file_line_count(root / source_file))
    if not ranges:
        return []
    return [
        ParsedObservation(
            file=source_file,
            ranges=ranges,
            kind="read",
            confidence="exact",
            weight=READ_WEIGHTS["focused"],
        )
    ]


def _parse_head(tokens: list[str], *, cwd: Path, root: Path) -> list[ParsedObservation]:
    source_file = _last_path_token(tokens[1:], cwd=cwd, root=root)
    if not source_file or _has_byte_count_arg(tokens):
        return []
    count = _line_count_arg(tokens, default=10)
    if count <= 0:
        return []
    line_count = file_line_count(root / source_file)
    end = min(count, line_count) if line_count else count
    return [
        ParsedObservation(
            file=source_file,
            ranges=[LineRange(1, max(1, end), "exact", READ_WEIGHTS["head_tail"])],
            kind="read",
            confidence="exact",
            weight=READ_WEIGHTS["head_tail"],
        )
    ]


def _parse_tail(tokens: list[str], *, cwd: Path, root: Path) -> list[ParsedObservation]:
    source_file = _last_path_token(tokens[1:], cwd=cwd, root=root)
    if not source_file or _has_byte_count_arg(tokens):
        return []
    line_count = file_line_count(root / source_file)
    if line_count <= 0:
        return [_unknown(" ".join(tokens), "tail requires file line count")]
    plus_start = _tail_plus_start_arg(tokens)
    if plus_start is not None:
        start = min(max(1, plus_start), line_count)
    else:
        count = _line_count_arg(tokens, default=10)
        if count <= 0:
            return []
        start = max(1, line_count - count + 1)
    return [
        ParsedObservation(
            file=source_file,
            ranges=[LineRange(start, line_count, "exact", READ_WEIGHTS["head_tail"])],
            kind="read",
            confidence="exact",
            weight=READ_WEIGHTS["head_tail"],
        )
    ]


def _parse_cat(tokens: list[str], *, cwd: Path, root: Path) -> list[ParsedObservation]:
    source_file = _last_path_token(tokens[1:], cwd=cwd, root=root, value_options=frozenset())
    if not source_file:
        return []
    line_count = file_line_count(root / source_file)
    if line_count <= 0:
        return []
    return [
        ParsedObservation(
            file=source_file,
            ranges=[LineRange(1, line_count, "exact", READ_WEIGHTS["whole_file"])],
            kind="read",
            confidence="exact",
            weight=READ_WEIGHTS["whole_file"],
        )
    ]


def _parse_awk(tokens: list[str], *, cwd: Path, root: Path) -> list[ParsedObservation]:
    if len(tokens) < 3:
        return []
    script = tokens[1]
    source_file = _last_path_token(tokens[2:], cwd=cwd, root=root)
    if not source_file:
        return []
    ranges: list[LineRange] = []
    match = re.search(r"NR\s*>=\s*(\d+)\s*&&\s*NR\s*<=\s*(\d+)", script)
    if match:
        ranges.append(
            LineRange(int(match.group(1)), int(match.group(2)), "exact", READ_WEIGHTS["focused"])
        )
    match = re.search(r"NR\s*==\s*(\d+)", script)
    if match:
        line = int(match.group(1))
        ranges.append(LineRange(line, line, "exact", READ_WEIGHTS["focused"]))
    if not ranges:
        return []
    return [
        ParsedObservation(
            file=source_file,
            ranges=ranges,
            kind="read",
            confidence="exact",
            weight=READ_WEIGHTS["focused"],
        )
    ]


def _parse_sort(tokens: list[str], *, cwd: Path, root: Path) -> list[ParsedObservation]:
    source_file = _last_path_token(
        tokens[1:],
        cwd=cwd,
        root=root,
        value_options=frozenset(
            {
                "-k",
                "--key",
                "-o",
                "--output",
                "-S",
                "--buffer-size",
                "-t",
                "--field-separator",
                "-T",
                "--temporary-directory",
            }
        ),
    )
    if not source_file:
        return []
    line_count = file_line_count(root / source_file)
    if line_count <= 0:
        return []
    return [
        ParsedObservation(
            file=source_file,
            ranges=[LineRange(1, line_count, "exact", READ_WEIGHTS["whole_file"])],
            kind="read",
            confidence="exact",
            weight=READ_WEIGHTS["whole_file"],
        )
    ]


def _parse_search_output(
    tool_response: object | None, *, cwd: Path, root: Path
) -> list[ParsedObservation]:
    text = _response_text(tool_response)
    if not text:
        return []
    by_file: dict[str, dict[int, float]] = {}
    for line in text.splitlines():
        match = re.match(r"^(.+?)(?::|-)(\d+)(?P<trailing>[:\-])", line)
        if not match:
            continue
        rel = normalize_file_path(match.group(1), cwd, root)
        if not rel:
            continue
        line_number = int(match.group(2))
        weight = (
            READ_WEIGHTS["search_context"]
            if match.group("trailing") == "-"
            else READ_WEIGHTS["search_hit"]
        )
        previous = by_file.setdefault(rel, {}).get(line_number, 0.0)
        by_file[rel][line_number] = max(previous, weight)
    observations = []
    for rel, lines in sorted(by_file.items()):
        ranges = [LineRange(line, line, "exact", weight) for line, weight in sorted(lines.items())]
        observations.append(
            ParsedObservation(
                file=rel,
                ranges=ranges,
                kind="search_seen",
                confidence="exact",
                weight=READ_WEIGHTS["search_hit"],
            )
        )
    return observations


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


def _sed_ranges_from_tokens(tokens: list[str], line_count: int) -> list[LineRange]:
    expressions: list[str] = []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in {"-n", "--quiet", "--silent"}:
            index += 1
            continue
        if token in {"-e", "--expression"} and index + 1 < len(tokens):
            expressions.append(tokens[index + 1])
            index += 2
            continue
        if re.search(r"\d+\s*(?:,\s*(?:\d+|\$))?\s*p", token):
            expressions.append(token)
        index += 1

    ranges: list[LineRange] = []
    for expression in expressions:
        for part in expression.split(";"):
            part = part.strip()
            match = re.search(r"(\d+)\s*,\s*(\d+|\$)\s*p", part)
            if match:
                start = int(match.group(1))
                raw_end = match.group(2)
                if raw_end == "$":
                    if line_count <= 0:
                        continue
                    end = line_count
                else:
                    end = int(raw_end)
                ranges.append(LineRange(start, end, "exact", READ_WEIGHTS["focused"]).normalized())
                continue
            match = re.search(r"(\d+)\s*p", part)
            if match:
                line = int(match.group(1))
                ranges.append(LineRange(line, line, "exact", READ_WEIGHTS["focused"]))
    return ranges


def _sed_source_file(tokens: list[str], *, cwd: Path, root: Path) -> str | None:
    skip_next = False
    candidates: list[str] = []
    for index, token in enumerate(tokens[1:], start=1):
        if skip_next:
            skip_next = False
            continue
        if _redirection_consumes_next(token):
            skip_next = True
            continue
        if _is_attached_redirection(token):
            continue
        if token in {"-e", "--expression", "-f", "--file"}:
            skip_next = True
            continue
        if token in {"-n", "--quiet", "--silent"} or token.startswith("-"):
            continue
        if re.search(r"\d+\s*(?:,\s*(?:\d+|\$))?\s*p", token):
            continue
        if index > 0:
            candidates.append(token)
    if not candidates:
        return None
    return normalize_file_path(candidates[-1], cwd, root)


def _last_path_token(
    tokens: list[str],
    *,
    cwd: Path,
    root: Path,
    value_options: frozenset[str] = frozenset({"-n", "-c"}),
) -> str | None:
    candidates = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if _redirection_consumes_next(token):
            skip_next = True
            continue
        if _is_attached_redirection(token):
            continue
        if token in value_options:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        candidates.append(token)
    if not candidates:
        return None
    return normalize_file_path(candidates[-1], cwd, root)


def _line_count_arg(tokens: list[str], default: int) -> int:
    for index, token in enumerate(tokens[1:], start=1):
        if token in {"-n", "--lines"} and index + 1 < len(tokens):
            value = _as_int(tokens[index + 1])
            if value is not None:
                return value
        if token.startswith("-n") and len(token) > 2:
            value = _as_int(token[2:])
            if value is not None:
                return value
        if token.startswith("--lines="):
            value = _as_int(token.removeprefix("--lines="))
            if value is not None:
                return value
        if re.match(r"^-\d+$", token):
            return abs(int(token))
    return default


def _has_byte_count_arg(tokens: list[str]) -> bool:
    for token in tokens[1:]:
        if token in {"-c", "--bytes"} or token.startswith(("-c", "--bytes=")):
            return True
    return False


def _tail_plus_start_arg(tokens: list[str]) -> int | None:
    for index, token in enumerate(tokens[1:], start=1):
        if token in {"-n", "--lines"} and index + 1 < len(tokens):
            value = _plus_int(tokens[index + 1])
            if value is not None:
                return value
        if token.startswith("-n+"):
            value = _plus_int(token[2:])
            if value is not None:
                return value
        if token.startswith("--lines=+"):
            value = _plus_int(token.removeprefix("--lines="))
            if value is not None:
                return value
        value = _plus_int(token)
        if value is not None:
            return value
    return None


def _plus_int(value: str) -> int | None:
    if re.match(r"^\+\d+$", value):
        return int(value[1:])
    return None


def _redirection_consumes_next(token: str) -> bool:
    return token in {">", ">>", "<", "<<", "2>", "2>>", "1>", "1>>", "&>", ">&"}


def _is_attached_redirection(token: str) -> bool:
    return re.match(r"^(?:\d*)?(?:>{1,2}|<<?|&>|\>&).+", token) is not None


def _parse_simple_for_loop(
    command: str,
    *,
    cwd: Path,
    root: Path,
    tool_response: object | None,
) -> list[ParsedObservation] | None:
    match = re.match(r"for\s+(\w+)\s+in\s+(.+?);\s*do\s+(.+?);\s*done\s*$", command)
    if not match:
        return None
    var_name, raw_items, body = match.groups()
    try:
        items = shlex.split(raw_items)
    except ValueError:
        return [_unknown(command, "unsupported for-loop item syntax")]
    observations: list[ParsedObservation] = []
    for item in items:
        expanded = body.replace(f'"${var_name}"', shlex.quote(item))
        expanded = expanded.replace(f"'${var_name}'", shlex.quote(item))
        expanded = expanded.replace(f"${var_name}", shlex.quote(item))
        observations.extend(
            parse_shell_command(expanded, cwd=cwd, root=root, tool_response=tool_response)
        )
    return observations


def _split_top_level_segments(command: str) -> list[str]:
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            if quote == '"' and char == "\\" and index + 1 < len(command):
                # An escaped character inside double quotes must not close them.
                current.append(char)
                current.append(command[index + 1])
                index += 2
                continue
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char == "\\":
            if index + 1 < len(command) and command[index + 1] == "\n":
                # Backslash-newline joins physical lines into one command.
                index += 2
                continue
            current.append(char)
            if index + 1 < len(command):
                current.append(command[index + 1])
                index += 2
                continue
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            index += 1
            continue
        operator = command[index : index + 2]
        if (
            char in {";", "\n"}
            or operator in {"&&", "||"}
            or _is_background_operator(command, index)
        ):
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            index += 2 if operator in {"&&", "||"} else 1
            continue
        current.append(char)
        index += 1
    segment = "".join(current).strip()
    if segment:
        segments.append(segment)
    return segments


def _is_background_operator(command: str, index: int) -> bool:
    if command[index] != "&":
        return False
    if command[index : index + 2] in {"&&", "&>"}:
        return False
    return index == 0 or command[index - 1] != ">"


def _unknown(command: str, reason: str) -> ParsedObservation:
    return ParsedObservation(
        file=None,
        ranges=[],
        kind="unknown",
        confidence="unknown",
        weight=0.0,
        reason=reason,
    )


def _dedupe_search_observations(
    observations: list[ParsedObservation],
) -> list[ParsedObservation]:
    # A compound command (`rg a src && rg b src`) parses the same combined
    # output once per search segment; the sightings are physically one.
    seen: set[tuple[object, ...]] = set()
    deduped: list[ParsedObservation] = []
    for observation in observations:
        if observation.kind == "search_seen":
            key = (
                observation.file,
                tuple((r.start, r.end, r.weight) for r in observation.ranges),
            )
            if key in seen:
                continue
            seen.add(key)
        deduped.append(observation)
    return deduped


def _cap_reads_to_response(
    observations: list[ParsedObservation],
    tool_response: object | None,
) -> list[ParsedObservation]:
    """Never let a read range claim more lines than the tool visibly returned.

    Capping only ever shrinks: with several observations sharing one combined
    response the total output line count is still a valid per-observation
    upper bound. Without usable text evidence the observations pass through.
    """
    evidence = _response_line_evidence(tool_response)
    if evidence is None:
        return observations
    capped: list[ParsedObservation] = []
    for observation in observations:
        if observation.kind != "read" or not observation.ranges:
            capped.append(observation)
            continue
        ranges = [line_range.normalized() for line_range in observation.ranges]
        span = sum(line_range.end - line_range.start + 1 for line_range in ranges)
        if evidence >= span:
            capped.append(observation)
            continue
        if evidence <= 0:
            capped.append(
                ParsedObservation(
                    file=observation.file,
                    ranges=[],
                    kind="unknown",
                    confidence="unknown",
                    weight=0.0,
                    reason="read command produced no visible output",
                )
            )
            continue
        if len(ranges) > 1:
            capped.append(
                ParsedObservation(
                    file=observation.file,
                    ranges=[],
                    kind="unknown",
                    confidence="unknown",
                    weight=0.0,
                    reason=(
                        f"tool output has {evidence} line(s), fewer than the"
                        f" {span} claimed across {len(ranges)} ranges"
                    ),
                )
            )
            continue
        only = ranges[0]
        capped.append(
            ParsedObservation(
                file=observation.file,
                ranges=[LineRange(only.start, only.start + evidence - 1, "inferred", only.weight)],
                kind="read",
                confidence="inferred",
                weight=observation.weight,
                reason=(
                    f"range capped to {evidence} output line(s);"
                    f" command claimed {only.start}-{only.end}"
                ),
            )
        )
    return capped


def _response_line_evidence(tool_response: object | None) -> int | None:
    """Count visible output lines, or None when the response is not usable text."""
    if tool_response is None:
        return None
    if isinstance(tool_response, str):
        return _text_line_count(tool_response)
    if isinstance(tool_response, dict):
        if not any(
            key in tool_response for key in ("stdout", "output", "stderr", "text", "content")
        ):
            return None
        return _text_line_count(_response_text(tool_response))
    if isinstance(tool_response, list):
        text = _response_text(tool_response)
        return _text_line_count(text) if text else None
    return None


def _text_line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
