"""Q0 Scope (QRY-3): validation names the field; defaults; all-time is the null pair."""

from __future__ import annotations

import pytest

from botsworth.query import QueryError
from botsworth.query.scope import Scope, parse_scope


def test_defaults():
    scope = parse_scope({})
    assert scope == Scope(None, None, "", (), (), (), (), False)


def test_both_days_null_is_all_time():
    scope = parse_scope({"from_day": None, "to_day": None})
    assert (scope.from_day, scope.to_day) == (None, None)


@pytest.mark.parametrize("given", [{"from_day": "2026-10-01"}, {"to_day": "2026-10-01"}])
def test_one_null_day_is_400(given):
    with pytest.raises(QueryError, match="from_day and to_day"):
        parse_scope(given)


def test_from_after_to_is_400():
    with pytest.raises(QueryError, match="after"):
        parse_scope({"from_day": "2026-10-12", "to_day": "2026-10-11"})


def test_single_day_period_is_valid():
    scope = parse_scope({"from_day": "2026-10-12", "to_day": "2026-10-12"})
    assert scope.from_day == scope.to_day == "2026-10-12"


@pytest.mark.parametrize(
    "given,field",
    [
        ({"from_day": "10-01-2026", "to_day": "2026-10-02"}, "from_day"),
        ({"from_day": "2026-10-01", "to_day": "2026-13-02"}, "to_day"),
        ({"from_day": "2026-02-30", "to_day": "2026-03-01"}, "from_day"),
        ({"from_day": 20261001, "to_day": "2026-10-02"}, "from_day"),
        ({"providers": "codex"}, "providers"),
        ({"models": [1]}, "models"),
        ({"q": None}, "q"),
        ({"show_automated": 1}, "show_automated"),
        ({"fromday": "2026-10-01"}, "fromday"),
    ],
)
def test_bad_value_names_field(given, field):
    with pytest.raises(QueryError, match=field):
        parse_scope(given)


def test_lists_become_tuples_and_scope_is_hashable():
    scope = parse_scope({"providers": ["codex", "omp"], "q": "rbac opus", "show_automated": True})
    assert scope.providers == ("codex", "omp") and scope.show_automated is True
    assert hash(scope) == hash(
        parse_scope({"providers": ("codex", "omp"), "q": "rbac opus", "show_automated": True})
    )
