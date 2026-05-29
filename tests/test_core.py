from __future__ import annotations

from pathlib import Path

import pytest

from aicov.config import AicovConfig, is_excluded, load_config
from aicov.git import is_text_file
from aicov.models import CoverageEvent
from aicov.paths import display_command, normalize_file_path


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
    config = AicovConfig(exclude=("test", "*.min.js", "node_modules/", "/rootonly/", "/root.txt"))

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


def test_load_config_rejects_non_table_aicov_section(tmp_path: Path) -> None:
    (tmp_path / ".aicov.toml").write_text('aicov = "bad"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="config must be a table"):
        load_config(tmp_path)


def test_load_config_rejects_scalar_list_fields(tmp_path: Path) -> None:
    (tmp_path / ".aicov.toml").write_text('exclude = "*.min.js"\n', encoding="utf-8")

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


def test_display_command_handles_small_limits() -> None:
    assert display_command("abcdef", limit=2) == ".."
