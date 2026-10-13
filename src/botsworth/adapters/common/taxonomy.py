"""CONTRACT: the action taxonomy shared by every adapter (design §4.10, D17, R-outline).

GUARANTEES:
- `VERBS[provider][tool_name]` is the design §4.10 verb table keyed by the raw
  provider tool name; `verb_of(provider, tool_name)` returns that verb, or
  `other` for a tool the table does not name (the raw name is kept by the
  caller, nothing is dropped).
- `unwrap`, `command_norm`, `command_key` are re-exported from
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

from botsworth.adapters.common.commands import command_key, command_norm, unwrap
from botsworth.model import SUMMARY_MAX, Verb

__all__ = ["VERBS", "clip", "command_key", "command_norm", "rel", "summary", "unwrap", "verb_of"]

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
    },
}


def verb_of(provider: str, tool_name: str | None) -> Verb:
    return VERBS[provider].get(tool_name or "", Verb.OTHER)  # FALLBACK-OK: design §4.10 unknown -> other


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
