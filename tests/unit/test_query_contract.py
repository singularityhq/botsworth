"""Wire contract: every response key set equals frontend/src/api/types.ts (+ shapes.WIRE_EXTRA).

The expected key lists are generated from the TS file at test time, so a change
to types.ts without a matching backend change (or the reverse) fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from query_fixture import build

import botsworth
from botsworth.query import analytics
from botsworth.query.shapes import OUTLINE_ROW_KEYS, WIRE_EXTRA

SESSION_EXTRA = frozenset({"title"}) | WIRE_EXTRA["SessionsRow"]
# Legacy v3 wire types frozen from 0d890bd; v4 wire is checked by Q0. Delete when legacy routes go.
# v4 Freshness (QRY-17); legacy_types.ts still holds the three-key v3 shape
FRESHNESS_KEYS = [
    "last_ingest_ms",
    "parser_version",
    "price_fingerprint",
    "schema_version",
    "api_version",
]
TYPES_TS = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "legacy_types.ts"


def _interfaces(text: str) -> dict[str, dict[str, bool]]:
    """interface name -> {field: required} for top-level fields (nested object types skipped)."""
    out: dict[str, dict[str, bool]] = {}
    for m in re.finditer(r"export interface (\w+)\s*\{", text):
        i, depth, body = m.end(), 1, []
        while depth:
            ch = text[i]
            depth += ch == "{"
            depth -= ch == "}"
            body.append(ch if depth == 1 or (depth == 2 and ch == "{") else ("\n" if ch == "\n" else ""))
            i += 1
        fields = {}
        for line in "".join(body).splitlines():
            f = re.match(r"^\s*(\w+)(\?)?\s*:", line)
            if f:
                fields[f.group(1)] = f.group(2) is None
        out[m.group(1)] = fields
    return out


def _unions(text: str) -> dict[str, set[str]]:
    """string-literal union name -> members (`export type X = | 'a' | 'b'`, comments allowed)."""
    out = {}
    for m in re.finditer(r"export type (\w+)\s*=(.*?)(?=\n\s*\n|\nexport )", text, re.S):
        body = re.sub(r"/\*.*?\*/", "", m.group(2), flags=re.S)
        if re.fullmatch(r"[\s|]*(?:'[^']*'[\s|]*)+", body):
            out[m.group(1)] = set(re.findall(r"'([^']*)'", body))
    return out


TS = TYPES_TS.read_text()
IFACE = _interfaces(TS)
UNION = _unions(TS)


def check(name: str, obj: dict, extra: frozenset[str] = frozenset()) -> None:
    fields = IFACE[name]
    allowed = set(fields) | WIRE_EXTRA.get(name, frozenset()) | extra
    required = {k for k, req in fields.items() if req}
    keys = set(obj)
    assert keys <= allowed, f"{name}: keys not in types.ts {sorted(keys - allowed)}"
    assert required <= keys, f"{name}: missing required {sorted(required - keys)}"


def exact(name: str, obj: dict, extra: frozenset[str] = frozenset()) -> None:
    """All fields present (optional ones included): used for records we always emit whole."""
    check(name, obj, extra)
    want = set(IFACE[name]) | WIRE_EXTRA.get(name, frozenset()) | extra
    assert set(obj) == want, f"{name}: {sorted(set(obj) ^ want)}"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = tmp_path_factory.mktemp("store")
    build(d / "store.db", d / "src")
    return d / "store.db"


def test_parser_reads_the_contract():
    assert {"Op", "Step", "ProfEvent", "Row", "QueryResponse", "ShowResponse", "Distribution"} <= set(IFACE)
    assert IFACE["Row"]["metrics"] is True and IFACE["Row"]["op"] is False
    assert "cost_usd" in UNION["MetricName"] and "start_ms" in UNION["MetricName"]
    assert "command_norm" in UNION["GroupKey"]


QUERIES = [
    {"scope": {"op": "claude_code/sA/session/-"}, "select": "children"},
    {"scope": {"all": True}, "select": "sessions", "outliers": {"over": "p90", "with_top_child": True}},
    analytics.usage(bucket="week"),
    analytics.trend(),
    analytics.commands(),
    analytics.distribution(),
    {
        "scope": {"all": True},
        "select": "descendants",
        "group_by": ["command_key", "path_sig"],
        "metrics": ["tokens.reasoning", "blocked_ms"],
    },
    analytics.skill_events("grill-me"),
    analytics.skills(bucket="week"),
    {"scope": {"all": True}, "select": "steps", "metrics": ["dur_ms", "start_ms"]},
    {"scope": {"all": True}, "select": "steps", "group_by": ["verb"]},
]


@pytest.mark.parametrize("q", QUERIES, ids=lambda q: f"{q['select']}-{'+'.join(q.get('group_by', []))}")
def test_query_response_keys(db, q):
    r = botsworth.query(q, db=db)
    check("QueryResponse", r)
    assert list(r["freshness"]) == FRESHNESS_KEYS
    for row in r["availability"]["rows"]:
        assert set(row) == {"metric", "claude_code", "codex", "omp"}
    metric_names = UNION["MetricName"] | WIRE_EXTRA["MetricName"]
    assert set(r["scope_totals"]) <= metric_names
    assert r["rows"], "query must produce rows to check"
    for row in r["rows"]:
        check("Row", row)
        assert set(row["metrics"]) <= metric_names and set(row["prov"]) <= metric_names
        assert set(row["prov"].values()) <= {"present", "derived", "estimated", "missing"}
        if "op" in row:
            exact("Op", row["op"], SESSION_EXTRA if q["select"] == "sessions" else frozenset())
        if row.get("top_child") is not None:
            exact("Op", row["top_child"])
        if "step" in row:
            exact("Step", row["step"])
        if "event" in row:
            exact("ProfEvent", row["event"])
        if "key" in row:
            assert set(row["key"]) <= UNION["GroupKey"] | WIRE_EXTRA["GroupKey"]
        for d in row.get("dist", {}).values():
            exact("Distribution", d)
        if "counts" in row:
            assert set(row["counts"]) <= {"n", "sessions", "first_ms", "last_ms"} and "n" in row["counts"]
        assert isinstance(row.get("share"), (float, int, type(None)))


def test_secondary_response_keys(db):
    show = botsworth.show("claude_code/sA/llm_call/req_a5", db=db)
    check("ShowResponse", show)
    exact("Op", show["op"])
    for a in show["ancestors"]:
        exact("Op", a)
    for e in show["events"]:
        exact("ProfEvent", e)
    assert set(show["children_counts"]) <= set(
        re.findall(r"'(\w+)'", re.search(r"type OpKind = (.*)", TS).group(1))
    )

    tl = botsworth.timeline("claude_code/sA/session/-", db=db)
    check("TimelineResponse", tl)
    for o in [tl["root"], *tl["ops"]]:
        exact("Op", o)

    st = botsworth.steps("claude_code/sA/turn/main#0", db=db)
    check("StepsResponse", st)
    for s in st["steps"]:
        exact("Step", s)

    src = botsworth.source("claude_code/sA/session/-", db=db, roots={"claude_code": db.parent / "src"})
    check("SourceResponse", src)

    ses = botsworth.sessions(db=db)
    check("SessionsResponse", ses)
    for s in ses["sessions"]:
        want = set(IFACE["Op"]) | WIRE_EXTRA["Op"] | {"title"} | WIRE_EXTRA["SessionsRow"]
        assert set(s) == want | WIRE_EXTRA["ListingRow"]  # API-27

    listing = botsworth.projects(db=db)
    assert list(listing["freshness"]) == FRESHNESS_KEYS and set(listing) == {"freshness", "projects"}  # API-7
    assert listing["projects"]
    for p in listing["projects"]:
        exact("ProjectRow", p)

    av = botsworth.availability()
    exact("Availability", av)


def test_outline_keys_match_declared_shape(db):
    # types.ts has no outline type yet (design §5.2 names the fields); the shape is declared in shapes.
    out = botsworth.outline("claude_code/sA/session/-", depth=2, db=db)
    assert set(out) == {"scope", "rows", "freshness"}
    assert set(out["scope"]) == {"id", "display_id", "kind"}
    for row in out["rows"]:
        assert tuple(row) == OUTLINE_ROW_KEYS
