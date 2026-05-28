from __future__ import annotations

from pathlib import Path

from .claude_transcript import looks_like_claude_transcript, payloads_from_claude_records
from .codex_transcript import payloads_from_codex_record
from .hooks import events_from_payload
from .models import CoverageEvent
from .transcripts import (
    claude_session_paths_from_transcript,
    find_claude_session_matches,
    find_codex_session_matches,
    read_jsonl,
)


def backfill_codex_session(
    *,
    session_id: str | None = None,
    transcript_path: Path | None = None,
) -> tuple[Path, list[CoverageEvent]]:
    path = transcript_path or _find_codex_session(session_id)
    events: list[CoverageEvent] = []
    seen: set[tuple[object, ...]] = set()
    root = Path.cwd().resolve()
    for record in read_jsonl(path):
        for payload in payloads_from_codex_record(record):
            root, parsed_events = events_from_payload(payload, source="transcript-backfill")
            _extend_new_events(events, parsed_events, seen)
    return root, events


def backfill_claude_session(
    *,
    session_id: str | None = None,
    transcript_path: Path | None = None,
) -> tuple[Path, list[CoverageEvent]]:
    paths = (
        claude_session_paths_from_transcript(transcript_path)
        if transcript_path
        else _find_claude_session_paths(session_id)
    )
    events: list[CoverageEvent] = []
    seen: set[tuple[object, ...]] = set()
    root = Path.cwd().resolve()
    for path in paths:
        records = read_jsonl(path)
        for payload in payloads_from_claude_records(records):
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
    matches = find_codex_session_matches(session_id)
    if not matches:
        raise FileNotFoundError(f"no Codex session JSONL found for {session_id!r}")
    return matches[-1]


def _find_claude_session_paths(session_id: str | None) -> list[Path]:
    if not session_id:
        raise ValueError("provide --session-id or --path")
    matches = find_claude_session_matches(session_id)
    if not matches:
        raise FileNotFoundError(f"no Claude session JSONL found for {session_id!r}")
    return claude_session_paths_from_transcript(matches[-1])


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


def _infer_agent(*, session_id: str | None, transcript_path: Path | None) -> str:
    if transcript_path:
        records = read_jsonl(transcript_path)
        detected = _detect_agent_from_records(records)
        if detected:
            return detected
        path_text = transcript_path.expanduser().as_posix()
        if "/.claude/" in path_text:
            return "claude"
        if "/.codex/" in path_text:
            return "codex"
    if session_id:
        codex_matches = find_codex_session_matches(session_id)
        claude_matches = find_claude_session_matches(session_id)
        if claude_matches and not codex_matches:
            return "claude"
        if codex_matches and not claude_matches:
            return "codex"
        if codex_matches or claude_matches:
            classified = [
                (_detect_agent_from_records(read_jsonl(path)), path)
                for path in [*codex_matches, *claude_matches]
            ]
            detected = [(agent, path) for agent, path in classified if agent]
            if detected:
                return max(detected, key=lambda item: item[1].stat().st_mtime)[0]
            return "claude" if claude_matches else "codex"
        raise FileNotFoundError(f"no Codex or Claude session JSONL found for {session_id!r}")
    return "codex"


def _detect_agent_from_records(records: list[dict[str, object]]) -> str | None:
    if any(payloads_from_codex_record(record) for record in records):
        return "codex"
    if looks_like_claude_transcript(records):
        return "claude"
    if any(record.get("type") == "response_item" for record in records):
        return "codex"
    return None
