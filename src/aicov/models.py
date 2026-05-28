from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import uuid4

CoverageKind = Literal["read", "search_seen", "unknown"]
Confidence = Literal["exact", "inferred", "low", "unknown"]
VALID_COVERAGE_KINDS = frozenset({"read", "search_seen", "unknown"})
VALID_CONFIDENCES = frozenset({"exact", "inferred", "low", "unknown"})


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
        kind = _coverage_kind(data.get("kind", "read"))
        confidence = _confidence(data.get("confidence", "exact"))
        ranges = _ranges_from_json(data.get("ranges", []), default_weight=data.get("weight", 1.0))
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
            kind=kind,
            confidence=confidence,
            weight=float(data.get("weight", 1.0)),
            task_path=_string_list(data.get("task_path")),
            reason=data.get("reason"),
        )


def _coverage_kind(value: object) -> CoverageKind:
    if value not in VALID_COVERAGE_KINDS:
        raise ValueError(f"invalid coverage kind: {value!r}")
    return cast(CoverageKind, value)


def _confidence(value: object) -> Confidence:
    if value not in VALID_CONFIDENCES:
        raise ValueError(f"invalid confidence: {value!r}")
    return cast(Confidence, value)


def _ranges_from_json(value: object, *, default_weight: object) -> list[LineRange]:
    if not isinstance(value, list):
        raise ValueError("ranges must be a list")
    ranges: list[LineRange] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"invalid range item: {item!r}")
        ranges.append(
            LineRange(
                start=int(item["start"]),
                end=int(item["end"]),
                confidence=_confidence(item.get("confidence", "exact")),
                weight=float(item.get("weight", default_weight)),
            ).normalized()
        )
    return ranges


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("task_path must be a list")
    return [str(item) for item in value]
