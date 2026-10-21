"""Hand-built canonical bundles for the store tests (no adapter involved)."""

from __future__ import annotations

import sqlite3

from botsworth.model import Bundle, Event, Op, Step
from botsworth.store import ingest, load

H = 3_600_000
T0 = 1_790_000_000_000


class B:
    """One session bundle; ops are added parent-first. Times are ms offsets from T0."""

    def __init__(self, sid: str, provider: str = "claude_code", start: int = 0, end: int = 100, **kw):
        self.sid, self.provider = sid, provider
        self.ops: list[Op] = []
        self.steps: list[Step] = []
        self.events: list[Event] = []
        self.session = self._op("session", "-", None, start, end, project="/repo", account="acct", **kw)

    def id(self, kind: str, key: str) -> str:
        return f"{self.provider}/{self.sid}/{kind}/{key}"

    def _op(self, kind, key, parent, start, end, **kw) -> str:
        op = Op(
            op_id=self.id(kind, key),
            kind=kind,
            provider=self.provider,
            session_id=self.sid,
            src=f"src://{self.provider}/{self.sid}.jsonl#L1-L1",
            parent=parent,
            start_ms=None if start is None else T0 + start,
            end_ms=None if end is None else T0 + end,
            **kw,
        )
        self.ops.append(op)
        return op.op_id

    def turn(self, key, parent=None, start=0, end=100, agent=None, **kw) -> str:
        if kw.get("turn_kind") == "human":
            kw.setdefault("label", key)  # DM-50 rule 5 reads the label of a human turn
        return self._op("turn", key, parent or self.session, start, end, agent_id=agent, **kw)

    def engagement(self, key, parent, agent, start=0, end=100, **kw) -> str:
        return self._op("engagement", key, parent, start, end, agent_id=agent, **kw)

    def llm(
        self, key, parent, start, end, tokens=(10, 20, 0, 0), cost=1.0, occ=None, agent=None, **kw
    ) -> str:
        i, o, cr, cw = tokens
        kw.setdefault("model", "m1")
        kw.setdefault("tok_prov", "present")
        kw.setdefault("time_prov", "present")
        kw.setdefault("cost_basis", "computed" if cost is not None else "missing")
        return self._op(
            "llm_call",
            key,
            parent,
            start,
            end,
            dur_ms=None if start is None or end is None else end - start,
            in_tok=i,
            out_tok=o,
            cache_read_tok=cr,
            cache_write_tok=cw,
            cost_usd=cost,
            occ_key=occ or key,
            agent_id=agent,
            **kw,
        )

    def tool(self, key, parent, start, end, agent=None, occ=None, **kw) -> str:
        kw.setdefault("time_prov", "present")
        dur = None if start is None or end is None else end - start
        return self._op(
            "tool_call", key, parent, start, end, dur_ms=dur, occ_key=occ or key, agent_id=agent, **kw
        )

    def step(self, turn, op_id, verb, **kw) -> None:
        n = sum(1 for s in self.steps if s.turn_id == turn)
        self.steps.append(
            Step(
                turn_id=turn,
                ordinal=n,
                op_id=op_id,
                verb=verb,
                src=f"src://{self.provider}/{self.sid}.jsonl#L1-L1",
                **kw,
            )
        )

    def event(self, kind, op_id=None, ts=0, **fields) -> None:
        n = len(self.events)
        self.events.append(
            Event(
                event_id=f"{self.provider}/{self.sid}/event/{kind}#{n}",
                kind=kind,
                session_id=self.sid,
                src=f"src://{self.provider}/{self.sid}.jsonl#L1-L1",
                ts_ms=T0 + ts,
                op_id=op_id,
                fields=fields,
            )
        )

    def bundle(self) -> Bundle:
        return Bundle(
            provider=self.provider,
            bundle_id=self.sid,
            session_id=self.sid,
            ops=list(self.ops),
            steps=list(self.steps),
            events=list(self.events),
        )


def store(tmp_path) -> sqlite3.Connection:
    conn = ingest.open_store(tmp_path / "s.db")
    conn.row_factory = sqlite3.Row
    return conn


def put(conn, *builders: B) -> list[load.LoadResult]:
    return [load.load_bundle(conn, b.bundle() if isinstance(b, B) else b) for b in builders]


def row(conn, op_id) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM ops_v WHERE op_id=?", (op_id,)).fetchone()
    assert r is not None, op_id
    return r


def owners_ok(conn) -> None:
    """Invariant 5 and invariant 3 over the whole store."""
    dup_keys = conn.execute(
        "SELECT provider, kind, occ_hash FROM ops WHERE kind IN ('llm_call','tool_call')"
        " AND occ_hash IS NOT NULL GROUP BY 1,2,3 HAVING sum(duplicate_of IS NULL) != 1"
    ).fetchall()
    assert dup_keys == []
    bad = conn.execute(
        "SELECT p.op_id FROM ops p WHERE abs(coalesce(p.incl_cost_usd,0)"
        " - coalesce((SELECT sum(c.incl_cost_usd) FROM ops c WHERE c.parent_rowid=p.rowid),0)"
        " - (CASE WHEN p.kind IN ('llm_call','tool_call') AND p.duplicate_of IS NULL"
        " THEN coalesce(p.cost_usd,0) ELSE 0 END)) > 1e-9"
    ).fetchall()
    assert bad == []


def split_rows(conn: sqlite3.Connection, op_rowid: int, scope: str) -> dict[tuple[str, str | None], int]:
    """The stored `time_split` rows of one op and scope as `{(state, substate): ms}`."""
    return {
        (r["state"], r["substate"]): r["ms"]
        for r in conn.execute("SELECT * FROM time_split WHERE op_rowid=? AND scope=?", (op_rowid, scope))
    }
