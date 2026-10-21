import pytest

from botsworth.store import timesplit as ts

M = 60_000
H = 60 * M


def turn(rowid, start, end=None, kind="human", seq=None, origin="user", label="hello", numbered=True):
    return ts.TurnFact(rowid, start, end, rowid if seq is None else seq, kind, origin, label, numbered)


def split(wall, llm=(), tool=(), agents=(), waits=None, turns=()):
    return ts.split(wall, list(llm), list(tool), list(agents), waits or {}, list(turns))


def sums(acc):
    return {k: v for k, v in acc.items() if v}


def test_session_split_10_5_20_25_sums_to_60():
    # DM-51: wall 60 min; main llm 10, main tool 5, agent-only 20, nothing 25 (no turn starts at its end)
    s = split(
        [(0, 60 * M)], [(0, 10 * M)], [(10 * M, 15 * M)], [(15 * M, 35 * M)], turns=[turn(1, 0, 60 * M)]
    )
    assert s.session == {
        ("model", None): 10 * M,
        ("tool", None): 5 * M,
        ("agents", None): 20 * M,
        ("none", "none"): 25 * M,
    }
    assert sum(s.session.values()) == 60 * M


def test_turn_windows_plus_before_first_sum_to_session():
    # DM-51: wall 0..100 min, turns start at 10, 40, 70 min; leaves are scattered across windows
    turns = [turn(1, 10 * M, 30 * M), turn(2, 40 * M, 60 * M), turn(3, 70 * M, 90 * M)]
    llm = [(0, 15 * M), (45 * M, 50 * M), (75 * M, 80 * M)]
    tool = [(12 * M, 20 * M), (65 * M, 72 * M)]
    agents = [(5 * M, 45 * M), (95 * M, 100 * M)]
    s = split([(0, 100 * M)], llm, tool, agents, turns=turns)
    for part in set(s.session) | set(s.before_first):
        total = s.before_first.get(part, 0) + sum(w.get(part, 0) for w in s.windows.values())
        assert total == s.session.get(part, 0), part
    assert sum(s.session.values()) == 100 * M
    # the last window ends at the wall end
    assert sums(s.windows[3]) == {
        ("model", None): 5 * M,
        ("tool", None): 2 * M,
        ("agents", None): 5 * M,
        ("none", "none"): 18 * M,
    }
    assert sum(s.before_first.values()) == 10 * M


def test_idle_before_human_turn_is_split_into_reply_and_none():
    # DM-52: nothing runs 13:45-14:32 and a human ask starts at 14:10
    def t(h, m):
        return h * H + m * M

    turns = [turn(1, t(13, 0), t(13, 45)), turn(2, t(14, 10), t(14, 32))]
    s = split([(t(13, 0), t(14, 32))], [(t(13, 0), t(13, 45))], turns=turns)
    assert s.stretches == [
        ts.Stretch(1, "you", "reply", t(13, 45), t(14, 10)),
        ts.Stretch(2, "none", "none", t(14, 10), t(14, 32)),
    ]


def test_idle_30_s_before_human_turn_is_reply_in_totals_but_no_stretch():
    # DM-50 rule 5: every instant counts in the totals; the 60 s floor applies to stretches only
    turns = [turn(1, 0, 10 * M), turn(2, 10 * M + 30_000, 12 * M)]
    s = split([(0, 12 * M)], [(0, 10 * M), (10 * M + 30_000, 12 * M)], turns=turns)
    assert s.session[("you", "reply")] == 30_000
    assert s.stretches == []


def test_idle_before_first_numbered_turn_is_before_first():
    turns = [turn(1, 10 * M, 20 * M)]
    s = split([(0, 20 * M)], [(10 * M, 20 * M)], turns=turns)
    assert sums(s.session) == {("none", "before_first"): 10 * M, ("model", None): 10 * M}
    assert s.stretches == [ts.Stretch(None, "none", "before_first", 0, 10 * M)]


def test_idle_before_first_when_only_harness_turns_start_earlier():
    # `numbered` decides, not "a turn starts earlier": a harness turn does not end the before-first period
    turns = [turn(1, 5 * M, 6 * M, kind="harness", numbered=False), turn(2, 10 * M, 20 * M)]
    s = split([(0, 20 * M)], [(10 * M, 20 * M)], turns=turns)
    assert s.session[("none", "before_first")] == 10 * M


def test_limit_label_from_next_human_turn_text():
    turns = [turn(1, 0, M), turn(2, 11 * M, 12 * M, label="  I hit my usage limit. Resets later")]
    s = split([(0, 12 * M)], [(0, M), (11 * M, 12 * M)], turns=turns)
    assert s.session[("limit", None)] == 10 * M
    assert ("you", "reply") not in s.session


def test_agentmsg_label_and_none_for_other_kinds():
    turns = [
        turn(1, 0, M),
        turn(2, 5 * M, 6 * M, kind="message", origin="teammate", label="m"),
        turn(3, 10 * M, 11 * M, kind="message", origin="plugin", label="p"),
        turn(4, 15 * M, 16 * M, kind="harness", origin="sdk", numbered=False),
    ]
    llm = [(0, M), (5 * M, 6 * M), (10 * M, 11 * M), (15 * M, 16 * M)]
    s = split([(0, 16 * M)], llm, turns=turns)
    assert s.session[("agentmsg", None)] == 4 * M
    assert s.session[("none", "none")] == 8 * M  # before the plugin message and before the harness turn


def test_shared_turn_start_lowest_seq_decides_label_and_holds_window():
    turns = [
        turn(1, 0, M),
        turn(2, 10 * M, 11 * M, kind="message", origin="plugin", seq=7),
        turn(3, 10 * M, 11 * M, kind="human", seq=3),
    ]
    s = split([(0, 11 * M)], [(0, M), (10 * M, 11 * M)], turns=turns)
    assert s.session[("you", "reply")] == 9 * M  # turn 3 (lowest seq) is human
    assert 2 not in s.windows and 3 in s.windows
    assert sums(s.windows[3]) == {("model", None): M}


def test_agents_only_40_min_one_row():
    s = split([(0, 70 * M)], [(0, 10 * M)], agents=[(15 * M, 55 * M)], turns=[turn(1, 0, 10 * M)])
    assert [r for r in s.stretches if r.state == "agents"] == [ts.Stretch(1, "agents", None, 15 * M, 55 * M)]


def test_agents_30_s_no_row():
    s = split([(0, 10 * M)], [(0, 2 * M)], agents=[(3 * M, 3 * M + 30_000)], turns=[turn(1, 0, 2 * M)])
    assert not [r for r in s.stretches if r.state == "agents"]


def test_parts_under_60_s_dropped_after_cut():
    # a 90 s idle run cut at a turn start 30 s in leaves a 30 s part (dropped) and a 60 s part (kept)
    turns = [turn(1, 0, 10 * M), turn(2, 10 * M + 30_000, 12 * M)]
    s = split([(0, 12 * M)], [(0, 10 * M), (11 * M + 30_000, 12 * M)], turns=turns)
    assert s.stretches == [ts.Stretch(2, "none", "none", 10 * M + 30_000, 11 * M + 30_000)]


def test_resume_gap_outside_wall_no_rows():
    # DM-52: two segments 27 h apart; the gap is outside the wall
    seg = [(0, 10 * M), (27 * H, 27 * H + 10 * M)]
    s = split(seg, seg, turns=[turn(1, 0, 10 * M), turn(2, 27 * H, 27 * H + 10 * M)])
    assert s.stretches == []
    assert s.session == {("model", None): 20 * M}


def test_span_10_5_0_15_and_gap_after_end_is_stretch():
    # DM-53: turn 10:00-10:30, 10 min model, 5 min tool, 15 min nothing; next turn at 11:00
    t0 = 10 * H
    # a plugin message: DM-53's test names no kind, and a human turn would label the idle gap you/reply
    turns = [turn(1, t0, t0 + 30 * M), turn(2, t0 + 60 * M, t0 + 70 * M, kind="message", origin="plugin")]
    s = split(
        [(t0, t0 + 70 * M)],
        [(t0, t0 + 10 * M), (t0 + 60 * M, t0 + 70 * M)],
        [(t0 + 10 * M, t0 + 15 * M)],
        turns=turns,
    )
    assert s.spans[1] == {("model", None): 10 * M, ("tool", None): 5 * M, ("none", "none"): 15 * M}
    assert s.stretches == [ts.Stretch(1, "none", "none", t0 + 15 * M, t0 + 60 * M)]


def test_span_excludes_agent_tail_after_end():
    # DM-53 (D38 H1): the agent runs 10 min after the turn's last main-thread record
    s = split([(0, 30 * M)], [(0, 20 * M)], agents=[(20 * M, 30 * M)], turns=[turn(1, 0, 20 * M)])
    assert s.spans[1] == {("model", None): 20 * M}
    assert s.windows[1][("agents", None)] == 10 * M  # the tail stays in the window figures


def test_span_counts_resume_gap_as_none():
    # D48: turn 10:00-10:30, wall 10:00-10:10 and 10:20-10:30, 10 min model; the gap is span none/none
    t0 = 10 * H
    s = split(
        [(t0, t0 + 10 * M), (t0 + 20 * M, t0 + 30 * M)],
        [(t0, t0 + 10 * M)],
        turns=[turn(1, t0, t0 + 30 * M)],
    )
    assert s.spans[1] == {("model", None): 10 * M, ("none", "none"): 20 * M}
    # session figures and stretches still exclude the gap: only the in-wall 10:20-10:30 is a stretch
    assert s.session == {("model", None): 10 * M, ("none", "none"): 10 * M}
    assert s.stretches == [ts.Stretch(1, "none", "none", t0 + 20 * M, t0 + 30 * M)]


def test_async_tool_counts_as_tool():
    # the caller passes async tool intervals in main_tool; they win over agents
    s = split([(0, 10 * M)], tool=[(0, 10 * M)], agents=[(2 * M, 4 * M)])
    assert s.session == {("tool", None): 10 * M}


def test_model_beats_tool_beats_agents_priority():
    s = split([(0, 10 * M)], [(0, 4 * M)], [(2 * M, 7 * M)], [(0, 10 * M)])
    assert s.session == {("model", None): 4 * M, ("tool", None): 3 * M, ("agents", None): 3 * M}


def test_leaf_outside_wall_does_not_count():
    s = split([(0, 10 * M)], [(0, 5 * M), (20 * M, 30 * M)])
    assert s.session == {("model", None): 5 * M, ("none", "before_first"): 5 * M}


# --- DM-50 user-wait cases (D54) ---------------------------------------------------------------------------


def test_main_ask_alone_20_min_is_you_ask_and_makes_no_stretch():
    waits = {("main", "ask"): [(10 * H, 10 * H + 20 * M)]}
    s = split([(10 * H, 10 * H + 20 * M)], waits=waits, turns=[turn(1, 10 * H, 10 * H + 20 * M)])
    assert s.session == {("you", "ask"): 20 * M}
    assert s.stretches == []  # a main-thread wait is a tool row already
    assert s.spans[1] == {("you", "ask"): 20 * M}


def test_main_ask_with_agent_inside_is_15_ask_5_agents():
    t0 = 10 * H
    waits = {("main", "ask"): [(t0, t0 + 20 * M)]}
    s = split([(t0, t0 + 20 * M)], agents=[(t0 + 5 * M, t0 + 10 * M)], waits=waits, turns=[turn(1, t0)])
    assert s.session == {("you", "ask"): 15 * M, ("agents", None): 5 * M}


def test_agent_permission_4_min_main_idle_is_you_permission_with_stretch():
    t0 = 10 * H
    waits = {("agent", "permission"): [(t0, t0 + 4 * M)]}
    s = split([(t0, t0 + 4 * M)], waits=waits, turns=[turn(1, t0, t0 + 4 * M)])
    assert s.session == {("you", "permission"): 4 * M}
    assert s.stretches == [ts.Stretch(1, "you", "permission", t0, t0 + 4 * M)]


def test_main_tool_parallel_with_main_ask_is_tool_for_the_overlap():
    waits = {("main", "ask"): [(0, 10 * M)]}
    s = split([(0, 10 * M)], tool=[(2 * M, 6 * M)], waits=waits, turns=[turn(1, 0, 10 * M)])
    assert s.session == {("tool", None): 4 * M, ("you", "ask"): 6 * M}


def test_wait_rule_order_main_ask_before_main_permission_before_agent():
    waits = {
        ("main", "ask"): [(0, 2 * M)],
        ("main", "permission"): [(0, 4 * M)],
        ("agent", "ask"): [(0, 6 * M)],
        ("agent", "permission"): [(0, 8 * M)],
    }
    s = split([(0, 8 * M)], waits=waits, turns=[turn(1, 0, 8 * M)])
    assert s.session == {("you", "ask"): 4 * M, ("you", "permission"): 4 * M}
    # main ask 0-2, main permission 2-4, agent ask 4-6, agent permission 6-8: only agent parts make stretches
    assert [(r.substate, r.start_ms, r.end_ms) for r in s.stretches] == [
        ("ask", 4 * M, 6 * M),
        ("permission", 6 * M, 8 * M),
    ]


def test_agent_wait_next_to_main_wait_of_same_substate_keeps_main_part_out_of_stretches():
    waits = {("main", "ask"): [(0, 5 * M)], ("agent", "ask"): [(5 * M, 8 * M)]}
    s = split([(0, 8 * M)], waits=waits, turns=[turn(1, 0, 8 * M)])
    assert s.session == {("you", "ask"): 8 * M}
    assert s.stretches == [ts.Stretch(1, "you", "ask", 5 * M, 8 * M)]


def test_span_with_12_min_ask_and_18_min_model():
    waits = {("main", "ask"): [(0, 12 * M)]}
    s = split([(0, 30 * M)], [(12 * M, 30 * M)], waits=waits, turns=[turn(1, 0, 30 * M)])
    assert s.spans[1] == {("you", "ask"): 12 * M, ("model", None): 18 * M}


def test_clip_whole_span_equals_stored_span_and_fills_outside_wall():
    t0 = 10 * H
    seg = [(t0, t0 + 10 * M), (t0 + 20 * M, t0 + 30 * M)]
    waits = {("main", "ask"): [(t0 + 20 * M, t0 + 25 * M)]}
    turns = [turn(1, t0, t0 + 30 * M)]
    s = split(seg, [(t0, t0 + 10 * M)], waits=waits, turns=turns)
    rs = ts.runs(seg, [(t0, t0 + 10 * M)], [], [], waits, turns)
    assert ts.clip(rs, t0, t0 + 30 * M, fill_outside=True) == s.spans[1]
    assert sum(s.spans[1].values()) == 30 * M
    assert sum(ts.clip(rs, t0, t0 + 30 * M).values()) == 20 * M


def test_runs_over_whole_session_equal_session_parts():
    turns = [turn(1, 0, 20 * M), turn(2, 50 * M, 60 * M)]
    waits = {("main", "ask"): [(5 * M, 15 * M)], ("agent", "permission"): [(30 * M, 33 * M)]}
    args = ([(0, 60 * M)], [(0, 5 * M), (50 * M, 60 * M)], [(15 * M, 20 * M)], [(25 * M, 40 * M)])
    rs = ts.runs(*args, waits, turns)
    s = ts.split(*args, waits, turns)
    acc: dict = {}
    for r in rs:
        acc[r.state, r.substate] = acc.get((r.state, r.substate), 0) + r.end_ms - r.start_ms
    assert acc == s.session
    assert sum(acc.values()) == 60 * M


def test_ordered_follows_ses15_then_substate_order():
    acc = {("none", "none"): 1, ("you", "reply"): 2, ("you", "ask"): 3, ("model", None): 4, ("tool", None): 0}
    assert ts.ordered(acc) == [
        ("model", None, 4),
        ("you", "ask", 3),
        ("you", "reply", 2),
        ("none", "none", 1),
    ]


def test_human_turn_without_label_asserts():
    turns = [turn(1, 0, M), turn(2, 5 * M, 6 * M, label=None)]
    with pytest.raises(AssertionError):
        split([(0, 6 * M)], [(0, M), (5 * M, 6 * M)], turns=turns)


# --- DM-40 max_silence --------------------------------------------------------------------------------------


def test_max_silence_queue_record_30_min():
    # DM-40: tool_use at t, queue-operation at t+10 min, result at t+40 min, nothing else
    t = 1_000_000
    stamps = [t, t + 10 * M, t + 40 * M]
    assert ts.max_silence([("a", t, t + 40 * M)], stamps) == {"a": 30 * M}


def test_max_silence_short_call_le_4_s():
    t = 5_000
    assert ts.max_silence([("a", t, t + 4_000)], [0, t, t + 4_000, 99_000]) == {"a": 4_000}
    assert ts.max_silence([("a", t, t + 4_000)], []) == {"a": 4_000}


# --- EXPECTS: each violation raises AssertionError ---------------------------------------------------------


def test_inverted_turn_asserts():
    with pytest.raises(AssertionError, match="inverted"):
        split([(0, 10 * M)], turns=[turn(1, 5 * M, 4 * M)])


def test_duplicate_turn_rowid_asserts():
    with pytest.raises(AssertionError, match="duplicate"):
        split([(0, 10 * M)], turns=[turn(1, 0, M), turn(1, 5 * M, 6 * M)])


def test_ordered_rejects_an_unknown_part():
    with pytest.raises(AssertionError, match="unknown part"):
        ts.ordered({("you", "maybe"): 5})


def _row(kind, **kw):
    base = dict(
        rowid=1, kind=kind, start_ms=0, end_ms=M, agent_id=None, segments=None, n_inverted=0,
        duplicate_of=None, user_wait=None, seq=0, pre=0, turn_kind=None, turn_origin=None,
        display_ix=None, label=None, bg_end_ms=None, command_class=None,
    )  # fmt: skip
    return base | kw


def test_collect_asserts_on_an_unknown_user_wait():
    with pytest.raises(AssertionError, match="unknown user_wait"):
        ts.collect([_row("tool_call", user_wait="maybe")])


def test_collect_asserts_when_display_ix_does_not_match_the_turn_kind():
    # DM-24: the fold numbers human and message turns before the split reads them
    with pytest.raises(AssertionError, match="display_ix"):
        ts.collect([_row("turn", turn_kind="human", display_ix=None, label="x")])
    with pytest.raises(AssertionError, match="display_ix"):
        ts.collect([_row("turn", turn_kind="harness", display_ix=3)])


def test_collect_asserts_on_a_human_turn_without_a_label_even_inside_a_model_call():
    # DM-24: the check runs for every human turn, not only for a turn that ends an idle run
    rows = [
        _row("llm_call", rowid=2, start_ms=0, end_ms=10 * M),
        _row("turn", rowid=3, start_ms=M, turn_kind="human", display_ix=1),
    ]
    with pytest.raises(AssertionError, match="has no label"):
        ts.collect(rows)


def test_collect_reads_waits_by_thread_and_leaves_duplicates_and_inverted_out():
    rows = [
        _row("session", rowid=1, start_ms=0, end_ms=10 * M),
        _row("tool_call", rowid=2, start_ms=0, end_ms=2 * M, user_wait="ask"),
        _row("tool_call", rowid=3, start_ms=2 * M, end_ms=4 * M, user_wait="permission", agent_id="a"),
        _row("tool_call", rowid=4, start_ms=4 * M, end_ms=6 * M, duplicate_of="x", user_wait="ask"),
        _row("llm_call", rowid=5, start_ms=6 * M, end_ms=8 * M, n_inverted=1),
        _row("llm_call", rowid=6, start_ms=8 * M, end_ms=9 * M, agent_id="a"),
        _row(
            "turn",
            rowid=7,
            start_ms=0,
            end_ms=M,
            turn_kind="human",
            display_ix=1,
            label="go",
            seq=None,
            pre=9,
        ),
    ]
    inp = ts.collect(rows)
    assert inp.waits == {("main", "ask"): [(0, 2 * M)], ("agent", "permission"): [(2 * M, 4 * M)]}
    assert (inp.main_llm, inp.main_tool, inp.agent_leaf) == ([], [], [(8 * M, 9 * M)])
    assert inp.wall == [(0, 10 * M)]
    assert inp.turns == [ts.TurnFact(7, 0, M, 9, "human", None, "go", True)]  # `pre` stands in for a NULL seq


@pytest.mark.parametrize(
    "label,want",
    [
        ("I hit my usage limit. Resets 5pm", ("limit", None)),
        ("  PLEASE CONTINUE from where you left off", ("limit", None)),
        ("continue from where you left off", ("limit", None)),
        ("please continue", ("you", "reply")),
        ("go on", ("you", "reply")),
    ],
)
def test_human_turn_label_decides_limit_or_reply(label, want):
    turns = [turn(1, 0, M), turn(2, 5 * M, 6 * M, label=label)]
    s = split([(0, 6 * M)], [(0, M), (5 * M, 6 * M)], turns=turns)
    assert [k for k in s.session if k not in (("model", None),)] == [want]


@pytest.mark.parametrize("origin", ["peer", "coordinator", "teammate", "agent"])
def test_message_origins_that_are_agentmsg(origin):
    turns = [turn(1, 0, M), turn(2, 5 * M, 6 * M, kind="message", origin=origin, label="x")]
    s = split([(0, 6 * M)], [(0, M), (5 * M, 6 * M)], turns=turns)
    assert s.session[("agentmsg", None)] == 4 * M


@pytest.mark.parametrize(
    "kind,origin", [("message", "typed"), ("harness", "interrupt"), ("system", "x"), (None, None)]
)
def test_other_next_turns_are_none(kind, origin):
    turns = [
        turn(1, 0, M),
        turn(2, 5 * M, 6 * M, kind=kind, origin=origin, label="x", numbered=kind != "harness"),
    ]
    s = split([(0, 6 * M)], [(0, M), (5 * M, 6 * M)], turns=turns)
    assert s.session[("none", "none")] == 4 * M
