# ruff: noqa: E501  (SQL and report prose)
"""CONTRACT: API-50 / CLI-24 latency check on the real store (schema 10) (TST-5, Q4).

GUARANTEES:
- Opens the store only through `sqlite3.connect("file:...?mode=ro", uri=True)`, directly or through the
  query layer; never writes, never runs ingest.
- Every API-50 endpoint is timed at the HTTP layer (request to last byte, `Accept-Encoding: gzip`, gzip
  not decoded inside the timed span) against `make_server` started in-process on port 0. The three CLI-24
  commands are timed by calling `project_slow`, `session_breakdown` and `top_tasks` with `db=<path>`.
- Warm: 3 discarded runs, then `--runs` (default 20) timed runs. p50 and p95 by linear interpolation.
- Arguments are chosen from the store (largest session by llm_calls, largest main turn, largest
  agent row, 7-day and all-time periods in the machine zone ending at `meta.last_ingest_ms`) and are written to the
  report with the store size and row counts.
- For every case over its target the report carries the slowest statements of one traced run, each with
  its `EXPLAIN QUERY PLAN`. A traced run of every case also tells which API-51 indexes a plan uses.
- Exit 1 when a request fails, an API-51 index is missing, or a p95 exceeds its target by more than
  50 % (TST-5). Exit 0 with a message when the store is missing or its schema is not 7.
- `--self-test --db STORE [--roots DIR]` runs every case once against a fixture store, no targets, no
  report file; `DIR` holds one subdirectory per provider (the `qroots` layout).
EXPECTS: a schema 10 store with `ANALYZE` run (DM-5).
FAILURE BEHAVIOR: a non-200 answer or a case whose arguments cannot be chosen is a failed case (exit 1).
DOES NOT: tune anything; measure `POST /api/query` (no v4 target); drop the OS cache.

Run: make bench-api   (or: uv run python tests/bench/bench_api.py --db PATH [--runs 20] [--only NAME])
"""

from __future__ import annotations

import argparse
import http.client
import importlib
import json
import re
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from botsworth import pointer  # noqa: E402
from botsworth.http import params as P  # noqa: E402
from botsworth.http.server import make_server, match_routes  # noqa: E402
from botsworth.query.scope import Scope  # noqa: E402
from botsworth.query.views import usecases  # noqa: E402
from botsworth.query.zone import resolve_tz  # noqa: E402
from botsworth.store import location  # noqa: E402

# `import botsworth.query` yields the function: botsworth/__init__ rebinds the name.
query_api = importlib.import_module("botsworth.query")

DEFAULT_DB = location.resolve_db()
DEFAULT_OUT = REPO / "tasks" / "2026-10-13-v4-build" / "evidence" / "q4-latency.md"
WARMUP = 3
FAIL_FACTOR = 1.5  # TST-5
SOURCE_MAX_BYTES = 20 * 1024 * 1024  # API-50 `/api/source` row
API51_INDEXES = (
    "sessions_op",
    "ops_parent_kind_start",
    "ops_caused_by",
    "rollup_bucket",
)  # API-51 item 5 (D50)
OTHER_INDEXES = (
    "ops_attr_turn",
    "stretches_root",
    "steps_skill_load",
    "sessions_automated_time",
    "ops_root_pre",
    "ops_op_id",
    "owner_key",
)


def ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


# ── argument choice ──────────────────────────────────────────────────────────


@dataclass
class Args:
    tz: str
    last_ingest_ms: int
    today: str
    day7_from: str
    day30_from: str
    day92_from: str
    session: str
    session_n_calls: int
    session2: str
    turns_session: str
    turns_session_n: int
    turn: str
    turn_n_calls: int
    agent: str
    agent_n_calls: int
    call_op: str
    tool_op: str
    resolve_id: str
    term: str
    project: str
    project_n: int
    source_op: str | None
    source_bytes: int | None
    counts: dict[str, int]
    store_bytes: int


def local_day(ms: int, tz: str) -> datetime:
    return datetime.fromtimestamp(ms / 1000, ZoneInfo(tz))


def choose(db: Path) -> Args:
    conn = ro(db)
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    tz, last = resolve_tz(), int(meta["last_ingest_ms"])  # D50: the zone is a request parameter
    today = local_day(last, tz).date()
    day = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731

    top = conn.execute(
        "SELECT o.op_id, s.n_llm_calls AS n, s.display_title FROM sessions s JOIN ops o ON o.rowid = s.op_rowid"
        " ORDER BY s.n_llm_calls DESC, o.op_id LIMIT 2"
    ).fetchall()
    session, session2 = top[0]["op_id"], top[1]["op_id"]
    turns = conn.execute(
        "SELECT o.op_id, COUNT(*) AS n FROM ops t JOIN ops o ON o.rowid = t.root_rowid"
        " JOIN ops p ON p.rowid = t.parent_rowid WHERE t.kind = 'turn' AND p.kind = 'session'"
        " GROUP BY t.root_rowid ORDER BY n DESC, o.op_id LIMIT 1"
    ).fetchone()
    turn = conn.execute(
        "SELECT t.op_id, t.n_llm_calls AS n FROM ops t JOIN ops p ON p.rowid = t.parent_rowid"
        " WHERE t.kind = 'turn' AND p.kind = 'session' ORDER BY t.n_llm_calls DESC, t.op_id LIMIT 1"
    ).fetchone()
    agent = conn.execute(
        "SELECT op_id, n_llm_calls AS n FROM ops WHERE kind = 'engagement' AND caused_by_rowid IS NOT NULL"
        " ORDER BY n_llm_calls DESC, op_id LIMIT 1"
    ).fetchone()
    root = conn.execute("SELECT rowid FROM ops WHERE op_id = ?", (session,)).fetchone()[0]
    calls = conn.execute(
        "SELECT c.op_id FROM ops c JOIN ops t ON t.rowid = c.parent_rowid JOIN ops p ON p.rowid = t.parent_rowid"
        " WHERE c.root_rowid = ? AND c.kind = 'llm_call' AND t.kind = 'turn' AND p.kind = 'session'"
        " ORDER BY c.rowid",
        (root,),
    ).fetchall()
    call_op = calls[len(calls) // 2][0]
    tool_op = conn.execute(
        "SELECT op_id FROM ops WHERE kind = 'tool_call' AND duplicate_of IS NULL"
        " ORDER BY result_bytes DESC, op_id LIMIT 1"
    ).fetchone()[0]
    title = top[0]["display_title"] or ""
    words = [w for w in re.findall(r"[A-Za-z]{5,}", title)]
    assert words, f"session {session} has no title word of 5+ letters for the palette and find terms"
    start_ms = int(datetime.fromisoformat(day(6)).replace(tzinfo=ZoneInfo(tz)).timestamp() * 1000)
    proj = conn.execute(
        "SELECT project, COUNT(*) AS n FROM sessions WHERE first_ms >= ? AND is_automated = 0"
        " AND project IS NOT NULL GROUP BY project ORDER BY n DESC, project LIMIT 1",
        (start_ms,),
    ).fetchone()
    assert proj is not None, "no non-automated session in the last 7 days"
    src_op, src_bytes = choose_source(conn)
    counts = {
        name: conn.execute(f"SELECT COUNT(*) FROM {table}{where}").fetchone()[0]
        for name, table, where in (
            ("ops", "ops", ""),
            ("sessions", "sessions", ""),
            ("turns", "ops", " WHERE kind = 'turn'"),
            ("llm_calls", "ops", " WHERE kind = 'llm_call'"),
            ("tool_calls", "ops", " WHERE kind = 'tool_call'"),
            ("engagements", "ops", " WHERE kind = 'engagement'"),
            ("steps", "steps", ""),
            ("events", "events", ""),
        )
    }
    conn.close()
    return Args(
        tz=tz,
        last_ingest_ms=last,
        today=today.isoformat(),
        day7_from=day(6),
        day30_from=day(29),
        day92_from=day(91),
        session=session,
        session_n_calls=top[0]["n"],
        session2=session2,
        turns_session=turns["op_id"],
        turns_session_n=turns["n"],
        turn=turn["op_id"],
        turn_n_calls=turn["n"],
        agent=agent["op_id"],
        agent_n_calls=agent["n"],
        call_op=call_op,
        tool_op=tool_op,
        resolve_id=call_op,
        term=words[0],
        project=proj["project"],
        project_n=proj["n"],
        source_op=src_op,
        source_bytes=src_bytes,
        counts=counts,
        store_bytes=db.stat().st_size,
    )


def choose_source(conn: sqlite3.Connection) -> tuple[str | None, int | None]:
    """The last op of the largest on-disk JSONL of at most 20 MB, or (None, None) when none is on disk."""
    best: tuple[int, int] | None = None
    for r in conn.execute("SELECT rowid, provider, relpath FROM src_files"):
        root = pointer.DEFAULT_ROOTS.get(r["provider"])
        if root is None:
            continue
        f = Path(root) / r["relpath"]
        if not f.is_file():
            continue
        size = f.stat().st_size
        if size <= SOURCE_MAX_BYTES and (best is None or size > best[1]):
            best = (r["rowid"], size)
    if best is None:
        return None, None
    row = conn.execute(
        "SELECT op_id FROM ops WHERE src_file = ? AND kind IN ('llm_call', 'tool_call') ORDER BY rowid DESC LIMIT 1",
        (best[0],),
    ).fetchone()
    return (row[0], best[1]) if row else (None, None)


# ── cases ────────────────────────────────────────────────────────────────────


@dataclass
class Case:
    name: str
    target_ms: float | None
    kind: str  # "http" or "cli"
    path: str = ""  # http: path with query string
    call: Callable[[Path], object] | None = None  # cli: timed call
    view_args: str = ""  # one-line description of the arguments
    skip: str | None = None


def qs(**kw: object) -> str:
    return urlencode({k: v for k, v in kw.items() if v is not None}, doseq=True)


def enc(x: str) -> str:
    return quote(x, safe="")


def build_cases(a: Args) -> list[Case]:
    p7 = qs(**{"from": a.day7_from, "to": a.today})
    c: list[Case] = []

    def http(name: str, target: float | None, path: str, skip: str | None = None) -> None:
        c.append(Case(name, target, "http", path=path, view_args=path, skip=skip))

    http("ingest/status", 50, "/api/ingest/status")
    http("explorer/summary 7d", 150, f"/api/explorer/summary?{p7}")
    http("explorer/summary all", 300, "/api/explorer/summary")
    days = qs(window_from=a.day92_from, window_to=a.today)
    http("explorer/days 92d", 150, f"/api/explorer/days?{days}")
    http("explorer/sessions 7d x200", 200, f"/api/explorer/sessions?{p7}&limit=200")
    http("explorer/sessions all x200", 200, "/api/explorer/sessions?limit=200")
    http("explorer/facets 7d", 200, f"/api/explorer/facets?{p7}")
    http("explorer/facets all", 200, "/api/explorer/facets")
    http("palette all", 150, f"/api/palette?{qs(text=a.term)}")
    http("sessions/{s} largest", 200, f"/api/sessions/{enc(a.session)}")
    http("sessions/{s}/turns most turns", 150, f"/api/sessions/{enc(a.turns_session)}/turns")
    http("turns/{t}/rows largest turn", 200, f"/api/turns/{enc(a.turn)}/rows")
    http("turns/{t}/histogram largest turn", 150, f"/api/turns/{enc(a.turn)}/histogram")
    http("sessions/{s}/histogram largest", 400, f"/api/sessions/{enc(a.session)}/histogram")
    http("sessions/{s}/histogram second", 150, f"/api/sessions/{enc(a.session2)}/histogram")
    http("ops/{e}/rows largest agent x500", 200, f"/api/ops/{enc(a.agent)}/rows?limit=500")
    http(
        "sessions/{s}/resolve",
        50,
        f"/api/sessions/{enc(a.session)}/resolve?{qs(id=a.resolve_id)}",
    )
    http("ops/{op}/detail call", 100, f"/api/ops/{enc(a.call_op)}/detail")
    http("ops/{op}/detail tool", 100, f"/api/ops/{enc(a.tool_op)}/detail")
    http("ops/{op}/detail agent", 200, f"/api/ops/{enc(a.agent)}/detail")
    http(
        "source <=20MB",
        100,
        f"/api/source?{qs(src=a.source_op)}",
        skip=None if a.source_op else "no JSONL of at most 20 MB found under the provider roots",
    )
    for kind in ("biggest", "waits", "failures", "skills", "agents"):
        http(f"find {kind}", 300, f"/api/sessions/{enc(a.session)}/find?{qs(kind=kind)}")
    http("find search", 300, f"/api/sessions/{enc(a.session)}/find?{qs(kind='search', text=a.term)}")

    scope7 = Scope(from_day=a.day7_from, to_day=a.today, projects=(a.project,))
    scope30 = Scope(from_day=a.day30_from, to_day=a.today)
    c.append(
        Case(
            "cli slow 7d largest project",
            1500,
            "cli",
            call=lambda db: usecases.project_slow(scope7, True, 10, db),
            view_args=f"project_slow(from {a.day7_from} to {a.today}, project {a.project}, compare, n=10)",
        )
    )
    c.append(
        Case(
            "cli breakdown largest session",
            1500,
            "cli",
            call=lambda db: usecases.session_breakdown(a.session, 10, db),
            view_args=f"session_breakdown({a.session}, n=10)",
        )
    )
    c.append(
        Case(
            "cli tasks 30d command_key",
            2000,
            "cli",
            call=lambda db: usecases.top_tasks(scope30, "command_key", "tokens", 20, db),
            view_args=f"top_tasks(from {a.day30_from} to {a.today}, by=command_key, measure=tokens, n=20)",
        )
    )
    return c


# ── measurement ──────────────────────────────────────────────────────────────


@dataclass
class Result:
    case: Case
    times_ms: list[float] = field(default_factory=list)
    status: int | None = None
    wire_bytes: int | None = None
    body_bytes: int | None = None
    rows: int | None = None
    error: str | None = None
    plans: list[tuple[float, str, list[str]]] = field(default_factory=list)
    indexes: set[str] = field(default_factory=set)

    @property
    def p50(self) -> float:
        return pct(self.times_ms, 0.5)

    @property
    def p95(self) -> float:
        return pct(self.times_ms, 0.95)

    @property
    def verdict(self) -> str:
        if self.error:
            return "ERROR"
        if self.case.skip:
            return "skipped"
        t = self.case.target_ms
        if t is None:
            return "n/a"
        if self.p95 <= t:
            return "pass"
        return "FAIL" if self.p95 > t * FAIL_FACTOR else "over"


def row_count(body: object) -> int | None:
    if isinstance(body, list):
        return len(body)
    if isinstance(body, dict):
        lens = [len(v) for v in body.values() if isinstance(v, list)]
        return max(lens) if lens else None
    return None


def fetch(host: str, port: int, path: str) -> tuple[float, int, int, bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=120)
    t0 = time.perf_counter()
    conn.request("GET", path, headers={"Accept-Encoding": "gzip"})
    resp = conn.getresponse()
    raw = resp.read()
    dt = (time.perf_counter() - t0) * 1000
    status = resp.status
    if resp.getheader("Content-Encoding") == "gzip":
        import gzip

        body = gzip.decompress(raw)
    else:
        body = raw
    conn.close()
    return dt, status, len(raw), body


def run_http(r: Result, port: int, runs: int, warmup: int) -> None:
    for i in range(warmup + runs):
        dt, status, wire_n, body = fetch("127.0.0.1", port, r.case.path)
        if status != 200:
            r.error = f"HTTP {status}: {body[:200].decode(errors='replace')}"
            return
        if i >= warmup:
            r.times_ms.append(dt)
    r.status, r.wire_bytes, r.body_bytes = status, wire_n, len(body)
    r.rows = row_count(json.loads(body))


def run_cli(r: Result, db: Path, runs: int, warmup: int) -> None:
    assert r.case.call is not None
    for i in range(warmup + runs):
        t0 = time.perf_counter()
        out = r.case.call(db)
        dt = (time.perf_counter() - t0) * 1000
        if i >= warmup:
            r.times_ms.append(dt)
    r.status = 200
    r.rows = None if out is None else 1


_NORM = re.compile(r"'(?:[^']|'')*'|\b\d+(?:\.\d+)?\b")


def traced_statements(r: Result, db: Path) -> list[tuple[float, str]]:
    """One traced in-process run; each statement with the ms until the next one started."""
    conn = ro(db)
    stamps: list[tuple[float, str]] = []
    conn.set_trace_callback(lambda s: stamps.append((time.perf_counter(), s)))
    t0 = time.perf_counter()
    try:
        if r.case.kind == "cli":
            r.case.call(conn)  # type: ignore[misc]
        else:
            url = urlparse(r.case.path)
            route, ids = match_routes(url.path)[0]
            kwargs = route.parse(ids, P.parse_query(url.query))
            fn = getattr(query_api, route.view_name)
            fn(**kwargs, **({"db": conn} if route.takes_db else {}))
    finally:
        conn.set_trace_callback(None)
    end = time.perf_counter()
    out: list[tuple[float, str]] = []
    for i, (t, sql) in enumerate(stamps):
        nxt = stamps[i + 1][0] if i + 1 < len(stamps) else end
        out.append(((nxt - t) * 1000, sql))
    conn.close()
    assert t0 <= end
    return out


def explain(conn: sqlite3.Connection, sql: str) -> list[str]:
    try:
        return [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()]
    except sqlite3.Error as exc:
        return [f"(no plan: {exc})"]


def analyse_plans(r: Result, db: Path, want_plans: bool) -> None:
    stmts = traced_statements(r, db)
    conn = ro(db)
    seen: dict[str, tuple[float, str]] = {}
    for ms, sql in stmts:
        key = _NORM.sub("?", sql)
        if key not in seen or ms > seen[key][0]:
            seen[key] = (ms, sql)
    for _ms, sql in seen.values():
        if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
            continue
        plan = explain(conn, sql)
        for line in plan:
            m = re.search(r"INDEX (\w+)", line)
            if m:
                r.indexes.add(m.group(1))
    if want_plans:
        top = sorted(seen.values(), key=lambda t: -t[0])[:3]
        for ms, sql in top:
            r.plans.append((ms, sql, explain(conn, sql)))
    conn.close()


def schema_ok(db: Path) -> tuple[bool, str]:
    if not db.is_file():
        return False, f"store {db} is missing"
    conn = ro(db)
    try:
        version = dict(conn.execute("SELECT key, value FROM meta").fetchall()).get("schema_version")
    except sqlite3.DatabaseError:
        version = None
    conn.close()
    return version == "10", f"store {db} has schema {version}, bench-api needs 10"


def index_report(db: Path) -> dict[str, bool]:
    conn = ro(db)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    have_stat = conn.execute("SELECT COUNT(*) FROM sqlite_stat1").fetchone()[0]
    conn.close()
    out = {n: n in names for n in (*API51_INDEXES, *OTHER_INDEXES)}
    out["sqlite_stat1 (ANALYZE)"] = have_stat > 0
    return out


# ── report ───────────────────────────────────────────────────────────────────


def fmt_bytes(n: int | None) -> str:
    if n is None:
        return "-"
    return f"{n / 1024:.0f} KB" if n < 1 << 20 else f"{n / (1 << 20):.2f} MB"


def render(a: Args, results: list[Result], idx: dict[str, bool], runs: int, db: Path) -> str:
    lines = ["# Q4 latency: API-50 and CLI-24 on the real store (schema 10)", ""]
    lines += [
        f"Store `{db}`: {fmt_bytes(a.store_bytes)}, schema 10 ({a.store_bytes:,} bytes). Warm: {WARMUP} discarded runs, then {runs} timed runs.",
        "HTTP cases are timed from request to last byte (`Accept-Encoding: gzip`, body not decoded inside the span).",
        "CLI cases call the view function with `db=<path>` (connection open included).",
        "A case passes when p95 <= target; `over` is up to 50 % above target; `FAIL` is more than 50 % above (TST-5).",
        "",
        "## Row counts",
        "",
        "| table | rows |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v:,} |" for k, v in a.counts.items()]
    lines += ["", "## Arguments", ""]
    lines += [
        f"- tz `{a.tz}`; last ingest {local_day(a.last_ingest_ms, a.tz).isoformat()}; today {a.today};"
        f" 7d = {a.day7_from}..{a.today}; 30d from {a.day30_from}; 92-day window from {a.day92_from}.",
        f"- Largest session by llm_calls: `{a.session}` ({a.session_n_calls:,} calls). Second: `{a.session2}`.",
        f"- Session with the most main turns: `{a.turns_session}` ({a.turns_session_n} turns).",
        f"- Largest main turn by llm_calls (inclusive of agents): `{a.turn}` ({a.turn_n_calls:,} calls).",
        f"- Largest agent row by llm_calls: `{a.agent}` ({a.agent_n_calls} calls).",
        f"- detail: call `{a.call_op}`, tool (largest result) `{a.tool_op}`; resolve id = that call (a call directly under a main turn).",
        f"- Search term (word of the largest session's title): `{a.term}`.",
        f"- slow project (most non-automated sessions in 7d, {a.project_n}): `{a.project}`.",
        f"- source: `{a.source_op}` ({fmt_bytes(a.source_bytes)} file)."
        if a.source_op
        else "- source: no JSONL of at most 20 MB found on disk.",
        "",
        "## Latency",
        "",
        "| case | target ms | p50 ms | p95 ms | result | rows | wire | decoded |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        t = "-" if r.case.target_ms is None else f"{r.case.target_ms:.0f}"
        if r.error or r.case.skip:
            lines.append(
                f"| {r.case.name} | {t} | - | - | {r.verdict}: {r.error or r.case.skip} | - | - | - |"
            )
            continue
        lines.append(
            f"| {r.case.name} | {t} | {r.p50:.1f} | {r.p95:.1f} | {r.verdict} | "
            f"{'-' if r.rows is None else r.rows} | {fmt_bytes(r.wire_bytes)} | {fmt_bytes(r.body_bytes)} |"
        )
    lines += ["", "Request arguments per case:", ""]
    lines += [f"- {r.case.name}: `{r.case.view_args}`" for r in results]
    lines += ["", "`POST /api/query` has no v4 target (API-50) and is not measured."]

    lines += ["", "## API-52 payloads", ""]
    for r in results:
        if r.case.name in (
            "explorer/sessions 7d x200",
            "explorer/sessions all x200",
            "turns/{t}/rows largest turn",
        ):
            lines.append(
                f"- {r.case.name}: {fmt_bytes(r.body_bytes)} decoded, {fmt_bytes(r.wire_bytes)} on the wire"
            )
    lines += [
        "- Spec bounds: about 150 KB for a 200-row session page, about 1.5 MB for the largest turn's rows.",
        "- Responses over 32 KB carry `Content-Encoding: gzip` when the request accepts it (the wire size above"
        " is the gzip size where decoded > 32 KB).",
    ]

    lines += ["", "## One request per listing page (E3)", ""]
    page = next((r for r in results if r.case.name == "explorer/sessions all x200"), None)
    if page is not None and not page.error:
        lines.append(
            "The listing page is `GET /api/explorer/sessions`: one request returns the rows with `activity`,"
            f" `day_split`, `n_agents`, project name and cost (rows returned: {page.rows}). Requests per page: 1."
        )

    lines += ["", "## API-51 indexes", "", "| index | in store | used by a plan |", "|---|---|---|"]
    used: dict[str, list[str]] = {}
    for r in results:
        for name in r.indexes:
            used.setdefault(name, []).append(r.case.name)
    for name, present in idx.items():
        users = used.get(name, [])
        shown = ", ".join(users[:3]) + (f" (+{len(users) - 3})" if len(users) > 3 else "")
        lines.append(f"| {name} | {'yes' if present else '**MISSING**'} | {shown or 'no traced plan'} |")

    over = [r for r in results if r.verdict in ("over", "FAIL")]
    lines += ["", "## Cases over target", ""]
    if not over:
        lines.append("None.")
    for r in over:
        lines += [
            f"### {r.case.name}: p95 {r.p95:.1f} ms, target {r.case.target_ms:.0f} ms ({r.verdict})",
            "",
            f"Arguments `{r.case.view_args}`; response rows {r.rows}; tables: ops {a.counts['ops']:,},"
            f" sessions {a.counts['sessions']:,}.",
            "",
            "Slowest statements of one traced run (ms is the time until the next statement started):",
            "",
        ]
        for ms, sql, plan in r.plans:
            short = " ".join(sql.split())
            lines += [f"- {ms:.1f} ms: `{short[:400]}{'...' if len(short) > 400 else ''}`"]
            lines += [f"  - plan: {step}" for step in plan]
        lines.append("")
    return "\n".join(lines) + "\n"


# ── main ─────────────────────────────────────────────────────────────────────


def start_server(db: Path) -> tuple[object, int]:
    server = make_server("127.0.0.1", 0, db, None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--only", help="run only cases whose name contains this text")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--roots", type=Path, help="provider roots for source pointers (self-test)")
    ns = ap.parse_args(argv)

    ok, why = schema_ok(ns.db)
    if not ok:
        print(f"skipped: {why}")
        return 0
    if ns.roots:
        pointer.DEFAULT_ROOTS.update({p.name: p for p in ns.roots.iterdir() if p.is_dir()})

    a = choose(ns.db)
    cases = [c for c in build_cases(a) if not ns.only or ns.only in c.name]
    runs, warmup = (1, 0) if ns.self_test else (ns.runs, WARMUP)
    server, port = start_server(ns.db)
    results: list[Result] = []
    try:
        for case in cases:
            r = Result(case)
            results.append(r)
            if case.skip and not ns.self_test:
                continue
            if case.skip:
                r.error = case.skip
                continue
            if case.kind == "http":
                run_http(r, port, runs, warmup)
            else:
                run_cli(r, ns.db, runs, warmup)
            print(f"{case.name}: " + (r.error or f"p50 {r.p50:.1f} ms p95 {r.p95:.1f} ms"), flush=True)
    finally:
        server.shutdown()
        server.server_close()

    if ns.self_test:
        bad = [r for r in results if r.error]
        for r in bad:
            print(f"self-test: {r.case.name}: {r.error}")
        print(f"self-test: {len(results)} cases, {len(bad)} failed")
        return 1 if bad else 0

    idx = index_report(ns.db)
    for r in results:
        if r.error or r.case.skip:
            continue
        analyse_plans(r, ns.db, want_plans=r.verdict in ("over", "FAIL"))
    report = render(a, results, idx, runs, ns.db)
    ns.out.parent.mkdir(parents=True, exist_ok=True)
    ns.out.write_text(report)
    print(f"wrote {ns.out}")
    failed = [r for r in results if r.verdict in ("FAIL", "ERROR")]
    missing = [n for n in API51_INDEXES if not idx[n]]
    for r in failed:
        print(f"FAIL {r.case.name}: {r.error or f'p95 {r.p95:.1f} ms > 1.5 x {r.case.target_ms:.0f} ms'}")
    for n in missing:
        print(f"MISSING INDEX {n}")
    over = [r.case.name for r in results if r.verdict == "over"]
    if over:
        print("over target (within 50 %): " + ", ".join(over))
    return 1 if failed or missing else 0


if __name__ == "__main__":
    sys.exit(main())
