# aicov

`aicov` tracks which lines in a repository were read by AI coding agents. It
maps AI read counts onto LCOV and textual `.gcov` reports, and it also writes a
native JSON format with per-line read counts, search hits, commands, and task
metadata.

## Install

```sh
uv tool install .
```

For local development:

```sh
uv sync --dev
uv run aicov --help
```

## Quickstart

Install Codex hooks:

```sh
aicov install-codex-hooks --user
```

Backfill an existing session:

```sh
aicov backfill --agent auto --session-id <session-id>
```

Generate reports:

```sh
aicov report --format lcov --counts full --out aicov.info
aicov report --format gcov --counts binary --out-dir coverage-gcov
aicov html --out aicov.html
aicov summary
aicov unread --limit 20
```

Render LCOV with standard tooling:

```sh
genhtml aicov.info --output-directory coverage-html
```

## What It Captures

- Codex hook payloads from `Bash`, `apply_patch`, and MCP tools.
- Codex and Claude Code transcript backfill from session id or JSONL path.
- Common shell reads: `sed`, `head`, `tail`, `cat`, `awk`, and `nl|sed`.
- `rg` and `grep` output as `search_seen`, separate from direct reads.
- Git-tracked text files that were never read, so untouched files show up with
  zero coverage.

Unsupported read-like shell shapes are recorded as unknown events rather than
being guessed as full-file reads.

## Outputs

- `.aicov/events.jsonl`: append-only observed read events.
- `.aicov/coverage.json`: native per-file and per-line coverage data.
- `aicov.info`: LCOV tracefile compatible with `genhtml`.
- `coverage-gcov/*.gcov`: textual gcov-style files.
- `aicov.html`: self-contained heatmap report.

`--counts full` writes observed read counts into LCOV/gcov output. `--counts
binary` writes `1` for read lines and `0` for unread lines.

## Configuration

Create `.aicov.toml` at the repo root when defaults need adjustment:

```toml
storage_dir = ".aicov"
exclude = [".aicov/", "node_modules/", "vendor/", "dist/", "build/", ".git/"]
include_lockfiles = true
auto_reports = ["json"]
```

`auto_reports` controls extra reports written on Codex `Stop`. JSON coverage is
always refreshed; add `lcov`, `gcov`, or `html` to write shareable reports under
`.aicov/reports/`.

## Privacy

By default, `aicov` stores commands, paths, line ranges, timestamps, and compact
metadata. It does not store raw tool output or source snippets in the event log.
The HTML report embeds source lines because it is a source viewer; share that
file with the same care as the repository.

## Development

```sh
uv run pytest
uv run ruff check .
uv run ruff format .
```
