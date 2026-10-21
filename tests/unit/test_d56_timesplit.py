"""D56 test and polling substates (DM-50 [D56], DM-52): the pure `store.timesplit` code."""

from botsworth.store import timesplit as ts

M = 60_000
H = 60 * M
T = 10 * H  # 10:00


def turn(rowid, start, end=None):
    return ts.TurnFact(rowid, start, end, rowid, "human", "typed", "hello", True)


def split(wall, llm=(), tool=(), agents=(), main_bg=(), classes=ts.NO_CLASSES):
    return ts.split(wall, llm, tool, agents, {}, [turn(1, T)], main_bg, (), classes)


def iv(a, b):
    return [(T + a * M, T + b * M)]


def test_main_test_run_in_parallel_with_a_plain_read_is_tool_test():
    # DM-50 [D56]: a main `uv run pytest` 10:00-10:07 in parallel with a main Read 10:00-10:01
    s = split(iv(0, 7), tool=iv(0, 7), classes=ts.Classes(main_test=iv(0, 7)))
    assert s.session == {("tool", "test"): 7 * M}
    # the Read (plain, 10:00-10:01) is a tool call in `main_tool` only; the pytest keeps its class
    s = split(iv(0, 7), tool=iv(0, 7), classes=ts.Classes(main_test=iv(1, 7)))
    assert s.session == {("tool", None): M, ("tool", "test"): 6 * M}


def test_main_sleep_is_tool_wait():
    s = split([(T, T + 30_000)], tool=[(T, T + 30_000)], classes=ts.Classes(main_wait=[(T, T + 30_000)]))
    assert s.session == {("tool", "wait"): 30_000}


def test_main_test_beats_main_wait_when_both_run():
    s = split(
        iv(0, 4),
        tool=iv(0, 4),
        classes=ts.Classes(main_test=iv(1, 3), main_wait=iv(0, 4)),
    )
    assert s.session == {("tool", "wait"): 2 * M, ("tool", "test"): 2 * M}


def test_main_background_test_is_tool_test_and_one_stretch():
    # a main background pytest 10:00-10:07 with nothing else (no main call covers it)
    s = split(iv(0, 7), main_bg=iv(0, 7), classes=ts.Classes(bg_test=iv(0, 7)))
    assert s.session == {("tool", "test"): 7 * M}
    assert [(x.state, x.substate, x.start_ms, x.end_ms) for x in s.stretches] == [
        ("tool", "test", T, T + 7 * M)
    ]  # DM-52: from a background run it is a stretch


def test_foreground_test_call_is_not_a_stretch():
    s = split(iv(0, 7), tool=iv(0, 7), classes=ts.Classes(main_test=iv(0, 7)))
    assert s.session == {("tool", "test"): 7 * M} and s.stretches == []
    w = split(iv(0, 7), tool=iv(0, 7), classes=ts.Classes(main_wait=iv(0, 7)))
    assert w.stretches == []


def test_agent_test_with_another_agents_llm_call_is_agents_test_only_where_alone():
    # an agent pytest 10:00-10:07 while another agent's llm_call runs 10:02-10:03, main idle
    agents = iv(0, 7)
    cl = ts.Classes(agent_plain=iv(2, 3), agent_test=iv(0, 7))
    s = split(iv(0, 7), agents=agents, classes=cl)
    assert s.session == {("agents", "test"): 6 * M, ("agents", None): M}


def test_agent_wait_loop_and_test_run_overlap():
    # an agent `until ... sleep 5` loop 10:00-10:04 and an agent pytest 10:01-10:03
    cl = ts.Classes(agent_wait=iv(0, 4), agent_test=iv(1, 3))
    s = split(iv(0, 4), agents=iv(0, 4), classes=cl)
    assert s.session == {("agents", "test"): 2 * M, ("agents", "wait"): 2 * M}


def test_agent_class_less_call_with_a_test_is_plain_agents():
    cl = ts.Classes(agent_plain=iv(0, 4), agent_test=iv(1, 3))
    s = split(iv(0, 4), agents=iv(0, 4), classes=cl)
    assert s.session == {("agents", None): 4 * M}


def test_runs_and_split_agree_with_classes():
    cl = ts.Classes(main_test=iv(0, 2), bg_test=iv(2, 6), agent_wait=iv(6, 8), agent_test=iv(7, 9))
    args = (iv(0, 10), [], iv(0, 2), iv(6, 9), {}, [turn(1, T)])
    rs = ts.runs(*args, main_bg=iv(2, 6), classes=cl)
    s = ts.split(*args, main_bg=iv(2, 6), classes=cl)
    acc: dict = {}
    for r in rs:
        acc[r.state, r.substate] = acc.get((r.state, r.substate), 0) + r.end_ms - r.start_ms
    assert acc == s.session
    assert (
        acc[("tool", "test")] == 6 * M and acc[("agents", "wait")] == M and acc[("agents", "test")] == 2 * M
    )


# --- collect ------------------------------------------------------------------------------------------


def _row(kind, **kw):
    base = dict(
        rowid=1, kind=kind, start_ms=0, end_ms=M, agent_id=None, segments=None, n_inverted=0,
        duplicate_of=None, user_wait=None, seq=0, pre=0, turn_kind=None, turn_origin=None,
        display_ix=None, label=None, bg_end_ms=None, command_class=None,
    )  # fmt: skip
    return base | kw


def test_collect_sorts_calls_into_class_lists():
    rows = [
        _row("session", rowid=1, start_ms=0, end_ms=60 * M),
        _row("tool_call", rowid=2, start_ms=0, end_ms=M, command_class="test", bg_end_ms=7 * M),
        _row("tool_call", rowid=3, start_ms=10 * M, end_ms=11 * M, command_class="wait"),
        _row("tool_call", rowid=4, start_ms=20 * M, end_ms=21 * M),
        _row("tool_call", rowid=5, start_ms=30 * M, end_ms=31 * M, agent_id="a", command_class="test"),
        _row("tool_call", rowid=6, start_ms=32 * M, end_ms=33 * M, agent_id="a", command_class="wait"),
        _row("tool_call", rowid=7, start_ms=34 * M, end_ms=35 * M, agent_id="a"),
        _row("llm_call", rowid=8, start_ms=36 * M, end_ms=37 * M, agent_id="a"),
        _row(
            "tool_call",
            rowid=9,
            start_ms=40 * M,
            end_ms=41 * M,
            agent_id="a",
            command_class="test",
            bg_end_ms=45 * M,
        ),  # fmt: skip
        _row("tool_call", rowid=10, start_ms=50 * M, end_ms=51 * M, command_class="wait", bg_end_ms=55 * M),
    ]
    c = ts.collect(rows).classes
    assert c.main_test == [(0, M)] and c.bg_test == [(0, 7 * M)]
    assert c.main_wait == [(10 * M, 11 * M), (50 * M, 51 * M)]
    assert c.agent_test == [(30 * M, 31 * M), (40 * M, 45 * M)]
    assert c.agent_wait == [(32 * M, 33 * M)]
    assert c.agent_plain == [(34 * M, 35 * M), (36 * M, 37 * M)]
    inp = ts.collect(rows)
    assert inp.main_bg == [(0, 7 * M), (50 * M, 55 * M)]  # the wait run is a plain background run
    assert inp.main_tool == [(0, M), (10 * M, 11 * M), (20 * M, 21 * M), (50 * M, 51 * M)]


def test_collect_asserts_on_an_unknown_command_class():
    import pytest

    with pytest.raises(AssertionError, match="command_class"):
        ts.collect([_row("tool_call", command_class="lint")])
