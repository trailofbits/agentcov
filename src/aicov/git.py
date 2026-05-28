from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from .config import AicovConfig, is_excluded


def run_git(root: Path, args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout


def find_repo_root(cwd: Path | str | None = None) -> Path:
    start = Path(cwd or ".").resolve()
    out = run_git(start, ["rev-parse", "--show-toplevel"])
    if out:
        return Path(out.strip()).resolve()
    return start


def git_metadata(root: Path) -> dict[str, object]:
    head = run_git(root, ["rev-parse", "HEAD"])
    branch = run_git(root, ["branch", "--show-current"])
    status = run_git(root, ["status", "--porcelain"])
    return {
        "root": str(root),
        "head": head.strip() if head else None,
        "branch": branch.strip() if branch else None,
        "dirty": bool(status and status.strip()),
    }


def list_project_files(root: Path, config: AicovConfig) -> list[str]:
    tracked = _git_tracked_files(root)
    if tracked is None:
        tracked = _filesystem_files(root, config)
    files = []
    for rel in tracked:
        if is_excluded(rel, config):
            continue
        path = root / rel
        if path.is_file() and is_text_file(path):
            files.append(rel)
    return sorted(files)


def _git_tracked_files(root: Path) -> list[str] | None:
    out = run_git(root, ["ls-files", "-z"])
    if out is None:
        return None
    return [item for item in out.split("\0") if item]


def _filesystem_files(root: Path, config: AicovConfig) -> list[str]:
    files: list[str] = []
    for current, dirs, filenames in os.walk(root):
        current_path = Path(current)
        try:
            rel_dir = current_path.relative_to(root).as_posix()
        except ValueError:
            continue
        rel_prefix = "" if rel_dir == "." else f"{rel_dir}/"
        dirs[:] = [
            dirname for dirname in dirs if not is_excluded(f"{rel_prefix}{dirname}/", config)
        ]
        for filename in filenames:
            rel = f"{rel_prefix}{filename}"
            if not is_excluded(rel, config):
                files.append(rel)
    return files


def is_text_file(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:8192]
    except OSError:
        return False
    if b"\0" in chunk:
        return False
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def file_line_count(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def inventory_hash(files: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in files:
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
