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
- `stop` returns after the process has ended: it asks, waits 5 s, then kills. A server that does not answer is
  reached by its process id from the socket's peer credentials (SIGINT, 5 s, SIGKILL); "stopped" prints only
  for a dead process. A command other than `start` that finds an unusable socket exits 2.
  A refused connect is checked against the server's pid (private link `<name>.<pid base 36>`)
  before any socket name is removed; a failed `start` ends the server it launched.
EXPECTS: pandas importable by the interpreter that runs the client (`start` checks and prints the install
  line).
FAILURE BEHAVIOR: `start` prints the reason and exits 5 for: pandas missing, socket folder not usable, server
  could not bind (e.g. a sandbox forbids it), store missing or of another schema, no answer within
    `LOAD_WAIT_S`.
  A session that does not answer a status request in `ANSWER_S` is listed as unresponsive.
DOES NOT: read the store, keep state between invocations, or write any file (the server owns the socket).
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import secrets
import signal
import socket
import struct
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


def peer_pid(s: socket.socket) -> int | None:
    """Pid of the process at the other end of a connected Unix socket, or None where the OS cannot say."""
    try:
        if sys.platform == "darwin":
            return int.from_bytes(s.getsockopt(0, 0x002, 4), sys.byteorder)  # SOL_LOCAL, LOCAL_PEERPID
        data = s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        return struct.unpack("3i", data)[0]
    except (OSError, AttributeError):
        return None  # FALLBACK-OK: REPL-4 an OS without a peer-pid option: `stop` cannot reach a stuck server


def settle(path: Path, ino: int | None) -> dict | None:
    """After a refused connect (or a connection that ended with no answer): None when the server is gone
    (its socket names are removed), else an `unresponsive` reply with the server's pid. A full queue also
    refuses, so a refusal is checked against the server's pid before anything is removed."""
    if not repl.refused_means_dead(path):
        pid = repl.owner_pid(path)
        return {"ok": False, "unresponsive": True, "pid": pid}
    pid = repl.owner_pid(path)
    with contextlib.suppress(FileNotFoundError):
        if ino is None or os.stat(path).st_ino == ino:
            path.unlink()
    if pid is not None:
        repl.private_path(path, pid).unlink(missing_ok=True)
    return None


def gone(path: Path, ino: int | None) -> dict | None:
    """A connection ended with no answer: give a dying server a moment to leave, then settle."""
    r = settle(path, ino)
    for _ in range(10):
        if r is None:
            return None
        time.sleep(0.1)
        r = settle(path, ino)
    return r


def call(name: str, req: dict, timeout: float = ANSWER_S, start: bool = False) -> dict | None:
    """One request. None when no server answers (a stale socket is removed); `unresponsive` on a timeout.

    A socket that cannot be used at all is exit 5 for `start` and "no session" for every other command.
    """
    path = repl.sock_path(name)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        ino = os.stat(path).st_ino
    except FileNotFoundError:
        s.close()
        return None  # FALLBACK-OK: REPL-4 no socket file: no session
    except OSError:
        ino = None
    try:
        s.connect(str(path))
    except FileNotFoundError:
        s.close()
        return None  # FALLBACK-OK: REPL-4 the socket file went away: no session
    except ConnectionRefusedError:
        # FALLBACK-OK: REPL-4 refused: stale socket (removed, "no session") or a live server with a full queue
        s.close()
        return settle(path, ino)
    except TimeoutError:
        s.close()
        return {"ok": False, "unresponsive": True}  # FALLBACK-OK: REPL-4 a server stuck in a long call
    except OSError as e:
        s.close()
        if start:
            raise Exit(repl.EXIT_START_FAILED, f"cannot use the session socket {path}: {e}") from None
        return None  # FALLBACK-OK: REPL-2 no usable socket under this name is no session
    pid = peer_pid(s)
    try:
        with s:
            s.sendall(json.dumps(req).encode() + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(1 << 20)
                if not chunk:
                    return gone(path, ino)  # FALLBACK-OK: REPL-4 the server ended while answering
                data += chunk
    except TimeoutError:
        return {"ok": False, "unresponsive": True, "pid": pid}  # FALLBACK-OK: REPL-4 a server stuck in a call
    except OSError:
        return gone(path, ino)  # FALLBACK-OK: REPL-4 the server went away while answering
    return json.loads(data)


def broken(st: dict) -> bool:
    """The reply is not a status: the server did not answer in time, or reported an error."""
    return bool(st.get("unresponsive")) or st.get("ok") is False


def failed(name: str, r: dict) -> Exit:
    if r.get("unresponsive"):
        return Exit(
            repl.EXIT_BUSY, f"session {name} does not answer; a cell may be stuck in a long call (see `stop`)"
        )
    return Exit(repl.EXIT_BUSY, f"session {name} reported an error: {r.get('error', 'unknown')}")


def live() -> list[tuple[str, dict]]:
    """Every running session as (name, status reply); stale sockets are removed on the way."""
    folder = repl.sock_dir()
    out = []
    for p in sorted(folder.glob("*.sock")) if folder.is_dir() else []:
        st = call(p.stem, {"op": "status"})
        if st is not None:
            out.append((p.stem, st))
    return out


def mb(v: int | None) -> str:
    return "unknown" if v is None else f"{v} MB"


def list_line(name: str, st: dict) -> str:
    if st.get("ok") is False and not st.get("unresponsive"):
        return f"  {name}: error: {st.get('error', 'unknown')}"
    if st.get("unresponsive"):
        return f"  {name}: unresponsive (stuck in a long call; `botsworth repl stop --name {name}` ends it)"
    p = st["params"]
    state = (
        "loading" if not st["ready"] else (f"busy {st['busy_s']} s" if st["busy_s"] is not None else "idle")
    )
    return f"  {name}: RSS {mb(st['rss_mb'])}, {state}, idle {st['idle_s']} s, tz {p['tz']}, store {p['db']}"


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
        print(f"  loading the frames; uptime {st['uptime_s']} s; RSS {mb(st['rss_mb'])}")
        return
    print(
        f"  loaded in {st['load_s']} s wall / {st['load_cpu_s']} s CPU; uptime {st['uptime_s']} s; "
        f"RSS {mb(st['rss_mb'])}; idle {st['idle_s']} s"
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


class Taken(Exception):
    """The server this `start` launched found a live server on the same name (a concurrent `start`)."""


def reap(proc: subprocess.Popen | None, name: str) -> None:
    """A `start` that fails leaves no server behind, and none of its socket names."""
    if proc is not None and proc.poll() is None:
        proc.kill()
        proc.wait()
        path = repl.sock_path(name)
        path.unlink(missing_ok=True)
        repl.private_path(path, proc.pid).unlink(missing_ok=True)


def wait_ready(name: str, proc: subprocess.Popen | None) -> dict:
    deadline = time.time() + LOAD_WAIT_S
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            if proc.returncode == repl.EXIT_SERVER_TAKEN:
                raise Taken
            err = proc.stderr.read().decode().strip().splitlines() if proc.stderr else []
            reason = err[-1].removeprefix("error: ") if err else f"the server exited with {proc.returncode}"
            raise Exit(repl.EXIT_START_FAILED, f"start failed: {reason}")
        try:
            st = call(name, {"op": "status"}, start=True)
        except Exit:
            reap(proc, name)
            raise
        if st is not None and st.get("ready"):
            return st
        if st is not None and st.get("ok") is False and not st.get("unresponsive"):
            reap(proc, name)
            raise Exit(repl.EXIT_START_FAILED, f"start failed: {st.get('error')}")
        time.sleep(0.1)
    reap(proc, name)
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
    want = wanted(db, tz, since, until, sessions)
    name = name or secrets.token_hex(3)
    for attempt in range(3):
        running = live()
        if attempt == 0 and running:
            print_sessions(running)
        same = dict(running).get(name)
        if same is not None:
            if broken(same):
                raise Exit(repl.EXIT_CHOICE, f"session {name} runs but does not answer; stop it first")
            diff = [
                f"{k}: running {same['params'][k]!r}, asked {v!r}"
                for k, v in want.items()
                if same["params"][k] != v
            ]
            if diff:
                raise Exit(
                    repl.EXIT_CHOICE, f"session {name} runs with different parameters: " + "; ".join(diff)
                )
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
        try:
            st = wait_ready(name, proc)
        except Taken:
            continue  # FALLBACK-OK: REPL-4 a concurrent start won the name: report it as a running session
        show_status(name, st)
        print(f"use it with: botsworth repl run --name {name} '<python>'   (bw.help() lists the helpers)")
        print(st["banner"])
        return repl.EXIT_OK
    raise Exit(repl.EXIT_START_FAILED, f"start failed: session {name} keeps appearing and ending")


def run(code: str, name: str | None = None, wait: float = 50) -> int:
    name = pick(name)
    r = call(name, {"op": "run", "code": code, "wait": wait}, timeout=wait + ANSWER_S)
    if r is None or r.get("stopping"):
        raise Exit(repl.EXIT_NO_SESSION, repl.NO_SESSION)
    if r.get("unresponsive"):
        raise Exit(
            repl.EXIT_BUSY,
            f"cell still running after {wait:g} s (the session does not answer: the cell holds the "
            f"interpreter). It continues; `stop --name {name}` ends the session.",
        )
    if r.get("ok") is False:
        raise failed(name, r)
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
        if len(sessions) == 1 and not broken(sessions[0][1]):
            show_status(*sessions[0])
        return repl.EXIT_OK
    name = pick(name)
    st = call(name, {"op": "status", "last": last})
    if st is None:
        raise Exit(repl.EXIT_NO_SESSION, repl.NO_SESSION)
    if broken(st):
        raise failed(name, st)
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
    if broken(r):
        raise failed(name, r)
    print("interrupt sent to the running cell" if r.get("was_busy") else "no cell is running")
    return repl.EXIT_OK


def stop(name: str | None = None) -> int:
    name = pick(name)
    r = call(name, {"op": "stop"})
    if r is None:
        raise Exit(repl.EXIT_NO_SESSION, repl.NO_SESSION)
    unresponsive = bool(r.get("unresponsive"))
    if not unresponsive and r.get("ok") is False:
        raise failed(name, r)
    pid = r.get("pid")
    if pid is None:
        raise Exit(
            repl.EXIT_BUSY,
            f"session {name} does not answer and its process id is unknown: it was not stopped",
        )
    if unresponsive:
        os.kill(
            pid, signal.SIGINT
        )  # the server may be stuck in a long call; this is what `stop` asks of a cell
    deadline = time.time() + STOP_GRACE_S
    while alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    killed = False
    if alive(pid):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        killed = True
        end = time.time() + 5
        while alive(pid) and time.time() < end:
            time.sleep(0.05)
        if alive(pid):
            raise Exit(repl.EXIT_BUSY, f"session {name} (pid {pid}) did not end after SIGKILL")
        repl.sock_path(name).unlink(missing_ok=True)
        repl.private_path(repl.sock_path(name), pid).unlink(missing_ok=True)
    print(f"session {name} stopped" + (" (killed after 5 s)" if killed else ""))
    return repl.EXIT_OK


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False  # FALLBACK-OK: REPL-3 the process is gone, which is what stop waits for
    return True
