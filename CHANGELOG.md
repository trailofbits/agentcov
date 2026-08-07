# Changelog

## Unreleased

First release of `agentcov`, which records the lines of a repository that AI coding
agents actually read and renders them as coverage.

- `agentcov report` writes LCOV tracefiles and textual `.gcov` files, `agentcov html`
  writes a self-contained heatmap, and `agentcov summary` and `agentcov unread` print
  read coverage and the largest unread files. `--counts full` records observed
  read counts; `--counts binary` records read/unread.
- `agentcov install-codex-hooks` installs Codex hooks for the user or a single
  repository, capturing reads live from `Bash`, `apply_patch`, and MCP tools.
- `agentcov backfill` replays an existing Codex, Claude Code, or Pi session by
  session id or transcript path, with `--agent auto` detecting which. Pi child
  sessions linked by `parentSession` are included.
- Reads are recognized from `sed`, `head`, `tail`, `cat`, `awk`, and `nl | sed`.
  `rg` and `grep` output is recorded separately as `search_seen`, with match
  lines and context lines counted apart from each other.
- Command shapes that cannot be resolved to a line range are recorded as unknown
  events with a reason rather than counted as whole-file reads.
- Git-tracked text files that were never read are reported at zero coverage, so
  untouched files remain visible.
- Reports carry attribution for each read: agent, session, tool, command, task,
  timestamp, and confidence.
- `.agentcov.toml` configures the storage directory, exclusions, lockfile handling,
  and which reports are refreshed automatically on Codex `Stop`.
