from __future__ import annotations

import json
from pathlib import Path

from aicov.backfill import backfill_claude_session, backfill_codex_session, backfill_session


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
                            "input": {
                                "file_path": str(tmp_path / "app.py"),
                                "offset": 2,
                                "limit": 2,
                            },
                        }
                    ]
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
    assert [(r.start, r.end) for r in events[0].ranges] == [(2, 3)]


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
                                    "name": "Read",
                                    "input": {"file_path": str(tmp_path / "app.py")},
                                }
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "parentUuid": "turn-1",
                        "toolUseResult": {
                            "type": "text",
                            "file": {
                                "filePath": str(tmp_path / "app.py"),
                                "content": "one\ntwo\n",
                                "numLines": 2,
                                "startLine": 1,
                                "totalLines": 5,
                            },
                        },
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_1",
                                    "content": "     1\u2192one\n     2\u2192two",
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

    _, events = backfill_claude_session(transcript_path=transcript)

    assert len(events) == 1
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(1, 2)]


def test_backfill_skips_failed_claude_read_result(tmp_path: Path) -> None:
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
                        "cwd": str(tmp_path),
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_1",
                                    "name": "Read",
                                    "input": {"file_path": str(tmp_path / "app.py")},
                                }
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "parentUuid": "turn-1",
                        "toolUseResult": "Error: File is too large to read",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_1",
                                    "is_error": True,
                                    "content": "<tool_use_error>File is too large</tool_use_error>",
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

    _, events = backfill_claude_session(transcript_path=transcript)

    assert events == []


def test_backfill_caps_claude_partial_read_from_numbered_tool_result(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
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
                                    "name": "Read",
                                    "input": {"file_path": str(tmp_path / "app.py")},
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
                                    "content": "     2\u2192two\n     3\u2192three",
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

    _, events = backfill_claude_session(transcript_path=transcript)

    assert len(events) == 1
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(2, 3)]


def test_backfill_caps_claude_partial_read_from_tab_numbered_result(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
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
                                    "name": "Read",
                                    "input": {"file_path": str(tmp_path / "app.py")},
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
                                    "content": "2\ttwo\n3\tthree",
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

    _, events = backfill_claude_session(transcript_path=transcript)

    assert len(events) == 1
    assert events[0].file == "app.py"
    assert [(r.start, r.end) for r in events[0].ranges] == [(2, 3)]
