# aicov Plan

## Goal

Build a gcov/lcov-compatible coverage tool for AI coding agents. Instead of
tracking which source lines were executed by a program, `aicov` tracks which
source lines were read by an AI agent, how often they were read, and which task,
subagent, command, or tool call caused the read.

The tool should support existing coverage consumers through LCOV and textual
`.gcov` output, while also producing a richer native format for future agents
and custom heatmap views.

Initial product direction:

- Ship as a standalone CLI first.
- Provide `install-codex-hooks` to wire the CLI into Codex.
- Implement the MVP in Python.
- Focus live hooks on Codex. Support transcript backfill for Codex and Claude
  Code, with Cursor and other agents later.
- Keep unresolved command handling conservative; do not use LLM-assisted
  inference by default.
- Store repo-specific data in `.aicov/` by default, with optional user-level
  storage for cross-repo history.
- Treat search hits from `rg` and `grep` as `search_seen`, separate from direct
  read evidence.
- Prioritize auditing what has been inspected and helping review agents find
  unread code, not primarily avoiding rereads.
- Capture continuously through hooks and keep report generation cheap enough to
  run on `Stop`; also support explicit manual report commands.
- On `Stop`, update `.aicov/coverage.json` by default. Generate LCOV, `.gcov`,
  and HTML manually unless config enables automatic report generation.
- Focus coverage scope on git-tracked files. Include tracked files that were
  never read in coverage reports, so reviewers can see untouched files and
  directories.
- Count source lines by reading tracked files during report generation; those
  reads are tool/report reads, not AI-agent read coverage.
- Store git revision and dirty-state metadata with generated reports because
  line numbers drift.
- Avoid storing raw tool outputs by default. Store only commands, ranges, and
  metadata unless debug capture is explicitly enabled.
- Prioritize LCOV output first, custom HTML second, and textual `.gcov` third.
- Generate custom HTML as a self-contained static file for easy sharing. Split
  CSS/JS later only if the artifact becomes unwieldy.
- Support an optional `.aicov.toml` config file, with sane defaults when it is
  absent.
- Use modern Python packaging and tooling: `pyproject.toml`, `uv`, console
  script entry point, `ruff` for lint/format, and `pytest` for tests.
- Recommend git-ignoring raw `.aicov/` event data, while allowing selected
  snapshots to be copied or merged later.

## Prior Art

`asymmetric-research/agent-coverage` is useful prior art. It parses Codex or
Claude session transcripts into task-scoped file ranges shaped as
`path:start:end`, then renders those ranges in a static browser viewer.

Adopt these ideas:

- Use `path:start:end` as a simple internal and import/export range format.
- Preserve hierarchical attribution: user request, commentary, checklist,
  reasoning summary, subagent, and command.
- Use deterministic shell parsing before falling back to weaker inference.
- Aggregate repeated ranges into unique line counts for summaries.

Do not directly adopt the code without a license. Also avoid making LLM-based
range repair a default path; unresolved commands should stay explicit unless the
user opts into a future inference mode.

## Architecture

### 1. Core Data Model

Represent every observed read as a structured event:

```json
{
  "session_id": "string",
  "turn_id": "string",
  "tool_use_id": "string",
  "timestamp": "ISO-8601",
  "cwd": "/repo",
  "agent": "codex",
  "source": "codex-post-tool-use",
  "tool_name": "Bash",
  "command": "sed -n '120,180p' src/app.ts",
  "file": "src/app.ts",
  "ranges": [
    {
      "start": 120,
      "end": 180,
      "confidence": "exact",
      "weight": 1.0
    }
  ],
  "task_path": ["user request", "commentary", "command"]
}
```

Aggregate events into per-file and per-line facts:

- `read_count`: total times the line appeared in resolved read ranges.
- `unique_read_count`: distinct read events covering the line.
- `search_seen_count`: times the line appeared as a search hit or search
  context line.
- `last_read_at`: latest observed read timestamp.
- `first_read_at`: first observed read timestamp.
- `sources`: hook, transcript backfill, import, or manual source.
- `commands`: command/tool snippets that caused the read.
- `task_paths`: task/subagent/commentary ownership.
- `confidence`: exact, inferred, low, or unknown.
- `attention_score`: weighted score for heatmap ranking.

### 2. Codex Hook Capture

Install Codex hooks with these roles:

- `PostToolUse`: record completed `Bash`, `apply_patch`, and MCP tool calls.
- `PreToolUse`: optionally inject read hints before repeated reads.
- `Stop`: flush session summaries and derived report files.

Default matcher:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash|apply_patch|mcp__.*",
        "hooks": [
          {
            "type": "command",
            "command": "aicov hook post-tool-use",
            "timeout": 30,
            "statusMessage": "Recording AI read coverage"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash|mcp__.*",
        "hooks": [
          {
            "type": "command",
            "command": "aicov hook pre-tool-use",
            "timeout": 5
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "aicov hook stop",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

The hook command should append normalized read events to local storage
continuously. Start with project-local JSONL under `.aicov/` for debuggability;
add optional user-level storage if cross-repo history becomes important. Add
SQLite only if query performance or retention management requires it.

Hook installation should support both:

- User-level hooks in `~/.codex/hooks.json`.
- Repo-local hooks in `.codex/hooks.json`.

Default to user-level install unless the user passes a repo-local flag.

### 3. Transcript Backfill

Codex hook interception is not guaranteed to catch every shell path. Add a
post-hoc backfill command that parses Codex session JSONL and fills missed
events.

Backfill should:

- Read `~/.codex/sessions` and `~/.claude/projects` by session id or
  transcript path.
- Extract user requests, visible commentary, checklist updates, subagents, and
  command/tool calls.
- Reconstruct task/subagent ownership for each read event.
- Merge with hook events by `session_id`, `turn_id`, `tool_use_id`, command, and
  timestamp where available.
- Mark backfilled events as `source: transcript-backfill`.

### 4. Read-Range Parsing

Implement deterministic parsers first.

Supported MVP command shapes:

- `sed -n '10,80p' file`
- `sed -n '10,80p;120,160p' file`
- `nl -ba file | sed -n '10,80p'`
- `cat file | sed -n '10,80p'`
- `for f in a b c; do sed -n '1,40p' "$f"; done`
- `head -n 40 file`
- `tail -n 40 file`
- `awk 'NR>=10 && NR<=80' file`
- Direct Codex or MCP file-read tool payloads when line ranges are explicit.

Known no-range commands should return no read ranges:

- `pwd`, `echo`, `printf`, `true`, `false`, `which`, `command -v`
- `git status`, `git rev-parse`
- metadata-only `ls`, `find`, `wc`, `sort`

Search commands need separate handling:

- `rg` and `grep` without file output context are not direct read coverage.
- If output includes `path:line:` matches, record those lines as search hits,
  with lower weight than a focused file read.
- If context flags like `-C`, `-A`, or `-B` are present and output is parseable,
  include those context ranges.
- Search hits should be stored as `search_seen`, not `read`, so reports can
  distinguish directly inspected source from lines only surfaced by search.

Ambiguous commands should not silently become full-file reads. Record them as
unknown or low-confidence events:

```json
{
  "command": "complex shell pipeline",
  "file": null,
  "ranges": [],
  "confidence": "unknown",
  "reason": "unsupported shell syntax"
}
```

LLM-assisted inference is not part of the default path. If it is added later,
it should be explicit, opt-in, and all inferred ranges should be marked with
their lower confidence.

### 5. Aggregation and Heat Data

For each source file, compute:

- Binary coverage: line was read at least once.
- Frequency: number of times a line was read.
- Unique reads: number of distinct read events covering the line.
- Search-seen counts: number of times a line appeared in search output.
- Recency: time since last read.
- Attention score: weighted read intensity.

Suggested weighting:

- Focused `sed`/direct line read: `1.0`
- `head`/`tail`: `0.8`
- Whole-file `cat`: `0.4`
- Search hit line: `0.35`, but tracked as `search_seen`, not direct read.
- Search context line: `0.2`, but tracked as `search_seen`, not direct read.
- Low-confidence inference: `0.1`

Keep raw counts separate from weighted attention scores so reports stay
explainable.

### 6. Output Formats

#### Scope and Filtering

Coverage reports should use git-tracked files as the default inventory. This
keeps the report focused on project-owned files and naturally avoids most
ignored build artifacts.

Default behavior:

- Include git-tracked text/source files.
- Exclude binary files.
- Exclude common noisy tracked paths by default, with config and CLI overrides:
  `node_modules/`, `vendor/`, `dist/`, `build/`, `.next/`, `coverage/`, and
  other generated-output directories.
- Treat lockfiles and generated files as configurable. They can be included for
  audit runs that care about dependency/config inspection.
- Read files during report generation to compute `LF` and render source views;
  do not record those reads as AI-agent read events.

Config overrides should live in optional `.aicov.toml`.

#### Native `.aicov/coverage.json`

Primary rich output for agents and custom UI.

It should include:

- Schema version.
- Session metadata.
- Git metadata: repository root, `HEAD` commit, branch if available, dirty
  state, and tracked-file inventory hash when practical.
- Files and line counts.
- Read ranges.
- Search-seen ranges.
- Per-line heat data.
- Task/subagent/command attribution.
- Unknown and low-confidence events.
- Source revision metadata when available.

#### LCOV

Write `aicov.info` as a standard LCOV tracefile:

```text
TN:<session-or-task-name>
SF:<source-file>
DA:<line>,<read_count>
LH:<number-of-lines-with-read-count>
LF:<total-source-lines>
end_of_record
```

This maps AI read count onto LCOV execution count. Existing tools such as
`genhtml` can then render a useful compatibility report.

The MVP should include git-tracked files even when they were never read. For
those files, emit `SF` records with `LF` set to total file lines and `LH:0`.
This makes completely uninspected files visible to coverage reviewers.

Support count modes:

- `--counts full`: write actual `read_count` values into `DA` records.
- `--counts binary`: write `1` for any read line and `0`/absence for unread
  lines.

Use `full` by default for heatmap-friendly compatibility reports, while keeping
`binary` available for tools or workflows that expect traditional covered vs
uncovered semantics.

#### Textual `.gcov`

Write one `.gcov`-style file per source file:

```text
        -:    0:Source:src/app.ts
        2:  120:const value = compute();
    #####:  121:const unread = false;
```

Use read counts as execution counts. Use `#####` for source lines that were not
read and `-` for non-counted metadata lines.

#### Optional JSON Gcov

Later, consider `.gcov.json.gz` output shaped like GCC's JSON format. This is
less important than LCOV because many tools expect compiler-generated gcov data,
while LCOV tracefiles are easier to consume directly.

### 7. Heatmap Reporting

Build a custom heatmap report because LCOV cannot preserve task attribution.

Generate the initial report as a single self-contained static HTML file so it
can be shared without a server or asset directory. If the report becomes too
large, later add an option to split HTML, CSS, JS, and data.

Views:

- Source viewer with highlighted lines.
- File tree with read percentage and read count.
- Unread file/range list sorted by path.
- Directory/file treemap.
- Task/subagent filter panel.
- Command/detail inspector for selected lines.

Modes:

- `coverage`: unread vs read.
- `frequency`: stronger color for repeated reads.
- `search_seen`: lines surfaced by search but not directly read.
- `recency`: recently read lines stronger.
- `attention`: weighted read score.

Line detail should answer:

- Which command read this line?
- Which user request or subagent owned that command?
- How many times was this line read?
- Was this line directly read, only search-seen, or never observed?
- Was this exact, inferred, or low confidence?
- What nearby ranges remain unread?

### 8. Agent-Facing Audit Guidance

Use generated summaries and optional hook context to help auditing agents focus
on unread or lightly read code. This is not primarily a reread-prevention
feature; rereads can be useful during review.

Examples:

- "Unread regions in `src/app.ts`: `1-119`, `181-240`."
- "This file has 72% read coverage; the bottom half has not been inspected."
- "High-risk files with no observed reads: `src/auth/session.ts`,
  `src/billing/webhook.ts`."
- "This task focused on `src/api/*`; related validation code in `src/core/*`
  is still unread."

Guidance should be short and non-blocking. It can be surfaced in reports,
summaries, and later through `PreToolUse` additional context when helpful.

### 9. Import and Compatibility

Support importing `agent-coverage`-style hierarchical JSON:

- `format: hierarchical-v1`
- `tasks[]`
- `kind`, `prompt`, `status`, `synthetic`
- `coverage[]` entries with `cmd` and `ranges`
- `children[]`

Also support simple arrays of:

```json
[
  { "cmd": "sed -n '1,20p' file", "ranges": ["file:1:20"] }
]
```

This makes it easy to compare old transcript-only runs with new hook-first
runs.

### 10. CLI

Proposed commands:

```sh
aicov install-codex-hooks --user
aicov install-codex-hooks --repo
aicov uninstall-codex-hooks --user
aicov uninstall-codex-hooks --repo
aicov hook post-tool-use
aicov hook pre-tool-use
aicov hook stop
aicov backfill --agent codex --session-id <id>
aicov backfill --agent claude --session-id <id>
aicov import agent-coverage.json
aicov report --format lcov --counts full --out aicov.info
aicov report --format lcov --counts binary --out aicov-binary.info
aicov report --format gcov --counts full --out-dir coverage-gcov
aicov report --format json --out .aicov/coverage.json
aicov html --out aicov-html
aicov summary
aicov unread
```

Install/uninstall should:

- Back up existing `~/.codex/hooks.json`.
- Back up existing `.codex/hooks.json` for repo-local installs.
- Merge without deleting unrelated hooks.
- Explain Codex hook trust review.
- Support dry-run output.
- Require a git root for repo-local hook install unless `--force` is passed.

### 11. Python Project Shape

Use a normal installable Python package from the start:

- `pyproject.toml`
- `uv` for environment and dependency management
- `aicov` console script entry point
- `ruff` for linting and formatting
- `pytest` for tests
- `src/aicov/` package layout
- `tests/` for fixtures and unit tests
- no required runtime web framework for the HTML report

Keep runtime dependencies minimal. Prefer standard-library parsing and file I/O
unless a dependency clearly reduces risk.

### 12. Tests

Add fixtures and tests for:

- Codex `PostToolUse`, `PreToolUse`, and `Stop` payloads.
- User-level and repo-local hook install/uninstall merge behavior.
- Transcript parsing for user prompts, commentary, checklist, and subagents.
- Shell parser edge cases.
- Path normalization and repo-relative paths.
- Git tracked-file inventory and dirty-state metadata.
- Ambiguous commands and unknown-read records.
- `search_seen` records from grep/ripgrep output.
- LCOV output.
- Textual `.gcov` output.
- Optional `.aicov.toml` config parsing and default filtering.
- Native `.aicov/coverage.json`.
- Heatmap aggregation modes.
- Import of `agent-coverage` hierarchical JSON.

### 13. MVP Cut

The first useful version should include:

1. Hook capture to JSONL.
2. Deterministic read parser for common `sed`, `nl|sed`, `cat|sed`, `head`,
   `tail`, and direct file reads.
3. Per-line read count aggregation.
4. Conservative unknown records for unsupported commands.
5. Search-hit handling for `rg`/`grep` output.
6. LCOV export with `--counts full|binary`.
7. Native `.aicov/coverage.json`.
8. Git-tracked file inventory so never-read files appear in reports.
9. Optional `.aicov.toml` config with default excludes.
10. Single-file static HTML heatmap.
11. Transcript backfill for Codex and Claude Code sessions.
12. `aicov unread` or equivalent summary to direct audit attention to unread
    files and ranges.

After that, add richer task filtering, recency/attention modes, import support,
PreToolUse context, and support for non-Codex agents.

## Git Ignore Guidance

The installer or initialization command should suggest ignoring raw local event
data:

```gitignore
.aicov/events.jsonl
.aicov/raw/
.aicov/tmp/
```

Generated summaries such as `.aicov/coverage.json` can be copied, archived, or
merged later when the user wants a durable audit artifact.
