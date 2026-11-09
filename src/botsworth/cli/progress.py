"""CONTRACT: live `botsworth ingest` progress on the terminal (CLI-4, CLI-11, D57).

GUARANTEES:
- Writes to one stream (stderr by default), never to stdout: the JSON run report stays the only thing
  on stdout, so a redirect or pipe still parses.
- With a stream whose `isatty()` is false every call is a no-op: no control bytes, no summary.
- Redraws a single line in place with `\\r` and trailing spaces (no ANSI codes), at most once per
  `min_interval` seconds; a phase change (`scanning`, `discovered`, `store`, the first paint) redraws
  at once. `finish()` clears the line before anything else writes to stderr.
- `finish(report, summary=True)` prints the `--table` block (`cli.render.ingest_lines`) on stderr;
  with `summary=False` it only clears the line. `abort()` clears the line and nothing else, so an
  error line or a traceback is not left glued to a half-drawn status.
- A line is clipped to the terminal width, so it never wraps. A write failure disables the renderer
  (a closed or redirected stderr must not fail an ingest).
DOES NOT: decide what a run returns, change the output format, or read the store.
"""

from __future__ import annotations

import shutil
import sys
import time
from collections.abc import Callable
from typing import Any, TextIO

from botsworth.cli.render import ingest_lines

_PHASES = ("start", "provider", "scanning", "discovered", "store")


class IngestProgress:
    """Callable progress sink for `pipeline.run` (D57); one rewritten stderr line plus a summary."""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        min_interval: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
        width: int | None = None,
    ) -> None:
        self.stream = sys.stderr if stream is None else stream
        self.enabled = bool(self.stream.isatty())
        self.min_interval = min_interval
        self.clock = clock
        self.width = max(20, width if width is not None else shutil.get_terminal_size((80, 24)).columns)
        self._started = clock()
        self._last = 0.0
        self._shown = ""
        self.mode = ""

    # -- drawing ------------------------------------------------------------------------------------

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except (OSError, ValueError):
            # FALLBACK-OK: D57 — progress is best effort; a closed stderr is not a failure
            self.enabled = False

    def _line(self, text: str, *, force: bool = False) -> None:
        if not self.enabled:
            return
        now = self.clock()
        if not force and now - self._last < self.min_interval:
            return
        self._last = now
        line = " ".join(text.split())  # one line, no embedded controls
        if len(line) > self.width - 1:
            line = line[: self.width - 2] + "…"
        pad = " " * max(0, len(self._shown) - len(line))
        self._shown = line
        self._write("\r" + line + pad)

    def clear(self) -> None:
        if not self.enabled or not self._shown:
            return
        self._write("\r" + " " * len(self._shown) + "\r")
        self._shown = ""

    def _elapsed(self) -> float:
        return self.clock() - self._started

    def _bundle_text(self, ev: dict) -> str:
        bits = (
            f"{ev['done']:,}/{ev['total']:,} bundles · {ev['rebuilt']:,} rebuilt"
            f" · {ev['unchanged']:,} unchanged"
        )
        if ev["rejected"]:
            bits += f" · {ev['rejected']:,} rejected"
        return f"ingest {self.mode} · {bits} · {self._elapsed():.0f}s"

    # -- sink ---------------------------------------------------------------------------------------

    def __call__(self, ev: dict) -> None:
        if not self.enabled:
            return
        kind = ev["event"]
        if kind == "start":
            self.mode = ev["mode"]
            self._line(f"ingest {self.mode} · {ev['store']}", force=True)
        elif kind == "provider":
            self._line(f"ingest {self.mode} · {ev['provider']} skipped — {ev['skipped']}", force=True)
        elif kind == "scanning":
            self._line(f"ingest {self.mode} · scanning {ev['provider']} …", force=True)
        elif kind == "discovered":
            n = ev["bundles"]
            self._line(f"ingest {self.mode} · {ev['provider']}: {n:,} bundles", force=True)
        elif kind == "bundle":
            self._line(self._bundle_text(ev))
        elif kind == "store":
            self._line(
                f"ingest {self.mode} · writing store · {ev['bundles']:,} bundles · {self._elapsed():.0f}s",
                force=True,
            )
        else:  # a pipeline event this renderer does not know: fail loudly, never hide it
            raise AssertionError(f"unknown progress event {kind!r}; expected one of {_PHASES} or 'bundle'")

    def finish(self, report: dict[str, Any], *, summary: bool = True) -> None:
        self.clear()
        if summary and self.enabled:
            self._write("\n".join(ingest_lines(report)) + "\n")

    def abort(self) -> None:
        self.clear()
