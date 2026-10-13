from botsworth.store import timesplit as ts

M = 60_000
H = 60 * M


def figs(f):
    return (f.model_ms, f.tool_ms, f.agents_ms, f.none_ms)


def test_session_split_10_5_20_25_sums_to_60():
    # DM-51: wall 60 min; main llm 10, main tool 5, agent-only 20, nothing 25
    s = ts.split(
        [(0, 60 * M)],
        [(0, 10 * M)],
        [(10 * M, 15 * M)],
        [(15 * M, 35 * M)],
        [(1, 0, 60 * M, "human")],
    )
    assert (s.model_ms, s.tool_ms, s.agents_ms, s.none_ms) == (10 * M, 5 * M, 20 * M, 25 * M)
    assert s.model_ms + s.tool_ms + s.agents_ms + s.none_ms == 60 * M


def test_turn_windows_plus_before_first_sum_to_session():
    # DM-51: wall 0..100 min, turns start at 10, 40, 70 min; leaves are scattered across windows
    turns = [(1, 10 * M, 30 * M, "human"), (2, 40 * M, 60 * M, "human"), (3, 70 * M, 90 * M, "human")]
    llm = [(0, 15 * M), (45 * M, 50 * M), (75 * M, 80 * M)]
    tool = [(12 * M, 20 * M), (65 * M, 72 * M)]
    agents = [(5 * M, 45 * M), (95 * M, 100 * M)]
    s = ts.split([(0, 100 * M)], llm, tool, agents, turns)
    parts = [s.before_first, *(f.window for f in s.turns.values())]
    for i, name in enumerate(("model", "tool", "agents", "none")):
        assert sum(figs(p)[i] for p in parts) == getattr(s, f"{name}_ms")
    assert s.model_ms + s.tool_ms + s.agents_ms + s.none_ms == 100 * M
    # last window ends at the wall end
    assert figs(s.turns[3].window) == (5 * M, 2 * M, 5 * M, 18 * M)
    assert sum(figs(s.before_first)) == 10 * M


def test_none_stretch_split_at_human_ask():
    # DM-52: nothing runs 13:45-14:32, a human ask starts at 14:10
    t = lambda h, m: h * H + m * M  # noqa: E731
    turns = [(1, t(13, 0), t(13, 45), "human"), (2, t(14, 10), t(14, 32), "human")]
    s = ts.split([(t(13, 0), t(14, 32))], [(t(13, 0), t(13, 45))], [], [], turns)
    assert s.stretches == [
        ts.Stretch(1, "none", t(13, 45), t(14, 10), "human"),
        ts.Stretch(2, "none", t(14, 10), t(14, 32), None),
    ]


def test_agents_only_40_min_one_row():
    s = ts.split([(0, 70 * M)], [(0, 10 * M)], [], [(15 * M, 55 * M)], [(1, 0, 10 * M, "human")])
    agents = [r for r in s.stretches if r.state == "agents"]
    assert agents == [ts.Stretch(1, "agents", 15 * M, 55 * M, None)]


def test_agents_30_s_no_row():
    s = ts.split([(0, 10 * M)], [(0, 2 * M)], [], [(3 * M, 3 * M + 30_000)], [(1, 0, 2 * M, "human")])
    assert not [r for r in s.stretches if r.state == "agents"]


def test_parts_under_60_s_dropped_after_split():
    # a 90 s none run cut at a turn start 30 s in leaves a 30 s part (dropped) and a 60 s part (kept)
    turns = [(1, 0, 10 * M, "human"), (2, 10 * M + 30_000, 12 * M, "human")]
    s = ts.split([(0, 12 * M)], [(0, 10 * M), (11 * M + 30_000, 12 * M)], [], [], turns)
    assert s.stretches == [ts.Stretch(2, "none", 10 * M + 30_000, 11 * M + 30_000, None)]


def test_resume_gap_outside_wall_no_rows():
    # DM-52: two segments 27 h apart; the gap is outside the wall
    s = ts.split(
        [(0, 10 * M), (27 * H, 27 * H + 10 * M)],
        [(0, 10 * M), (27 * H, 27 * H + 10 * M)],
        [],
        [],
        [(1, 0, 10 * M, "human"), (2, 27 * H, 27 * H + 10 * M, "human")],
    )
    assert s.stretches == []
    assert s.none_ms == 0
    assert s.model_ms == 20 * M


def test_span_10_5_0_15_and_gap_after_end_is_stretch():
    # DM-53: turn 10:00-10:30, 10 min model, 5 min tool, 15 min nothing; next turn 11:00
    t0 = 10 * H
    turns = [(1, t0, t0 + 30 * M, "human"), (2, t0 + 60 * M, t0 + 70 * M, "human")]
    s = ts.split(
        [(t0, t0 + 70 * M)],
        [(t0, t0 + 10 * M), (t0 + 60 * M, t0 + 70 * M)],
        [(t0 + 10 * M, t0 + 15 * M)],
        [],
        turns,
    )
    f = s.turns[1]
    assert f.span_wall_ms == 30 * M
    assert (f.span_model_ms, f.span_tool_ms, f.span_agents_ms, f.span_none_ms) == (10 * M, 5 * M, 0, 15 * M)
    assert s.stretches == [
        ts.Stretch(1, "none", t0 + 15 * M, t0 + 60 * M, "human"),
    ]
    # the 10:30-11:00 part is inside the stretch, not inside the span
    assert f.span_none_ms == 15 * M


def test_span_excludes_agent_tail_after_end():
    # DM-53 (D38 H1): the agent runs 10 min after the turn's last main-thread record
    turns = [(1, 0, 20 * M, "human")]
    s = ts.split([(0, 30 * M)], [(0, 20 * M)], [], [(20 * M, 30 * M)], turns)
    f = s.turns[1]
    assert f.span_wall_ms == 20 * M
    assert f.span_agents_ms == 0 and f.span_none_ms == 0
    assert f.window.agents_ms == 10 * M  # the tail stays in the window figures


def test_async_tool_counts_as_tool():
    # the caller passes async tool intervals in main_tool; they win over agents
    s = ts.split([(0, 10 * M)], [], [(0, 10 * M)], [(2 * M, 4 * M)], [])
    assert (s.tool_ms, s.agents_ms, s.none_ms) == (10 * M, 0, 0)


def test_model_beats_tool_beats_agents_priority():
    s = ts.split([(0, 10 * M)], [(0, 4 * M)], [(2 * M, 7 * M)], [(0, 10 * M)], [])
    assert (s.model_ms, s.tool_ms, s.agents_ms, s.none_ms) == (4 * M, 3 * M, 3 * M, 0)


def test_leaf_outside_wall_does_not_count():
    s = ts.split([(0, 10 * M)], [(0, 5 * M), (20 * M, 30 * M)], [], [], [])
    assert s.model_ms == 5 * M and s.none_ms == 5 * M


def test_max_silence_queue_record_30_min():
    # DM-40: tool_use at t, queue-operation at t+10 min, result at t+40 min, nothing else
    t = 1_000_000
    stamps = [t, t + 10 * M, t + 40 * M]
    assert ts.max_silence([("a", t, t + 40 * M)], stamps) == {"a": 30 * M}


def test_max_silence_short_call_le_4_s():
    t = 5_000
    assert ts.max_silence([("a", t, t + 4_000)], [0, t, t + 4_000, 99_000]) == {"a": 4_000}
    assert ts.max_silence([("a", t, t + 4_000)], []) == {"a": 4_000}


def test_span_counts_resume_gap_as_none():
    # D48: turn 10:00-10:30, wall segments 10:00-10:10 and 10:20-10:30, 10 min model; the gap is span `none`
    t0 = 10 * H
    s = ts.split(
        [(t0, t0 + 10 * M), (t0 + 20 * M, t0 + 30 * M)],
        [(t0, t0 + 10 * M)],
        [],
        [],
        [(1, t0, t0 + 30 * M, "human")],
    )
    f = s.turns[1]
    assert f.span_wall_ms == 30 * M
    assert (f.span_model_ms, f.span_tool_ms, f.span_agents_ms, f.span_none_ms) == (10 * M, 0, 0, 20 * M)
    assert f.span_model_ms + f.span_tool_ms + f.span_agents_ms + f.span_none_ms == f.span_wall_ms
    # session figures and stretches still exclude the gap: only the in-wall 10:20-10:30 is a stretch
    assert (s.model_ms, s.none_ms) == (10 * M, 10 * M)
    assert s.stretches == [ts.Stretch(1, "none", t0 + 20 * M, t0 + 30 * M, None)]
