"""CONTRACT: the action taxonomy shared by every adapter (design §4.10, D17, R-outline).

GUARANTEES:
- `VERBS[provider][tool_name]` is the design §4.10 verb table keyed by the raw
  provider tool name; `verb_of(provider, tool_name)` returns that verb, or
  `other` for a tool the table does not name (the raw name is kept by the
  caller, nothing is dropped).
- `effect_of(verb, command)` (DM-28) is the one value of `Effect` for a tool call: `read search fetch skill`
  -> `look`; `write edit` -> `change`; `spawn` -> `run`; `ask-user answer message` -> `message`; `wait` ->
  `wait`; `other` -> `unknown` (every tool the table does not name, MCP tools included); `run` -> the
  whole-command classifier first (`test`, `wait`), else `shell_effect` of the command, `run` when the call
  has no command text. `think` and `say` are no tool call and assert.
- `unwrap`, `command_norm`, `command_key`, `command_class` (D55) are re-exported from
  `botsworth.adapters.common.commands` (D17); this module never copies them.
- `clip(text, n)` collapses whitespace and cuts to at most n chars; empty -> None.
  `rel(path, project)` makes a path under `project` project-relative, else
  returns it unchanged. `summary(verb, target)` is the one label template
  (`<verb> <target>`, <= 120 chars).
EXPECTS: `project` is the session's first cwd (D10) or None.
FAILURE BEHAVIOR: none; pure string functions.
DOES NOT: parse provider records; decide outcomes; read files.
"""

from __future__ import annotations

from botsworth.adapters.common.commands import (
    command_class,
    command_key,
    command_norm,
    shell_effect,
    unwrap,
)
from botsworth.model import SUMMARY_MAX, Effect, Verb

__all__ = [
    "VERBS",
    "clip",
    "command_class",
    "effect_of",
    "command_key",
    "command_norm",
    "rel",
    "summary",
    "unwrap",
    "verb_of",
]

VERBS: dict[str, dict[str, Verb]] = {
    "claude_code": {
        "Read": Verb.READ,
        "NotebookRead": Verb.READ,
        "Write": Verb.WRITE,
        "Edit": Verb.EDIT,
        "MultiEdit": Verb.EDIT,
        "NotebookEdit": Verb.EDIT,
        "Bash": Verb.RUN,
        "Grep": Verb.SEARCH,
        "Glob": Verb.SEARCH,
        "WebSearch": Verb.SEARCH,
        "WebFetch": Verb.FETCH,
        "Agent": Verb.SPAWN,
        "Task": Verb.SPAWN,
        "Skill": Verb.SKILL,
        "AskUserQuestion": Verb.ASK_USER,
        "SendMessage": Verb.MESSAGE,
        "ScheduleWakeup": Verb.WAIT,
        "Monitor": Verb.WAIT,
    },
    "codex": {
        "exec_command": Verb.RUN,
        "shell": Verb.RUN,
        "local_shell": Verb.RUN,
        "local_shell_call": Verb.RUN,
        "container.exec": Verb.RUN,
        "write_stdin": Verb.RUN,
        "exec": Verb.RUN,
        "apply_patch": Verb.EDIT,
        "view_image": Verb.READ,
        "web_search_call": Verb.SEARCH,
        "spawn_agent": Verb.SPAWN,
        "request_user_input": Verb.ASK_USER,
        "send_input": Verb.MESSAGE,
        "wait_agent": Verb.WAIT,
    },
    "omp": {
        "read": Verb.READ,
        "write": Verb.WRITE,
        "edit": Verb.EDIT,
        "bash": Verb.RUN,
        "eval": Verb.RUN,
        "grep": Verb.SEARCH,
        "glob": Verb.SEARCH,
        "find": Verb.SEARCH,
        "web_search": Verb.SEARCH,
        "fetch": Verb.FETCH,
        "task": Verb.SPAWN,
        "ask": Verb.ASK_USER,
        "wait": Verb.WAIT,
    },
}


def verb_of(provider: str, tool_name: str | None) -> Verb:
    return VERBS[provider].get(tool_name or "", Verb.OTHER)  # FALLBACK-OK: design §4.10 unknown -> other


_VERB_EFFECT = {
    Verb.READ: Effect.LOOK,
    Verb.SEARCH: Effect.LOOK,
    Verb.FETCH: Effect.LOOK,
    Verb.SKILL: Effect.LOOK,
    Verb.WRITE: Effect.CHANGE,
    Verb.EDIT: Effect.CHANGE,
    Verb.SPAWN: Effect.RUN,
    Verb.ASK_USER: Effect.MESSAGE,
    Verb.ANSWER: Effect.MESSAGE,
    Verb.MESSAGE: Effect.MESSAGE,
    Verb.WAIT: Effect.WAIT,
    Verb.OTHER: Effect.UNKNOWN,
}


def effect_of(verb: str, command: str | list | None) -> str:
    """The `Effect` of one tool call from its step verb and, for `run`, its raw command."""
    if verb != Verb.RUN:
        assert verb in _VERB_EFFECT, f"verb {verb!r} is no tool call"
        return _VERB_EFFECT[verb]
    text = unwrap(command)
    if not text or not text.strip():
        return Effect.RUN
    return command_class(command) or shell_effect(command)


def clip(text: object, n: int) -> str | None:
    if text is None:
        return None
    return " ".join(str(text).split())[:n] or None


def rel(path: str | None, project: str | None) -> str | None:
    if path and project:
        root = project.rstrip("/") + "/"
        if path.startswith(root):
            return path[len(root) :]
    return path


def summary(verb: str, target: str | None) -> str:
    return clip(f"{verb} {target or ''}", SUMMARY_MAX) or verb
