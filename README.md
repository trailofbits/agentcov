# agentcov

[![CI](https://img.shields.io/github/actions/workflow/status/trailofbits/agentcov/ci.yml?event=merge_group&label=CI)](https://github.com/trailofbits/agentcov/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agentcov)](https://pypi.org/project/agentcov/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/agentcov/)
[![License](https://img.shields.io/pypi/l/agentcov)](LICENSE)

`agentcov` tracks which lines in a repository were read by AI coding agents. It
maps AI read counts onto LCOV and textual `.gcov` reports, and it also writes a
native JSON format with per-line read counts, search hits, commands, and task
metadata.

## Install

From this repository:

```sh
uv tool install .
```

From a published package, use the same shape:

```sh
uv tool install agentcov
```

For local development:

```sh
uv sync --dev
uv run agentcov --help
```

## Quickstart

Install Codex hooks:

```sh
agentcov install-codex-hooks --user
```

For a repo-local install instead:

```sh
agentcov install-codex-hooks --repo
```

Backfill an existing session:

```sh
agentcov backfill --agent auto --session-id <session-id>
agentcov backfill --agent auto --path ~/.codex/sessions/<session>.jsonl
agentcov backfill --agent pi --path ~/.pi/agent/sessions/<encoded-cwd>/<session>.jsonl
```

Import ranges recorded by another tool:

```sh
agentcov import agent-coverage.json
```

`import` accepts `agent-coverage`-style hierarchical JSON, or a plain array of
`{"cmd": "...", "ranges": ["src/app.ts:1:20"]}` entries.

Generate reports:

```sh
agentcov report --format lcov --counts full --out agentcov.info
agentcov report --format gcov --counts binary --out-dir coverage-gcov
agentcov html --out agentcov.html
agentcov summary
agentcov unread --limit 20
```

Render LCOV with standard tooling:

```sh
genhtml agentcov.info --output-directory coverage-html
```

## What It Captures

- Codex hook payloads from `Bash`, `apply_patch`, and MCP tools.
- Codex, Claude Code, and Pi transcript backfill from session id or JSONL path.
  Pi child sessions linked by `parentSession` are included when backfilling a
  Pi session path.
- Common shell reads: `sed`, `head`, `tail`, `cat`, `awk`, and `nl|sed`.
- `rg` and `grep` output as `search_seen`, separate from direct reads. Match
  lines and context lines are counted separately in the native JSON and HTML.
- Git-tracked text files that were never read, so untouched files show up with
  zero coverage.

Unsupported read-like shell shapes are recorded as unknown events rather than
being guessed as full-file reads.

## Outputs

- `.agentcov/events.jsonl`: append-only observed read events.
- `.agentcov/coverage.json`: native per-file and per-line coverage data.
- `agentcov.info`: LCOV tracefile compatible with `genhtml`.
- `coverage-gcov/*.gcov`: textual gcov-style files.
- `agentcov.html`: self-contained heatmap report.

`--counts full` writes observed read counts into LCOV/gcov output. `--counts
binary` writes `1` for read lines and `0` for unread lines.

The native JSON and HTML include compact attribution for read lines and ranges:
agent, session id, source, tool name, command, task path, timestamp, and
confidence when available. Unknown events are also surfaced so unsupported
commands can be audited instead of silently disappearing.

## Configuration

Create `.agentcov.toml` at the repo root when defaults need adjustment:

```toml
storage_dir = ".agentcov"
exclude = [".agentcov/", "node_modules/", "vendor/", "dist/", "build/", ".git/"]
include_lockfiles = true
auto_reports = ["json"]
```

`auto_reports` controls extra reports written on Codex `Stop`. JSON coverage is
always refreshed; add `lcov`, `gcov`, or `html` to write shareable reports under
`.agentcov/reports/`.

Recommended local ignore rules:

```gitignore
.agentcov/
```

Commit or archive `.agentcov/coverage.json`, LCOV, gcov, or HTML outputs only when
you want a durable audit artifact.

## Privacy

By default, `agentcov` stores commands, paths, line ranges, timestamps, session and
tool ids, agent names, and task attribution. Backfilled transcripts may include
the agent's prompt or task label in `task_path` so reports can explain why a
line was inspected. It does not store raw tool output or source snippets in the
event log.

The native coverage JSON and HTML report include attribution from the event log.
The HTML report also embeds source lines because it is a source viewer; share
those files with the same care as the repository and transcript metadata.

## Roadmap

Not shipped yet:

- Richer HTML views: task and subagent filtering, a recency mode, and a
  directory treemap.
- Optional user-level storage, so coverage history can span repositories.
- Transcript backfill for agents beyond Codex, Claude Code, and Pi.
- Opt-in range repair for command shapes that are currently recorded as unknown,
  with inferred ranges marked as lower confidence.

## Development

```sh
make dev      # install the dev dependency group
make test     # pytest
make lint     # ruff format --check, ruff check, ty check
make format   # ruff format and ruff check --fix
make check    # lint and test
```

See [AGENTS.md](AGENTS.md) for the module layout, contribution notes, and the
release process.
