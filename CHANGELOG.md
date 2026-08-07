# Changelog

## 0.1.0

First release of `agentcov`, which records the lines of a repository that AI coding
agents actually read and renders them as coverage.

- `agentcov report` writes LCOV tracefiles, textual `.gcov` files, and native JSON
  with per-line read counts, `agentcov html` writes a self-contained heatmap, and
  `agentcov summary` prints repository-wide read coverage while `agentcov unread`
  lists each file's unread lines and ranges. `--counts full` writes observed read
  counts into LCOV and gcov output; `--counts binary` writes read/unread.
- `agentcov install-codex-hooks` installs Codex hooks for the user or a single
  repository, capturing reads live from `Bash`, `apply_patch`, and MCP tools, and
  `agentcov uninstall-codex-hooks` removes them again.
- `agentcov backfill` replays an existing Codex, Claude Code, or Pi session by
  session id or transcript path, with `--agent auto` detecting which. Pi child
  sessions linked by `parentSession` are included.
- `agentcov import` reads ranges recorded by another tool, either
  `agent-coverage`-style JSON or a plain array of command and range entries.
- Reads are recognized from `sed`, `head`, `tail`, `cat`, `awk`, `sort`, and
  `cat`/`nl` piped into `sed`, including inside command lines joined with `&&`
  or `;` and inside simple `for` loops. `rg` and `grep` output is recorded
  separately as `search_seen`, with match lines and context lines counted apart
  from each other.
- Command shapes that cannot be resolved to a line range are recorded as unknown
  events with a reason rather than counted as whole-file reads.
- Git-tracked text files that were never read are reported at zero coverage, so
  untouched files remain visible.
- Reports carry attribution for each read: agent, session, tool, command, task,
  timestamp, and confidence.
- `.agentcov.toml` configures the storage directory, exclusions, lockfile handling,
  and which reports are refreshed automatically on Codex `Stop`.
- Published to PyPI as `agentcov`, requiring Python 3.11 or newer and no runtime
  dependencies.
