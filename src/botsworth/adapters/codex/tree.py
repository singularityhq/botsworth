"""CONTRACT: Codex rollout files -> one bundle's op tree, steps and events (design §4.3-§4.6, §4.10, §4.11).

GUARANTEES:
- `parse_file(path, relpath, pricer)` reads one rollout in file order:
  - turns: `task_started` (natural `turn_id`), else a `user_message` outside a
    turn (older files); ask label = first `user_message` (<= 120); the last
    say of a turn becomes `answer`; `turn_aborted` -> `interrupted`.
  - llm_calls: `token_usage_record` present in the file -> one op per
    `response_id` (natural); else one op per change of
    `token_count.info.total_token_usage` (`records.TokenTracker`), key
    `seg@<n>` (derived). Time is derived (gap since the previous token event
    or turn start). Cost from the given `Pricer` (`cost_basis` computed |
    missing; aliases D11).
  - every tool_call and every think/say/skill step is owned by an llm_call
    (no null `op_id`, tool_call.parent is an llm_call): items written before a
    token event belong to that event's call; in older rollouts the call's
    `response_item`s follow its `token_count` with the identical timestamp
    and belong to it; items left without a call when the turn ends (an
    aborted response) get an `open@L<n>` llm_call with tokens and cost
    missing.
  - results pair to calls by `call_id` only; `*_end` events give present
    duration and exit code; output text gives derived ones.
- `assemble(root, parses, rename, thread_title)` builds the `Bundle`: op ids per §4.3
  (child-thread leaves scoped `<thread>:<key>`), one session op for the root
  file and one engagement per child file (`parent = caused_by` = the
  `spawn_agent` tool_call paired by `collab_agent_spawn_end.call_id`, basis
  `id`; no match -> parent session, basis `none`), containers carry
  `segments` split at a repeated same-id `session_meta` (resume, D19) and no
  metrics, leaves carry `occ_key`, `spawn_depth`, `is_fork`; `duplicate_of`
  is a hint: the first op in ownership order with the same `(kind, occ_key)`.
- Session metadata (root thread only): `title` / `title_source` (DM-10, D43) =
  the given `rename` (session_index), else the last `thread_name_updated`
  (both `rename`), else the given `thread_title` (`threads.title`), else both
  None (the first user message is only the `label` fallback); `entrypoint` =
  `session_meta.payload.source` when a string, `originator` = its `originator`
  (DM-26); `git_branch` = session_meta `git.branch`; `harness_version` =
  session_meta `cli_version`; `first_line_ms` (D23) = the first envelope
  timestamp of the root rollout in file order; null when absent.
- Turns carry `turn_kind`/`turn_origin` (DM-20): child-thread turns
  message/agent; a main turn with no or blank `user_message` harness/empty;
  `source = exec` or `originator = codex_exec` message/exec; else human/typed.
  A turn's `src` is its first `user_message` line with path `payload.message`
  (a range over the turn only when it has no `user_message`).
- Tool_call `src` spans the call line to its result line with the json path of
  the call block (`payload.arguments|input|action`) and a hash over the range
  (DM-7). `user_wait` = `ask` on `request_user_input` (DM-40, D54; Codex records no approvals).
  `SourceFile.ts_list` = sorted timestamps of the file's records.
- `occ_key` (provider-scoped): tool_call = `codex:call:<call_id>`; natural llm_call =
  `codex:response:<response_id>`; derived
  llm_call = `codex:fp:<fingerprint of (total, last) usage>`, so a fork's or a copying
  session's prefix repeats its source's keys (design §4.3 asks for
  `<thread>:<segment>:<ordinal>`; that form collides between a fork's own calls
  and its parent's later calls, and some forks carry no source session_meta).
  Corpus 2026-10-12: 13,780 derived copies, every one in a fork child or a
  session that copied another session's history (B2 count reproduced).
EXPECTS: files in ownership order with the root thread first.
FAILURE BEHAVIOR: malformed lines and a trailing partial line are skipped and
  counted; unknown record types are counted in `FileParse.unknown`, never
  fatal; a thread cycle or op depth > 32 raises `BundleRejected` (D18).
DOES NOT: compute rollups or cross-bundle ownership; read anything but the given files.
KNOWN LIMITATIONS: llm_call time includes tool time (derived gap); the
  `open@` call of an aborted response has no usage; copied turns of a fork are
  kept as turns (their leaves carry the duplicate hint).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from botsworth import pointer
from botsworth.adapters import BundleRejected
from botsworth.adapters.codex import events as ev
from botsworth.adapters.codex import records, steps
from botsworth.adapters.common.taxonomy import clip, rel, summary
from botsworth.model import (
    LABEL_MAX,
    SNIPPET_MAX,
    TARGET_MAX,
    AdapterTitleSource,
    Bundle,
    CostBasis,
    Event,
    IdProv,
    Kind,
    Op,
    Outcome,
    Prov,
    Role,
    SourceFile,
    Step,
    TurnKind,
    Verb,
)
from botsworth.pricing import Pricer

PROVIDER = "codex"
MAX_DEPTH = 32
POST_WINDOW_MS = 250  # older rollouts write a call's items <= 250 ms after its token_count
TOOL_ITEMS = frozenset(
    {
        "function_call",
        "custom_tool_call",
        "local_shell_call",
        "web_search_call",
        "tool_search_call",
        "image_generation_call",
    }
)
OUTPUT_ITEMS = frozenset({"function_call_output", "custom_tool_call_output", "tool_search_output"})
IGNORED_EVENT_MSG = frozenset(
    {
        "agent_reasoning",
        "item_completed",
        "sub_agent_activity",
        "thread_settings_applied",
        "image_generation_end",
        "web_search_end",
        "collab_waiting_end",
        "collab_close_end",
        "collab_agent_interaction_end",
        "collab_resume_end",
        "view_image_tool_call",
        "entered_review_mode",
        "exited_review_mode",
        "agent_reasoning_section_break",
        "agent_reasoning_raw_content",
    }
)
IGNORED_RESPONSE = frozenset({"ghost_snapshot", "agent_message"})  # agent_message item = inter-agent mail
IGNORED_TOP = frozenset({"inter_agent_communication_metadata", "world_state", "realtime_item"})
TUR_RE = re.compile(rb'"type":\s*"token_usage_record"')  # structural key only: string content escapes quotes
SKILL_NAME = re.compile(r"<name>([^<]+)</name>")
SKILL_MD = re.compile(r"<path>([^<]+)</path>")


@dataclass(slots=True, eq=False)
class Node:
    """An op under construction; `op_id` is assigned by `assemble`."""

    kind: str
    key: str
    line: int
    turn: Node | None
    attrs: dict[str, Any]
    parent: Node | None = None  # None -> the file's container
    occ: str | None = None
    line_last: int | None = None
    path: str | None = None  # json path of `src`
    hashed: bool = False
    ask_line: int | None = None  # turns: the first `user_message` line (DM-7 turn ref)
    ask: str | None = None  # turns: that message, unclipped (DM-20 classification only)
    op_id: str = ""
    step_count: int = 0  # turns only
    skills: set[str] = field(default_factory=set)


@dataclass(slots=True, eq=False)
class StepNode:
    step: Step
    turn: Node
    line: int
    path: str | None = None
    op: Node | None = None


@dataclass(slots=True, eq=False)
class EventNode:
    raw: ev.RawEvent
    ts: int | None
    line: int
    op: Node | None
    account: str | None = None
    step: StepNode | None = None


@dataclass(slots=True)
class FileParse:
    path: Path
    relpath: str
    sid: str = ""
    parent_thread: str | None = None
    spawn_depth: int | None = None
    forked_from: str | None = None
    role: str | None = None
    nickname: str | None = None
    cwd: str | None = None
    originator: str | None = None
    entrypoint: str | None = None  # session_meta.payload.source when a string (DM-26)
    ts_list: list[int] = field(default_factory=list)
    title: str | None = None
    account: str | None = None
    first_ts: int | None = None
    last_ts: int | None = None
    n_lines: int = 0
    segments: list[list[int]] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    steps: list[StepNode] = field(default_factory=list)
    events: list[EventNode] = field(default_factory=list)
    spawns: dict[str, str] = field(default_factory=dict)  # child thread -> spawn call_id
    spawn_paths: dict[str, list[str]] = field(default_factory=dict)  # agent_path -> spawn call_ids
    agent_path: str | None = None
    hashes: dict[int, str] = field(default_factory=dict)  # line -> content hash
    source: SourceFile | None = None
    n_malformed: int = 0
    n_partial: int = 0
    unknown: Counter = field(default_factory=Counter)
    first_ask: str | None = None
    git_branch: str | None = None
    cli_version: str | None = None
    lines: list[bytes] = field(default_factory=list, repr=False)  # dropped after assembly


def _args(p: dict[str, Any]) -> Any:
    raw = p.get("arguments") if "arguments" in p else p.get("input")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw  # FALLBACK-OK: design §4.10 custom_tool_call input is free text (apply_patch, exec)
    return raw


class _Parser:
    """State machine over one rollout; see the module CONTRACT for the rules."""

    def __init__(self, path: Path, relpath: str, pricer: Pricer, lines: list[bytes]):
        self.r = FileParse(path=path, relpath=relpath)
        self.pricer = pricer
        self.lines = lines
        self.tur_mode = any(TUR_RE.search(ln) for ln in lines)
        self.tt = records.TokenTracker()
        self.model: str | None = None
        self.context_window: int | None = None
        self.cwd: str | None = None
        self.meta_thread = ""
        self.copy_boundary = False
        self.turn: Node | None = None
        self.turn_ord = 0
        self.call_ord = 0
        self.pending_tools: list[Node] = []
        self.pending_steps: list[StepNode] = []
        self.tools: dict[str, tuple[Node, list[StepNode]]] = {}
        self.last_call_end: int | None = None
        self.last_llm: Node | None = None
        self.post: tuple[Node, int] | None = None  # (call, ts): older rollouts write items after the call
        self.prev_ts: int | None = None
        self.prev_total: int | None = None
        self.open_compaction: EventNode | None = None
        self.open_rollback: EventNode | None = None
        self.last_limits: dict[str, tuple] = {}
        self.seg_start: int | None = None
        self.turn_open_ord: Counter = Counter()
        self.script: list[StepNode] | None = None  # steps of the latest exec script

    # ---- builders -----------------------------------------------------------------------
    def node(self, kind: str, key: str, ts: int | None, line: int, **attrs: Any) -> Node:
        n = Node(kind=kind, key=key, line=line, turn=self.turn, attrs={"start_ms": ts, **attrs})
        n.parent = self.turn
        self.r.nodes.append(n)
        return n

    def step(self, verb: str, ts: int | None, line: int, path: str | None, **kw: Any) -> StepNode | None:
        if self.turn is None:
            return None
        s = Step(turn_id="", ordinal=self.turn.step_count, op_id="", verb=verb, src="", start_ms=ts, **kw)
        self.turn.step_count += 1
        sn = StepNode(step=s, turn=self.turn, line=line, path=path)
        self.r.steps.append(sn)
        return sn

    def event(self, raw: ev.RawEvent, ts: int | None, line: int, op: Node | None) -> EventNode:
        e = EventNode(raw=raw, ts=ts, line=line, op=op)
        self.r.events.append(e)
        return e

    def skill_event(self, name: str, via: str, ts, line, op: Node | None, md, step: StepNode | None) -> None:
        if op is not None:
            if name in op.skills:
                return
            op.skills.add(name)
        e = self.event(ev.skill(name, via, md), ts, line, op)
        e.step = step

    # ---- ownership of items -------------------------------------------------------------
    def attach(self, ts: int | None, tool: Node | None = None, step: StepNode | None = None) -> None:
        if self.post is not None and self.post[0].turn is self.turn:
            call = self.post[0]
            if tool is not None:
                tool.parent = call
            if step is not None:
                step.op = call
            return
        if tool is not None:
            self.pending_tools.append(tool)
        if step is not None:
            self.pending_steps.append(step)

    def flush(self, call: Node) -> None:
        for t in self.pending_tools:
            t.parent = call
        for s in self.pending_steps:
            if s.op is None:
                s.op = call
        self.pending_tools.clear()
        self.pending_steps.clear()

    def open_call(self, ts: int | None, line: int) -> None:
        """Items whose response never got a token event (aborted): an llm_call with usage missing."""
        if not self.pending_tools and not self.pending_steps:
            return
        first_tool = next((t.key for t in self.pending_tools), None)
        turn_key = self.turn.attrs.get("src_key") if self.turn else None
        k = self.turn_open_ord[id(self.turn)]
        self.turn_open_ord[id(self.turn)] += 1
        anchor = first_tool or f"{turn_key or 'L' + str(line)}#{k}"
        first_line = min([t.line for t in self.pending_tools] + [s.line for s in self.pending_steps])
        start = self.last_call_end if self.last_call_end is not None else ts
        n = self.node(
            Kind.LLM_CALL,
            f"open@L{first_line}",
            start,
            first_line,
            end_ms=ts,
            time_prov=Prov.DERIVED if start is not None and ts is not None else Prov.MISSING,
            tok_prov=Prov.MISSING,
            cost_basis=CostBasis.MISSING,
            model=self.model,
            model_context_window=self.context_window,
            label=clip(f"{self.model or 'llm'} (no usage record)", LABEL_MAX),
            id_prov=IdProv.DERIVED,
            role=Role.MAIN,
        )
        n.occ = f"codex:open:{self.meta_thread}:{anchor}"
        n.hashed = True
        self.flush(n)

    # ---- turns ----------------------------------------------------------------------------
    def new_turn(self, ts: int | None, line: int, natural: str | None) -> None:
        self.close_turn(ts, line)
        self.turn = None
        t = self.node(Kind.TURN, f"#{self.turn_ord}", ts, line, end_ms=None, src_key=natural)
        self.turn_ord += 1
        self.turn = t
        t.parent = None
        self.last_call_end = ts

    def close_turn(self, ts: int | None, line: int, outcome: str | None = None) -> None:
        self.open_call(ts, line)
        t = self.turn
        if t is None:
            return
        if t.attrs.get("end_ms") is None:
            t.attrs["end_ms"] = ts
        t.line_last = line
        own = [s for s in self.r.steps if s.turn is t]
        if outcome:
            for s in own:
                if s.step.outcome == Outcome.UNKNOWN:
                    s.step.outcome = outcome
        said = [s for s in own if s.step.verb in (Verb.SAY, Verb.ANSWER)]
        if said:
            said[-1].step.verb, said[-1].step.summary = Verb.ANSWER, "answer"
        self.turn = None

    # ---- llm calls ------------------------------------------------------------------------
    def llm_call(self, key: str, usage: dict, ts: int | None, line: int, natural: bool, occ: str) -> Node:
        tok = records.tokens(usage)
        rates = self.pricer.resolve(self.model, PROVIDER)
        cost = self.pricer.cost(tok, rates)
        start = self.last_call_end if self.last_call_end is not None else ts
        attrs: dict[str, Any] = dict(
            end_ms=ts,
            time_prov=Prov.DERIVED if self.last_call_end is not None else Prov.MISSING,
            tok_prov=Prov.PRESENT,
            model=self.model,
            model_context_window=self.context_window,
            label=clip(self.model or "llm", LABEL_MAX),
            id_prov=IdProv.NATURAL if natural else IdProv.DERIVED,
            role=Role.MAIN,
            **tok,
        )
        if cost is None:
            attrs["cost_basis"] = CostBasis.MISSING
        else:
            attrs.update(
                cost_usd=cost.cost_usd,
                cost_in_usd=cost.cost_in_usd,
                cost_out_usd=cost.cost_out_usd,
                cost_cache_read_usd=cost.cost_cache_read_usd,
                cost_cache_write_usd=cost.cost_cache_write_usd,
                cost_basis=CostBasis.COMPUTED,
                price_alias=rates.price_alias,
                price_fingerprint=self.pricer.fingerprint,
            )
        n = self.node(Kind.LLM_CALL, key, start, line, **attrs)
        n.occ = occ
        n.hashed = True
        self.flush(n)
        if self.open_compaction is not None:
            self.open_compaction.raw.fields["post_tokens"] = tok["context_tokens"]
            self.open_compaction = None
        self.last_llm = n
        self.last_call_end = ts
        return n

    def token_count(self, p: dict, ts: int | None, line: int) -> None:
        rl = p.get("rate_limits")
        if rl:
            acct = ev.account_of(rl)
            self.r.account = self.r.account or acct
            for raw in ev.limit_states(rl, self.last_limits):
                e = self.event(raw, ts, line, self.last_llm)
                e.account = acct
        info = p.get("info")
        if not info or self.tur_mode:
            return
        total, last = info["total_token_usage"], info["last_token_usage"]
        kind = self.tt.feed(total, last)
        if kind == "dup":
            return
        if kind == "reset":
            self.rollback_from_tokens(total, last, ts, line)
        self.copy_boundary = False
        n = self.llm_call(
            f"seg@{self.call_ord}",
            last,
            ts,
            line,
            False,
            f"codex:fp:{records.fingerprint(total, last)}",
        )
        self.call_ord += 1
        self.prev_total = total.get("total_tokens")
        if ts is not None:
            self.post = (n, ts)

    def rollback_from_tokens(self, total: dict, last: dict, ts, line) -> None:
        if self.copy_boundary:
            return
        dropped = None
        if self.prev_total is not None:
            dropped = self.prev_total - ((total.get("total_tokens") or 0) - (last.get("total_tokens") or 0))
        if self.open_rollback is not None:
            self.open_rollback.raw.fields["dropped_tokens"] = dropped
            self.open_rollback = None
            return
        self.event(ev.rollback(dropped, None), ts, line, self.last_llm)

    def usage_record(self, p: dict, ts: int | None, line: int) -> None:
        rid = p.get("response_id")
        key = rid or f"seg@{self.call_ord}"
        occ = f"codex:response:{rid}" if rid else f"codex:tur:{self.meta_thread}:{self.call_ord}"
        self.llm_call(key, p.get("usage") or {}, ts, line, rid is not None, occ)
        self.call_ord += 1

    # ---- tools ----------------------------------------------------------------------------
    def tool_call(self, p: dict, pt: str, ts: int | None, line: int) -> None:
        cid = p.get("call_id") or p.get("id")
        name = p.get("name") or pt
        if p.get("namespace"):
            name = f"{p['namespace']}.{name}"
        project = self.r.cwd
        if pt == "local_shell_call":
            view = steps.classify("local_shell", p.get("action") or {}, project)
            name = "local_shell_call"
        elif pt == "web_search_call":
            view = steps.web_search(p.get("action") or {})
        else:
            view = steps.classify(name, _args(p), project)
        key = cid or f"L{line}"
        n = self.node(
            Kind.TOOL_CALL,
            key,
            ts,
            line,
            end_ms=None,
            time_prov=Prov.MISSING,
            tool_name=name,
            label=clip(view.summary or name, LABEL_MAX),
            command=view.command,
            command_norm=view.command_norm,
            command_key=view.command_key,
            id_prov=IdProv.NATURAL if cid else IdProv.DERIVED,
        )
        n.occ = f"codex:call:{cid}" if cid else f"codex:line:{self.meta_thread}:L{line}"
        n.hashed = True
        n.path = next((f"payload.{k}" for k in ("arguments", "input", "action") if k in p), None)
        if steps.base_name(name) == "request_user_input":
            n.attrs["user_wait"] = "ask"
        if pt in ("web_search_call", "image_generation_call"):
            n.attrs.update(end_ms=ts, time_prov=Prov.DERIVED)
        outcome0 = Outcome.OK if pt in ("web_search_call", "image_generation_call") else Outcome.UNKNOWN
        made: list[StepNode] = []
        targets = view.edit_targets if view.verb == Verb.EDIT and view.edit_targets else [view.target]
        for tg in targets:
            s = self.step(
                view.verb,
                ts,
                line,
                n.path,
                tool_name=name,
                target=tg,
                outcome=outcome0,
                summary=summary("edit", tg) if view.verb == Verb.EDIT else view.summary,
                sub_items=view.sub_items,
            )
            if s is not None:
                s.op = n
                made.append(s)
        self.tools[key] = (n, made)
        if view.sub_items is not None:
            self.script = made
        self.attach(ts, tool=n)
        for skill, md in steps.skill_reads(view.command):
            self.skill_event(skill, "read", ts, line, n, md, made[0] if made else None)

    def tool_output(self, p: dict, ts: int | None, line: int) -> None:
        n, made = self.tools.get(p.get("call_id"), (None, []))
        if n is None:
            self.r.unknown["unpaired_output"] += 1
            return
        if n.attrs.get("time_prov") != Prov.PRESENT:
            n.attrs.update(end_ms=ts, time_prov=Prov.DERIVED)
        n.line_last = line
        raw = p.get("output")
        out = raw if isinstance(raw, str) else json.dumps(raw)
        if steps.base_name(n.attrs.get("tool_name")) in steps.SPAWN_TOOLS:
            self.spawn_output(n.key, out)
        n.attrs["result_bytes"] = len(out.encode())
        code = steps.exit_from_output(out)
        for s in made:
            if s.step.sub_items is not None:  # exec script: per-inner-call exit codes in order
                execs = [x for x in s.step.sub_items if x.tool == "exec_command"]
                for x, c in zip(execs, steps.script_exit_codes(raw), strict=False):
                    x.outcome = Outcome.OK if c == 0 else f"exit:{c}"
                if s.step.outcome == Outcome.UNKNOWN:
                    s.step.outcome = Outcome.OK if "Script completed" in out[:300] else Outcome.ERROR
                n.attrs["is_error"] = s.step.outcome == Outcome.ERROR
            elif s.step.outcome == Outcome.UNKNOWN:
                s.step.outcome = Outcome.OK if code in (None, 0) else f"exit:{code}"
        if code is not None and n.attrs.get("exit_code_prov") != Prov.PRESENT:
            n.attrs.update(exit_code=code, exit_code_prov=Prov.DERIVED, is_error=code != 0)

    def spawn_output(self, call_id: str, out: str) -> None:
        """`spawn_agent` returns `{"agent_id": <thread>, "nickname": ...}`: an id link like spawn_end."""
        try:
            body = json.loads(out)
        except json.JSONDecodeError:
            return  # FALLBACK-OK: design §4.5 failed spawn (plain-text error) links nothing
        if isinstance(body, dict) and isinstance(body.get("agent_id"), str):
            self.r.spawns.setdefault(body["agent_id"], call_id)
        elif isinstance(body, dict) and isinstance(body.get("task_name"), str):
            self.r.spawn_paths.setdefault(body["task_name"], []).append(call_id)

    def script_patch(self, p: dict) -> bool:
        """An `exec-<uuid>` patch_apply_end is an inner `tools.apply_patch` of the running exec script."""
        if not str(p.get("call_id", "")).startswith("exec-") or self.script is None:
            return False
        for s in self.script:
            for x in s.step.sub_items or []:
                if x.tool == "apply_patch" and x.outcome == Outcome.UNKNOWN:
                    x.outcome = Outcome.OK if p.get("success", True) else Outcome.ERROR
                    return True
        return False

    def tool_end(self, p: dict, pt: str, ts: int | None, line: int) -> None:
        n, made = self.tools.get(p.get("call_id"), (None, []))
        if n is None:
            if not (pt == "patch_apply_end" and self.script_patch(p)):
                self.r.unknown[f"unpaired_{pt}"] += 1
            return
        project = self.r.cwd
        d = p.get("duration")
        if isinstance(d, dict) and n.attrs["start_ms"] is not None:
            dur = d.get("secs", 0) * 1000 + d.get("nanos", 0) // 1_000_000
            n.attrs.update(dur_ms=dur, end_ms=n.attrs["start_ms"] + dur, time_prov=Prov.PRESENT)
        s = made[0] if made else None
        if pt == "exec_command_end":
            code = p.get("exit_code")
            if code is not None:
                n.attrs.update(exit_code=code, exit_code_prov=Prov.PRESENT, is_error=code != 0)
            pc = p.get("parsed_cmd") or []
            if s is not None:
                if code is not None:
                    s.step.outcome = Outcome.OK if code == 0 else f"exit:{code}"
                if pc:
                    s.step.verb = steps.parsed_verb(pc)
                    s.step.target = steps.parsed_target(pc, project)
                    s.step.summary = steps.parsed_label(pc, project)
                    n.attrs["label"] = s.step.summary
            if not n.attrs.get("command_norm") and p.get("command"):
                cmd = p["command"]
                text = steps.unwrap(cmd)
                n.attrs.update(
                    command=clip(text, 2000) if text else None,
                    command_norm=steps.command_norm(cmd, project),
                    command_key=steps.command_key(cmd),
                )
            for c in pc:
                path = str(c.get("path") or "")
                if c.get("type") == "read" and path.endswith("SKILL.md"):
                    self.skill_event(Path(path).parent.name, "read", ts, line, n, path, s)
            n.attrs["file_path"] = next((c.get("path") for c in pc if c.get("path")), None)
        elif pt == "patch_apply_end":
            paths = list((p.get("changes") or {}).keys())
            ok = p.get("success", True)
            n.attrs.update(is_error=not ok, file_path=paths[0] if paths else None)
            for x in made:
                x.step.outcome = Outcome.OK if ok else Outcome.ERROR
            if len(made) == 1 and made[0].step.target is None and paths:
                made[0].step.target = clip(", ".join(rel(x, project) for x in paths), TARGET_MAX)
        else:  # mcp_tool_call_end
            inv = p.get("invocation") or {}
            res = p.get("result") or {}
            err = "Err" in res or bool((res.get("Ok") or {}).get("isError"))
            name = f"mcp:{inv.get('server')}.{inv.get('tool')}"
            n.attrs.update(is_error=err, label=clip(name, LABEL_MAX))
            if s is not None:
                s.step.target = clip(f"{inv.get('server')}.{inv.get('tool')}", TARGET_MAX)
                s.step.outcome = Outcome.ERROR if err else Outcome.OK

    # ---- the loop -------------------------------------------------------------------------
    def run(self) -> FileParse:
        r = self.r
        first = True
        for n, raw in enumerate(self.lines, 1):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                r.n_malformed += 1
                continue
            if first and records.generation(rec) == "v0":
                break  # no timestamps or token events (extract-codex Q5); discover skips these
            first = False
            ts = records.ms(rec.get("timestamp"))
            if self.post is not None and (ts is None or ts - self.post[1] > POST_WINDOW_MS):
                self.post = None
            if ts is not None:
                r.ts_list.append(ts)
                r.first_ts = r.first_ts if r.first_ts is not None else ts
                r.last_ts = ts
                if self.seg_start is None:
                    self.seg_start = ts
            self.record(rec, ts, n)
            self.prev_ts = ts
        self.close_turn(r.last_ts, len(self.lines))
        self.open_call(r.last_ts, len(self.lines))
        if self.seg_start is not None and r.last_ts is not None:
            r.segments.append([self.seg_start, r.last_ts])
        r.n_lines = len(self.lines)
        return r

    def record(self, rec: dict, ts: int | None, n: int) -> None:
        r = self.r
        typ = rec.get("type")
        p = rec.get("payload")
        p = p if isinstance(p, dict) else {}
        pt = p.get("type")
        if typ == "session_meta":
            self.session_meta(p, ts, n)
        elif typ == "turn_context":
            self.model = p.get("model") or self.model
            self.cwd = p.get("cwd") or self.cwd
        elif typ == "token_usage_record":
            self.usage_record(p, ts, n)
        elif typ == "compacted":
            self.compaction(ts, n)
        elif typ == "event_msg":
            self.event_msg(p, pt, ts, n)
        elif typ == "response_item":
            self.response_item(p, pt, ts, n)
        elif typ not in IGNORED_TOP:
            r.unknown[f"{typ}"] += 1

    def session_meta(self, p: dict, ts: int | None, n: int) -> None:
        r = self.r
        sid = p.get("id")
        if not r.sid:
            meta = p
            r.sid = sid
            r.cwd = p.get("cwd")
            self.cwd = r.cwd
            r.originator = p.get("originator")
            git = p.get("git")
            r.git_branch = (git.get("branch") or None) if isinstance(git, dict) else None
            r.cli_version = p.get("cli_version") or None
            r.forked_from = p.get("forked_from_id")
            src = p.get("source")
            r.entrypoint = (
                src if isinstance(src, str) and src else None
            )  # FALLBACK-OK: DM-26 object source = subagent, no entrypoint
            sub = (src.get("subagent") or {}) if isinstance(src, dict) else {}
            spawn = (sub.get("thread_spawn") or {}) if isinstance(sub, dict) else {}
            r.parent_thread = spawn.get("parent_thread_id")
            r.agent_path = spawn.get("agent_path")
            r.spawn_depth = spawn.get("depth", 0 if not r.parent_thread else None)
            r.role = meta.get("agent_role") or spawn.get("agent_role")
            r.nickname = meta.get("agent_nickname") or spawn.get("agent_nickname")
            self.meta_thread = sid
        elif sid == r.sid:  # the thread re-attached: a resume (D19)
            if self.seg_start is not None and self.prev_ts is not None:
                r.segments.append([self.seg_start, self.prev_ts])
            self.seg_start = ts
            self.event(ev.resume(sid), ts, n, self.last_llm)
            self.meta_thread = sid
        else:  # a fork copies its source's history after the source's session_meta
            self.meta_thread = sid
            self.copy_boundary = True

    def event_msg(self, p: dict, pt: str | None, ts: int | None, n: int) -> None:
        r = self.r
        if pt == "task_started":
            self.context_window = p.get("model_context_window") or self.context_window
            self.new_turn(ts, n, p.get("turn_id"))
        elif pt == "user_message":
            if self.turn is None or (self.turn.attrs.get("src_key") is None and self.turn.step_count > 0):
                self.new_turn(ts, n, None)  # older files: user_message is the boundary (design §4.5)
            if self.turn is not None and self.turn.attrs.get("label") is None:
                self.turn.ask_line, self.turn.ask = n, p.get("message")
                self.turn.attrs["label"] = clip(p.get("message"), LABEL_MAX)
                r.first_ask = r.first_ask or self.turn.attrs["label"]
        elif pt in ("task_complete", "turn_aborted"):
            aborted = pt == "turn_aborted"
            t = self.turn
            if t is not None:
                t.attrs["end_ms"] = ts
                if aborted:
                    t.attrs["interrupted"] = True
                if p.get("time_to_first_token_ms") is not None:
                    t.attrs.update(ttft_ms=p["time_to_first_token_ms"], ttft_prov=Prov.PRESENT)
                if p.get("duration_ms") is not None:
                    t.attrs["time_prov"] = Prov.PRESENT
            self.close_turn(ts, n, Outcome.INTERRUPTED if aborted else None)
        elif pt == "token_count":
            self.token_count(p, ts, n)
        elif pt == "agent_message":
            s = self.step(
                Verb.SAY,
                ts,
                n,
                "payload.message",
                snippet=clip(p.get("message"), SNIPPET_MAX),
                summary="said",
            )
            if s is not None:
                self.pending_steps.append(s)  # event_msg precedes the call's token event in every format
        elif pt in ("exec_command_end", "mcp_tool_call_end", "patch_apply_end"):
            self.tool_end(p, pt, ts, n)
        elif pt == "collab_agent_spawn_end":
            if p.get("new_thread_id") and p.get("call_id"):
                r.spawns.setdefault(p["new_thread_id"], p["call_id"])
        elif pt == "context_compacted":
            self.compaction(ts, n)
        elif pt in ("error", "stream_error"):
            self.event(ev.api_error(p), ts, n, self.last_llm)
            if p.get("codex_error_info") == "usage_limit_exceeded":
                self.event(ev.usage_limit_hit(), ts, n, self.last_llm)
        elif pt == "thread_rolled_back":
            self.open_rollback = self.event(ev.rollback(None, p.get("num_turns")), ts, n, self.last_llm)
        elif pt == "thread_name_updated":
            r.title = clip(p.get("thread_name"), LABEL_MAX) or r.title
        elif pt not in IGNORED_EVENT_MSG:
            r.unknown[f"event_msg:{pt}"] += 1

    def compaction(self, ts: int | None, n: int) -> None:
        if self.open_compaction is not None and self.open_compaction.ts == ts:
            return  # `compacted` + `context_compacted` describe one compaction
        pre = self.last_llm.attrs.get("context_tokens") if self.last_llm else None
        self.open_compaction = self.event(ev.compaction(pre, "unknown"), ts, n, self.last_llm)

    def response_item(self, p: dict, pt: str | None, ts: int | None, n: int) -> None:
        if pt in TOOL_ITEMS:
            self.tool_call(p, pt, ts, n)
        elif pt in OUTPUT_ITEMS:
            self.tool_output(p, ts, n)
        elif pt == "reasoning":
            text = " ".join(x.get("text", "") for x in p.get("summary") or [] if isinstance(x, dict))
            snippet = clip(text, SNIPPET_MAX)
            s = self.step(Verb.THINK, ts, n, "payload.summary", snippet=snippet, summary="reasoning")
            if s is not None:
                self.attach(ts, step=s)
        elif pt == "message":
            if p.get("role") == "user":
                self.user_content(p, ts, n)
        elif pt not in IGNORED_RESPONSE:
            self.r.unknown[f"response_item:{pt}"] += 1

    def user_content(self, p: dict, ts: int | None, n: int) -> None:
        for c in p.get("content") or []:
            text = c.get("text") if isinstance(c, dict) else None
            if not text or not text.lstrip().startswith("<skill>"):
                continue
            name = SKILL_NAME.search(text)
            md = SKILL_MD.search(text)
            if not name:
                continue
            skill = name.group(1).strip()
            target = clip(skill, TARGET_MAX)
            s = self.step(Verb.SKILL, ts, n, None, target=target, summary=summary("skill", skill))
            if s is not None:
                self.pending_steps.append(s)
            self.skill_event(skill, "slash", ts, n, None, md.group(1) if md else None, s)


def source_file(path: Path, ts_list: list[int]) -> SourceFile:
    st = path.stat()
    with path.open("rb") as f:
        head = f.read(4096)
    return SourceFile(
        provider=PROVIDER,
        path=str(path),
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
        sha_first_4k=hashlib.sha256(head).hexdigest(),
        ts_list=sorted(ts_list),
    )


def parse_file(path: Path, relpath: str, pricer: Pricer) -> FileParse:
    lines, partial = records.read_lines(path)
    parser = _Parser(path, relpath, pricer, lines)
    r = parser.run()
    r.n_partial = int(partial)
    r.lines = lines
    r.source = source_file(path, r.ts_list)
    return r


# ---- assembly ----------------------------------------------------------------------------------
def _src(r: FileParse, first: int, last: int | None = None, path: str | None = None, hashed=False) -> str:
    end = last or first
    h = None
    if hashed:
        h = pointer.content_hash(b"".join(r_line(r, i) for i in range(first, end + 1)))
    return pointer.format(PROVIDER, r.relpath, pointer.Lines(first, end), path, h)


def r_line(r: FileParse, line: int) -> bytes:
    return r.lines[line - 1] + b"\n"


def turn_class(root_r: FileParse, ask: str | None, has_ask: bool, is_agent: bool):
    """DM-20 Codex table -> (turn_kind, turn_origin). Agent-transcript turns are `message`/`agent`."""
    if is_agent:
        return TurnKind.MESSAGE, "agent"
    if not has_ask or not (ask or "").strip():
        return TurnKind.HARNESS, "empty"
    if root_r.entrypoint == "exec" or root_r.originator == "codex_exec":
        return TurnKind.MESSAGE, "exec"
    return TurnKind.HUMAN, "typed"


def assemble(
    root: str, parses: list[FileParse], rename: str | None = None, thread_title: str | None = None
) -> Bundle:
    """`rename`: the `session_index.jsonl` name; `thread_title`: `threads.title` (D43)."""
    by_sid = {r.sid: r for r in parses}
    if root not in by_sid:
        raise BundleRejected("root thread file missing", [root])
    spawn_call: dict[str, tuple[str, str]] = {}  # child -> (sender thread, call_id)
    for r in parses:
        for child, call in r.spawns.items():
            spawn_call.setdefault(child, (r.sid, call))

    def parent_thread(r: FileParse) -> str | None:
        p = r.parent_thread or spawn_call.get(r.sid, (None, None))[0]
        return p if p in by_sid and p != r.sid else None

    for r in parses:  # every thread reaches the root without a cycle (D18); the root has no parent
        seen = [r.sid]
        while seen[-1] != root or len(seen) == 1:
            p = parent_thread(by_sid[seen[-1]])
            if p is None:
                break
            if p in seen:
                raise BundleRejected("spawn cycle", [f"codex/{root}/engagement/{x}#0" for x in seen])
            seen.append(p)

    bundle = Bundle(provider=PROVIDER, bundle_id=root, session_id=root)
    root_r = by_sid[root]
    project = root_r.cwd
    session_id = f"codex/{root}/session/-"
    seen_occ: dict[tuple[str, str], str] = {}
    dup_ids: set[str] = set()
    all_nodes: dict[str, Node] = {}
    containers: dict[str, Op] = {}
    for r in parses:
        agent = None if r.sid == root else r.sid
        scope = agent or "main"
        account = r.account or ev.DEFAULT_ACCOUNT
        is_fork = r.forked_from is not None
        common = dict(
            provider=PROVIDER,
            session_id=root,
            agent_id=agent,
            project=project,
            account=account,
            spawn_depth=r.spawn_depth if agent else 0,
            is_fork=is_fork,
        )
        session_title, title_source = None, None
        if agent is None:
            if rename or r.title:
                session_title, title_source = rename or r.title, AdapterTitleSource.RENAME
            elif thread_title:
                session_title, title_source = thread_title, AdapterTitleSource.THREAD_TITLE
        label = clip(
            session_title or r.title or r.first_ask or r.nickname or r.originator or "codex", LABEL_MAX
        )
        src = _src(r, 1, max(r.n_lines, 1))
        if agent is None:
            container = Op(
                op_id=session_id,
                kind=Kind.SESSION,
                parent=None,
                src=src,
                cwd=r.cwd,
                start_ms=r.first_ts,
                end_ms=r.last_ts,
                segments=r.segments or None,
                label=label,
                title=session_title,
                title_source=title_source,
                entrypoint=r.entrypoint,
                originator=r.originator,
                git_branch=r.git_branch,
                harness_version=r.cli_version,
                first_line_ms=r.first_ts,
                id_prov=IdProv.NATURAL,
                **common,
            )
        else:
            ptid = parent_thread(r)
            call = spawn_call.get(r.sid)
            caused, basis = None, "none"
            if ptid is not None and call is not None and call[0] == ptid:
                caused, basis = call[1], "id"
            elif ptid is not None and r.agent_path and by_sid[ptid].spawn_paths.get(r.agent_path):
                caused, basis = (
                    by_sid[ptid].spawn_paths[r.agent_path].pop(0),
                    "name",
                )  # task_name = agent_path
            if caused is not None:
                caused = f"codex/{root}/tool_call/" + (caused if ptid == root else f"{ptid}:{caused}")
                if caused not in all_nodes:
                    caused, basis = None, "none"  # FALLBACK-OK: design §4.5 no spawn match -> session, none
            container = Op(
                op_id=f"codex/{root}/engagement/{agent}#0",
                kind=Kind.ENGAGEMENT,
                parent=caused or session_id,
                caused_by=caused,
                caused_by_basis=basis,
                engagement_ix=0,
                agent_type=r.role,
                agent_name=r.nickname,
                src=src,
                cwd=r.cwd,
                start_ms=r.first_ts,
                end_ms=r.last_ts,
                segments=r.segments or None,
                label=label,
                id_prov=IdProv.NATURAL,
                **common,
            )
        containers[r.sid] = container
        bundle.ops.append(container)
        for nd in r.nodes:
            if nd.kind == Kind.TURN:
                nd.op_id = f"codex/{root}/turn/{scope}{nd.key}"
            elif agent:
                nd.op_id = f"codex/{root}/{nd.kind}/{agent}:{nd.key}"
            else:
                nd.op_id = f"codex/{root}/{nd.kind}/{nd.key}"
            all_nodes[nd.op_id] = nd
        for nd in r.nodes:
            a = dict(nd.attrs)
            a.pop("src_key", None)
            dup = None
            if nd.occ is not None:
                k = (nd.kind, nd.occ)
                if k in seen_occ:
                    dup = seen_occ[k]
                    dup_ids.add(nd.op_id)
                else:
                    seen_occ[k] = nd.op_id
            if nd.kind in (Kind.LLM_CALL, Kind.TOOL_CALL) and a.get("dur_ms") is None:
                a["dur_ms"] = _dur(nd)
            if nd.kind == Kind.TURN:
                a["turn_kind"], a["turn_origin"] = turn_class(
                    root_r, nd.ask, nd.ask_line is not None, agent is not None
                )
                if a.get("end_ms") is None:
                    a["end_ms"] = r.last_ts
                a.setdefault("time_prov", Prov.DERIVED)
                if a["start_ms"] is not None and a["end_ms"] is not None:
                    a["segments"] = [[a["start_ms"], a["end_ms"]]]
            last = nd.line_last if nd.kind != Kind.LLM_CALL else None
            op = Op(
                op_id=nd.op_id,
                kind=nd.kind,
                parent=nd.parent.op_id if nd.parent is not None else container.op_id,
                src=_src(r, nd.ask_line, None, "payload.message", True)
                if nd.ask_line is not None
                else _src(r, nd.line, last, nd.path, nd.hashed),
                cwd=r.cwd,
                occ_key=nd.occ,
                duplicate_of=dup,
                **common,
                **a,
            )
            if nd.kind == Kind.TURN:
                op.id_prov = IdProv.DERIVED
            bundle.ops.append(op)
        for sn in r.steps:
            s = sn.step
            s.turn_id = sn.turn.op_id
            assert sn.op is not None, f"step without an owning op: {r.relpath}#L{sn.line}"
            s.op_id = sn.op.op_id
            s.src = _src(r, sn.line, None, sn.path, True)
            if s.verb not in (Verb.THINK, Verb.SAY, Verb.ANSWER, Verb.SKILL):
                s.dur_ms = sn.op.attrs.get("dur_ms") or _dur(sn.op)
            bundle.steps.append(s)
        per_line: Counter = Counter()
        for e in r.events:
            k = per_line[e.line]
            per_line[e.line] += 1
            fields = dict(e.raw.fields)
            if e.step is not None:
                fields["step_id"] = f"{e.step.turn.op_id}#{e.step.step.ordinal}"
            op_id = e.op.op_id if e.op is not None else None
            if op_id is None and e.step is not None and e.step.op is not None:
                op_id = e.step.op.op_id
            if op_id in dup_ids:
                fields["in_copied_op"] = True  # fork-copy replay; consumers may skip
            bundle.events.append(
                Event(
                    event_id=f"codex/{root}/event/{scope}:L{e.line}#{k}",
                    kind=e.raw.kind,
                    session_id=root,
                    src=_src(r, e.line, None, e.raw.json_path, True),
                    ts_ms=e.ts,
                    op_id=op_id,
                    account=e.account or account,
                    basis=e.raw.basis,
                    fields=fields,
                )
            )
        bundle.source_files.append(r.source)
        bundle.n_malformed += r.n_malformed
        bundle.n_partial += r.n_partial
        r.lines = []
    _depths(bundle)
    return bundle


def _dur(n: Node) -> int | None:
    a = n.attrs
    if a.get("end_ms") is None or a.get("start_ms") is None or a.get("time_prov") == Prov.MISSING:
        return None
    return a["end_ms"] - a["start_ms"]


def _depths(bundle: Bundle) -> None:
    by_id = {o.op_id: o for o in bundle.ops}
    for o in bundle.ops:
        chain: list[Op] = []
        cur: Op | None = o
        while cur is not None and cur.depth is None:
            if len(chain) > MAX_DEPTH or any(cur is x for x in chain):
                raise BundleRejected(f"cycle or op depth > {MAX_DEPTH}", [x.op_id for x in chain])
            chain.append(cur)
            # a dangling parent ends the chain; stream.defects reports it
            cur = by_id.get(cur.parent) if cur.parent is not None else None
        base = cur.depth if cur is not None else -1
        for i, x in enumerate(reversed(chain), 1):
            x.depth = base + i
        if o.depth > MAX_DEPTH:
            raise BundleRejected(f"op depth > {MAX_DEPTH}", [o.op_id])
