"""CONTRACT: the shell-command keys and the D55 command class shared by every adapter (D17, DM-28, DM-29).

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
- The three return None for an empty or non-string/non-list command.
- `command_class(command)` (DM-28, D55) is `test`, `wait` or None, computed from the FULL command (call it
  before any clipping). The unwrapped command, without heredoc bodies, is split into simple commands at
  `&&`, `||`, `;`, `|`, `&` (not in `>&` or `&>`), newlines and the words `do`, `then`, `else`; in each,
  leading `VAR=value` words and the prefixes `timeout <n>`, `time`, `nice`, `env`, `uv run`, `uvx`,
  `poetry run`, `pnpm exec`, `npx`, `bunx`, `python -m`, `python3 -m` (with their flags) are skipped.
  `test` when some simple command then starts with a test runner (`pytest`, `py.test`, `vitest`, `jest`,
  `mocha`, `playwright test`, `go test`, `cargo test|nextest`, `tox`, `nox`, `unittest`, `rspec`,
  `phpunit`, `bun test`, `deno test`, `npm|pnpm|yarn test` or `run test...`); else `wait` when some simple
  command starts with `sleep`, `until` or `while`; else None.
- `count_tests(text)` (DM-29, D55) is `(passed, failed)` from the last summary line of a test runner it
  recognises (pytest, vitest, jest; for cargo test the sum of all its `test result:` lines; `failed`
  includes pytest errors), else None. ANSI colour codes are ignored.
  It returns two integers and keeps no text (D39).
- `shell_effect(command)` (DM-28) is `look`, `change` or `run` for the unwrapped command without heredoc
  bodies. An output redirection to anything but `/dev/null` (`>`, `>>`; `2>&1` and `>&N` are not
  redirections) makes it `change`. Otherwise each segment (split on `&& || ; |` and newlines) is judged by
  its first word after `VAR=value` prefixes and a leading `(` or `{`: `cd pushd popd export set unset
  source .` are skipped; `git` is `look` for a read subcommand (status, log, diff, show, ...), `change` for
  a write subcommand (add, commit, checkout, ...), else `run`; `sed` is `change` with `-i`, else `look`;
  `find` is `run` with `-delete|-exec|-execdir|-ok`, else `look`; `sqlite3` is `change` when its arguments
  hold write SQL, `look` when they hold only read SQL, else `run`; a read program (`cat ls grep ...`) is
  `look`; a writing program (`rm mv cp mkdir ...`) is `change`; any other program is `run`.
  The command takes the first of `change`, `run`, `look` that any segment has; no judged segment gives `look`.
EXPECTS: `project` is the session's first cwd (D10) or None.
FAILURE BEHAVIOR: none; no shell parsing errors are possible (no shlex). `shell_effect` asserts its argument
  is a string or an argv list.
DOES NOT: detect writes inside a quoted argument, a function body, a script or a subshell the shell runs.
KNOWN LIMITATIONS: quoting is not parsed, so a `&&` inside quotes splits a
  segment for `command_key` and `command_class`; a runner name at the start of a line inside a quoted
  multi-line string (not a heredoc) counts as a command. `make test` is not a test runner (DM-28).
  `shell_effect` takes `>` inside a quoted argument (`echo "a -> b"`) for a redirection and judges a shell
  function call (`ab() { ...; }; ab x`) as `run`.
"""

from __future__ import annotations

import re
from bisect import bisect_right

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


# ── D55: command class (DM-28) and test counts (DM-29) ───────────────────────────────────────────

_HEREDOC_START = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")
_LINE_WORD = re.compile(r"[ \t]*(\w+)")
_CLASS_SPLIT = re.compile(r"&&|\|\||;|\||\n|(?<![>&])&(?![>&])")
_CONTINUE = frozenset({"do", "then", "else"})
_PYTHON = re.compile(r"python[\d.]*$")
_TEST_PROGRAMS = frozenset(
    {"pytest", "py.test", "vitest", "jest", "mocha", "tox", "nox", "unittest", "rspec", "phpunit"}
)
_TEST_SUBCOMMANDS = {
    "playwright": ("test",),
    "go": ("test",),
    "cargo": ("test", "nextest"),
    "bun": ("test",),
    "deno": ("test",),
}
_PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn"})
# flags that take a separate value, per prefix program
_VALUE_FLAGS = {
    "nice": frozenset({"-n"}),
    "timeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
    "uv": frozenset(
        {
            "--project",
            "--with",
            "--directory",
            "-p",
            "--python",
            "--group",
            "--extra",
            "--env-file",
            "--package",
        }
    ),
    "npx": frozenset({"-p", "--package"}),
    "npm": frozenset({"--prefix", "-C", "--cwd", "--filter", "-w", "--workspace"}),
}


def _skip_flags(words: list[str], value_flags: frozenset[str] = frozenset()) -> list[str]:
    while words and words[0].startswith("-"):
        words = words[2:] if words[0] in value_flags else words[1:]
    return words


def _after_prefixes(words: list[str]) -> list[str]:
    """DM-28: the words of one simple command from its program on."""
    while words:
        base = words[0].rsplit("/", 1)[-1]
        rest = words[1:]
        if _ASSIGN.match(words[0]):
            words = rest
        elif base in ("time", "nice", "env", "uvx", "npx", "bunx"):
            words = _skip_flags(rest, _VALUE_FLAGS.get(base, frozenset()))
        elif base == "timeout":
            rest = _skip_flags(rest, _VALUE_FLAGS["timeout"])
            words = rest[1:]  # the duration
        elif base in ("uv", "poetry") and rest[:1] == ["run"]:
            words = _skip_flags(rest[1:], _VALUE_FLAGS["uv"])
        elif base == "pnpm" and _skip_flags(rest, _VALUE_FLAGS["npm"])[:1] == ["exec"]:
            words = _skip_flags(_skip_flags(rest, _VALUE_FLAGS["npm"])[1:])
        elif _PYTHON.match(base) and rest[:1] == ["-m"]:
            words = rest[1:]
        else:
            break
    return words


def _drop_heredoc_bodies(text: str) -> str:
    """Remove the lines between a `<<WORD` line and the next line that starts with WORD. One pass over the
    lines plus a bisect per heredoc, so the cost is linear (a missing terminator keeps the text)."""
    if "<<" not in text:
        return text
    lines = text.split("\n")
    ends: dict[str, list[int]] = {}
    for i, line in enumerate(lines):
        m = _LINE_WORD.match(line)
        if m:
            ends.setdefault(m[1], []).append(i)
    out: list[str] = []
    i = 0
    while i < len(lines):
        out.append(lines[i])
        m = _HEREDOC_START.search(lines[i])
        if m:
            at = ends.get(m[1], [])
            k = bisect_right(at, i)
            if k < len(at):
                i = at[k]  # the terminator line is dropped with the body
        i += 1
    return "\n".join(out)


def _simple_commands(text: str) -> list[list[str]]:
    text = _drop_heredoc_bodies(text)
    out: list[list[str]] = []
    for segment in _CLASS_SPLIT.split(text):
        current: list[str] = []
        for word in segment.split():
            if word in _CONTINUE:
                out.append(current)
                current = []
            else:
                current.append(word)
        out.append(current)
    stripped = [_after_prefixes(words) for words in out if words]
    return [words for words in stripped if words]  # a prefix with nothing after it is no command


def _is_test(words: list[str]) -> bool:
    program = words[0].rsplit("/", 1)[-1]
    rest = words[1:]
    if program in _TEST_PROGRAMS:
        return True
    if program in _TEST_SUBCOMMANDS:
        return rest[:1] != [] and rest[0] in _TEST_SUBCOMMANDS[program]
    if program in _PACKAGE_MANAGERS:
        rest = _skip_flags(rest, _VALUE_FLAGS["npm"])
        return rest[:1] == ["test"] or (rest[:1] == ["run"] and len(rest) > 1 and rest[1].startswith("test"))
    return False


def command_class(command: str | list | None) -> str | None:
    text = unwrap(command)
    if not text or not text.strip():
        return None
    simple = _simple_commands(text)
    if any(_is_test(words) for words in simple):
        return "test"
    if any(words[0].rsplit("/", 1)[-1] in ("sleep", "until", "while") for words in simple):
        return "wait"
    return None


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PYTEST_PART = re.compile(r"(\d+) (passed|failed|errors?)\b")
_PYTEST_TIME = re.compile(r"\bin \d+(?:\.\d+)?s\b")
_JS_LINE = re.compile(r"^\s*Tests:?\s+(.*)$")
_JS_PART = re.compile(r"(\d+) (passed|failed)\b")
_CARGO_LINE = re.compile(r"^test result: \w+\.\s+(\d+) passed; (\d+) failed")


def count_tests(text: str | None) -> tuple[int, int] | None:
    """DM-29: `(passed, failed)` of the last summary line of a test runner in `text`, else None."""
    if not text:
        return None
    lines = _ANSI.sub("", text).splitlines()
    cargo = [m for line in lines if (m := _CARGO_LINE.match(line))]
    if cargo:  # one line per test binary and for the doc tests: the run is their sum
        return sum(int(m[1]) for m in cargo), sum(int(m[2]) for m in cargo)
    for line in reversed(lines):
        m = _JS_LINE.match(line)
        if m:
            parts = _JS_PART.findall(m[1])
            if parts:
                counts = {name: int(n) for n, name in parts}
                return counts.get("passed", 0), counts.get("failed", 0)
        if _PYTEST_TIME.search(line):
            parts = _PYTEST_PART.findall(line)
            if parts:
                passed = sum(int(n) for n, name in parts if name == "passed")
                failed = sum(int(n) for n, name in parts if name != "passed")
                return passed, failed
    return None


# ── DM-28: the effect of a shell command ─────────────────────────────────────────────────────────

_NOISE_REDIRECT = re.compile(r"\d*>&\d+|&?\d*>>?\s*/dev/null")
_EFFECT_SPLIT = re.compile(r"&&|\|\||;|\||\n")
_SEGMENT_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*\s*")
_NO_EFFECT_WORDS = frozenset({"cd", "pushd", "popd", "export", "set", "unset", "source", "."})
_READ_PROGRAMS = frozenset(
    "cat ls grep egrep fgrep rg head tail wc stat file du df jq awk cut sort uniq tr tree which pwd echo"
    " printf test [ diff basename dirname realpath readlink date ps true false column nl xxd od strings less"
    " more type command env id whoami hostname uname lsof pgrep sleep".split()
)
_WRITE_PROGRAMS = frozenset(
    "rm mv cp mkdir touch chmod chown ln tee truncate rmdir install patch kill pkill".split()
)
_GIT_READ = frozenset(
    "status log diff show rev-parse branch grep ls-files blame describe remote config shortlog ls-tree"
    " cat-file rev-list".split()
)
_GIT_WRITE = frozenset(
    "add commit checkout switch merge rebase reset stash push pull fetch restore rm mv apply cherry-pick tag"
    " clean worktree init clone".split()
)
_SQL_WRITE = re.compile(r"\b(insert|update|delete|drop|create|alter|replace|vacuum)\b", re.I)
_SQL_READ = re.compile(r"\b(select|pragma)\b|\.schema|\.tables|\.headers", re.I)
_SED_IN_PLACE = re.compile(r"(^|\s)-\w*i")
_FIND_ACTION = re.compile(r"-(delete|exec|execdir|ok)\b")


def shell_effect(command: str | list | None) -> str:
    """DM-28: `look`, `change` or `run` for a shell command (rules in the CONTRACT)."""
    text = unwrap(command)
    assert text is not None, f"shell_effect of {type(command).__name__}"
    text = _drop_heredoc_bodies(text)
    if ">" in _NOISE_REDIRECT.sub("", text):
        return "change"
    effects = {_segment_effect(segment) for segment in _EFFECT_SPLIT.split(text)}
    for effect in ("change", "run"):
        if effect in effects:
            return effect
    return "look"


def _segment_effect(segment: str) -> str | None:
    """`look`, `change` or `run` for one segment, None for one that does nothing (`cd`, an assignment)."""
    s = segment.strip().lstrip("({").strip()
    while (t := _SEGMENT_PREFIX.sub("", s, count=1)) != s:
        s = t
    if not s:
        return None
    first, *rest = s.split(None, 1)
    args = rest[0] if rest else ""
    program = first.strip("\"'").rsplit("/", 1)[-1]
    if program in _NO_EFFECT_WORDS:
        return None
    if program == "git":
        sub = args.split(None, 1)[0] if args else ""
        return "look" if sub in _GIT_READ else "change" if sub in _GIT_WRITE else "run"
    if program == "sed":
        return "change" if _SED_IN_PLACE.search(args) else "look"
    if program == "find":
        return "run" if _FIND_ACTION.search(args) else "look"
    if program == "sqlite3":
        if _SQL_WRITE.search(args):
            return "change"
        return "look" if _SQL_READ.search(args) else "run"
    if program in _READ_PROGRAMS:
        return "look"
    return "change" if program in _WRITE_PROGRAMS else "run"
