# aicov

`aicov` tracks which lines in a repository have been read by AI coding agents.
It writes ordinary LCOV and textual `.gcov`-style reports so existing coverage
tools can inspect AI read coverage, and it also writes a richer native JSON
format for agent audit workflows.

The MVP is Codex-focused:

- install Codex hooks with `aicov install-codex-hooks --user` or `--repo`
- capture hook events to `.aicov/events.jsonl`
- parse common `sed`, `head`, `tail`, `cat`, `awk`, `rg`, and `grep` reads
- distinguish direct reads from `search_seen` lines
- include git-tracked files that were never read
- generate LCOV, gcov-style text, JSON, and a single-file HTML heatmap

Development:

```sh
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format .
```
