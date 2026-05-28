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


def claude_session_paths_from_transcript(path: Path) -> list[Path]:
    paths = [path]
    subagents = path.with_suffix("") / "subagents"
    if subagents.exists():
        paths.extend(sorted(subagents.glob("*.jsonl")))
    return paths
