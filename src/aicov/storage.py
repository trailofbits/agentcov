from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .config import AicovConfig, load_config
from .git import find_repo_root
from .models import CoverageEvent, coverage_event_identity

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

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
    count = 0
    with path.open("a+", encoding="utf-8") as file:
        _lock_file(file)
        if dedupe:
            file.seek(0)
            seen = {coverage_event_identity(event) for event in _read_events(file, path)}
            file.seek(0, os.SEEK_END)
        else:
            seen = set()
        for event in events:
            if dedupe:
                key = coverage_event_identity(event)
                if key in seen:
                    continue
                seen.add(key)
            file.write(json.dumps(event.to_json(), sort_keys=True, separators=(",", ":")) + "\n")
            count += 1
        file.flush()
        os.fsync(file.fileno())
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
    with event_path.open("r", encoding="utf-8") as file:
        return _read_events(file, event_path)


def _read_events(file: Iterable[str], event_path: Path) -> list[CoverageEvent]:
    events: list[CoverageEvent] = []
    for line_number, line in enumerate(file, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
            if not isinstance(data, dict):
                raise ValueError("event JSON must be an object")
            events.append(CoverageEvent.from_json(data))
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
    data = json.dumps(coverage, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=out.parent,
        prefix=f".{out.name}.",
        suffix=".tmp",
        delete=False,
    ) as file:
        tmp_name = file.name
        try:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    try:
        os.replace(tmp_name, out)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    _fsync_parent(out)
    return out


def _lock_file(file: object) -> None:
    if fcntl is not None:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)


def _fsync_parent(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
