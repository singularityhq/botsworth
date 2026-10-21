# ruff: noqa: F811
"""Q2a session views (views/session.py): QRY-29, QRY-30, QRY-33, QRY-38a, QRY-2; TST-3 items 4 and 6.

Expected values come from tests/fixtures/qstore/expected.json (hand-derived from the fixture timeline), never
from the code. Two variant stores are built by the pipeline from edited copies of QF streams.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from qstore import STREAMS, expected, qstore  # noqa: F401 (the QF fixture)
from qstore_ids import SAGENTS, SHARNESS, SSKILLS, SSLOW1, SSTRETCH, parts, turn
from stream_load import load_streams

import botsworth
from botsworth.query import NotFound, QueryError, _conn, wire
from botsworth.query.views import session as V
from botsworth.query.views.common import lookup_session

TZ = "Asia/Kolkata"
ALL = [
    "s2day", "sauto", "sunpriced", "szero", "sagents", "sharness", "sstretch", "stools", "sskills", "sslow1",
    "sslow2", "sfork", "somp", "scodex",
]  # fmt: skip


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp() * 1000)


def sid(key: str) -> str:
    return key.split("/")[1]


def detail(db: Path, key: str) -> wire.SessionInfo:
    return V.session_detail(sid(key), db=db).session


def turns(db: Path, key: str, min_gap_ms: int = 120000) -> list:
    return V.session_turns(sid(key), min_gap_ms, db=db).items


def only(items: list, kind: str) -> list:
    return [i for i in items if i.type == kind]


def build_variant(tmp_path_factory, name: str, source: str, edit) -> Path:
    """The pipeline's store for the `source` stream renamed to `name`, each record passed through `edit`."""
    base = tmp_path_factory.mktemp(name)
    streams = base / "streams"
    streams.mkdir()
    lines = []
    for line in (STREAMS / f"{source}.jsonl").read_text().replace(source, name).splitlines():
        rec = json.loads(line)
        lines.append(json.dumps(edit(rec) or rec))
    (streams / f"{name}.jsonl").write_text("\n".join(lines) + "\n")
    db = base / "p.sqlite"
    result = load_streams(streams=[streams], db=db)
    assert result.rejected == [], result.rejected
    return db


@pytest.fixture(scope="module")
def before_first_store(tmp_path_factory) -> Path:
    """sharness whose first turn is a harness `/clear`: the first numbered turn starts at 12:03:00."""

    def edit(rec: dict):
        if rec.get("op_id") == "claude_code/sbefore/turn/main#0":
            rec.update(turn_kind="harness", turn_origin="builtin:clear", label="/clear")

    return build_variant(tmp_path_factory, "sbefore", "sharness", edit)


@pytest.fixture(scope="module")
def seqless_store(tmp_path_factory) -> Path:
    """sharness with `seq` removed from every op, as Codex streams carry it (QRY-30, DM-24)."""

    def edit(rec: dict):
        rec.pop("seq", None)

    return build_variant(tmp_path_factory, "sseqless", "sharness", edit)


@pytest.fixture(scope="module")
def dm47_store(tmp_path_factory) -> Path:
    """somp with the DM-47 pair: (out 100, dur 2000 ms, derived) and (out 50, dur null)."""

    def edit(rec: dict):
        if rec.get("rec") != "op":
            return
        if rec.get("op_id") == "omp/sdm47/llm_call/resp_o1":
            rec.update(out_tok=100, dur_ms=2000, end_ms=rec["start_ms"] + 2000, time_prov="derived")
        if rec.get("op_id") == "omp/sdm47/llm_call/resp_o2":
            rec.update(out_tok=50, time_prov="missing")
            for key in ("dur_ms", "ttft_ms", "ttft_prov"):
                rec.pop(key, None)

    return build_variant(tmp_path_factory, "sdm47", "somp", edit)


# ── QRY-33 ───────────────────────────────────────────────────────────────────


def test_model_lines_speed_and_basis(qstore, dm47_store):
    with _conn(dm47_store) as conn:
        root = lookup_session(conn, "sdm47")["rowid"]
        (line,) = V.model_lines(conn, "o.root_rowid = ?", (root,))
    # DM-47: 100 tokens over 2000 ms; the call with a null dur does not qualify
    assert (line.n_calls, line.speed_tok_s, line.speed_basis) == (2, 50.0, "derived")
    assert line.model_ms == 2000
    assert line.ttft_ms_mean == 800  # the mean of the non-null ttft values: only the first call has one
    with _conn(qstore) as conn:
        root = lookup_session(conn, "somp")["rowid"]
        (omp,) = V.model_lines(conn, "o.root_rowid = ?", (root,))
    # 200 + 300 tokens over 4000 + 6000 ms, both `present`; ttft 800 and 1200 (case 14)
    assert (omp.speed_tok_s, omp.speed_basis, omp.model_ms, omp.ttft_ms_mean) == (
        50.0,
        "present",
        10000,
        1000,
    )
    assert omp.cost_usd == pytest.approx(0.5) and not omp.cost_lower_bound


def test_model_lines_unpriced_call_makes_a_lower_bound(qstore):
    with _conn(qstore) as conn:
        root = lookup_session(conn, "sunpriced")["rowid"]
        (line,) = V.model_lines(conn, "o.root_rowid = ?", (root,))
    assert line.cost_usd == pytest.approx(3.0)  # 1.00 + null + 2.00 (case 3)
    assert (line.cost_lower_bound, line.n_unpriced, line.n_calls) == (True, 1, 3)


# ── QRY-29 ───────────────────────────────────────────────────────────────────


def test_cost_main_agents_unattributed(qstore):
    c = detail(qstore, SAGENTS).cost
    b3 = expected()["case_6_sagents"]["b3"]
    # c1 + c2 + c2b are the main calls; the rest ran in agents; C#0 has no turn
    assert c.cost_usd == pytest.approx(expected()["sessions"]["sagents"]["incl_cost_usd"])
    assert c.main == pytest.approx(0.30)
    assert c.agents == pytest.approx(4.40)
    assert c.unattributed == pytest.approx(b3["unattributed_cost_usd"])
    assert not c.cost_lower_bound and c.n_unpriced == 0
    stretch = detail(qstore, SSTRETCH).cost
    assert (stretch.main, stretch.agents, stretch.unattributed) == pytest.approx((0.60, 0.80, 0.0))
    # QRY-11: an unpriced call makes a lower bound; a session with no owned call has null costs
    assert detail(qstore, "x/sunpriced").cost.cost_lower_bound
    zero = detail(qstore, "x/szero").cost
    assert (zero.cost_usd, zero.main, zero.agents, zero.unattributed) == (None, None, None, None)


def test_time_split_sums_to_wall(qstore):
    ex = expected()
    for key, name in ((SAGENTS, "case_6_sagents"), (SSTRETCH, "case_8_sstretch")):
        d = detail(qstore, key)
        split = ex[name]["b3_time" if name == "case_6_sagents" else "b3"]["session_split_ms"]
        assert parts(d.time_split) == split
        assert d.wall_ms == ex["sessions"][sid(key)]["wall_ms"]
    for name in ALL:
        d = detail(qstore, f"x/{name}")
        assert sum(parts(d.time_split).values()) == d.wall_ms, name  # DM-51


def test_time_split_parts_come_in_ses15_order_with_substates(qstore):
    d = detail(qstore, SSTRETCH)
    want = expected()["case_8_sstretch"]["b3"]["session_split_ms"]
    got = parts(d.time_split)
    assert list(got) == [
        "model", "tool", "agents", "you/permission", "you/reply", "agentmsg", "limit", "none/none"
    ]  # fmt: skip
    assert got == want
    assert all(p.ms > 0 for p in d.time_split.parts)  # parts above 0 ms only
    assert "none_split" not in {f.name for f in wire.SessionInfo.__dataclass_fields__.values()}


def test_longest_two_minute_floor(qstore):
    d = detail(qstore, SSTRETCH)
    dur = expected()["case_8_sstretch"]["b3"]["durations_ms"]
    L = d.longest
    assert (L.you.dur_ms, L.limit.dur_ms, L.agentmsg.dur_ms, L.agents.dur_ms) == (
        dur[0],
        dur[2],
        dur[3],
        dur[6],  # the longer of the two agents stretches around the permission wait
    )
    assert L.none.dur_ms == dur[1]  # the 22 min wait is the longest `none` stretch
    for t in (L.you, L.limit, L.agentmsg, L.agents, L.none):
        assert (
            t.row.type == "gap"
            and t.row.id == f"gap-{ms('2026-10-10T02:01:00Z')}"
            or t.row.id.startswith("gap-")
        )
    assert L.limit.turn == turn(SSTRETCH, 1)  # SES-37: the turn whose ledger holds the gap
    assert L.model is None and L.tool is None  # no main-thread call lasts 2 min
    # every stretch of the harness session is under 2 min, except one that is exactly 2 min (>= 2 min counts)
    h = detail(qstore, SHARNESS).longest
    assert h.you.dur_ms == 120000 and h.none is None and h.agents is None and h.limit is None
    # model and tool: the longest main-thread call (case 11: 180 s model call, 40 min Bash)
    slow = detail(qstore, SSLOW1).longest
    assert (slow.model.dur_ms, slow.model.row.type) == (180000, "call")
    assert (slow.tool.dur_ms, slow.tool.row.type) == (2400000, "tool")
    assert slow.tool.row.id == "claude_code/sslow1/tool_call/toolu_slowbash"
    assert slow.tool.turn == turn(SSLOW1, 0)
    # D54: the 15-min AskUserQuestion is `you`, reached through its tool row; `tool` skips it
    assert (slow.you.dur_ms, slow.you.row.type) == (900000, "tool")
    assert slow.you.row.id == "claude_code/sslow1/tool_call/toolu_ask1"
    assert slow.you.turn == turn(SSLOW1, 0)


def test_before_first_counts_under_none_not_you(before_first_store):
    d = V.session_detail("sbefore", db=before_first_store).session
    # 12:00:00-12:00:05 and 12:00:35-12:03:00 lie before the first numbered turn (T1 starts at 12:03:00)
    got = parts(d.time_split)
    assert got["you/reply"] == 90000  # only 12:06:30-12:08:00, which ends at the human turn T3
    assert got["none/before_first"] == 150000  # 5 s + 145 s, never folded into `you` (SES-18, D41 M11)
    assert sum(got.values()) == d.wall_ms and "you/ask" not in got
    assert d.longest.none.dur_ms == 120000  # the before-first stretch is the `none` jump target
    assert d.longest.you is None  # 90 s is under 2 min
    first = d.longest.none
    items = turns(before_first_store, "x/sbefore")
    gaps = only(items, "gap")
    assert [(g.state, g.substate) for g in gaps] == [("none", "before_first")]
    assert first.row.id == gaps[0].id
    # SES-37: the gap belongs to the first numbered turn, and sits directly above it in the list
    t1 = next(i for i in items if i.type == "turn" and i.display_id == "T1")
    assert gaps[0].turn == t1.turn
    assert items[items.index(gaps[0]) + 1] is t1


def test_n_agents_distinct_agent_id(qstore):
    # six engagements, four agent ids: ra (#0 and the +cycle #1), rb (#0, #1), rc, rn
    assert detail(qstore, SAGENTS).n_agents == 4
    assert detail(qstore, SSTRETCH).n_agents == 1
    assert detail(qstore, SHARNESS).n_agents == 0


def test_n_skill_loads_steps_plus_slash_events(qstore):
    b3 = expected()["case_10_sskills"]
    # three steps carry skill_load, and the slash turn has an event but no step
    assert detail(qstore, SSKILLS).n_skill_loads == b3["b3"]["n_steps_with_skill_load"] + 1
    per_turn = [t.n_skill_loads for t in only(turns(qstore, SSKILLS), "turn")]
    assert per_turn == [1, b3["turn_main#1_skill_steps"], 1]
    assert detail(qstore, SHARNESS).n_skill_loads == 0


def test_session_header_fields(qstore):
    d = detail(qstore, SHARNESS)
    assert (d.n_turns, d.n_harness_turns) == (3, 3)
    assert d.session == "claude_code/sharness/session/-" and d.provider == "claude_code"
    assert d.project == "/u/f/harnessproj" and d.project_name == "harnessproj"
    assert d.wall_ms == expected()["sessions"]["sharness"]["wall_ms"]
    assert d.ends_after_days == 0 and d.is_automated is False
    assert detail(qstore, "x/sauto").is_automated is True
    assert detail(qstore, "x/s2day").ends_after_days == 1  # 16:59:55Z-19:01:00Z crosses IST midnight (case 1)
    assert (
        detail(qstore, "x/stools").n_failed == 3
    )  # exit 1, interrupted, error; exit 0 and the fork copies do not count
    assert detail(qstore, "x/sfork").n_failed == 0
    assert detail(qstore, "x/szero").tok_total is None or detail(qstore, "x/szero").tok_total == 0


# ── QRY-30 ───────────────────────────────────────────────────────────────────


def test_turns_include_harness_kind(qstore):
    ex = expected()["case_7_sharness"]
    got = only(turns(qstore, SHARNESS), "turn")
    assert [(t.kind, t.origin, t.label) for t in got] == [
        (w["turn_kind"], w["turn_origin"], w["label"]) for w in ex["turns"]
    ]
    assert [t.display_id for t in got] == [None if ix is None else f"T{ix}" for ix in ex["b3"]["display_ix"]]
    assert [t.turn for t in got] == [turn(SHARNESS, n) for n in range(6)]


def test_turn_order_null_start_by_seq():
    rows = [
        {"rowid": 1, "seq": 0, "start_ms": 1000},
        {"rowid": 2, "seq": 1, "start_ms": None},
        {"rowid": 3, "seq": 2, "start_ms": None},
        {"rowid": 4, "seq": 3, "start_ms": 5000},
        {"rowid": 5, "seq": 4, "start_ms": 5000},
        {"rowid": 6, "seq": 5, "start_ms": None},
    ]
    assert [r["rowid"] for r in V.time_order(rows[::-1])] == [1, 2, 3, 4, 5, 6]
    # a null-start turn with an earlier seq stays after the turn that precedes it, not at the start
    leading = [{"rowid": 9, "seq": 0, "start_ms": None}, {"rowid": 8, "seq": 1, "start_ms": 10}]
    assert [r["rowid"] for r in V.time_order(leading)] == [9, 8]
    # start_ms orders before seq
    swapped = [{"rowid": 1, "seq": 0, "start_ms": 900}, {"rowid": 2, "seq": 1, "start_ms": 100}]
    assert [r["rowid"] for r in V.time_order(swapped)] == [2, 1]


def test_turn_order_without_seq_falls_back_to_pre(qstore, seqless_store):
    # QRY-30: a Codex-style store has no `seq` on any op; session_turns used to raise TypeError
    with _conn(seqless_store) as conn:
        assert conn.execute("SELECT count(*) FROM ops WHERE seq IS NOT NULL").fetchone()[0] == 0
    got = turns(seqless_store, "x/sseqless")
    want = turns(qstore, SHARNESS)
    assert [(i.type, getattr(i, "label", None), getattr(i, "display_id", None)) for i in got] == [
        (i.type, getattr(i, "label", None), getattr(i, "display_id", None)) for i in want
    ]


def test_time_order_pre_breaks_ties_when_seq_is_null():
    rows = [
        {"rowid": 3, "seq": None, "pre": 7, "start_ms": 5000},
        {"rowid": 1, "seq": None, "pre": 1, "start_ms": 1000},
        {"rowid": 2, "seq": None, "pre": 4, "start_ms": None},
        {"rowid": 4, "seq": None, "pre": 9, "start_ms": 5000},
    ]
    assert [r["rowid"] for r in V.time_order(rows)] == [1, 2, 3, 4]


def test_turn_span_figures(qstore):
    span = expected()["case_6_sagents"]["b3_time"]["span_ms"]
    t1, t2 = only(turns(qstore, SAGENTS), "turn")
    for t, key in ((t1, "main#0"), (t2, "main#1")):
        s = span[key]
        assert t.wall_ms == s["wall"]
        got = parts(t.time_split)
        assert got == {k: v for k, v in s.items() if k != "wall"}
        assert t.waiting_ms == t.wall_ms - s["model"] - s["tool"]  # D38 L2, D54
        assert sum(got.values()) == t.wall_ms
    assert (t1.start_ms, t1.end_ms) == (
        ms("2026-10-08T09:00:00Z"),
        ms("2026-10-08T09:00:17Z"),
    )  # H1: not widened
    attr = expected()["case_6_sagents"]["b3"]["attr"]
    assert t1.cost_usd == pytest.approx(attr["main#0"]["attr_cost_usd"])
    assert (t1.tok_total, t1.n_llm_calls) == (
        attr["main#0"]["attr_tok_total"],
        attr["main#0"]["attr_n_llm_calls"],
    )
    assert (t2.tok_total, t2.n_llm_calls) == (
        attr["main#1"]["attr_tok_total"],
        attr["main#1"]["attr_n_llm_calls"],
    )
    assert t1.cost_main == pytest.approx(0.10) and t2.cost_main == pytest.approx(
        0.20
    )  # own calls: c1; c2 + c2b
    assert [(m.model, m.cost_usd) for m in t2.models] == [("synth-model-1", pytest.approx(0.20))]


def test_turn_cost_sum_plus_unattributed_equals_total(qstore):
    for name in ALL:
        d = detail(qstore, f"x/{name}")
        if d.cost.cost_usd is None:
            continue
        spent = sum(t.cost_usd or 0.0 for t in only(turns(qstore, f"x/{name}"), "turn"))
        assert spent + d.cost.unattributed == pytest.approx(d.cost.cost_usd), name  # QRY-30 invariant (DM-45)


def test_gap_rows_threshold_and_position(qstore):
    dur = expected()["case_8_sstretch"]["b3"]["durations_ms"]
    items = turns(qstore, SSTRETCH)
    gaps = only(items, "gap")
    # default 2 min: you, limit and agentmsg end where a turn starts; the 22 min wait ends mid-turn, the
    # agent's 4 min permission wait ends mid-turn, and the agents stretches are never gap rows here
    assert [(g.state, g.substate, g.dur_ms) for g in gaps] == [
        ("you", "reply", dur[0]),
        ("limit", None, dur[2]),
        ("agentmsg", None, dur[3]),
    ]
    assert all(g.id == f"gap-{g.start_ms}" for g in gaps)
    for g in gaps:
        after = items[items.index(g) + 1]
        assert after.type == "turn" and after.start_ms == g.end_ms  # the gap sits directly above its turn
    assert [i.turn for i in items if i.type == "turn"] == [turn(SSTRETCH, n) for n in range(5)]
    assert [g.state for g in only(turns(qstore, SSTRETCH, 3_000_000), "gap")] == ["limit"]
    assert [g.state for g in only(turns(qstore, SSTRETCH, 60000), "gap")] == ["you", "limit", "agentmsg"]
    # sharness: 90 s and 85 s stretches before harness turns show at the 60 s floor, not at 2 min
    assert len(only(turns(qstore, SHARNESS), "gap")) == 1
    low = only(turns(qstore, SHARNESS, 60000), "gap")
    assert [(g.dur_ms, g.next_turn.kind) for g in low] == [
        (120000, "human"),
        (85000, "harness"),
        (90000, "harness"),
        (90000, "human"),
    ]


def test_limit_gap_next_turn_label(qstore):
    items = turns(qstore, SSTRETCH)
    (limit,) = [g for g in only(items, "gap") if g.state == "limit"]
    assert limit.dur_ms == 62 * 60 * 1000  # the 1h02m gap of the SES-17 scenario
    prefix = expected()["case_8_sstretch"]["limit_label_prefix"]
    assert limit.next_turn.label.startswith(prefix)
    resume = items[items.index(limit) + 1]
    assert resume.turn == limit.next_turn.turn and resume.label == limit.next_turn.label
    assert (limit.next_turn.kind, limit.next_turn.display_id) == ("human", "T3")


def test_agent_runs_tails(qstore):
    t1, t2 = only(turns(qstore, SAGENTS), "turn")
    eng = lambda k: f"claude_code/sagents/engagement/{k}"  # noqa: E731
    # DM-55: engagements whose causing tool_call is attributed to the turn, by start. The nested rn#0 was
    # caused by a call inside ra#0, which DM-45 attributes to T1; rc#0 has no cause and is on no turn.
    assert [(r.engagement, r.agent_name) for r in t1.agent_runs] == [
        (eng("ra#0"), "ra"), (eng("rb#0"), "rb"), (eng("rn#0"), "rn"), (eng("rb#1"), "rb"),
    ]  # fmt: skip
    assert (t1.agent_runs[3].start_ms, t1.agent_runs[3].end_ms) == (
        ms("2026-10-08T10:02:30Z"),
        ms("2026-10-08T10:07:30Z"),
    )
    assert t1.agent_runs[3].end_ms > t1.end_ms  # the tail that the Waterfall draws past the turn
    assert [r.engagement for r in t2.agent_runs] == [eng("ra#1")]
    assert (t2.agent_runs[0].start_ms, t2.agent_runs[0].end_ms) == (
        ms("2026-10-08T10:00:00Z"),
        ms("2026-10-08T10:01:15Z"),
    )
    # n_agents counts agent rows (one per causing call): spawnA, spawnB (two runs), spawnN; and sendA
    assert (t1.n_agents, t2.n_agents) == (3, 1)


def test_turn_row_equals_batched_row(qstore):
    items = only(turns(qstore, SAGENTS), "turn")
    with _conn(qstore) as conn:
        for t in items:
            row = conn.execute("SELECT * FROM ops WHERE op_id = ?", (t.turn,)).fetchone()
            assert V.turn_row(conn, row) == t


def test_min_gap_below_60000_is_a_query_error(qstore):
    with pytest.raises(QueryError, match="min_gap_ms"):
        V.session_turns("sagents", 59999, db=qstore)


# ── QRY-38a ──────────────────────────────────────────────────────────────────


def test_resolve_op_id_occ_key_tool_use_engagement_gap(qstore):
    eng = lambda k: f"claude_code/sagents/engagement/{k}"  # noqa: E731
    call = "claude_code/sagents/llm_call/req_ra0"

    def r(ident: str, session: str = "sagents"):
        got = V.resolve(session, ident, db=qstore)
        return got.row.type, got.row.id, got.turn, got.depth

    assert r(call) == ("call", call, turn(SAGENTS, 0), 1)  # inside ra#0
    assert r("req_ra0") == r(call)  # the provider request id (suffix of the op_id)
    assert r("req_rn0") == (
        "call",
        "claude_code/sagents/llm_call/req_rn0",
        turn(SAGENTS, 0),
        2,
    )  # nested agent
    assert r("req_c2")[3] == 0 and r("req_c2")[2] == turn(SAGENTS, 1)
    # a tool_use id; a spawn call is the agent row, named by its first engagement (QRY-34)
    assert r("toolu_spawnB") == ("agent", eng("rb#0"), turn(SAGENTS, 0), 0)
    assert r("toolu_sendA") == ("agent", eng("ra#1"), turn(SAGENTS, 1), 0)
    # an engagement id: any engagement of a spawn call resolves to that row
    assert r(eng("rb#1")) == ("agent", eng("rb#0"), turn(SAGENTS, 0), 0)
    assert r(eng("rn#0")) == ("agent", eng("rn#0"), turn(SAGENTS, 0), 1)
    assert r(turn(SAGENTS, 1)) == ("turn", turn(SAGENTS, 1), turn(SAGENTS, 1), 0)
    # a gap
    start = ms("2026-10-08T09:12:30Z")
    assert r(f"gap-{start}") == ("gap", f"gap-{start}", turn(SAGENTS, 0), 0)
    # a tool call that is not a spawn
    tool = V.resolve("stools", "toolu_pt1", db=qstore)
    assert (tool.row, tool.depth) == (wire.RowRef("tool", "claude_code/stools/tool_call/toolu_pt1"), 0)


def test_resolve_unknown_404(qstore):
    for ident in ("nope", "req_nope", "toolu_nope", "gap-1", "gap-x", "claude_code/sagents/session/-"):
        with pytest.raises(NotFound):
            V.resolve("sagents", ident, db=qstore)
    with pytest.raises(NotFound):
        V.resolve("sagents", "req_t1", db=qstore)  # a row of another session
    with pytest.raises(NotFound):
        V.resolve("nosuchsession", "req_c1", db=qstore)


def test_resolve_row_with_no_turn_is_not_found(qstore):
    for ident in ("req_rc0", "claude_code/sagents/engagement/rc#0"):  # C#0 has no causing call (DM-45)
        with pytest.raises(NotFound, match="no turn"):
            V.resolve("sagents", ident, db=qstore)


# ── QRY-2 ────────────────────────────────────────────────────────────────────


def test_display_ids_skip_harness(qstore):
    # T1, harness, T2: `show <s>:T2` is the third turn op; T3 the sixth; there is no T4
    assert botsworth.show("sharness:T1", db=qstore)["op"]["op_id"] == turn(SHARNESS, 0)
    assert botsworth.show("sharness:T2", db=qstore)["op"]["op_id"] == turn(SHARNESS, 2)
    assert botsworth.show("sharness:T3", db=qstore)["op"]["op_id"] == turn(SHARNESS, 5)
    with pytest.raises(NotFound, match="out of range"):
        botsworth.show("sharness:T4", db=qstore)
    rows = botsworth.outline("claude_code/sharness/session/-", depth=1, db=qstore)["rows"]
    by_id = {r["id"]: r["display_id"] for r in rows if r["kind"] == "turn"}
    assert by_id[turn(SHARNESS, 1)] is None and by_id[turn(SHARNESS, 3)] is None
    assert [by_id[turn(SHARNESS, n)] for n in (0, 2, 5)] == ["T1", "T2", "T3"]
    assert botsworth.show(turn(SHARNESS, 1), db=qstore)["op"]["kind"] == "turn"  # harness: reachable by op_id
