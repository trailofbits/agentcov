from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


DEFAULT_EXCLUDES = [
    ".aicov/",
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
class AicovConfig:
    storage_dir: str = ".aicov"
    exclude: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_EXCLUDES))
    include_lockfiles: bool = True
    auto_reports: tuple[str, ...] = ("json",)


def load_config(root: Path | str | None = None) -> AicovConfig:
    base = Path(root or ".").resolve()
    path = base / ".aicov.toml"
    if not path.exists():
        return AicovConfig()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    section: dict[str, Any] = data.get("aicov", data)
    excludes = section.get("exclude", DEFAULT_EXCLUDES)
    auto_reports = section.get("auto_reports", ("json",))
    return AicovConfig(
        storage_dir=str(section.get("storage_dir", ".aicov")),
        exclude=tuple(str(item) for item in excludes),
        include_lockfiles=bool(section.get("include_lockfiles", True)),
        auto_reports=tuple(str(item) for item in auto_reports),
    )


def is_excluded(relpath: str, config: AicovConfig) -> bool:
    normalized = relpath.replace("\\", "/")
    storage_dir = config.storage_dir.strip().replace("\\", "/").strip("/")
    storage_patterns = (f"{storage_dir}/",) if storage_dir else ()
    for pattern in (*storage_patterns, *config.exclude):
        pattern = pattern.replace("\\", "/")
        if pattern.endswith("/"):
            directory = pattern.strip("/")
            padded_path = f"/{normalized.strip('/')}/"
            if (
                normalized == directory
                or normalized.startswith(pattern)
                or f"/{directory}/" in padded_path
            ):
                return True
        elif normalized == pattern or normalized.startswith(pattern.rstrip("*")):
            return True
    return not config.include_lockfiles and _is_lockfile(normalized)


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
