from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from aicov.aggregate import build_coverage
from aicov.config import load_config
from aicov.models import CoverageEvent, LineRange
from aicov.report import unread_text, write_gcov, write_lcov


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
