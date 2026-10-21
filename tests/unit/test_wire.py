"""Q0 wire types: every dataclass of query/wire.py has the key list of interfaces.md §3 and §5.2 (QRY-21)."""

from __future__ import annotations

import dataclasses
import json

import pytest

from botsworth.query import wire

# Name -> ordered JSON keys. Part 1 is copied from the spec's TypeScript (§3.3-§3.9; `extends` bases first);
# part 2 lists the nested object shapes the spec writes inline, and the §5.2 shapes.
SPEC_KEYS: dict[str, list[str]] = {
    "Freshness": [
        "last_ingest_ms",
        "parser_version",
        "price_fingerprint",
        "schema_version",
        "api_version",
    ],
    "Money": ["cost_usd", "cost_lower_bound", "n_unpriced"],
    "TimePart": ["state", "substate", "ms"],
    "TimeSplit": ["parts"],
    "ModelLine": [
        "cost_usd",
        "cost_lower_bound",
        "n_unpriced",
        "model",
        "n_calls",
        "model_ms",
        "speed_tok_s",
        "speed_basis",
        "ttft_ms_mean",
    ],
    "ProjectRef": ["project", "project_name", "color_slot"],
    "PeriodSummary": ["freshness", "first_day", "last_day", "totals", "projects", "empty"],
    "DayBar": [
        "day",
        "from_day",
        "to_day",
        "cost_usd",
        "cost_lower_bound",
        "n_unpriced",
        "tok_total",
        "n_sessions",
        "by_project",
    ],
    "DaySeries": ["freshness", "days"],
    "SessionRow": [
        "project",
        "project_name",
        "color_slot",
        "cost_usd",
        "cost_lower_bound",
        "n_unpriced",
        "session",
        "session_id",
        "provider",
        "name",
        "title_source",
        "start_ms",
        "end_ms",
        "ends_after_days",
        "tok_total",
        "wall_ms",
        "active_ms",
        "is_automated",
        "n_agents",
        "git_branch",
        "activity",
        "day_split",
    ],
    "SessionGroup": [
        "cost_usd",
        "cost_lower_bound",
        "n_unpriced",
        "key",
        "name",
        "color_slot",
        "n_sessions",
        "tok_total",
        "wall_ms",
        "active_ms",
        "period_activity",
        "rows",
    ],
    "SessionPage": ["freshness", "total", "offset", "limit", "stats", "rows", "groups"],
    "Facets": ["freshness", "providers", "models", "branches", "automated"],
    "PaletteResult": ["freshness", "filter_values", "projects", "sessions"],
    "IngestStatus": ["store_exists", "store_state", "freshness", "ingest", "sources", "last_run"],
    "NextTurn": ["turn", "kind", "origin", "label", "display_id"],
    "Stretch": ["start_ms", "end_ms", "dur_ms", "state", "substate", "turn", "next_turn", "running"],
    "SessionDetail": ["freshness", "session"],
    "TurnRow": [
        "cost_usd",
        "cost_lower_bound",
        "n_unpriced",
        "type",
        "turn",
        "display_id",
        "kind",
        "origin",
        "label",
        "start_ms",
        "end_ms",
        "wall_ms",
        "time_split",
        "waiting_ms",
        "cost_main",
        "tok_total",
        "n_llm_calls",
        "models",
        "n_agents",
        "n_skill_loads",
        "n_failed",
        "agent_runs",
    ],
    "GapRow": [
        "start_ms",
        "end_ms",
        "dur_ms",
        "state",
        "substate",
        "turn",
        "next_turn",
        "running",
        "type",
        "id",
    ],
    "TurnList": ["freshness", "items"],
    "FindResult": ["freshness", "kind", "total", "hits", "per_turn"],
    "Resolved": ["freshness", "row", "turn", "depth"],
    "Histogram": ["freshness", "start_ms", "bin_ms", "tail_from_ms", "bins", "marks", "stretches"],
    "CallRow": [
        "cost_usd",
        "cost_lower_bound",
        "n_unpriced",
        "type",
        "id",
        "depth",
        "start_ms",
        "end_ms",
        "dur_ms",
        "time_prov",
        "model",
        "tok",
        "ttft_ms",
        "stop_reason",
        "error_kind",
        "is_final",
        "steps",
    ],
    "ToolRow": [
        "type",
        "id",
        "depth",
        "step_id",
        "tool_name",
        "verb",
        "summary",
        "target",
        "command",
        "command_src",
        "file_path",
        "start_ms",
        "end_ms",
        "dur_ms",
        "time_prov",
        "status",
        "failed",
        "exit_code",
        "is_error",
        "interrupted",
        "skill_load",
        "skill_via",
        "user_wait",
        "max_silence_ms",
        "result_bytes",
        "src",
    ],
    "AgentRow": [
        "cost_usd",
        "cost_lower_bound",
        "n_unpriced",
        "type",
        "id",
        "depth",
        "caused_by",
        "mode",
        "description",
        "agent_names",
        "agent_type",
        "tok_total",
        "n_llm_calls",
        "n_tool_calls",
        "n_errors",
        "models",
        "run_ms",
        "n_rows",
        "runs",
    ],
    "TurnRows": ["freshness", "turn", "rows", "range"],
    "AgentRowPage": ["freshness", "total", "offset", "limit", "rows"],
    "Position": ["session", "turn", "display_id", "row", "depth"],
    "MessageDetail": [
        "freshness",
        "kind",
        "position",
        "turn",
        "display_id",
        "turn_kind",
        "turn_origin",
        "label",
        "text_src",
        "text_bytes",
    ],
    "SourceResponse": ["freshness", "text", "total_bytes", "drift", "file", "locator", "block"],
}

NESTED_KEYS: dict[str, list[str]] = {
    "RowRef": ["type", "id"],
    "FindHit": ["row", "turn", "display_id", "depth", "label", "value", "start_ms"],
    "PeriodTotals": [
        "cost_usd",
        "cost_lower_bound",
        "n_unpriced",
        "tok_total",
        "n_sessions",
        "n_sessions_period",
        "n_automated_hidden",
    ],
    "PeriodProject": [
        "project",
        "project_name",
        "color_slot",
        "cost_usd",
        "cost_lower_bound",
        "n_unpriced",
        "tok_total",
        "n_sessions",
    ],
    "DayCount": ["day", "n_sessions"],
    "PeriodEmpty": ["all_time_matches", "prev_day", "next_day"],
    "DayProject": ["project", "project_name", "color_slot", "cost_usd", "tok_total"],
    "Activity": ["bin_ms", "tok"],
    "DaySplit": ["day", "cost_usd", "tok_total"],
    "PeriodActivity": ["bin_ms", "start_ms", "tok"],
    "SessionStats": ["max_activity_tok", "max_cost_usd", "cost_p90"],
    "FacetValue": ["value", "n"],
    "AutomatedFacet": ["n"],
    "FilterValue": ["kind", "value", "label", "n"],
    "PaletteProject": [
        "project",
        "project_name",
        "color_slot",
        "cost_usd",
        "cost_lower_bound",
        "n_unpriced",
        "n_sessions",
        "n_automated",
    ],
    "PaletteSession": [
        "session",
        "session_id",
        "provider",
        "name",
        "title_source",
        "project",
        "project_name",
        "color_slot",
        "start_ms",
        "cost_usd",
        "cost_lower_bound",
        "n_unpriced",
        "is_automated",
        "first_day",
        "in_period",
        "in_filters",
    ],
    "IngestRunning": ["running", "started_ms", "pid"],
    "IngestSource": ["provider", "root", "files", "bundles", "live", "last_ingest_ms", "rejected"],
    "LastRun": ["started_ms", "ended_ms", "bundles_changed", "rejected"],
    "RunningEngagement": ["row", "agent_name", "start_ms", "end_ms", "cost_usd"],
    "LongestTarget": ["row", "turn", "dur_ms"],
    "Longest": ["model", "tool", "agents", "you", "agentmsg", "limit", "none"],
    "SessionCost": ["cost_usd", "cost_lower_bound", "n_unpriced", "main", "agents", "unattributed"],
    "SessionInfo": [
        "project",
        "project_name",
        "color_slot",
        "session",
        "session_id",
        "provider",
        "name",
        "title_source",
        "cwd",
        "git_branch",
        "harness_version",
        "entrypoint",
        "originator",
        "start_ms",
        "end_ms",
        "ends_after_days",
        "is_automated",
        "n_turns",
        "n_harness_turns",
        "cost",
        "tok_total",
        "wall_ms",
        "active_ms",
        "time_split",
        "longest",
        "n_agents",
        "n_skill_loads",
        "n_failed",
        "n_llm_calls",
        "models",
    ],
    "TurnModel": ["model", "cost_usd"],
    "AgentRun": ["engagement", "agent_name", "start_ms", "end_ms"],
    "HistBin": ["cost_usd", "main_cost_usd", "tok_total", "state_ms"],
    "HistBinByModel": ["cost_usd", "main_cost_usd", "tok_total", "state_ms", "by_model"],
    "TurnStartMark": ["turn", "display_id", "start_ms"],
    "SkillMark": ["row", "start_ms", "skill"],
    "HistMarks": ["skill_loads"],
    "SessionHistMarks": ["turn_starts", "skill_loads"],
    "CallStep": ["step_id", "verb", "summary", "snippet"],
    "AgentRowRun": ["engagement", "agent_name", "start_ms", "end_ms", "wall_ms"],
    "TurnCost": ["cost_usd", "cost_lower_bound", "n_unpriced", "main", "agents"],
    "TurnHeader": [
        "cost_usd",
        "cost_lower_bound",
        "n_unpriced",
        "type",
        "turn",
        "display_id",
        "kind",
        "origin",
        "label",
        "start_ms",
        "end_ms",
        "wall_ms",
        "time_split",
        "waiting_ms",
        "cost_main",
        "tok_total",
        "n_llm_calls",
        "models",
        "n_agents",
        "n_skill_loads",
        "n_failed",
        "agent_runs",
        "cost",
        "models_full",
        "text_src",
        "text_bytes",
        "n_turns_in_session",
    ],
    "RangeAgent": ["row", "cost_usd", "tok_total", "n_calls_in_range", "n_calls"],
    "TurnRange": ["from_ms", "to_ms", "cost", "cost_main", "cost_agents", "time_split", "agents"],
    "CallSection": ["part", "src", "snippet", "bytes", "row"],
    "Neighbours": ["before", "after"],
    "AgentDetailRun": ["engagement", "agent_name", "start_ms", "end_ms", "wall_ms", "ask_src"],
    "OtherCalls": ["n", "cost_usd"],
    # §5.2
    "SlowBlock": [
        "n_sessions",
        "wall_ms",
        "active_ms",
        "time_split",
        "n_llm_calls",
        "n_tool_calls",
        "tok_total",
        "cost",
    ],
    "SlowSession": [
        "session",
        "session_id",
        "name",
        "project_name",
        "start_ms",
        "wall_ms",
        "active_ms",
        "time_split",
        "cost",
        "tok_total",
        "longest_wait",
    ],
    "ToolGroup": [
        "tool_name",
        "command_key",
        "n",
        "n_sessions",
        "dur_ms",
        "p50_ms",
        "p90_ms",
        "max_ms",
        "n_derived",
        "n_no_records",
    ],
    "SlowestTool": [
        "row",
        "session",
        "turn",
        "display_id",
        "tool_name",
        "command_key",
        "summary",
        "dur_ms",
        "time_prov",
        "max_silence_ms",
    ],
    "UserWaitCount": ["n", "dur_ms"],
    "UserWaits": ["ask", "permission"],
    "TestRuns": ["n", "run_ms", "n_failed", "n_passed", "n_no_result", "n_background"],
    "Wait": ["start_ms", "end_ms", "dur_ms", "state", "substate", "turn", "next_turn", "running", "row"],
    "SessionWait": [
        "start_ms",
        "end_ms",
        "dur_ms",
        "state",
        "substate",
        "turn",
        "next_turn",
        "running",
        "row",
        "session",
        "display_id",
    ],
    "SlowReport": [
        "freshness",
        "current",
        "previous",
        "sessions",
        "tools",
        "slowest_tools",
        "user_waits",
        "test_runs",
        "waits",
        "models",
        "review",
    ],
    "BreakdownSession": [
        "session",
        "session_id",
        "name",
        "project_name",
        "provider",
        "start_ms",
        "end_ms",
        "wall_ms",
        "active_ms",
        "cost",
        "tok_total",
        "time_split",
    ],
    "BreakdownAgent": [
        "row",
        "turn",
        "display_id",
        "depth",
        "agent_names",
        "cost_usd",
        "cost_lower_bound",
        "n_unpriced",
        "tok_total",
        "run_ms",
        "n_llm_calls",
    ],
    "SessionBreakdown": [
        "freshness",
        "session",
        "turns_by_time",
        "turns_by_tokens",
        "rows_by_time",
        "rows_by_cost",
        "rows_by_tokens",
        "tools",
        "agents",
        "waits",
        "test_runs",
        "models",
        "review",
    ],
    "CausePart": ["cause", "ms"],
    "Evidence": [
        "session",
        "session_id",
        "name",
        "row",
        "turn",
        "display_id",
        "start_ms",
        "end_ms",
        "dur_ms",
    ],  # fmt: skip
    "FindingSession": ["session", "session_id", "name", "ms"],
    "Finding": ["cause", "ms", "lost", "n", "longest", "threads", "sessions"],
    "Review": ["wall_ms", "lost_ms", "n_sessions", "n_unmeasured", "causes", "findings", "test_runs"],
    "ReviewReport": [
        "freshness",
        "wall_ms",
        "lost_ms",
        "n_sessions",
        "n_unmeasured",
        "causes",
        "findings",
        "test_runs",
    ],
    "TaskExample": ["row", "session"],
    "TaskGroup": [
        "key",
        "n",
        "n_sessions",
        "tok_total",
        "cost_usd",
        "cost_lower_bound",
        "n_unpriced",
        "dur_ms",
        "p50_ms",
        "p90_ms",
        "share",
        "example",
    ],
    "ScopeTotals": ["tok_total", "cost", "dur_ms"],
    "Unassigned": ["tok_total", "cost_usd"],
    "TaskGroups": [
        "freshness",
        "by",
        "measure",
        "basis",
        "scope_totals",
        "unassigned",
        "total_groups",
        "groups",
    ],
}

# Spec types the dataclasses are built from other names (a TS union member, or the same type renamed).
UNION_MEMBERS = {
    "CallDetail": [
        "freshness",
        "kind",
        "position",
        "turn_cost_usd",
        "cost_in_usd",
        "cost_out_usd",
        "cost_cache_read_usd",
        "cost_cache_write_usd",
        "cost_basis",
        "context_tokens",
        "speed_tok_s",
        "speed_basis",
        "sections",
    ],
    "ToolDetail": ["freshness", "kind", "position", "input_src", "output_src", "neighbours"],
    "AgentDetail": [
        "freshness",
        "kind",
        "position",
        "turn_cost_usd",
        "focus_engagement",
        "runs",
        "top_calls",
        "other_calls",
    ],
}


def _keys(cls: type) -> list[str]:
    return [f.name for f in dataclasses.fields(cls)]


def test_every_wire_type_has_spec_keys():
    expected = {**SPEC_KEYS, **NESTED_KEYS}
    expected.pop("Scope", None)  # Scope is an input type (query/scope.py)
    expected.pop("StoreState", None)
    for name, keys in expected.items():
        assert hasattr(wire, name), f"wire.{name} is missing"
        assert _keys(getattr(wire, name)) == keys, name
    # a detail dataclass = its own keys, then the row type's keys without `type`
    for name, own in UNION_MEMBERS.items():
        row = {"CallDetail": "CallRow", "ToolDetail": "ToolRow", "AgentDetail": "AgentRow"}[name]
        rest = [k for k in SPEC_KEYS[row] if k != "type" and not (name == "AgentDetail" and k == "runs")]
        assert _keys(getattr(wire, name)) == own + rest, name


def test_no_wire_dataclass_is_untested():
    classes = {n for n, c in vars(wire).items() if dataclasses.is_dataclass(c)}
    covered = set(SPEC_KEYS) | set(NESTED_KEYS) | set(UNION_MEMBERS)
    assert classes <= covered, sorted(classes - covered)


def test_wire_types_are_frozen():
    fresh = wire.Freshness(1, "p", "f", 8)
    with pytest.raises(dataclasses.FrozenInstanceError):
        fresh.parser_version = "x"  # type: ignore[misc]


def test_fixed_discriminators_are_defaults_in_spec_position():
    money = dict(cost_usd=1.0, cost_lower_bound=False, n_unpriced=0)
    gap = wire.GapRow(1, 2, 1, "none", "none", None, None, [], "gap-1")
    assert (gap.type, list(dataclasses.asdict(gap))[8:]) == ("gap", ["type", "id"])
    tool_keys = list(dataclasses.asdict(_tool()))
    assert tool_keys[0] == "type"
    assert list(dataclasses.asdict(wire.OtherCalls(0, None))) == ["n", "cost_usd"]
    assert wire.Freshness(1, "p", "f", 8).api_version == 1
    assert money  # shape reference for the builders above


def _tool() -> wire.ToolRow:
    return wire.ToolRow(
        id="t", depth=0, step_id=None, tool_name="Bash", verb="run", summary="s", target="x",
        command="ls", command_src=None, file_path=None, start_ms=1, end_ms=2, dur_ms=1, time_prov="present",
        status="ok", failed=False, exit_code=0, is_error=False, interrupted=False, skill_load=None,
        skill_via=None, user_wait=None, max_silence_ms=None, result_bytes=None, src="blob://x",
    )  # fmt: skip


def test_money_is_flat():
    row = wire.TurnRow(
        cost_usd=1.5, cost_lower_bound=True, n_unpriced=2, turn="t", display_id="T1", kind="human",
        origin="user", label="hi", start_ms=1, end_ms=2, wall_ms=1,
        time_split=wire.TimeSplit([wire.TimePart("model", None, 1)]), waiting_ms=0, cost_main=1.5,
        tok_total=10, n_llm_calls=1, models=[], n_agents=0, n_skill_loads=0,
        n_failed=0, agent_runs=[],
    )  # fmt: skip
    body = json.loads(wire.to_json(row))
    assert "money" not in body
    assert list(body)[:4] == ["cost_usd", "cost_lower_bound", "n_unpriced", "type"]
    # a field named `cost: Money` holds the three keys as an object
    rng = wire.TurnRange(1, 2, wire.Money(1.0, False, 0), None, None, wire.TimeSplit([]), [])
    assert list(json.loads(wire.to_json(rng))["cost"]) == ["cost_usd", "cost_lower_bound", "n_unpriced"]


def test_call_tok_uses_the_keyword_key():
    tok: wire.Tok = {
        "in": 1,
        "cache_read": None,
        "cache_write": None,
        "out": 2,
        "reasoning": None,
        "total": 3,
    }
    assert json.loads(wire.to_json({"tok": tok}))["tok"]["in"] == 1


def test_to_json_is_one_line_utf8():
    assert wire.to_json(wire.OtherCalls(2, 0.5)) == b'{"n":2,"cost_usd":0.5}'
    out = wire.to_json({"label": "caf\u00e9 \u2192 line1\nline2"})
    assert "caf\u00e9 \u2192".encode() in out  # UTF-8, not \\u escapes
    assert b"\n" not in out  # a newline inside a string is escaped; the body is one line


def test_to_json_rejects_nan():
    with pytest.raises(ValueError):
        wire.to_json(wire.OtherCalls(1, float("nan")))
