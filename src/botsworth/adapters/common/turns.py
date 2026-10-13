"""CONTRACT: provider-neutral text helpers for turn boundaries and arrivals (design §4.5).

GUARANTEES:
- `clip(value, limit)` collapses whitespace and returns at most `limit` chars,
  or None when the value is None or blank. It is the one bound used for
  labels (120), summaries (120), snippets (160) and targets (200).
- `envelope_body(text)` returns the body of a message envelope, tried in the
  order `<teammate-message ...>`, `<agent-message ...>`, `<fork-boilerplate>`,
  else `text` without a known relay prefix, stripped. The same
  function is applied to the sending side (tool input) and the arriving side
  (the receiver's first user text), so equal bodies match by content hash.
- `starts_with_any(text, prefixes)` tests the stripped text against the
  prefixes.
EXPECTS: str input (None allowed for `clip`).
FAILURE BEHAVIOR: none.
DOES NOT: decide what a boundary is for a provider (each provider's tree does).
"""

from __future__ import annotations

import re

ENVELOPES = ("teammate-message", "agent-message", "fork-boilerplate")
RELAY_PREFIXES = ("The coordinator sent a message while you were working:",)
_ENVELOPE = [re.compile(rf"<{tag}\b[^>]*>(.*?)</{tag}>", re.S) for tag in ENVELOPES]


def clip(value: object, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())[:limit]
    return text or None


def envelope_body(text: str) -> str:
    for pattern in _ENVELOPE:
        m = pattern.search(text)
        if m:
            return m[1].strip()
    for prefix in RELAY_PREFIXES:
        text = text.removeprefix(prefix)
    return text.strip()


def starts_with_any(text: str, prefixes: tuple[str, ...]) -> bool:
    return text.lstrip().startswith(prefixes)
