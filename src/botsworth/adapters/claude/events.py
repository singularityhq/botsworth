"""CONTRACT: Claude Code events (design §4.11, D15, D16).

GUARANTEES:
- Events come only from lines a transcript owns (not `Line.copy`) and from
  tool calls that are not in-bundle copies, so a fork never repeats its
  parent's events.
- `compaction`: one per `system/compact_boundary` record; fields `trigger`
  (manual|auto|unknown), `pre_tokens`, `post_tokens`, `duration_ms` from
  `compactMetadata`; basis present.
- `api_error`: one per `isApiErrorMessage` record; fields `kind`
  (rate_limit|overloaded|error), `status` (record field or parsed from the
  text), `message_head` (<= 160); basis present.
- `limit_hit`: an api_error whose text says a usage/session/weekly limit was
  hit (or `error: rate_limit`) also yields a raw limit_hit (D16): `window`
  (session|weekly|None), `resets_at_ms` (explicit `resetsAt`, else parsed
  "resets 2:20am (Zone)" as the next such local time), `tz`, `reset_text`;
  basis derived.
- `skill_invocation` (D15: events only): a `Skill` tool call (`via` tool), a
  Read of `*/skills/<name>/SKILL.md` (`via` read, `skill_md_src` = the read's
  pointer), a `<command-name>` user record whose expanded prompt follows
  (`via` slash; a built-in command such as `/clear` emits none, DM-21); fields `skill`,
  `args_head`, `via`, `skill_md_src`, `step_id` (the skill step
  `turn_id#ordinal`; for a slash command the first step of the turn it opens,
  else None, DM-30).
- `op_id` = the enclosing llm_call when the line belongs to one, else the
  turn; for skill tool events the tool_call. `event_id` =
  `claude_code/<sid>/event/<kind>/<agent or main>:<line>[.<block>]` — stable
  while the file is append-only. `account` = `anthropic:default`.
EXPECTS: steps built (for `step_id`).
FAILURE BEHAVIOR: unknown time zone in a reset text -> `resets_at_ms`/`tz`
  None.
DOES NOT: estimate limit windows (D16); attribute cost to skills (D15).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from botsworth.adapters.claude.calls import Leaves
from botsworth.adapters.claude.records import message, ts_ms
from botsworth.adapters.claude.steps import skill_of
from botsworth.adapters.claude.tree import (
    ACCOUNT,
    PROVIDER,
    Ctx,
    Tree,
    arrival_text,
    expanded_prompt_follows,
)
from botsworth.adapters.common.turns import clip
from botsworth.model import SNIPPET_MAX, TARGET_MAX, Event, EventBasis, EventKind, Step

_COMMAND = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_STATUS = re.compile(r"(?:API Error:|status[ :]+)\s*(\d{3})", re.I)
_LIMIT = re.compile(r"hit your (?:session |weekly )?limit|usage limit", re.I)
_RESET = re.compile(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(([^)]+)\)", re.I)


def _event(
    ctx: Ctx, kind: EventKind, where: str, ts: int | None, op_id: str | None, src: str, basis, /, **fields
):
    return Event(
        event_id=f"{PROVIDER}/{ctx.sid}/event/{kind}/{where}",
        kind=kind,
        session_id=ctx.sid,
        src=src,
        ts_ms=ts,
        op_id=op_id,
        account=ACCOUNT,
        basis=basis,
        fields=fields,
    )


def _text(rec: dict[str, Any]) -> str:
    content = message(rec).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text") or "" for b in content if isinstance(b, dict))
    return ""


def _status(rec: dict[str, Any], text: str) -> int | None:
    status = rec.get("statusCode") or rec.get("status")
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    if isinstance(status, str) and status.isdigit():
        return int(status)
    m = _STATUS.search(text)
    return int(m[1]) if m else None


def _error_kind(rec: dict[str, Any], status: int | None, text: str) -> str:
    if rec.get("error") == "rate_limit" or status == 429 or _LIMIT.search(text):
        return "rate_limit"
    if status == 529 or "overloaded" in text.lower():
        return "overloaded"
    return "error"


def reset_ms(text: str, ts: int | None, explicit: Any) -> tuple[int | None, str | None]:
    """(resets_at_ms, tz) of a limit-hit text."""
    if isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
        return (int(explicit * 1000) if explicit < 10**11 else int(explicit)), None
    if isinstance(explicit, str):
        return ts_ms(explicit), None
    m = _RESET.search(text)
    if not m or ts is None:
        return None, None  # FALLBACK-OK: design §4.11 limit_hit derived; no reset text -> null
    try:
        zone = ZoneInfo(m[4])
    except (ZoneInfoNotFoundError, ValueError):
        return None, None  # FALLBACK-OK: design §4.11 limit_hit derived; unknown zone -> null
    hour = int(m[1]) % 12 + (12 if m[3].lower() == "pm" else 0)
    now = datetime.fromtimestamp(ts / 1000, zone)
    reset = now.replace(hour=hour, minute=int(m[2] or 0), second=0, microsecond=0)
    if reset <= now:
        reset += timedelta(days=1)
    return int(reset.timestamp() * 1000), m[4]


def _window(text: str) -> str | None:
    low = text.lower()
    if "week" in low:
        return "weekly"
    if "session limit" in low or ("5" in text and "hour" in low):
        return "session"
    return None


def _step_id(step: Step | None) -> str | None:
    return f"{step.turn_id}#{step.ordinal}" if step else None


def build(ctx: Ctx, tree: Tree, leaves: Leaves, steps: list[Step]) -> list[Event]:
    out: list[Event] = []
    step_of = {s.op_id: s for s in steps if s.verb == "skill"}
    first_step = {s.turn_id: s for s in steps if s.ordinal == 0}  # DM-30: a slash event's step
    for use in leaves.tools:
        skill = skill_of(use.name, use.inp)
        if skill is None or use.duplicate:
            continue
        op, step = use.op, step_of.get(use.op.op_id)
        out.append(
            _event(
                ctx,
                EventKind.SKILL_INVOCATION,
                f"{ctx.transcripts[use.ti].owner}:{use.line.n}.{use.bi}",
                op.start_ms,
                op.op_id,
                op.src,
                EventBasis.PRESENT,
                skill=clip(skill[0], TARGET_MAX),
                args_head=clip(use.inp.get("args"), SNIPPET_MAX),
                via=skill[1],
                skill_md_src=op.src if skill[1] == "read" else None,
                step_id=_step_id(step),
            )
        )
    for ti, t in enumerate(ctx.transcripts):
        for li, line in enumerate(t.lines):
            if line.copy:
                continue
            r = line.rec
            ts = ts_ms(r.get("timestamp"))
            rid = r.get("requestId")
            call = leaves.llm_of.get((ti, rid)) if rid else None
            context = call.op_id if call else tree.turn_of[(ti, line.n)]
            where = f"{t.owner}:{line.n}"
            if r.get("type") == "system" and r.get("subtype") == "compact_boundary":
                md = r.get("compactMetadata") or {}  # FALLBACK-OK: design §4.11 compaction fields optional
                trigger = md.get("trigger") if md.get("trigger") in ("manual", "auto") else "unknown"
                out.append(
                    _event(
                        ctx,
                        EventKind.COMPACTION,
                        where,
                        ts,
                        context,
                        ctx.src(ti, line),
                        EventBasis.PRESENT,
                        trigger=trigger,
                        pre_tokens=md.get("preTokens"),
                        post_tokens=md.get("postTokens"),
                        duration_ms=md.get("durationMs"),
                    )
                )
            if r.get("isApiErrorMessage"):
                text = _text(r)
                status = _status(r, text)
                kind = _error_kind(r, status, text)
                out.append(
                    _event(
                        ctx,
                        EventKind.API_ERROR,
                        where,
                        ts,
                        context,
                        ctx.src(ti, line),
                        EventBasis.PRESENT,
                        kind=kind,
                        status=status,
                        message_head=clip(text, SNIPPET_MAX),
                    )
                )
                if _LIMIT.search(text) or r.get("error") == "rate_limit":
                    resets_at, tz = reset_ms(text, ts, r.get("resetsAt"))
                    out.append(
                        _event(
                            ctx,
                            EventKind.LIMIT_HIT,
                            where,
                            ts,
                            context,
                            ctx.src(ti, line),
                            EventBasis.DERIVED,
                            window=_window(text),
                            resets_at_ms=resets_at,
                            tz=tz,
                            reset_text=clip(text, SNIPPET_MAX),
                        )
                    )
            if r.get("type") == "user":
                text = arrival_text(r)
                command = _COMMAND.search(text)
                if command and expanded_prompt_follows(t.lines, li):  # DM-21: built-ins emit no event
                    args = _ARGS.search(text)
                    out.append(
                        _event(
                            ctx,
                            EventKind.SKILL_INVOCATION,
                            where,
                            ts,
                            context,
                            ctx.src(ti, line),
                            EventBasis.PRESENT,
                            skill=clip(command[1].strip().lstrip("/"), TARGET_MAX),
                            args_head=clip(args[1] if args else None, SNIPPET_MAX),
                            via="slash",
                            skill_md_src=None,
                            step_id=_step_id(first_step.get(tree.turn_of[(ti, line.n)])),
                        )
                    )
    return out
