"""CONTRACT: the v4 view functions (interfaces.md §2.1, §5.2), one module per owning task.

GUARANTEES:
- Every view function has its final signature from the day this package exists (Q0). `db: Db = None`
  is last; the first parameters follow the §2.1 table.
- A view not built yet carries the `@stub` marker (`fn.__stub__ = True`) and raises
  `NotImplementedError`. The owning task replaces the body and removes the marker.
- `STUBS` names the views that are still stubs; the HTTP layer answers 501 for them.
- Owners: explorer.py (Q1); session.py, stretches.py (Q2a); rows.py, turn.py (Q2b); detail.py, find.py,
  text.py (Q2c); usecases.py (Q3b); review.py (D56). ingest.py and common.py are Q0's and hold real code.
EXPECTS: nothing.
FAILURE BEHAVIOR: see each view's docstring; store state errors are raised by `botsworth.query._conn`.
DOES NOT: hold SQL outside the owning module.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any


def stub[F: Callable[..., Any]](fn: F) -> F:
    """Mark `fn` as not built yet: keep its signature and docstring, replace the body."""

    @functools.wraps(fn)
    def not_built(*args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(f"{fn.__name__} is not built yet")

    not_built.__stub__ = True  # type: ignore[attr-defined]
    return not_built  # type: ignore[return-value]


from botsworth.query.views.detail import detail  # noqa: E402
from botsworth.query.views.explorer import day_series, facets, list_sessions, palette, period_summary  # noqa: E402
from botsworth.query.views.find import find  # noqa: E402
from botsworth.query.views.ingest import ingest_state  # noqa: E402
from botsworth.query.views.review import scope_review, session_review  # noqa: E402
from botsworth.query.views.session import resolve, session_detail, session_turns  # noqa: E402
from botsworth.query.views.turn import agent_rows, histogram, turn_rows  # noqa: E402
from botsworth.query.views.usecases import project_slow, session_breakdown, top_tasks  # noqa: E402

VIEWS: tuple[Callable[..., Any], ...] = (
    period_summary,
    day_series,
    list_sessions,
    facets,
    palette,
    session_detail,
    session_turns,
    turn_rows,
    agent_rows,
    histogram,
    resolve,
    detail,
    find,
    ingest_state,
    project_slow,
    session_breakdown,
    session_review,
    scope_review,
    top_tasks,
)
STUBS: frozenset[str] = frozenset(fn.__name__ for fn in VIEWS if getattr(fn, "__stub__", False))

__all__ = ["STUBS", "VIEWS", "stub", *(fn.__name__ for fn in VIEWS)]
