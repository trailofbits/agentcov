from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .git import find_repo_root
from .models import CoverageEvent, LineRange


def import_agent_coverage(
    path: Path, *, cwd: Path | None = None
) -> tuple[Path, list[CoverageEvent]]:
    root = find_repo_root(cwd or Path.cwd())
    data = json.loads(path.read_text(encoding="utf-8"))
    events: list[CoverageEvent] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                events.extend(_events_from_coverage_item(item, root=root, task_path=[]))
    elif isinstance(data, dict):
        for task in data.get("tasks", []):
            if isinstance(task, dict):
                events.extend(_walk_task(task, root=root, task_path=[]))
        for item in data.get("coverage", []):
            if isinstance(item, dict):
                events.extend(_events_from_coverage_item(item, root=root, task_path=[]))
    else:
        raise ValueError("unsupported import format")
    return root, events


def _walk_task(task: dict[str, Any], *, root: Path, task_path: list[str]) -> list[CoverageEvent]:
    label = str(task.get("prompt") or task.get("kind") or "task")
    next_path = [*task_path, label]
    events: list[CoverageEvent] = []
    for item in task.get("coverage", []):
        if isinstance(item, dict):
            events.extend(_events_from_coverage_item(item, root=root, task_path=next_path))
    for child in task.get("children", []):
        if isinstance(child, dict):
            events.extend(_walk_task(child, root=root, task_path=next_path))
    return events


def _events_from_coverage_item(
    item: dict[str, Any], *, root: Path, task_path: list[str]
) -> list[CoverageEvent]:
    command = item.get("cmd") or item.get("command")
    events = []
    for raw_range in item.get("ranges", []):
        parsed = _parse_range(str(raw_range))
        if not parsed:
            continue
        file, start, end = parsed
        events.append(
            CoverageEvent(
                repo_root=str(root),
                source="agent-coverage-import",
                tool_name="import",
                command=str(command) if command else None,
                file=file,
                ranges=[LineRange(start, end)],
                kind="read",
                confidence="exact",
                task_path=task_path,
            )
        )
    return events


def _parse_range(value: str) -> tuple[str, int, int] | None:
    parts = value.rsplit(":", 2)
    if len(parts) != 3:
        return None
    file, start, end = parts
    try:
        return file, int(start), int(end)
    except ValueError:
        return None
