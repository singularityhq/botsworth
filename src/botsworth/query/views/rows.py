"""CONTRACT: row builders for `CallRow`, `ToolRow`, `AgentRow` and `GapRow` (owner Q2b).

No public view function. `turn.py` builds the turn blade from these; detail, find and use-case
views reuse them.
Spec ids: QRY-31, QRY-32, QRY-33, QRY-34, QRY-37; DM-36, DM-45, DM-46.

GUARANTEES:
- Rows come from llm_calls and tool_calls, not from steps (DM-46). A call's `steps` are its own think, say and
  answer steps; a tool row's step is the first step of the tool_call (steps whose `op_id` is the call or
  one of its tool_calls make up the call's content, DM-46).
- `tool_status(row, step)` is QRY-37: `exit:<n>` from `exit_code`, else `interrupted`, else the step outcome
  when it is `error`, `interrupted` or `exit:<n>`, else `ok` when the outcome is `ok`, else None (never `ok`
  by default). `failed` = `is_error`, an exit code other than 0, `interrupted`, or a step outcome `exit:n`
  with n != 0.
- `Forest` holds the session's engagements. An engagement's cause is its `caused_by` tool_call (DM-45); its
  host is the engagement that contains the cause in the op tree (nearest, by `pre`/`post`, DM-15). A leaf's
  walk to its turn passes through its nearest engagement, then that engagement's host, and so on. One
  agent row per causing tool_call C groups the engagements with `caused_by` = C (QRY-34, DM-36). A `+cycle`
  engagement that sits under an earlier spawn call in the tree but is caused by a later SendMessage belongs
  to the row of its cause, not to the row of its tree parent.
- An agent row's cost, tokens and call counts are the sums over owned leaves whose walk passes through one of
  its engagements, nested agents included (`scan_turn`, aggregated by the cause at every hop).
- `rows_as_ran(conn, turn_rowid, depth)` is the one ordering: the turn's depth-0 rows by start time (a gap
  row before a call before a tool or agent row at the same start, then tree order); with `depth` > 0 an agent
  row is followed by its own rows, to that nesting depth. Row `depth` is the nesting level, capped at 3.
- `src` and `command_src` are content references built with `refs.uri` (DM-8, D42).
EXPECTS: schema v10 filled by B3a/B3b (`attr_turn_rowid`, `pre`/`post`, `skill_load`, `stretches`); a
  connection with `row_factory = sqlite3.Row`.
FAILURE BEHAVIOR: an owned llm_call or tool_call without `start_ms`, or a `caused_by` cycle in the engagement
  walk (more than 32 hops, D18), raises AssertionError.
DOES NOT: read source files, write, or build SQL in `http/` or `cli/`.
"""

from __future__ import annotations

import re
import sqlite3
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from botsworth.query import wire
from botsworth.query.views.common import money
from botsworth.query.views.session import MAX_DEPTH
from botsworth.query.views.session import gap_row as stretch_gap_row
from botsworth.query.views.stretches import stretches_for
from botsworth.store import refs
from botsworth.store.intervals import union_ms

MAX_HOPS = 32  # D18
_CALL_VERBS = ("think", "say", "answer")
_EXIT = re.compile(r"exit:(-?\d+)")
_CHUNK = 500

# an `ops` row with its content reference (DM-8); `refs.uri` is the Python formatter, `src_sql` the same rule
OP_SELECT = (
    f"SELECT o.*, {refs.src_sql('o', 'sf')} AS src FROM ops o JOIN src_files sf ON sf.rowid = o.src_file"
)

AnyRow = wire.AnyRow


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _chunks(ids: Sequence[int]) -> Iterable[Sequence[int]]:
    for i in range(0, len(ids), _CHUNK):
        yield ids[i : i + _CHUNK]


# ── QRY-37 ───────────────────────────────────────────────────────────────────


def tool_status(row: Any, step: Any) -> tuple[str | None, bool]:
    """`(status, failed)` of a tool_call `row` (`exit_code`, `interrupted`, `is_error`) and its `step`
    (`outcome`; None when it has no step). QRY-37: the order of the five cases is the spec's."""
    outcome = step["outcome"] if step is not None else None
    m = _EXIT.fullmatch(outcome) if outcome is not None else None
    step_exit = int(m.group(1)) if m else None
    exit_code = row["exit_code"]
    if exit_code is not None:
        status: str | None = f"exit:{exit_code}"
    elif row["interrupted"]:
        status = "interrupted"
    elif outcome in ("error", "interrupted") or step_exit is not None:
        status = outcome
    elif outcome == "ok":
        status = "ok"
    else:
        # FALLBACK-OK: interfaces.md QRY-37 item 5 (null otherwise; never default to ok)
        status = None
    failed = (
        bool(row["is_error"])
        or (exit_code is not None and exit_code != 0)
        or bool(row["interrupted"])
        or (step_exit is not None and step_exit != 0)
    )
    return status, failed


# ── steps ────────────────────────────────────────────────────────────────────


def steps_by_op(conn: sqlite3.Connection, op_rowids: Sequence[int]) -> dict[int, list[sqlite3.Row]]:
    """The steps whose `op_rowid` is in `op_rowids`, by op, in ordinal order, each with `turn_id`."""
    out: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for part in _chunks(list(op_rowids)):
        for s in conn.execute(
            f"SELECT s.*, t.op_id AS turn_id FROM steps s JOIN ops t ON t.rowid = s.turn_rowid "
            f"WHERE s.op_rowid IN ({_marks(len(part))}) ORDER BY s.op_rowid, s.ordinal",
            list(part),
        ):
            out[s["op_rowid"]].append(s)
    return out


def step_id(step: sqlite3.Row) -> str:
    """`<turn op_id>#<ordinal>` (DM-8)."""
    return f"{step['turn_id']}#{step['ordinal']}"


def _text(value: str | None) -> str:
    # FALLBACK-OK: interfaces.md API-22 + ui/turn.md TRN-31 (summary and snippet are strings; a step with no
    # stored text, such as an empty think step, falls to the "Thinking only" name)
    return value if value is not None else ""


# ── call, tool, gap ──────────────────────────────────────────────────────────


def _time_prov(row: sqlite3.Row) -> str:
    # FALLBACK-OK: interfaces.md §3.3 (Provenance includes `missing`; an op with no duration has no basis)
    return row["time_prov"] if row["time_prov"] is not None else "missing"


def call_row(row: sqlite3.Row, steps: Sequence[sqlite3.Row], depth: int) -> wire.CallRow:
    """A `CallRow` (QRY-31, TRN-31) for an owned llm_call `row`; `steps` are the steps of the call itself."""
    assert row["start_ms"] is not None and row["end_ms"] is not None, f"call {row['op_id']} has no times"
    own = [s for s in steps if s["verb"] in _CALL_VERBS]
    unpriced = row["cost_usd"] is None
    m = money(row["cost_usd"], unpriced, int(unpriced))
    tok = wire.Tok(
        {
            "in": row["in_tok"],
            "cache_read": row["cache_read_tok"],
            "cache_write": row["cache_write_tok"],
            "out": row["out_tok"],
            "reasoning": row["reasoning_tok"],
            "total": row["tok_total"],
        }
    )
    return wire.CallRow(
        m.cost_usd,
        m.cost_lower_bound,
        m.n_unpriced,
        id=row["op_id"],
        depth=depth,
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        dur_ms=row["dur_ms"],
        time_prov=_time_prov(row),
        model=row["model"],
        tok=tok,
        ttft_ms=row["ttft_ms"],
        stop_reason=row["stop_reason"],
        error_kind=row["error_kind"],
        is_final=any(s["verb"] == "answer" for s in own),
        steps=[wire.CallStep(step_id(s), s["verb"], _text(s["summary"]), _text(s["snippet"])) for s in own],
    )


def tool_row(
    row: sqlite3.Row, steps: Sequence[sqlite3.Row], depth: int, skill_via: str | None = None
) -> wire.ToolRow:
    """A `ToolRow` (QRY-31, TRN-32) for an owned tool_call `row` (an `OP_SELECT` row) and its `steps`."""
    assert row["start_ms"] is not None, f"tool_call {row['op_id']} has no start"
    step = steps[0] if steps else None
    status, failed = tool_status(row, step)
    if step is None:
        # FALLBACK-OK: interfaces.md API-22 (`ToolRow.step_id` is nullable: a tool_call may have no step)
        sid, verb, summary, target, skill = None, "other", row["tool_name"] or "", "", None
    else:
        sid, verb, summary, target, skill = (
            step_id(step),
            step["verb"],
            _text(step["summary"]),
            _text(step["target"]),
            step["skill_load"],
        )
    return wire.ToolRow(
        id=row["op_id"],
        depth=depth,
        step_id=sid,
        tool_name=row["tool_name"],
        verb=verb,
        summary=summary,
        target=target,
        command=row["command"],
        command_src=row["src"] if row["command"] is not None else None,
        file_path=row["file_path"],
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        dur_ms=row["dur_ms"],
        time_prov=_time_prov(row),
        status=status,
        failed=failed,
        exit_code=row["exit_code"],
        is_error=bool(row["is_error"]),
        interrupted=bool(row["interrupted"]),
        skill_load=skill,
        skill_via=skill_via if skill is not None else None,
        user_wait=row["user_wait"],
        max_silence_ms=row["max_silence_ms"],
        result_bytes=row["result_bytes"],
        src=row["src"],
    )


def gap_row(stretch: wire.Stretch) -> wire.GapRow:
    """A `GapRow` for a stretch (QRY-41); the id is `gap-<start_ms>`."""
    return stretch_gap_row(stretch)


def skill_via_by_step(conn: sqlite3.Connection, root_rowid: int) -> dict[str, str]:
    """`{step_id: via}` of the session's `skill_invocation` events that name a step (DM-30)."""
    return dict(
        conn.execute(
            """
            SELECT json_extract(fields, '$.step_id'), json_extract(fields, '$.via') FROM events
            WHERE root_rowid = ? AND kind = 'skill_invocation' AND copied = 0
              AND json_extract(fields, '$.step_id') IS NOT NULL
            """,
            (root_rowid,),
        ).fetchall()
    )


# ── engagements (DM-36, DM-45) ───────────────────────────────────────────────


class Forest:
    """The engagements of one session, their hosts and their agent-row groups."""

    def __init__(self, conn: sqlite3.Connection, root_rowid: int) -> None:
        self.rows: list[sqlite3.Row] = conn.execute(
            """
            SELECT e.rowid, e.op_id, e.pre, e.post, e.caused_by_rowid, e.agent_id, e.agent_name, e.agent_type,
                   e.start_ms, e.end_ms, e.wall_ms, c.pre AS cause_pre
            FROM ops e LEFT JOIN ops c ON c.rowid = e.caused_by_rowid
            WHERE e.root_rowid = ? AND e.kind = 'engagement' ORDER BY e.pre
            """,
            (root_rowid,),
        ).fetchall()
        self.by_rowid = {r["rowid"]: r for r in self.rows}
        self._pres = [r["pre"] for r in self.rows]
        self._tree_parent: list[int | None] = []
        stack: list[int] = []
        for i, r in enumerate(self.rows):
            while stack and self.rows[stack[-1]]["post"] < r["pre"]:
                stack.pop()
            self._tree_parent.append(stack[-1] if stack else None)
            stack.append(i)
        self.host: dict[int, int | None] = {}
        for r in self.rows:
            h = self.containing(r["cause_pre"]) if r["cause_pre"] is not None else None
            self.host[r["rowid"]] = h["rowid"] if h is not None else None
        self.groups: dict[int, list[sqlite3.Row]] = defaultdict(list)
        for r in sorted(self.rows, key=lambda r: (r["start_ms"] is None, r["start_ms"], r["op_id"])):
            if r["caused_by_rowid"] is not None:
                self.groups[r["caused_by_rowid"]].append(r)

    def containing(self, pre: int) -> sqlite3.Row | None:
        """The nearest engagement whose subtree holds the op at pre-order position `pre`."""
        i: int | None = bisect_right(self._pres, pre) - 1
        while i is not None and i >= 0 and self.rows[i]["post"] < pre:
            i = self._tree_parent[i]
        return self.rows[i] if i is not None and i >= 0 else None

    def chain_causes(self, engagement_rowid: int) -> list[int]:
        """The causing tool_calls met on the walk from an engagement up to its turn (DM-45), nearest first."""
        out: list[int] = []
        e: int | None = engagement_rowid
        while e is not None:
            cause = self.by_rowid[e]["caused_by_rowid"]
            if cause is None:
                return []  # DM-45: an engagement with `caused_by` NULL ends the walk with no turn
            out.append(cause)
            assert len(out) <= MAX_HOPS, f"engagement walk from {engagement_rowid} exceeds {MAX_HOPS} hops"
            e = self.host[e]
        return out

    def level(self, cause_rowid: int) -> int:
        """Engagement ancestors of the causing tool_call (0 = a main-thread call)."""
        first = self.groups[cause_rowid][0]
        return len(self.chain_causes(first["rowid"])) - 1


# ── aggregates of one turn (QRY-34) ──────────────────────────────────────────


@dataclass
class Agg:
    """Sums over the owned leaves counted for one agent row (or the main thread)."""

    n_llm: int = 0
    n_priced: int = 0
    cost: float = 0.0
    tok: int = 0
    n_tok: int = 0
    n_tools: int = 0
    n_failed: int = 0
    direct: int = 0
    models: dict[str, float] = field(default_factory=dict)
    rng_n: int = 0
    rng_priced: int = 0
    rng_cost: float = 0.0
    rng_tok: int = 0
    rng_n_tok: int = 0

    def add(self, leaf: sqlite3.Row, failed: bool, rng: tuple[int, int] | None) -> None:
        if leaf["kind"] == "tool_call":
            self.n_tools += 1
            self.n_failed += failed
            return
        self.n_llm += 1
        if leaf["cost_usd"] is not None:
            self.n_priced += 1
            self.cost += leaf["cost_usd"]
        if leaf["tok_total"] is not None:
            self.n_tok += 1
            self.tok += leaf["tok_total"]
        if leaf["model"] is not None:
            self.models[leaf["model"]] = self.models.get(leaf["model"], 0.0) + (leaf["cost_usd"] or 0.0)
        if rng is not None and rng[0] <= leaf["start_ms"] <= rng[1]:
            self.rng_n += 1
            if leaf["cost_usd"] is not None:
                self.rng_priced += 1
                self.rng_cost += leaf["cost_usd"]
            if leaf["tok_total"] is not None:
                self.rng_n_tok += 1
                self.rng_tok += leaf["tok_total"]

    @property
    def money(self) -> wire.Money:
        return money(
            self.cost if self.n_llm else None, self.n_priced < self.n_llm, self.n_llm - self.n_priced
        )

    @property
    def tok_total(self) -> int | None:
        return self.tok if self.n_tok else None

    @property
    def rng_money(self) -> wire.Money:
        return money(
            self.rng_cost if self.rng_n else None, self.rng_priced < self.rng_n, self.rng_n - self.rng_priced
        )


@dataclass
class TurnScan:
    """The leaves attributed to one turn (DM-45): the main thread, all agents, and each agent row."""

    main: Agg = field(default_factory=Agg)
    agents: Agg = field(default_factory=Agg)
    by_cause: dict[int, Agg] = field(default_factory=lambda: defaultdict(Agg))


def scan_turn(
    conn: sqlite3.Connection, forest: Forest, turn_rowid: int, rng: tuple[int, int] | None = None
) -> TurnScan:
    """One pass over the owned leaves with `attr_turn_rowid` = the turn (`ops_attr_turn`). A leaf with no
    engagement above it is main-thread; any other adds to its engagement's cause and to every host's cause."""
    out = TurnScan()
    for leaf in conn.execute(
        """
        SELECT o.kind, o.pre, o.cost_usd, o.tok_total, o.model, o.start_ms, o.agent_id, o.is_error,
               o.exit_code, o.interrupted,
               CASE WHEN o.kind = 'tool_call'
                    THEN (SELECT outcome FROM steps s WHERE s.op_rowid = o.rowid ORDER BY s.ordinal LIMIT 1)
               END AS outcome
        FROM ops o
        WHERE o.attr_turn_rowid = ? AND o.kind IN ('llm_call', 'tool_call') AND o.duplicate_of IS NULL
        """,
        (turn_rowid,),
    ):
        assert leaf["kind"] != "llm_call" or leaf["start_ms"] is not None, "an owned llm_call has no start"
        failed = leaf["kind"] == "tool_call" and tool_status(leaf, leaf)[1]
        e = forest.containing(leaf["pre"])
        if e is None:
            out.main.add(leaf, failed, rng)
            continue
        out.agents.add(leaf, failed, rng)
        causes = forest.chain_causes(e["rowid"])
        assert causes, f"leaf under unattributed engagement {e['op_id']} carries attr_turn_rowid"
        out.by_cause[causes[0]].direct += 1
        for c in causes:
            out.by_cause[c].add(leaf, failed, rng)
    return out


# ── agent rows ───────────────────────────────────────────────────────────────


def _agent_row(
    cause: sqlite3.Row,
    cause_step: sqlite3.Row | None,
    engagements: Sequence[sqlite3.Row],
    agg: Agg,
    depth: int,
) -> wire.AgentRow:
    for e in engagements:
        assert e["start_ms"] is not None and e["end_ms"] is not None, f"engagement {e['op_id']} has no run"
        assert e["wall_ms"] is not None, f"engagement {e['op_id']} has no wall_ms (DM-36)"
    first = engagements[0]
    runs = [
        wire.AgentRowRun(
            e["op_id"],
            e["agent_name"],
            e["start_ms"],
            e["end_ms"],
            e["wall_ms"],
        )
        for e in engagements
    ]
    m = agg.money
    # FALLBACK-OK: design §4.2 (agent type is optional; the loader names an engagement agent_type or
    # agent_name or "agent"), ui/turn.md TRN-33 (name: the agent name, else its type)
    names = [e["agent_name"] or e["agent_type"] or "agent" for e in engagements]
    return wire.AgentRow(
        m.cost_usd,
        m.cost_lower_bound,
        m.n_unpriced,
        id=first["op_id"],
        depth=min(depth, MAX_DEPTH),
        caused_by=cause["op_id"],
        mode="messaged" if cause["tool_name"] == "SendMessage" else "started",
        description=cause_step["summary"] if cause_step is not None else None,
        agent_names=names,
        agent_type=next((e["agent_type"] for e in engagements if e["agent_type"] is not None), None),
        tok_total=agg.tok_total,
        n_llm_calls=agg.n_llm,
        n_tool_calls=agg.n_tools,
        n_errors=agg.n_failed,
        models=[mod for mod, _ in sorted(agg.models.items(), key=lambda kv: (-kv[1], kv[0]))],
        run_ms=union_ms((e["start_ms"], e["end_ms"]) for e in engagements),
        n_rows=agg.direct,
        runs=runs,
    )


@dataclass
class _Entry:
    key: tuple
    kind: str  # call | tool | agent | gap
    row: Any  # an OP_SELECT row, or a wire.Stretch for a gap


class RowBuilder:
    """Builds ordered rows of one session from a `TurnScan` (the aggregates of the turn they belong to)."""

    def __init__(self, conn: sqlite3.Connection, root_rowid: int, forest: Forest, scan: TurnScan) -> None:
        self.conn, self.root, self.forest, self.scan = conn, root_rowid, forest, scan
        self._skill_via: dict[str, str] | None = None

    # entries

    def turn_entries(self, turn_rowid: int, gaps: Sequence[wire.Stretch] = ()) -> list[_Entry]:
        """The depth-0 rows of a main turn: its calls, their tool_calls, and the gaps given."""
        calls = self.conn.execute(
            f"{OP_SELECT} WHERE o.parent_rowid = ? AND o.kind = 'llm_call' AND o.duplicate_of IS NULL",
            (turn_rowid,),
        ).fetchall()
        tools: list[sqlite3.Row] = []
        for part in _chunks([c["rowid"] for c in calls]):
            tools += self.conn.execute(
                f"{OP_SELECT} WHERE o.parent_rowid IN ({_marks(len(part))}) AND o.kind = 'tool_call' "
                "AND o.duplicate_of IS NULL",
                list(part),
            ).fetchall()
        return self._entries(calls, tools, gaps)

    def group_entries(self, cause_rowid: int) -> list[_Entry]:
        """The direct rows of an agent row: leaves whose nearest engagement is one of its engagements."""
        mine = {e["rowid"] for e in self.forest.groups[cause_rowid]}
        calls: list[sqlite3.Row] = []
        tools: list[sqlite3.Row] = []
        for e in self.forest.groups[cause_rowid]:
            for leaf in self.conn.execute(
                f"{OP_SELECT} WHERE o.root_rowid = ? AND o.pre BETWEEN ? AND ? "
                "AND o.kind IN ('llm_call', 'tool_call') AND o.duplicate_of IS NULL",
                (self.root, e["pre"], e["post"]),
            ):
                near = self.forest.containing(leaf["pre"])
                if near is not None and near["rowid"] in mine:
                    (calls if leaf["kind"] == "llm_call" else tools).append(leaf)
        return self._entries(calls, tools, ())

    def _entries(
        self, calls: Sequence[sqlite3.Row], tools: Sequence[sqlite3.Row], gaps: Sequence[wire.Stretch]
    ) -> list[_Entry]:
        out = [_Entry((g.start_ms, 0, 0), "gap", g) for g in gaps]
        for c in calls:
            assert c["start_ms"] is not None, f"call {c['op_id']} has no start"
            out.append(_Entry((c["start_ms"], 1, c["pre"]), "call", c))
        for t in tools:
            assert t["start_ms"] is not None, f"tool_call {t['op_id']} has no start"
            kind = "agent" if t["rowid"] in self.forest.groups else "tool"
            out.append(_Entry((t["start_ms"], 2, t["pre"]), kind, t))
        out.sort(key=lambda e: e.key)
        return out

    # wire rows

    def build(self, entries: Sequence[_Entry], depth: int, expand: int = 0) -> list[AnyRow]:
        """Wire rows for `entries`, reported at nesting `depth`. With `expand` > 0 an agent row is followed by
        its own rows (one level deeper), down to `expand` levels."""
        ids = [e.row["rowid"] for e in entries if e.kind != "gap"]
        steps = steps_by_op(self.conn, ids)
        if self._skill_via is None and any(s["skill_load"] for ss in steps.values() for s in ss):
            self._skill_via = skill_via_by_step(self.conn, self.root)
        via = self._skill_via or {}
        rows: list[AnyRow] = []
        for e in entries:
            if e.kind == "gap":
                rows.append(gap_row(e.row))
            elif e.kind == "call":
                rows.append(call_row(e.row, steps.get(e.row["rowid"], ()), min(depth, MAX_DEPTH)))
            elif e.kind == "tool":
                st = steps.get(e.row["rowid"], ())
                # FALLBACK-OK: interfaces.md API-22 (`skill_via` is nullable)
                sv = via.get(step_id(st[0])) if st and st[0]["skill_load"] else None
                rows.append(tool_row(e.row, st, min(depth, MAX_DEPTH), sv))
            else:
                cause = e.row
                st = steps.get(cause["rowid"], ())
                agent = _agent_row(
                    cause, st[0] if st else None, self.forest.groups[cause["rowid"]],
                    self.scan.by_cause[cause["rowid"]], min(depth, MAX_DEPTH),
                )  # fmt: skip
                rows.append(agent)
                if expand > 0:
                    rows += self.build(self.group_entries(cause["rowid"]), depth + 1, expand - 1)
        return rows


def agent_rows_for_turn(conn: sqlite3.Connection, turn_rowid: int, root_rowid: int) -> list[wire.AgentRow]:
    """The depth-0 `AgentRow`s of a main turn, in start order (QRY-31, QRY-34)."""
    forest = Forest(conn, root_rowid)
    builder = RowBuilder(conn, root_rowid, forest, scan_turn(conn, forest, turn_rowid))
    return [r for r in builder.build(builder.turn_entries(turn_rowid), 0) if isinstance(r, wire.AgentRow)]


def turn_gaps(conn: sqlite3.Connection, root_rowid: int, turn_op_id: str) -> list[wire.Stretch]:
    """The 60 s or longer stretches that SES-37 attaches to the turn (QRY-31)."""
    return [g for g in stretches_for(conn, root_rowid) if g.turn == turn_op_id]


def rows_as_ran(
    conn: sqlite3.Connection,
    turn_rowid: int,
    depth: int = 0,
    *,
    stretches: Sequence[wire.Stretch] | None = None,
) -> list[AnyRow]:
    """The turn's rows as they ran (TRN-13): depth 0 only, or with each agent row followed by its rows down to
    nesting level `depth`. `stretches` are the session's stretches (QRY-41); loaded when not given."""
    turn = conn.execute("SELECT op_id, root_rowid FROM ops WHERE rowid = ?", (turn_rowid,)).fetchone()
    root = turn["root_rowid"]
    forest = Forest(conn, root)
    builder = RowBuilder(conn, root, forest, scan_turn(conn, forest, turn_rowid))
    gaps = stretches if stretches is not None else stretches_for(conn, root)
    return builder.build(
        builder.turn_entries(turn_rowid, [g for g in gaps if g.turn == turn["op_id"]]), 0, depth
    )
