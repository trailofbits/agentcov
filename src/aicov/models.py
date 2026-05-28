from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

CoverageKind = Literal["read", "search_seen", "unknown"]
Confidence = Literal["exact", "inferred", "low", "unknown"]


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class LineRange:
    start: int
    end: int
    confidence: Confidence = "exact"
    weight: float = 1.0

    def normalized(self) -> LineRange:
        start = max(1, self.start)
        end = max(start, self.end)
        return LineRange(start=start, end=end, confidence=self.confidence, weight=self.weight)


@dataclass(frozen=True)
class ParsedObservation:
    file: str | None
    ranges: list[LineRange] = field(default_factory=list)
    kind: CoverageKind = "read"
    confidence: Confidence = "exact"
    weight: float = 1.0
    reason: str | None = None


@dataclass
class CoverageEvent:
    event_id: str = field(default_factory=lambda: uuid4().hex)
    schema_version: int = 1
    session_id: str | None = None
    turn_id: str | None = None
    tool_use_id: str | None = None
    timestamp: str = field(default_factory=now_iso)
    cwd: str | None = None
    repo_root: str | None = None
    agent: str = "codex"
    source: str = "manual"
    tool_name: str | None = None
    command: str | None = None
    file: str | None = None
    ranges: list[LineRange] = field(default_factory=list)
    kind: CoverageKind = "read"
    confidence: Confidence = "exact"
    weight: float = 1.0
    task_path: list[str] = field(default_factory=list)
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["ranges"] = [asdict(r.normalized()) for r in self.ranges]
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CoverageEvent:
        ranges = [
            LineRange(
                start=int(item["start"]),
                end=int(item["end"]),
                confidence=item.get("confidence", "exact"),
                weight=float(item.get("weight", data.get("weight", 1.0))),
            ).normalized()
            for item in data.get("ranges", [])
        ]
        return cls(
            event_id=data.get("event_id") or uuid4().hex,
            schema_version=int(data.get("schema_version", 1)),
            session_id=data.get("session_id"),
            turn_id=data.get("turn_id"),
            tool_use_id=data.get("tool_use_id"),
            timestamp=data.get("timestamp") or now_iso(),
            cwd=data.get("cwd"),
            repo_root=data.get("repo_root"),
            agent=data.get("agent", "codex"),
            source=data.get("source", "manual"),
            tool_name=data.get("tool_name"),
            command=data.get("command"),
            file=data.get("file"),
            ranges=ranges,
            kind=data.get("kind", "read"),
            confidence=data.get("confidence", "exact"),
            weight=float(data.get("weight", 1.0)),
            task_path=list(data.get("task_path") or []),
            reason=data.get("reason"),
        )
