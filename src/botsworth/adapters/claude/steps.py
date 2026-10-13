"""CONTRACT: Claude Code steps — the readable actions of a turn (design §4.10, R-steps, D15, D17).

GUARANTEES:
- One step per tool_call and one per distinct `thinking`/`text` block of an
  llm_call's non-synthetic records (a block repeated across the records of
  one requestId counts once). Steps of a turn are ordered by (line, block
  index) and numbered 0..n-1 (`ordinal`); `turn_id` = the turn that holds
  the step's op.
- `verb` from the raw tool name (table below; unknown tools -> `other` with
  `target` = the tool name); `tool_name` keeps the raw name. A Read of
  `*/skills/<name>/SKILL.md` and the `Skill` tool are `skill` steps with
  `target` = the skill name.
- `answer` = the turn's last text step when no later non-thinking step
  follows it; other text is `say`.
- `run` summary = Bash `description`, else the command head (<= 120);
  `target` = `command_key` (D17), else the first word.
- Text bounds: target <= 200, summary <= 120, snippet <= 160 (invariant 7).
  `skill` is never set (D15). Steps carry no cost.
- `build` also sets each tool_call's `label` to its step summary.
EXPECTS: `Leaves` from calls.build.
FAILURE BEHAVIOR: none (unknown tools and blocks map to `other` / are skipped).
DOES NOT: emit events; parse compound shell commands (R-outline, deferred).
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

from botsworth.adapters.claude.calls import Leaves, ToolUse, result_outcome
from botsworth.adapters.claude.records import is_synthetic, message, ts_ms
from botsworth.adapters.claude.tree import Ctx
from botsworth.adapters.common.taxonomy import verb_of
from botsworth.adapters.common.turns import clip
from botsworth.model import SNIPPET_MAX, SUMMARY_MAX, TARGET_MAX, Step, Verb

_SKILL_MD = re.compile(r"/skills/(.+)/SKILL\.md$")


def skill_of(name: str | None, inp: dict[str, Any]) -> tuple[str, str] | None:
    """(skill, via) for a Skill tool call or a Read of a SKILL.md, else None."""
    if name == "Skill" and isinstance(inp.get("skill"), str) and inp["skill"].strip():
        return inp["skill"], "tool"
    path = inp.get("file_path")
    if name == "Read" and isinstance(path, str):
        m = _SKILL_MD.search(path)
        if m:
            return m[1], "read"
    return None


def _rel(path: str, project: str) -> str:
    root = project.rstrip("/") + "/"
    return path[len(root) :] if path.startswith(root) else path


def describe(
    name: str | None, inp: dict[str, Any], project: str, command_key: str | None
) -> tuple[str, str | None, str]:
    """(verb, target, summary) of one tool call, from its arguments only."""
    verb = verb_of("claude_code", name)
    target: Any = None
    detail: Any = inp.get("description")
    skill = skill_of(name, inp)
    if skill:
        verb, target = Verb.SKILL, skill[0]
        detail = f"{skill[0]} {inp.get('args') or ''}" if skill[1] == "tool" else f"read {skill[0]} SKILL.md"
    elif verb in (Verb.READ, Verb.WRITE, Verb.EDIT):
        path = next(
            (inp[k] for k in ("file_path", "path", "notebook_path") if isinstance(inp.get(k), str)), None
        )
        target = _rel(path, project) if path else None
    elif verb == Verb.RUN:
        command = clip(inp.get("command"), SUMMARY_MAX)
        target = command_key or (command.split()[0] if command else None)
        detail = detail or command
    elif verb == Verb.SEARCH:
        target = inp.get("path") or inp.get("pattern") or inp.get("query")
        detail = inp.get("pattern") or inp.get("query") or target
    elif verb == Verb.FETCH:
        url = inp.get("url")
        target = urlparse(url).netloc if isinstance(url, str) else None
        detail = url
    elif verb == Verb.SPAWN:
        target = inp.get("subagent_type") or inp.get("name") or "agent"
        detail = detail or target
    elif verb == Verb.ASK_USER:
        questions = inp.get("questions")
        first = questions[0] if isinstance(questions, list) and questions else None
        detail = first.get("question") if isinstance(first, dict) else None
    else:
        target = name
    target_text = clip(target, TARGET_MAX)
    summary = clip(detail, SUMMARY_MAX) or clip(f"{verb} {target_text or name or ''}", SUMMARY_MAX) or verb
    return verb, target_text, summary


def _tool_step(ctx: Ctx, use: ToolUse) -> tuple[tuple[int, int], Step]:
    op = use.op
    verb, target, summary = describe(use.name, use.inp, ctx.project, op.command_key)
    op.label = summary
    step = Step(
        turn_id="",  # set by build from the emitting call
        ordinal=-1,
        op_id=op.op_id,
        verb=verb,
        src=op.src,
        tool_name=use.name,
        target=target,
        outcome=result_outcome(use.result)[0],
        summary=summary,
        start_ms=op.start_ms,
        dur_ms=op.dur_ms,
    )
    return (use.line.n, use.bi), step


def build(ctx: Ctx, leaves: Leaves) -> list[Step]:
    by_turn: dict[str, list[tuple[tuple[int, int], Step]]] = defaultdict(list)
    turn_of_op = {c.op.op_id: c.op.parent for c in leaves.calls}
    for use in leaves.tools:
        order, step = _tool_step(ctx, use)
        step.turn_id = turn_of_op[use.op.parent]
        by_turn[step.turn_id].append((order, step))
    for call in leaves.calls:
        seen: set[tuple[str, str]] = set()
        for line in call.lines:
            if is_synthetic(line.rec):
                continue
            content = message(line.rec).get("content")
            if not isinstance(content, list):
                continue
            for bi, block in enumerate(content):
                if not isinstance(block, dict) or block.get("type") not in ("thinking", "text"):
                    continue
                kind = block["type"]
                text = block.get("thinking" if kind == "thinking" else "text") or ""
                if (kind, text) in seen:
                    continue
                seen.add((kind, text))
                think = kind == "thinking"
                step = Step(
                    turn_id=call.op.parent,
                    ordinal=-1,
                    op_id=call.op.op_id,
                    verb=Verb.THINK if think else Verb.SAY,
                    src=ctx.src(call.ti, line, json_path=f"message.content.{bi}"),
                    outcome="interrupted" if call.op.cut_off else "ok",
                    summary="Thinking" if think else "Response",
                    snippet=clip(text, SNIPPET_MAX),
                    start_ms=ts_ms(line.rec.get("timestamp")),
                )
                by_turn[step.turn_id].append(((line.n, bi), step))
    out: list[Step] = []
    for entries in by_turn.values():
        entries.sort(key=lambda e: e[0])
        turn_steps = [s for _, s in entries]
        for ordinal, step in enumerate(turn_steps):
            step.ordinal = ordinal
        last = next((s for s in reversed(turn_steps) if s.verb != Verb.THINK), None)
        if last is not None and last.verb == Verb.SAY:
            last.verb, last.summary = Verb.ANSWER, "Answer"
        out.extend(turn_steps)
    return out
