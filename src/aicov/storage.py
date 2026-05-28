from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from .config import AicovConfig, load_config
from .git import find_repo_root
from .models import CoverageEvent

EVENTS_FILE = "events.jsonl"
COVERAGE_FILE = "coverage.json"


def storage_dir(root: Path, config: AicovConfig | None = None) -> Path:
    cfg = config or load_config(root)
    return root / cfg.storage_dir


def events_path(root: Path, config: AicovConfig | None = None) -> Path:
    return storage_dir(root, config) / EVENTS_FILE


def coverage_path(root: Path, config: AicovConfig | None = None) -> Path:
    return storage_dir(root, config) / COVERAGE_FILE


def append_events(
    events: Iterable[CoverageEvent],
    *,
    root: Path | None = None,
    config: AicovConfig | None = None,
    dedupe: bool = False,
) -> int:
    repo_root = root or find_repo_root()
    cfg = config or load_config(repo_root)
    path = events_path(repo_root, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    if dedupe:
        seen = {_event_identity(event) for event in load_events(root=repo_root, config=cfg)}
    else:
        seen = set()
    count = 0
    with path.open("a", encoding="utf-8") as file:
        for event in events:
            if dedupe:
                key = _event_identity(event)
                if key in seen:
                    continue
                seen.add(key)
            file.write(json.dumps(event.to_json(), sort_keys=True, separators=(",", ":")))
            file.write("\n")
            count += 1
    return count


def load_events(
    *,
    root: Path | None = None,
    config: AicovConfig | None = None,
    path: Path | None = None,
) -> list[CoverageEvent]:
    repo_root = root or find_repo_root()
    cfg = config or load_config(repo_root)
    event_path = path or events_path(repo_root, cfg)
    if not event_path.exists():
        return []
    events: list[CoverageEvent] = []
    with event_path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                events.append(CoverageEvent.from_json(json.loads(stripped)))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                message = f"invalid event JSON at {event_path}:{line_number}: {exc}"
                raise ValueError(message) from exc
    return events


def write_coverage_json(
    coverage: dict[str, object],
    *,
    root: Path | None = None,
    config: AicovConfig | None = None,
    path: Path | None = None,
) -> Path:
    repo_root = root or find_repo_root()
    cfg = config or load_config(repo_root)
    out = path or coverage_path(repo_root, cfg)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(coverage, indent=2, sort_keys=True), encoding="utf-8")
    return out


def _event_identity(event: CoverageEvent) -> tuple[object, ...]:
    ranges = tuple((item.start, item.end, item.confidence, item.weight) for item in event.ranges)
    return (
        event.schema_version,
        event.session_id,
        event.turn_id,
        event.tool_use_id,
        event.agent,
        event.source,
        event.tool_name,
        event.command,
        event.file,
        ranges,
        event.kind,
        event.confidence,
        event.reason,
        tuple(event.task_path),
    )
