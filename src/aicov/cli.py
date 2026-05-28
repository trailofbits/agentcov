from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .aggregate import build_coverage
from .backfill import backfill_codex_session
from .config import load_config
from .git import find_repo_root
from .hooks import (
    install_codex_hooks,
    read_hook_payload,
    run_post_tool_use,
    run_pre_tool_use,
    run_stop,
    uninstall_codex_hooks,
)
from .importers import import_agent_coverage
from .report import summary_text, unread_text, write_gcov, write_html, write_lcov
from .storage import append_events, load_events, write_coverage_json


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except Exception as exc:  # noqa: BLE001 - CLI should report cleanly.
        print(f"aicov: error: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aicov")
    parser.add_argument("--root", type=Path, help="Repository root or working directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install-codex-hooks", help="Install Codex hook commands")
    _add_target_flags(install)
    install.add_argument("--dry-run", action="store_true", help="Print merged hooks JSON only")
    install.add_argument(
        "--force",
        action="store_true",
        help="Allow repo-local install outside git",
    )
    install.set_defaults(func=_cmd_install)

    uninstall = subparsers.add_parser("uninstall-codex-hooks", help="Remove aicov Codex hooks")
    _add_target_flags(uninstall)
    uninstall.add_argument("--dry-run", action="store_true", help="Print updated hooks JSON only")
    uninstall.add_argument(
        "--force",
        action="store_true",
        help="Allow repo-local uninstall outside git",
    )
    uninstall.set_defaults(func=_cmd_uninstall)

    hook = subparsers.add_parser("hook", help="Codex hook entry points")
    hook_sub = hook.add_subparsers(dest="hook_command", required=True)
    post = hook_sub.add_parser("post-tool-use")
    post.set_defaults(func=_cmd_hook_post_tool_use)
    pre = hook_sub.add_parser("pre-tool-use")
    pre.set_defaults(func=_cmd_hook_pre_tool_use)
    stop = hook_sub.add_parser("stop")
    stop.set_defaults(func=_cmd_hook_stop)

    report = subparsers.add_parser("report", help="Generate a coverage report")
    report.add_argument("--format", choices=["json", "lcov", "gcov"], default="lcov")
    report.add_argument("--counts", choices=["full", "binary"], default="full")
    report.add_argument("--out", type=Path, help="Output file for json/lcov")
    report.add_argument("--out-dir", type=Path, help="Output directory for gcov")
    report.set_defaults(func=_cmd_report)

    html = subparsers.add_parser("html", help="Generate a self-contained HTML report")
    html.add_argument("--out", type=Path, default=Path("aicov-html"), help="HTML file or directory")
    html.set_defaults(func=_cmd_html)

    summary = subparsers.add_parser("summary", help="Print read coverage summary")
    summary.set_defaults(func=_cmd_summary)

    unread = subparsers.add_parser("unread", help="Print unread files and ranges sorted by path")
    unread.add_argument("--limit", type=int, help="Maximum rows to print")
    unread.set_defaults(func=_cmd_unread)

    backfill = subparsers.add_parser("backfill", help="Backfill events from a Codex transcript")
    backfill.add_argument("--session-id", help="Codex session id to find under ~/.codex/sessions")
    backfill.add_argument("--path", type=Path, help="Transcript JSONL path")
    backfill.set_defaults(func=_cmd_backfill)

    importer = subparsers.add_parser("import", help="Import agent-coverage-style JSON")
    importer.add_argument("path", type=Path)
    importer.set_defaults(func=_cmd_import)

    return parser


def _add_target_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--user", action="store_true", help="Install into ~/.codex/hooks.json")
    group.add_argument("--repo", action="store_true", help="Install into .codex/hooks.json")


def _cmd_install(args: argparse.Namespace) -> int:
    target = _target(args)
    path, merged, changed = install_codex_hooks(
        target=target,
        cwd=_cwd(args),
        force=args.force,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print(json.dumps(merged, indent=2, sort_keys=True))
    else:
        state = "updated" if changed else "already installed"
        print(f"{path}: {state}")
        print("Codex will show hooks during trust review before they run.")
    return 0


def _cmd_uninstall(args: argparse.Namespace) -> int:
    target = _target(args)
    path, updated, changed = uninstall_codex_hooks(
        target=target,
        cwd=_cwd(args),
        force=args.force,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print(json.dumps(updated, indent=2, sort_keys=True))
    else:
        print(f"{path}: {'updated' if changed else 'no aicov hooks found'}")
    return 0


def _cmd_hook_post_tool_use(args: argparse.Namespace) -> int:
    _ = args
    payload = read_hook_payload()
    count = run_post_tool_use(payload)
    if count:
        print(f"recorded {count} aicov event(s)", file=sys.stderr)
    return 0


def _cmd_hook_pre_tool_use(args: argparse.Namespace) -> int:
    _ = args
    payload = read_hook_payload()
    output = run_pre_tool_use(payload)
    if output:
        print(output)
    return 0


def _cmd_hook_stop(args: argparse.Namespace) -> int:
    _ = args
    payload = read_hook_payload()
    path = run_stop(payload)
    print(f"wrote {path}", file=sys.stderr)
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    root, coverage = _coverage(args)
    if args.format == "json":
        out = args.out or root / ".aicov" / "coverage.json"
        path = write_coverage_json(coverage, root=root, path=_resolve_output(root, out))
    elif args.format == "lcov":
        out = args.out or Path("aicov.info")
        path = write_lcov(coverage, out=_resolve_output(root, out), counts=args.counts)
    else:
        out_dir = args.out_dir or Path("coverage-gcov")
        path = write_gcov(
            coverage,
            root=root,
            out_dir=_resolve_output(root, out_dir),
            counts=args.counts,
        )
    print(path)
    return 0


def _cmd_html(args: argparse.Namespace) -> int:
    root, coverage = _coverage(args)
    path = write_html(coverage, root=root, out=_resolve_output(root, args.out))
    print(path)
    return 0


def _cmd_summary(args: argparse.Namespace) -> int:
    _, coverage = _coverage(args)
    print(summary_text(coverage))
    return 0


def _cmd_unread(args: argparse.Namespace) -> int:
    _, coverage = _coverage(args)
    print(unread_text(coverage, limit=args.limit))
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    root, events = backfill_codex_session(session_id=args.session_id, transcript_path=args.path)
    count = append_events(events, root=root)
    print(f"imported {count} event(s)")
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    root, events = import_agent_coverage(args.path, cwd=_cwd(args))
    count = append_events(events, root=root)
    print(f"imported {count} event(s)")
    return 0


def _coverage(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    root = find_repo_root(args.root or Path.cwd())
    cfg = load_config(root)
    events = load_events(root=root, config=cfg)
    return root, build_coverage(root=root, events=events, config=cfg)


def _resolve_output(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _cwd(args: argparse.Namespace) -> Path:
    return Path(args.root).resolve() if args.root else Path.cwd().resolve()


def _target(args: argparse.Namespace) -> str:
    return "repo" if getattr(args, "repo", False) else "user"
