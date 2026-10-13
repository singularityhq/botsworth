"""CONTRACT: one omp transcript file -> parsed lines and the per-file facts the adapter needs.

Inlined from spikes/extract-omp/extract.py (gated B3 rules) and the account
table of spikes/adapter-omp/adapter.py.

GUARANTEES:
- `read_file(path)` returns every complete line's bytes (1-based by index + 1)
  and `(line_no, record)` pairs in file order; a trailing line without a
  newline is skipped and counted `n_partial`; an unparsable complete line is
  skipped and counted `n_malformed` (live files are appended while we read).
- `FileFacts` holds, per file: the first session header, title, first and last
  record timestamps (min/max) and the first in file order, every `toolCall` keyed by `toolCallId` with its
  `tool_execution_start` time and paired `toolResult` (paired by id only),
  async jobs matched to the launching call (`details.async.jobId`, task
  `progress[].id`), task child ids from `results[].id`/`progress[].id`, and the
  count of record types it does not model (`unhandled`).
- Tool timing: start = `tool_execution_start.data.startedAt`, end = the
  toolResult *record* timestamp (the message timestamp runs up to 0.8 s before
  startedAt; gated clock fix). No start record -> start = the issuing
  assistant's `completedAt`, `derived`. No result -> `missing`.
- `account(provider, credential, ts)` follows the table below; ChatGPT keys use
  Codex's plan-level format (`chatgpt-<plan_type>`, fallback `chatgpt-default`)
  so omp and Codex usage on one plan share one account key.

Account mapping evidence (spikes/adapter-omp/account_evidence.py, read-only over
~/.omp/agent/agent.db non-secret columns and Codex rollout `rate_limits`):
omp `usage_history` windows matched to Codex `rate_limits` by resets_at minute
and used_percent. Credential -> identity: openai-codex #2 -> a62ffd0b (740
rows), openai-codex #5 -> 529daac4 (558), openai-codex-2 #6 -> 529daac4 (558).
Codex plan for a62ffd0b: plus (263 + 777 + 49 + 12 + 8 matched windows, last
seen 2026-10-12). 529daac4: plus until its last 5 h/7 d window on
2026-10-01T12:11:10Z, then a `free` 30-day window (1 match). Unpinned
openai-codex calls: both identities were plus before that instant.
Not verified: that these identities are the same accounts as the Codex CLI's
auth; the mapping is plan-level only.

EXPECTS: an omp session JSONL (one `session` header per file, possibly repeated).
FAILURE BEHAVIOR: a file without a session header -> ValueError.
DOES NOT: build ops, steps or events; read credentials.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

HANDLED = frozenset({"session", "title", "message", "model_usage", "custom", "custom_message", "compaction"})

# (provider, credentialId) -> plus-until epoch ms (None = no observed end); see CONTRACT.
PLUS_UNTIL_529 = 1790856670000  # 2026-10-01T12:11:10Z, last plus window of identity 529daac4
CHATGPT_IDENTITY: dict[tuple[str, int | None], int | None] = {
    ("openai-codex", 2): None,
    ("openai-codex", 5): PLUS_UNTIL_529,
    ("openai-codex-2", None): PLUS_UNTIL_529,  # credential row 6 = identity 529daac4
}
DEFAULT_CHATGPT = "chatgpt-default"  # same fallback key as the Codex adapter


def account(provider: str | None, credential: int | None = None, ts: int | None = None) -> str:
    if provider and provider.startswith("openai-codex"):
        key = (provider, credential)
        if key in CHATGPT_IDENTITY:
            until = CHATGPT_IDENTITY[key]
            plus = until is None or (ts is not None and ts <= until)
            return "chatgpt-plus" if plus else DEFAULT_CHATGPT
        if key == ("openai-codex", None) and ts is not None and ts <= PLUS_UNTIL_529:
            return "chatgpt-plus"
        return DEFAULT_CHATGPT
    return f"{provider or 'unknown'}:{credential if credential is not None else 'unknown'}"


def iso_ms(s: Any) -> int | None:
    if not isinstance(s, str) or not s:
        return None
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def num(v: Any) -> int | float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def as_int(v: Any) -> int | None:
    n = num(v)
    return None if n is None else round(n)


@dataclass(slots=True)
class FileLines:
    lines: list[bytes]
    records: list[tuple[int, dict]]
    n_malformed: int = 0
    n_partial: int = 0


def read_file(path: Path) -> FileLines:
    lines: list[bytes] = []
    records: list[tuple[int, dict]] = []
    out = FileLines(lines, records)
    with path.open("rb") as f:
        for raw in f:
            if not raw.endswith(b"\n"):
                out.n_partial += 1
                continue
            lines.append(raw)
            if not raw.strip():
                continue
            try:
                r = json.loads(raw)
            except json.JSONDecodeError:
                out.n_malformed += 1
                continue
            if isinstance(r, dict):
                records.append((len(lines), r))
            else:
                out.n_malformed += 1
    return out


@dataclass(slots=True)
class ToolFacts:
    call_id: str
    name: str | None
    args: dict
    line: int  # the assistant record carrying the toolCall
    issued_ms: int | None
    start_ms: int | None = None
    end_ms: int | None = None
    result: dict | None = None
    result_line: int | None = None
    jobs: list[int | None] = field(default_factory=list)  # matched async job durations


@dataclass(slots=True)
class FileFacts:
    header: dict
    header_line: int
    title: str | None
    first_ms: int | None
    last_ms: int | None
    first_line_ms: int | None  # D23: the first timestamp in file order
    tools: dict[str, ToolFacts]
    task_children: dict[str, str]  # child id (file stem) -> task toolCallId
    unhandled: Counter


def parse(fl: FileLines, path: Path) -> FileFacts:
    header: dict | None = None
    header_line = 0
    title = None
    first = last = first_line = None
    tools: dict[str, ToolFacts] = {}
    starts: dict[str, int | None] = {}
    task_children: dict[str, str] = {}
    job_tool: dict[str, str] = {}
    unhandled: Counter = Counter()
    for ln, r in fl.records:
        t = r.get("type")
        ts = iso_ms(r.get("timestamp"))
        if ts is not None:
            first = ts if first is None else min(first, ts)
            first_line = ts if first_line is None else first_line
            last = ts if last is None else max(last, ts)
        if t == "session":
            if header is None:
                header, header_line = r, ln
        elif t == "title":
            title = r.get("title") or title
        elif t == "message":
            m = r.get("message") or {}  # FALLBACK-OK: extract-omp gated rules, message-less record
            if m.get("role") == "assistant":
                issued = as_int(m.get("completedAt")) or ts
                for c in m.get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "toolCall" and isinstance(c.get("id"), str):
                        args = c.get("arguments") if isinstance(c.get("arguments"), dict) else {}
                        tools[c["id"]] = ToolFacts(c["id"], c.get("name"), args, ln, issued)
            elif m.get("role") == "toolResult":
                _result(m, ln, ts, tools, starts, task_children, job_tool, unhandled)
        elif t == "custom" and r.get("customType") == "tool_execution_start":
            d = r.get("data") or {}  # FALLBACK-OK: extract-omp gated rules
            starts[d.get("toolCallId")] = iso_ms(d.get("startedAt")) or ts
        elif t == "custom_message" and r.get("customType") == "async-result":
            for job in (r.get("details") or {}).get("jobs") or []:  # FALLBACK-OK: extract-omp
                tc = tools.get(job_tool.get(job.get("jobId"), ""))
                if tc is None:
                    unhandled["async_result_unmatched"] += 1
                else:
                    tc.jobs.append(as_int(job.get("durationMs")))
        elif t not in HANDLED:
            unhandled[str(t)] += 1
    if header is None:
        raise ValueError(f"no session header: {path}")
    return FileFacts(header, header_line, title, first, last, first_line, tools, task_children, unhandled)


def _result(m, ln, ts, tools, starts, task_children, job_tool, unhandled) -> None:
    cid = m.get("toolCallId")
    tc = tools.get(cid)
    if tc is None:
        unhandled["toolResult_without_call"] += 1
        return
    tc.start_ms = starts.pop(cid, None)
    tc.end_ms, tc.result, tc.result_line = ts, m, ln
    det = m.get("details") if isinstance(m.get("details"), dict) else {}
    if tc.name == "task":
        for key in ("results", "progress"):
            for e in det.get(key) or []:
                if isinstance(e, dict) and isinstance(e.get("id"), str):
                    task_children.setdefault(e["id"], cid)
                    if key == "progress":
                        job_tool[e["id"]] = cid
    a = det.get("async")
    if isinstance(a, dict) and a.get("jobId"):
        job_tool[a["jobId"]] = cid


def tool_timing(tc: ToolFacts) -> tuple[int | None, int | None, str]:
    if tc.start_ms is not None and tc.end_ms is not None:
        return tc.start_ms, tc.end_ms, "present"
    if tc.end_ms is not None and tc.issued_ms is not None:
        return tc.issued_ms, tc.end_ms, "derived"
    return tc.issued_ms, None, "missing"


def result_details(tc: ToolFacts) -> dict:
    det = (tc.result or {}).get("details")
    return det if isinstance(det, dict) else {}


def exit_code(tc: ToolFacts) -> int | None:
    det = result_details(tc)
    code = det.get("exitCode")
    if code is None and isinstance(det.get("cells"), list):
        cells = [c for c in det["cells"] if isinstance(c, dict)]
        codes = [c["exitCode"] for c in cells if c.get("exitCode") is not None]
        code = max(codes, key=abs) if codes else None
    return as_int(code)


def is_async(tc: ToolFacts) -> bool:
    det = result_details(tc)
    if isinstance(det.get("async"), dict):
        return True
    return tc.name == "task" and det.get("results") == [] and bool(det.get("progress"))


def header_ms(path: Path) -> int | None:
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("type") == "session":
                return iso_ms(r.get("timestamp"))
    return None
