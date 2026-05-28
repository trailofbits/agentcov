from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .aggregate import coverage_files, top_unread_files


def write_lcov(
    coverage: dict[str, Any],
    *,
    out: Path,
    counts: str = "full",
    test_name: str = "aicov",
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
    for rel, file_cov in sorted(files.items()):
        source_path = root / rel
        source_lines = _read_source_lines(source_path)
        target = out_dir / f"{_safe_report_path(rel)}.gcov"
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = [f"{'-':>9}:{0:>5}:Source:{rel}"]
        for line_number in range(1, file_cov.line_count + 1):
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
        "files": rendered_files,
        "unknown_events": coverage.get("unknown_events", []),
    }


def _render_html(data: dict[str, Any]) -> str:
    json_data = json.dumps(data, separators=(",", ":"))
    escaped_json = json_data.replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__AICOV_DATA__", escaped_json)


HTML_STYLE = """
:root {
  color-scheme: light;
  --bg: #f7f8fa;
  --panel: #ffffff;
  --text: #1f2933;
  --muted: #667085;
  --border: #d7dde5;
  --read: #1b9a6b;
  --search: #2d6cdf;
  --unread: #d64545;
  --accent: #7a4d00;
  font-family:
    Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
    "Segoe UI", sans-serif;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--panel);
  position: sticky;
  top: 0;
  z-index: 2;
}
h1 {
  font-size: 18px;
  margin: 0;
  font-weight: 700;
}
button,
select,
input {
  border: 1px solid var(--border);
  background: #fff;
  color: var(--text);
  border-radius: 6px;
  padding: 7px 9px;
  font: inherit;
}
button.active {
  border-color: var(--search);
  box-shadow: inset 0 0 0 1px var(--search);
}
.metrics {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  color: var(--muted);
  font-size: 13px;
}
.layout {
  display: grid;
  grid-template-columns: minmax(260px, 360px) 1fr;
  min-height: calc(100vh - 61px);
}
aside {
  border-right: 1px solid var(--border);
  background: var(--panel);
  overflow: auto;
  max-height: calc(100vh - 61px);
}
main {
  overflow: auto;
  max-height: calc(100vh - 61px);
}
.toolbar {
  display: grid;
  gap: 8px;
  padding: 12px;
  border-bottom: 1px solid var(--border);
}
.modes {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.file-list {
  display: grid;
}
.file-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 10px;
  align-items: center;
  padding: 9px 12px;
  border-bottom: 1px solid #edf0f4;
  cursor: pointer;
}
.file-row:hover,
.file-row.selected {
  background: #eef4ff;
}
.file-path,
.unread-item,
.source-head strong,
.source {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.file-path {
  font-size: 12px;
  overflow-wrap: anywhere;
}
.file-meta {
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}
.unread-panel {
  padding: 12px;
  border-top: 1px solid var(--border);
}
.unread-panel h2 {
  font-size: 13px;
  margin: 0 0 8px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: .04em;
}
.unread-item {
  font-size: 12px;
  padding: 5px 0;
  overflow-wrap: anywhere;
}
.source-head {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  background: #fff;
  position: sticky;
  top: 0;
  z-index: 1;
}
.source {
  font-size: 12px;
  line-height: 1.45;
  overflow-x: auto;
}
.line {
  display: flex;
  min-width: 100%;
  border-bottom: 1px solid rgba(215, 221, 229, .45);
}
.line-no {
  flex: 0 0 64px;
  padding: 0 10px;
  text-align: right;
  color: var(--muted);
  user-select: none;
  border-right: 1px solid rgba(215, 221, 229, .8);
}
.line-code {
  flex: 1 0 auto;
  padding: 0 12px;
  white-space: pre;
}
.line.read .line-code {
  background: rgba(27, 154, 107, .14);
}
.line.unread .line-code {
  background: rgba(214, 69, 69, .09);
}
.line.search-only .line-code {
  background: rgba(45, 108, 223, .12);
}
.line:hover .line-code {
  outline: 1px solid rgba(45, 108, 223, .45);
  outline-offset: -1px;
}
.inspector {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
  background: #fff;
  color: var(--muted);
}
.inspector code {
  color: var(--text);
}
@media (max-width: 850px) {
  .layout {
    grid-template-columns: 1fr;
  }
  aside {
    max-height: 44vh;
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }
  main {
    max-height: none;
  }
  header {
    align-items: flex-start;
    flex-direction: column;
  }
}
""".strip()


HTML_BODY = """
<header>
  <div>
    <h1>aicov report</h1>
    <div class="metrics" id="metrics"></div>
  </div>
  <div class="metrics" id="git"></div>
</header>
<div class="layout">
  <aside>
    <div class="toolbar">
      <input id="filter" placeholder="Filter files">
      <div class="modes">
        <button data-mode="coverage" class="active">Coverage</button>
        <button data-mode="frequency">Frequency</button>
        <button data-mode="search">Search</button>
        <button data-mode="attention">Attention</button>
      </div>
    </div>
    <div id="files" class="file-list"></div>
    <div class="unread-panel">
      <h2>Unread</h2>
      <div id="unread"></div>
    </div>
  </aside>
  <main>
    <div class="source-head" id="source-head">Select a file</div>
    <div id="source" class="source"></div>
    <div id="inspector" class="inspector">Line details appear here.</div>
  </main>
</div>
<script id="coverage-data" type="application/json">__AICOV_DATA__</script>
""".strip()


HTML_SCRIPT = r"""
const DATA = JSON.parse(document.getElementById('coverage-data').textContent);
let selected = Object.keys(DATA.files).sort()[0] || null;
let mode = 'coverage';
const metrics = DATA.summary || {};
document.getElementById('metrics').textContent =
  `${metrics.read_lines || 0}/${metrics.total_lines || 0} lines read ` +
  `(${metrics.read_percent || 0}%) · ${metrics.files || 0} files · ` +
  `${metrics.unknown_events || 0} unknown events`;
const git = DATA.git || {};
document.getElementById('git').textContent = [
  git.branch,
  git.head ? git.head.slice(0, 10) : null,
  git.dirty ? 'dirty' : null,
].filter(Boolean).join(' · ');

document.querySelectorAll('[data-mode]').forEach(button => {
  button.addEventListener('click', () => {
    mode = button.dataset.mode;
    document
      .querySelectorAll('[data-mode]')
      .forEach(item => item.classList.toggle('active', item === button));
    renderSource();
  });
});
document.getElementById('filter').addEventListener('input', renderFileList);

function renderFileList() {
  const query = document.getElementById('filter').value.toLowerCase();
  const container = document.getElementById('files');
  container.innerHTML = '';
  Object.entries(DATA.files).sort(([a], [b]) => a.localeCompare(b)).forEach(
    ([path, file]) => {
      if (query && !path.toLowerCase().includes(query)) return;
      const row = document.createElement('div');
      row.className = 'file-row' + (path === selected ? ' selected' : '');
      row.innerHTML =
        `<div class="file-path">${escapeHtml(path)}</div>` +
        `<div class="file-meta">${file.read_percent}%</div>`;
      row.addEventListener('click', () => {
        selected = path;
        renderFileList();
        renderSource();
      });
      container.appendChild(row);
    },
  );
  renderUnread();
}

function renderUnread() {
  const container = document.getElementById('unread');
  const rows = Object.entries(DATA.files)
    .filter(([, file]) => file.line_count > file.read_lines)
    .sort(([a], [b]) => a.localeCompare(b))
    .slice(0, 80);
  container.innerHTML = rows.map(([path, file]) => {
    const ranges = (file.unread_ranges || [])
      .slice(0, 4)
      .map(range => range.start === range.end ? range.start : `${range.start}-${range.end}`)
      .join(', ');
    return `<div class="unread-item">${escapeHtml(path)}: ${ranges}</div>`;
  }).join('') || '<div class="unread-item">No unread tracked lines.</div>';
}

function renderSource() {
  const file = DATA.files[selected];
  const head = document.getElementById('source-head');
  const source = document.getElementById('source');
  if (!file) {
    head.textContent = 'Select a file';
    source.innerHTML = '';
    return;
  }
  head.innerHTML =
    `<strong>${escapeHtml(selected)}</strong> · ` +
    `${file.read_lines}/${file.line_count} read · ` +
    `${file.search_seen_lines} search-seen`;
  source.innerHTML = '';
  const lineStats = file.lines || {};
  const lines = file.source || [];
  for (let i = 1; i <= file.line_count; i++) {
    const stats = lineStats[String(i)] || {};
    const row = document.createElement('div');
    row.className = 'line ' + lineClass(stats);
    row.style.background = heatBackground(stats);
    row.innerHTML =
      `<div class="line-no">${i}</div>` +
      `<div class="line-code">${escapeHtml(lines[i - 1] || '')}</div>`;
    row.addEventListener('click', () => inspectLine(i, stats));
    source.appendChild(row);
  }
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
  const color = mode === 'search' ? '45,108,223' : '27,154,107';
  return `rgba(${color}, ${value})`;
}

function inspectLine(line, stats) {
  const commands = (stats.commands || [])
    .map(command => `<div><code>${escapeHtml(command)}</code></div>`)
    .join('');
  document.getElementById('inspector').innerHTML =
    `<strong>${escapeHtml(selected)}:${line}</strong> · ` +
    `read ${stats.read_count || 0} · ` +
    `search-seen ${stats.search_seen_count || 0} · ` +
    `attention ${stats.attention_score || 0}` +
    (commands ? `<div style="margin-top:8px">${commands}</div>` : '');
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

renderFileList();
renderSource();
""".strip()


HTML_TEMPLATE = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>aicov report</title>
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
