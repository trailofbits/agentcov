from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agentcov.aggregate import build_coverage
from agentcov.cli import _append_events_by_root
from agentcov.config import load_config
from agentcov.git import line_range_digest
from agentcov.importers import import_agent_coverage
from agentcov.models import CoverageEvent, LineRange
from agentcov.parser import parse_shell_command
from agentcov.report import unread_text, write_gcov, write_html, write_lcov
from agentcov.storage import append_events, load_events, load_events_with_errors


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
    out = write_lcov(coverage, out=tmp_path / "agentcov.info", counts="full")
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


def test_event_log_parse_errors_are_disclosed_in_coverage(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\n", encoding="utf-8")

    coverage = build_coverage(
        root=tmp_path,
        config=load_config(tmp_path),
        events=[],
        parse_errors=[{"line": 3, "error": "boom", "preview": "{bad"}],
    )

    assert coverage["summary"]["event_parse_errors"] == 1
    assert any(
        entry["source"] == "event-log" and "line 3" in entry["reason"]
        for entry in coverage["unknown_events"]
    )


def test_range_summaries_are_bounded_and_disclosed(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("".join(f"line {i}\n" for i in range(1, 261)), encoding="utf-8")
    events = [
        CoverageEvent(
            tool_use_id=f"tool-{i}",
            file="a.py",
            ranges=[LineRange(i, i)],
            command=f"sed -n '{i}p' a.py",
        )
        for i in range(1, 251)
    ]

    coverage = build_coverage(root=tmp_path, config=load_config(tmp_path), events=events)

    file_data = coverage["files"]["a.py"]
    assert len(file_data["read_ranges"]) == 200
    assert file_data["read_ranges_elided"] == 50
    assert file_data["read_lines"] == 250
    assert "search_seen_ranges_elided" not in file_data


def test_reparsed_tool_call_supersedes_older_parser_events(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    call = {
        "session_id": "sess",
        "turn_id": "turn",
        "tool_use_id": "tool-1",
        "tool_name": "Bash",
        "command": "cat a.py",
    }
    old = CoverageEvent(**call, file="a.py", ranges=[LineRange(1, 4)], parser_version=0)
    new = CoverageEvent(
        **call, file="a.py", ranges=[LineRange(1, 2)], confidence="inferred", parser_version=2
    )

    coverage = build_coverage(root=tmp_path, config=load_config(tmp_path), events=[old, new])

    assert coverage["files"]["a.py"]["read_lines"] == 2
    assert coverage["summary"]["superseded_events"] == 1
    assert coverage["summary"]["events"] == 1


def test_events_without_tool_use_id_are_never_superseded(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    old = CoverageEvent(file="a.py", ranges=[LineRange(1, 4)], parser_version=0)
    new = CoverageEvent(file="a.py", ranges=[LineRange(1, 2)], parser_version=2)

    coverage = build_coverage(root=tmp_path, config=load_config(tmp_path), events=[old, new])

    assert coverage["files"]["a.py"]["read_lines"] == 4
    assert coverage["summary"]["superseded_events"] == 0


def test_matching_content_anchor_keeps_confidence(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    digest = line_range_digest(["one", "two", "three"], 1, 2)
    event = CoverageEvent(file="a.py", ranges=[LineRange(1, 2, digest=digest)])

    coverage = build_coverage(root=tmp_path, config=load_config(tmp_path), events=[event])

    file_data = coverage["files"]["a.py"]
    assert file_data["lines"]["1"]["confidence"] == "exact"
    assert "content_changed" not in file_data["read_ranges"][0]


def test_stale_content_anchor_downgrades_confidence(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\nCHANGED\nthree\n", encoding="utf-8")
    original_digest = line_range_digest(["one", "two", "three"], 1, 2)
    event = CoverageEvent(file="a.py", ranges=[LineRange(1, 2, digest=original_digest)])

    coverage = build_coverage(root=tmp_path, config=load_config(tmp_path), events=[event])

    file_data = coverage["files"]["a.py"]
    assert file_data["lines"]["1"]["confidence"] == "low"
    assert file_data["read_ranges"][0]["content_changed"] is True
    assert file_data["read_ranges"][0]["confidence"] == "low"


def test_coverage_json_includes_attribution_and_search_context_counts(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    coverage = build_coverage(
        root=tmp_path,
        config=load_config(tmp_path),
        events=[
            CoverageEvent(
                session_id="sess-1",
                turn_id="turn-1",
                tool_use_id="tool-1",
                agent="claude",
                source="transcript-backfill",
                tool_name="Read",
                command="Read a.py",
                file="a.py",
                ranges=[LineRange(2, 2)],
                task_path=["review auth", "agent-a"],
            ),
            CoverageEvent(
                session_id="sess-1",
                agent="claude",
                source="transcript-backfill",
                tool_name="Grep",
                file="a.py",
                ranges=[LineRange(3, 3, weight=0.2)],
                kind="search_seen",
            ),
        ],
    )

    line_two = coverage["files"]["a.py"]["lines"]["2"]
    line_three = coverage["files"]["a.py"]["lines"]["3"]

    assert coverage["summary"]["sessions"] == 1
    assert coverage["sessions"][0]["session_id"] == "sess-1"
    assert coverage["sessions"][0]["task_paths"] == [["review auth", "agent-a"]]
    assert line_two["attributions"][0]["agent"] == "claude"
    assert line_two["attributions"][0]["task_path"] == ["review auth", "agent-a"]
    assert line_three["search_seen_count"] == 1
    assert line_three["search_context_count"] == 1
    assert line_three["search_hit_count"] == 0


def test_adjacent_search_context_and_hit_lines_keep_distinct_counts(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    observations = parse_shell_command(
        "rg -n -C 1 two a.py",
        cwd=tmp_path,
        root=tmp_path,
        tool_response={"stdout": "a.py-1-one\na.py:2:two\na.py-3-three\n"},
    )
    coverage = build_coverage(
        root=tmp_path,
        config=load_config(tmp_path),
        events=[
            CoverageEvent(
                file=observations[0].file,
                ranges=observations[0].ranges,
                kind=observations[0].kind,
            )
        ],
    )

    lines = coverage["files"]["a.py"]["lines"]

    assert lines["1"]["search_context_count"] == 1
    assert lines["1"]["search_hit_count"] == 0
    assert lines["2"]["search_context_count"] == 0
    assert lines["2"]["search_hit_count"] == 1
    assert lines["3"]["search_context_count"] == 1
    assert lines["3"]["search_hit_count"] == 0
    assert [
        (item["start"], item["end"], item["weight"], item["search_role"])
        for item in coverage["files"]["a.py"]["search_seen_ranges"]
    ] == [
        (1, 1, 0.2, "context"),
        (2, 2, 0.35, "hit"),
        (3, 3, 0.2, "context"),
    ]


def test_fallback_inventory_excludes_agentcov_storage(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("one\n", encoding="utf-8")
    (tmp_path / "packages" / "foo" / "node_modules").mkdir(parents=True)
    (tmp_path / "packages" / "foo" / "node_modules" / "dep.js").write_text(
        "ignored\n", encoding="utf-8"
    )
    (tmp_path / "nested" / ".git").mkdir(parents=True)
    (tmp_path / "nested" / ".git" / "config").write_text("ignored\n", encoding="utf-8")
    (tmp_path / ".agentcov").mkdir()
    (tmp_path / ".agentcov" / "coverage.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / ".agentcov" / "events.jsonl").write_text("{}\n", encoding="utf-8")

    coverage = build_coverage(root=tmp_path, config=load_config(tmp_path), events=[])

    assert sorted(coverage["files"]) == ["src/app.py"]


def test_config_can_exclude_lockfiles_from_git_inventory(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\n", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / ".agentcov.toml").write_text("include_lockfiles = false\n", encoding="utf-8")
    _git(["init"], tmp_path)
    _git(["add", "app.py", "package-lock.json", ".agentcov.toml"], tmp_path)

    coverage = build_coverage(root=tmp_path, config=load_config(tmp_path), events=[])

    assert "app.py" in coverage["files"]
    assert "package-lock.json" not in coverage["files"]


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


def test_gcov_keeps_colliding_sanitized_paths_distinct(tmp_path: Path) -> None:
    coverage = {
        "files": {
            "src/a:b.py": {
                "line_count": 1,
                "lines": {"1": {"read_count": 1}},
                "read_ranges": [],
                "search_seen_ranges": [],
            },
            "src/a_b.py": {
                "line_count": 1,
                "lines": {"1": {"read_count": 1}},
                "read_ranges": [],
                "search_seen_ranges": [],
            },
        }
    }

    out_dir = write_gcov(coverage, root=tmp_path, out_dir=tmp_path / "gcov")

    assert len(list((out_dir / "src").glob("a_b.py*.gcov"))) == 2


def test_html_includes_unknown_events_and_attribution_payload(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\ntwo\n", encoding="utf-8")
    coverage = build_coverage(
        root=tmp_path,
        config=load_config(tmp_path),
        events=[
            CoverageEvent(
                session_id="sess-html",
                agent="codex",
                source="codex-post-tool-use",
                tool_name="Bash",
                command="sed -n '1,1p' a.py",
                file="a.py",
                ranges=[LineRange(1, 1)],
                task_path=["inspect html"],
            ),
            CoverageEvent(
                agent="codex",
                source="codex-post-tool-use",
                tool_name="Bash",
                command="python dynamic_reader.py",
                kind="unknown",
                confidence="unknown",
                reason="unsupported command",
            ),
        ],
    )

    out = write_html(coverage, root=tmp_path, out=tmp_path / "agentcov.html")
    text = out.read_text(encoding="utf-8")

    assert "Unknown Events" in text
    assert "sess-html" in text
    assert "inspect html" in text
    assert "python dynamic_reader.py" in text


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
            "agentcov",
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
    (repo / ".agentcov.toml").write_text('storage_dir = ".custom-agentcov"\n', encoding="utf-8")
    _git(["init"], repo)
    _git(["add", "a.py", ".agentcov.toml"], repo)

    completed = subprocess.run(
        [sys.executable, "-m", "agentcov", "--root", str(repo), "report", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == str(repo / ".custom-agentcov" / "coverage.json")
    assert (repo / ".custom-agentcov" / "coverage.json").exists()


def test_report_rejects_mismatched_output_flags(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\n", encoding="utf-8")
    _git(["init"], tmp_path)
    _git(["add", "a.py"], tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentcov",
            "--root",
            str(tmp_path),
            "report",
            "--format",
            "gcov",
            "--out",
            "agentcov.info",
        ],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "uses --out-dir" in completed.stderr


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


def test_load_events_skips_invalid_kind_and_discloses_it(tmp_path: Path) -> None:
    event_dir = tmp_path / ".agentcov"
    event_dir.mkdir()
    (event_dir / "events.jsonl").write_text('{"kind":"bogus","ranges":[]}\n', encoding="utf-8")

    events, errors = load_events_with_errors(root=tmp_path)

    assert events == []
    assert len(errors) == 1
    assert "invalid coverage kind" in str(errors[0]["error"])


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
        '[{"cmd":"sed -n 1,2p app.py","ranges":["app.py:1:2","app.py:3","app.py:0:1"]}]',
        encoding="utf-8",
    )

    root, events = import_agent_coverage(payload, cwd=tmp_path)

    assert root == tmp_path
    assert len(events) == 2
    assert [event.agent for event in events] == ["import", "import"]
    assert [event.file for event in events] == ["app.py", "app.py"]
    assert [[(item.start, item.end) for item in event.ranges] for event in events] == [
        [(1, 2)],
        [(3, 3)],
    ]


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
