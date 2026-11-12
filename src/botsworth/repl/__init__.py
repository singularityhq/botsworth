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
import socket
import subprocess
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_RAISED = 1  # the cell raised
EXIT_NO_SESSION = 2  # no session running
EXIT_BUSY = 3  # the cell is still running, or the session is busy or still loading
EXIT_CHOICE = 4  # start parameters differ from the running session, or run cannot pick a session
EXIT_START_FAILED = 5  # start could not create the session

EXIT_SERVER_TAKEN = (
    17  # exit code of a server process that finds a live server on its name (not a client code)
)

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


def b36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if n == 0:
            return out


def private_path(path: Path, pid: int) -> Path:
    """The server's own name for its socket: `<name>.<pid in base 36>`, no longer than `<name>.sock` for any
    pid below 1,679,616. It is a second hard link to the same socket and lives as long as the server."""
    return path.with_suffix("." + b36(pid))


def owner_pid(path: Path) -> int | None:
    """The pid in the private link name that shares the socket file `path` (same inode), or None."""
    try:
        ino = os.stat(path).st_ino
    except FileNotFoundError:
        return None  # FALLBACK-OK: REPL-4 no socket file: no owner
    for other in path.parent.glob(f"{path.stem}.*"):
        if other.suffix == ".sock":
            continue
        try:
            if os.stat(other).st_ino == ino:
                return int(other.suffix[1:], 36)
        except (FileNotFoundError, ValueError):
            continue  # FALLBACK-OK: REPL-4 an unrelated or vanished file: not the private link
    return None


def cmdline(pid: int) -> str | None:
    proc = Path(f"/proc/{pid}/cmdline")
    if proc.exists():
        return proc.read_bytes().replace(b"\0", b" ").decode(errors="replace")
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    except OSError:
        return None  # FALLBACK-OK: REPL-4 no way to read a command line: the caller treats it as unknown
    if out.returncode == 0:
        return out.stdout
    if out.returncode == 1 and not out.stdout.strip() and not out.stderr.strip():
        return ""  # `ps` ran and found no such process
    return None  # FALLBACK-OK: REPL-4 `ps` itself failed: identity unknown, never "not our server"


def is_server(pid: int, name: str) -> bool | None:
    """True when `pid` is a live REPL server for session `name`, False when it is not (gone or another
    program), None when that cannot be told."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False  # FALLBACK-OK: REPL-4 the process is gone
    except PermissionError:
        return False  # FALLBACK-OK: REPL-4 a process of another user is not our server
    line = cmdline(pid)
    if line is None:
        return None  # FALLBACK-OK: REPL-4 unverifiable
    return "botsworth.repl.server" in line and f"--name {name} " in line + " "


def refused_means_dead(path: Path) -> bool:
    """After a refused connect to `path`: True when its server is gone. A server that is alive but whose
    queue is full also refuses, so a refusal alone is no proof of death."""
    pid = owner_pid(path)
    return pid is None or is_server(pid, path.stem) is False


def socket_dead(path: Path) -> bool:
    """True when nothing serves `path`: a refused connect whose server is gone."""
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(2)
    try:
        c.connect(str(path))
    except (ConnectionRefusedError, FileNotFoundError):
        return refused_means_dead(path)
    except OSError:
        return False  # FALLBACK-OK: REPL-4 a timeout or an odd socket is not proof of death
    finally:
        c.close()
    return False
