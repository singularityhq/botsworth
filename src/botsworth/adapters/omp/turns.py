"""CONTRACT: omp turn boundary -> ask label, turn kind and origin (DM-20, DM-22, DM-23).

GUARANTEES:
- `ask_text(message)`: the `text` parts of `message.content` (a string, or a list
  of `{type:"text"}` blocks) joined by a space; None when there is none.
- `message_label(message, from_agent)`: `ask_text` clipped to LABEL_MAX
  (`common.turns.clip`); for an agent-attributed or child-transcript message the
  envelope body (`common.turns.envelope_body`) is clipped instead (DM-23).
- `skill_label(details)`: `/` + `details.name` + space + `details.args`, clipped.
- `classify(...)`: DM-20 omp rows. `skill-prompt` -> human/slash;
  `attribution = agent` -> message/agent; `user` or absent -> human/typed; every
  turn of a child transcript -> message/agent; the derived initial turn of the
  main transcript (no boundary record, so no ask) -> harness/empty (D43).
EXPECTS: `message` is the record's `message` dict, `details` its `details` dict.
FAILURE BEHAVIOR: none; a boundary with no text has a None label.
DOES NOT: decide what a boundary is (the adapter walk does), count asks or title sessions.
"""

from __future__ import annotations

from botsworth.adapters.common.turns import clip, envelope_body
from botsworth.model import LABEL_MAX, TurnKind

AGENT = "agent"


def ask_text(message: dict) -> str | None:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None  # FALLBACK-OK: DM-22 / B2c contract, a boundary without text parts has a null label
    texts = [
        c["text"]
        for c in content
        if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str)
    ]
    return " ".join(texts) if texts else None  # FALLBACK-OK: DM-22, no text parts -> null label


def message_label(message: dict, from_agent: bool) -> str | None:
    text = ask_text(message)
    if text is not None and from_agent:
        text = envelope_body(text)
    return clip(text, LABEL_MAX)


def skill_label(details: dict) -> str | None:
    name, args = details.get("name"), details.get("args")
    parts = [
        "/" + name if isinstance(name, str) and name else "/"
    ]  # FALLBACK-OK: DM-22, nameless skill-prompt
    if isinstance(args, str) and args.strip():
        parts.append(args)
    return clip(" ".join(parts), LABEL_MAX)


def classify(*, child: bool, skill_prompt: bool = False, message: dict | None = None) -> tuple[str, str]:
    """`(turn_kind, turn_origin)`; `message=None` and not `skill_prompt` = the derived initial turn."""
    if child:
        return TurnKind.MESSAGE.value, AGENT
    if skill_prompt:
        return TurnKind.HUMAN.value, "slash"
    if message is None:
        return TurnKind.HARNESS.value, "empty"
    if message.get("attribution") == AGENT:
        return TurnKind.MESSAGE.value, AGENT
    return TurnKind.HUMAN.value, "typed"
