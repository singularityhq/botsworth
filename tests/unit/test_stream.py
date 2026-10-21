import json

import pytest

from botsworth import stream
from botsworth.model import Bundle, Event, Op, SourceFile, Step, SubItem
from botsworth.stream import StreamDefects, StreamError

S = "claude_code/s1/session/-"
T = "claude_code/s1/turn/main#0"
L = "claude_code/s1/llm_call/r1"
C = "claude_code/s1/tool_call/t1"
SRC = "src://claude_code/p/s1.jsonl#L1-L1"


def op(op_id, kind, parent, **kw):
    return Op(op_id=op_id, kind=kind, parent=parent, provider="claude_code", session_id="s1", src=SRC, **kw)


def good_bundle():
    return Bundle(
        provider="claude_code",
        bundle_id="s1",
        session_id="s1",
        source_files=[
            SourceFile(provider="claude_code", path="p/s1.jsonl", size=10, mtime_ns=1, sha_first_4k="ab")
        ],
        ops=[
            op(
                S,
                "session",
                None,
                label="s1",
                title="t",
                git_branch="main",
                harness_version="2.1",
                first_line_ms=7,
            ),
            op(T, "turn", S, segments=[[1, 5], [9, 12]]),
            op(L, "llm_call", T, in_tok=10, out_tok=2, dur_ms=5, cost_usd=0.1, cost_basis="computed"),
            op(
                C,
                "tool_call",
                L,
                tool_name="Bash",
                dur_ms=3,
                caused_by=L,
                caused_by_basis="content_hash+cycle",
            ),
        ],
        steps=[
            Step(turn_id=T, ordinal=0, op_id=L, verb="think", src=SRC, outcome="ok"),
            Step(
                turn_id=T,
                ordinal=1,
                op_id=C,
                verb="run",
                src=SRC,
                outcome="exit:1",
                sub_items=[SubItem(tool="tools.read", target="a.py", outcome="ok")],
            ),
        ],
        events=[
            Event(
                event_id="e1", kind="compaction", ts_ms=1, session_id="s1", src=SRC, op_id=T, fields={"x": 1}
            )
        ],
    )


def kinds(bundle):
    return sorted(d.kind for d in stream.defects(bundle))


def test_valid_bundle_has_no_defects_and_validate_passes():
    b = good_bundle()
    assert stream.defects(b) == []
    stream.validate(b)


def test_write_read_round_trip(tmp_path):
    b = good_bundle()
    path = stream.write_bundle(b, tmp_path)
    assert path.name == "s1.jsonl" and [p.name for p in tmp_path.iterdir()] == ["s1.jsonl"]
    back = stream.read_bundle(path)
    assert (back.ops, back.steps, back.events, back.source_files) == (
        b.ops,
        b.steps,
        b.events,
        b.source_files,
    )
    assert (back.provider, back.session_id, back.dropped_fields) == ("claude_code", "s1", {})
    stream.write_bundle(back, tmp_path)  # replace in place
    assert [p.name for p in tmp_path.iterdir()] == ["s1.jsonl"]


def test_each_defect_class_detected():
    b = good_bundle()
    b.ops.append(op("claude_code/s1/session/-x", "session", None))
    assert kinds(b) == ["session_count"]

    b = good_bundle()
    b.ops.append(op(C, "tool_call", L))
    assert kinds(b) == ["duplicate_op_id"]

    b = good_bundle()
    b.ops[1].parent = "nope"
    b.ops[3].caused_by = "nope"
    b.steps[0].op_id = "nope"
    b.events[0].op_id = "nope"
    assert kinds(b) == ["dangling_ref"] * 4

    b = good_bundle()
    b.ops[1].in_tok = 5
    b.ops[0].cost_usd = 0.0
    b.ops[1].dur_ms = 4
    assert kinds(b) == ["container_metric"] * 3

    b = good_bundle()
    b.ops[1].title = "x"
    b.ops[2].git_branch = "x"
    b.ops[3].harness_version = "x"
    b.ops[2].first_line_ms = 1
    assert kinds(b) == ["session_only_field"] * 4

    b = good_bundle()
    b.ops[2].segments = [[1, 2]]
    b.ops[3].segments = [[1, 2]]
    assert kinds(b) == ["leaf_segments"] * 2

    b = good_bundle()
    b.steps[1].ordinal = 2
    assert kinds(b) == ["ordinal_gap"]

    b = good_bundle()
    b.ops[0].label = "x" * 121
    b.steps[0].summary = "x" * 121
    b.steps[0].snippet = "x" * 161
    b.steps[0].target = "x" * 201
    assert kinds(b) == ["over_length"] * 4

    b = good_bundle()
    b.ops[2].time_prov = "span"
    b.ops[2].cost_basis = "computed_nominal"
    b.ops[3].caused_by_basis = "fuzzy"
    b.steps[0].verb = "ponder"
    b.steps[0].outcome = "exit:x"
    b.events[0].kind = "limit_snapshot"
    assert kinds(b) == ["bad_enum"] * 6


def test_bounds_are_inclusive():
    b = good_bundle()
    b.ops[0].label = "x" * 120
    b.steps[0].snippet = "x" * 160
    b.steps[0].target = "x" * 200
    assert stream.defects(b) == []


def test_validate_raises_with_exact_list():
    b = good_bundle()
    b.ops[1].parent = "nope"
    with pytest.raises(StreamDefects) as exc:
        stream.validate(b)
    assert exc.value.defects == stream.defects(b)


def test_duplicate_of_may_name_another_bundle():
    b = good_bundle()
    b.ops[2].duplicate_of = "claude_code/other/llm_call/r1"
    assert stream.defects(b) == []


def write_lines(path, rows, tail=None):
    text = "".join(json.dumps(r) + "\n" for r in rows)
    path.write_text(text + (tail or ""))
    return path


def test_partial_trailing_line_and_malformed_lines_counted(tmp_path):
    rows = [stream.to_dict(r) for r in (*good_bundle().ops,)]
    path = write_lines(tmp_path / "s1.jsonl", rows[:2] + ["x"], tail='{"rec": "op", "op_id": "half')
    path.write_text(path.read_text().replace('"x"\n', "{not json\n"))
    b = stream.read_bundle(path)
    assert (b.n_partial, b.n_malformed, len(b.ops)) == (1, 1, 2)


def test_complete_trailing_line_without_newline_is_still_partial(tmp_path):
    rows = [stream.to_dict(r) for r in good_bundle().ops]
    path = write_lines(tmp_path / "s1.jsonl", rows[:-1], tail=json.dumps(rows[-1]))
    b = stream.read_bundle(path)
    assert (b.n_partial, len(b.ops)) == (1, 3)


def test_unknown_rec_rejected(tmp_path):
    rows = [stream.to_dict(r) for r in good_bundle().ops] + [{"rec": "fact", "kind": "x"}]
    with pytest.raises(StreamError):
        stream.read_bundle(write_lines(tmp_path / "s1.jsonl", rows))


def test_legacy_names_normalized(tmp_path):
    rows = [
        {"rec": "source_file", "path": "/abs/x.jsonl", "size": 3, "mtime": 1757945214627, "sha_head": "aa"},
        {
            "rec": "op",
            "op_id": S,
            "kind": "session",
            "parent": None,
            "provider": "claude-code",
            "session_id": "s1",
            "src": SRC,
            "time_prov": "span",
            "tokens": None,
            "cost_basis": None,
            "metric_provenance": {"tokens": "missing", "time": "derived"},
            "boundary_basis": "x",
        },
        {
            "rec": "op",
            "op_id": L,
            "kind": "llm_call",
            "parent": S,
            "provider": "codex",
            "session_id": "s1",
            "src": SRC,
            "tokens": {"in": 1, "out": 2, "cache_read": 3, "cache_write_5m": 4, "cache_write_1h": None},
            "provenance": {"tokens": "present", "time": "derived"},
            "cost_basis": "computed_nominal",
            "price_version": "fp1",
        },
        {
            "rec": "op",
            "op_id": C,
            "kind": "llm_call",
            "parent": S,
            "provider": "omp",
            "session_id": "s1",
            "src": SRC,
            "tokens": {"input": 5, "output": 6, "cache_read": 0, "cache_write_5m": 1, "cache_write_1h": 2},
            "ttft_provenance": "present",
        },
        {
            "rec": "event",
            "kind": "limit_hit",
            "ts_ms": 9,
            "session_id": "s1",
            "op_id": None,
            "src": SRC,
            "window": "5h",
            "provider": "claude_code",
            "project": "/p",
            "reset_text": "resets 6am",
        },
        {"rec": "event", "kind": "limit_hit", "ts_ms": 10, "src": SRC, "window": "weekly"},
    ]
    b = stream.read_bundle(write_lines(tmp_path / "s1.jsonl", rows))
    session, llm, omp = b.ops
    assert (
        session.provider == "claude_code" and session.time_prov == "derived" and session.tok_prov == "missing"
    )
    assert session.in_tok is None and session.cost_basis is None
    assert (llm.in_tok, llm.out_tok, llm.cache_read_tok, llm.cache_write_tok, llm.cache_write_5m_tok) == (
        1,
        2,
        3,
        4,
        4,
    )
    assert (llm.tok_prov, llm.time_prov, llm.cost_basis, llm.price_fingerprint) == (
        "present",
        "derived",
        "computed",
        "fp1",
    )
    assert (omp.in_tok, omp.out_tok, omp.cache_write_tok, omp.ttft_prov) == (5, 6, 3, "present")
    e0, e1 = b.events
    assert e0.fields == {"window": "session", "reset_text": "resets 6am"}
    assert (
        e0.event_id == "claude_code/s1/event/limit_hit#0"
        and e1.event_id == "claude_code/s1/event/limit_hit#1"
    )
    assert e1.session_id == "s1"
    sf = b.source_files[0]
    assert (sf.provider, sf.sha_first_4k, sf.mtime_ns) == ("claude_code", "aa", 1757945214627 * 1_000_000)
    assert b.dropped_fields == {
        "boundary_basis": 1,
        "provider": 1,
        "project": 1,
    }  # event provider/project: session-derived
    assert stream.defects(b) == []


def test_read_bundles_skips_underscore_files(tmp_path):
    stream.write_bundle(good_bundle(), tmp_path)
    (tmp_path / "_summary.jsonl").write_text("{not json\n")
    assert [b.bundle_id for b in stream.read_bundles(tmp_path)] == ["s1"]


def test_segments_carried_through_write_and_read(tmp_path):
    back = stream.read_bundle(stream.write_bundle(good_bundle(), tmp_path))
    assert back.ops[1].segments == [[1, 5], [9, 12]] and back.ops[0].segments is None
    assert "segments" not in back.dropped_fields


def test_session_metadata_carried_through_write_and_read(tmp_path):
    back = stream.read_bundle(stream.write_bundle(good_bundle(), tmp_path))
    s = back.ops[0]
    assert (s.title, s.git_branch, s.harness_version, s.first_line_ms) == ("t", "main", "2.1", 7)


@pytest.mark.parametrize("field", ["provider", "session_id"])
def test_session_op_without_identity_raises_stream_error(tmp_path, field):
    rows = [stream.to_dict(r) for r in good_bundle().ops]
    del rows[0][field]
    with pytest.raises(StreamError):
        stream.read_bundle(write_lines(tmp_path / "s1.jsonl", rows))


# --- v7 fields (DM-13, DM-20, DM-26, DM-40) ----------------------------------------------------------


def v7_bundle():
    b = good_bundle()
    b.ops[0].title_source = "rename"
    b.ops[0].entrypoint = "cli"
    b.ops[0].originator = "codex_exec"
    b.ops[1].turn_kind = "human"
    b.ops[1].turn_origin = "typed"
    b.ops[3].user_wait = "permission"
    b.source_files[0].ts_list = [5, 5, 9, 12]
    return b


def test_v7_fields_round_trip(tmp_path):
    b = v7_bundle()
    assert stream.defects(b) == []
    back = stream.read_bundle(stream.write_bundle(b, tmp_path))
    session, turn, _, call = back.ops
    assert (session.title_source, session.entrypoint, session.originator) == ("rename", "cli", "codex_exec")
    assert (turn.turn_kind, turn.turn_origin, call.user_wait) == ("human", "typed", "permission")
    assert back.source_files[0].ts_list == [5, 5, 9, 12]


def test_bad_turn_kind_bad_enum():
    b = v7_bundle()
    b.ops[1].turn_kind = "robot"
    b.ops[0].title_source = "guessed"
    assert kinds(b) == ["bad_enum"] * 2


def test_bad_user_wait_bad_enum():
    b = v7_bundle()
    b.ops[3].user_wait = "maybe"
    assert kinds(b) == ["bad_enum"]


def test_turn_kind_on_llm_call_kind_field():
    b = v7_bundle()
    b.ops[2].turn_kind = "human"
    b.ops[0].turn_origin = "typed"
    b.ops[1].user_wait = "ask"  # a user_wait on a turn
    assert kinds(b) == ["kind_field"] * 3
    b = v7_bundle()
    b.ops[2].title_source = "rename"  # session-only field on an llm_call
    assert kinds(b) == ["session_only_field"]


def test_ts_list_round_trip_and_unsorted_rejected(tmp_path):
    b = v7_bundle()
    b.source_files[0].ts_list = []
    assert stream.defects(b) == []
    for bad in ([3, 2], [1, "2"], [1.5, 2], [True, 2]):
        b.source_files[0].ts_list = bad
        found = stream.defects(b)
        assert [d.kind for d in found] == ["bad_enum"], bad
        assert found[0].ref == "p/s1.jsonl"
    b.source_files[0].ts_list = None  # absent stays valid
    assert stream.defects(b) == []
