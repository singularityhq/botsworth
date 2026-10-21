"""CONTRACT: Claude Code task notices — completion notifications queued to a thread (DM-41, D55).

GUARANTEES:
- `scan(transcript)` returns the transcript's notices by task id. A notice is read from a line the transcript
  owns (not `Line.copy`) that carries a `<task-id>` and a `<tool-use-id>`: a `queue-operation` `enqueue`
  record (`content`), a `user` record with `origin.kind = task-notification`, or an `attachment` of type
  `queued_command` with `origin.kind = task-notification` (`attachment.prompt`). The record `timestamp` is
  the queue time in all three, also for the attachment, which is written when the thread next runs. Per task
  id the earliest queue time wins, so an `enqueue` and the later `user` record of one task give one notice.
  `status` is the `<status>` text, else None.
- `bg_end(notices, tool_use_id)` = the earliest queue time of a notice whose `<tool-use-id>` is the call's
  id, else None (DM-41 `bg_end_ms`).
EXPECTS: transcripts in attribution order.
FAILURE BEHAVIOR: a record without a parsable `<task-id>`/`<tool-use-id>` or timestamp is not a notice.
DOES NOT: build events (events.py), decide stalls (store/timesplit.py), keep the notification text (D39).
KNOWN LIMITATIONS: Codex and omp record no completion notices. A notice for a task that was not started
  by a Bash call in this transcript (a subagent task, a monitor) is still a notice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from botsworth.adapters.claude.attribution import Transcript
from botsworth.adapters.claude.records import Line, message, ts_ms

_TASK_ID = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")
_TOOL_USE_ID = re.compile(r"<tool-use-id>\s*([^<\s]+)\s*</tool-use-id>")
_STATUS = re.compile(r"<status>\s*([^<]*?)\s*</status>")
_NOTIFICATION = "task-notification"


@dataclass(slots=True)
class Notice:
    task_id: str
    tool_use_id: str
    status: str | None
    ts_ms: int
    line: Line


def _text_of(rec: dict[str, Any]) -> str | None:
    kind = rec.get("type")
    if kind == "queue-operation":
        content = rec.get("content")
        return content if rec.get("operation") == "enqueue" and isinstance(content, str) else None
    if kind == "user" and (rec.get("origin") or {}).get("kind") == _NOTIFICATION:
        content = message(rec).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(b.get("text") or "" for b in content if isinstance(b, dict))
        return None
    if is_notification_attachment(rec):
        prompt = rec["attachment"].get("prompt")
        return prompt if isinstance(prompt, str) else None
    return None


def is_notification_attachment(rec: dict[str, Any]) -> bool:
    att = rec.get("attachment")
    return (
        rec.get("type") == "attachment"
        and isinstance(att, dict)
        and att.get("type") == "queued_command"
        and (att.get("origin") or {}).get("kind") == _NOTIFICATION
    )


def scan(t: Transcript) -> dict[str, Notice]:
    out: dict[str, Notice] = {}
    for line in t.lines:
        if line.copy:
            continue
        text = _text_of(line.rec)
        ts = ts_ms(line.rec.get("timestamp"))
        if text is None or ts is None:
            continue
        task, tool = _TASK_ID.search(text), _TOOL_USE_ID.search(text)
        if not task or not tool:
            continue
        status = _STATUS.search(text)
        cur = out.get(task[1])
        if cur is None or ts < cur.ts_ms:
            out[task[1]] = Notice(task[1], tool[1], status[1] if status else None, ts, line)
    return out


def bg_end(notices: dict[str, Notice], tool_use_id: str) -> int | None:
    times = [n.ts_ms for n in notices.values() if n.tool_use_id == tool_use_id]
    return min(times) if times else None
