"""CONTRACT: `botsworth.repl`, the persistent analysis REPL for agents (interfaces.md §5.3 REPL-1..7, D58).

GUARANTEES:
- `client` (stdlib only, no pandas) implements `botsworth repl start|run|status|interrupt|stop` and returns
  the
  exit codes of REPL-2. `server` is the detached process (`python -m botsworth.repl.server`, started with the
  interpreter that runs the client) that holds the frames and one namespace. `bw` is the helper library.
- This package module holds what both sides share and imports nothing heavy: the socket folder, the session-
  name
  rule, the exit codes and the message for a missing session.
- The socket folder is `$TMPDIR/botsworth-repl` (mode 0700), one `<name>.sock` (mode 0600) per session. A
  session
  writes nothing else: no store, no cache, no log (D52).
DOES NOT: read or write the store (frames are loaded by `botsworth.query.frames`), and does not import pandas
  here.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_RAISED = 1  # the cell raised
EXIT_NO_SESSION = 2  # no session running
EXIT_BUSY = 3  # the cell is still running, or the session is busy or still loading
EXIT_CHOICE = 4  # start parameters differ from the running session, or run cannot pick a session
EXIT_START_FAILED = 5  # start could not create the session

NO_SESSION = (
    "no session running: all session state is lost and must be rebuilt; start one with botsworth repl start"
)
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")
INSTALL_LINE = (
    "pandas is not installed; install it with: pip install 'botsworth[repl]'"
    "  (or: uv tool install 'botsworth[repl]')"
)


def sock_dir() -> Path:
    """The socket folder for this user; read at call time so `TMPDIR` can change between calls."""
    return Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / "botsworth-repl"


def sock_path(name: str) -> Path:
    return sock_dir() / f"{name}.sock"
