"""CONTRACT: Codex tool calls -> step verb, target, summary, outcome (design §4.10, R-outline).

GUARANTEES:
- `classify(name, args, project)` returns a `ToolView` for a tool call from its
  own record: verb via `taxonomy.verb_of("codex", name)` (MCP tools whose name
  contains `fetch` -> `fetch`), target (project-relative path, command head,
  agent type, URL/query, or tool name for `other`), summary <= 120, the raw
  command, `command_norm`, `command_key` (D17) and `command_class` (DM-28, D55) for every run.
- `apply_patch` yields one target per changed path (`patch_paths`), so the
  caller emits one edit step per path.
- A code-mode `exec` script is ONE run step: `exec_script` lists its inner
  `tools.*` calls as `sub_items` `{tool, target, outcome}` in order, and the
  shell commands it runs.
- `parsed_label(parsed_cmd, project)` is the run label from Codex's own
  `exec_command_end.parsed_cmd` (preferred over a command head, R-outline);
  `parsed_verb` maps it to read | search | run.
- `skill_reads(command)` (DM-31) lists SKILL.md loads: the command is split at `&&`, `||`, `;`, `|`
  outside quotes; a segment loads when its program is cat, sed (no -i), head, tail, nl, less or
  bat and it mentions the path other than as a redirect target.
- `exit_from_output(text)` returns the exit code printed in a tool output, or None.
EXPECTS: `args` is the decoded `arguments`/`input` (dict, or str for custom tools).
FAILURE BEHAVIOR: unparsable JS string escapes keep the raw literal.
DOES NOT: pair results; set times; split compound shell commands (deferred).
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from botsworth.adapters.common.taxonomy import (
    clip,
    command_class,
    command_key,
    command_norm,
    rel,
    summary,
    unwrap,
    verb_of,
)
from botsworth.model import SUMMARY_MAX, TARGET_MAX, SubItem, Verb

RUN_TOOLS = frozenset({"exec_command", "shell", "local_shell", "local_shell_call", "container.exec"})
SPAWN_TOOLS = frozenset({"spawn_agent"})
PARSED_VERB = {"read": Verb.READ, "search": Verb.SEARCH, "list_files": Verb.SEARCH}
EXIT_RE = re.compile(r"(?:Exit code|exited with code|Process exited with code)[: ]+(-?\d+)")
PATCH_RE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.M)
PATCH_ANY_RE = re.compile(r"\*\*\* (?:Update|Add|Delete) File: ([^\\\n\"]+)")
CMD_RE = re.compile(r'\bcmd\s*:\s*(?:"((?:[^"\\]|\\.)*)"|`((?:[^`\\]|\\.)*)`|\'((?:[^\'\\]|\\.)*)\')')
INNER_RE = re.compile(r"tools\.(\w+)\s*\(")
EXIT_CODE_JSON_RE = re.compile(r'\\?"exit_code\\?"\s*:\s*(-?\d+)')
SKILL_PATH_RE = re.compile(r"/skills/([^/\s'\"]+)/SKILL\.md")


@dataclass(slots=True)
class ToolView:
    verb: str
    target: str | None
    summary: str
    command: str | None = None
    command_norm: str | None = None
    command_key: str | None = None
    command_class: str | None = None  # DM-28, from the full command
    sub_items: list[SubItem] | None = None
    edit_targets: list[str | None] = field(default_factory=list)


def _target(value: object) -> str | None:
    return clip(value, TARGET_MAX)


def patch_paths(text: str, project: str | None) -> list[str]:
    return [rel(p.strip(), project) for p in PATCH_RE.findall(text)]


def exec_script(js: str, project: str | None) -> tuple[list[SubItem], list[str]]:
    subs: list[SubItem] = []
    cmds: list[str] = []
    hits = list(INNER_RE.finditer(js))
    for i, m in enumerate(hits):
        seg = js[m.end() : hits[i + 1].start() if i + 1 < len(hits) else len(js)]
        tool, target = m.group(1), None
        if tool == "exec_command":
            c = CMD_RE.search(seg)
            if c:
                lit = next(g for g in c.groups() if g is not None)
                try:
                    raw = json.loads(f'"{lit}"') if c.group(1) is not None else lit
                except json.JSONDecodeError:
                    raw = lit  # FALLBACK-OK: design §4.10 R-outline — JS escapes that are not JSON; keep raw
                cmds.append(raw)
                target = _target(command_norm(raw, project))
        elif tool == "apply_patch":
            target = _target(", ".join(rel(x.strip(), project) for x in PATCH_ANY_RE.findall(seg)))
        subs.append(SubItem(tool=tool, target=target, outcome="unknown"))
    return subs, cmds


def _run(raw: object, project: str | None) -> ToolView:
    norm = command_norm(raw, project)
    text = unwrap(raw)
    return ToolView(
        verb=Verb.RUN,
        target=_target(norm.split(" ")[0]) if norm else None,
        summary=clip(norm, SUMMARY_MAX) or "run",
        command=clip(text, 2000) if text else None,
        command_norm=norm,
        command_key=command_key(raw),
        command_class=command_class(raw),
    )


def base_name(name: str | None) -> str:
    """Tool name without a `namespace.` prefix (`multi_agent_v1.spawn_agent` -> `spawn_agent`)."""
    return (name or "").rsplit(".", 1)[-1]


def classify(name: str, args: Any, project: str | None) -> ToolView:
    a = args if isinstance(args, dict) else {}
    if name != "container.exec":
        name = base_name(name)
    if name in RUN_TOOLS:
        return _run(a.get("cmd") or a.get("command"), project)
    if name == "exec" and isinstance(args, str):
        subs, cmds = exec_script(args, project)
        raw = "; ".join(cmds) or None
        first = next((x.target for x in subs if x.target), None)
        return ToolView(
            verb=Verb.RUN,
            target=_target(first.split(" ")[0]) if first else None,
            summary=clip(f"script: {len(subs)} calls" + (f" ({first})" if first else ""), SUMMARY_MAX),
            command=clip(raw, 2000),
            command_norm=command_norm(raw, project),
            command_key=command_key(cmds[0]) if cmds else None,
            command_class=command_class(raw),
            sub_items=subs,
        )
    if name == "apply_patch":
        text = args if isinstance(args, str) else a.get("input") or ""  # FALLBACK-OK: §4.10 no patch text
        paths = patch_paths(text, project)
        return ToolView(
            verb=Verb.EDIT,
            target=_target(", ".join(paths)),
            summary=summary("edit", ", ".join(paths)),
            edit_targets=[_target(p) for p in paths] or [None],
        )
    if name in SPAWN_TOOLS:
        tgt = a.get("agent_type") or a.get("role") or a.get("agent_id") or a.get("target")
        return ToolView(verb=Verb.SPAWN, target=_target(tgt), summary=summary(name, tgt))
    if name == "view_image":
        path = rel(a.get("path"), project)
        return ToolView(verb=Verb.READ, target=_target(path), summary=summary("view", path))
    if name == "request_user_input":
        return ToolView(verb=Verb.ASK_USER, target=None, summary=clip(json.dumps(a), SUMMARY_MAX) or name)
    if name == "write_stdin":
        return ToolView(verb=Verb.RUN, target=None, summary=summary("stdin", a.get("chars")))
    verb = verb_of("codex", name)
    if verb == Verb.OTHER and name.startswith("mcp:") and "fetch" in name:
        verb = Verb.FETCH
    return ToolView(verb=verb, target=_target(name), summary=clip(name, SUMMARY_MAX) or "other")


def web_search(action: dict[str, Any]) -> ToolView:
    what = action.get("query") or action.get("url")
    return ToolView(
        verb=Verb.SEARCH,
        target=_target(what),
        summary=clip(f"web {action.get('type') or 'search'} {what or ''}", SUMMARY_MAX) or "web",
    )


def parsed_verb(parsed_cmd: list[dict[str, Any]]) -> str:
    verbs = {PARSED_VERB.get(c.get("type"), Verb.RUN) for c in parsed_cmd}
    return verbs.pop() if len(verbs) == 1 else Verb.RUN


def parsed_target(parsed_cmd: list[dict[str, Any]], project: str | None) -> str | None:
    first = parsed_cmd[0]
    return _target(
        rel(first.get("path"), project) or first.get("query") or command_norm(first.get("cmd"), project)
    )


def parsed_label(parsed_cmd: list[dict[str, Any]], project: str | None) -> str | None:
    parts = []
    for c in parsed_cmd:
        t, path = c.get("type"), rel(c.get("path"), project)
        if t == "read":
            parts.append(f"read {path or c.get('name') or ''}")
        elif t == "search":
            parts.append(f"search {c.get('query') or ''}" + (f" in {path}" if path else ""))
        elif t == "list_files":
            parts.append(f"list {path or '.'}")
        else:
            parts.append(clip(command_norm(c.get("cmd"), project), 60) or "")
    return clip("; ".join(parts), SUMMARY_MAX)


def exit_from_output(text: str) -> int | None:
    m = EXIT_RE.search(text[:4000])
    return int(m.group(1)) if m else None


def script_exit_codes(output: Any) -> list[int]:
    return [int(c) for c in EXIT_CODE_JSON_RE.findall(json.dumps(output))]


READ_PROGRAMS = frozenset({"cat", "sed", "head", "tail", "nl", "less", "bat"})
SHELLS = frozenset({"bash", "sh", "zsh"})
OPERATORS = ("&&", "||", ";", "|")
IN_PLACE_RE = re.compile(r"^(?:-[A-Za-z]*i[A-Za-z]*|--in-place(?:=.*)?)$")


def split_segments(command: str) -> list[str]:
    """Split at `&&`, `||`, `;`, `|` outside quotes (DM-31)."""
    out, cur, quote, i = [], [], "", 0
    while i < len(command):
        ch = command[i]
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        else:
            op = next((o for o in OPERATORS if command.startswith(o, i)), None)
            if op:
                out.append("".join(cur))
                cur, i = [], i + len(op)
                continue
        cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()  # FALLBACK-OK: DM-31 an unbalanced quote still names a program and a path


def _segment_loads(segment: str) -> list[tuple[str, str]]:
    """Loads in one segment; a `bash -lc '<inner>'` segment is read as its inner command."""
    toks = _tokens(segment)
    while toks and "=" in toks[0] and not toks[0].startswith(("-", "/")):
        toks = toks[1:]  # leading VAR=value assignments
    if not toks:
        return []
    prog = toks[0].rsplit("/", 1)[-1]
    if prog in SHELLS:
        inner = [t for t in toks[1:] if not t.startswith("-")]
        return skill_reads(inner[0]) if inner else []
    if prog not in READ_PROGRAMS:
        return []
    if prog == "sed" and any(IN_PLACE_RE.match(t) for t in toks[1:]):
        return []
    found = []
    for i, tok in enumerate(toks[1:], 1):
        m = SKILL_PATH_RE.search(tok)
        redirected = tok.lstrip("0123456789").startswith(">") or toks[i - 1].lstrip("0123456789") in (
            ">",
            ">>",
        )
        if m and not redirected:
            found.append((m.group(1), m.group(0).lstrip("/")))
    return found


def skill_reads(command: str | None) -> list[tuple[str, str]]:
    """(skill name, SKILL.md path) per DM-31: a segment loads a skill when its program reads the file.

    Programs: cat, sed (without -i), head, tail, nl, less, bat. rg, grep, wc, git, ls, writes and
    redirects into the file do not count. The parsed_cmd `read` rule lives in tree.py.
    """
    out: list[tuple[str, str]] = []
    for seg in split_segments(command or ""):
        for hit in _segment_loads(seg):
            if hit not in out:
                out.append(hit)
    return out
