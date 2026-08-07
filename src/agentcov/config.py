from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

DEFAULT_EXCLUDES = [
    ".agentcov/",
    "node_modules/",
    "vendor/",
    "dist/",
    "build/",
    ".next/",
    "coverage/",
    ".git/",
    ".venv/",
]


@dataclass(frozen=True)
class AgentcovConfig:
    storage_dir: str = ".agentcov"
    exclude: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_EXCLUDES))
    include_lockfiles: bool = True
    auto_reports: tuple[str, ...] = ("json",)


def load_config(root: Path | str | None = None) -> AgentcovConfig:
    base = Path(root or ".").resolve()
    path = base / ".agentcov.toml"
    if not path.exists():
        return AgentcovConfig()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw_section = data.get("agentcov", data)
    if not isinstance(raw_section, dict):
        raise ValueError(f"{path}: [agentcov] config must be a table")
    section: dict[str, Any] = raw_section
    excludes = _string_tuple(section.get("exclude", DEFAULT_EXCLUDES), field_name="exclude")
    auto_reports = _string_tuple(section.get("auto_reports", ("json",)), field_name="auto_reports")
    return AgentcovConfig(
        storage_dir=str(section.get("storage_dir", ".agentcov")),
        exclude=excludes,
        include_lockfiles=bool(section.get("include_lockfiles", True)),
        auto_reports=auto_reports,
    )


def is_excluded(relpath: str, config: AgentcovConfig) -> bool:
    normalized = _normalize_relpath(relpath)
    storage_dir = config.storage_dir.strip().replace("\\", "/").strip("/")
    storage_patterns = (f"{storage_dir}/",) if storage_dir else ()
    for pattern in (*storage_patterns, *config.exclude):
        if _matches_exclude(normalized, pattern):
            return True
    return not config.include_lockfiles and _is_lockfile(normalized)


def _matches_exclude(relpath: str, pattern: str) -> bool:
    raw_pattern = pattern.strip().replace("\\", "/")
    while raw_pattern.startswith("./"):
        raw_pattern = raw_pattern[2:]
    anchored = raw_pattern.startswith("/")
    normalized = raw_pattern.lstrip("/")
    if not normalized.endswith("/"):
        normalized = normalized.strip("/")
    if not normalized:
        return False
    if normalized.endswith("/"):
        directory = normalized.strip("/")
        if not directory:
            return False
        if anchored:
            return relpath == directory or relpath.startswith(f"{directory}/")
        padded_path = f"/{relpath.strip('/')}/"
        return (
            relpath == directory
            or relpath.startswith(f"{directory}/")
            or f"/{directory}/" in padded_path
        )

    candidates = [relpath]
    if "/" not in normalized:
        candidates.append(Path(relpath).name)
    if any(char in normalized for char in "*?[]"):
        if any(fnmatchcase(candidate, normalized) for candidate in candidates):
            return True
        if not anchored and "/" in normalized:
            return fnmatchcase(relpath, f"*/{normalized}")
        return False
    return relpath == normalized or (
        not anchored and "/" not in normalized and Path(relpath).name == normalized
    )


def _normalize_relpath(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.endswith("/") and normalized != "/":
        return normalized
    return normalized.strip("/")


def _string_tuple(value: object, *, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list | tuple):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(str(item) for item in value)


def _is_lockfile(path: str) -> bool:
    name = Path(path).name
    return name in {
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "poetry.lock",
        "uv.lock",
        "Cargo.lock",
        "Gemfile.lock",
    }
