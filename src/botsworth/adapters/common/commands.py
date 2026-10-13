"""CONTRACT: the two shell-command keys shared by every adapter (D17).

GUARANTEES:
- `unwrap(command)` returns the shell text without a `bash|zsh|sh|dash|ksh -c|-lc`
  wrapper, in argv form (list) or string form (one pair of surrounding quotes
  removed). Other argv lists are joined with spaces.
- `command_norm(command, project)` = the FULL command, normalized: unwrapped,
  one leading `cd X &&` dropped, `<project>/` prefixes removed and a bare
  `<project>` turned into `.`, whitespace collapsed, at most 200 chars.
  `cd /r && uv run pytest tests/x -q` -> `uv run pytest tests/x -q`.
- `command_key(command)` = a coarse grouping key: in the first simple command
  that is not `cd|export|set|source|.` (segments split on `&& || ; |` and
  newlines; leading `VAR=value` skipped), the launcher prefix (`uv run`, `uvx`,
  `npx`, `bunx`, `pnpm exec`, `poetry run`, `python|python3 -m`) kept, stacked launchers included, then the
  program basename, then at most one subcommand word (not a flag, path, quoted,
  `$`, redirect, `=` or dotted word). `cd /r && uv run pytest tests/x -q` ->
  `uv run pytest`; `git commit -m x` -> `git commit`.
- Both return None for an empty or non-string/non-list command.
EXPECTS: `project` is the session's first cwd (D10) or None.
FAILURE BEHAVIOR: none; no shell parsing errors are possible (no shlex).
DOES NOT: split compound commands into sub-actions or detect writes (R-outline, deferred).
KNOWN LIMITATIONS: quoting is not parsed, so a `&&` inside quotes splits a
  segment for `command_key`.
"""

from __future__ import annotations

import re

MAX = 200
_SHELL_ARGV0 = re.compile(r"(?:^|/)(?:ba|z|da|k)?sh$")
_SHELL_WRAP = re.compile(r"^(?:\S*/)?(?:ba|z|da|k)?sh\s+-l?c\s+")
_CD_PREFIX = re.compile(r"^cd\s+\S+\s*&&\s*")
_SEGMENTS = re.compile(r"&&|\|\||;|\||\n")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SKIP = frozenset({"cd", "export", "set", "source", "."})
_LAUNCHERS = (("uv", "run"), ("pnpm", "exec"), ("poetry", "run"), ("python", "-m"), ("python3", "-m"))
_LAUNCHER_1 = frozenset({"uvx", "npx", "bunx"})


def unwrap(command: str | list | None) -> str | None:
    if isinstance(command, list):
        parts = [str(x) for x in command]
        if len(parts) >= 3 and _SHELL_ARGV0.search(parts[0]) and parts[1] in ("-c", "-lc"):
            return parts[2]
        return " ".join(parts)
    if not isinstance(command, str):
        return None
    text = command.strip()
    inner = _SHELL_WRAP.sub("", text, count=1)
    if inner != text and len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in "'\"":
        inner = inner[1:-1]
    return inner


def command_norm(command: str | list | None, project: str | None) -> str | None:
    text = unwrap(command)
    if not text or not text.strip():
        return None
    text = _CD_PREFIX.sub("", text.strip(), count=1)
    if project:
        root = project.rstrip("/")
        text = text.replace(root + "/", "")
        text = re.sub(re.escape(root) + r"(?=\s|$)", ".", text)
    return " ".join(text.split())[:MAX] or None


def _plain(word: str) -> bool:
    return not (word.startswith(("-", '"', "'", "$", "<", ">")) or any(c in word for c in "/=."))


def command_key(command: str | list | None) -> str | None:
    text = unwrap(command)
    if not text or not text.strip():
        return None
    for segment in _SEGMENTS.split(text):
        words = segment.split()
        while words and _ASSIGN.match(words[0]):
            words = words[1:]
        if not words or words[0] in _SKIP:
            continue
        key = [words[0].rsplit("/", 1)[-1]]
        rest = words[1:]
        while rest:  # launchers stack: `uv run python -m pytest`
            if key[-1] in _LAUNCHER_1:
                key.append(rest.pop(0).rsplit("/", 1)[-1])
            elif (key[-1], rest[0]) in _LAUNCHERS and len(rest) > 1:
                key += [rest[0], rest[1].rsplit("/", 1)[-1]]
                rest = rest[2:]
            else:
                break
        if rest and _plain(rest[0]):
            key.append(rest[0])
        return " ".join(key)[:MAX]
    return None
