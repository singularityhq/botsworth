"""CONTRACT: Codex event payloads (design §4.11, D15, D16).

GUARANTEES:
- Each builder returns `RawEvent(kind, fields, basis, json_path)`; `fields` uses
  the §4.11 names for its kind:
  - `limit_states(rate_limits, last)` -> one `limit_state` per window whose
    `(account, window, window_minutes, used_percent, resets_at)` changed since
    the previous snapshot of that window (raw, non-additive, never summed; D16),
    plus a `limit_hit` when `rate_limit_reached_type` is set;
  - `usage_limit_hit()` for `codex_error_info = usage_limit_exceeded`;
  - `compaction(pre_tokens, trigger)` (basis derived: `pre_tokens` = previous
    call `context_tokens`; `post_tokens` filled by the caller from the next call);
  - `api_error(payload)`; `skill(skill, via, md, args_head)` (`via` slash | read;
    Codex has no Skill tool); `resume(session)`; `rollback(dropped_tokens, num_turns)`.
- `account_of(rate_limits)` = `chatgpt-<plan_type>`, else `chatgpt-default` (B2/B3
  key family shared with omp).
EXPECTS: Codex `event_msg` payload dicts.
DOES NOT: attach events to ops or assign ids (tree.py does).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from botsworth.adapters.common.taxonomy import clip
from botsworth.model import SNIPPET_MAX, EventBasis, EventKind

DEFAULT_ACCOUNT = "chatgpt-default"
WINDOWS = ("primary", "secondary")


@dataclass(slots=True)
class RawEvent:
    kind: str
    fields: dict[str, Any]
    basis: str
    json_path: str | None = None


def account_of(rate_limits: dict[str, Any]) -> str:
    plan = rate_limits.get("plan_type")
    return f"chatgpt-{plan}" if plan else DEFAULT_ACCOUNT


def limit_states(rl: dict[str, Any], last: dict[str, tuple]) -> list[RawEvent]:
    """`last` maps window name -> previous state tuple; updated in place."""
    acct = account_of(rl)
    out = []
    for name in WINDOWS:
        w = rl.get(name)
        if not w:
            continue
        resets = w["resets_at"] * 1000 if w.get("resets_at") else None
        state = (acct, name, w.get("window_minutes"), w.get("used_percent"), resets)
        if last.get(name) == state:
            continue
        last[name] = state
        out.append(
            RawEvent(
                EventKind.LIMIT_STATE,
                {
                    "account": acct,
                    "window": name,
                    "window_minutes": w.get("window_minutes"),
                    "used_percent": w.get("used_percent"),
                    "resets_at_ms": resets,
                    "plan_type": rl.get("plan_type"),
                    "limit_id": rl.get("limit_id"),
                },
                EventBasis.PRESENT,
                "payload.rate_limits",
            )
        )
    if rl.get("rate_limit_reached_type"):
        out.append(
            RawEvent(
                EventKind.LIMIT_HIT,
                {"window": rl["rate_limit_reached_type"], "resets_at_ms": None, "tz": None},
                EventBasis.PRESENT,
                "payload.rate_limits",
            )
        )
    return out


def usage_limit_hit() -> RawEvent:
    fields = {"window": None, "resets_at_ms": None, "tz": None}
    return RawEvent(EventKind.LIMIT_HIT, fields, EventBasis.PRESENT)


def compaction(pre_tokens: int | None, trigger: str) -> RawEvent:
    return RawEvent(
        EventKind.COMPACTION,
        {"trigger": trigger, "pre_tokens": pre_tokens, "post_tokens": None},
        EventBasis.DERIVED,
    )


def api_error(payload: dict[str, Any]) -> RawEvent:
    info = payload.get("codex_error_info")
    status = info if isinstance(info, str) else json.dumps(info) if info else payload.get("type")
    kind = "rate_limit" if info == "usage_limit_exceeded" else "error"
    return RawEvent(
        EventKind.API_ERROR,
        {"kind": kind, "status": status, "message_head": clip(payload.get("message"), SNIPPET_MAX)},
        EventBasis.PRESENT,
    )


def skill(name: str, via: str, md: str | None, args_head: str | None = None) -> RawEvent:
    basis = EventBasis.PRESENT if via == "slash" else EventBasis.DERIVED
    return RawEvent(
        EventKind.SKILL_INVOCATION,
        {"skill": name, "args_head": args_head, "via": via, "skill_md_src": md, "step_id": None},
        basis,
    )


def resume(session: str) -> RawEvent:
    return RawEvent(EventKind.RESUME, {"from_session": session, "copied_ops": 0}, EventBasis.PRESENT)


def rollback(dropped_tokens: int | None, num_turns: int | None) -> RawEvent:
    basis = EventBasis.PRESENT if num_turns is not None else EventBasis.DERIVED
    return RawEvent(EventKind.ROLLBACK, {"dropped_tokens": dropped_tokens, "num_turns": num_turns}, basis)
