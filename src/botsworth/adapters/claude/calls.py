"""CONTRACT: Claude Code leaves — llm_call per requestId, tool_call per tool_use id (design §4.4, §4.6, §4.7).

GUARANTEES:
- One `llm_call` per (transcript, requestId) whose group has a non-synthetic
  record; usage from the ONE final record (`records.select_final`), never
  summed. `occ_key = claude_code:request:<requestId>`; parent = the turn of
  the group's first line.
- One `tool_call` per (transcript, tool_use id) whose emitting call exists;
  parent = that `llm_call`; the result is paired by `tool_use_id` within the
  same transcript only. `occ_key = claude_code:tool_use:<id>`.
- Within the bundle a call or tool use is owned by the first transcript in
  attribution order that holds it in a non-copy line; any other transcript's
  op is a copy: key `<id>@copy:<owner>` and `duplicate_of` = the owner's op id
  (a hint; the store decides ownership across bundles by `occ_key`).
- Timing (design §4.6): llm_call start = timestamp of the nearest earlier
  non-assistant line of the transcript (else the group's first line), end =
  the final record, `time_prov` derived. tool_call start = the tool_use line,
  end = the result line; `dur_ms` = `toolUseResult` durationMs /
  totalDurationMs / durationSeconds (`present`) else end - start (`derived`);
  an async launch has `dur_ms` 0 (or the reported value) and `is_async`.
- Cost via `Pricer` (computed | missing, never 0 for an unknown model);
  `price_fingerprint` on every llm_call; tokens absent -> `tok_prov` missing.
- Bash calls carry `command` (<= 2 KB; full text via `src`), D17
  `command_norm` (project-relative) and `command_key`. `exit_code` from
  `toolUseResult.exitCode` (`present`) else parsed from the result text
  (`derived`).
- Every leaf carries `agent_id`, `agent_type`, `spawn_depth`, `is_fork`,
  `occ_key`, `src` (lines first..last; `h` on single-line pointers).
- D55 on Bash calls: `command_class` (DM-28) from the full command; for `test`, `tests_passed` and
  `tests_failed` (DM-29) from `toolUseResult.stdout` and `stderr`, else the tool_result text, never stored;
  An llm_call whose nearest earlier non-assistant line is a task-notification attachment starts at the
  group's first line (the attachment's timestamp is the queue time, not the time the thread reached it).
  `bg_end_ms` (DM-41) when the result has a `backgroundTaskId`: the earliest queue time of a notice of
  the same transcript for this call (`notices.py`), else None; also None when that time is before the
  call's start.
- `user_wait` (DM-40, D54): `ask` on `AskUserQuestion` calls; else `permission` when the result record's
  `permissionDecision.source` starts with `user`, or the result text starts with "The user doesn't want to
  proceed"; else None (a `config` source, or an unseen source value, leaves it None). Only the category is
  stored, never the result text (D39).
- `sends` lists every Agent/Task/SendMessage call with the hash of its
  message body and the result's `agentId`, for engagement linking.
EXPECTS: `turn_of` covers every line (tree.build).
FAILURE BEHAVIOR: tool uses without an emitting call are counted
  (`tool_without_llm`) and skipped.
DOES NOT: build steps or events; decide engagement parents.
KNOWN LIMITATIONS: llm_call timing is a gap estimate that includes queue and
  hook time; `ttft_ms` is missing for Claude.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from botsworth.adapters.claude import notices, records
from botsworth.adapters.claude.records import Line, message, ts_ms
from botsworth.adapters.claude.tree import ACCOUNT, PROVIDER, Ctx, Send, Tree
from botsworth.adapters.common.commands import command_class, command_key, command_norm, count_tests
from botsworth.adapters.common.links import body_hash
from botsworth.adapters.common.turns import envelope_body
from botsworth.model import ARGS_JSON_MAX, CostBasis, IdProv, Kind, Op, Prov, Role
from botsworth.pricing import Pricer

SPAWN_TOOLS = frozenset({"Agent", "Task"})
SEND_TOOLS = SPAWN_TOOLS | {"SendMessage"}
ASK_USER_TOOL = "AskUserQuestion"  # DM-40
REJECTION_TEXT = "The user doesn't want to proceed"  # DM-40 [D54]
_EXIT_TEXT = re.compile(r"(?:Exit code:|exit code|exited with code)\s*(\d+)", re.I)


@dataclass(slots=True)
class Call:
    op: Op
    ti: int
    rid: str
    lines: list[Line]
    duplicate: bool


@dataclass(slots=True)
class ToolUse:
    op: Op
    name: str | None
    inp: dict[str, Any]
    ti: int
    line: Line
    bi: int
    result: tuple[Line, dict[str, Any]] | None
    duplicate: bool


@dataclass(slots=True)
class Leaves:
    ops: list[Op] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    tools: list[ToolUse] = field(default_factory=list)
    sends: list[Send] = field(default_factory=list)
    llm_of: dict[tuple[int, str], Op] = field(default_factory=dict)
    stats: Counter = field(default_factory=Counter)
    notices: list[dict[str, notices.Notice]] = field(default_factory=list)  # per transcript index (DM-41)


def _blocks(rec: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    content = message(rec).get("content")
    if not isinstance(content, list):
        return []
    return [(bi, b) for bi, b in enumerate(content) if isinstance(b, dict)]


def _owners(ctx: Ctx) -> tuple[dict[str, int], dict[str, int]]:
    """requestId / tool_use id -> owning transcript index (first non-copy holder, else first holder)."""
    rid_owner: dict[str, int] = {}
    tool_owner: dict[str, int] = {}
    for prefer_copy in (False, True):
        for ti, t in enumerate(ctx.transcripts):
            for line in t.lines:
                if line.copy != prefer_copy or line.rec.get("type") != "assistant":
                    continue
                rid = line.rec.get("requestId")
                if rid:
                    rid_owner.setdefault(rid, ti)
                for _, b in _blocks(line.rec):
                    if b.get("type") == "tool_use" and b.get("id"):
                        tool_owner.setdefault(b["id"], ti)
    return rid_owner, tool_owner


def tool_duration(tur: Any) -> int | None:
    if not isinstance(tur, dict):
        return None
    for k in ("durationMs", "totalDurationMs"):
        if isinstance(tur.get(k), (int, float)):
            return int(tur[k])
    if isinstance(tur.get("durationSeconds"), (int, float)):
        return int(tur["durationSeconds"] * 1000)
    return None


def result_outcome(result: tuple[Line, dict[str, Any]] | None) -> tuple[str, int | None, str | None]:
    """(outcome, exit_code, exit_code_prov) of a paired tool result."""
    if result is None:
        return "unknown", None, None
    line, block = result
    tur = line.rec.get("toolUseResult")
    code = tur.get("exitCode") if isinstance(tur, dict) else None
    prov: str | None = Prov.PRESENT
    if not isinstance(code, int) or isinstance(code, bool):
        code, prov = None, None
        content = block.get("content")
        m = _EXIT_TEXT.search(content) if isinstance(content, str) else None
        if m:
            code, prov = int(m[1]), Prov.DERIVED
    if code is not None:
        return f"exit:{code}", code, prov
    if block.get("is_error"):
        return ("interrupted" if "interrupt" in str(block.get("content")).lower() else "error"), None, None
    return "ok", None, None


def _result_bytes(block: dict[str, Any]) -> int | None:
    content = block.get("content")
    if isinstance(content, str):
        return len(content.encode())
    if isinstance(content, list):
        return sum(len((b.get("text") or "").encode()) for b in content if isinstance(b, dict))
    return None


def _result_text(block: dict[str, Any]) -> str:
    """The text of a tool_result block: the string, else the first text block (tested, never stored, D39)."""
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                return b["text"]
    return ""  # FALLBACK-OK: DM-40 (no result text: not a rejection, user_wait NULL)


def user_wait(name: str | None, result: tuple[Line, dict[str, Any]] | None) -> str | None:
    """DM-40 [D54]: `ask`, `permission` or None for one tool call and its paired result."""
    if name == ASK_USER_TOOL:
        return "ask"
    if result is None:
        return None
    line, block = result
    # FALLBACK-OK: DM-40 (no permissionDecision, or an unseen shape: user_wait NULL otherwise)
    decision = line.rec.get("permissionDecision")
    source = decision.get("source") if isinstance(decision, dict) else None
    if isinstance(source, str) and source.startswith("user"):
        return "permission"
    if _result_text(block).lstrip().startswith(REJECTION_TEXT):
        return "permission"
    return None


def _leaf(ctx: Ctx, t_index: int, kind: Kind, key: str, parent: str, src: str, **kw: Any) -> Op:
    t = ctx.transcripts[t_index]
    return Op(
        op_id=ctx.op_id(kind, key),
        kind=kind,
        provider=PROVIDER,
        session_id=ctx.sid,
        src=src,
        parent=parent,
        project=ctx.project,
        account=ACCOUNT,
        agent_id=t.agent_id,
        agent_type=t.meta.get("agentType"),
        spawn_depth=t.spawn_depth,
        is_fork=t.is_fork,
        id_prov=IdProv.NATURAL,
        **kw,
    )


def _llm_call(
    ctx: Ctx, tree: Tree, pricer: Pricer, ti: int, rid: str, group: list[tuple[int, Line]], owner: int
) -> Call | None:
    t = ctx.transcripts[ti]
    picked = records.select_final([line.rec for _, line in group])
    if picked is None:
        return None
    final, cut_off = picked
    msg = message(final)
    first_index, first = group[0]
    duplicate = owner != ti
    key = rid + (f"@copy:{t.owner}" if duplicate else "")
    prior = next(
        (
            ln
            for ln in reversed(t.lines[:first_index])
            if ln.rec.get("type") != "assistant" and ln.rec.get("timestamp")
        ),
        None,
    )
    if prior is not None and notices.is_notification_attachment(prior.rec):
        # D55: such an attachment is stamped with the queue time but written when the thread next runs, so
        # it says nothing about when this call started; the group's first line is the best record there is
        prior = None
    start = ts_ms((prior or first).rec.get("timestamp"))
    end = ts_ms(final.get("timestamp"))
    dur = end - start if start is not None and end is not None else None
    usage = msg.get("usage")
    tok = records.tokens(usage) if isinstance(usage, dict) and usage else None
    model = msg.get("model")
    rates = pricer.resolve(model, PROVIDER)
    cost = pricer.cost(tok, rates) if tok else None
    op = _leaf(
        ctx,
        ti,
        Kind.LLM_CALL,
        key,
        tree.turn_of[(ti, first.n)],
        ctx.src(ti, first, group[-1][1]),
        cwd=final.get("cwd"),
        occ_key=f"{PROVIDER}:request:{rid}",
        duplicate_of=ctx.op_id(Kind.LLM_CALL, rid) if duplicate else None,
        start_ms=start,
        end_ms=end,
        dur_ms=dur,
        time_prov=Prov.DERIVED if dur is not None else Prov.MISSING,
        ttft_prov=Prov.MISSING,
        tok_prov=Prov.PRESENT if tok else Prov.MISSING,
        cost_basis=CostBasis.COMPUTED if cost else CostBasis.MISSING,
        price_alias=rates.price_alias if cost else None,
        price_fingerprint=pricer.fingerprint,
        cut_off=cut_off,
        stop_reason=msg.get("stop_reason"),
        model=model,
        role=Role.MAIN,
        label=model,
    )
    if tok:
        for name, value in tok.items():
            setattr(op, name, value)
    if cost:
        op.cost_usd, op.cost_in_usd, op.cost_out_usd = cost.cost_usd, cost.cost_in_usd, cost.cost_out_usd
        op.cost_cache_read_usd, op.cost_cache_write_usd = cost.cost_cache_read_usd, cost.cost_cache_write_usd
    return Call(op, ti, rid, [line for _, line in group], duplicate)


def _output_text(rline: Line | None, rblock: dict[str, Any] | None) -> str:
    """DM-29: the text to count tests in: `toolUseResult` stdout and stderr, else the result text."""
    tur = rline.rec.get("toolUseResult") if rline else None
    parts = [tur[k] for k in ("stdout", "stderr") if isinstance(tur, dict) and isinstance(tur.get(k), str)]
    if any(parts):  # both empty (a shell that wrote nothing to them): the result text
        return "\n".join(parts)
    return _result_text(rblock) if rblock is not None else ""  # FALLBACK-OK: DM-29 (no output: no counts)


def _tool_call(
    ctx: Ctx,
    ti: int,
    call: Op,
    use: tuple[Line, int, dict[str, Any]],
    result,
    owner: int,
    found: dict[str, notices.Notice],
) -> ToolUse:
    t = ctx.transcripts[ti]
    line, bi, block = use
    tuid = block["id"]
    name = block.get("name")
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    duplicate = owner != ti
    key = tuid + (f"@copy:{t.owner}" if duplicate else "")
    rline, rblock = result if result else (None, None)
    start = ts_ms(line.rec.get("timestamp"))
    end = ts_ms(rline.rec.get("timestamp")) if rline else None
    tur = rline.rec.get("toolUseResult") if rline else None
    is_async = isinstance(tur, dict) and (
        tur.get("status") == "async_launched" or tur.get("async") is True or tur.get("isAsync") is True
    )
    reported = tool_duration(tur)
    dur = (
        reported if reported is not None else (end - start if start is not None and end is not None else None)
    )
    if is_async:
        dur = reported or 0
        end = start + dur if start is not None else None
    outcome, exit_code, exit_prov = result_outcome(result)
    command = inp.get("command") if isinstance(inp.get("command"), str) else None
    bash = name == "Bash"
    klass = command_class(command) if bash else None
    counts = count_tests(_output_text(rline, rblock)) if klass == "test" else None
    bg_end = (
        notices.bg_end(found, tuid)
        if bash and isinstance(tur, dict) and isinstance(tur.get("backgroundTaskId"), str)
        else None
    )
    if bg_end is not None and start is not None and bg_end < start:
        bg_end = None  # FALLBACK-OK: DM-41 (a notice queued before its launch is no end of this run)
    path = next((inp[k] for k in ("file_path", "path", "notebook_path") if isinstance(inp.get(k), str)), None)
    op = _leaf(
        ctx,
        ti,
        Kind.TOOL_CALL,
        key,
        call.op_id,
        ctx.src(ti, line, rline if rline and rline.n > line.n else None, f"message.content.{bi}"),
        cwd=line.rec.get("cwd"),
        occ_key=f"{PROVIDER}:tool_use:{tuid}",
        duplicate_of=ctx.op_id(Kind.TOOL_CALL, tuid) if duplicate else None,
        start_ms=start,
        end_ms=end,
        dur_ms=dur,
        time_prov=Prov.PRESENT
        if reported is not None
        else (Prov.DERIVED if dur is not None else Prov.MISSING),
        is_async=is_async,
        tool_name=name,
        command=command[:ARGS_JSON_MAX] if command else None,
        command_norm=command_norm(command, ctx.project) if bash else None,
        command_key=command_key(command) if bash else None,
        command_class=klass,
        bg_end_ms=bg_end,
        tests_passed=counts[0] if counts else None,
        tests_failed=counts[1] if counts else None,
        file_path=path,
        exit_code=exit_code,
        exit_code_prov=exit_prov,
        is_error=bool(rblock.get("is_error")) if rblock is not None else None,
        interrupted=outcome == "interrupted",
        user_wait=user_wait(name, result),
        result_bytes=_result_bytes(rblock) if rblock is not None else None,
    )
    return ToolUse(op, name, inp, ti, line, bi, result, duplicate)


def build(ctx: Ctx, tree: Tree, pricer: Pricer) -> Leaves:
    out = Leaves()
    rid_owner, tool_owner = _owners(ctx)
    out.notices = [notices.scan(t) for t in ctx.transcripts]
    for ti, t in enumerate(ctx.transcripts):
        groups: dict[str, list[tuple[int, Line]]] = {}
        uses: dict[str, tuple[Line, int, dict[str, Any], str | None]] = {}
        results: dict[str, tuple[Line, dict[str, Any]]] = {}
        for index, line in enumerate(t.lines):
            r = line.rec
            kind = r.get("type")
            if kind == "assistant":
                rid = r.get("requestId")
                if rid:
                    groups.setdefault(rid, []).append((index, line))
                for bi, b in _blocks(r):
                    if b.get("type") == "tool_use" and b.get("id"):
                        uses.setdefault(b["id"], (line, bi, b, rid))
            elif kind == "user":
                for _, b in _blocks(r):
                    if b.get("type") == "tool_result":
                        results.setdefault(b.get("tool_use_id"), (line, b))
        for rid, group in groups.items():
            call = _llm_call(ctx, tree, pricer, ti, rid, group, rid_owner[rid])
            if call is None:
                out.stats["synthetic_groups_dropped"] += 1
                continue
            out.calls.append(call)
            out.ops.append(call.op)
            out.llm_of[(ti, rid)] = call.op
        for tuid, (line, bi, block, rid) in uses.items():
            call_op = out.llm_of.get((ti, rid)) if rid else None
            if call_op is None:
                out.stats["tool_without_llm"] += 1
                continue
            use = _tool_call(
                ctx, ti, call_op, (line, bi, block), results.get(tuid), tool_owner[tuid], out.notices[ti]
            )
            out.tools.append(use)
            out.ops.append(use.op)
            if use.name in SEND_TOOLS:
                msg = (
                    use.inp.get("prompt")
                    if use.name in SPAWN_TOOLS
                    else use.inp.get("message", use.inp.get("content"))
                )
                tur = use.result[0].rec.get("toolUseResult") if use.result else None
                out.sends.append(
                    Send(
                        use.op,
                        use.op.op_id.rsplit("/", 1)[1],
                        body_hash(envelope_body(msg)) if isinstance(msg, str) else None,
                        tur.get("agentId") if isinstance(tur, dict) else None,
                    )
                )
    return out
