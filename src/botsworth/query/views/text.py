"""CONTRACT: content references and lazy full text (owner Q2c).

`src_uri` turns a stored reference into a `src://` URI; `source` serves the text a URI addresses.
Spec ids: API-26; data-model §2.3 (DM-6 to DM-9); D39, D42.

GUARANTEES:
- `src_uri(conn, ref_row)` is the one function that builds a URI for a field Q2c puts on the wire
  (`input_src`, `output_src`, `ask_src`, `text_src`, `command_src`, `sections[].src`). `ref_row` is any
  `ops`, `steps` or `events` row (it needs `src_file`, `src_loc`, `src_h`). The result equals the stream's
  `src` byte for byte and `pointer.parse` accepts it. Rows built by Q2b (`ToolRow.src`, `command_src`,
  `TurnHeader.text_src`) use the SQL form of the same rule (`refs.src_sql`) and give the same bytes.
- `source(id_or_src, part, offset, max_bytes, db, roots)` (API-26) returns the dict form of
  `wire.SourceResponse`. `id_or_src` is a `src://` URI or any id `outline.resolve_src` accepts. `max_bytes` is
  1..1,048,576. `block` is the JSON type of the content block that holds the text (`pointer.resolve`).
- Part mapping (DM-7). The paths below are tried in order; the first that exists wins.
  `input`: the first line. A reference whose own json path already ends in `input`, `arguments` or `action`
  (Codex `payload.arguments`, `payload.input`) is the input. Else, below the call block: `input.command`
  (Bash), `input.prompt` (Agent, Task), `input`, then the same three for `arguments` (omp).
  `output`: the LAST line of the locator, whatever json path the reference carries: `toolUseResult`,
  `message.content.0.content` (Claude), `payload.output` (Codex), `message.content` (omp result line).
  `thinking`: `thinking` below the block. `text`: below the block `text`, then `message`, then the block
  itself when its path ends in `message`, `content`, `text` or `details`; a reference with no json path (a
  turn ask) tries `message.content`, `details` (omp skill-prompt), `payload.message`.
  A value that is a list of content blocks (omp result `message.content`, Claude `tool_result` content, a
  user message with blocks) is returned as its `text` blocks joined by a newline; any other block is the
  placeholder `[<type>]`, for example `[image]`. This applies to `output` and `text`.
- A Codex reasoning item (block `reasoning`) never returns `encrypted_content`: every part, and a read with no
  part, returns the plaintext summary (`payload.summary[*].text` joined by a blank line) or "". A Claude
  `redacted_thinking` block read with `part=thinking` returns "" with `block = 'redacted_thinking'`.
  `drift` of an `output` read is the drift of the reference's own range.
EXPECTS: a connection from `botsworth.query._conn` (schema v7); `roots` maps provider -> directory, default
  `pointer.DEFAULT_ROOTS`.
FAILURE BEHAVIOR: a bad part, offset or limit, a malformed pointer or no part path that exists -> QueryError;
  a missing file or a locator past the end of the file -> `pointer.SourceMissing` (HTTP 404); a `ref_row`
  whose `src_file` has no `src_files` row -> AssertionError.
DOES NOT: store text read from a file, cache, or write.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from botsworth import pointer
from botsworth.query import Db, QueryError, _conn, shapes, wire
from botsworth.query.outline import resolve_src
from botsworth.store import refs

PARTS = ("input", "output", "thinking", "text")
MAX_LIMIT = 1_048_576  # API-26
MAX_SUMMARY_ITEMS = 64
_INPUT_LEAVES = ("input", "arguments", "action")
_TEXT_LEAVES = ("message", "content", "text", "details")
_INPUT_BELOW_BLOCK = (
    "input.command",
    "input.prompt",
    "input",
    "arguments.command",
    "arguments.prompt",
    "arguments",
)
_OUTPUT_PATHS = ("toolUseResult", "message.content.0.content", "payload.output", "message.content")
_TEXT_BELOW_BLOCK = ("text", "message")
_TEXT_OF_RECORD = ("message.content", "details", "payload.message")
MAX_BLOCKS = 1000


def src_uri(conn: sqlite3.Connection, ref_row: Any) -> str:
    """The `src://` URI of a stored reference (DM-8): prefix, `src_loc`, and `?h=` when `src_h` is set."""
    prefix = conn.execute("SELECT prefix FROM src_files WHERE rowid = ?", (ref_row["src_file"],)).fetchone()
    assert prefix is not None, f"src_file {ref_row['src_file']} has no src_files row (DM-8)"
    return refs.uri(prefix[0], ref_row["src_loc"], ref_row["src_h"])


# ── part mapping ─────────────────────────────────────────────────────────────


def _candidates(src: str, p: pointer.Pointer, part: str) -> list[tuple[str, str | None]]:
    """`(uri, part path)` pairs to try for `part`, in order (DM-7)."""
    pj = p.json_path
    leaf = pj.rsplit(".", 1)[-1] if pj else None
    bare = pointer.format(p.provider, p.relpath, p.locator, None, p.h)
    if part == "input":
        if leaf in _INPUT_LEAVES:
            return [(src, None)]
        return [(src, path) for path in _INPUT_BELOW_BLOCK]
    if part == "output":
        if not isinstance(p.locator, pointer.Lines):
            return []
        last = pointer.format(p.provider, p.relpath, pointer.Lines(p.locator.last, p.locator.last))
        return [(last, path) for path in _OUTPUT_PATHS]
    if part == "thinking":
        return [(src, "thinking")]
    if pj is None:
        return [(bare, path) for path in _TEXT_OF_RECORD]
    out: list[tuple[str, str | None]] = [(src, path) for path in _TEXT_BELOW_BLOCK]
    if leaf in _TEXT_LEAVES:
        out.append((src, None))
    return out


def _probe(src: str, roots: dict[str, Path] | None, part: str | None) -> dict[str, Any] | None:
    """Resolve with no text, for `block` and `drift`; None when the path does not exist."""
    try:
        return pointer.resolve(src, roots, part, 0, 0, with_block=True)
    except pointer.PointerError:
        # FALLBACK-OK: DM-7 (the part paths are candidates per provider; a probe that finds none is a miss)
        return None


def _is_reasoning(src: str, p: pointer.Pointer, roots: dict[str, Path] | None) -> bool:
    """A Codex reasoning item holds `encrypted_content` and must never be read raw (API-26, DET-63)."""
    if p.provider != "codex":
        return False
    for part in (None, "payload") if p.json_path is None else (None,):
        got = _probe(src, roots, part)
        if got is not None and got["block"] == "reasoning":
            return True
    return False


def _page(text: str, offset: int, limit: int) -> tuple[str, int]:
    data = text.encode("utf-8")
    return data[offset : offset + limit].decode("utf-8", errors="ignore"), len(data)


def _reasoning_summary(
    src: str, p: pointer.Pointer, roots: dict[str, Path] | None, offset: int, limit: int
) -> dict[str, Any]:
    """The plaintext summary of a Codex reasoning item, paged. Only `summary[*].text` is read (DET-63)."""
    base = "payload"  # a Codex item lives at `payload`; the adapter's reference may name `payload.summary`
    bare = pointer.format(p.provider, p.relpath, p.locator, None, p.h)
    parts: list[str] = []
    for i in range(MAX_SUMMARY_ITEMS):
        got = _probe_text(bare, roots, f"{base}.summary.{i}.text")
        if got is None:
            break
        parts.append(got)
    head = pointer.resolve(src, roots, None, 0, 0, with_block=True)
    text, total = _page("\n\n".join(parts), offset, limit)
    return {**head, "text": text, "total_bytes": total, "block": "reasoning"}


def _probe_text(bare: str, roots: dict[str, Path] | None, path: str) -> str | None:
    try:
        return pointer.resolve(bare, roots, path, 0, MAX_LIMIT)["text"]
    except pointer.PointerError:
        # FALLBACK-OK: DET-63 (a reasoning item has as many summary parts as it has; the first miss ends them)
        return None


def _blocks_text(uri: str, path: str | None, roots: dict[str, Path] | None) -> str | None:
    """The text of a value that is a list of content blocks: each `text` block, `[<type>]` for any other
    block, joined by a newline. None when the value is not a list (or is empty)."""

    def at(i: int, sub: str = "") -> str:
        return (f"{path}.{i}" if path else str(i)) + sub

    def read(part: str) -> str | None:
        try:
            return pointer.resolve(uri, roots, part, 0, MAX_LIMIT)["text"]
        except pointer.PointerError:
            # FALLBACK-OK: DM-7 (a value is a list of blocks only while index i exists; a miss ends the list)
            return None

    out: list[str] = []
    for i in range(MAX_BLOCKS):
        if read(at(i)) is None:
            break
        body = read(at(i, ".text"))
        if body is not None:
            out.append(body)
        else:
            # FALLBACK-OK: ui/detail.md DET-67 (a non-text block, such as an image, is shown as a placeholder)
            out.append(f"[{read(at(i, '.type')) or 'block'}]")
    return "\n".join(out) if out else None


def _read(
    src: str, part: str | None, offset: int, limit: int, roots: dict[str, Path] | None
) -> dict[str, Any]:
    try:
        p = pointer.parse(src)
        if _is_reasoning(src, p, roots):
            return _reasoning_summary(src, p, roots, offset, limit)
        if part is None:
            return pointer.resolve(src, roots, None, offset, limit, with_block=True)
        for uri, path in _candidates(src, p, part):
            try:
                out = pointer.resolve(uri, roots, path, offset, limit, with_block=True)
            except pointer.PointerError:
                continue  # FALLBACK-OK: DM-7 (part paths are candidates; none matching raises below)
            if part in ("output", "text"):
                joined = _blocks_text(uri, path, roots)
                if joined is not None:
                    page, total = _page(joined, offset, limit)
                    out = {**out, "text": page, "total_bytes": total}
            if part == "output" and p.h is not None:
                own = _probe(pointer.format(p.provider, p.relpath, p.locator, None, p.h), roots, None)
                assert own is not None  # no json path, so nothing to miss
                out["drift"] = own["drift"]
            return out
        if part == "thinking":
            got = _probe(src, roots, None)
            if got is not None and got["block"] == "redacted_thinking":
                return {**got, "text": "", "total_bytes": 0}
        raise QueryError(f"part {part!r} not found under {src}")
    except pointer.PointerError as exc:
        raise QueryError(str(exc)) from exc


def source(
    id_or_src: str,
    part: str | None = None,
    offset: int = 0,
    max_bytes: int = 65536,
    db: Db = None,
    roots: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """API-26: the text a reference addresses, with `block`. `part` is input, output, thinking or text."""
    if part is not None and part not in PARTS:
        raise QueryError(f"part must be one of {sorted(PARTS)}")
    if not (isinstance(offset, int) and offset >= 0):
        raise QueryError("offset must be >= 0")
    if not (isinstance(max_bytes, int) and 1 <= max_bytes <= MAX_LIMIT):
        raise QueryError(f"limit must be 1..{MAX_LIMIT}")
    with _conn(db) as conn:
        src = id_or_src if id_or_src.startswith(pointer.SCHEME) else resolve_src(conn, id_or_src)
        got = _read(src, part, offset, max_bytes, roots)
        return asdict(
            wire.SourceResponse(
                freshness=wire.Freshness(**shapes.freshness(conn)),
                text=got["text"],
                total_bytes=got["total_bytes"],
                drift=got["drift"],
                file=got["file"],
                locator=got["locator"],
                block=got["block"],
            )
        )
