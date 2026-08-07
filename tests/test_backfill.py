from __future__ import annotations

import json
from pathlib import Path

from agentcov.backfill import (
    backfill_claude_session,
    backfill_codex_session,
    backfill_pi_session,
    backfill_session,
)


def _write_jsonl(path: Path, *records: dict[str, object]) -> None:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


_UNSET = object()


def _claude_tool_use_record(
    tmp_path: Path,
    *,
    tool_name: str,
    tool_input: dict[str, object],
    tool_id: str = "toolu_1",
    turn_id: str = "turn-1",
    session_id: str = "sess",
    **extra: object,
) -> dict[str, object]:
    record: dict[str, object] = {
        "type": "assistant",
        "uuid": turn_id,
        "sessionId": session_id,
        "cwd": str(tmp_path),
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": tool_name,
                    "input": tool_input,
                }
            ]
        },
    }
    record.update(extra)
    return record


def _claude_tool_result_record(
    *,
    content: object,
    tool_id: str = "toolu_1",
    parent_uuid: str = "turn-1",
    tool_use_result: object = _UNSET,
    is_error: bool = False,
) -> dict[str, object]:
    block: dict[str, object] = {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "content": content,
    }
    if is_error:
        block["is_error"] = True
    record: dict[str, object] = {
        "type": "user",
        "parentUuid": parent_uuid,
        "message": {"content": [block]},
    }
    if tool_use_result is not _UNSET:
        record["toolUseResult"] = tool_use_result
    return record


def _pi_session_header(tmp_path: Path, *, session_id: str = "pi-sess") -> dict[str, object]:
    return {
        "type": "session",
        "version": 3,
        "id": session_id,
        "timestamp": "2026-01-01T00:00:00Z",
        "cwd": str(tmp_path),
    }


def _pi_user_record(text: str = "inspect files") -> dict[str, object]:
    return {
        "type": "message",
        "id": "user-1",
        "parentId": None,
        "timestamp": "2026-01-01T00:00:01Z",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    }


def _pi_tool_call_record(
    *,
    tool_name: str,
    arguments: dict[str, object],
    tool_id: str = "tool-1",
    entry_id: str = "assistant-1",
) -> dict[str, object]:
    return {
        "type": "message",
        "id": entry_id,
        "parentId": "user-1",
        "timestamp": "2026-01-01T00:00:02Z",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "toolCall",
                    "id": tool_id,
                    "name": tool_name,
                    "arguments": arguments,
                }
            ],
        },
    }


def _pi_tool_result_record(
    *,
    tool_name: str,
    content: str,
    tool_id: str = "tool-1",
    entry_id: str = "result-1",
    is_error: bool = False,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    message: dict[str, object] = {
        "role": "toolResult",
        "toolCallId": tool_id,
        "toolName": tool_name,
        "content": [{"type": "text", "text": content}],
        "isError": is_error,
    }
    if details is not None:
        message["details"] = details
    return {
        "type": "message",
        "id": entry_id,
        "parentId": "assistant-1",
        "timestamp": "2026-01-01T00:00:03Z",
        "message": message,
    }


def test_backfill_reads_codex_response_item_payload_function_call(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "functions.exec_command",
                    "call_id": "call-1",
                    "arguments": json.dumps(
                        {
                            "cmd": "sed -n '1,2p' app.py",
                            "workdir": str(tmp_path),
                        }
                    ),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    root, events = backfill_codex_session(transcript_path=transcript)

    assert root == tmp_path
    assert len(events) == 1
    assert events[0].tool_name == "functions.exec_command"
    assert events[0].tool_use_id == "call-1"
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(1, 2)]


def test_backfill_reads_pi_bash_tool_use(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    transcript = tmp_path / "pi.jsonl"
    _write_jsonl(
        transcript,
        _pi_session_header(tmp_path),
        _pi_user_record("inspect the app"),
        _pi_tool_call_record(
            tool_name="bash",
            arguments={"command": "sed -n '2,3p' app.py"},
        ),
        _pi_tool_result_record(tool_name="bash", content="two\nthree\n"),
    )

    root, events = backfill_pi_session(transcript_path=transcript)

    assert root == tmp_path
    assert len(events) == 1
    assert events[0].agent == "pi"
    assert events[0].tool_name == "bash"
    assert events[0].session_id == "pi-sess"
    assert events[0].tool_use_id == "tool-1"
    assert events[0].task_path == ["inspect the app"]
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(2, 3)]


def test_backfill_reads_pi_direct_read_with_one_indexed_offset(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    transcript = tmp_path / "pi.jsonl"
    _write_jsonl(
        transcript,
        _pi_session_header(tmp_path),
        _pi_user_record(),
        _pi_tool_call_record(
            tool_name="read",
            arguments={"path": "app.py", "offset": 2, "limit": 2},
        ),
        _pi_tool_result_record(tool_name="read", content="two\nthree\n"),
    )

    root, events = backfill_pi_session(transcript_path=transcript)

    assert root == tmp_path
    assert len(events) == 1
    assert events[0].agent == "pi"
    assert events[0].tool_name == "read"
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(2, 3)]


def test_backfill_caps_pi_unbounded_read_from_continuation_notice(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    transcript = tmp_path / "pi.jsonl"
    _write_jsonl(
        transcript,
        _pi_session_header(tmp_path),
        _pi_user_record(),
        _pi_tool_call_record(tool_name="read", arguments={"path": "app.py"}),
        _pi_tool_result_record(
            tool_name="read",
            content="one\ntwo\n\n[Showing lines 1-2 of 4. Use offset=3 to continue.]",
        ),
    )

    _, events = backfill_pi_session(transcript_path=transcript)

    assert len(events) == 1
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(1, 2)]


def test_backfill_skips_failed_pi_read_result(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    transcript = tmp_path / "pi.jsonl"
    _write_jsonl(
        transcript,
        _pi_session_header(tmp_path),
        _pi_user_record(),
        _pi_tool_call_record(tool_name="read", arguments={"path": "app.py"}),
        _pi_tool_result_record(
            tool_name="read",
            content="Error: file too large",
            is_error=True,
        ),
    )

    _, events = backfill_pi_session(transcript_path=transcript)

    assert events == []


def test_backfill_skips_pi_image_read_result(tmp_path: Path) -> None:
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    transcript = tmp_path / "pi.jsonl"
    _write_jsonl(
        transcript,
        _pi_session_header(tmp_path),
        _pi_user_record(),
        _pi_tool_call_record(tool_name="read", arguments={"path": "image.png"}),
        _pi_tool_result_record(tool_name="read", content="Read image file [image/png]"),
    )

    _, events = backfill_pi_session(transcript_path=transcript)

    assert events == []


def test_backfill_records_pi_grep_hits_and_context(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("one\nneedle\nthree\n", encoding="utf-8")
    transcript = tmp_path / "pi.jsonl"
    _write_jsonl(
        transcript,
        _pi_session_header(tmp_path),
        _pi_user_record(),
        _pi_tool_call_record(
            tool_name="grep",
            arguments={"pattern": "needle", "path": "src", "context": 1},
        ),
        _pi_tool_result_record(
            tool_name="grep",
            content="app.py-1- one\napp.py:2: needle\napp.py-3- three",
        ),
    )

    root, events = backfill_pi_session(transcript_path=transcript)

    assert root == tmp_path
    assert len(events) == 1
    assert events[0].agent == "pi"
    assert events[0].tool_name == "grep"
    assert events[0].kind == "search_seen"
    assert events[0].file == "src/app.py"
    assert [(r.start, r.end, r.weight) for r in events[0].ranges] == [
        (1, 1, 0.2),
        (2, 2, 0.35),
        (3, 3, 0.2),
    ]


def test_backfill_reads_pi_bash_execution_messages(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    transcript = tmp_path / "pi.jsonl"
    _write_jsonl(
        transcript,
        _pi_session_header(tmp_path),
        _pi_user_record("run a command"),
        {
            "type": "message",
            "id": "bash-exec-1",
            "parentId": "user-1",
            "timestamp": "2026-01-01T00:00:02Z",
            "message": {
                "role": "bashExecution",
                "command": "sed -n '1,1p' app.py",
                "output": "one\n",
                "exitCode": 0,
                "cancelled": False,
            },
        },
    )

    _, events = backfill_pi_session(transcript_path=transcript)

    assert len(events) == 1
    assert events[0].agent == "pi"
    assert events[0].tool_name == "bashExecution"
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(1, 1)]


def test_backfill_pi_path_includes_child_sessions(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("one\n", encoding="utf-8")
    (tmp_path / "child.py").write_text("one\ntwo\n", encoding="utf-8")
    parent = tmp_path / "parent.jsonl"
    child = tmp_path / "child.jsonl"
    _write_jsonl(
        parent,
        _pi_session_header(tmp_path, session_id="parent-session"),
        _pi_user_record("inspect main"),
        _pi_tool_call_record(tool_name="read", arguments={"path": "main.py", "limit": 1}),
        _pi_tool_result_record(tool_name="read", content="one\n"),
    )
    child_header = _pi_session_header(tmp_path, session_id="child-session")
    child_header["parentSession"] = str(parent)
    _write_jsonl(
        child,
        child_header,
        _pi_user_record("inspect child"),
        _pi_tool_call_record(
            tool_name="read",
            arguments={"path": "child.py", "offset": 2, "limit": 1},
        ),
        _pi_tool_result_record(tool_name="read", content="two\n"),
    )

    _, events = backfill_pi_session(transcript_path=parent)

    by_file = {event.file: event for event in events}
    assert sorted(by_file) == ["child.py", "main.py"]
    assert [(r.start, r.end) for r in by_file["main.py"].ranges] == [(1, 1)]
    assert [(r.start, r.end) for r in by_file["child.py"].ranges] == [(2, 2)]
    assert by_file["child.py"].task_path == ["inspect child", "pi child session child-session"]


def test_backfill_auto_detects_copied_pi_transcript(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\n", encoding="utf-8")
    transcript = tmp_path / "copied-pi.jsonl"
    _write_jsonl(
        transcript,
        _pi_session_header(tmp_path),
        _pi_user_record(),
        _pi_tool_call_record(tool_name="read", arguments={"path": "app.py", "limit": 1}),
        _pi_tool_result_record(tool_name="read", content="one\n"),
    )

    _, events = backfill_session(agent="auto", transcript_path=transcript)

    assert len(events) == 1
    assert events[0].agent == "pi"
    assert events[0].file == "app.py"


def test_backfill_auto_detects_pi_session_id(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("one\n", encoding="utf-8")
    session_dir = tmp_path / ".pi" / "agent" / "sessions" / "--tmp-repo--"
    session_dir.mkdir(parents=True)
    transcript = session_dir / "2026-01-01T00-00-00-000Z_pi-sess-123.jsonl"
    _write_jsonl(
        transcript,
        _pi_session_header(repo, session_id="pi-sess-123"),
        _pi_user_record(),
        _pi_tool_call_record(tool_name="read", arguments={"path": "app.py", "limit": 1}),
        _pi_tool_result_record(tool_name="read", content="one\n"),
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    root, events = backfill_session(agent="auto", session_id="pi-sess-123")

    assert root == repo
    assert len(events) == 1
    assert events[0].agent == "pi"
    assert events[0].file == "app.py"


def test_backfill_reads_claude_bash_tool_use(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "uuid": "turn-1",
                        "sessionId": "sess",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "cwd": str(tmp_path),
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_1",
                                    "name": "Bash",
                                    "input": {"command": "sed -n '2,3p' app.py"},
                                }
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "parentUuid": "turn-1",
                        "toolUseResult": {"stdout": "two\nthree\n", "stderr": ""},
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_1",
                                    "content": "two\nthree\n",
                                }
                            ]
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    root, events = backfill_claude_session(transcript_path=transcript)

    assert root == tmp_path
    assert len(events) == 1
    assert events[0].agent == "claude"
    assert events[0].tool_name == "Bash"
    assert events[0].session_id == "sess"
    assert events[0].tool_use_id == "toolu_1"
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(2, 3)]


def test_backfill_reads_claude_read_tool_limit_offset(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    _write_jsonl(
        transcript,
        _claude_tool_use_record(
            tmp_path,
            tool_name="Read",
            tool_input={
                "file_path": str(tmp_path / "app.py"),
                "offset": 2,
                "limit": 2,
            },
        ),
    )

    root, events = backfill_claude_session(transcript_path=transcript)

    assert root == tmp_path
    assert len(events) == 1
    assert events[0].agent == "claude"
    assert events[0].tool_name == "Read"
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(2, 3)]


def test_backfill_skips_unpaired_unbounded_claude_read(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    for tool_name in ("Read", "NotebookRead"):
        _write_jsonl(
            transcript,
            _claude_tool_use_record(
                tmp_path,
                tool_name=tool_name,
                tool_input={"file_path": str(tmp_path / "app.py")},
            ),
        )

        _, events = backfill_claude_session(transcript_path=transcript)

        assert events == []


def test_backfill_reads_claude_progress_record(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "progress",
                "cwd": str(tmp_path),
                "sessionId": "sess",
                "data": {
                    "message": {
                        "type": "assistant",
                        "uuid": "turn-1",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_1",
                                    "name": "Read",
                                    "input": {"file_path": str(tmp_path / "app.py"), "limit": 1},
                                }
                            ]
                        },
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    root, events = backfill_claude_session(transcript_path=transcript)

    assert root == tmp_path
    assert len(events) == 1
    assert events[0].agent == "claude"
    assert events[0].tool_name == "Read"
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(1, 1)]


def test_backfill_records_claude_grep_search_hits(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\nneedle\nthree\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "uuid": "turn-1",
                        "sessionId": "sess",
                        "cwd": str(tmp_path),
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_1",
                                    "name": "Grep",
                                    "input": {
                                        "pattern": "needle",
                                        "path": str(tmp_path),
                                        "output_mode": "content",
                                    },
                                }
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "parentUuid": "turn-1",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_1",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": f"{tmp_path / 'app.py'}:2:needle",
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    root, events = backfill_claude_session(transcript_path=transcript)

    assert root == tmp_path
    assert len(events) == 1
    assert events[0].agent == "claude"
    assert events[0].tool_name == "Grep"
    assert events[0].kind == "search_seen"
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(2, 2)]


def test_backfill_records_claude_single_file_grep_hits(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\nneedle\nthree\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "uuid": "turn-1",
                        "sessionId": "sess",
                        "cwd": str(tmp_path),
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_1",
                                    "name": "Grep",
                                    "input": {
                                        "pattern": "needle",
                                        "path": str(tmp_path / "app.py"),
                                        "output_mode": "content",
                                    },
                                }
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "parentUuid": "turn-1",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_1",
                                    "content": "2:needle",
                                }
                            ]
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    root, events = backfill_claude_session(transcript_path=transcript)

    assert root == tmp_path
    assert len(events) == 1
    assert events[0].agent == "claude"
    assert events[0].tool_name == "Grep"
    assert events[0].kind == "search_seen"
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(2, 2)]


def test_backfill_ignores_claude_write_tool(tmp_path: Path) -> None:
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "uuid": "turn-1",
                "sessionId": "sess",
                "cwd": str(tmp_path),
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "Write",
                            "input": {
                                "file_path": str(tmp_path / "app.py"),
                                "content": "print('hello')\n",
                            },
                        }
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    _, events = backfill_claude_session(transcript_path=transcript)

    assert events == []


def test_backfill_dedupes_claude_progress_and_subagent_duplicates(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\n", encoding="utf-8")
    assistant_record = {
        "type": "assistant",
        "uuid": "turn-1",
        "sessionId": "sess",
        "cwd": str(tmp_path),
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Read",
                    "input": {"file_path": str(tmp_path / "app.py"), "limit": 1},
                }
            ]
        },
    }
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(assistant_record),
                json.dumps(
                    {
                        "type": "progress",
                        "cwd": str(tmp_path),
                        "sessionId": "sess",
                        "data": {"message": assistant_record},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    _, events = backfill_claude_session(transcript_path=transcript)

    assert len(events) == 1
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(1, 1)]


def test_backfill_preserves_claude_progress_task_metadata(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\n", encoding="utf-8")
    session = tmp_path / "session.jsonl"
    subagents = tmp_path / "session" / "subagents"
    subagents.mkdir(parents=True)
    assistant_record = {
        "type": "assistant",
        "uuid": "turn-1",
        "sessionId": "sess",
        "cwd": str(tmp_path),
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Read",
                    "input": {"file_path": str(tmp_path / "app.py"), "limit": 1},
                }
            ]
        },
    }
    session.write_text(
        json.dumps(
            {
                "type": "progress",
                "cwd": str(tmp_path),
                "sessionId": "sess",
                "data": {
                    "agentId": "agent-a",
                    "prompt": "inspect generated code",
                    "message": assistant_record,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    subagent_record = dict(assistant_record)
    subagent_record["agentId"] = "agent-a"
    subagent_record["prompt"] = "inspect generated code"
    (subagents / "agent-a.jsonl").write_text(
        json.dumps(subagent_record) + "\n",
        encoding="utf-8",
    )

    _, events = backfill_claude_session(transcript_path=session)

    assert len(events) == 1
    assert events[0].file == "app.py"
    assert events[0].task_path == ["inspect generated code", "agent-a"]


def test_backfill_claude_path_includes_adjacent_subagents(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("one\n", encoding="utf-8")
    (tmp_path / "sub.py").write_text("one\ntwo\n", encoding="utf-8")
    session = tmp_path / "session.jsonl"
    subagents = tmp_path / "session" / "subagents"
    subagents.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                "type": "assistant",
                "uuid": "turn-main",
                "sessionId": "sess",
                "cwd": str(tmp_path),
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_main",
                            "name": "Read",
                            "input": {"file_path": str(tmp_path / "main.py"), "limit": 1},
                        }
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (subagents / "agent-a.jsonl").write_text(
        json.dumps(
            {
                "type": "assistant",
                "uuid": "turn-sub",
                "sessionId": "sess",
                "agentId": "agent-a",
                "cwd": str(tmp_path),
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_sub",
                            "name": "Read",
                            "input": {
                                "file_path": str(tmp_path / "sub.py"),
                                "offset": 2,
                                "limit": 1,
                            },
                        }
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    _, events = backfill_claude_session(transcript_path=session)

    by_file = {event.file: event for event in events}
    assert sorted(by_file) == ["main.py", "sub.py"]
    assert [(r.start, r.end) for r in by_file["main.py"].ranges] == [(1, 1)]
    assert [(r.start, r.end) for r in by_file["sub.py"].ranges] == [(2, 2)]


def test_backfill_auto_detects_copied_claude_transcript(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\n", encoding="utf-8")
    transcript = tmp_path / "copied-session.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "uuid": "turn-1",
                "sessionId": "sess",
                "cwd": str(tmp_path),
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "Read",
                            "input": {"file_path": str(tmp_path / "app.py"), "limit": 1},
                        }
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    _, events = backfill_session(agent="auto", transcript_path=transcript)

    assert len(events) == 1
    assert events[0].agent == "claude"
    assert events[0].file == "app.py"


def test_backfill_auto_detects_claude_session_id(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    repo = tmp_path / "repo"
    (repo / "app.py").write_text("one\n", encoding="utf-8")
    session_dir = tmp_path / ".claude" / "projects" / "-tmp-repo"
    session_dir.mkdir(parents=True)
    (session_dir / "sess-123.jsonl").write_text(
        json.dumps(
            {
                "type": "assistant",
                "uuid": "turn-1",
                "sessionId": "sess-123",
                "cwd": str(repo),
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "Read",
                            "input": {"file_path": str(repo / "app.py"), "limit": 1},
                        }
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    root, events = backfill_session(agent="auto", session_id="sess-123")

    assert root == repo
    assert len(events) == 1
    assert events[0].agent == "claude"
    assert events[0].file == "app.py"


def test_backfill_caps_claude_partial_read_from_tool_result(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    _write_jsonl(
        transcript,
        _claude_tool_use_record(
            tmp_path,
            tool_name="Read",
            tool_input={"file_path": str(tmp_path / "app.py")},
        ),
        _claude_tool_result_record(
            content="     1\u2192one\n     2\u2192two",
            tool_use_result={
                "type": "text",
                "file": {
                    "filePath": str(tmp_path / "app.py"),
                    "content": "one\ntwo\n",
                    "numLines": 2,
                    "startLine": 1,
                    "totalLines": 5,
                },
            },
        ),
    )

    _, events = backfill_claude_session(transcript_path=transcript)

    assert len(events) == 1
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(1, 2)]


def test_backfill_skips_failed_claude_read_result(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    _write_jsonl(
        transcript,
        _claude_tool_use_record(
            tmp_path,
            tool_name="Read",
            tool_input={"file_path": str(tmp_path / "app.py")},
        ),
        _claude_tool_result_record(
            content="<tool_use_error>File is too large</tool_use_error>",
            tool_use_result="Error: File is too large to read",
            is_error=True,
        ),
    )

    _, events = backfill_claude_session(transcript_path=transcript)

    assert events == []


def test_backfill_caps_claude_partial_read_from_numbered_tool_result(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    _write_jsonl(
        transcript,
        _claude_tool_use_record(
            tmp_path,
            tool_name="Read",
            tool_input={"file_path": str(tmp_path / "app.py")},
        ),
        _claude_tool_result_record(content="     2\u2192two\n     3\u2192three"),
    )

    _, events = backfill_claude_session(transcript_path=transcript)

    assert len(events) == 1
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(2, 3)]


def test_backfill_caps_claude_partial_read_from_tab_numbered_result(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    _write_jsonl(
        transcript,
        _claude_tool_use_record(
            tmp_path,
            tool_name="Read",
            tool_input={"file_path": str(tmp_path / "app.py")},
        ),
        _claude_tool_result_record(content="2\ttwo\n3\tthree"),
    )

    _, events = backfill_claude_session(transcript_path=transcript)

    assert len(events) == 1
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(2, 3)]


def test_backfill_uses_successful_bounded_claude_read_input_when_result_is_plain(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    transcript = tmp_path / "claude.jsonl"
    _write_jsonl(
        transcript,
        _claude_tool_use_record(
            tmp_path,
            tool_name="Read",
            tool_input={
                "file_path": str(tmp_path / "app.py"),
                "offset": 2,
                "limit": 2,
            },
        ),
        _claude_tool_result_record(content="two\nthree"),
    )

    _, events = backfill_claude_session(transcript_path=transcript)

    assert len(events) == 1
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(2, 3)]
