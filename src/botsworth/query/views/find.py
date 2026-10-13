"""CONTRACT: session finders (owner Q2c). `resolve` (QRY-38a) lives in session.py (Q2a).

`find(session, kind, measure, text, limit)` returns one hit set per call, in session order (QRY-38, API-25).
Spec ids: QRY-38, QRY-37; API-25; SES-32, SES-51 to SES-56; D38 M4, D42.

GUARANTEES:
- Session order: the session's numbered turns by time (`session.time_order`), harness turns skipped, then each
  turn's rows as they ran (Q2b `RowBuilder`, gap rows included), with an agent's own rows after its agent
  row, to any depth. A slash turn hit (`skills`) comes before its turn's rows.
- One hit set feeds `total`, `per_turn` and `hits`: `total` counts every hit, `hits` holds the first `limit`,
  `per_turn` maps a turn op_id to its hit count.
- `biggest`: depth-0 rows with a `measure` value above 0, by value descending (ties keep session order).
  `cost` and `tokens` cover call and agent rows; `time` covers call (`dur_ms`), tool (`dur_ms`), agent
  (`run_ms`) and gap rows, and leaves out tools with `waits_on_user` or `max_silence_ms >= 300000`.
  `value` is that figure.
- `waits`: gap rows of every state and label with `dur_ms >= 120000`; `value` is `dur_ms`.
- `failures`: tool rows with `failed` (QRY-37), any depth. `skills`: tool rows with a `skill_load`, plus the
  turn of a slash `skill_invocation` event when the turn has no step with a `skill_load`. `agents`: agent
  rows, any depth. `value` is null for these three.
- `search`: call, tool and agent rows (gap rows have no text of their own) for which every
  whitespace-separated term of `text` is a case-insensitive (ASCII) substring of at least one stored field:
  the row name, the target, `tool_name`, `command` (the stored 160-char preview, D42), `file_path`, the step
  `summary` and `snippet`, the agent name and description, and the label of the row's turn. Nothing is read
  from a source file.
- `label` is the row name (TRN-31 to TRN-33): a call's first line of said or answer text, else of thinking,
  else `Tool request: <tool names>`, else `Thinking only`; a tool's skill name, step summary or tool name; an
  agent's names joined by `, `; a gap's label (`you`, `limit`, `none`, `agents`, ...); a slash turn's label.
EXPECTS: schema v7 filled by B3a/B3b; a connection with `row_factory = sqlite3.Row`.
FAILURE BEHAVIOR: unknown session -> NotFound; ambiguous or short session key -> QueryError (QRY-6); a `kind`
  or `measure` outside the lists, `limit` outside 1-10000, or `search` with no term -> QueryError.
DOES NOT: read source files, write, or build SQL in `http/` or `cli/`.

SELECT FIRST (FIND-PERF, API-50): `failures`, `skills`, `agents` and `search` decide from the entries of a
turn (`RowBuilder.turn_entries`, `group_entries`) and their steps which rows hit, and build a wire row only
for a hit. The decision reads the same stored fields as the row it would build (`tool_status`, a step's
`skill_load`, the texts `_search_pieces` lists), so the hits equal those of a walk over fully built rows
(`tests/unit/test_find_parity.py`). `biggest` and `waits` need the figures of every row and build them all.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from typing import Any

from botsworth.query import Db, QueryError, _conn, shapes, wire
from botsworth.query.views import rows as R
from botsworth.query.views.common import lookup_session
from botsworth.query.views.session import MAX_DEPTH, time_order
from botsworth.query.views.stretches import display_id, stretches_for

KINDS = ("biggest", "waits", "failures", "skills", "agents", "search")
MEASURES = ("cost", "time", "tokens")
MAX_LIMIT = 10_000  # API-25
WAIT_FLOOR_MS = 120_000  # SES-53
SILENCE_MS = 300_000  # SES-52
_ASCII_UPPER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}  # SQLite `lower` folds ASCII only (QRY-7)

_TURNS = """
SELECT t.* FROM sessions s JOIN ops t ON t.parent_rowid = s.op_rowid AND t.kind = 'turn' WHERE s.rowid = ?
"""
_SLASH_TURNS = """
SELECT DISTINCT t.rowid FROM events ev JOIN ops t ON t.op_id = ev.op_id
WHERE ev.root_rowid = ? AND ev.kind = 'skill_invocation' AND ev.copied = 0
  AND json_extract(ev.fields, '$.via') = 'slash' AND t.kind = 'turn'
  AND NOT EXISTS (SELECT 1 FROM steps s WHERE s.turn_rowid = t.rowid AND s.skill_load IS NOT NULL)
"""
# The `ops` columns the finders decide from. `ops` has 124 columns and the full `OP_SELECT` row of every
# leaf of a large session costs ~5x this; a hit is refetched with `OP_SELECT` (`_with_full_rows`).
_LEAF_COLS = (
    "o.rowid, o.op_id, o.kind, o.pre, o.start_ms, o.parent_rowid, o.tool_name, o.command, o.file_path, "
    "o.exit_code, o.interrupted, o.is_error"
)
_LEAF_WHERE = "o.kind IN ('llm_call', 'tool_call') AND o.duplicate_of IS NULL"


class _Item:
    """One visited row: the wire row, its turn, and what the finders read from it."""

    __slots__ = ("depth", "entry", "name", "row", "steps", "tool_names", "turn")

    def __init__(self, turn: sqlite3.Row, entry: R._Entry, row: R.AnyRow, steps: Sequence[Any]) -> None:
        self.turn, self.entry, self.row, self.steps = turn, entry, row, steps
        # a gap row sits in the turn's list, so it has no depth of its own
        self.depth = row.depth if not isinstance(row, wire.GapRow) else 0
        self.tool_names: list[str] = []
        self.name = ""


def _first_line(text: str) -> str:
    # FALLBACK-OK: ui/turn.md TRN-31 (a step with no text gives no name; the next rule of the name applies)
    lines = text.strip().splitlines()
    return lines[0].strip() if lines else ""


def _call_name(steps: Sequence[tuple[str, str]], tool_names: Sequence[str]) -> str:
    """TRN-31: said or answer text, else thinking, else the tools asked for, else "Thinking only".
    `steps` are the `(verb, snippet)` of the call's own steps."""
    for verbs in (("say", "answer"), ("think",)):
        for verb, snippet in steps:
            if verb in verbs and (line := _first_line(snippet)):
                return line
    if tool_names:
        return "Tool request: " + ", ".join(tool_names)
    return "Thinking only"  # FALLBACK-OK: ui/turn.md TRN-31 (the last name in the call-row rule)


def _name(item: _Item) -> str:
    row = item.row
    if isinstance(row, wire.CallRow):
        return _call_name([(st.verb, st.snippet) for st in row.steps], item.tool_names)
    if isinstance(row, wire.ToolRow):
        # FALLBACK-OK: ui/turn.md TRN-32 (name: the skill, else the step summary, else the tool name)
        return row.skill_load or row.summary or row.tool_name or ""
    if isinstance(row, wire.AgentRow):
        return ", ".join(row.agent_names)
    return row.label  # a gap row


def _ascii_lower(text: str) -> str:
    return text.translate(_ASCII_UPPER)


def _walk(
    conn: sqlite3.Connection,
    builder: R.RowBuilder,
    turn: sqlite3.Row,
    entries: Sequence[R._Entry],
    depth: int,
) -> Iterator[_Item]:
    """The depth-0 rows of `entries` as they ran (no agent rows' own rows)."""
    rows = builder.build(entries, depth)
    assert len(rows) == len(entries), "RowBuilder.build must give one row per entry"
    steps = R.steps_by_op(conn, [e.row["rowid"] for e in entries if e.kind != "gap"])
    tool_names: dict[int, list[str]] = defaultdict(list)
    for e in entries:
        if e.kind in ("tool", "agent") and e.row["tool_name"]:
            tool_names[e.row["parent_rowid"]].append(e.row["tool_name"])
    for e, row in zip(entries, rows, strict=True):
        item = _Item(turn, e, row, steps.get(e.row["rowid"], ()) if e.kind != "gap" else ())
        if e.kind == "call":
            item.tool_names = tool_names.get(e.row["rowid"], [])
        item.name = _name(item)
        yield item


def _session_items(conn: sqlite3.Connection, root: int) -> Iterator[tuple[sqlite3.Row, _Item]]:
    """`(turn, item)` for every depth-0 row of the numbered turns, in session order."""
    stretches = stretches_for(conn, root)
    forest = R.Forest(conn, root)
    for turn in time_order(conn.execute(_TURNS, (root,)).fetchall()):
        if turn["turn_kind"] == "harness":
            continue  # SES-51
        builder = R.RowBuilder(conn, root, forest, R.scan_turn(conn, forest, turn["rowid"]))
        gaps = [g for g in stretches if g.turn == turn["op_id"]]
        for item in _walk(conn, builder, turn, builder.turn_entries(turn["rowid"], gaps), 0):
            yield turn, item


def _turn_label(turn: sqlite3.Row) -> str:
    # FALLBACK-OK: interfaces.md API-25 + SES-32 (`label` is a string; a turn with no label has no text)
    return turn["label"] if turn["label"] is not None else ""


def _ref(item: _Item) -> wire.RowRef:
    row = item.row
    kind = {"call": "call", "tool": "tool", "agent": "agent", "gap": "gap"}[row.type]
    return wire.RowRef(kind, row.id)


def _hit(item: _Item, value: float | None) -> wire.FindHit:
    return wire.FindHit(
        row=_ref(item),
        turn=item.turn["op_id"],
        display_id=display_id(item.turn),
        depth=item.depth,
        label=item.name,
        value=value,
        start_ms=item.row.runs[0].start_ms if isinstance(item.row, wire.AgentRow) else item.row.start_ms,
    )


def _turn_hit(turn: sqlite3.Row) -> wire.FindHit:
    return wire.FindHit(
        row=wire.RowRef("turn", turn["op_id"]),
        turn=turn["op_id"],
        display_id=display_id(turn),
        depth=0,
        label=_turn_label(turn),
        value=None,
        start_ms=turn["start_ms"],
    )


def _measure_value(row: R.AnyRow, measure: str) -> float | None:
    """The `measure` figure of a depth-0 row, or None when the row does not take part (QRY-38 `biggest`)."""
    if isinstance(row, wire.CallRow):
        return {"cost": row.cost_usd, "tokens": row.tok["total"], "time": row.dur_ms}[measure]
    if isinstance(row, wire.AgentRow):
        return {"cost": row.cost_usd, "tokens": row.tok_total, "time": row.run_ms}[measure]
    if measure != "time":
        return None
    if isinstance(row, wire.ToolRow):
        if row.waits_on_user or (row.max_silence_ms is not None and row.max_silence_ms >= SILENCE_MS):
            return None  # SES-52: these would top every ranking by time
        return row.dur_ms
    return row.dur_ms  # a gap row


def _hits_built(conn: sqlite3.Connection, root: int, kind: str, measure: str) -> list[wire.FindHit]:
    """`biggest` and `waits`: they read a figure of every depth-0 row, so every row is built."""
    hits: list[wire.FindHit] = []
    for _, item in _session_items(conn, root):
        row = item.row
        if kind == "biggest":
            value = _measure_value(row, measure)
            if value is not None and value > 0:
                hits.append(_hit(item, value))
        elif isinstance(row, wire.GapRow) and row.dur_ms >= WAIT_FLOOR_MS:
            hits.append(_hit(item, row.dur_ms))
    if kind == "biggest":
        hits.sort(key=lambda h: -h.value)  # type: ignore[operator]  # a stable sort keeps session order on ties
    return hits


def _fold(text: str) -> str:
    # `lower` equals the ASCII-only fold (QRY-7) on an all-ASCII string; any other string takes the table
    return text.lower() if text.isascii() else _ascii_lower(text)


def _search_pieces(
    e: R._Entry, steps: Sequence[Any], tool_names: Sequence[str], forest: R.Forest
) -> list[str]:
    """The stored text of a call, tool or agent entry (QRY-38 `search`), without the turn label: the texts
    the built row would give, in the order `name` first. Mirrors `_name` and the row fields of `rows.py`."""
    texts = [t for s in steps for t in (s["summary"], s["snippet"]) if t]
    row = e.row
    if e.kind == "call":
        own = [(s["verb"], R._text(s["snippet"])) for s in steps if s["verb"] in R._CALL_VERBS]
        return [_call_name(own, tool_names), *texts]
    if e.kind == "tool":
        first = steps[0] if steps else None
        if first is None:
            summary, target, skill = row["tool_name"] or "", "", None
        else:
            summary, target, skill = R._text(first["summary"]), R._text(first["target"]), first["skill_load"]
        name = skill or summary or row["tool_name"] or ""
        return [name, *texts, *(x for x in (target, row["tool_name"], row["command"], row["file_path"]) if x)]
    engagements = forest.groups[row["rowid"]]
    names = [g["agent_name"] or g["agent_type"] or "agent" for g in engagements]  # as R._agent_row
    description = steps[0]["summary"] if steps else None
    agent_type = next((g["agent_type"] for g in engagements if g["agent_type"] is not None), None)
    pieces = (description, agent_type, row["tool_name"], *names)
    return [", ".join(names), *texts, *(x for x in pieces if x)]


def _chunked(ids: Sequence[int]) -> Iterator[Sequence[int]]:
    for i in range(0, len(ids), R._CHUNK):
        yield ids[i : i + R._CHUNK]


def _turn_entries(conn: sqlite3.Connection, builder: R.RowBuilder, turn_rowid: int) -> list[R._Entry]:
    """`RowBuilder.turn_entries` (no gaps) over the `_LEAF_COLS` of each op."""
    calls = conn.execute(
        f"SELECT {_LEAF_COLS} FROM ops o WHERE o.parent_rowid = ? AND o.kind = 'llm_call' "
        "AND o.duplicate_of IS NULL",
        (turn_rowid,),
    ).fetchall()
    tools: list[sqlite3.Row] = []
    for part in _chunked([c["rowid"] for c in calls]):
        tools += conn.execute(
            f"SELECT {_LEAF_COLS} FROM ops o WHERE o.parent_rowid IN ({R._marks(len(part))}) "
            "AND o.kind = 'tool_call' AND o.duplicate_of IS NULL",
            list(part),
        ).fetchall()
    return builder._entries(calls, tools, ())


def _group_entries(
    conn: sqlite3.Connection, builder: R.RowBuilder, root: int, cause_rowid: int
) -> list[R._Entry]:
    """`RowBuilder.group_entries` over the `_LEAF_COLS` of each op."""
    forest = builder.forest
    mine = {g["rowid"] for g in forest.groups[cause_rowid]}
    calls: list[sqlite3.Row] = []
    tools: list[sqlite3.Row] = []
    for g in forest.groups[cause_rowid]:
        for leaf in conn.execute(
            f"SELECT {_LEAF_COLS} FROM ops o WHERE o.root_rowid = ? AND o.pre BETWEEN ? AND ? "
            f"AND {_LEAF_WHERE}",
            (root, g["pre"], g["post"]),
        ):
            near = forest.containing(leaf["pre"])
            if near is not None and near["rowid"] in mine:
                (calls if leaf["kind"] == "llm_call" else tools).append(leaf)
    return builder._entries(calls, tools, ())


def _with_full_rows(conn: sqlite3.Connection, entries: Sequence[R._Entry]) -> list[R._Entry]:
    """The entries with their `OP_SELECT` row, which `rows.py` builds wire rows from."""
    full: dict[int, sqlite3.Row] = {}
    for part in _chunked([e.row["rowid"] for e in entries]):
        for r in conn.execute(f"{R.OP_SELECT} WHERE o.rowid IN ({R._marks(len(part))})", list(part)):
            full[r["rowid"]] = r
    return [R._Entry(e.key, e.kind, full[e.row["rowid"]]) for e in entries]


def _selected(
    kind: str,
    e: R._Entry,
    steps: Sequence[Any],
    tool_names: Sequence[str],
    forest: R.Forest,
    terms: Sequence[str],
) -> bool:
    """Whether the entry hits, from its stored fields. `terms` are the search terms the turn label lacks."""
    if kind == "agents":
        return e.kind == "agent"
    if kind == "failures":
        return e.kind == "tool" and R.tool_status(e.row, steps[0] if steps else None)[1]
    if kind == "skills":
        return e.kind == "tool" and bool(steps) and steps[0]["skill_load"] is not None
    if not terms:
        return True
    folded = [_fold(p) for p in _search_pieces(e, steps, tool_names, forest)]
    return all(any(t in p for p in folded) for t in terms)


def _hit_item(
    turn: sqlite3.Row,
    e: R._Entry,
    steps: Sequence[Any],
    tool_names: Sequence[str],
    forest: R.Forest,
    depth: int,
) -> _Item:
    """The built row of a hit. An agent row takes empty aggregates: a hit reads only its id, names and run."""
    d = min(depth, MAX_DEPTH)
    if e.kind == "call":
        row: R.AnyRow = R.call_row(e.row, steps, d)
    elif e.kind == "tool":
        row = R.tool_row(e.row, steps, d)
    else:
        row = R._agent_row(e.row, steps[0] if steps else None, forest.groups[e.row["rowid"]], R.Agg(), d)
    item = _Item(turn, e, row, steps)
    item.tool_names = list(tool_names)
    item.name = _name(item)
    return item


def _select(
    conn: sqlite3.Connection,
    builder: R.RowBuilder,
    root: int,
    turn: sqlite3.Row,
    entries: Sequence[R._Entry],
    depth: int,
    kind: str,
    terms: Sequence[str],
    out: list[wire.FindHit],
) -> None:
    """Append the hits among `entries`, each agent entry followed by the hits among its own rows."""
    forest = builder.forest
    # `failures` and `skills` read the first step of a tool; `search` reads the steps of every entry
    wanted = ("call", "tool", "agent") if kind == "search" else () if kind == "agents" else ("tool",)
    steps = R.steps_by_op(conn, [e.row["rowid"] for e in entries if e.kind in wanted])
    tool_names: dict[int, list[str]] = defaultdict(list)
    if kind == "search":
        for e in entries:
            if e.kind in ("tool", "agent") and e.row["tool_name"]:
                tool_names[e.row["parent_rowid"]].append(e.row["tool_name"])
    chosen = [
        e
        for e in entries
        if _selected(
            kind,
            e,
            steps.get(e.row["rowid"], ()),
            tool_names.get(e.row["rowid"], ()) if e.kind == "call" else (),
            forest,
            terms,
        )
    ]
    full = {f.row["rowid"]: f for f in _with_full_rows(conn, chosen)}
    for e in entries:
        rowid = e.row["rowid"]
        if rowid in full:
            names = tool_names.get(rowid, ()) if e.kind == "call" else ()
            item = _hit_item(turn, full[rowid], steps.get(rowid, ()), names, forest, depth)
            out.append(_hit(item, None))
        if e.kind == "agent":
            inner = _group_entries(conn, builder, root, rowid)
            _select(conn, builder, root, turn, inner, depth + 1, kind, terms, out)


def _hits_selected(
    conn: sqlite3.Connection, root: int, kind: str, terms: Sequence[str]
) -> list[wire.FindHit]:
    """`failures`, `skills`, `agents` and `search` (see SELECT FIRST in the contract)."""
    slash = {r[0] for r in conn.execute(_SLASH_TURNS, (root,))} if kind == "skills" else set()
    folded_terms = [_fold(t) for t in terms]
    forest = R.Forest(conn, root)
    # entries only: no row of this path reads the turn scan
    builder = R.RowBuilder(conn, root, forest, R.TurnScan())
    hits: list[wire.FindHit] = []
    for turn in time_order(conn.execute(_TURNS, (root,)).fetchall()):
        if turn["turn_kind"] == "harness":
            continue  # SES-51
        if turn["rowid"] in slash:
            hits.append(_turn_hit(turn))
        label = _fold(_turn_label(turn))
        lacking = [t for t in folded_terms if t not in label]  # a term in the turn label hits every row of it
        _select(
            conn, builder, root, turn, _turn_entries(conn, builder, turn["rowid"]), 0, kind, lacking, hits
        )
    return hits


def find(
    session: str, kind: str, measure: str = "cost", text: str = "", limit: int = 2000, db: Db = None
) -> wire.FindResult:
    """QRY-38; API-25. `kind` is biggest, waits, failures, skills, agents or search."""
    if kind not in KINDS:
        raise QueryError(f"kind must be one of {list(KINDS)}, got {kind!r}")
    if measure not in MEASURES:
        raise QueryError(f"measure must be one of {list(MEASURES)}, got {measure!r}")
    if not 1 <= limit <= MAX_LIMIT:
        raise QueryError(f"limit must be between 1 and {MAX_LIMIT}, got {limit}")
    terms = text.split() if text else []
    if kind == "search" and not terms:
        raise QueryError("text is required for kind=search")
    with _conn(db) as conn:
        root = lookup_session(conn, session)["rowid"]
        if kind in ("biggest", "waits"):
            hits = _hits_built(conn, root, kind, measure)
        else:
            hits = _hits_selected(conn, root, kind, terms)
        return wire.FindResult(
            freshness=wire.Freshness(**shapes.freshness(conn)),
            kind=kind,
            total=len(hits),
            hits=hits[:limit],
            per_turn=dict(Counter(h.turn for h in hits)),
        )
