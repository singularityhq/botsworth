import pytest

from botsworth.store import intervals as iv

H = 3_600_000


def test_F3_parallel_agents():
    # F3: main calls [0,5s] and [47s,50s] around two parallel agents [5s,45s] and [10s,35s].
    main = [(0, 5_000), (5_000, 5_000), (47_000, 50_000)]
    agents = [(5_000, 45_000), (10_000, 35_000)]
    everything = main + agents
    assert iv.wall_ms([(0, 50_000)], everything) == 50_000
    assert iv.busy_sum_ms([5_000, 0, 3_000, 40_000, 25_000]) == 73_000
    assert iv.union_ms(everything) == 48_000
    assert iv.blocked_idle(main, agents) == (40_000, 2_000)
    assert iv.max_parallel(everything) == 2


def test_touching_endpoints_merge_and_are_not_parallel():
    assert iv.merge([(0, 10), (10, 20)]) == [(0, 20)]
    assert iv.max_parallel([(0, 10), (10, 20)]) == 1
    assert iv.union_ms([(0, 10), (10, 20), (5, 15)]) == 20
    assert iv.max_parallel([(3, 3), (3, 3)]) == 0  # zero-length intervals never count


def test_resume_gap_is_outside_wall_and_counted_idle():
    # D19: two segments of one file 27 h apart; calls in each.
    segments = [(0, 10_000), (27 * H, 27 * H + 10_000)]
    calls = [(1_000, 9_000), (27 * H + 1_000, 27 * H + 9_000)]
    assert iv.wall_ms(segments, calls) == 20_000
    blocked, idle = iv.blocked_idle(calls, [])
    assert (blocked, idle) == (0, 27 * H - 8_000)
    assert iv.union_ms(calls) == 16_000  # active never includes the gap


def test_leaf_interval_outside_segments_widens_wall():
    # active <= wall must hold even when an adapter's segment misses a call
    assert iv.wall_ms([(0, 10)], [(5, 30)]) == 30


def test_blocked_only_counts_gap_covered_by_children():
    own = [(0, 10), (50, 60), (100, 110)]
    child = [(20, 70), (65, 80)]  # covers 20..50 in gap 1; 60..80 in gap 2
    assert iv.blocked_idle(own, child) == (30 + 20, 10 + 20)


def test_inverted_interval_asserts():
    with pytest.raises(AssertionError):
        iv.union_ms([(10, 5)])
