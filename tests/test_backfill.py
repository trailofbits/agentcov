from __future__ import annotations

import json
from pathlib import Path

from aicov.backfill import backfill_codex_session


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
