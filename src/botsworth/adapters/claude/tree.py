"""CONTRACT: Claude Code containers — session, engagements, turns — and their links (design §4.3, §4.5, §4.6).

GUARANTEES:
- `build(ctx)` makes one session op, one engagement per arrival in an agent
  transcript (`{agent_id}#{engagement_ix}`), and one turn per boundary
  (`{agent_id or main}#{ordinal}`). Main transcript boundary = a typed user
  prompt (string/text content without relay envelopes, notification or
  caveat prefixes, compact summaries or tool results; `isMeta` only with a
  `<command-name>` tag or human origin). Agent transcript boundary = the
  first record or an arrival (`<teammate-message`, `<agent-message`,
  coordinator relay); each agent boundary opens an engagement and its turn.
  The first record of a transcript always opens a turn. Every line maps to
  exactly one turn (`turn_of`).
- `link(ctx, tree, sends)` parents each engagement under the tool call that
  caused it: `#0` by id (`meta.toolUseId` = the call key, or the call's
  result `agentId` = this agent), else by content hash of the arrival body
  against Agent/Task prompts and SendMessage messages of other agents; several
  equal bodies resolve to the latest send at or before the arrival. A unique
  match sets `parent = caused_by` with basis `id` or `content_hash`; no unique
  match leaves parent = session, basis `none`.
- `finalize(ctx, tree, leaves)` applies the §4.5 cycle rule, then checks the
  tree: every op reachable from the session, depth <= 32 (D18). It sets
  `depth`, `seq` (position among siblings), container `start_ms`/`end_ms` =
  min/max over the container's own records and its direct children,
  `time_prov` (derived, or missing with no timestamps), and `segments` = the
  span of the container's own records in its own file (D19; one segment:
  Claude files carry no resume marker; `[]` for a session without a main
  transcript). Returns all ops ordered by depth (parents first), siblings in creation order.
- Containers carry no own metrics (invariant 2); session label = the title
  (clipped), else the first turn label, else the id.
- Every turn carries `turn_kind` / `turn_origin` (DM-20). Main-transcript
  turns: the first matching row of the DM-20 Claude table (`classify`);
  agent-transcript turns: `message` / `agent`. A `<command-name>` record is
  a skill or custom command (human / `slash`) only when the next `user`
  record of the transcript has the same `promptId`, `isMeta` and text (DM-21);
  otherwise it is a built-in (harness / `builtin:<name>`, `/compact` included).
  Turn boundaries are unchanged.
- Turn labels (<= 120): a command turn = `/X` plus its `<command-args>` text
  (DM-21); an `<mc-message>` = its body; a plugin message = the text after the
  `The <name> plugin sent a message` line (DM-23); else `envelope_body`.
- Session metadata: `title` (DM-10) = the last main-transcript `custom-title`
  (`title_source = rename`), else the last `ai-title`, else the first `summary`
  (`generated`); no title -> both null. `entrypoint` (DM-26) = the first
  main-transcript record that has one. `git_branch` = the first `gitBranch` other than
  ''/'HEAD' in any transcript; `harness_version` = the first `version`.
  Each stays null when no record carries it. `first_line_ms` (D23) = the
  timestamp of the first timestamped line of the main transcript in file
  order (a resumed copy's first line is stamped at copy time, while its
  copied records keep older stamps); null without a main transcript.
EXPECTS: transcripts in attribution owner order.
FAILURE BEHAVIOR: a cycle left after the rule, depth > 32, a repeated op_id
  or a parent outside the bundle -> `BundleRejected(reason, op_ids)`.
DOES NOT: build leaves (calls.py), steps or events; decide cross-bundle ownership.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from botsworth import pointer
from botsworth.adapters import BundleRejected
from botsworth.adapters.claude.attribution import Transcript
from botsworth.adapters.claude.records import Line, message, ts_ms
from botsworth.adapters.common.links import apply_cycle_rule, body_hash, latest_preceding
from botsworth.adapters.common.turns import clip, envelope_body, starts_with_any
from botsworth.model import (
    LABEL_MAX,
    AdapterTitleSource,
    IdProv,
    Kind,
    LinkBasis,
    Op,
    Prov,
    TurnKind,
)

PROVIDER = "claude_code"
ACCOUNT = "anthropic:default"  # design §4.11: no plan field in Claude transcripts
MAX_DEPTH = 32  # D18
NOISE = (
    "<system-reminder",
    "<task-notification",
    "<local-command-caveat",
    "<local-command-stdout",
    "<local-command-stderr",
    "Caveat: The messages below were generated",
    "Base directory for this skill:",
    "[SYSTEM NOTIFICATION - NOT USER INPUT]",
    "[Your previous response had no visible output",
    "This session is being continued from a previous conversation",
)
_INTERIOR_ORIGINS = frozenset({"task-notification", "system", "hook", "skill"})
_COMMAND = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_MC_MESSAGE = re.compile(r"<mc-message\b[^>]*>(.*?)</mc-message>", re.S)
_PLUGIN_PREFIX = re.compile(r"\A\s*The .+? plugin sent a message[^\n]*\n?")
_BUILTIN_BASH = ("<bash-input>", "<bash-stdout>", "<local-command-stdout>")
_PEER_ORIGINS = ("peer", "coordinator")


@dataclass(slots=True)
class Ctx:
    sid: str
    root: Path
    project: str
    transcripts: list[Transcript]
    _rel: dict[int, str] = field(default_factory=dict)

    def op_id(self, kind: str, key: str) -> str:
        return f"{PROVIDER}/{self.sid}/{kind}/{key}"

    def src(
        self, ti: int, first: Line | int, last: Line | int | None = None, json_path: str | None = None
    ) -> str:
        """Pointer to lines first..last of transcript `ti`; `h` only for a single line."""
        rel = self._rel.get(ti)
        if rel is None:
            rel = self._rel[ti] = self.transcripts[ti].path.relative_to(self.root).as_posix()
        a = first.n if isinstance(first, Line) else first
        b = a if last is None else (last.n if isinstance(last, Line) else last)
        h = first.h if isinstance(first, Line) and a == b else None
        return pointer.format(PROVIDER, rel, pointer.Lines(a, b), json_path, h)


@dataclass(slots=True)
class Arrival:
    engagement: Op
    ti: int
    body_hash: str | None


@dataclass(slots=True)
class Send:
    """A tool call that may cause an engagement (Agent/Task spawn, SendMessage)."""

    op: Op
    key: str
    body_hash: str | None
    result_agent: str | None


@dataclass(slots=True)
class Tree:
    session: Op
    containers: list[Op]
    arrivals: list[Arrival]
    turn_of: dict[tuple[int, int], str]
    spans: dict[str, list[int]]


def arrival_text(rec: dict[str, Any]) -> str:
    content = message(rec).get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    blocks = [b for b in content if isinstance(b, dict)]
    texts = [b.get("text") or "" for b in blocks if b.get("type") == "text"]
    if any(b.get("type") == "tool_result" for b in blocks):
        # a fork's first tool_result + directive record is an arrival; other tool results are interior
        return "\n".join(texts) if any("<fork-boilerplate>" in t for t in texts) else ""
    return "\n".join(texts)


def is_boundary(rec: dict[str, Any], agent_id: str | None, first: bool) -> bool:
    if rec.get("type") != "user":
        return False
    text = arrival_text(rec).lstrip()
    if not text or rec.get("isCompactSummary") or rec.get("sourceToolUseID"):
        return False
    if starts_with_any(text, NOISE):
        return False
    origin = rec.get("origin") or {}  # FALLBACK-OK: design §4.5 origin is optional
    if origin.get("kind") in _INTERIOR_ORIGINS:
        return False
    if agent_id:
        return (
            first
            or "<teammate-message" in text
            or "<agent-message" in text
            or text.startswith("The coordinator sent a message")
        )
    if "<teammate-message" in text or "<agent-message" in text:
        return False
    return not rec.get("isMeta") or origin.get("kind") == "human" or "<command-name>" in text


def expanded_prompt_follows(lines: list[Line], i: int) -> bool:
    """DM-21: the next `user` record is the command's expanded prompt (same promptId, isMeta, text)."""
    prompt_id = lines[i].rec.get("promptId")
    for j in range(i + 1, len(lines)):
        nxt = lines[j].rec
        if nxt.get("type") != "user":
            continue
        return (
            bool(prompt_id)
            and nxt.get("promptId") == prompt_id
            and bool(nxt.get("isMeta"))
            and bool(arrival_text(nxt).strip())
        )
    return False


def classify(rec: dict[str, Any], text: str, lines: list[Line], i: int) -> tuple[TurnKind, str]:
    """DM-20 Claude table: (turn_kind, turn_origin) of a main-transcript boundary; first match wins."""
    head = text.lstrip()
    origin = rec.get("origin") or {}  # FALLBACK-OK: DM-20 origin is optional (older harness rows)
    origin_kind, source = origin.get("kind"), rec.get("promptSource")
    command = _COMMAND.search(text)
    expanded = command is not None and expanded_prompt_follows(lines, i)
    if head.startswith("[Request interrupted by user"):
        return TurnKind.HARNESS, "interrupt"
    if head in ("", "(no text)"):
        return TurnKind.HARNESS, "empty"
    if command and not expanded:
        return TurnKind.HARNESS, f"builtin:{command[1].strip().lstrip('/')}"
    if head.startswith(_BUILTIN_BASH):
        return TurnKind.HARNESS, "builtin:bash"
    if origin_kind == "plugin":
        name = origin.get("name")
        # FALLBACK-OK: DM-20 table row `plugin:<origin.name>`; a plugin record without a name is just `plugin`
        return TurnKind.MESSAGE, f"plugin:{name}" if name else "plugin"
    if origin_kind in _PEER_ORIGINS:
        return TurnKind.MESSAGE, origin_kind
    if "<mc-message" in text or "<teammate-message" in text or head.startswith("Another Claude session sent"):
        return TurnKind.MESSAGE, "peer"
    if source == "sdk":
        return TurnKind.MESSAGE, "sdk"
    if source == "system":
        return TurnKind.MESSAGE, "system"
    if command:
        return TurnKind.HUMAN, "slash"
    if origin_kind == "human" or source in ("typed", "queued"):
        return TurnKind.HUMAN, "queued" if source == "queued" else "typed"
    if rec.get("entrypoint") == "sdk-cli" and source is None and not origin:
        return TurnKind.MESSAGE, "sdk"
    # FALLBACK-OK: DM-20 last Claude rows ("no promptSource and no origin (older harness)" read as cli): a
    # shape no row names (entrypoint absent or other) is treated as a typed ask
    return TurnKind.HUMAN, "typed"


def turn_label(rec: dict[str, Any], text: str, body: str) -> str | None:
    """DM-21 / DM-23 label: command + args, mc-message body, plugin body, else the envelope body."""
    command = _COMMAND.search(text)
    if command:
        args = _ARGS.search(text)
        return clip(f"{command[1].strip()} {args[1].strip() if args else ''}", LABEL_MAX)
    mc = _MC_MESSAGE.search(text)
    if mc:
        return clip(mc[1], LABEL_MAX)
    if (rec.get("origin") or {}).get("kind") == "plugin":  # FALLBACK-OK: DM-20 origin is optional
        return clip(envelope_body(_PLUGIN_PREFIX.sub("", text, count=1)), LABEL_MAX)
    return clip(body, LABEL_MAX)


def _container(ctx: Ctx, kind: Kind, key: str, parent: str | None, src: str, **kw: Any) -> Op:
    return Op(
        op_id=ctx.op_id(kind, key),
        kind=kind,
        provider=PROVIDER,
        session_id=ctx.sid,
        src=src,
        parent=parent,
        project=ctx.project,
        account=ACCOUNT,
        id_prov=IdProv.DERIVED if kind == Kind.TURN else IdProv.NATURAL,
        **kw,
    )


def _extend(spans: dict[str, list[int]], op_id: str | None, ts: int | None) -> None:
    if op_id is None or ts is None:
        return
    span = spans.get(op_id)
    if span is None:
        spans[op_id] = [ts, ts]
    else:
        span[0], span[1] = min(span[0], ts), max(span[1], ts)


def build(ctx: Ctx, cwd_present: bool) -> Tree:
    t0 = ctx.transcripts[0]
    session = _container(
        ctx,
        Kind.SESSION,
        "-",
        None,
        ctx.src(0, t0.lines[0] if t0.lines else 1),
        cwd=ctx.project if cwd_present else None,
        spawn_depth=0,
    )
    containers: list[Op] = [session]
    arrivals: list[Arrival] = []
    turn_of: dict[tuple[int, int], str] = {}
    spans: dict[str, list[int]] = {}
    custom: str | None = None
    generated: str | None = None
    summary: str | None = None
    for ti, t in enumerate(ctx.transcripts):
        aid = t.agent_id
        agent_type = t.meta.get("agentType")
        turn: Op | None = None
        engagement: Op | None = None
        ordinal = engagement_ix = 0
        for li, line in enumerate(t.lines):
            r = line.rec
            if aid is None and r.get("type") == "custom-title" and r.get("customTitle"):
                custom = r["customTitle"]
            elif aid is None and r.get("type") == "ai-title" and r.get("aiTitle"):
                generated = r["aiTitle"]
            elif aid is None and r.get("type") == "summary" and r.get("summary") and summary is None:
                summary = r["summary"]
            if aid is None and session.entrypoint is None and r.get("entrypoint"):
                session.entrypoint = r["entrypoint"]
            if session.git_branch is None and r.get("gitBranch") not in (None, "", "HEAD"):
                session.git_branch = r["gitBranch"]
            if session.harness_version is None and r.get("version"):
                session.harness_version = r["version"]
            ts = ts_ms(r.get("timestamp"))
            if aid is None and session.first_line_ms is None and ts is not None:
                session.first_line_ms = ts  # D23: physical first, not the minimum
            boundary = is_boundary(r, aid, turn is None)
            if turn is None or boundary:
                text = arrival_text(r) if boundary else ""
                body = envelope_body(text)
                if aid:
                    engagement = _container(
                        ctx,
                        Kind.ENGAGEMENT,
                        f"{aid}#{engagement_ix}",
                        session.op_id,
                        ctx.src(ti, line),
                        caused_by_basis=LinkBasis.NONE,
                        agent_id=aid,
                        engagement_ix=engagement_ix,
                        agent_type=agent_type,
                        agent_name=t.meta.get("name"),
                        spawn_depth=t.spawn_depth,
                        is_fork=t.is_fork,
                        start_ms=ts,
                        label=clip(agent_type or aid, LABEL_MAX),
                    )
                    containers.append(engagement)
                    arrivals.append(Arrival(engagement, ti, body_hash(body)))
                    engagement_ix += 1
                kind, origin = (TurnKind.MESSAGE, "agent") if aid else classify(r, text, t.lines, li)
                turn = _container(
                    ctx,
                    Kind.TURN,
                    f"{aid or 'main'}#{ordinal}",
                    engagement.op_id if engagement else session.op_id,
                    ctx.src(ti, line),
                    agent_id=aid,
                    agent_type=agent_type,
                    spawn_depth=t.spawn_depth,
                    is_fork=t.is_fork if aid else None,
                    cwd=r.get("cwd"),
                    start_ms=ts,
                    label=turn_label(r, text, body),
                    turn_kind=kind,
                    turn_origin=origin,
                )
                containers.append(turn)
                ordinal += 1
            turn_of[(ti, line.n)] = turn.op_id
            _extend(spans, turn.op_id, ts)
            _extend(spans, engagement.op_id if engagement else None, ts)
            if aid is None:
                _extend(spans, session.op_id, ts)
    first_turn = next((c for c in containers if c.kind == Kind.TURN and c.label), None)
    if custom:
        session.title, session.title_source = custom, AdapterTitleSource.RENAME
    elif generated or summary:
        session.title, session.title_source = generated or summary, AdapterTitleSource.GENERATED
    session.label = clip(session.title, LABEL_MAX) or (first_turn.label if first_turn else None) or ctx.sid
    return Tree(session, containers, arrivals, turn_of, spans)


def link(ctx: Ctx, tree: Tree, sends: list[Send]) -> None:
    for arrival in tree.arrivals:
        eg = arrival.engagement
        aid = eg.agent_id
        meta = ctx.transcripts[arrival.ti].meta
        exact = []
        if eg.engagement_ix == 0:
            spawn_id = meta.get("toolUseId")
            exact = [s for s in sends if s.key == spawn_id or s.result_agent == aid]
        candidates = exact or [
            s
            for s in sends
            if arrival.body_hash and s.body_hash == arrival.body_hash and s.op.agent_id != aid
        ]
        if len(candidates) > 1:
            candidates = latest_preceding(candidates, eg.start_ms, lambda s: s.op.start_ms)
        if len(candidates) == 1:
            eg.parent = eg.caused_by = candidates[0].op.op_id
            eg.caused_by_basis = LinkBasis.ID if exact else LinkBasis.CONTENT_HASH


def finalize(ctx: Ctx, tree: Tree, leaves: list[Op]) -> tuple[list[Op], int]:
    """Returns (ops ordered by depth, number of engagements moved by the cycle rule)."""
    ops = tree.containers + leaves
    by_id = {o.op_id: o for o in ops}
    if len(by_id) != len(ops):
        counts = Counter(o.op_id for o in ops)
        raise BundleRejected("duplicate op_id", [k for k, n in counts.items() if n > 1])
    dangling = [o.op_id for o in ops if o.parent is not None and o.parent not in by_id]
    if dangling:
        raise BundleRejected("parent outside the bundle", dangling)
    engagements = [o for o in tree.containers if o.kind == Kind.ENGAGEMENT]
    moved = apply_cycle_rule(engagements, by_id, tree.session.op_id)
    children: dict[str, list[Op]] = defaultdict(list)
    for o in ops:
        if o.parent is not None:
            children[o.parent].append(o)
    order: list[Op] = []
    tree.session.depth = 0
    stack = [tree.session]
    while stack:  # pre-order; a parent cycle leaves its ops unreached
        op = stack.pop()
        order.append(op)
        kids = children[op.op_id]
        for seq, child in enumerate(kids):
            child.depth, child.seq = op.depth + 1, seq
        stack.extend(reversed(kids))  # siblings leave the stack in creation order
    if len(order) != len(ops):
        reached = {o.op_id for o in order}
        raise BundleRejected("cycle after the §4.5 rule", [o.op_id for o in ops if o.op_id not in reached])
    deep = [o.op_id for o in order if o.depth > MAX_DEPTH]
    if deep:
        raise BundleRejected(f"depth > {MAX_DEPTH}", deep)
    for op in reversed(order):  # children before parents
        if op.kind in (Kind.LLM_CALL, Kind.TOOL_CALL):
            continue
        span = tree.spans.get(op.op_id)
        starts = [c.start_ms for c in children[op.op_id] if c.start_ms is not None]
        ends = [c.end_ms for c in children[op.op_id] if c.end_ms is not None]
        if span:
            starts.append(span[0])
            ends.append(span[1])
        op.start_ms = min(starts) if starts else None
        op.end_ms = max(ends) if ends else None
        op.time_prov = Prov.DERIVED if starts and ends else Prov.MISSING
        op.segments = [list(span)] if span else []
    order.sort(key=lambda o: o.depth)
    return order, moved
