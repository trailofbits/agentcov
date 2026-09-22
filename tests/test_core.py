from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcov.config import AgentcovConfig, is_excluded, load_config
from agentcov.git import is_text_file
from agentcov.models import CoverageEvent, LineRange
from agentcov.paths import display_command, normalize_file_path, redact_secrets
from agentcov.storage import append_events, load_events, load_events_with_errors


def test_normalize_file_path_rejects_paths_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")

    assert normalize_file_path(str(outside), root, root) is None
    assert normalize_file_path("../outside.py", root, root) is None


def test_normalize_file_path_resolves_absolute_paths_under_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = root / "src" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")

    raw = root / "src" / ".." / "src" / "app.py"

    assert normalize_file_path(str(raw), root, root) == "src/app.py"


def test_exclude_matching_uses_exact_glob_and_directory_semantics() -> None:
    config = AgentcovConfig(
        exclude=("test", "*.min.js", "node_modules/", "/rootonly/", "/root.txt")
    )

    assert is_excluded("test", config)
    assert is_excluded("nested/test", config)
    assert not is_excluded("tests/foo.py", config)
    assert not is_excluded("test_helper.py", config)
    assert is_excluded("src/app.min.js", config)
    assert is_excluded("packages/foo/node_modules/dep.js", config)
    assert is_excluded("rootonly/app.py", config)
    assert not is_excluded("nested/rootonly/app.py", config)
    assert is_excluded("root.txt", config)
    assert not is_excluded("nested/root.txt", config)


def test_load_config_rejects_non_table_agentcov_section(tmp_path: Path) -> None:
    (tmp_path / ".agentcov.toml").write_text('agentcov = "bad"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="config must be a table"):
        load_config(tmp_path)


def test_load_config_rejects_scalar_list_fields(tmp_path: Path) -> None:
    (tmp_path / ".agentcov.toml").write_text('exclude = "*.min.js"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="exclude must be a list"):
        load_config(tmp_path)


def test_is_text_file_accepts_chunk_ending_with_partial_utf8(tmp_path: Path) -> None:
    path = tmp_path / "utf8.txt"
    path.write_bytes((b"a" * 8191) + b"\xf0\x9f\x98\x80")

    assert is_text_file(path)


def test_is_text_file_rejects_incomplete_utf8_at_eof(tmp_path: Path) -> None:
    path = tmp_path / "invalid.txt"
    path.write_bytes(b"abc\xf0")

    assert not is_text_file(path)


def test_is_text_file_rejects_nul_bytes(tmp_path: Path) -> None:
    path = tmp_path / "binary.bin"
    path.write_bytes(b"text\0binary")

    assert not is_text_file(path)


def test_coverage_event_from_json_rejects_malformed_ranges() -> None:
    with pytest.raises(ValueError, match="range missing start/end"):
        CoverageEvent.from_json({"ranges": [{"start": 1}]})


def test_coverage_event_from_json_rejects_invalid_weight() -> None:
    with pytest.raises(ValueError, match="finite non-negative"):
        CoverageEvent.from_json({"weight": -1})


def test_coverage_event_round_trips_digest_and_parser_version() -> None:
    event = CoverageEvent.from_json(
        {
            "file": "a.py",
            "ranges": [{"start": 1, "end": 2, "digest": "abc123"}],
            "parser_version": 2,
        }
    )

    assert event.ranges[0].digest == "abc123"
    assert event.parser_version == 2
    data = event.to_json()
    assert data["ranges"][0]["digest"] == "abc123"
    assert data["parser_version"] == 2
    # Legacy records without either field still load.
    legacy = CoverageEvent.from_json({"file": "a.py", "ranges": [{"start": 1, "end": 2}]})
    assert legacy.ranges[0].digest is None
    assert legacy.parser_version == 0


def test_coverage_event_from_json_rejects_bad_digest_and_parser_version() -> None:
    with pytest.raises(ValueError, match="digest must be a string"):
        CoverageEvent.from_json({"ranges": [{"start": 1, "end": 2, "digest": 5}]})
    with pytest.raises(ValueError, match="non-negative"):
        CoverageEvent.from_json({"ranges": [], "parser_version": -1})


def test_display_command_handles_small_limits() -> None:
    assert display_command("abcdef", limit=2) == ".."


def test_redact_secrets_masks_common_credentials() -> None:
    assert redact_secrets(
        "curl -H 'Authorization: Bearer abc123def456'"
    ) and "abc123def456" not in redact_secrets("curl -H 'Authorization: Bearer abc123def456'")
    assert redact_secrets("git push https://ghp_" + "a1" * 12 + "@github.com") == (
        "git push https://[REDACTED]@github.com"
    )
    assert redact_secrets("aws s3 ls --key AKIAABCDEFGHIJKLMNOP") == ("aws s3 ls --key [REDACTED]")
    assert redact_secrets("export API_KEY=supersecret123") == "export API_KEY=[REDACTED]"
    assert "sk-proj9abc_def123" not in redact_secrets("echo sk-proj9abc_def123")


def test_redact_secrets_spares_benign_lookalikes() -> None:
    assert redact_secrets("cat sk-learn-notes.md") == "cat sk-learn-notes.md"
    assert redact_secrets("echo token=$TOKEN") == "echo token=$TOKEN"
    assert redact_secrets("rg 'token=[a-z]+' src") == "rg 'token=[a-z]+' src"
    assert redact_secrets("head -n 3 tokens.txt") == "head -n 3 tokens.txt"
    assert redact_secrets(None) is None
    assert redact_secrets("") == ""


def test_load_events_skips_unreadable_lines_and_discloses_them(tmp_path: Path) -> None:
    event = CoverageEvent(repo_root=str(tmp_path), file="a.py", ranges=[LineRange(1, 2)])
    future = event.to_json()
    future["schema_version"] = 2
    log = tmp_path / "events.jsonl"
    log.write_text(
        json.dumps(event.to_json()) + "\n{broken json\n" + json.dumps(future) + "\n",
        encoding="utf-8",
    )

    events, errors = load_events_with_errors(root=tmp_path, path=log)

    assert len(events) == 1
    assert [error["line"] for error in errors] == [2, 3]
    assert load_events(root=tmp_path, path=log) == events


def test_append_events_dedupe_survives_corrupt_log_line(tmp_path: Path) -> None:
    event = CoverageEvent(repo_root=str(tmp_path), file="a.py", ranges=[LineRange(1, 2)])
    appended = append_events([event], root=tmp_path)
    assert appended == 1
    log = tmp_path / ".agentcov" / "events.jsonl"
    with log.open("a", encoding="utf-8") as file:
        file.write("{broken json\n")

    assert append_events([event], root=tmp_path, dedupe=True) == 0
    other = CoverageEvent(repo_root=str(tmp_path), file="b.py", ranges=[LineRange(3, 4)])
    assert append_events([other], root=tmp_path, dedupe=True) == 1
