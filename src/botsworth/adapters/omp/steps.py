"""CONTRACT: omp tool calls -> step verb, target, summary, outcome (design §4.10).

GUARANTEES:
- `verb(name, args)`: `read` of a `skill://` path -> `skill`; `eval` whose code
  contains `agent(` -> `spawn`; else the omp table in `common.taxonomy.VERBS`; unknown tools -> `other`
  (the raw name is kept by the caller in `tool_name`).
- `target(name, args, project)` <= 200 chars: a project-relative path (a
  `skill://` URI kept as is), the bash command head, the task agent, a URL
  host, else pattern/query/action/code/name, else the tool name.
- `spawn_targets(args)`: one target per `task` child (`tasks[].name`), so a
  task call yields one spawn step per child; never empty.
- `summary(...)` <= 120: the agent's own intent (`toolCall.intent`, `args.i`)
  for `run` steps, else `"<tool> <target>"`.
- `outcome(...)`: unpaired -> `unknown`; `isError` -> `error`; exit code ->
  `exit:<n>`; else `ok`.
EXPECTS: `args` is the toolCall `arguments` dict.
DOES NOT: build records.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from botsworth.adapters.common.taxonomy import verb_of
from botsworth.model import SUMMARY_MAX, TARGET_MAX, Outcome, Verb

SKILL_SCHEME = "skill://"


def head(value: object, n: int) -> str:
    return " ".join(str(value or "").split())[:n]  # FALLBACK-OK: design §4.10 bounded display text


def _path(args: dict) -> str | None:
    value = args.get("path") or args.get("file_path") or args.get("file")
    if not value and isinstance(args.get("paths"), list):
        value = ", ".join(map(str, args["paths"]))
    return str(value) if value else None


def file_path(args: dict) -> str | None:
    value = args.get("path") or args.get("file_path") or args.get("file")
    return value if isinstance(value, str) and value else None


def target(name: str | None, args: dict, project: str | None) -> str:
    value = _path(args)
    if value:
        if value.startswith(SKILL_SCHEME) or not project:
            return head(value, TARGET_MAX)
        try:
            value = str(Path(value).relative_to(project))
        except ValueError:
            pass  # FALLBACK-OK: design §4.10 target keeps a path outside the project absolute
        return head(value, TARGET_MAX)
    if name == "bash":
        return head(args.get("command"), TARGET_MAX)
    if name == "task":
        agent = args.get("agent") or args.get("agentType") or args.get("subagent_type")
        return head(agent or "agent", TARGET_MAX)
    if isinstance(args.get("url"), str):
        return head(urlparse(args["url"]).hostname, TARGET_MAX)
    for key in ("pattern", "query", "action", "code", "name"):
        if args.get(key):
            return head(args[key], TARGET_MAX)
    return head(name, TARGET_MAX)


def is_skill_read(name: str | None, args: dict) -> bool:
    return name == "read" and (_path(args) or "").startswith(SKILL_SCHEME)


def verb(name: str | None, args: dict) -> Verb:
    if is_skill_read(name, args):
        return Verb.SKILL
    if name == "eval" and "agent(" in str(args.get("code", "")):
        return Verb.SPAWN
    return verb_of("omp", name)


def spawn_targets(args: dict, default: str) -> list[str]:
    tasks = args.get("tasks") if isinstance(args.get("tasks"), list) else []
    names = [head(t.get("name") or t.get("id") or default, TARGET_MAX) for t in tasks if isinstance(t, dict)]
    return names or [default]


def intent(call: dict, args: dict) -> str | None:
    value = call.get("intent") or args.get("i")
    return value if isinstance(value, str) and value.strip() else None


def summary(name: str | None, v: Verb, tgt: str, why: str | None) -> str:
    if v == Verb.RUN and why:
        return head(why, SUMMARY_MAX)
    return head(f"{name} {tgt}", SUMMARY_MAX)


def outcome(paired: bool, is_error: bool, code: int | None) -> str:
    if not paired:
        return Outcome.UNKNOWN.value
    if is_error:
        return Outcome.ERROR.value
    if code is not None:
        return f"exit:{code}"
    return Outcome.OK.value
