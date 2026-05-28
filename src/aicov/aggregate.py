from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import AicovConfig, is_excluded, load_config
from .git import file_line_count, git_metadata, inventory_hash, list_project_files
from .models import Confidence, CoverageEvent, LineRange
from .paths import display_command


@dataclass
class LineStats:
    read_count: int = 0
    unique_read_count: int = 0
    search_seen_count: int = 0
    search_hit_count: int = 0
    search_context_count: int = 0
    attention_score: float = 0.0
    first_read_at: str | None = None
    last_read_at: str | None = None
    commands: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    attributions: list[dict[str, Any]] = field(default_factory=list)
    confidence: str = "exact"

    def to_json(self) -> dict[str, Any]:
        return {
            "read_count": self.read_count,
            "unique_read_count": self.unique_read_count,
            "search_seen_count": self.search_seen_count,
            "search_hit_count": self.search_hit_count,
            "search_context_count": self.search_context_count,
            "attention_score": round(self.attention_score, 4),
            "first_read_at": self.first_read_at,
            "last_read_at": self.last_read_at,
            "commands": self.commands,
            "events": self.events,
            "attributions": self.attributions,
            "confidence": self.confidence,
        }


@dataclass
class FileCoverage:
    path: str
    line_count: int
    lines: dict[int, LineStats] = field(default_factory=dict)
    read_ranges: list[dict[str, Any]] = field(default_factory=list)
    search_seen_ranges: list[dict[str, Any]] = field(default_factory=list)

    @property
    def read_lines(self) -> int:
        return sum(1 for stats in self.lines.values() if stats.read_count > 0)

    @property
    def search_seen_lines(self) -> int:
        return sum(1 for stats in self.lines.values() if stats.search_seen_count > 0)

    @property
    def search_hit_lines(self) -> int:
        return sum(1 for stats in self.lines.values() if stats.search_hit_count > 0)

    @property
    def search_context_lines(self) -> int:
        return sum(1 for stats in self.lines.values() if stats.search_context_count > 0)

    @property
    def read_percent(self) -> float:
        if self.line_count <= 0:
            return 100.0
        return round((self.read_lines / self.line_count) * 100, 2)

    def count_for_line(self, line: int, *, mode: str = "full") -> int:
        stats = self.lines.get(line)
        count = stats.read_count if stats else 0
        if mode == "binary":
            return 1 if count else 0
        return count

    def unread_ranges(self) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        start: int | None = None
        for line in range(1, self.line_count + 1):
            if self.count_for_line(line, mode="binary") == 0:
                if start is None:
                    start = line
            elif start is not None:
                ranges.append((start, line - 1))
                start = None
        if start is not None:
            ranges.append((start, self.line_count))
        return ranges

    def to_json(self, *, include_zero_lines: bool = False) -> dict[str, Any]:
        if include_zero_lines:
            line_data = {
                str(line): self.lines.get(line, LineStats()).to_json()
                for line in range(1, self.line_count + 1)
            }
        else:
            line_data = {
                str(line): stats.to_json()
                for line, stats in sorted(self.lines.items())
                if stats.read_count or stats.search_seen_count
            }
        return {
            "path": self.path,
            "line_count": self.line_count,
            "read_lines": self.read_lines,
            "search_seen_lines": self.search_seen_lines,
            "search_hit_lines": self.search_hit_lines,
            "search_context_lines": self.search_context_lines,
            "read_percent": self.read_percent,
            "unread_ranges": [{"start": start, "end": end} for start, end in self.unread_ranges()],
            "read_ranges": self.read_ranges,
            "search_seen_ranges": self.search_seen_ranges,
            "lines": line_data,
        }


def build_coverage(
    *,
    root: Path,
    events: list[CoverageEvent],
    config: AicovConfig | None = None,
) -> dict[str, Any]:
    cfg = config or load_config(root)
    inventory = list_project_files(root, cfg)
    files: dict[str, FileCoverage] = {
        rel: FileCoverage(path=rel, line_count=file_line_count(_source_path(root, rel)))
        for rel in inventory
    }
    unknown_events: list[dict[str, Any]] = []

    for event in events:
        if event.kind == "unknown" or not event.file or not event.ranges:
            unknown_events.append(_event_summary(event))
            continue
        rel = _event_file_key(event.file, root=root, config=cfg)
        if rel is None:
            unknown_events.append(
                _event_summary(event, reason="file is excluded or outside coverage scope")
            )
            continue
        if rel not in files:
            path = _source_path(root, rel)
            line_count = file_line_count(path)
            if line_count <= 0:
                unknown_events.append(
                    _event_summary(event, reason="file is missing, unreadable, or empty")
                )
                continue
            files[rel] = FileCoverage(path=rel, line_count=line_count)
        file_cov = files[rel]
        if file_cov.line_count <= 0:
            unknown_events.append(
                _event_summary(event, reason="file is missing, unreadable, or empty")
            )
            continue
        ranges = (
            _bounded_search_ranges(event.ranges, file_cov.line_count)
            if event.kind == "search_seen"
            else _merged_bounded_ranges(event.ranges, file_cov.line_count)
        )
        for normalized in ranges:
            start = normalized.start
            end = normalized.end
            range_summary = {
                "start": start,
                "end": end,
                "confidence": normalized.confidence,
                "weight": normalized.weight,
                "event_id": event.event_id,
                "command": display_command(event.command),
                "timestamp": event.timestamp,
                "attribution": _event_attribution(event),
            }
            if event.kind == "search_seen":
                range_summary["search_role"] = _search_role(normalized.weight)
                file_cov.search_seen_ranges.append(range_summary)
            else:
                file_cov.read_ranges.append(range_summary)
            for line in range(start, end + 1):
                stats = file_cov.lines.setdefault(line, LineStats(confidence=event.confidence))
                _apply_event_to_line(stats, event, weight=normalized.weight)

    files_json = {path: coverage.to_json() for path, coverage in sorted(files.items())}
    total_lines = sum(coverage.line_count for coverage in files.values())
    read_lines = sum(coverage.read_lines for coverage in files.values())
    search_seen_lines = sum(coverage.search_seen_lines for coverage in files.values())
    search_hit_lines = sum(coverage.search_hit_lines for coverage in files.values())
    search_context_lines = sum(coverage.search_context_lines for coverage in files.values())
    sessions = _session_rollups(events)
    return {
        "schema_version": 1,
        "tool": "aicov",
        "git": {
            **git_metadata(root),
            "tracked_file_count": len(inventory),
            "tracked_file_inventory_hash": inventory_hash(inventory),
        },
        "summary": {
            "files": len(files),
            "total_lines": total_lines,
            "read_lines": read_lines,
            "search_seen_lines": search_seen_lines,
            "search_hit_lines": search_hit_lines,
            "search_context_lines": search_context_lines,
            "read_percent": round((read_lines / total_lines) * 100, 2) if total_lines else 100.0,
            "events": len(events),
            "sessions": len(sessions),
            "unknown_events": len(unknown_events),
        },
        "sessions": sessions,
        "files": files_json,
        "unknown_events": unknown_events,
    }


def coverage_files(coverage: dict[str, Any]) -> dict[str, FileCoverage]:
    files: dict[str, FileCoverage] = {}
    for path, data in coverage.get("files", {}).items():
        file_cov = FileCoverage(path=path, line_count=int(data.get("line_count", 0)))
        for line_text, stats_data in data.get("lines", {}).items():
            stats = LineStats(
                read_count=int(stats_data.get("read_count", 0)),
                unique_read_count=int(stats_data.get("unique_read_count", 0)),
                search_seen_count=int(stats_data.get("search_seen_count", 0)),
                search_hit_count=int(stats_data.get("search_hit_count", 0)),
                search_context_count=int(stats_data.get("search_context_count", 0)),
                attention_score=float(stats_data.get("attention_score", 0.0)),
                first_read_at=stats_data.get("first_read_at"),
                last_read_at=stats_data.get("last_read_at"),
                commands=list(stats_data.get("commands") or []),
                events=list(stats_data.get("events") or []),
                attributions=list(stats_data.get("attributions") or []),
                confidence=stats_data.get("confidence", "exact"),
            )
            file_cov.lines[int(line_text)] = stats
        file_cov.read_ranges = list(data.get("read_ranges") or [])
        file_cov.search_seen_ranges = list(data.get("search_seen_ranges") or [])
        files[path] = file_cov
    return files


def top_unread_files(coverage: dict[str, Any], *, limit: int | None = None) -> list[dict[str, Any]]:
    rows = []
    for path, file_data in sorted(coverage.get("files", {}).items()):
        line_count = int(file_data.get("line_count", 0))
        read_lines = int(file_data.get("read_lines", 0))
        if line_count and read_lines < line_count:
            rows.append(
                {
                    "path": path,
                    "line_count": line_count,
                    "read_lines": read_lines,
                    "unread_lines": line_count - read_lines,
                    "read_percent": file_data.get("read_percent", 0.0),
                    "unread_ranges": file_data.get("unread_ranges", []),
                }
            )
    return rows if limit is None else rows[:limit]


def _apply_event_to_line(stats: LineStats, event: CoverageEvent, *, weight: float) -> None:
    if event.kind == "search_seen":
        stats.search_seen_count += 1
        if _search_role(weight) == "context":
            stats.search_context_count += 1
        else:
            stats.search_hit_count += 1
    else:
        stats.read_count += 1
        if event.event_id not in stats.events:
            stats.unique_read_count += 1
        stats.attention_score += weight
        if stats.first_read_at is None or event.timestamp < stats.first_read_at:
            stats.first_read_at = event.timestamp
        if stats.last_read_at is None or event.timestamp > stats.last_read_at:
            stats.last_read_at = event.timestamp
    command = display_command(event.command)
    if command and command not in stats.commands and len(stats.commands) < 12:
        stats.commands.append(command)
    if event.event_id not in stats.events and len(stats.events) < 24:
        stats.events.append(event.event_id)
    attribution = _event_attribution(event)
    if attribution and attribution not in stats.attributions and len(stats.attributions) < 24:
        stats.attributions.append(attribution)
    if event.confidence == "unknown":
        stats.confidence = "unknown"
    elif event.confidence == "low" and stats.confidence == "exact":
        stats.confidence = "low"


def _merged_bounded_ranges(ranges: list[LineRange], line_count: int) -> list[LineRange]:
    bounded: list[tuple[int, int, Confidence, float]] = []
    for line_range in ranges:
        normalized = line_range.normalized()
        start = max(1, normalized.start)
        end = min(normalized.end, line_count)
        if end >= start:
            bounded.append((start, end, normalized.confidence, normalized.weight))
    if not bounded:
        return []

    bounded.sort(key=lambda item: (item[0], item[1]))
    merged = [bounded[0]]
    for start, end, confidence, weight in bounded[1:]:
        last_start, last_end, last_confidence, last_weight = merged[-1]
        if start <= last_end + 1:
            merged[-1] = (
                last_start,
                max(last_end, end),
                _least_confident(last_confidence, confidence),
                max(last_weight, weight),
            )
        else:
            merged.append((start, end, confidence, weight))

    return [
        LineRange(start=start, end=end, confidence=confidence, weight=weight)
        for start, end, confidence, weight in merged
    ]


def _bounded_search_ranges(ranges: list[LineRange], line_count: int) -> list[LineRange]:
    by_line: dict[int, tuple[Confidence, float]] = {}
    for line_range in ranges:
        normalized = line_range.normalized()
        start = max(1, normalized.start)
        end = min(normalized.end, line_count)
        for line in range(start, end + 1):
            previous = by_line.get(line)
            if previous is None:
                by_line[line] = (normalized.confidence, normalized.weight)
                continue
            confidence, weight = previous
            by_line[line] = (
                _least_confident(confidence, normalized.confidence),
                max(weight, normalized.weight),
            )
    return [
        LineRange(start=line, end=line, confidence=confidence, weight=weight)
        for line, (confidence, weight) in sorted(by_line.items())
    ]


def _least_confident(left: Confidence, right: Confidence) -> Confidence:
    order = {"exact": 0, "inferred": 1, "low": 2, "unknown": 3}
    return left if order.get(left, 0) >= order.get(right, 0) else right


def _event_summary(event: CoverageEvent, *, reason: str | None = None) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp,
        "agent": event.agent,
        "source": event.source,
        "session_id": event.session_id,
        "turn_id": event.turn_id,
        "tool_use_id": event.tool_use_id,
        "tool_name": event.tool_name,
        "file": event.file,
        "command": display_command(event.command, limit=300),
        "task_path": event.task_path,
        "reason": reason or event.reason,
        "confidence": event.confidence,
    }


def _event_attribution(event: CoverageEvent) -> dict[str, Any]:
    data = {
        "event_id": event.event_id,
        "timestamp": event.timestamp,
        "agent": event.agent,
        "source": event.source,
        "session_id": event.session_id,
        "turn_id": event.turn_id,
        "tool_use_id": event.tool_use_id,
        "tool_name": event.tool_name,
        "command": display_command(event.command, limit=240),
        "task_path": event.task_path,
        "confidence": event.confidence,
    }
    return {
        key: value
        for key, value in data.items()
        if value is not None and value != "" and value != []
    }


def _session_rollups(events: list[CoverageEvent]) -> list[dict[str, Any]]:
    rollups: dict[tuple[str | None, str], dict[str, Any]] = {}
    for event in events:
        key = (event.session_id, event.agent)
        rollup = rollups.setdefault(
            key,
            {
                "session_id": event.session_id,
                "agent": event.agent,
                "first_seen_at": event.timestamp,
                "last_seen_at": event.timestamp,
                "events": 0,
                "read_events": 0,
                "search_seen_events": 0,
                "unknown_events": 0,
                "sources": set(),
                "task_paths": [],
            },
        )
        rollup["events"] += 1
        if event.kind == "read":
            rollup["read_events"] += 1
        elif event.kind == "search_seen":
            rollup["search_seen_events"] += 1
        else:
            rollup["unknown_events"] += 1
        rollup["first_seen_at"] = min(rollup["first_seen_at"], event.timestamp)
        rollup["last_seen_at"] = max(rollup["last_seen_at"], event.timestamp)
        rollup["sources"].add(event.source)
        if event.task_path and event.task_path not in rollup["task_paths"]:
            rollup["task_paths"].append(event.task_path)

    rows = []
    for rollup in rollups.values():
        rows.append(
            {
                **rollup,
                "sources": sorted(rollup["sources"]),
                "task_paths": rollup["task_paths"][:12],
            }
        )
    return sorted(
        rows,
        key=lambda item: (
            str(item["session_id"] or ""),
            str(item["agent"] or ""),
            str(item["first_seen_at"] or ""),
        ),
    )


def _search_role(weight: float) -> str:
    return "context" if weight < 0.35 else "hit"


def _source_path(root: Path, rel: str) -> Path:
    path = Path(rel)
    return path if path.is_absolute() else root / path


def _event_file_key(raw: str | None, *, root: Path, config: AicovConfig) -> str | None:
    if not raw:
        return None
    root_resolved = root.resolve()
    path = Path(raw)
    candidate = path.resolve() if path.is_absolute() else (root_resolved / path).resolve()
    try:
        rel = candidate.relative_to(root_resolved).as_posix()
    except ValueError:
        return None
    if is_excluded(rel, config):
        return None
    return rel
