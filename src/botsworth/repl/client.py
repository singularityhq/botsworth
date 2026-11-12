"""CONTRACT: the REPL client, `botsworth repl start|run|status|interrupt|stop` (interfaces.md REPL-1, REPL-2,
  D58).

GUARANTEES:
- Stdlib only: it imports neither pandas nor the query layer, so a `run` costs one socket round trip.
- Each function returns the exit code of REPL-2: 0 ok; 1 the cell raised; 2 no session running; 3 the cell is
  still
  running or the session is busy or loading; 4 `start` parameters differ from the running session, or no
    unique
  session to pick; 5 `start` could not create the session. Results go to stdout; reasons and notices go to
    stderr.
- `start` lists the sessions already running (name, RSS), then either reports an equal running session (no
  second
  load) or starts a server with `sys.executable -m botsworth.repl.server` (the interpreter that runs the
    client, never
  a temporary environment) and returns once the frames are loaded. With no `--name` it makes a short name.
- `run` waits at most `wait` seconds; a longer cell keeps running and `run` exits 3. `run` and `status`
  without
  `--name` use the only running session, or exit 4 listing them.
- A socket file with no server behind it is removed, and the command exits 2 with the "state is lost" message.
- `stop` returns after the process has ended: it asks, waits 5 s, then kills.
EXPECTS: pandas importable by the interpreter that runs the client (`start` checks and prints the install
  line).
FAILURE BEHAVIOR: `start` prints the reason and exits 5 for: pandas missing, socket folder not usable, server
  could not bind (e.g. a sandbox forbids it), store missing or of another schema, no answer within
    `LOAD_WAIT_S`.
  A session that does not answer a status request in `ANSWER_S` is listed as unresponsive.
DOES NOT: read the store, keep state between invocations, or write any file (the server owns the socket).
"""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from botsworth import repl

LOAD_WAIT_S = (
    600.0  # a very large store can take minutes to load; then the caller starts again with the same --name
)
ANSWER_S = 5.0  # a status request that takes longer means the server is stuck in a long call
STOP_GRACE_S = 5.0


class Exit(Exception):
    """End the command with `code`; the message (if any) is printed on stderr."""

    def __init__(self, code: int, message: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message


def call(name: str, req: dict, timeout: float = ANSWER_S) -> dict | None:
    """One request. None when no server answers (a stale socket is removed); `unresponsive` on a timeout."""
    path = repl.sock_path(name)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
    except (FileNotFoundError, ConnectionRefusedError):
        # FALLBACK-OK: REPL-4 no server behind the socket: remove a stale file, report "no session"
        path.unlink(missing_ok=True)
        return None
    except TimeoutError:
        s.close()
        return {"ok": False, "unresponsive": True}  # FALLBACK-OK: REPL-4 a server stuck in a long call
    except OSError as e:
        raise Exit(repl.EXIT_START_FAILED, f"cannot use the session socket {path}: {e}") from None
    try:
        with s:
            s.sendall(json.dumps(req).encode() + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(1 << 20)
                if not chunk:
                    return None  # FALLBACK-OK: REPL-4 the server ended while answering
                data += chunk
    except TimeoutError:
        return {"ok": False, "unresponsive": True}  # FALLBACK-OK: REPL-4 a server stuck in a long call
    return json.loads(data)


def live() -> list[tuple[str, dict]]:
    """Every running session as (name, status reply); stale sockets are removed on the way."""
    folder = repl.sock_dir()
    out = []
    for p in sorted(folder.glob("*.sock")) if folder.is_dir() else []:
        st = call(p.stem, {"op": "status"})
        if st is not None:
            out.append((p.stem, st))
    return out


def list_line(name: str, st: dict) -> str:
    if st.get("unresponsive"):
        return f"  {name}: unresponsive (stuck in a long call; `botsworth repl stop --name {name}` ends it)"
    p = st["params"]
    state = (
        "loading" if not st["ready"] else (f"busy {st['busy_s']} s" if st["busy_s"] is not None else "idle")
    )
    return f"  {name}: RSS {st['rss_mb']} MB, {state}, idle {st['idle_s']} s, tz {p['tz']}, store {p['db']}"


def print_sessions(sessions: list[tuple[str, dict]]) -> None:
    print(f"{len(sessions)} session(s) running:")
    for name, st in sessions:
        print(list_line(name, st))


def pick(name: str | None) -> str:
    """The session to use: `name`, else the only running one."""
    if name:
        return name
    sessions = live()
    if not sessions:
        raise Exit(repl.EXIT_NO_SESSION, repl.NO_SESSION)
    if len(sessions) > 1:
        names = ", ".join(n for n, _ in sessions)
        raise Exit(repl.EXIT_CHOICE, f"several sessions run: {names}; pass --name")
    return sessions[0][0]


def show_status(name: str, st: dict) -> None:
    p = st["params"]
    scope = f"since={p['since']} until={p['until']} sessions={p['sessions']}"
    print(f"session {name}: pid {st['pid']}  store {p['db']}  tz {p['tz']}  {scope}")
    if not st["ready"]:
        print(f"  loading the frames; uptime {st['uptime_s']} s; RSS {st['rss_mb']} MB")
        return
    print(
        f"  loaded in {st['load_s']} s wall / {st['load_cpu_s']} s CPU; uptime {st['uptime_s']} s; "
        f"RSS {st['rss_mb']} MB; idle {st['idle_s']} s"
    )
    print("  frames: " + ", ".join(f"{k} {v}" for k, v in st["rows"].items()))
    print(f"  your names: {', '.join(st['user_names']) or '(none)'}")
    state = f"busy for {st['busy_s']} s" if st["busy_s"] is not None else "idle"
    last = st["last_state"] if st["last_state"] == "none" else f"{st['last_state']} after {st['last_s']} s"
    print(f"  state: {state}; last cell: {last}")


def wanted(db: str, tz: str, since: str | None, until: str | None, sessions: list[str] | None) -> dict:
    return {
        "db": str(Path(db).resolve()),
        "tz": tz,
        "since": since,
        "until": until,
        "sessions": sessions or None,
    }


def wait_ready(name: str, proc: subprocess.Popen | None) -> dict:
    deadline = time.time() + LOAD_WAIT_S
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            err = proc.stderr.read().decode().strip().splitlines() if proc.stderr else []
            reason = err[-1].removeprefix("error: ") if err else f"the server exited with {proc.returncode}"
            raise Exit(repl.EXIT_START_FAILED, f"start failed: {reason}")
        st = call(name, {"op": "status"})
        if st is not None and st.get("ready"):
            return st
        time.sleep(0.1)
    raise Exit(
        repl.EXIT_START_FAILED,
        f"start failed: no answer in {LOAD_WAIT_S:g} s; the server may still be loading",
    )


def start(
    db: str,
    tz: str,
    name: str | None = None,
    since: str | None = None,
    until: str | None = None,
    sessions: list[str] | None = None,
    idle: float = 20,
    max_rss: float = 2000,
) -> int:
    if importlib.util.find_spec("pandas") is None:
        raise Exit(repl.EXIT_START_FAILED, repl.INSTALL_LINE)
    if name is not None and not repl.NAME.fullmatch(name):
        raise Exit(
            repl.EXIT_START_FAILED,
            f"start failed: a session name is 1-32 letters, digits, - or _, got {name!r}",
        )
    folder = repl.sock_dir()
    try:
        folder.mkdir(mode=0o700, exist_ok=True)
        os.chmod(folder, 0o700)
    except OSError as e:
        raise Exit(
            repl.EXIT_START_FAILED, f"start failed: cannot use the socket folder {folder}: {e}"
        ) from None
    running = live()
    if running:
        print_sessions(running)
    name = name or secrets.token_hex(3)
    want = wanted(db, tz, since, until, sessions)
    same = dict(running).get(name)
    if same is not None:
        if same.get("unresponsive"):
            raise Exit(repl.EXIT_CHOICE, f"session {name} runs but does not answer; stop it first")
        diff = [
            f"{k}: running {same['params'][k]!r}, asked {v!r}"
            for k, v in want.items()
            if same["params"][k] != v
        ]
        if diff:
            raise Exit(repl.EXIT_CHOICE, f"session {name} runs with different parameters: " + "; ".join(diff))
        show_status(name, wait_ready(name, None))
        return repl.EXIT_OK
    cmd = [sys.executable, "-m", "botsworth.repl.server", "--db", db, "--tz", tz, "--name", name,
           "--idle", str(idle), "--max-rss", str(max_rss)]  # fmt: skip
    for opt, value in (("since", since), ("until", until)):
        if value:
            cmd += [f"--{opt}", value]
    if sessions:
        cmd += ["--sessions", *sessions]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    st = wait_ready(name, proc)
    show_status(name, st)
    print(f"use it with: botsworth repl run --name {name} '<python>'   (bw.help() lists the helpers)")
    print(st["banner"])
    return repl.EXIT_OK


def run(code: str, name: str | None = None, wait: float = 50) -> int:
    name = pick(name)
    r = call(name, {"op": "run", "code": code, "wait": wait}, timeout=wait + 30)
    if r is None:
        raise Exit(repl.EXIT_NO_SESSION, repl.NO_SESSION)
    if r.get("unresponsive"):
        raise Exit(
            repl.EXIT_BUSY,
            f"session {name} does not answer; a cell may be stuck in a long call (see `status`)",
        )
    if r.get("loading"):
        raise Exit(repl.EXIT_BUSY, f"session {name} is still loading the frames; run again in a few seconds")
    if r.get("busy"):
        raise Exit(
            repl.EXIT_BUSY,
            f"session {name} is busy with an earlier cell (see `status`; `interrupt` stops it)",
        )
    if r.get("pending"):
        raise Exit(
            repl.EXIT_BUSY,
            f"cell still running after {wait:g} s; it continues. `status --name {name}` shows progress, "
            f"`status --name {name} --last` the output, `interrupt` stops it.",
        )
    emit(r["out"])
    return repl.EXIT_OK if r["state"] == "ok" else repl.EXIT_RAISED


def emit(out: str) -> None:
    sys.stdout.write(out if out.endswith("\n") or not out else out + "\n")


def status(name: str | None = None, last: bool = False) -> int:
    if name is None and not last:
        sessions = live()
        if not sessions:
            raise Exit(repl.EXIT_NO_SESSION, repl.NO_SESSION)
        print_sessions(sessions)
        if len(sessions) == 1 and not sessions[0][1].get("unresponsive"):
            show_status(*sessions[0])
        return repl.EXIT_OK
    name = pick(name)
    st = call(name, {"op": "status", "last": last})
    if st is None:
        raise Exit(repl.EXIT_NO_SESSION, repl.NO_SESSION)
    if st.get("unresponsive"):
        raise Exit(repl.EXIT_BUSY, f"session {name} does not answer; a cell may be stuck in a long call")
    if not last:
        show_status(name, st)
        return repl.EXIT_OK
    if st["busy_s"] is not None:
        raise Exit(repl.EXIT_BUSY, f"cell still running after {st['busy_s']} s; ask again later")
    if st["last_state"] == "none":
        print("no cell has run in this session yet", file=sys.stderr)
        return repl.EXIT_OK
    emit(st["last_out"])
    return repl.EXIT_OK if st["last_state"] == "ok" else repl.EXIT_RAISED


def interrupt(name: str | None = None) -> int:
    name = pick(name)
    r = call(name, {"op": "interrupt"})
    if r is None:
        raise Exit(repl.EXIT_NO_SESSION, repl.NO_SESSION)
    print("interrupt sent to the running cell" if r.get("was_busy") else "no cell is running")
    return repl.EXIT_OK


def stop(name: str | None = None) -> int:
    name = pick(name)
    r = call(name, {"op": "stop"})
    if r is None:
        raise Exit(repl.EXIT_NO_SESSION, repl.NO_SESSION)
    pid = r.get("pid")
    deadline = time.time() + STOP_GRACE_S
    while pid is not None and alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    killed = False
    if pid is not None and alive(pid):
        os.kill(pid, signal.SIGKILL)
        killed = True
        repl.sock_path(name).unlink(missing_ok=True)
    print(f"session {name} stopped" + (" (killed after 5 s)" if killed else ""))
    return repl.EXIT_OK


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False  # FALLBACK-OK: REPL-3 the process is gone, which is what stop waits for
    return True
