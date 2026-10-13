"""CONTRACT: src:// pointers into provider files (design §4.9).

Grammar:
  src://<provider>/<relpath>#<locator>[/<json.path>][?h=<hash>]
  locator = L<first>-L<last> (1-based lines) | B<start>+<len> (bytes)
  relpath is percent-encoded (safe "/"), so '#', '?' and spaces round-trip.

GUARANTEES:
- `format` and `parse` are inverses: parse(format(...)) returns the inputs, and
  format(*parse(u)) == u for every pointer `format` emits.
- `resolve(uri, roots, part, offset, max_bytes, with_block)` returns
  `{text, total_bytes, drift, file, locator}`, plus `block` when `with_block`; `drift` is True iff `h` is
  present and differs from `content_hash` over the addressed bytes (whole
  lines including their newline for L, the range for B). `part` is a dotted
  JSON path applied below the pointer's own json path (query.source maps
  `--part input|output|thinking|text` onto it). Paging is over the UTF-8 bytes
  of the selected text: `text` holds at most `max_bytes` bytes from `offset`.
  `block` (only with `with_block=True`; other callers get the five keys) is the JSON `type` of the
  content block that holds the selected value (the deepest typed object on the path below the record
  itself, so `thinking`, `redacted_thinking`, `tool_result`, `reasoning`, `text`), or None when the
  path addresses no typed block or the pointer has no json path. A path that
  starts at `toolUseResult` (the structured copy of a Claude tool result) reports
  the type of `message.content.0`.
- `content_hash` is the one hash adapters use to emit `h`.
EXPECTS: `roots` maps provider -> directory the relpath is relative to
  (`DEFAULT_ROOTS` for the real corpus).
FAILURE BEHAVIOR: bad grammar -> `PointerError`; missing file or a locator past
  end of file -> `SourceMissing`; a json path that does not exist in the
  record -> `PointerError`.
DOES NOT: cache text; choose which part of a record is "output".
KNOWN LIMITATIONS: parse also accepts the bracket index form `content[0]` that
  B1-B3 streams emit and normalizes it to `content.0`; a paged slice that cuts a
  multi-byte character drops the partial character.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

SCHEME = "src://"
HASH_LEN = 16

DEFAULT_ROOTS: dict[str, Path] = {
    "claude_code": Path.home() / ".claude" / "projects",
    "codex": Path.home() / ".codex",
    "omp": Path.home() / ".omp" / "agent" / "sessions",
}


class PointerError(ValueError):
    """Malformed pointer or unresolvable json path."""


class SourceMissing(FileNotFoundError):
    """The addressed file or range no longer exists."""


@dataclass(frozen=True, slots=True)
class Lines:
    first: int
    last: int

    def __str__(self) -> str:
        return f"L{self.first}-L{self.last}"


@dataclass(frozen=True, slots=True)
class Bytes:
    start: int
    length: int

    def __str__(self) -> str:
        return f"B{self.start}+{self.length}"


Locator = Lines | Bytes


@dataclass(frozen=True, slots=True)
class Pointer:
    provider: str
    relpath: str
    locator: Locator
    json_path: str | None = None
    h: str | None = None


_URI = re.compile(
    r"src://(?P<provider>[a-z_]+)/(?P<rel>[^#]+)#"
    r"(?:L(?P<first>\d+)-L(?P<last>\d+)|B(?P<start>\d+)\+(?P<len>\d+))"
    r"(?:/(?P<path>[^?]+))?(?:\?h=(?P<h>[0-9a-f]+))?"
)
_BRACKET = re.compile(r"\[(\d+)\]")


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:HASH_LEN]


def _check_locator(locator: Locator) -> None:
    if isinstance(locator, Lines):
        if not 1 <= locator.first <= locator.last:
            raise PointerError(f"bad line range {locator}")
    elif isinstance(locator, Bytes):
        if locator.start < 0 or locator.length < 0:
            raise PointerError(f"bad byte range {locator}")
    else:
        raise PointerError(f"unknown locator {locator!r}")


def format(
    provider: str, relpath: str, locator: Locator, json_path: str | None = None, h: str | None = None
) -> str:
    _check_locator(locator)
    if not relpath or relpath.startswith("/"):
        raise PointerError(f"relpath must be relative and non-empty: {relpath!r}")
    out = f"{SCHEME}{provider}/{quote(relpath, safe='/')}#{locator}"
    if json_path:
        out += "/" + _normalize_path(json_path)
    if h:
        out += f"?h={h}"
    return out


def _normalize_path(json_path: str) -> str:
    return _BRACKET.sub(r".\1", json_path).strip(".")


def parse(uri: str) -> Pointer:
    m = _URI.fullmatch(uri)
    if m is None:
        raise PointerError(f"not a src:// pointer: {uri!r}")
    if m["first"] is not None:
        locator: Locator = Lines(int(m["first"]), int(m["last"]))
    else:
        locator = Bytes(int(m["start"]), int(m["len"]))
    _check_locator(locator)
    path = _normalize_path(m["path"]) if m["path"] else None
    return Pointer(m["provider"], unquote(m["rel"]), locator, path, m["h"])


def _walk(value: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise PointerError(f"json path {path!r} not found at {part!r}")
    return value


def _read_lines(file: Path, loc: Lines) -> bytes:
    out: list[bytes] = []
    with file.open("rb") as f:
        for n, line in enumerate(f, start=1):
            if n >= loc.first:
                out.append(line)
            if n == loc.last:
                return b"".join(out)
    raise SourceMissing(f"{file}: line {loc.last} past end of file")


def _read_bytes(file: Path, loc: Bytes) -> bytes:
    if file.stat().st_size < loc.start + loc.length:
        raise SourceMissing(f"{file}: byte range {loc} past end of file")
    with file.open("rb") as f:
        f.seek(loc.start)
        return f.read(loc.length)


def _block_type(record: Any, path: str) -> str | None:
    """The `type` of the deepest typed object on `path` below `record` (the record itself is not a block)."""
    # FALLBACK-OK: interfaces.md API-26 (`block` is null when the path addresses no typed block)
    segs = path.split(".")
    if segs[0] == "toolUseResult":
        segs = ["message", "content", "0"]
    block = None
    value = record
    for seg in segs:
        if isinstance(value, list) and seg.isdigit() and int(seg) < len(value):
            value = value[int(seg)]
        elif isinstance(value, dict) and seg in value:
            value = value[seg]
        else:
            return None
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            block = value["type"]
    return block


def resolve(
    uri: str,
    roots: dict[str, Path] | None = None,
    part: str | None = None,
    offset: int = 0,
    max_bytes: int = 65536,
    with_block: bool = False,
) -> dict[str, Any]:
    p = parse(uri)
    roots = DEFAULT_ROOTS if roots is None else roots
    if p.provider not in roots:
        raise PointerError(f"no root for provider {p.provider!r}")
    file = Path(roots[p.provider]) / p.relpath
    if not file.is_file():
        raise SourceMissing(str(file))
    raw = _read_lines(file, p.locator) if isinstance(p.locator, Lines) else _read_bytes(file, p.locator)
    drift = p.h is not None and content_hash(raw) != p.h
    path = ".".join(x for x in (p.json_path, _normalize_path(part) if part else None) if x)
    block = None
    if path:
        first = raw.split(b"\n", 1)[0]
        try:
            record = json.loads(first)
        except json.JSONDecodeError as exc:
            raise PointerError(f"json path on a non-JSON line: {uri}") from exc
        value = _walk(record, path)
        block = _block_type(record, path)
        data = (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)).encode()
    else:
        data = raw
    if offset < 0 or max_bytes < 0:
        raise PointerError("offset and max_bytes must be >= 0")
    chunk = data[offset : offset + max_bytes]
    out = {
        "text": chunk.decode("utf-8", errors="ignore"),
        "total_bytes": len(data),
        "drift": drift,
        "file": str(file),
        "locator": str(p.locator),
    }
    if with_block:
        out["block"] = block
    return out
