"""CONTRACT: omp events (design §4.11, D15/D16 narrowing).

GUARANTEES:
- `event_id` = `omp/<session>/event/<kind>/<agent|main>@L<line>[.<part>]`:
  stable while files are append-only (no bundle-wide counter).
- `api_error` from an assistant `stopReason` of `error|aborted`; fields
  `kind, status, message_head` (<= 160). `error_kind`: aborted -> `aborted`;
  status 429 -> `rate_limit`; 503/529 -> `overloaded`; else `error`.
- `compaction` fields `trigger, pre_tokens, post_tokens` from
  `tokensBefore/tokensAfter`; omp records no trigger -> `unknown`.
- `skill_invocation` fields `skill, args_head, via, skill_md_src, step_id`:
  `via=read` for a `read skill://<name>` call, `via=slash` for a
  `custom_message` `skill-prompt` (its `step_id` is the first step of the turn
  it opens, null without one: DM-30). No skill attribution to steps or ops (D15).
- `resume` fields `from_session, copied_ops` (= own session, 0): the first
  timestamped record after a `session_exit`.
- No `limit_state`/`limit_hit` (omp has none, design §4.8).
DOES NOT: decide where events occur (the adapter walk does).
"""

from __future__ import annotations

from typing import Any

from botsworth.model import SNIPPET_MAX, ErrorKind, Event, EventBasis, EventKind

STATUS_KIND = {429: ErrorKind.RATE_LIMIT, 503: ErrorKind.OVERLOADED, 529: ErrorKind.OVERLOADED}


def error_kind(stop: str | None, status: Any) -> str | None:
    if stop == "aborted":
        return ErrorKind.ABORTED.value
    if stop != "error":
        return None
    return STATUS_KIND.get(status, ErrorKind.ERROR).value  # FALLBACK-OK: design §4.11 other status -> error


def event(
    kind: EventKind,
    sid: str,
    agent: str | None,
    line: int,
    /,
    *,
    src: str,
    ts_ms: int | None,
    op_id: str | None,
    part: int | None = None,
    **fields: Any,
) -> Event:
    loc = f"{agent or 'main'}@L{line}" + (f".{part}" if part is not None else "")
    return Event(
        event_id=f"omp/{sid}/event/{kind.value}/{loc}",
        kind=kind.value,
        session_id=sid,
        src=src,
        ts_ms=ts_ms,
        op_id=op_id,
        basis=EventBasis.PRESENT.value,
        fields=fields,
    )


def message_head(text: Any) -> str | None:
    return " ".join(str(text).split())[:SNIPPET_MAX] if text else None
