from __future__ import annotations

import json
from pathlib import Path

from aicov.hooks import events_from_payload, install_codex_hooks, run_stop, uninstall_codex_hooks
from aicov.models import CoverageEvent, LineRange
from aicov.storage import append_events


def test_events_from_codex_bash_payload(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    payload = {
        "session_id": "sess",
        "turn_id": "turn",
        "tool_use_id": "tool",
        "tool_name": "Bash",
        "tool_input": {"command": "sed -n '2,3p' src/app.py", "cwd": str(tmp_path)},
    }

    root, events = events_from_payload(payload)

    assert root == tmp_path
    assert len(events) == 1
    assert events[0].session_id == "sess"
    assert events[0].file == "src/app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(2, 3)]


def test_events_from_top_level_command_payload(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    payload = {
        "command": "sed -n '1,2p' app.py",
        "cwd": str(tmp_path),
    }

    root, events = events_from_payload(payload, source="transcript-backfill")

    assert root == tmp_path
    assert len(events) == 1
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(1, 2)]


def test_write_only_mcp_path_payload_is_not_counted_as_read(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    payload = {
        "tool_name": "mcp__filesystem__write_file",
        "tool_input": {
            "path": "app.py",
            "content": "replacement",
            "cwd": str(tmp_path),
        },
    }

    _, events = events_from_payload(payload)

    assert events == []


def test_read_like_mcp_path_payload_is_counted_as_read(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    payload = {
        "tool_name": "mcp__filesystem__read_file",
        "tool_input": {
            "path": "app.py",
            "start_line": 2,
            "end_line": 3,
            "cwd": str(tmp_path),
        },
    }

    _, events = events_from_payload(payload)

    assert len(events) == 1
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(2, 3)]


def test_path_payload_without_read_tool_marker_is_not_counted(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    payload = {
        "tool_name": "mcp__filesystem__stat",
        "tool_input": {
            "path": "app.py",
            "cwd": str(tmp_path),
        },
    }

    _, events = events_from_payload(payload)

    assert events == []


def test_metadata_get_mcp_path_payload_is_not_counted(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    payload = {
        "tool_name": "mcp__filesystem__get_file_info",
        "tool_input": {
            "path": "app.py",
            "cwd": str(tmp_path),
        },
    }

    _, events = events_from_payload(payload)

    assert events == []


def test_user_hook_dry_run_and_uninstall(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    path, merged, changed = install_codex_hooks(
        target="user",
        cwd=tmp_path,
        dry_run=True,
    )

    assert path == tmp_path / ".codex" / "hooks.json"
    assert changed is True
    assert "PostToolUse" in merged["hooks"]
    assert not path.exists()

    install_codex_hooks(target="user", cwd=tmp_path, dry_run=False)
    _, updated, changed = uninstall_codex_hooks(target="user", cwd=tmp_path, dry_run=False)

    assert changed is True
    assert updated.get("hooks") == {}


def test_install_and_uninstall_repo_hooks_preserves_unrelated_hooks(tmp_path: Path) -> None:
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "echo unrelated"}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    path, merged, changed = install_codex_hooks(
        target="repo",
        cwd=tmp_path,
        force=True,
        dry_run=False,
    )
    assert path == hooks_path
    assert changed is True
    commands = [
        hook["command"]
        for entry in merged["hooks"]["PostToolUse"]
        for hook in entry.get("hooks", [])
    ]
    assert "echo unrelated" in commands
    assert "aicov hook post-tool-use" in commands

    _, updated, changed = uninstall_codex_hooks(
        target="repo",
        cwd=tmp_path,
        force=True,
        dry_run=False,
    )
    assert changed is True
    remaining = [
        hook["command"]
        for entry in updated["hooks"]["PostToolUse"]
        for hook in entry.get("hooks", [])
    ]
    assert remaining == ["echo unrelated"]


def test_stop_writes_configured_auto_reports(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / ".aicov.toml").write_text(
        'auto_reports = ["lcov", "html"]\n',
        encoding="utf-8",
    )
    append_events(
        [
            CoverageEvent(
                repo_root=str(tmp_path),
                source="test",
                file="app.py",
                ranges=[LineRange(1, 1)],
            )
        ],
        root=tmp_path,
    )

    coverage_path = run_stop({"cwd": str(tmp_path)})

    assert coverage_path == tmp_path / ".aicov" / "coverage.json"
    assert (tmp_path / ".aicov" / "reports" / "aicov.info").exists()
    assert (tmp_path / ".aicov" / "reports" / "aicov.html").exists()
