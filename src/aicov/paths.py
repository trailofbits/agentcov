from __future__ import annotations

from pathlib import Path


def normalize_file_path(raw: str, cwd: Path, root: Path) -> str | None:
    if not raw or raw == "-":
        return None
    candidate = raw.strip().strip("'\"")
    if not candidate or candidate.startswith(("http://", "https://")):
        return None
    path = Path(candidate)
    if not path.is_absolute():
        path = (cwd / path).resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def display_command(command: str | None, limit: int = 180) -> str | None:
    if command is None:
        return None
    compact = " ".join(command.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
