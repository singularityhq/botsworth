"""CONTRACT: the SQL form of the QRY-37 failure rule (owner Q2c).

The rule has two forms that must agree, and one test holds them together
(`test_detail_view.py::test_failed_sql_equals_tool_status`):
- Python: `rows.tool_status(row, step)` gives `(status, failed)` for one tool row and its first step.
- SQL: `failed_sql(op)` is the same `failed` as a condition over an `ops` row, for counts in the session
  header, the turn list and any view that counts without building rows.

GUARANTEES:
- `failed_sql(op)` (QRY-37, TRN-32): true when `op.is_error = 1`, or `op.exit_code` is not null and not 0,
  or `op.interrupted = 1`, or the outcome of the op's first step (lowest `ordinal`) is `exit:<n>` with n an
  integer other than 0. An outcome such as `exit:`, `exit:x` or `exit:1x` is not `exit:<n>`: it does not
  count. The value is 1 or 0, never NULL. `op` is the table alias of an `ops` row in the surrounding
  query; the default is the table name.
EXPECTS: schema v7 (`steps.op_rowid`, `steps.ordinal`, `steps.outcome`).
FAILURE BEHAVIOR: none; the result is an SQL text.
DOES NOT: run a query, or decide a tool's `status` (that is `rows.tool_status`).
"""

from __future__ import annotations

_DIGITS = "[0-9]"


def failed_sql(op: str = "ops") -> str:
    """The QRY-37 `failed` condition over the `ops` row `op` (see the CONTRACT)."""
    o = f"(SELECT s.outcome FROM steps s WHERE s.op_rowid = {op}.rowid ORDER BY s.ordinal LIMIT 1)"
    exit_n = (
        f"(({o} GLOB 'exit:{_DIGITS}*' AND {o} NOT GLOB 'exit:*[^0-9]*')"
        f" OR ({o} GLOB 'exit:-{_DIGITS}*' AND {o} NOT GLOB 'exit:-*[^0-9]*'))"
        f" AND CAST(substr({o}, 6) AS INTEGER) <> 0"
    )
    return (
        f"COALESCE({op}.is_error = 1 OR ({op}.exit_code IS NOT NULL AND {op}.exit_code <> 0)"
        f" OR {op}.interrupted = 1 OR ({exit_n}), 0)"
    )
