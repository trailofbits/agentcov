from __future__ import annotations

from pathlib import Path


def normalize_file_path(raw: str, cwd: Path, root: Path) -> str | None:
    if not raw or raw == "-":
        return None
    candidate = raw.strip().strip("'\"")
    if not candidate or candidate.startswith(("http://", "https://")):
        return None
    root_resolved = root.resolve()
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = cwd / path
    path = path.resolve()
    try:
        return path.relative_to(root_resolved).as_posix()
    except ValueError:
        return None


def display_command(command: str | None, limit: int = 180) -> str | None:
    if command is None:
        return None
    compact = " ".join(command.split())
    if len(compact) <= limit:
        return compact
    if limit <= 0:
        return ""
    if limit <= 3:
        return "." * limit
    return compact[: limit - 3] + "..."
