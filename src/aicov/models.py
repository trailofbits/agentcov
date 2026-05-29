from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from math import isfinite
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
        return LineRange(
            start=start,
            end=end,
            confidence=_confidence(self.confidence),
            weight=_weight(self.weight, field_name="range weight"),
        )


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
        data["kind"] = _coverage_kind(self.kind)
        data["confidence"] = _confidence(self.confidence)
        data["weight"] = _weight(self.weight, field_name="weight")
        data["ranges"] = [asdict(r.normalized()) for r in self.ranges]
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CoverageEvent:
        kind = _coverage_kind(data.get("kind", "read"))
        confidence = _confidence(data.get("confidence", "exact"))
        ranges = _ranges_from_json(data.get("ranges", []), default_weight=data.get("weight", 1.0))
        weight = _weight(data.get("weight", 1.0), field_name="weight")
        return cls(
            event_id=data.get("event_id") or uuid4().hex,
            schema_version=_schema_version(data.get("schema_version", 1)),
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
            weight=weight,
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
        if "start" not in item or "end" not in item:
            raise ValueError(f"range missing start/end: {item!r}")
        ranges.append(
            LineRange(
                start=int(item["start"]),
                end=int(item["end"]),
                confidence=_confidence(item.get("confidence", "exact")),
                weight=_weight(item.get("weight", default_weight), field_name="range weight"),
            ).normalized()
        )
    return ranges


def _schema_version(value: object) -> int:
    version = int(value)
    if version != 1:
        raise ValueError(f"unsupported schema_version: {value!r}")
    return version


def _weight(value: object, *, field_name: str) -> float:
    weight = float(value)
    if not isfinite(weight) or weight < 0:
        raise ValueError(f"{field_name} must be a finite non-negative number")
    return weight


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("task_path must be a list")
    return [str(item) for item in value]


def coverage_event_identity(event: CoverageEvent) -> tuple[object, ...]:
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
