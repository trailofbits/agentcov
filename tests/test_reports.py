from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from aicov.aggregate import build_coverage
from aicov.cli import _append_events_by_root
from aicov.config import load_config
from aicov.importers import import_agent_coverage
from aicov.models import CoverageEvent, LineRange
from aicov.report import unread_text, write_gcov, write_lcov
from aicov.storage import append_events, load_events


def _git(command: list[str], cwd: Path) -> None:
    subprocess.run(["git", *command], cwd=cwd, check=True, stdout=subprocess.PIPE)


def test_lcov_includes_never_read_git_tracked_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("alpha\nbeta\n", encoding="utf-8")
    _git(["init"], tmp_path)
    _git(["add", "a.py", "b.py"], tmp_path)

    coverage = build_coverage(
        root=tmp_path,
        config=load_config(tmp_path),
        events=[
            CoverageEvent(
                repo_root=str(tmp_path),
                source="test",
                tool_name="Bash",
                command="sed -n '2,2p' a.py",
                file="a.py",
                ranges=[LineRange(2, 2)],
            )
        ],
    )
    out = write_lcov(coverage, out=tmp_path / "aicov.info", counts="full")
    text = out.read_text(encoding="utf-8")

    assert "SF:a.py\nDA:1,0\nDA:2,1\nDA:3,0\nLH:1\nLF:3" in text
    assert "SF:b.py\nDA:1,0\nDA:2,0\nLH:0\nLF:2" in text
    assert coverage["summary"]["read_lines"] == 1


def test_gcov_and_unread_output(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(["init"], tmp_path)
    _git(["add", "a.py"], tmp_path)
    coverage = build_coverage(
        root=tmp_path,
        config=load_config(tmp_path),
        events=[
            CoverageEvent(
                repo_root=str(tmp_path),
                source="test",
                file="a.py",
                ranges=[LineRange(2, 3)],
            )
        ],
    )

    out_dir = write_gcov(coverage, root=tmp_path, out_dir=tmp_path / "gcov", counts="binary")
    gcov = (out_dir / "a.py.gcov").read_text(encoding="utf-8")

    assert "    #####:    1:one" in gcov
    assert "        1:    2:two" in gcov
    assert "a.py: 1/3 unread" in unread_text(coverage)


def test_overlapping_ranges_from_one_event_count_once(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    event = CoverageEvent(
        file="a.py",
        ranges=[LineRange(1, 3), LineRange(2, 4)],
    )

    coverage = build_coverage(root=tmp_path, config=load_config(tmp_path), events=[event])

    lines = coverage["files"]["a.py"]["lines"]
    assert [lines[str(line)]["read_count"] for line in range(1, 5)] == [1, 1, 1, 1]
    assert [lines[str(line)]["unique_read_count"] for line in range(1, 5)] == [1, 1, 1, 1]


def test_fallback_inventory_excludes_aicov_storage(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("one\n", encoding="utf-8")
    (tmp_path / "packages" / "foo" / "node_modules").mkdir(parents=True)
    (tmp_path / "packages" / "foo" / "node_modules" / "dep.js").write_text(
        "ignored\n", encoding="utf-8"
    )
    (tmp_path / "nested" / ".git").mkdir(parents=True)
    (tmp_path / "nested" / ".git" / "config").write_text("ignored\n", encoding="utf-8")
    (tmp_path / ".aicov").mkdir()
    (tmp_path / ".aicov" / "coverage.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / ".aicov" / "events.jsonl").write_text("{}\n", encoding="utf-8")

    coverage = build_coverage(root=tmp_path, config=load_config(tmp_path), events=[])

    assert sorted(coverage["files"]) == ["src/app.py"]


def test_event_only_files_still_honor_excludes_and_root_scope(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("one\n", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("one\n", encoding="utf-8")

    coverage = build_coverage(
        root=tmp_path,
        config=load_config(tmp_path),
        events=[
            CoverageEvent(file="node_modules/pkg.js", ranges=[LineRange(1, 1)]),
            CoverageEvent(file=str(outside), ranges=[LineRange(1, 1)]),
        ],
    )

    assert coverage["files"] == {}
    assert coverage["summary"]["read_lines"] == 0
    assert coverage["summary"]["unknown_events"] == 2


def test_missing_or_empty_event_files_do_not_create_impossible_line_totals(
    tmp_path: Path,
) -> None:
    (tmp_path / "empty.py").write_text("", encoding="utf-8")

    coverage = build_coverage(
        root=tmp_path,
        config=load_config(tmp_path),
        events=[
            CoverageEvent(file="missing.py", ranges=[LineRange(1, 10)]),
            CoverageEvent(file="empty.py", ranges=[LineRange(1, 10)]),
        ],
    )

    assert coverage["summary"]["total_lines"] == 0
    assert coverage["summary"]["read_lines"] == 0
    assert coverage["summary"]["unknown_events"] == 2


def test_gcov_sanitizes_absolute_and_parent_paths(tmp_path: Path) -> None:
    coverage = {
        "files": {
            "../outside.py": {
                "line_count": 1,
                "lines": {"1": {"read_count": 1}},
                "read_ranges": [],
                "search_seen_ranges": [],
            },
            "/tmp/absolute.py": {
                "line_count": 1,
                "lines": {"1": {"read_count": 1}},
                "read_ranges": [],
                "search_seen_ranges": [],
            },
        }
    }

    out_dir = write_gcov(coverage, root=tmp_path, out_dir=tmp_path / "gcov")

    assert (out_dir / "outside.py.gcov").exists()
    assert (out_dir / "_/tmp/absolute.py.gcov").exists()
    assert not (tmp_path / "outside.py.gcov").exists()


def test_json_report_out_is_resolved_against_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    caller = tmp_path / "caller"
    repo.mkdir()
    caller.mkdir()
    (repo / "a.py").write_text("one\n", encoding="utf-8")
    _git(["init"], repo)
    _git(["add", "a.py"], repo)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "aicov",
            "--root",
            str(repo),
            "report",
            "--format",
            "json",
            "--out",
            "coverage.json",
        ],
        cwd=caller,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == str(repo / "coverage.json")
    assert (repo / "coverage.json").exists()
    assert not (caller / "coverage.json").exists()


def test_json_report_default_uses_configured_storage_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("one\n", encoding="utf-8")
    (repo / ".aicov.toml").write_text('storage_dir = ".custom-aicov"\n', encoding="utf-8")
    _git(["init"], repo)
    _git(["add", "a.py", ".aicov.toml"], repo)

    completed = subprocess.run(
        [sys.executable, "-m", "aicov", "--root", str(repo), "report", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == str(repo / ".custom-aicov" / "coverage.json")
    assert (repo / ".custom-aicov" / "coverage.json").exists()


def test_append_events_can_dedupe_backfilled_events(tmp_path: Path) -> None:
    first = CoverageEvent(
        session_id="sess",
        turn_id="turn",
        tool_use_id="tool",
        source="transcript-backfill",
        tool_name="Bash",
        command="sed -n '1,1p' a.py",
        file="a.py",
        ranges=[LineRange(1, 1)],
    )
    second = CoverageEvent.from_json({**first.to_json(), "event_id": "different"})

    assert append_events([first], root=tmp_path, dedupe=True) == 1
    assert append_events([second], root=tmp_path, dedupe=True) == 0
    assert len(load_events(root=tmp_path)) == 1


def test_append_events_keeps_duplicate_live_events_without_dedupe(tmp_path: Path) -> None:
    event = CoverageEvent(
        session_id="sess",
        turn_id="turn",
        tool_use_id="tool",
        source="codex-post-tool-use",
        file="a.py",
        ranges=[LineRange(1, 1)],
    )
    duplicate = CoverageEvent.from_json({**event.to_json(), "event_id": "different"})

    assert append_events([event, duplicate], root=tmp_path, dedupe=False) == 2
    assert len(load_events(root=tmp_path)) == 2


def test_load_events_rejects_invalid_kind(tmp_path: Path) -> None:
    event_dir = tmp_path / ".aicov"
    event_dir.mkdir()
    (event_dir / "events.jsonl").write_text('{"kind":"bogus","ranges":[]}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="invalid coverage kind"):
        load_events(root=tmp_path)


def test_append_events_by_root_writes_each_repo_store(tmp_path: Path) -> None:
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    events = [
        CoverageEvent(repo_root=str(one), file="a.py", ranges=[LineRange(1, 1)]),
        CoverageEvent(repo_root=str(two), file="b.py", ranges=[LineRange(1, 1)]),
    ]

    assert _append_events_by_root(events, fallback_root=tmp_path, dedupe=True) == 2

    assert len(load_events(root=one)) == 1
    assert len(load_events(root=two)) == 1


def test_import_agent_coverage_simple_array(tmp_path: Path) -> None:
    payload = tmp_path / "agent-coverage.json"
    payload.write_text(
        '[{"cmd":"sed -n 1,2p app.py","ranges":["app.py:1:2"]}]',
        encoding="utf-8",
    )

    root, events = import_agent_coverage(payload, cwd=tmp_path)

    assert root == tmp_path
    assert len(events) == 1
    assert events[0].file == "app.py"
    assert [(item.start, item.end) for item in events[0].ranges] == [(1, 2)]


def test_import_agent_coverage_hierarchical_tasks(tmp_path: Path) -> None:
    payload = tmp_path / "agent-coverage.json"
    payload.write_text(
        """
        {
          "tasks": [
            {
              "prompt": "review",
              "coverage": [{"ranges": ["src/app.py:3:4"]}],
              "children": [
                {
                  "kind": "subagent",
                  "coverage": [{"ranges": ["src/lib.py:1:1"]}]
                }
              ]
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    _, events = import_agent_coverage(payload, cwd=tmp_path)

    assert [event.file for event in events] == ["src/app.py", "src/lib.py"]
    assert events[0].task_path == ["review"]
    assert events[1].task_path == ["review", "subagent"]
