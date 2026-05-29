from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from .aggregate import build_coverage
from .config import AicovConfig, load_config
from .git import find_repo_root
from .models import CoverageEvent, ParsedObservation, now_iso
from .parser import parse_direct_tool_input, parse_shell_command
from .storage import append_events, load_events, storage_dir, write_coverage_json

AICOV_HOOK_COMMANDS = {
    "aicov hook post-tool-use",
    "aicov hook pre-tool-use",
    "aicov hook stop",
}


def read_hook_payload(stdin: TextIO | None = None) -> dict[str, Any]:
    raw = (stdin or sys.stdin).read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def events_from_payload(
    payload: dict[str, Any],
    *,
    source: str = "codex-post-tool-use",
    agent: str = "codex",
) -> tuple[Path, list[CoverageEvent]]:
    tool_input = payload.get("tool_input") or payload.get("input") or payload.get("arguments") or {}
    if isinstance(tool_input, str):
        try:
            parsed_input: object = json.loads(tool_input)
        except json.JSONDecodeError:
            parsed_input = {"command": tool_input}
        tool_input = parsed_input

    cwd = _payload_cwd(payload, tool_input)
    root = find_repo_root(cwd)
    tool_name = payload.get("tool_name") or payload.get("name") or payload.get("tool")
    command = _payload_command(tool_input) or _payload_command(payload)
    tool_response = payload.get("tool_response") or payload.get("response") or payload.get("output")

    observations: list[ParsedObservation] = []
    if command:
        observations = parse_shell_command(command, cwd=cwd, root=root, tool_response=tool_response)
    elif _is_direct_read_tool(tool_name, tool_input):
        observations = parse_direct_tool_input(tool_input, cwd=cwd, root=root)

    events = [
        _event_from_observation(
            observation,
            payload=payload,
            root=root,
            cwd=cwd,
            source=source,
            agent=agent,
            tool_name=str(tool_name) if tool_name else None,
            command=command,
        )
        for observation in observations
    ]
    return root, events


def run_post_tool_use(payload: dict[str, Any]) -> int:
    root, events = events_from_payload(payload, source="codex-post-tool-use")
    if events:
        append_events(events, root=root)
    return len(events)


def run_pre_tool_use(payload: dict[str, Any]) -> str:
    # Reserved for future read-audit hints. The MVP keeps this non-blocking.
    _ = payload
    return ""


def run_stop(payload: dict[str, Any] | None = None) -> Path:
    payload = payload or {}
    cwd = _payload_cwd(payload, payload.get("tool_input") or {})
    root = find_repo_root(cwd)
    cfg = load_config(root)
    events = load_events(root=root, config=cfg)
    coverage = build_coverage(root=root, events=events, config=cfg)
    coverage_file = write_coverage_json(coverage, root=root, config=cfg)
    _write_auto_reports(coverage, root=root, config=cfg)
    return coverage_file


def codex_hooks_config() -> dict[str, Any]:
    return {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Bash|apply_patch|mcp__.*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "aicov hook post-tool-use",
                            "timeout": 30,
                            "statusMessage": "Recording AI read coverage",
                        }
                    ],
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "Bash|mcp__.*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "aicov hook pre-tool-use",
                            "timeout": 5,
                        }
                    ],
                }
            ],
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "aicov hook stop",
                            "timeout": 30,
                        }
                    ]
                }
            ],
        }
    }


def install_codex_hooks(
    *,
    target: str,
    cwd: Path,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[Path, dict[str, Any], bool]:
    path = _hooks_file_path(target=target, cwd=cwd, force=force)
    existing = _read_json_file(path)
    merged = _merge_hooks(existing, codex_hooks_config())
    changed = merged != existing
    if not dry_run and changed:
        _backup_file(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path, merged, changed


def uninstall_codex_hooks(
    *,
    target: str,
    cwd: Path,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[Path, dict[str, Any], bool]:
    path = _hooks_file_path(target=target, cwd=cwd, force=force)
    existing = _read_json_file(path)
    updated = _remove_aicov_hooks(existing)
    changed = updated != existing
    if not dry_run and changed:
        _backup_file(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path, updated, changed


def _write_auto_reports(coverage: dict[str, Any], *, root: Path, config: AicovConfig) -> None:
    enabled = {name.lower() for name in config.auto_reports}
    if not enabled:
        return

    reports_dir = storage_dir(root, config) / "reports"
    for name in sorted(enabled - {"json"}):
        try:
            if name == "lcov":
                from .report import write_lcov

                write_lcov(coverage, out=reports_dir / "aicov.info")
            elif name == "gcov":
                from .report import write_gcov

                write_gcov(coverage, root=root, out_dir=reports_dir / "gcov")
            elif name == "html":
                from .report import write_html

                write_html(coverage, root=root, out=reports_dir / "aicov.html")
            else:
                print(f"aicov: warning: unknown auto_report {name!r}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - hook reports must stay best-effort.
            print(f"aicov: warning: failed to write {name} report: {exc}", file=sys.stderr)


def _merge_hooks(existing: dict[str, Any], addition: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(existing))
    hooks = merged.setdefault("hooks", {})
    for event_name, entries in addition.get("hooks", {}).items():
        destination = hooks.setdefault(event_name, [])
        for entry in entries:
            if not _event_contains_command(destination, entry):
                destination.append(entry)
    return merged


def _remove_aicov_hooks(existing: dict[str, Any]) -> dict[str, Any]:
    updated = json.loads(json.dumps(existing))
    hooks = updated.get("hooks")
    if not isinstance(hooks, dict):
        return updated
    for event_name in list(hooks):
        entries = hooks[event_name]
        if not isinstance(entries, list):
            continue
        kept_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                kept_entries.append(entry)
                continue
            hook_list = entry.get("hooks")
            if not isinstance(hook_list, list):
                kept_entries.append(entry)
                continue
            kept_hooks = [
                hook
                for hook in hook_list
                if not (
                    isinstance(hook, dict)
                    and str(hook.get("command", "")).strip() in AICOV_HOOK_COMMANDS
                )
            ]
            if kept_hooks:
                next_entry = dict(entry)
                next_entry["hooks"] = kept_hooks
                kept_entries.append(next_entry)
        if kept_entries:
            hooks[event_name] = kept_entries
        else:
            del hooks[event_name]
    return updated


def _event_contains_command(destination: list[Any], entry: dict[str, Any]) -> bool:
    commands = {
        hook.get("command")
        for existing_entry in destination
        if isinstance(existing_entry, dict)
        for hook in existing_entry.get("hooks", [])
        if isinstance(hook, dict)
    }
    return any(
        isinstance(hook, dict) and hook.get("command") in commands
        for hook in entry.get("hooks", [])
    )


def _hooks_file_path(*, target: str, cwd: Path, force: bool) -> Path:
    if target == "user":
        return Path.home() / ".codex" / "hooks.json"
    if target == "repo":
        root = find_repo_root(cwd)
        if root == cwd.resolve() and not (root / ".git").exists() and not force:
            raise ValueError("repo-local hook install requires a git root; pass --force to use cwd")
        return root / ".codex" / "hooks.json"
    raise ValueError(f"unknown hook target: {target}")


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _backup_file(path: Path) -> None:
    if not path.exists():
        return
    suffix = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    shutil.copy2(path, path.with_suffix(path.suffix + f".bak.{suffix}"))


def _payload_cwd(payload: dict[str, Any], tool_input: object) -> Path:
    candidates = [payload.get("cwd")]
    if isinstance(tool_input, dict):
        candidates.append(tool_input.get("cwd"))
        candidates.append(tool_input.get("workdir"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return Path(candidate).resolve()
    return Path.cwd().resolve()


def _payload_command(tool_input: object) -> str | None:
    if isinstance(tool_input, str):
        return tool_input
    if not isinstance(tool_input, dict):
        return None
    for key in ("command", "cmd", "script", "bash"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _is_direct_read_tool(tool_name: object, tool_input: object) -> bool:
    if not isinstance(tool_input, dict) or not _has_path_input(tool_input):
        return False
    name = str(tool_name or "").lower()
    action_parts = [
        str(tool_input.get(key, "")).lower() for key in ("action", "operation", "op", "mode")
    ]
    name_parts = [part for part in re.split(r"[^a-z0-9]+", name) if part]
    action_tokens = {
        token for action in action_parts for token in re.split(r"[^a-z0-9]+", action) if token
    }
    write_markers = (
        "apply",
        "copy",
        "create",
        "delete",
        "edit",
        "insert",
        "move",
        "patch",
        "remove",
        "rename",
        "replace",
        "save",
        "update",
        "write",
    )
    if any(marker in name_parts or marker in action_tokens for marker in write_markers):
        return False
    read_markers = {"cat", "fetch", "open", "read", "show", "view"}
    return bool(read_markers.intersection(name_parts) or read_markers.intersection(action_tokens))


def _has_path_input(tool_input: dict[str, Any]) -> bool:
    path_keys = ("path", "file", "file_path", "filename")
    if any(isinstance(tool_input.get(key), str) for key in path_keys):
        return True
    files = tool_input.get("files")
    return isinstance(files, list) and any(isinstance(item, str) for item in files)


def _event_from_observation(
    observation: ParsedObservation,
    *,
    payload: dict[str, Any],
    root: Path,
    cwd: Path,
    source: str,
    agent: str,
    tool_name: str | None,
    command: str | None,
) -> CoverageEvent:
    return CoverageEvent(
        session_id=_first_string(payload, "session_id", "conversation_id")
        or os.getenv("CODEX_SESSION_ID"),
        turn_id=_first_string(payload, "turn_id"),
        tool_use_id=_first_string(payload, "tool_use_id", "call_id"),
        timestamp=_first_string(payload, "timestamp", "time") or now_iso(),
        cwd=str(cwd),
        repo_root=str(root),
        agent=agent,
        source=source,
        tool_name=tool_name,
        command=command,
        file=observation.file,
        ranges=observation.ranges,
        kind=observation.kind,
        confidence=observation.confidence,
        weight=observation.weight,
        task_path=[
            item
            for item in (
                _first_string(payload, "user_request", "prompt"),
                _first_string(payload, "subagent", "task"),
            )
            if item
        ],
        reason=observation.reason,
    )


def _first_string(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None
