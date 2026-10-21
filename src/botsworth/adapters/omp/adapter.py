"""CONTRACT: omp transcripts -> one canonical Bundle per session (build-plan §3 adapters).

GUARANTEES:
- Pure: files in, `Bundle` out; source files are opened read-only.
- Tree: session -> turn -> llm_call -> tool_call; a child transcript is an
  engagement whose parent and `caused_by` is the causing tool_call (links per
  `tree.link_files`, `caused_by_basis` id|name|time-heuristic|none; `none` ->
  parent = session). Depth > 32 or a cycle -> `BundleRejected` (D18).
- Turns: a non-steering `user` message or a `skill-prompt` custom message
  starts a turn; steering users, tool results, `irc:incoming` and other
  context records are interior. Activity before the first boundary gets a
  derived initial turn. Turn key `<agent|main>#<ordinal>`. A turn's `label` is
  its boundary's text (DM-22) and its `turn_kind`/`turn_origin` follow DM-20
  (`omp.turns`); the derived initial turn keeps `initial` and is harness/empty.
- llm_call: one per assistant message (with or without usage) and one per
  `model_usage` record (`role=aux`, timing missing). Op key = `responseId`
  (natural); without one, or when the id repeats inside the bundle,
  `<agent|main>@L<line>` (derived). `occ_key` = `omp:response:<responseId>`,
  else `omp:llm_call:<relpath>@L<line>`. Tokens: input, output, cacheRead,
  cacheWrite (one bucket, TTL unknown: `cache_write_tok` only, tiers null),
  reasoningTokens (inside out_tok); `context_tokens` = in + cache_read +
  cache_write. Cost copied from `usage.cost` (`reported`); absent -> null and
  `missing`. Time `present` from `timestamp/completedAt/duration`, ttft
  copied.
- tool_call: one per `toolCall` block, key = `toolCallId` (derived
  `<agent|main>@L<line>:<id>` if repeated in the bundle), `src` = the call line
  to its result line with path `message.content.<i>` (DM-7), `user_wait`
  `ask` for `ask` (DM-40, D54), `occ_key` =
  `omp:tool:<id>`; timing per `records.tool_timing`; async launches keep their
  launch duration (`is_async`); bash carries `command`, `command_norm`,
  `command_key` (D17), `command_class` (DM-28, D55) and, for a `test` command, `tests_passed`/`tests_failed`
  (DM-29) from the text blocks of its result.
- Steps per turn in file order (§4.10): thinking -> think, text -> say (the
  turn's last text -> answer), one step per tool call (a `task` call: one
  spawn step per child). `steps.skill` and op skill stay unset (D15).
- Containers own no metrics; `segments` (D19) = this file's activity spans
  split at `session_exit` -> resume; a `session_exit` record never extends a
  span; turns carry their file's segments clipped to the turn. start/end =
  first/last segment bound. No cross-file union.
- Each source file's `ts_list` = the sorted timestamps of its records (DM-40).
- A slash `skill_invocation` carries `step_id` = the first step of the turn it
  opens, null when that turn has no step (DM-30).
- Events per `events` module; every record carries a `src://` pointer (`h` on
  single-line pointers).
- `project` = the main transcript's first header cwd for every op; `cwd` =
  the record's file cwd (a later header's cwd applies from its line on).
- Session `title` = the main transcript's last `title` record, else its
  header `title`, with `title_source = generated` when set (DM-10);
  `entrypoint`/`originator` stay null (DM-26); `git_branch` and `harness_version` stay null (omp records
  neither; the header `version` is the file format). `first_line_ms` (D23) =
  the main transcript's first record timestamp in file order.
EXPECTS: `ref.files[0]` is the main transcript at `<root>/<dir>/<name>.jsonl`.
FAILURE BEHAVIOR: malformed / partial lines counted on the bundle; unknown
  record types counted in `stats` (never fatal); a file without a header ->
  ValueError.
DOES NOT: price (omp cost is reported; the pricer is unused); cross-bundle
  ownership or `duplicate_of`; rollups; storage.
KNOWN LIMITATIONS: `eval` run steps carry no `command_norm/command_key`
  (JS code, not a shell command; accepted by Kai 2026-10-12). Async job
  durations are deferred: no canonical field in V1, launch duration only.
  Messaging resumes (`irc:incoming`) are not engagements.
  Account keys map omp ChatGPT credentials to a plan only (`records`
  CONTRACT); that they are the Codex CLI's accounts is not verified.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from botsworth import pointer
from botsworth.adapters import BundleRef
from botsworth.adapters.common.commands import command_class, command_key, command_norm, count_tests
from botsworth.adapters.omp import discover as _discover
from botsworth.adapters.omp import events as ev
from botsworth.adapters.omp import steps as st
from botsworth.adapters.omp import turns as tn
from botsworth.adapters.omp.records import (
    FileFacts,
    FileLines,
    account,
    as_int,
    exit_code,
    is_async,
    iso_ms,
    num,
    parse,
    read_file,
    tool_timing,
)
from botsworth.adapters.omp.tree import FileNode, check_depth, link_files
from botsworth.model import (
    ARGS_JSON_MAX,
    LABEL_MAX,
    SNIPPET_MAX,
    AdapterTitleSource,
    Bundle,
    CostBasis,
    Event,
    EventKind,
    IdProv,
    Kind,
    Op,
    Outcome,
    Prov,
    Role,
    SourceFile,
    Step,
    Verb,
)
from botsworth.pricing import Pricer

PROVIDER = "omp"
UNKNOWN_ACCOUNT = "unknown:unknown"


def _q(key: str) -> str:
    return quote(key, safe="@#:-._")


def _op_id(sid: str, kind: Kind, key: str) -> str:
    return f"{PROVIDER}/{sid}/{kind.value}/{_q(key)}"


@dataclass
class _Ctx:
    """Bundle-wide build state."""

    sid: str
    project: str | None
    root: Path
    ops: list[Op] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    used_llm: set[str] = field(default_factory=set)
    used_tool: set[str] = field(default_factory=set)
    tool_ops: dict[tuple[Path, str], Op] = field(default_factory=dict)
    stats: Counter = field(default_factory=Counter)


@dataclass
class _File:
    """One file being walked."""

    path: Path
    rel: str
    fl: FileLines
    facts: FileFacts
    node: Op
    spawn_depth: int

    def src(self, first: int, last: int | None = None, part: str | None = None) -> str:
        last = first if last is None else last
        h = pointer.content_hash(self.fl.lines[first - 1]) if first == last else None
        return pointer.format(PROVIDER, self.rel, pointer.Lines(first, last), part, h)


def _clip(segments: list[list[int]], start: int | None, end: int | None) -> list[list[int]] | None:
    if start is None or end is None:
        return None
    out = [[max(a, start), min(b, end)] for a, b in segments if a <= end and b >= start]
    return out or [[start, end]]


class _Walk:
    """Second pass over one file: turns, leaves, steps, events, segments."""

    def __init__(self, ctx: _Ctx, f: _File):
        self.ctx, self.f = ctx, f
        self.agent = f.node.agent_id
        self.turn: Op | None = None
        self.turn_ix = -1
        self.turn_steps: list[Step] = []
        self.slash_event: Event | None = None
        self.turns: list[Op] = []
        self.cwd = f.facts.header.get("cwd")
        self.last_account: str | None = None
        self.segments: list[list[int]] = []
        self.seg_start: int | None = None
        self.last_activity: int | None = None
        self.exited: int | None = None

    # -- containers -----------------------------------------------------------------
    def new_turn(self, ln: int, ts: int | None, label: str | None, kind: str, origin: str) -> None:
        self.close_turn()
        self.turn_ix += 1
        who = self.agent or "main"
        f = self.f
        self.turn = Op(
            op_id=_op_id(self.ctx.sid, Kind.TURN, f"{who}#{self.turn_ix}"),
            kind=Kind.TURN.value,
            provider=PROVIDER,
            session_id=self.ctx.sid,
            src=f.src(ln),
            parent=f.node.op_id,
            agent_id=self.agent,
            engagement_ix=f.node.engagement_ix,
            project=self.ctx.project,
            cwd=self.cwd,
            id_prov=IdProv.DERIVED.value,
            start_ms=ts,
            end_ms=ts,
            time_prov=Prov.DERIVED.value,
            label=label,
            turn_kind=kind,
            turn_origin=origin,
        )
        self.ctx.ops.append(self.turn)
        self.turns.append(self.turn)
        self.turn_steps = []
        self.slash_event = None

    def close_turn(self) -> None:
        if self.slash_event is not None and self.turn_steps:
            first = self.turn_steps[0]  # DM-30: the first step of the turn the slash event opens
            self.slash_event.fields["step_id"] = f"{first.turn_id}#{first.ordinal}"
        self.slash_event = None
        says = [s for s in self.turn_steps if s.verb == Verb.SAY]
        if says:
            says[-1].verb = Verb.ANSWER.value

    def extend_turn(self, *values: int | None) -> None:
        t = self.turn
        for v in values:
            if v is not None:
                t.start_ms = v if t.start_ms is None else min(t.start_ms, v)
                t.end_ms = v if t.end_ms is None else max(t.end_ms, v)

    def add_step(self, op: Op, v: str, ln: int, part: str | None, **kw) -> Step:
        s = Step(
            turn_id=self.turn.op_id,
            ordinal=len(self.turn_steps),
            op_id=op.op_id,
            verb=v,
            src=self.f.src(ln, part=part),
            start_ms=op.start_ms,
            dur_ms=op.dur_ms,
            **kw,
        )
        self.turn_steps.append(s)
        self.ctx.steps.append(s)
        return s

    def event(
        self, kind: EventKind, ln: int, ts: int | None, op_id: str | None, /, part=None, **fields
    ) -> None:
        self.ctx.events.append(
            ev.event(
                kind,
                self.ctx.sid,
                self.agent,
                ln,
                src=self.f.src(ln, part=None if part is None else f"message.content.{part}"),
                ts_ms=ts,
                op_id=op_id,
                part=part,
                **fields,
            )
        )

    def here(self) -> str:
        return (self.turn or self.f.node).op_id

    # -- the walk -------------------------------------------------------------------
    def run(self) -> None:
        f = self.f
        for ln, r in f.fl.records:
            t = r.get("type")
            ts = iso_ms(r.get("timestamp"))
            if t == "session" and ln != f.facts.header_line:
                self.cwd = r.get("cwd") or self.cwd  # FALLBACK-OK: CONTRACT, later header cwd applies
            if t == "custom" and r.get("customType") == "session_exit":
                # Disposal, not work: never extends a span; the next record is a resume.
                self.exited = ts if ts is not None else self.last_activity
                continue
            if ts is not None:
                if self.exited is not None:
                    if self.seg_start is not None:
                        self.segments.append([self.seg_start, self.last_activity])
                    self.seg_start = None
                    self.exited = None
                    self.event(EventKind.RESUME, ln, ts, self.here(), from_session=self.ctx.sid, copied_ops=0)
                if self.seg_start is None:
                    self.seg_start = ts
                self.last_activity = ts if self.last_activity is None else max(self.last_activity, ts)
            m = r.get("message") if isinstance(r.get("message"), dict) else {}
            if t == "custom_message" and r.get("customType") == "skill-prompt":
                d = r.get("details") if isinstance(r.get("details"), dict) else {}
                child = self.agent is not None
                kind, origin = tn.classify(child=child, skill_prompt=True)
                self.new_turn(ln, ts, tn.skill_label(d), kind, origin)
                self.event(
                    EventKind.SKILL_INVOCATION,
                    ln,
                    ts,
                    self.turn.op_id,
                    skill=st.head(d.get("name"), 200),
                    args_head=st.head(d.get("args"), SNIPPET_MAX),
                    via="slash",
                    skill_md_src=d.get("path"),
                    step_id=None,
                )
                self.slash_event = self.ctx.events[-1]
            elif t == "message" and m.get("role") == "user" and not m.get("steering"):
                child = self.agent is not None
                kind, origin = tn.classify(child=child, message=m)
                from_agent = child or origin == tn.AGENT
                self.new_turn(ln, ts, tn.message_label(m, from_agent), kind, origin)
            elif t == "compaction":
                self.event(
                    EventKind.COMPACTION,
                    ln,
                    ts,
                    self.here(),
                    trigger="unknown",
                    pre_tokens=as_int(r.get("tokensBefore")),
                    post_tokens=as_int(r.get("tokensAfter")),
                )
            elif (t == "message" and m.get("role") == "assistant") or t == "model_usage":
                if self.turn is None:
                    kind, origin = tn.classify(child=self.agent is not None)
                    self.new_turn(ln, ts, "initial", kind, origin)
                    self.ctx.stats["derived_initial_turn"] += 1
                self.llm(ln, r, m if t == "message" else None, ts)
        self.close_turn()
        if self.seg_start is not None:
            self.segments.append([self.seg_start, self.last_activity])
        node = f.node
        node.segments = self.segments or None
        if self.segments:
            node.start_ms, node.end_ms = self.segments[0][0], self.segments[-1][1]
        node.account = node.account or UNKNOWN_ACCOUNT
        for turn in self.turns:
            turn.segments = _clip(self.segments, turn.start_ms, turn.end_ms)
            turn.account = turn.account or node.account

    def llm(self, ln: int, r: dict, m: dict | None, ts: int | None) -> None:
        ctx, f = self.ctx, self.f
        aux = m is None
        src_rec = r if aux else m
        usage = src_rec.get("usage") if isinstance(src_rec.get("usage"), dict) else None
        rid = None if aux else m.get("responseId")
        if isinstance(rid, str) and rid and rid not in ctx.used_llm:
            key, id_prov = rid, IdProv.NATURAL
            ctx.used_llm.add(rid)
        else:
            key, id_prov = f"{self.agent or 'main'}@L{ln}", IdProv.DERIVED
        occ = f"omp:response:{rid}" if isinstance(rid, str) and rid else f"omp:llm_call:{f.rel}@L{ln}"
        if aux:
            start, end, dur, time_prov, ttft = ts, None, None, Prov.MISSING, None
        else:
            start, end = as_int(m.get("timestamp")), as_int(m.get("completedAt"))
            time_prov = Prov.PRESENT if start is not None and end is not None else Prov.DERIVED
            end = ts if end is None else end
            if start is None:
                time_prov = Prov.MISSING if end is None else Prov.DERIVED
            dur = as_int(m.get("duration"))
            if dur is None and start is not None and end is not None:
                dur = end - start
            ttft = as_int(m.get("ttft"))
        provider = src_rec.get("provider") or r.get("provider")
        credential = src_rec.get("credentialId", r.get("credentialId"))
        acct = account(provider, credential, start if start is not None else ts) if provider else None
        if acct is not None:
            self.last_account = acct
        stop = src_rec.get("stopReason")
        status = src_rec.get("errorStatus")
        op = Op(
            op_id=_op_id(ctx.sid, Kind.LLM_CALL, key),
            kind=Kind.LLM_CALL.value,
            provider=PROVIDER,
            session_id=ctx.sid,
            src=f.src(ln),
            parent=self.turn.op_id,
            agent_id=self.agent,
            engagement_ix=f.node.engagement_ix,
            project=ctx.project,
            cwd=self.cwd,
            account=acct or self.last_account or UNKNOWN_ACCOUNT,
            spawn_depth=f.spawn_depth,
            is_fork=False,
            occ_key=occ,
            id_prov=id_prov.value,
            start_ms=start,
            end_ms=end,
            dur_ms=dur,
            time_prov=time_prov.value,
            ttft_ms=ttft,
            ttft_prov=(Prov.PRESENT if ttft is not None else Prov.MISSING).value,
            stop_reason=stop,
            error_kind=ev.error_kind(stop, status),
            model=src_rec.get("model"),
            role=(Role.AUX if aux else Role.MAIN).value,
            label=st.head(src_rec.get("model") or ("aux" if aux else "llm"), LABEL_MAX),
        )
        _tokens(op, usage)
        if usage is None:
            ctx.stats["llm_call_without_usage"] += 1
        ctx.ops.append(op)
        if self.turn.account is None:
            self.turn.account = op.account
        if f.node.account is None and acct is not None:
            f.node.account = acct
        self.extend_turn(op.start_ms, op.end_ms)
        if op.error_kind is not None:
            self.event(
                EventKind.API_ERROR,
                ln,
                ts,
                op.op_id,
                kind=op.error_kind,
                status=status,
                message_head=ev.message_head(src_rec.get("errorMessage")),
            )
        if aux:
            return
        content = m.get("content") if isinstance(m.get("content"), list) else []
        part_outcome = {"aborted": Outcome.INTERRUPTED, "error": Outcome.ERROR}.get(stop, Outcome.OK).value
        for ix, c in enumerate(content):
            if not isinstance(c, dict):
                continue
            typ = c.get("type")
            part = f"message.content.{ix}"
            if typ in ("thinking", "text"):
                v = Verb.THINK if typ == "thinking" else Verb.SAY
                self.add_step(op, v.value, ln, part, outcome=part_outcome, snippet=_snip(c.get(typ)))
            elif typ == "toolCall" and isinstance(c.get("id"), str):
                self.tool(op, ln, ix, c)

    def tool(self, llm: Op, ln: int, ix: int, c: dict) -> None:
        ctx, f = self.ctx, self.f
        cid = c["id"]
        tc = f.facts.tools[cid]
        args = tc.args
        name = tc.name
        if cid not in ctx.used_tool:
            key, id_prov = cid, IdProv.NATURAL
            ctx.used_tool.add(cid)
        else:
            key, id_prov = f"{self.agent or 'main'}@L{ln}:{cid}", IdProv.DERIVED
        start, end, timing = tool_timing(tc)
        paired = tc.result is not None
        if not paired:
            ctx.stats["tool_unpaired"] += 1
        code = exit_code(tc)
        is_err = bool(tc.result.get("isError")) if paired else None
        v = st.verb(name, args)
        tgt = st.target(name, args, ctx.project)
        why = st.intent(c, args)
        summ = st.summary(name, v, tgt, why)
        command = args.get("command") if name == "bash" and isinstance(args.get("command"), str) else None
        klass = command_class(command)
        counts = count_tests(_result_text(tc.result)) if klass == "test" else None
        op = Op(
            op_id=_op_id(ctx.sid, Kind.TOOL_CALL, key),
            kind=Kind.TOOL_CALL.value,
            provider=PROVIDER,
            session_id=ctx.sid,
            src=f.src(tc.line, tc.result_line or tc.line, part=f"message.content.{ix}"),
            parent=llm.op_id,
            agent_id=self.agent,
            engagement_ix=f.node.engagement_ix,
            project=ctx.project,
            cwd=self.cwd,
            account=llm.account,
            spawn_depth=f.spawn_depth,
            is_fork=False,
            occ_key=f"omp:tool:{cid}",
            id_prov=id_prov.value,
            start_ms=start,
            end_ms=end,
            dur_ms=end - start if start is not None and end is not None else None,
            time_prov=timing,
            is_async=is_async(tc),
            tool_name=name,
            command=command[:ARGS_JSON_MAX] if command else None,
            command_norm=command_norm(command, ctx.project),
            command_key=command_key(command),
            command_class=klass,
            tests_passed=counts[0] if counts else None,
            tests_failed=counts[1] if counts else None,
            file_path=st.file_path(args),
            exit_code=code,
            exit_code_prov=Prov.PRESENT.value if code is not None else None,
            is_error=is_err,
            user_wait="ask" if name == "ask" else None,
            label=st.head(summ, LABEL_MAX),
        )
        ctx.ops.append(op)
        ctx.tool_ops[(f.path, cid)] = op
        self.extend_turn(op.start_ms, op.end_ms)
        out = st.outcome(paired, bool(is_err), code)
        part = f"message.content.{ix}"
        targets = st.spawn_targets(args, tgt) if name == "task" else [tgt]
        for one in targets:
            s = self.add_step(op, v.value, ln, part, tool_name=name, target=one, outcome=out, summary=summ)
        if v == Verb.SKILL:
            self.event(
                EventKind.SKILL_INVOCATION,
                ln,
                op.start_ms,
                op.op_id,
                part=ix,
                skill=st.head(tgt.removeprefix(st.SKILL_SCHEME), 200),
                args_head=None,
                via="read",
                skill_md_src=tgt,
                step_id=f"{s.turn_id}#{s.ordinal}",
            )


def _result_text(result: dict | None) -> str:
    """DM-29: the text blocks of a tool result, joined (parsed for counts, never stored, D39)."""
    content = (result or {}).get("content")  # FALLBACK-OK: DM-29 (no result: no counts)
    if not isinstance(content, list):
        return ""  # FALLBACK-OK: DM-29 (no text blocks: no counts)
    return "\n".join(b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))


def _snip(text: object) -> str | None:
    return st.head(text, SNIPPET_MAX) or None


def _tokens(op: Op, usage: dict | None) -> None:
    if usage is None:
        op.tok_prov = Prov.MISSING.value
        op.cost_basis = CostBasis.MISSING.value
        return
    op.in_tok = as_int(usage.get("input"))
    op.out_tok = as_int(usage.get("output"))
    op.cache_read_tok = as_int(usage.get("cacheRead"))
    op.cache_write_tok = as_int(usage.get("cacheWrite"))
    op.reasoning_tok = as_int(usage.get("reasoningTokens"))
    parts = (op.in_tok, op.cache_read_tok, op.cache_write_tok)
    op.context_tokens = sum(parts) if all(p is not None for p in parts) else None
    op.tok_prov = Prov.PRESENT.value
    cost = usage.get("cost") if isinstance(usage.get("cost"), dict) else {}
    op.cost_usd = num(cost.get("total"))
    if op.cost_usd is None:
        op.cost_basis = CostBasis.MISSING.value
        return
    op.cost_basis = CostBasis.REPORTED.value
    op.cost_in_usd = num(cost.get("input"))
    op.cost_out_usd = num(cost.get("output"))
    op.cost_cache_read_usd = num(cost.get("cacheRead"))
    op.cost_cache_write_usd = num(cost.get("cacheWrite"))


def _source_file(path: Path, fl: FileLines) -> SourceFile:
    """Absolute path (source_files.path is absolute; src:// pointers stay root-relative)."""
    stat = path.stat()
    with path.open("rb") as fh:
        digest = hashlib.sha256(fh.read(4096)).hexdigest()
    return SourceFile(
        provider=PROVIDER,
        path=str(path.absolute()),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        sha_first_4k=digest,
        ts_list=sorted(ts for _, r in fl.records if (ts := iso_ms(r.get("timestamp"))) is not None),
    )


def _container(ctx: _Ctx, f_node: FileNode, facts: FileFacts, rel: str, src: str) -> Op:
    main = f_node.parent is None
    agent = None if main else facts.header["id"]
    title = facts.title or facts.header.get("title")
    return Op(
        op_id=_op_id(ctx.sid, Kind.SESSION, "-") if main else _op_id(ctx.sid, Kind.ENGAGEMENT, f"{agent}#0"),
        kind=(Kind.SESSION if main else Kind.ENGAGEMENT).value,
        provider=PROVIDER,
        session_id=ctx.sid,
        src=src,
        agent_id=agent,
        engagement_ix=None if main else 0,
        project=ctx.project,
        cwd=facts.header.get("cwd"),
        agent_name=None if main else f_node.path.stem,
        spawn_depth=f_node.spawn_depth,
        id_prov=IdProv.NATURAL.value,
        time_prov=Prov.DERIVED.value,
        caused_by_basis=None if main else f_node.basis,
        label=st.head(title or f_node.path.stem, LABEL_MAX),
        title=title if main else None,
        title_source=AdapterTitleSource.GENERATED.value if main and title else None,
        first_line_ms=facts.first_line_ms if main else None,
    )


def build_with_stats(ref: BundleRef) -> tuple[Bundle, Counter]:
    top = ref.files[0]
    root = top.parent.parent
    lines = {p: read_file(p) for p in ref.files}
    facts = {p: parse(lines[p], p) for p in ref.files}
    ctx = _Ctx(sid=ref.session_id, project=facts[top].header.get("cwd"), root=root)
    bundle = Bundle(provider=PROVIDER, bundle_id=ref.bundle_id, session_id=ref.session_id)
    nodes = link_files(top, facts)
    files: list[tuple[FileNode, _File]] = []
    for n in nodes:
        rel = n.path.relative_to(root).as_posix()
        fl, fp = lines[n.path], facts[n.path]
        f = _File(n.path, rel, fl, fp, None, n.spawn_depth)  # type: ignore[arg-type]
        f.node = _container(ctx, n, fp, rel, f.src(fp.header_line))
        ctx.ops.append(f.node)
        files.append((n, f))
        bundle.source_files.append(_source_file(n.path, fl))
        bundle.n_malformed += fl.n_malformed
        bundle.n_partial += fl.n_partial
        ctx.stats.update({f"unhandled:{k}": v for k, v in fp.unhandled.items()})
        ctx.stats[f"link:{n.basis or 'main'}"] += 1
    for _, f in files:
        _Walk(ctx, f).run()
    session = files[0][1].node
    for n, f in files[1:]:
        cause = ctx.tool_ops.get((n.parent, n.cause_call)) if n.cause_call else None
        f.node.parent = cause.op_id if cause else session.op_id
        f.node.caused_by = cause.op_id if cause else None
        if cause is None and n.basis != "none":
            f.node.caused_by_basis = "none"
            ctx.stats["link:cause_tool_missing"] += 1
    depth = check_depth(ctx.ops)
    seq: Counter = Counter()
    for op in ctx.ops:
        op.depth = depth[op.op_id]
        op.seq = seq[op.parent]
        seq[op.parent] += 1
    bundle.ops, bundle.steps, bundle.events = ctx.ops, ctx.steps, ctx.events
    return bundle, ctx.stats


class OmpAdapter:
    provider = PROVIDER

    def discover(self, root: Path):
        return _discover.discover(root)

    def build(self, ref: BundleRef, pricer: Pricer) -> Bundle:
        return build_with_stats(ref)[0]


ADAPTER = OmpAdapter()
