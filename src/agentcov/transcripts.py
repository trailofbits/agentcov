from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def find_codex_session_matches(session_id: str) -> list[Path]:
    sessions_root = Path.home() / ".codex" / "sessions"
    if not sessions_root.exists():
        return []
    return sorted(sessions_root.rglob(f"*{session_id}*.jsonl"))


def find_claude_session_matches(session_id: str) -> list[Path]:
    sessions_root = Path.home() / ".claude" / "projects"
    if not sessions_root.exists():
        return []
    matches = sorted(sessions_root.rglob(f"*{session_id}*.jsonl"))
    return [path for path in matches if "/subagents/" not in path.as_posix()]


def find_pi_session_matches(session_id: str) -> list[Path]:
    agent_root = Path.home() / ".pi" / "agent"
    sessions_root = agent_root / "sessions"
    matches: list[Path] = []
    if sessions_root.exists():
        matches.extend(sorted(sessions_root.rglob(f"*{session_id}*.jsonl")))
    if agent_root.exists():
        matches.extend(sorted(agent_root.glob(f"*{session_id}*.jsonl")))
    return sorted(dict.fromkeys(matches))


def claude_session_paths_from_transcript(path: Path) -> list[Path]:
    paths = [path]
    subagents = path.with_suffix("") / "subagents"
    if subagents.exists():
        paths.extend(sorted(subagents.glob("*.jsonl")))
    return paths


def pi_session_paths_from_transcript(path: Path) -> list[Path]:
    paths = [path]
    seen = {path.resolve()}
    queue = [path.resolve()]
    candidates = _pi_candidate_paths(path)
    while queue:
        parent = queue.pop(0)
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            parent_session = _pi_parent_session(candidate)
            if parent_session is None:
                continue
            try:
                if parent_session.resolve() != parent:
                    continue
            except OSError:
                continue
            seen.add(resolved)
            paths.append(candidate)
            queue.append(resolved)
    return paths


def _pi_candidate_paths(path: Path) -> list[Path]:
    root = _pi_sessions_root_for_path(path)
    if root and root.exists():
        return sorted(root.rglob("*.jsonl"))
    return sorted(path.parent.rglob("*.jsonl")) if path.parent.exists() else []


def _pi_sessions_root_for_path(path: Path) -> Path | None:
    sessions_root = Path.home() / ".pi" / "agent" / "sessions"
    try:
        path.resolve().relative_to(sessions_root.resolve())
    except (OSError, ValueError):
        return None
    return sessions_root


def _pi_parent_session(path: Path) -> Path | None:
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                record = json.loads(line)
                break
            else:
                return None
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or record.get("type") != "session":
        return None
    parent = record.get("parentSession") or record.get("branchedFrom")
    if not isinstance(parent, str) or not parent:
        return None
    return Path(parent).expanduser()
