from __future__ import annotations

from pathlib import Path

from aicov.parser import parse_shell_command


def _write_lines(path: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"line {i}\n" for i in range(1, count + 1)), encoding="utf-8")


def test_parse_sed_multiple_ranges(tmp_path: Path) -> None:
    _write_lines(tmp_path / "src" / "app.py", 200)

    observations = parse_shell_command(
        "sed -n '10,20p;50,60p' src/app.py",
        cwd=tmp_path,
        root=tmp_path,
    )

    assert [(r.start, r.end) for r in observations[0].ranges] == [(10, 20), (50, 60)]
    assert observations[0].file == "src/app.py"
    assert observations[0].kind == "read"


def test_parse_sed_read_with_redirection_uses_source_file(tmp_path: Path) -> None:
    _write_lines(tmp_path / "src" / "app.py", 50)

    observations = parse_shell_command(
        "sed -n '1,20p' src/app.py > snippet.txt",
        cwd=tmp_path,
        root=tmp_path,
    )

    assert observations[0].file == "src/app.py"
    assert [(r.start, r.end) for r in observations[0].ranges] == [(1, 20)]


def test_parse_sed_before_or_operator_uses_source_file(tmp_path: Path) -> None:
    _write_lines(tmp_path / "a.py", 10)

    observations = parse_shell_command("sed -n '1,2p' a.py || true", cwd=tmp_path, root=tmp_path)

    assert len(observations) == 1
    assert observations[0].file == "a.py"
    assert [(r.start, r.end) for r in observations[0].ranges] == [(1, 2)]


def test_parse_sed_dollar_with_no_line_count_is_unknown(tmp_path: Path) -> None:
    observations = parse_shell_command("sed -n '1,$p' missing.py", cwd=tmp_path, root=tmp_path)

    assert observations[0].kind == "unknown"
    assert observations[0].reason == "unsupported sed shape"


def test_parse_multiline_shell_reads_as_separate_commands(tmp_path: Path) -> None:
    _write_lines(tmp_path / "a.py", 10)
    _write_lines(tmp_path / "b.py", 10)

    observations = parse_shell_command(
        "sed -n '1,2p' a.py\nsed -n '3,4p' b.py",
        cwd=tmp_path,
        root=tmp_path,
    )

    assert [event.file for event in observations] == ["a.py", "b.py"]
    assert [(r.start, r.end) for r in observations[0].ranges] == [(1, 2)]
    assert [(r.start, r.end) for r in observations[1].ranges] == [(3, 4)]


def test_parse_pipeline_and_for_loop(tmp_path: Path) -> None:
    _write_lines(tmp_path / "a.py", 20)
    _write_lines(tmp_path / "b.py", 20)

    pipeline = parse_shell_command("nl -ba a.py | sed -n '3,7p'", cwd=tmp_path, root=tmp_path)
    loop = parse_shell_command(
        'for f in a.py b.py; do sed -n "1,2p" "$f"; done',
        cwd=tmp_path,
        root=tmp_path,
    )

    assert pipeline[0].file == "a.py"
    assert [(r.start, r.end) for r in pipeline[0].ranges] == [(3, 7)]
    assert [event.file for event in loop] == ["a.py", "b.py"]
    assert [(r.start, r.end) for r in loop[1].ranges] == [(1, 2)]


def test_parse_head_tail_cat_and_awk(tmp_path: Path) -> None:
    _write_lines(tmp_path / "sample.py", 100)

    head = parse_shell_command("head -n 12 sample.py", cwd=tmp_path, root=tmp_path)
    tail = parse_shell_command("tail -n 5 sample.py", cwd=tmp_path, root=tmp_path)
    cat = parse_shell_command("cat sample.py", cwd=tmp_path, root=tmp_path)
    awk = parse_shell_command("awk 'NR>=10 && NR<=15' sample.py", cwd=tmp_path, root=tmp_path)

    assert [(r.start, r.end) for r in head[0].ranges] == [(1, 12)]
    assert [(r.start, r.end) for r in tail[0].ranges] == [(96, 100)]
    assert [(r.start, r.end) for r in cat[0].ranges] == [(1, 100)]
    assert [(r.start, r.end) for r in awk[0].ranges] == [(10, 15)]


def test_parse_head_tail_byte_counts_are_unknown(tmp_path: Path) -> None:
    _write_lines(tmp_path / "sample.py", 100)

    head = parse_shell_command("head -c 100 sample.py", cwd=tmp_path, root=tmp_path)
    tail = parse_shell_command("tail --bytes=100 sample.py", cwd=tmp_path, root=tmp_path)

    assert head[0].kind == "unknown"
    assert head[0].reason == "unsupported head shape"
    assert tail[0].kind == "unknown"
    assert tail[0].reason == "unsupported tail shape"


def test_parse_sort_file_as_whole_file_read(tmp_path: Path) -> None:
    _write_lines(tmp_path / "sample.py", 12)

    observations = parse_shell_command("sort sample.py", cwd=tmp_path, root=tmp_path)

    assert observations[0].file == "sample.py"
    assert [(r.start, r.end) for r in observations[0].ranges] == [(1, 12)]


def test_parse_background_operator_splits_commands(tmp_path: Path) -> None:
    _write_lines(tmp_path / "a.py", 5)
    _write_lines(tmp_path / "b.py", 6)

    observations = parse_shell_command("cat a.py & cat b.py", cwd=tmp_path, root=tmp_path)

    assert [event.file for event in observations] == ["a.py", "b.py"]


def test_parse_cat_dash_n_preserves_filename(tmp_path: Path) -> None:
    _write_lines(tmp_path / "sample.py", 12)

    observations = parse_shell_command("cat -n sample.py", cwd=tmp_path, root=tmp_path)

    assert observations[0].file == "sample.py"
    assert [(r.start, r.end) for r in observations[0].ranges] == [(1, 12)]


def test_parse_tail_plus_start_line(tmp_path: Path) -> None:
    _write_lines(tmp_path / "sample.py", 100)

    separated = parse_shell_command("tail -n +50 sample.py", cwd=tmp_path, root=tmp_path)
    attached = parse_shell_command("tail -n+75 sample.py", cwd=tmp_path, root=tmp_path)
    long_separated = parse_shell_command("tail --lines +25 sample.py", cwd=tmp_path, root=tmp_path)
    long_attached = parse_shell_command("tail --lines=+30 sample.py", cwd=tmp_path, root=tmp_path)

    assert [(r.start, r.end) for r in separated[0].ranges] == [(50, 100)]
    assert [(r.start, r.end) for r in attached[0].ranges] == [(75, 100)]
    assert [(r.start, r.end) for r in long_separated[0].ranges] == [(25, 100)]
    assert [(r.start, r.end) for r in long_attached[0].ranges] == [(30, 100)]


def test_parse_search_seen_from_output(tmp_path: Path) -> None:
    _write_lines(tmp_path / "src" / "app.py", 20)

    observations = parse_shell_command(
        "rg -n TODO src",
        cwd=tmp_path,
        root=tmp_path,
        tool_response={"stdout": "src/app.py:4:TODO one\nsrc/app.py-8-context\n"},
    )

    assert observations[0].kind == "search_seen"
    assert observations[0].file == "src/app.py"
    assert [(r.start, r.end) for r in observations[0].ranges] == [(4, 4), (8, 8)]
    assert [r.weight for r in observations[0].ranges] == [0.35, 0.2]


def test_ambiguous_command_is_unknown(tmp_path: Path) -> None:
    observations = parse_shell_command("python script.py", cwd=tmp_path, root=tmp_path)

    assert observations[0].kind == "unknown"
    assert observations[0].confidence == "unknown"


def test_find_exec_read_shape_is_unknown_not_ignored(tmp_path: Path) -> None:
    observations = parse_shell_command(
        "find . -name '*.py' -exec sed -n '1,2p' {} ';'",
        cwd=tmp_path,
        root=tmp_path,
    )

    assert observations[0].kind == "unknown"
    assert observations[0].reason == "unsupported find read shape"


def test_find_pipeline_read_shape_is_unknown_not_ignored(tmp_path: Path) -> None:
    observations = parse_shell_command(
        "find . -name '*.py' | xargs sed -n '1,2p'",
        cwd=tmp_path,
        root=tmp_path,
    )

    assert observations[0].kind == "unknown"
    assert observations[0].reason == "unsupported pipeline"


def test_ls_xargs_read_pipeline_is_unknown_not_ignored(tmp_path: Path) -> None:
    observations = parse_shell_command(
        "ls *.py | xargs sed -n '1,2p'",
        cwd=tmp_path,
        root=tmp_path,
    )

    assert observations[0].kind == "unknown"
    assert observations[0].reason == "unsupported pipeline"


def test_printf_xargs_read_pipeline_is_unknown_not_ignored(tmp_path: Path) -> None:
    observations = parse_shell_command(
        "printf 'a.py\\n' | xargs sed -n '1,2p'",
        cwd=tmp_path,
        root=tmp_path,
    )

    assert observations[0].kind == "unknown"
    assert observations[0].reason == "unsupported pipeline"


def test_find_count_pipeline_is_ignored(tmp_path: Path) -> None:
    observations = parse_shell_command("find . -type f | wc -l", cwd=tmp_path, root=tmp_path)

    assert observations == []


def test_echo_sed_pipeline_is_ignored(tmp_path: Path) -> None:
    observations = parse_shell_command("echo foo | sed -n '1p'", cwd=tmp_path, root=tmp_path)

    assert observations == []
