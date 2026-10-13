"""Botsworth. `import botsworth` exposes the query API (design §6.1)."""

from botsworth.query import (
    NotFound,
    QueryError,
    availability,
    outline,
    projects,
    query,
    sessions,
    show,
    source,
    steps,
    timeline,
)
from botsworth.store.location import configure

__all__ = [
    "NotFound",
    "QueryError",
    "availability",
    "configure",
    "outline",
    "projects",
    "query",
    "sessions",
    "show",
    "source",
    "steps",
    "timeline",
]
