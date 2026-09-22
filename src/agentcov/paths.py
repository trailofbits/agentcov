from __future__ import annotations

import re
from pathlib import Path

REDACTED = "[REDACTED]"

_BEARER_PATTERN = re.compile(r"(?i)\b(bearer)(\s+)(?!\[REDACTED\])[A-Za-z0-9._~+/=-]{8,}")
_TOKEN_PATTERNS = (
    # Hyphenated key prefixes (OpenAI, GitLab, Slack). Real tokens carry digits;
    # requiring one spares names like "sk-learn-notes".
    re.compile(r"\b(?:sk|glpat|xoxb|xoxp|xoxa|xoxs|xapp)-(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?key|auth[_-]?token|authorization"
    r"|password|passwd|secret|token)"
    # Values starting with $ { < [ are placeholders or regex patterns, not secrets.
    r"(\s*[=:]\s*)(['\"]?)(?![$\{<\[])[^\s'\"]{6,}\3"
)


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


def redact_secrets(command: str | None) -> str | None:
    if not command:
        return command
    redacted = _BEARER_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", command)
    for pattern in _TOKEN_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return _KEY_VALUE_PATTERN.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}{m.group(3)}",
        redacted,
    )


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
