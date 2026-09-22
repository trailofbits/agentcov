from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .aggregate import coverage_files, top_unread_files


def write_lcov(
    coverage: dict[str, Any],
    *,
    out: Path,
    counts: str = "full",
    test_name: str = "agentcov",
) -> Path:
    files = coverage_files(coverage)
    lines: list[str] = []
    for rel, file_cov in sorted(files.items()):
        lines.append(f"TN:{test_name}")
        lines.append(f"SF:{rel}")
        hit_lines = 0
        for line in range(1, file_cov.line_count + 1):
            count = file_cov.count_for_line(line, mode=counts)
            if count:
                hit_lines += 1
            lines.append(f"DA:{line},{count}")
        lines.append(f"LH:{hit_lines}")
        lines.append(f"LF:{file_cov.line_count}")
        lines.append("end_of_record")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return out


def write_gcov(
    coverage: dict[str, Any],
    *,
    root: Path,
    out_dir: Path,
    counts: str = "full",
) -> Path:
    files = coverage_files(coverage)
    out_dir.mkdir(parents=True, exist_ok=True)
    targets: dict[Path, str] = {}
    for rel, file_cov in sorted(files.items()):
        source_path = root / rel
        source_lines = _read_source_lines(source_path)
        target = _gcov_target(out_dir, rel, targets)
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = [f"{'-':>9}:{0:>5}:Source:{rel}"]
        line_total = len(source_lines) if source_lines else file_cov.line_count
        for line_number in range(1, line_total + 1):
            count = file_cov.count_for_line(line_number, mode=counts)
            count_text = str(count) if count else "#####"
            source = source_lines[line_number - 1] if line_number - 1 < len(source_lines) else ""
            rows.append(f"{count_text:>9}:{line_number:>5}:{source}")
        target.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return out_dir


def write_html(coverage: dict[str, Any], *, root: Path, out: Path) -> Path:
    if out.suffix.lower() != ".html":
        out = out / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    data = _html_data(coverage, root=root)
    out.write_text(_render_html(data), encoding="utf-8")
    return out


def summary_text(coverage: dict[str, Any]) -> str:
    summary = coverage.get("summary", {})
    return "\n".join(
        [
            f"Files: {summary.get('files', 0)}",
            f"Lines: {summary.get('read_lines', 0)}/{summary.get('total_lines', 0)} read "
            f"({summary.get('read_percent', 0.0)}%)",
            f"Search-seen lines: {summary.get('search_seen_lines', 0)}",
            f"Events: {summary.get('events', 0)}",
            f"Unknown events: {summary.get('unknown_events', 0)}",
        ]
    )


def unread_text(coverage: dict[str, Any], *, limit: int | None = None) -> str:
    rows = top_unread_files(coverage, limit=limit)
    if not rows:
        return "No unread tracked lines."
    output: list[str] = []
    for row in rows:
        ranges = ", ".join(_format_range(r["start"], r["end"]) for r in row["unread_ranges"][:8])
        if len(row["unread_ranges"]) > 8:
            ranges += ", ..."
        output.append(
            f"{row['path']}: {row['unread_lines']}/{row['line_count']} unread "
            f"({row['read_percent']}% read) [{ranges}]"
        )
    return "\n".join(output)


def _html_data(coverage: dict[str, Any], *, root: Path) -> dict[str, Any]:
    files = coverage.get("files", {})
    rendered_files = {}
    for rel, file_data in files.items():
        source_lines = _read_source_lines(root / rel)
        rendered_files[rel] = {
            **file_data,
            "source": source_lines,
        }
    return {
        "summary": coverage.get("summary", {}),
        "git": coverage.get("git", {}),
        "sessions": coverage.get("sessions", []),
        "files": rendered_files,
        "unknown_events": coverage.get("unknown_events", []),
    }


def _render_html(data: dict[str, Any]) -> str:
    json_data = json.dumps(data, separators=(",", ":"))
    escaped_json = json_data.replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__AGENTCOV_DATA__", escaped_json)


HTML_STYLE = """
:root {
  color-scheme: light;
  --bg: #f4f6f8;
  --panel: #ffffff;
  --text: #1d2733;
  --muted: #64748b;
  --border: #dbe2ea;
  --read: #17825c;
  --read-soft: rgba(23, 130, 92, .16);
  --search: #2563cf;
  --search-soft: rgba(37, 99, 207, .14);
  --unread: #c2453f;
  --unread-soft: rgba(194, 69, 63, .07);
  --caution: #a16207;
  --caution-soft: rgba(161, 98, 7, .12);
  font-family:
    ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
    "Segoe UI", sans-serif;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
}
button,
select,
input {
  border: 1px solid var(--border);
  background: #fff;
  color: var(--text);
  border-radius: 6px;
  padding: 6px 9px;
  font: inherit;
  font-size: 13px;
}
button {
  cursor: pointer;
}
code,
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
header {
  display: flex;
  align-items: stretch;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--panel);
}
.brand h1 {
  font-size: 17px;
  margin: 0 0 2px;
  font-weight: 700;
}
.brand .git-meta {
  color: var(--muted);
  font-size: 12px;
}
.stats {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: stretch;
}
.stat {
  min-width: 96px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel);
}
.stat .value {
  font-size: 19px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}
.stat .label {
  color: var(--muted);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .05em;
  white-space: nowrap;
}
.stat.hero {
  min-width: 220px;
}
.stat.hero .value {
  color: var(--read);
}
.stat.warn .value {
  color: var(--caution);
}
.coverage-bar {
  height: 6px;
  border-radius: 4px;
  background: rgba(194, 69, 63, .18);
  overflow: hidden;
  margin-top: 6px;
}
.coverage-bar > span {
  display: block;
  height: 100%;
  background: var(--read);
}
.layout {
  display: grid;
  grid-template-columns: minmax(280px, 360px) 1fr;
  min-height: calc(100vh - 79px);
}
aside {
  border-right: 1px solid var(--border);
  background: var(--panel);
  overflow: auto;
  max-height: calc(100vh - 79px);
}
main {
  overflow: auto;
  max-height: calc(100vh - 79px);
}
.toolbar {
  display: grid;
  gap: 8px;
  padding: 12px;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--panel);
  z-index: 2;
}
.toolbar .controls {
  display: flex;
  gap: 8px;
}
.toolbar input {
  flex: 1;
}
.toolbar select {
  max-width: 46%;
}
.nav-overview {
  text-align: left;
  font-weight: 600;
}
.nav-overview.selected {
  border-color: var(--search);
  box-shadow: inset 0 0 0 1px var(--search);
}
.file-row {
  display: block;
  padding: 8px 12px 9px;
  border-bottom: 1px solid #eef1f5;
  cursor: pointer;
}
.file-row:hover,
.file-row.selected {
  background: #eef4ff;
}
.file-line1 {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: baseline;
}
.file-path {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.file-pct {
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.pct-zero { color: var(--unread); }
.pct-low { color: var(--caution); }
.pct-high { color: var(--read); }
.file-bar {
  height: 4px;
  border-radius: 3px;
  background: #e8ebf0;
  overflow: hidden;
  margin-top: 6px;
}
.file-bar > span {
  display: block;
  height: 100%;
  background: var(--read);
}
.file-sub {
  color: var(--muted);
  font-size: 11px;
  margin-top: 4px;
  font-variant-numeric: tabular-nums;
}
.empty-note {
  padding: 14px;
  color: var(--muted);
}
.overview {
  padding: 18px 22px 40px;
  display: grid;
  gap: 18px;
  max-width: 980px;
}
.card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
}
.card h2 {
  font-size: 13px;
  margin: 0 0 10px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: .05em;
}
.card .hint {
  color: var(--muted);
  font-size: 12px;
  margin: -4px 0 10px;
}
table {
  border-collapse: collapse;
  width: 100%;
  font-size: 13px;
}
th, td {
  text-align: left;
  padding: 6px 10px 6px 0;
  border-bottom: 1px solid #eef1f5;
  vertical-align: top;
}
th {
  color: var(--muted);
  font-weight: 500;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .04em;
}
td.num, th.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  padding-right: 18px;
  white-space: nowrap;
}
td.num:last-child, th.num:last-child {
  padding-right: 0;
}
tr.clickable { cursor: pointer; }
tr.clickable:hover td { background: #eef4ff; }
.reason-group {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0;
  margin: 0 0 8px;
}
.reason-group summary {
  cursor: pointer;
  padding: 8px 12px;
  font-size: 13px;
  display: flex;
  justify-content: space-between;
  gap: 10px;
}
.reason-group summary .count {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.reason-items {
  border-top: 1px solid var(--border);
  padding: 8px 12px;
  display: grid;
  gap: 8px;
}
.reason-item code {
  font-size: 12px;
  overflow-wrap: anywhere;
}
.reason-item .meta {
  color: var(--muted);
  font-size: 11px;
  margin-top: 2px;
}
.source-head {
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--panel);
  position: sticky;
  top: 0;
  z-index: 1;
  display: grid;
  gap: 8px;
}
.source-head .row1 {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}
.source-head .back {
  font-size: 12px;
  color: var(--search);
  cursor: pointer;
  background: none;
  border: none;
  padding: 0;
}
.source-head strong {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  overflow-wrap: anywhere;
}
.source-head .file-stats {
  color: var(--muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.source-head .row2 {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  align-items: center;
}
.modes {
  display: flex;
  gap: 0;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}
.modes button {
  border: none;
  border-radius: 0;
  border-right: 1px solid var(--border);
  padding: 5px 10px;
  font-size: 12px;
  background: #fff;
}
.modes button:last-child { border-right: none; }
.modes button.active {
  background: #eef4ff;
  color: var(--search);
  font-weight: 600;
}
.legend {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 11px;
  color: var(--muted);
}
.legend .chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.legend .swatch {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  border: 1px solid var(--border);
}
.swatch.read { background: var(--read-soft); }
.swatch.search { background: var(--search-soft); }
.swatch.unread { background: var(--unread-soft); }
.swatch.lowconf {
  background: var(--caution-soft);
  border-left: 3px solid var(--caution);
}
.source {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  overflow-x: auto;
  padding-bottom: 20px;
}
.line {
  display: flex;
  min-width: 100%;
  cursor: pointer;
}
.line-no {
  flex: 0 0 60px;
  padding: 0 10px;
  text-align: right;
  color: var(--muted);
  user-select: none;
  border-right: 1px solid rgba(219, 226, 234, .9);
}
.line-code {
  flex: 1 0 auto;
  padding: 0 12px;
  white-space: pre;
  border-left: 3px solid transparent;
}
.line.read .line-code { background: var(--read-soft); }
.line.unread .line-code { background: var(--unread-soft); }
.line.search-only .line-code { background: var(--search-soft); }
.line.lowconf .line-code {
  border-left-color: var(--caution);
}
.line:hover .line-code {
  outline: 1px solid rgba(37, 99, 207, .5);
  outline-offset: -1px;
}
.line.focused .line-code {
  outline: 2px solid var(--search);
  outline-offset: -2px;
}
.inspector {
  padding: 12px 16px 24px;
  border-top: 1px solid var(--border);
  background: var(--panel);
  color: var(--muted);
  font-size: 13px;
}
.inspector code { color: var(--text); font-size: 12px; overflow-wrap: anywhere; }
.badge {
  display: inline-block;
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 11px;
  font-weight: 600;
}
.badge.exact { background: var(--read-soft); color: var(--read); }
.badge.inferred, .badge.low, .badge.unknown {
  background: var(--caution-soft);
  color: var(--caution);
}
.detail-list {
  display: grid;
  gap: 8px;
  margin-top: 8px;
}
.detail-item {
  border-left: 2px solid var(--border);
  padding-left: 8px;
}
.detail-meta {
  color: var(--muted);
  font-size: 12px;
  margin-top: 2px;
}
@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  aside {
    max-height: 40vh;
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }
  main { max-height: none; }
  header { flex-direction: column; }
}
""".strip()


HTML_BODY = """
<header>
  <div class="brand">
    <h1>agentcov report</h1>
    <div class="git-meta" id="git"></div>
  </div>
  <div class="stats" id="stats"></div>
</header>
<div class="layout">
  <aside>
    <div class="toolbar">
      <button id="nav-overview" class="nav-overview">&#8962; Overview</button>
      <div class="controls">
        <input id="filter" placeholder="Filter files" aria-label="Filter files">
        <select id="sort" aria-label="Sort files">
          <option value="least-read">Least read first</option>
          <option value="most-read">Most read first</option>
          <option value="attention">Most attention first</option>
          <option value="path">By path</option>
        </select>
      </div>
    </div>
    <div id="files" class="file-list"></div>
  </aside>
  <main id="main"></main>
</div>
<script id="coverage-data" type="application/json">__AGENTCOV_DATA__</script>
""".strip()


HTML_SCRIPT = r"""
const DATA = JSON.parse(document.getElementById('coverage-data').textContent);
const summary = DATA.summary || {};
const git = DATA.git || {};
const fileEntries = Object.entries(DATA.files || {}).map(([path, file]) => {
  let attention = 0;
  const lines = file.lines || {};
  for (const key in lines) attention += lines[key].attention_score || 0;
  return {
    path,
    file,
    attention,
    percent: file.read_percent || 0,
    unread: (file.line_count || 0) - (file.read_lines || 0),
  };
});

let view = 'overview';
let mode = 'coverage';
let focusedLine = null;

document.getElementById('git').textContent = [
  git.branch,
  git.head ? git.head.slice(0, 10) : null,
  git.dirty ? 'dirty' : null,
].filter(Boolean).join(' · ') || 'no git metadata';

function renderStats() {
  const readPct = summary.read_percent || 0;
  const tiles = [
    `<div class="stat hero">
       <div class="label">Lines read by agents</div>
       <div class="value">${readPct}%</div>
       <div class="coverage-bar"><span style="width:${Math.min(100, readPct)}%"></span></div>
       <div class="label" style="margin-top:4px">
         ${summary.read_lines || 0} / ${summary.total_lines || 0} lines</div>
     </div>`,
    statTile('Files', summary.files || 0),
    statTile('Search-seen lines', summary.search_seen_lines || 0),
    statTile('Sessions', summary.sessions || 0),
    statTile('Unknown events', summary.unknown_events || 0, (summary.unknown_events || 0) > 0),
  ];
  if (summary.event_parse_errors) {
    tiles.push(statTile('Log lines skipped', summary.event_parse_errors, true));
  }
  if (summary.superseded_events) {
    tiles.push(statTile('Superseded events', summary.superseded_events));
  }
  document.getElementById('stats').innerHTML = tiles.join('');
}

function statTile(label, value, warn) {
  return `<div class="stat${warn ? ' warn' : ''}">
    <div class="value">${escapeHtml(String(value))}</div>
    <div class="label">${escapeHtml(label)}</div>
  </div>`;
}

function pctClass(percent) {
  if (percent <= 0) return 'pct-zero';
  if (percent < 50) return 'pct-low';
  return 'pct-high';
}

function sortedFiles() {
  const sortKey = document.getElementById('sort').value;
  const rows = [...fileEntries];
  if (sortKey === 'most-read') {
    rows.sort((a, b) => b.percent - a.percent || a.path.localeCompare(b.path));
  } else if (sortKey === 'least-read') {
    rows.sort((a, b) =>
      a.percent - b.percent || b.unread - a.unread || a.path.localeCompare(b.path));
  } else if (sortKey === 'attention') {
    rows.sort((a, b) => b.attention - a.attention || a.path.localeCompare(b.path));
  } else {
    rows.sort((a, b) => a.path.localeCompare(b.path));
  }
  return rows;
}

function renderFileList() {
  const query = document.getElementById('filter').value.toLowerCase();
  const container = document.getElementById('files');
  const rows = sortedFiles()
    .filter(row => !query || row.path.toLowerCase().includes(query));
  document.getElementById('nav-overview').classList.toggle('selected', view === 'overview');
  if (!rows.length) {
    container.innerHTML = '<div class="empty-note">No files match this filter.</div>';
    return;
  }
  container.innerHTML = rows.map(row => {
    const searchNote = row.file.search_seen_lines
      ? ` · ${row.file.search_seen_lines} search-seen`
      : '';
    return `
    <div class="file-row${row.path === view ? ' selected' : ''}"
         data-path="${escapeHtml(row.path)}">
      <div class="file-line1">
        <div class="file-path">${escapeHtml(row.path)}</div>
        <div class="file-pct ${pctClass(row.percent)}">${row.percent}%</div>
      </div>
      <div class="file-bar"><span style="width:${Math.min(100, row.percent)}%"></span></div>
      <div class="file-sub">${row.file.read_lines}/${row.file.line_count} read${searchNote}</div>
    </div>`;
  }).join('');
}

function renderMain() {
  if (view === 'overview') {
    renderOverview();
  } else {
    renderFileView();
  }
}

function renderOverview() {
  const unreadRows = [...fileEntries]
    .filter(row => row.unread > 0)
    .sort((a, b) => b.unread - a.unread || a.path.localeCompare(b.path))
    .slice(0, 10);
  const unreadTable = unreadRows.length ? `
    <table>
      <tr><th>File</th><th class="num">Unread lines</th><th class="num">Read</th></tr>
      ${unreadRows.map(row => `
        <tr class="clickable" data-path="${escapeHtml(row.path)}">
          <td class="mono">${escapeHtml(row.path)}</td>
          <td class="num">${row.unread}</td>
          <td class="num ${pctClass(row.percent)}">${row.percent}%</td>
        </tr>`).join('')}
    </table>` : '<div class="hint">Every tracked line was read at least once.</div>';

  const sessions = DATA.sessions || [];
  const sessionTable = sessions.length ? `
    <table>
      <tr><th>Session</th><th>Agent</th><th class="num">Reads</th>
        <th class="num">Searches</th><th class="num">Unknown</th><th>Tasks</th></tr>
      ${sessions.map(item => {
        const tasks = (item.task_paths || [])
          .map(task => task.join(' › ')).join('; ').slice(0, 120);
        return `
        <tr>
          <td class="mono">${escapeHtml(shortSession(item.session_id) || '(none)')}</td>
          <td>${escapeHtml(item.agent || '')}</td>
          <td class="num">${item.read_events || 0}</td>
          <td class="num">${item.search_seen_events || 0}</td>
          <td class="num">${item.unknown_events || 0}</td>
          <td>${escapeHtml(tasks)}</td>
        </tr>`;
      }).join('')}
    </table>` : '<div class="hint">No session metadata recorded.</div>';

  document.getElementById('main').innerHTML = `
    <div class="overview">
      <div class="card">
        <h2>Start here: least-read files</h2>
        <div class="hint">These files have the most lines no agent has read.
          Click one to inspect it.</div>
        ${unreadTable}
      </div>
      <div class="card">
        <h2>Sessions</h2>
        ${sessionTable}
      </div>
      <div class="card">
        <h2>Unknown Events</h2>
        <div class="hint">Commands agentcov could not attribute to specific
          lines. They are disclosed here instead of being guessed or hidden.</div>
        ${renderUnknownGroups()}
      </div>
    </div>`;
}

function renderUnknownGroups() {
  const events = DATA.unknown_events || [];
  if (!events.length) return '<div class="hint">No unknown events.</div>';
  const groups = new Map();
  for (const event of events) {
    const reason = event.reason || 'unrecognized';
    if (!groups.has(reason)) groups.set(reason, []);
    groups.get(reason).push(event);
  }
  const ordered = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
  return ordered.map(([reason, items], index) => `
    <details class="reason-group"${index === 0 ? ' open' : ''}>
      <summary><span>${escapeHtml(reason)}</span>
        <span class="count">${items.length}</span></summary>
      <div class="reason-items">
        ${items.slice(0, 12).map(event => {
          const label = event.command || event.tool_name || event.file
            || event.source || 'unknown';
          const meta = [event.agent, shortSession(event.session_id), event.source]
            .filter(Boolean).join(' · ');
          const metaHtml = meta ? `<div class="meta">${escapeHtml(meta)}</div>` : '';
          return `<div class="reason-item"><code>${escapeHtml(label)}</code>${metaHtml}</div>`;
        }).join('')}
        ${items.length > 12
          ? `<div class="meta">… and ${items.length - 12} more with this reason</div>`
          : ''}
      </div>
    </details>`).join('');
}

const MODES = [
  ['coverage', 'Coverage', 'Read, search-seen, and unread lines'],
  ['frequency', 'Frequency', 'Darker green = read more times'],
  ['search', 'Search', 'Darker blue = seen in more search results'],
  ['attention', 'Attention', 'Darker green = higher weighted attention score'],
];

function renderFileView() {
  const file = DATA.files[view];
  if (!file) {
    view = 'overview';
    renderOverview();
    return;
  }
  const lineStats = file.lines || {};
  const lines = file.source || [];
  const rows = [];
  for (let i = 1; i <= file.line_count; i++) {
    const stats = lineStats[String(i)] || {};
    const classes = ['line', lineClass(stats)];
    if ((stats.read_count || 0) > 0 && stats.confidence && stats.confidence !== 'exact') {
      classes.push('lowconf');
    }
    if (focusedLine === i) classes.push('focused');
    const heat = heatBackground(stats);
    rows.push(
      `<div class="${classes.join(' ')}" data-line="${i}">` +
      `<div class="line-no">${i}</div>` +
      `<div class="line-code"${heat ? ` style="background:${heat}"` : ''}>` +
      `${escapeHtml(lines[i - 1] || '')}</div>` +
      `</div>`
    );
  }
  document.getElementById('main').innerHTML = `
    <div class="source-head">
      <div class="row1">
        <button class="back" id="back-overview">&larr; Overview</button>
        <strong>${escapeHtml(view)}</strong>
        <span class="file-stats">${file.read_lines}/${file.line_count} read
          · ${file.search_seen_lines} search-seen
          · ${file.line_count - file.read_lines} unread</span>
      </div>
      <div class="row2">
        <div class="modes" id="modes">
          ${MODES.map(([key, label, help]) => {
            const active = key === mode ? ' class="active"' : '';
            return `<button data-mode="${key}" title="${escapeHtml(help)}"` +
              `${active}>${label}</button>`;
          }).join('')}
        </div>
        <div class="legend">
          <span class="chip"><span class="swatch read"></span>Read</span>
          <span class="chip"><span class="swatch search"></span>Search-seen only</span>
          <span class="chip"><span class="swatch unread"></span>Never read</span>
          <span class="chip"><span class="swatch lowconf"></span>Low confidence
            (capped or content changed)</span>
        </div>
      </div>
    </div>
    <div class="source" id="source">${rows.join('')}</div>
    <div class="inspector" id="inspector">Click any line to see who read it,
      when, and with which command.</div>`;
}

function lineClass(stats) {
  if ((stats.read_count || 0) > 0) return 'read';
  if ((stats.search_seen_count || 0) > 0) return 'search-only';
  return 'unread';
}

function heatBackground(stats) {
  if (mode === 'coverage') return '';
  let value = 0;
  if (mode === 'frequency') value = Math.min(0.35, (stats.read_count || 0) * 0.06);
  if (mode === 'search') value = Math.min(0.35, (stats.search_seen_count || 0) * 0.08);
  if (mode === 'attention') value = Math.min(0.35, (stats.attention_score || 0) * 0.06);
  if (!value) return '';
  const color = mode === 'search' ? '37,99,207' : '23,130,92';
  return `rgba(${color}, ${value})`;
}

function inspectLine(line) {
  const file = DATA.files[view];
  if (!file) return;
  const stats = (file.lines || {})[String(line)] || {};
  const confidence = stats.confidence || 'exact';
  const commands = (stats.commands || [])
    .map(command => `<div><code>${escapeHtml(command)}</code></div>`)
    .join('');
  const attribution = renderAttribution(stats.attributions || []);
  document.getElementById('inspector').innerHTML =
    `<strong class="mono">${escapeHtml(view)}:${line}</strong> · ` +
    `read ${stats.read_count || 0}× · ` +
    `search-seen ${stats.search_seen_count || 0}×` +
    ` (${stats.search_hit_count || 0} hit, ${stats.search_context_count || 0} context) · ` +
    `attention ${stats.attention_score || 0} · ` +
    `<span class="badge ${escapeHtml(confidence)}">${escapeHtml(confidence)}</span>` +
    (commands ? `<div class="detail-list">${commands}</div>` : '') +
    attribution;
}

function renderAttribution(items) {
  if (!items.length) return '';
  return (
    '<div class="detail-list">' +
    items.slice(0, 8).map(item => {
      const task = (item.task_path || []).join(' › ');
      const title = [
        item.agent,
        shortSession(item.session_id),
        item.tool_name,
      ].filter(Boolean).join(' · ') || 'event';
      const meta = [
        item.source,
        item.timestamp,
        task,
      ].filter(Boolean).join(' · ');
      const command = item.command ? `<div><code>${escapeHtml(item.command)}</code></div>` : '';
      return (
        `<div class="detail-item">` +
        `<div>${escapeHtml(title)}</div>` +
        (meta ? `<div class="detail-meta">${escapeHtml(meta)}</div>` : '') +
        command +
        `</div>`
      );
    }).join('') +
    '</div>'
  );
}

function shortSession(value) {
  if (!value) return '';
  const text = String(value);
  return text.length > 12 ? text.slice(0, 12) : text;
}

function escapeHtml(value) {
  const replacements = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  };
  return String(value).replace(/[&<>"']/g, char => replacements[char]);
}

document.getElementById('filter').addEventListener('input', renderFileList);
document.getElementById('sort').addEventListener('change', renderFileList);
document.getElementById('nav-overview').addEventListener('click', () => {
  view = 'overview';
  focusedLine = null;
  renderFileList();
  renderMain();
});
document.getElementById('files').addEventListener('click', eventTarget => {
  const row = eventTarget.target.closest('.file-row');
  if (!row) return;
  view = row.dataset.path;
  focusedLine = null;
  renderFileList();
  renderMain();
});
document.getElementById('main').addEventListener('click', eventTarget => {
  const back = eventTarget.target.closest('#back-overview');
  if (back) {
    view = 'overview';
    focusedLine = null;
    renderFileList();
    renderMain();
    return;
  }
  const modeButton = eventTarget.target.closest('[data-mode]');
  if (modeButton) {
    mode = modeButton.dataset.mode;
    renderFileView();
    return;
  }
  const unreadRow = eventTarget.target.closest('tr.clickable');
  if (unreadRow) {
    view = unreadRow.dataset.path;
    focusedLine = null;
    renderFileList();
    renderMain();
    return;
  }
  const line = eventTarget.target.closest('.line');
  if (line) {
    focusedLine = Number(line.dataset.line);
    document.querySelectorAll('.line.focused').forEach(item => item.classList.remove('focused'));
    line.classList.add('focused');
    inspectLine(focusedLine);
  }
});

renderStats();
renderFileList();
renderMain();
""".strip()


HTML_TEMPLATE = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>agentcov report</title>
<style>
{HTML_STYLE}
</style>
</head>
<body>
{HTML_BODY}
<script>
{HTML_SCRIPT}
</script>
</body>
</html>
"""


def _read_source_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()


def _format_range(start: int, end: int) -> str:
    return str(start) if start == end else f"{start}-{end}"


def _safe_report_path(path: str) -> str:
    safe_parts = []
    for part in Path(path).parts:
        if part in {"", ".", ".."} or part.endswith(":"):
            continue
        cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in part)
        if cleaned:
            safe_parts.append(cleaned)
    return "/".join(safe_parts) or "external"


def _gcov_target(out_dir: Path, rel: str, targets: dict[Path, str]) -> Path:
    base = _safe_report_path(rel)
    target = out_dir / f"{base}.gcov"
    if target in targets and targets[target] != rel:
        digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:10]
        target = out_dir / f"{base}.{digest}.gcov"
    targets[target] = rel
    return target
